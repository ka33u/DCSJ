"""Bounded interpreter for the recovered numerical instruction graph.

Only decoded arithmetic/control-flow IR and non-executable initial data are
loaded. This module has no native emulation, PE parser, or third-party runtime
dependency. Each run owns its memory, registers, and eight physical x87 slots.
"""
from __future__ import annotations
import base64
from collections import Counter
from functools import lru_cache
import gzip
import json
import math
from pathlib import Path
import struct


class NumericKernelError(ValueError):
    """Invalid numeric input, bounded-memory violation, or unsupported IR."""


class NumericKernelLimitError(NumericKernelError):
    """The numerical program exceeded its declared execution budget."""


@lru_cache(maxsize=1)
def _program():
    path = Path(__file__).with_name("generated") / "numeric_kernel.json.gz"
    program = json.loads(gzip.decompress(path.read_bytes()))
    program["code"] = {row[0]: (row[1], row[2], row[3]) for row in program.pop("instructions")}
    program["function_names"] = {f["address"]: f["name"] for f in program["functions"]}
    program["initial_segments"] = [(s["address"], base64.b64decode(s["data_base64"])) for s in program["segments"]]
    return program


class Memory:
    """Owned bounded memory, exposing bytes and typed values for inspection."""
    def __init__(self, program):
        self.regions = [
            (program["image_base"], bytearray(program["image_size"])),
            (program["stack_base"], bytearray(program["stack_size"])),
        ]
        for address, data in program["initial_segments"]:
            self.write(address, data)

    def _region(self, address, size):
        for base, region in self.regions:
            offset = address - base
            if 0 <= offset and offset + size <= len(region):
                return region, offset
        raise NumericKernelError(f"Memory outside numeric state: {address:#x} + {size}")

    def read(self, address, size):
        region, offset = self._region(address, size)
        return bytes(region[offset:offset + size])

    def write(self, address, data):
        region, offset = self._region(address, len(data))
        region[offset:offset + len(data)] = data

    def uint(self, address, size=4):
        region, offset = self._region(address, size)
        return int.from_bytes(region[offset:offset + size], "little")

    def sint(self, address, size=4):
        return _signed(self.uint(address, size), size)

    def set_uint(self, address, value, size=4):
        self.write(address, (int(value) & ((1 << (size * 8)) - 1)).to_bytes(size, "little"))

    def f32(self, address):
        region, offset = self._region(address, 4)
        return struct.unpack_from("<f", region, offset)[0]

    read_float = f32

    def f64(self, address):
        region, offset = self._region(address, 8)
        return struct.unpack_from("<d", region, offset)[0]

    def set_float(self, address, value, size=4):
        if size not in (4, 8):
            raise NumericKernelError(f"Unsupported floating storage width: {size}")
        try:
            packed = struct.pack("<f" if size == 4 else "<d", value)
        except OverflowError as exc:
            raise NumericKernelError(f"Floating overflow at {address:#x}") from exc
        self.write(address, packed)


def read_float(memory, address):
    return memory.f32(address)


def _signed(value, size=4):
    bits = size * 8
    value &= (1 << bits) - 1
    return value - (1 << bits) if value & (1 << (bits - 1)) else value


ALIASES = {}
for _root in ("eax", "ebx", "ecx", "edx"):
    ALIASES[_root[1:]] = (_root, 0, 16)
    ALIASES[_root[1] + "l"] = (_root, 0, 8)
    ALIASES[_root[1] + "h"] = (_root, 8, 8)
for _root in ("esi", "edi", "ebp", "esp"):
    ALIASES[_root[1:]] = (_root, 0, 16)


