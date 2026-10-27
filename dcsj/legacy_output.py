"""Bounded reader for the original calculation sheet's formatted output.

Formats and literals stay in the installed private numeric resource. This only
observes the unit-2 write arguments; it never changes the numerical state or IO.
"""
import math


MAX_TOKENS = 4096
MAX_LINES = 500


def decode_format(memory, address):
    stack = [[]]
    repeats = []
    pointer = address
    for _ in range(MAX_TOKENS):
        word = memory.uint(pointer); pointer += 4
        op = word & 0xffff
        if op == 54:  # format start
            continue
        if op == 55:
            if len(stack) != 1:
                raise ValueError('Unclosed report format group')
            return stack[0]
        if op == 28:  # literal bytes, padded to a word
            length = word >> 16
            if length > 1024:
                raise ValueError('Report literal too long')
            token = ('literal', memory.read(pointer, length).decode('gb18030'))
            pointer += (length + 3) // 4 * 4
            stack[-1].append(token)
        elif op in (11, 14, 29, 33):  # TR, X, A, F
            count, width = memory.uint(pointer), memory.uint(pointer + 4)
            pointer += 8
            if not 1 <= count <= 256 or not 0 <= width <= 256:
                raise ValueError('Report field bounds exceeded')
            kind = {11: 'space', 14: 'space', 29: 'string', 33: 'float'}[op]
            digits = word >> 24
            if digits > 12:
                raise ValueError('Report precision exceeded')
            stack[-1].extend([(kind, width, digits)] * count)
        elif op == 71:
            count = word >> 16
            if not 1 <= count <= MAX_LINES:
                raise ValueError('Report line bounds exceeded')
            stack[-1].extend([('newline',)] * count)
        elif op == 56:
            count = word >> 16
            if not 1 <= count <= 256 or len(stack) >= 8:
                raise ValueError('Report group bounds exceeded')
            repeats.append(count); stack.append([])
        elif op == 57:
            if len(stack) == 1:
                raise ValueError('Unexpected report group end')
            group, count = stack.pop(), repeats.pop()
            if len(group) * count + len(stack[-1]) > MAX_TOKENS:
                raise ValueError('Expanded report too large')
            stack[-1].extend(group * count)
        else:
            raise ValueError('Unsupported report format')
        if sum(map(len, stack)) > MAX_TOKENS:
            raise ValueError('Report token budget exceeded')
    raise ValueError('Unterminated report format')


def fixed(value, width, digits):
    if value is None:
        return '-'.rjust(width)
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Nonfinite report number')
    text = f'{value:.{digits}f}' + ('.' if digits == 0 else '')
    if len(text) > width and text.startswith('0.'):
        text = text[1:]
    if len(text) > width and text.startswith('-0.'):
        text = '-' + text[2:]
    return text.rjust(width) if len(text) <= width else '*' * width


def format_record(tokens, values):
    result = []; index = 0
    for token in tokens:
        kind = token[0]
        if kind == 'literal':
            result.append(token[1])
        elif kind == 'space':
            result.append(' ' * token[1])
        elif kind == 'newline':
            result.append('\n')
        else:
            if index >= len(values):
                raise ValueError('Missing report value')
            value = values[index]; index += 1
            if kind == 'float':
                result.append(fixed(value, token[1], token[2]))
            elif kind == 'string':
                if not isinstance(value, str):
                    raise ValueError('Invalid report string')
                # A fields are byte-width in the native GB18030 report.
                raw = value.encode('gb18030', errors='replace')
                result.append(raw[-token[1]:].rjust(token[1], b' ').decode('gb18030', errors='replace'))
            else:
                raise ValueError('Unknown report token')
    if index != len(values):
        raise ValueError('Unused report values')
    return ''.join(result)


class Collector:
    def __init__(self):
        self.active = None
        self.records = []
        self.error = False

    def observe(self, vm):
        if self.error:
            return
        try:
            self._observe(vm)
        except (ValueError, ArithmeticError, IndexError):
            # An unsupported format must not change an otherwise valid result.
            self.active = None; self.records = []; self.error = True

    def _observe(self, vm):
        first = vm.pc == 0x42fbc0
        sp = vm.reg('esp')
        def arg(i):
            return vm.memory.uint(sp + i * 4)
        if first:
            if self.active:
                raise ValueError('Incomplete formatted record')
            self.active = ({'tokens': decode_format(vm.memory, arg(6)), 'values': []}
                           if arg(2) == 2 else None)
        if self.active is None:
            return
        descriptor, data = arg(4 if first else 2), arg(5 if first else 3)
        word = vm.memory.uint(descriptor); kind = word & 0xffff
        if kind == 0x11a:
            self.active['values'].append(vm.memory.f32(data))
        elif kind in (0x438, 0x638):
            count, pointer = vm.memory.uint(data), vm.memory.uint(data + 4)
            if count > 1024:
                raise ValueError('Report string too long')
            value = vm.memory.read(pointer, count).decode('gb18030', errors='replace')
            self.active['values'].extend(list(value) if kind == 0x638 else [value])
        elif word != 1:
            raise ValueError('Unsupported report argument')
        if len(self.active['values']) > MAX_TOKENS:
            raise ValueError('Too many report values')
        if word >> 16 == 1 or word == 1:
            self.records.append(format_record(self.active['tokens'], self.active['values']))
            self.active = None
            if (sum(record.count('\n') + 1 for record in self.records) > MAX_LINES
                    or sum(map(len,self.records)) > 100000):
                raise ValueError('Too many report lines')

    def result(self):
        if self.error or self.active or not self.records:
            return {'format_version': 1, 'status': 'unavailable'}
        return {'format_version': 1, 'status': 'complete',
                'source': 'kernel-formatted-unit-2', 'text': '\n'.join(self.records)}
