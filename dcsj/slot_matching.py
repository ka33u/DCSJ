"""Cross-reference drawing applications with original model input dimensions.

An application match is evidence for a candidate, never proof of drawing identity.
Keep historical variants and all disagreements; do not update the resource library.
"""
from collections import defaultdict
from functools import lru_cache
import copy
import hashlib
import json
import math
import re
from pydantic import ValidationError

from . import catalog, slots
from .parameters import InputError, input_parameters
from .storage import ConflictError, NotFoundError

METHOD = 'application-cross-reference-v1'
LABELS = {'unique': '单组外形一致', 'ambiguous': '多组尺寸待核对',
          'mismatch': '规格有差异', 'unmatched': '未找到对应型号', 'unsupported': '模块不适用'}
COMPARISONS = [('stator_outer_mm', '定子外径'), ('bore_mm', '定子内径'),
               ('gap_mm', '气隙'), ('rotor_outer_mm', '转子外径'), ('stack_mm', '铁心长')]
NOTE = '按系列、机座、极数和功率关联原型号库，再核对外形尺寸与铁心长；槽尺寸来自型号输入，尚未核对冲片原图。'


def number(value):
    try:
        result = float(value) if not isinstance(value, bool) else math.nan
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _signature(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


def _stamp():
    path = catalog.catalog_path().resolve()
    stat = path.stat()
    return str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _family(frame):
    return re.sub(r'\d+$', '', frame.upper())


@lru_cache(maxsize=3)
def _index(stamp):
    from pathlib import Path
    path = Path(stamp[0])
    index = defaultdict(list)
    # Pin the actual file for the entire read, including a resource activation.
    with catalog.connect(path) as db:
        rows = db.execute('SELECT record_id, model, series, rated_power_kw, poles, is_primary, '
                          'raw_json, source_file, source_table, row_number, source_sha256 '
                          'FROM model_catalog ORDER BY record_id')
        for row in rows:
            identity = re.match(r'^([A-Za-z][A-Za-z0-9]*)[-_](\d{2,4}[A-Za-z]+\d*)[-_]', row['model'] or '')
            power = number(row['rated_power_kw'])
            if not identity or power is None:
                continue
            series, frame = (s.upper() for s in identity.groups())
            p = input_parameters(json.loads(row['raw_json']))
            geometry = slots.geometry(p)
            entry = {'record_id': row['record_id'], 'model': row['model'], 'series': row['series'],
                     'frame': frame, 'primary': bool(row['is_primary']), 'geometry': geometry,
                     'stack_mm': number(p.get('L')), 'material': str(p.get('Paihao', '')),
                     'input_sha256': _signature(p),
                     'source': {'path': row['source_file'], 'table': row['source_table'],
                                'row': row['row_number'], 'sha256': row['source_sha256']},
                     'parameters': {side: {k: number(p[k]) for k in keys if number(p.get(k)) is not None}
                                    for side, keys in slots.MAIN_KEYS.items()}}
            index[(series, _family(frame), row['poles'], round(power, 6))].append(entry)
    return index


def _compare(application, model):
    actual = {**model['geometry'], 'stack_mm': model['stack_mm']}
    reference = {**application.get('geometry', {}), 'stack_mm': application.get('stack_mm')}
    return [{'key': key, 'label': label, 'reference': reference.get(key), 'model': actual.get(key),
             'equal': (number(reference.get(key)) is not None and number(actual.get(key)) is not None
                       and abs(float(reference[key]) - float(actual[key])) <= .001)}
            for key, label in COMPARISONS]


def match_item(item, index, token):
    doc = item['document']; side = doc['side']
    groups = {}
    if doc['module'] != 'tx':
        for app_index, application in enumerate(doc.get('applications', [])):
            frame = application.get('frame', '').upper()
            power = number(application.get('power'))
            if power is None or not frame:
                continue
            key = (application.get('series', '').upper(), _family(frame), application.get('poles'), round(power, 6))
            for model in index.get(key, []):
                # A general 80M application may include 80M1/80M2; a specific
                # 100L1 application must never silently include 100L2.
                if frame != model['frame'] and re.search(r'\d$', frame):
                    continue
                parameters = model['parameters'][side]
                if not parameters:
                    continue
                identifier = _signature(parameters)
                if identifier not in groups:
                    required = slots.MAIN_KEYS[side] if side == 'stator' or parameters.get('zc', 0) > 20 else slots.MAIN_KEYS[side][:12]
                    groups[identifier] = {'id': identifier, 'parameters': parameters,
                        'missing_fields': [key for key in required if key not in parameters], 'sources': []}
                comparisons = _compare(application, model)
                evidence = {key: value for key, value in model.items() if key != 'parameters'}
                evidence.update(application_index=app_index, application_source=application.get('source', ''),
                                comparisons=comparisons, geometry_matches=all(c['equal'] for c in comparisons))
                groups[identifier]['sources'].append(evidence)
    candidates = list(groups.values())
    for group in candidates:
        group['sources'].sort(key=lambda s: (not s['geometry_matches'], not s['primary'], s['model'], s['record_id'], s['application_index']))
        group['geometry_matches'] = any(s['geometry_matches'] for s in group['sources'])
        group['area'] = slots.areas(group['parameters'])[side]
    candidates.sort(key=lambda g: (not g['geometry_matches'], bool(g['missing_fields']), g['sources'][0]['model'], g['id']))
    exact_count = sum(g['geometry_matches'] for g in candidates)
    status = ('unsupported' if doc['module'] == 'tx' else 'unmatched' if not candidates else
              'mismatch' if not exact_count else 'unique' if exact_count == 1 else 'ambiguous')
    return {'reference': item['reference'], 'name': item['name'], 'drawing': doc['drawing'], 'side': side,
            'notes': doc.get('notes', ''),
            'status': status, 'label': LABELS[status], 'candidate_count': len(candidates),
            'geometry_matched_count': exact_count, 'application_count': len(doc.get('applications', [])),
            'catalog_token': token, 'method': METHOD, 'note': NOTE, 'candidates': candidates}


def matches(reference):
    item = slots.get(reference)
    stamp = _stamp()
    if item['document'].get('drawing_source') and item['document']['module'] != 'tx':
        from .slot_dimension_matching import matches as dimensions
        return dimensions(item, stamp, _signature(stamp))
    return match_item(item, _index(stamp), _signature(stamp))


def summary():
    from . import slot_dimension_matching as dimensions
    stamp = _stamp(); index = _index(stamp); token = _signature(stamp)
    items = []
    for item in slots.resource_entries():
        result = dimensions.matches(item,stamp,token) if item['document'].get('drawing_source') and item['document']['module'] != 'tx' else match_item(item, index, token)
        items.append({key: value for key, value in result.items() if key not in ('candidates', 'note', 'catalog_token')})
    labels={**LABELS,**dimensions.LABELS}
    return {'items': items, 'total': len(items), 'counts': {k: sum(i['status'] == k for i in items) for k in labels},
            'labels': labels, 'catalog_token': token, 'note': NOTE+' '+dimensions.NOTE}


def _document(**values):
    try:
        return slots.SlotDocument(**values).model_dump(mode='json')
    except ValidationError as error:
        raise InputError([{'field': '.'.join(str(k) for k in e['loc']),
                           'message': '来源型号无法直接建立槽型：' + e['msg']} for e in error.errors()]) from None


def draft(reference, record_id, application, catalog_token):
    """Return a reviewable copy; only the existing user-library save writes it."""
    item = slots.get(reference)
    if item['document'].get('drawing_source') and item['document']['module'] != 'tx':
        raise InputError([{'field':'reference', 'message':'此槽型已有表格尺寸，请直接另存为用户槽型；型号对比用于核对差异'}])
    stamp = _stamp(); token = _signature(stamp)
    if token != catalog_token:
        raise ConflictError('型号库已更新，请重新查询尺寸对应关系')
    result = match_item(item, _index(stamp), token)
    for candidate in result['candidates']:
        for source in candidate['sources']:
            if source['record_id'] != record_id or source['application_index'] != application:
                continue
            if candidate['missing_fields']:
                raise InputError([{'field': 'parameters', 'message': '来源型号槽尺寸不完整：' + '、'.join(candidate['missing_fields'])}])
            original = item['document']
            baseline = copy.deepcopy(source['geometry'])
            notes = NOTE + ' 仅带入所选型号的槽尺寸和外形基准；适用范围需按图纸核实。'
            if original['side'] == 'stator':
                baseline['hr0_mm'] = None  # A stator drawing supplies no rotor-neck reference.
            elif baseline.get('hr0_mm') is not None and baseline['hr0_mm'] < 0:
                baseline['hr0_mm'] = None
                notes += ' 原型号 hr0 为负的特殊槽形标记，保留参数原值，不作为普通槽口高度联动基准。'
            # The selected model supplies actual geometry. Do not carry all Excel
            # applications into the copy: another application's OD/gap must not
            # override the selected model's hr0 basis during apply().
            document = _document(side=original['side'], module='main', drawing=original['drawing'],
                parameters=copy.deepcopy(candidate['parameters']), geometry=baseline,
                source=f"型号尺寸关联：{source['model']} · {source['series']} · {source['record_id']}",
                notes=notes,
                dimension_source={'method': METHOD, 'slot_reference': reference, 'record_id': record_id,
                    'model': source['model'], 'series': source['series'], 'catalog_token': token,
                    'input_sha256': source['input_sha256'], 'source': source['source'],
                    'application': original['applications'][application],
                    'geometry_matches': source['geometry_matches']})
            return {'name': f"{item['name']} · {source['model']}"[:120], 'origin': 'user', 'document': document}
    raise NotFoundError('该型号不属于当前槽型的尺寸候选，请重新查询')
