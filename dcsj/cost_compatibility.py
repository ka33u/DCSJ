"""Bounded two-group traversal for the verified legacy COST instruction block.

The legacy routine uses diameter equality as a proxy for group identity. With
equal diameters its second pass jumps back forever, including when MM is zero.
Only the second equal-diameter branch is redirected to its existing cost tail;
both original diameter lookups and all original arithmetic still execute.
"""
import hashlib
import json
import math

ENTRY = 0x420663
BRANCH = 0x4208B2
AGAIN = 0x4208D1
TAIL = 0x4208D6
RETURN = 0x4209ED
END = 0x4209F0
EVENTS = frozenset((ENTRY, BRANCH, AGAIN, RETURN))
CODE_SHA256 = '671f60e9ec6d87816189b06cbbb630c1cb1608cc0918fbdad5dbddd0df59c471'
REPAIR_ID = 'equal-diameter-cost-loop-v1'


def matches(program):
    """Do not infer compatibility from a resource's declared name or version."""
    rows = [[pc, *row] for pc, row in sorted(program['code'].items()) if ENTRY <= pc < END]
    return hashlib.sha256(json.dumps(rows, separators=(',', ':')).encode()).hexdigest() == CODE_SHA256


class CostTraversal:
    def __init__(self, program):
        self.enabled = matches(program)
        self.pass_number = 0
        self.repairs = []

    def visit(self, vm):
        """Return true only when consuming the verified second-pass branch."""
        if not self.enabled:
            return False
        if vm.pc == ENTRY:
            self.pass_number = 1
        elif vm.pc == RETURN:
            self.pass_number = 0
        elif vm.pc == AGAIN and self.pass_number:
            self.pass_number += 1
        elif vm.pc == BRANCH and self.pass_number == 2 and not vm.pf and vm.fpu_compare == 0x4000:
            first = vm.memory.f32(0x4AFD64)
            second = vm.memory.f32(0x4AFD6C)
            current = vm.memory.f32(0x4AB69C)
            if math.isfinite(first) and first > 0 and first == second == current:
                vm.pc = TAIL
                self.repairs.append({'id': REPAIR_ID, 'diameter_mm': first, 'groups_evaluated': 2})
                return True
        return False