class _VM:
    def __init__(self, program, memory_overrides):
        self.program = program
        self.memory = Memory(program)
        self.registers = {r: 0 for r in ("eax", "ebx", "ecx", "edx", "esi", "edi", "ebp", "esp")}
        self.registers["ebp"] = program["initial_ebp"]
        self.registers["esp"] = program["initial_ebp"] - 0x124 - 12
        self.memory.set_uint(program["initial_ebp"] + 4, 0xFFFF000)
        for address, value in memory_overrides.items():
            if not isinstance(address, int):
                raise NumericKernelError("Override addresses must be integers")
            if isinstance(value, (bytes, bytearray)):
                self.memory.write(address, value)
            elif isinstance(value, (int, float)) and math.isfinite(value):
                self.memory.set_float(address, value)
            else:
                raise NumericKernelError(f"Invalid override value at {address:#x}")
        self.pc = program["entry"]
        self.fpu = [None] * 8
        self.top = 0
        self.control_word = 0x37F
        self.fpu_compare = 0
        self.cf = self.zf = self.sf = self.of = self.pf = False
        self.outputs = {}
        self.unit = None
        self.calls = Counter()
        self.steps = 0
        self.done = False

    def reg(self, name):
        if name in self.registers:
            return self.registers[name]
        root, shift, bits = ALIASES[name]
        return (self.registers[root] >> shift) & ((1 << bits) - 1)

    def set_reg(self, name, value):
        if name in self.registers:
            self.registers[name] = int(value) & 0xFFFFFFFF
        else:
            root, shift, bits = ALIASES[name]
            mask = ((1 << bits) - 1) << shift
            self.registers[root] = (self.registers[root] & ~mask) | ((int(value) << shift) & mask)

    def address(self, operand):
        return ((self.reg(operand[1]) if operand[1] else 0) +
                (self.reg(operand[2]) * operand[3] if operand[2] else 0) + operand[4]) & 0xFFFFFFFF

    def integer(self, operand):
        if operand[0] == "i":
            return operand[1]
        if operand[0] == "r":
            return self.reg(operand[1])
        return self.memory.uint(self.address(operand), operand[-1])

    def set_integer(self, operand, value):
        if operand[0] == "r":
            self.set_reg(operand[1], value)
        else:
            self.memory.set_uint(self.address(operand), value, operand[-1])

    def st(self, index=0):
        value = self.fpu[(self.top + index) & 7]
        if value is None:
            raise NumericKernelError(f"Empty x87 slot st({index}) at {self.pc:#x}")
        return value

    def set_st(self, index, value):
        self.fpu[(self.top + index) & 7] = value

    @staticmethod
    def st_index(operand):
        name = operand[1]
        return int(name[3:-1]) if name.startswith("st(") else 0

    def push_float(self, value):
        self.top = (self.top - 1) & 7
        if self.fpu[self.top] is not None:
            raise NumericKernelError(f"x87 stack overflow at {self.pc:#x}")
        self.fpu[self.top] = float(value)

    def pop_float(self):
        value = self.st()
        self.fpu[self.top] = None
        self.top = (self.top + 1) & 7
        return value

    def floating(self, operand):
        if operand[0] == "r":
            return self.st(self.st_index(operand))
        address = self.address(operand)
        if operand[-1] == 4:
            return self.memory.f32(address)
        if operand[-1] == 8:
            return self.memory.f64(address)
        raise NumericKernelError(f"Unsupported x87 load width {operand[-1]}")

    def set_floating(self, operand, value):
        if operand[0] == "r":
            self.set_st(self.st_index(operand), value)
        else:
            self.memory.set_float(self.address(operand), value, operand[-1])

    def push(self, value):
        self.registers["esp"] = (self.registers["esp"] - 4) & 0xFFFFFFFF
        self.memory.set_uint(self.registers["esp"], value)

    def pop(self):
        value = self.memory.uint(self.registers["esp"])
        self.registers["esp"] = (self.registers["esp"] + 4) & 0xFFFFFFFF
        return value

    def ret(self, cleanup=0):
        self.pc = self.pop()
        self.registers["esp"] += cleanup

    def flags(self, value, size, a=0, b=0, operation="logic"):
        bits = size * 8
        mask = (1 << bits) - 1
        v, a, b = value & mask, a & mask, b & mask
        sign = 1 << (bits - 1)
        self.zf = v == 0
        self.sf = bool(v & sign)
        self.pf = (v & 0xFF).bit_count() % 2 == 0
        if operation == "sub":
            self.cf = a < b
            self.of = bool(((a ^ b) & (a ^ v)) & sign)
        elif operation == "add":
            self.cf = a + b > mask
            self.of = bool((~(a ^ b) & (a ^ v)) & sign)
        else:
            self.cf = self.of = False

    def branch(self, mnemonic):
        return {
            "je": self.zf, "jne": not self.zf,
            "jp": self.pf, "jnp": not self.pf,
            "jl": self.sf != self.of, "jle": self.zf or self.sf != self.of,
            "jg": not self.zf and self.sf == self.of, "jge": self.sf == self.of,
            "jb": self.cf, "jbe": self.cf or self.zf,
            "ja": not self.cf and not self.zf, "jae": not self.cf,
        }[mnemonic]

    def callback(self):
        unary = {0x42CFFF: math.cos, 0x42D01A: math.sin, 0x42D035: math.tan,
                 0x42D050: math.sqrt, 0x42D06E: math.log, 0x42D0DA: math.asin,
                 0x42D110: math.atan, 0x42D1C1: math.exp}
        if self.pc in unary:
            self.calls[unary[self.pc].__name__] += 1
            self.set_st(0, unary[self.pc](self.st()))
            self.ret()
        elif self.pc == 0x459060:
            self.calls["pow"] += 1
            exponent, base = self.st(0), self.st(1)
            self.set_st(1, math.pow(base, exponent))
            self.pop_float()
            self.ret()
        elif self.pc in (0x42D28B, 0x42D264):
            value = self.st()
            result = (math.copysign(math.floor(abs(value) + 0.5), value)
                      if self.pc == 0x42D28B else math.trunc(value))
            self.set_st(0, float(result))
            self.ret()
        elif self.pc in (0x42D780, 0x42DDD0):
            sp = self.registers["esp"]
            first = self.pc == 0x42D780
            if first:
                self.unit = self.memory.uint(sp + 8)
            pointer = self.memory.uint(sp + (20 if first else 12))
            value = self.memory.f32(pointer)
            if not math.isfinite(value):
                raise NumericKernelError(f"Nonfinite output in unit {self.unit}")
            self.outputs.setdefault(str(self.unit), []).append(value)
            self.ret()
        elif self.pc in (0x42FBC0, 0x430220, 0x435660, 0x432650, 0x432740, 0x4369D0):
            # Formatted report writes and runtime bookkeeping have no numeric effect.
            self.ret()
        elif self.pc == 0x4358E0 or self.pc == 0xFFFF000:
            self.done = True
        elif self.pc in (0x436A50,):
            raise NumericKernelError("Legacy mathematical error handler reached")
        else:
            return False
        return True

    def execute(self, next_pc, op, args):
        old_pc = self.pc
        self.pc = next_pc
        if op == "mov" or op == "movzx":
            self.set_integer(args[0], self.integer(args[1]))
        elif op == "lea":
            self.set_integer(args[0], self.address(args[1]))
        elif op == "push":
            self.push(self.integer(args[0]))
        elif op == "pop":
            self.set_integer(args[0], self.pop())
        elif op in ("add", "sub", "cmp", "and", "or", "xor", "test"):
            a, b = self.integer(args[0]), self.integer(args[1])
            value = (a + b if op == "add" else a - b if op in ("sub", "cmp") else
                     a & b if op in ("and", "test") else a | b if op == "or" else a ^ b)
            self.flags(value, args[0][-1], a, b, "add" if op == "add" else "sub" if op in ("sub", "cmp") else "logic")
            if op not in ("cmp", "test"):
                self.set_integer(args[0], value)
        elif op == "dec":
            a, old_cf = self.integer(args[0]), self.cf
            self.set_integer(args[0], a - 1)
            self.flags(a - 1, args[0][-1], a, 1, "sub")
            self.cf = old_cf
        elif op == "neg":
            a = self.integer(args[0])
            self.flags(-a, args[0][-1], 0, a, "sub")
            self.set_integer(args[0], -a)
        elif op == "imul":
            a = _signed(self.integer(args[1] if len(args) == 3 else args[0]), args[0][-1])
            b = _signed(self.integer(args[2] if len(args) == 3 else args[1]), args[0][-1])
            result = a * b
            self.set_integer(args[0], result)
            self.cf = self.of = _signed(result, args[0][-1]) != result
        elif op == "cdq":
            self.registers["edx"] = 0xFFFFFFFF if self.registers["eax"] & 0x80000000 else 0
        elif op == "idiv":
            divisor = _signed(self.integer(args[0]), args[0][-1])
            dividend = (self.registers["edx"] << 32) | self.registers["eax"]
            if dividend & (1 << 63):
                dividend -= 1 << 64
            quotient = abs(dividend) // abs(divisor)
            if (dividend < 0) != (divisor < 0):
                quotient = -quotient
            if not -(1 << 31) <= quotient < (1 << 31):
                raise NumericKernelError("Integer quotient overflow")
            self.set_reg("eax", quotient)
            self.set_reg("edx", dividend - quotient * divisor)
        elif op == "bound":
            value = _signed(self.integer(args[0]), args[0][-1])
            address = self.address(args[1])
            width = args[0][-1]
            if not self.memory.sint(address, width) <= value <= self.memory.sint(address + width, width):
                raise NumericKernelError(f"Array index out of bounds at {old_pc:#x}: {value}")
        elif op == "call":
            self.push(next_pc)
            self.pc = self.integer(args[0])
        elif op == "jmp":
            self.pc = self.integer(args[0])
        elif op.startswith("j"):
            if self.branch(op):
                self.pc = self.integer(args[0])
        elif op == "ret":
            self.ret(self.integer(args[0]) if args else 0)
        elif op == "sahf":
            ah = self.reg("ah")
            self.sf, self.zf, self.pf, self.cf = bool(ah & 128), bool(ah & 64), bool(ah & 4), bool(ah & 1)
        elif op == "fld":
            self.push_float(self.floating(args[0]))
        elif op == "fldz":
            self.push_float(0.0)
        elif op == "fild":
            self.push_float(_signed(self.integer(args[0]), args[0][-1]))
        elif op in ("fst", "fstp"):
            self.set_floating(args[0], self.st())
            if op == "fstp":
                self.pop_float()
        elif op == "ffree":
            self.fpu[(self.top + self.st_index(args[0])) & 7] = None
        elif op in ("fadd", "fsub", "fsubr", "fmul", "fdiv", "fdivr"):
            a, b = self.st(), self.floating(args[0])
            if op.endswith("r"):
                a, b = b, a
            if op == "fadd":
                value = a + b
            elif op.startswith("fsub"):
                value = a - b
            elif op == "fmul":
                value = a * b
            else:
                value = a / b if b != 0 else math.nan if a == 0 else math.copysign(math.inf, a * math.copysign(1, b))
            self.set_st(0, value)
        elif op == "fcomp":
            a, b = self.st(), self.floating(args[0])
            self.fpu_compare = 0x4500 if math.isnan(a) or math.isnan(b) else 0x100 if a < b else 0x4000 if a == b else 0
            self.pop_float()
        elif op == "fnstsw":
            self.set_integer(args[0], self.fpu_compare | (self.top << 11))
        elif op == "fnstcw":
            self.set_integer(args[0], self.control_word)
        elif op == "fldcw":
            self.control_word = self.integer(args[0])
        elif op == "fistp":
            value = self.pop_float()
            rounding = (self.control_word >> 10) & 3
            integer = round(value) if rounding == 0 else math.floor(value) if rounding == 1 else math.ceil(value) if rounding == 2 else math.trunc(value)
            self.set_integer(args[0], integer)
        elif op == "fabs":
            self.set_st(0, abs(self.st()))
        elif op == "fchs":
            self.set_st(0, -self.st())
        elif op in ("wait", "nop"):
            pass
        else:
            raise NumericKernelError(f"Unsupported decoded operation {op} at {old_pc:#x}")


