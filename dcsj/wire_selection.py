"""Wire specifications read from the solver, separate from submitted inputs."""
import math

from .kernel_layout import INPUT_MAP
from .numeric_vm import NumericKernelError

KEYS = ('JJ', 'Djj', 'MM', 'Dmm')
UNITS = {'JJ': '根', 'Djj': 'mm', 'MM': '根', 'Dmm': 'mm'}


def extract(parameters, values, memory):
    # These existing input locations are also overwritten by the original
    # target-fill branch. Reading them does not change the resource/output ABI.
    used = {key: memory.f32(INPUT_MAP[key]) for key in KEYS}
    if (not all(math.isfinite(v) for v in used.values())
            or any(used[key] < 0 or not used[key].is_integer() for key in ('JJ', 'MM'))
            or used['JJ'] + used['MM'] == 0
            or any(used[count] > 0 and used[diameter] <= 0
                   for count, diameter in (('JJ', 'Djj'), ('MM', 'Dmm')))):
        raise NumericKernelError('计算后的线规无效，无法与槽满率对应')
    for key in ('JJ', 'MM'):
        used[key] = int(used[key])
    target = parameters['_wire_target']
    automatic = target > 0
    return {'mode': 'target_fill' if automatic else 'fixed',
            'input': {key: parameters[key] for key in KEYS}, 'used': used,
            'ignored_input_keys': list(KEYS) if automatic else [],
            'target_fill': target if automatic else None, 'actual_fill': values['Sf'],
            'fill_delta': values['Sf'] - target if automatic else None,
            'units': {**UNITS, 'target_fill': 'ratio', 'actual_fill': 'ratio', 'fill_delta': 'ratio'},
            'source': 'kernel-final-state'}


def recorded(snapshot):
    """Old target-fill snapshots never recorded the selected wire: do not guess."""
    selection = snapshot.get('wire_selection')
    if selection:
        return selection
    p, values = snapshot['inputs'], snapshot['values']
    automatic = (p.get('_wire_target') or 0) > 0
    submitted = {key: p.get(key) for key in KEYS}
    return {'mode': 'target_fill' if automatic else 'fixed', 'input': submitted,
            'used': None if automatic else submitted,
            'target_fill': p.get('_wire_target') if automatic else None,
            'actual_fill': values.get('Sf'),
            'source': 'not-recorded' if automatic else 'fixed-input-snapshot'}
