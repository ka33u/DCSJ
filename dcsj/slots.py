"""Versioned slot references, native parameter sets and explicit geometry checks.

Reference drawings alone never supply undocumented slot dimensions. Main and TX
parameter sets stay separate: their type codes and units are not interchangeable.
"""
from functools import lru_cache
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from .parameters import InputError, field_info
from .storage import NotFoundError, dumps

MAIN_KEYS = {
    'stator': 'Q1 b01 hs0 zs1 bs1 bs2 hs12'.split(),
    'rotor': 'Q2 zc bc02 hr0 zs2 br1 br2 br3 br4 hr12 hr3 hr4'.split() +
             [f'{key}{i}' for i in range(1, 10) for key in ('N', 'A', 'B', 'L')],
}
TX_KEYS = {'stator': 'Q1 b01 Hs0 DXJJ bs1 bs2 hs12'.split(),
           'rotor': 'Q2 M2 b02 hr0 ZXJJ br1 br2 br3 br4 hr12 hr3'.split()}
KEYS = {'main': MAIN_KEYS, 'tx': TX_KEYS, 'reference': {'stator': [], 'rotor': []}}
SIDES = {'stator': '定子', 'rotor': '转子'}


class Geometry(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    bore_mm: float | None = Field(default=None, gt=0, le=100000)
    rotor_outer_mm: float | None = Field(default=None, gt=0, le=100000)
    gap_mm: float | None = Field(default=None, gt=0, le=1000)
    stator_outer_mm: float | None = Field(default=None, gt=0, le=100000)
    hr0_mm: float | None = Field(default=None, ge=0, le=1000)


class Application(BaseModel):
    model_config = ConfigDict(extra='forbid')
    series: str = Field(default='', max_length=80)
    frame: str = Field(default='', max_length=80)
    poles: int | None = Field(default=None, ge=2, le=100)
    power: str = Field(default='', max_length=80)
    material: str = Field(default='', max_length=80)
    geometry: Geometry = Field(default_factory=Geometry)
    stack_mm: float | None = Field(default=None, gt=0, le=100000)
    source: str = Field(default='', max_length=400)


class SlotDocument(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    side: Literal['stator', 'rotor']
    module: Literal['reference', 'main', 'tx'] = 'reference'
    drawing: str = Field(default='', max_length=120)
    parameters: dict[str, float] = Field(default_factory=dict)
    geometry: Geometry = Field(default_factory=Geometry)
    applications: list[Application] = Field(default_factory=list, max_length=1000)
    source: str = Field(default='手工录入', min_length=1, max_length=2000)
    notes: str = Field(default='', max_length=4000)
    reference_area_mm2: float | None = Field(default=None, gt=0, le=1e9,
        description='图纸给出的单槽面积；仅作参考，不替换本次参数的几何面积')

    @model_validator(mode='after')
    def validate_dimensions(self):
        if set(self.parameters) - set(KEYS[self.module][self.side]):
            raise ValueError('槽型含其他模块或非本侧的参数')
        if any(not math.isfinite(v) or abs(v) > 1e6 for v in self.parameters.values()):
            raise ValueError('槽尺寸必须是有限数值，绝对值不超过 1000000')
        integers = {'Q1', 'Q2', 'zc', 'M2'} | {f'N{i}' for i in range(1, 10)}
        if any(v != int(v) for k, v in self.parameters.items() if k in integers):
            raise ValueError('槽数、槽型代码和元素类型须为整数')
        if not self.source.strip():
            raise ValueError('请填写资料来源')
        return self


class Bindings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    stator: str | None = Field(default=None, min_length=1, max_length=200)
    rotor: str | None = Field(default=None, min_length=1, max_length=200)


def schema():
    from .tx_parameters import schema as tx_schema
    tx_fields = {f['key']: f for f in tx_schema()['fields']}
    return {'document': SlotDocument.model_json_schema(), 'fields': {
        module: {side: [(field_info(k) | {'key': k}) if module == 'main' else tx_fields[k]
                        for k in keys] for side, keys in sides.items()}
        for module, sides in KEYS.items() if module != 'reference'}}


def validate_catalog(data):
    if not isinstance(data, dict) or data.get('format_version') != 1 or not isinstance(data.get('items'), list) or len(data['items']) > 20000:
        raise ValueError('槽型资源结构无效')
    seen = set()
    for item in data['items']:
        if not re.fullmatch(r'[a-z0-9_-]{1,80}', item.get('id', '')) or item['id'] in seen:
            raise ValueError('槽型资源 ID 无效或重复')
        if not isinstance(item.get('name'), str) or not 0 < len(item['name']) <= 120:
            raise ValueError('槽型名称无效')
        SlotDocument.model_validate(item['document'])
        seen.add(item['id'])
    return len(seen)


@lru_cache(maxsize=8)
def _resource(path, digest):
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        from .resources import ResourceError
        raise ResourceError('槽型资源校验失败')
    data = json.loads(raw)
    validate_catalog(data)
    return data['items']


def resource_entries(package=None):
    from . import resources
    package = resources.current() if package is None else package
    if not package or 'slot_catalog.json' not in package['files']:
        return []
    path = resources.asset('slot_catalog.json', package)
    return [{**item, 'kind': 'slots', 'revision': 1, 'archived': False,
             'reference': f"resource-slot:{package['package_id']}:{item['id']}",
             'resource_version': package['version'], 'origin': 'resource'}
            for item in _resource(str(path), package['files']['slot_catalog.json']['sha256'])]


def get(reference):
    from . import library, resources
    application = re.fullmatch(r'(.+)#app([0-9]{1,4})', reference)
    if application:
        item = get(application[1]);index = int(application[2])
        if index >= len(item['document']['applications']):
            raise NotFoundError('槽型适用规格不存在')
        return {**item, 'reference': reference, 'document': {**item['document'],
                'geometry': item['document']['applications'][index]['geometry']}}
    if reference.startswith('user-slot:'):
        return library.from_reference(reference, 'slots') | {'origin': 'user'}
    match = re.fullmatch(r'resource-slot:([a-f0-9]{64}):([a-z0-9_-]{1,80})', reference)
    if not match:
        raise NotFoundError('槽型引用无效')
    manifest = resources.directory() / match[1] / 'manifest.json'
    if not manifest.is_file():
        raise NotFoundError('槽型引用的私有资源版本尚未安装')
    package = json.loads(manifest.read_text()) | {'package_id': match[1]}
    item = next((r for r in resource_entries(package) if r['id'] == match[2]), None)
    if item is None:
        raise NotFoundError('槽型不存在于引用的资源版本')
    return item


def search(q='', side=None, module=None, source='all', include_archived=False, limit=40, offset=0):
    from . import library
    items = resource_entries() if source != 'user' else []
    if source != 'resource':
        # Fetch all revisions currently visible; searching occurs before pagination.
        from .storage import connect
        with connect() as db:
            rows = db.execute('SELECT e.*,r.document FROM library_entries e JOIN library_revisions r '
                              'ON r.entry_id=e.id AND r.revision=e.revision WHERE e.kind=? ' +
                              ('' if include_archived else 'AND e.archived=0 ') + 'ORDER BY e.updated_at DESC', ('slots',)).fetchall()
        items = [library._decode(r) | {'origin': 'user'} for r in rows] + items
    terms = q.casefold().split()
    items = [item for item in items if (not side or item['document']['side'] == side)
             and (not module or item['document']['module'] in (module, 'reference'))
             and all(term in (item['name'] + ' ' + dumps(item['document'])).casefold() for term in terms)]
    return {'items': items[offset:offset+limit], 'total': len(items), 'limit': limit, 'offset': offset}


def resolve(bindings=None, module='main'):
    bindings = Bindings.model_validate(bindings or {}).model_dump(exclude_none=True)
    resolved = {}
    for side, ref in bindings.items():
        item = get(ref)
        if item['document']['side'] != side or item['document']['module'] not in (module, 'reference'):
            raise InputError([{'field': side, 'message': '槽型侧别或计算模块不匹配'}])
        resolved[side] = item
    return resolved


def geometry(parameters, module='main'):
    keys = ('D3', 'G', 'D1', 'hr0') if module == 'tx' else ('Di1', 'g', 'D1', 'hr0')
    factor = 10 if module == 'tx' else 1
    def value(key):
        try:
            v = float(parameters[key]) * factor
            return v if math.isfinite(v) else None
        except (KeyError, TypeError, ValueError):
            return None
    bore, gap, outer, h0 = [value(k) for k in keys]
    return {'bore_mm': bore, 'gap_mm': gap, 'stator_outer_mm': outer,
            'rotor_outer_mm': bore-2*gap if bore is not None and gap is not None else None, 'hr0_mm': h0}


def geometry_check(parameters, module='main', reference=None):
    g = geometry(parameters, module)
    checks = []
    bore, gap, d2, h0 = (g[k] for k in ('bore_mm', 'gap_mm', 'rotor_outer_mm', 'hr0_mm'))
    if bore is not None and gap is not None:
        checks.append({'key': 'airgap', 'passed': gap > 0 and d2 > 0,
                       'message': f'D2 = {bore:g} − 2 × {gap:g} = {d2:g} mm'})
        outer = g['stator_outer_mm']
        shaft_key = 'D4' if module == 'tx' else 'Di2'
        try:
            shaft = float(parameters[shaft_key]) * (10 if module == 'tx' else 1)
        except (KeyError, TypeError, ValueError):
            shaft = None
        if shaft is not None and outer is not None:
            checks.append({'key':'diameter_order','passed':0 <= shaft < d2 < bore < outer,
                           'message':f'轴孔 {shaft:g} < 转子外径 {d2:g} < 定子内径 {bore:g} < 定子外径 {outer:g} mm'})
    if h0 is not None and d2 is not None:
        checks.append({'key': 'neck', 'passed': 0 <= h0 < d2/2,
                       'message': f'槽颈底直径 D2 − 2 × hr0 = {d2-2*h0:g} mm'})
    ref = reference or {}
    if d2 is not None and ref.get('rotor_outer_mm') is not None:
        rd = ref['rotor_outer_mm']
        checks.append({'key': 'reference_diameter', 'passed': math.isclose(d2, rd, abs_tol=.001, rel_tol=0),
                       'message': f'本次转子外径 {d2:g} mm；参考外径 {rd:g} mm；差值 {d2-rd:+.4g} mm'})
        if ref.get('hr0_mm') is not None and h0 is not None:
            expected = ref['hr0_mm'] + (d2-rd)/2
            checks.append({'key': 'hr0_link', 'passed': expected >= 0 and math.isclose(h0, expected, abs_tol=.001, rel_tol=0),
                           'expected_hr0_mm': expected,
                           'message': f'若同一冲片槽颈底位置不变，hr0 应为 {expected:g} mm；当前 {h0:g} mm'})
    return {'geometry': g, 'checks': checks,
            'hr0_reference_available': ref.get('rotor_outer_mm') is not None and ref.get('hr0_mm') is not None,
            'tolerance_mm': .001}


def _path_area(commands):
    """Green's theorem for lines and cubic Beziers (exact polynomial integral)."""
    start = current = commands[0][1:3]
    integral = 0
    for command in [*commands[1:], ['L', *start]]:
        if command[0] == 'L':
            end = command[1:3]
            integral += current[0]*end[1]-end[0]*current[1]
        elif command[0] == 'C':
            points = [current, command[1:3], command[3:5], command[5:7]]
            polys = []
            for axis in (0, 1):
                a,b,c,d = [p[axis] for p in points]
                polys.append([a, 3*(b-a), 3*(a-2*b+c), d-a+3*(b-c)])
            x,y = polys
            integral += sum((x[i]*j*y[j]-y[i]*j*x[j])/(i+j) for i in range(4) for j in range(1,4))
            end = points[-1]
        else:
            raise ValueError('Unsupported area path')
        current = end
    return abs(integral)/2


def areas(parameters, module='main'):
    result = {side: {'value_mm2': None, 'method': 'unavailable', 'label': '单槽几何面积（含槽口）',
                     'note': '当前槽型未建立面积公式'} for side in SIDES}
    if module != 'main':
        return result
    from .slot_diagrams import slot_drawings
    for panel in slot_drawings(parameters):
        if panel['supported'] and panel.get('paths') and panel.get('geometry', {}).get('scale'):
            value = _path_area(panel['paths'][0]['commands']) / panel['geometry']['scale']**2
            if math.isfinite(value) and value > 0:
                result[panel['id']].update(value_mm2=value, method='profile-outline-v1',
                    note='按尺寸轮廓积分，含槽口、不扣绝缘；圆弧采用绘图三次曲线近似，不等同于导体有效面积')
    return result


def snapshot(parameters, bindings=None, module='main', resolved=None, details=None):
    resolved = resolve(bindings, module) if resolved is None else resolved
    area = areas(parameters, module)
    if module == 'tx' and details and details.get('stator_slot_area_mm2') is not None:
        area['stator'] = {'value_mm2': details['stator_slot_area_mm2'], 'method': 'tx-kernel-output',
                          'label': 'TX 原核单槽面积', 'note': '取本次计算明细的定子槽面积，单位 mm²'}
    entries = []
    for side in SIDES:
        item = resolved.get(side)
        doc = item['document'] if item else None
        def equal(key, value):
            try:
                return math.isclose(float(parameters[key]), value, rel_tol=1e-9, abs_tol=1e-9)
            except (KeyError, TypeError, ValueError):
                return False
        changes = [k for k,v in doc['parameters'].items() if not equal(k,v)] if doc else []
        entries.append({'side': side, 'name': item['name'] if item else '手工参数',
                        'drawing': doc['drawing'] if doc else '', 'reference': item['reference'] if item else None,
                        'revision': item['revision'] if item else None, 'document': doc,
                        'status': ('modified' if changes else 'matched') if doc and doc['parameters'] else ('reference_only' if doc else 'manual'),
                        'changed_fields': changes, 'area': area[side]})
    rotor = resolved.get('rotor', {}).get('document', {})
    return {'entries': entries, 'geometry_check': geometry_check(parameters, module, rotor.get('geometry'))}


def capture(parameters, side, module='main'):
    try:
        return SlotDocument(side=side, module=module,
                            parameters={k:parameters[k] for k in KEYS[module][side] if k in parameters},
                            geometry=geometry(parameters, module), source='从当前设计提取').model_dump(mode='json')
    except ValidationError as error:
        raise InputError([{'field':'.'.join(str(k) for k in e['loc']), 'message':e['msg']} for e in error.errors()]) from None


def apply(reference, parameters, module='main', application=None, include_geometry=False):
    if application is not None:
        reference = reference.split('#app')[0] + '#app' + str(application)
    item = get(reference);doc = item['document'];side = doc['side']
    resolve({side:reference}, module)
    changes = dict(doc['parameters'])
    ref = doc['geometry']
    if include_geometry:
        factor = 10 if module == 'tx' else 1
        mapping = {'bore_mm': 'D3' if module == 'tx' else 'Di1', 'gap_mm': 'G' if module == 'tx' else 'g', 'stator_outer_mm': 'D1'}
        changes.update({key:ref[name]/factor for name,key in mapping.items() if ref.get(name) is not None})
    result = {**parameters, **changes}
    return {'parameters': result, 'binding': {side:reference},
            'changes': [{'key':k, 'before':parameters.get(k), 'after':v} for k,v in changes.items() if parameters.get(k) != v],
            'has_dimensions': bool(doc['parameters']), 'geometry_check': geometry_check(result, module, ref)}