def run(memory_overrides: dict[int, float | bytes], *, max_steps: int = 2000000):
    """Run one isolated computation, rejecting unmodeled calls and budget overruns."""
    if not isinstance(max_steps, int) or not 1 <= max_steps <= 50000000:
        raise NumericKernelError("max_steps must be an integer from 1 to 50000000")
    program = _program()
    vm = _VM(program, memory_overrides)
    code = program["code"]
    names = program["function_names"]
    try:
        while not vm.done and vm.steps < max_steps:
            vm.steps += 1
            if vm.pc in names:
                vm.calls[names[vm.pc]] += 1
            if vm.callback():
                continue
            instruction = code.get(vm.pc)
            if instruction is None:
                raise NumericKernelError(f"Unmodeled numeric address: {vm.pc:#x}")
            vm.execute(*instruction)
    except NumericKernelError:
        raise
    except (ArithmeticError, ValueError, KeyError, struct.error) as exc:
        raise NumericKernelError(f"Numerical failure at {vm.pc:#x}: {exc}") from exc
    if not vm.done:
        raise NumericKernelLimitError(f"Numeric instruction budget ({max_steps}) exceeded at {vm.pc:#x}")
    return {
        "outputs": vm.outputs, "memory": vm.memory, "steps": vm.steps,
        "calls": dict(vm.calls), "completed": True,
        "precision": "binary64 intermediates with binary32 source stores",
    }
