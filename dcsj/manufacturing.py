"""Explicit numeric stock sizes and optional rotor machining linkage.

These records check declared choices, not the full winding connection topology.
"""
import math
import struct
from functools import lru_cache


def single(value):
    return struct.unpack('<f', struct.pack('<f', value))[0]


@lru_cache(maxsize=512)
def _stock_lookup(values, negative_zeros):
    # Preserve the first matching specification, even when an exact value and
    # another specification's float32 encoding coincide. Key by immutable
    # contents so editing a list cannot leave an obsolete lookup behind.
    lookup = {}
    for index, value in enumerate(values):
        if value == 0:
            value = -0.0 if index in negative_zeros else 0.0
        lookup.setdefault(value, value)
        lookup.setdefault(single(value), value)
    return lookup


def nominal(variable, value):
    """Accept exact values or their legacy float32 encoding, without a loose epsilon."""
    if not variable.values:
        return None
    try:
        sizes = tuple(variable.values)
        # Python considers -0.0 and +0.0 equal as dictionary/cache keys, but
        # returning the actual declared specification must retain its sign.
        negative_zeros = tuple(i for i, x in enumerate(sizes) if x == 0 and math.copysign(1., x) < 0)
        return _stock_lookup(sizes, negative_zeros).get(value)
    except TypeError:
        # A malformed nonnumeric candidate is not a declared specification.
        return None


def linked_parameters(request, parameters):
    p = dict(parameters)
    if request.rotor_gap_mode == 'fixed_neck_bottom':
        base = request.parameters
        # Keep the reference bit-for-bit; subtract from the same upper bound
        # used by validation so its endpoint is exactly zero, never -epsilon.
        p['hr0'] = base['hr0'] if p['g'] == base['g'] else (base['g'] + base['hr0']) - p['g']
    return p


def linkage_valid(request, parameters):
    if request.rotor_gap_mode != 'fixed_neck_bottom':
        return True
    expected = linked_parameters(request, parameters)['hr0']
    return expected >= 0 and math.isclose(parameters['hr0'], expected, rel_tol=0, abs_tol=1e-10)


def audit(request, parameters):
    rows = [{'key': v.key, 'allowed': list(v.values), 'actual': parameters[v.key],
             'nominal': nominal(v, parameters[v.key]), 'passed': nominal(v, parameters[v.key]) is not None}
            for v in request.variables if v.values]
    gap = None
    if request.rotor_gap_mode == 'fixed_neck_bottom':
        base = request.parameters
        d2 = parameters['Di1'] - 2 * parameters['g']
        gap = {'mode': request.rotor_gap_mode, 'base_gap_mm': base['g'], 'base_hr0_mm': base['hr0'],
               'gap_mm': parameters['g'], 'hr0_mm': parameters['hr0'], 'rotor_outer_mm': d2,
               'neck_bottom_mm': d2 - 2 * parameters['hr0'],
               'reference_neck_bottom_mm': base['Di1'] - 2 * (base['g'] + base['hr0']),
               'formula': 'hr0 = hr0基准 + g基准 − g；D2 = Di1 − 2g',
               'passed': linkage_valid(request, parameters)}
    if not rows and gap is None:
        return None
    return {'numeric_sets': rows, 'rotor_gap': gap,
            'passed': all(row['passed'] for row in rows) and (gap is None or gap['passed'])}
