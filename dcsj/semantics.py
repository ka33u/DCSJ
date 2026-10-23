"""Explicit engineering units and decision sources around the legacy solver."""
import hashlib
import json
import math
import struct

from .parameters import field_info, InputError


def f32(value):
    return struct.unpack('<f', struct.pack('<f', value))[0]


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def losses(p):
    resolved = {}
    for key in ('pfw', 'ps'):
        value = f32(p[key])
        mode = p.get(key + '_unit', 'legacy')
        unit = ('W' if value > 1 else 'pu') if mode == 'legacy' else mode
        try:
            pu = f32(value / f32(p['kW']) / 1000) if unit == 'W' else value
        except (OverflowError, ZeroDivisionError):
            raise InputError([{'field': key, 'message': '额定功率与损耗换算超出数值核范围'}]) from None
        factor = 1.
        reference = p.get('pfw_ref_rpm', 0) if key == 'pfw' else 0
        exponent = p.get('pfw_speed_exponent', 2)
        speed = 120 * p['f'] / p['p']
        if reference:
            try:
                factor = (speed / reference) ** exponent
                pu = f32(pu * factor)
            except (OverflowError, ZeroDivisionError):
                raise InputError([{'field': 'pfw_ref_rpm', 'message': '转速折算超出数值范围'}]) from None
        if not math.isfinite(pu) or not 0 <= pu <= 1:
            raise InputError([{'field': key, 'message': '解析后的损耗比例须在 0 至 1 范围，请核对单位及参考转速'}])
        resolved[key] = {'input': p[key], 'mode': mode, 'resolved_unit': unit,
                         'kernel_pu': pu, 'rated_w': pu * p['kW'] * 1000,
                         'load_dependence': 'constant_w' if key == 'pfw' else 'proportional_to_output_power',
                         'speed_scaling': {'enabled': bool(reference), 'reference_sync_rpm': reference or None,
                                           'sync_rpm': speed, 'exponent': exponent if reference else None,
                                           'factor': factor}}
    return resolved


def frequency_model(frequency):
    # The source table's measurement frequency remains unknown. This is the
    # recovered algorithm's frequency factor, not a material certification.
    return {'method': 'legacy-power-law', 'reference_hz': 50,
            'exponent': f32(1.33), 'frequency_hz': frequency,
            'factor': math.exp(math.log(f32(frequency) / f32(50)) * f32(1.33)),
            'basis': 'recovered-kernel', 'material_measurement_verified': False}


def electrical(p):
    phase = p.get('Ufai')
    try:
        phase = float(phase)
        if not math.isfinite(phase):
            phase = None
    except (TypeError, ValueError):
        phase = None
    connection = p.get('Jiefa')
    return {'connection': connection, 'voltage_type': 'phase', 'phase_voltage_v': phase,
            'line_voltage_v': None if phase is None or connection not in {'Y', 'y', '△', 'Δ', 'D'} else phase * math.sqrt(3) if connection in {'Y', 'y'} else phase,
            'line_voltage_method': 'sqrt(3)*phase' if connection in {'Y', 'y'} else 'phase' if connection in {'△', 'Δ', 'D'} else None}


def requirements(p, values):
    items = []
    for key, relation in [('ATA', 'minimum'), ('PF', 'minimum'), ('IST', 'maximum'), ('TST', 'minimum'), ('TM', 'minimum')]:
        target = p.get(key)
        actual = values.get(key + '1')
        passed = None if target is None or actual is None else actual <= target if relation == 'maximum' else actual >= target
        items.append({'metric': key + '1', 'target': target, 'relation': relation, 'actual': actual,
                      'passed': passed, 'target_source': key,
                      'decision_source': 'values.' + key + '1'})
    return items


def metric_info(key, value):
    result = {**field_info(key), 'group': '计算结果', 'value': value,
              'authoritative': True, 'authority_scope': 'application-design-check',
              'source': 'legacy-start-check' if key in {'IST1', 'TST1'} else 'legacy-perf-estimate' if key == 'TM1' else 'legacy-kernel-output'}
    if result['unit'] == 'G':
        result['si'] = {'value': value / 10000, 'unit': 'T'}
    return result


def result_metadata(p, values, material, curves):
    loss = losses(p)
    for curve in curves:
        curve.update(authoritative=False, authority_scope='trend-only',
                     source='legacy-sm-samples' if curve['id'] == 'slip' else 'legacy-load-samples')
    slip = next((c for c in curves if c['id'] == 'slip'), None)
    checks = []
    if slip:
        for index, metric in [(0, 'IST1'), (1, 'TST1')]:
            start = next((p['y'] for p in slip['series'][index]['points'] if abs(p['x'] - 1) < 1e-6), None)
            if start is not None:
                checks.append({'metric': metric, 'check_value': values[metric], 'curve_value': start,
                               'delta': start - values[metric], 'position': {'s': 1}})
        points = slip['series'][1]['points']
        if points:
            peak = max(points, key=lambda p: p['y'])
            checks.append({'metric': 'TM1', 'check_value': values['TM1'], 'curve_value': peak['y'],
                           'delta': peak['y'] - values['TM1'], 'position': {'s': peak['x']}})
    return {'contract': {'version': '1.2', 'values': 'canonical-numerical-output',
                         'metrics': 'metadata-view-of-values', 'decision_values': ['IST1', 'TST1', 'TM1'],
                         'curve_authority': 'trend-only', 'effective_wire': 'wire_selection.used'},
            'values_units': {key: field_info(key)['unit'] for key in values},
            'values_si': {key: {'value': value / 10000, 'unit': 'T'} for key, value in values.items() if field_info(key)['unit'] == 'G'},
            'electrical': electrical(p), 'requirements': requirements(p, values),
            'loss_semantics': loss, 'curve_checks': checks,
            'material_semantics': {'loss_curve_frequency_hz': 50 if material.get('kind') == 'user_native' else None,
                                   'measurement_status': 'user_declared' if material.get('kind') == 'user_native' else 'not_declared_in_table',
                                   'loss_unit': 'kernel_native', 'loss_frequency_model': frequency_model(p['f'])},
            'input_fingerprints': {'core_input_sha256': fingerprint(p)}}
