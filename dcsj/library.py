"""User-authored, immutable revisions that never overwrite the installed resource catalog."""
from bisect import bisect_right
from copy import deepcopy
import hashlib
import json
import math
import re
from typing import Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from .parameters import InputError, validate, input_parameters
from .storage import ConflictError, NotFoundError, audit, connect, dumps, now


class ModelDocument(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    parameters: dict[str, str | float | int | None]
    source: str = Field(default='手工录入', min_length=1, max_length=2000)
    notes: str = Field(default='', max_length=4000)

    @field_validator('parameters')
    @classmethod
    def valid_parameters(cls, values):
        if len(values) > 500 or any(len(k) > 100 for k in values):
            raise ValueError('参数数量或名称长度超过限制')
        if any(isinstance(value, str) and len(value) > 2000 for value in values.values()):
            raise ValueError('参数文本超过 2000 字符')
        return validate(values, strict=True)


class MaterialDocument(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    source: str = Field(min_length=1, max_length=2000)
    notes: str = Field(default='', max_length=4000)
    frequency_hz: float = Field(gt=0, le=100000)
    h_unit: Literal['A/m', 'A/cm'] = 'A/m'
    loss_unit: Literal['W/kg', 'W/m3', 'kernel_native'] = 'W/kg'
    bh: list[tuple[float, float]] = Field(min_length=2, max_length=2000, description='磁密 B 使用 T，第二列 H 的单位由 h_unit 指定')
    loss: list[tuple[float, float]] = Field(min_length=2, max_length=2000, description='磁密 B 使用 T，第二列损耗的单位由 loss_unit 指定')

    @model_validator(mode='after')
    def valid_curves(self):
        if not self.source.strip():
            raise ValueError('请填写材料来源')
        for label, points in [('磁化', self.bh), ('损耗', self.loss)]:
            if any(not math.isfinite(v) or not 0 <= v <= 1e9 for row in points for v in row):
                raise ValueError(label + '曲线须为非负有限数值，最大 10⁹')
            if any(a[0] >= b[0] for a, b in zip(points, points[1:])):
                raise ValueError(label + '曲线的磁密必须严格递增，不允许重复点')
            if any(a[1] > b[1] for a, b in zip(points, points[1:])):
                raise ValueError(label + '曲线值必须单调不减')
        return self


def _kind(kind):
    if kind not in {'models', 'materials'}:
        raise NotFoundError('用户数据类型不存在')


def compatibility(document):
    reasons = []
    if document['loss_unit'] != 'kernel_native':
        reasons.append('损耗单位尚未映射到原计算核；可管理和绘图，暂不能用于该计算核')
    if document['frequency_hz'] != 50:
        reasons.append('当前用户材料适配仅支持 50 Hz，其他频率可保存但不能直接计算')
    for key in ('bh', 'loss'):
        if document[key][0][0] > .5 or document[key][-1][0] < 1.88:
            reasons.append(('磁化' if key == 'bh' else '损耗') + '曲线须覆盖 0.5–1.88 T；不进行外推')
    return {'compatible': not reasons, 'reasons': reasons,
            'method': '磁化 H 转为 A/cm，在原采样轴内线性插值；损耗只接受显式声明的原计算核数值。'}


def _decode(row):
    result = dict(row)
    result['document'] = json.loads(result['document'])
    result['reference'] = ('user-model:' if result['kind'] == 'models' else 'user-material:') + result['id'] + '@' + str(result['revision'])
    result['document_sha256'] = hashlib.sha256(dumps(result['document']).encode()).hexdigest()
    if result['kind'] == 'models':
        result['input_parameters'] = input_parameters(result['document']['parameters'])
    if result['kind'] == 'materials':
        result['compatibility'] = compatibility(result['document'])
    return result


def get(kind, identity, revision=None):
    _kind(kind)
    with connect() as db:
        row = db.execute('SELECT e.id,e.kind,r.name,r.revision,e.archived,r.created_at,e.updated_at,r.document '
                         'FROM library_entries e JOIN library_revisions r ON e.id=r.entry_id '
                         'AND r.revision=coalesce(?,e.revision) WHERE e.id=? AND e.kind=?', (revision, identity, kind)).fetchone()
    if row is None:
        raise NotFoundError('用户记录或版本不存在')
    return _decode(row)


def list_entries(kind, include_archived=False, limit=50, offset=0):
    _kind(kind)
    where = 'e.kind=?' + ('' if include_archived else ' AND e.archived=0')
    with connect() as db:
        count = db.execute('SELECT count(*) FROM library_entries e WHERE ' + where, (kind,)).fetchone()[0]
        rows = db.execute('SELECT e.*,r.document FROM library_entries e JOIN library_revisions r '
                          'ON r.entry_id=e.id AND r.revision=e.revision WHERE ' + where +
                          ' ORDER BY e.updated_at DESC,e.id LIMIT ? OFFSET ?', (kind, limit, offset)).fetchall()
    return {'items': [_decode(row) for row in rows], 'total': count, 'limit': limit, 'offset': offset}


def save(kind, name, document, identity=None, expected_revision=None):
    _kind(kind)
    name = name.strip()
    if not name or len(name) > 120:
        raise ValueError('名称须为 1–120 个字符')
    try:
        document = (ModelDocument if kind == 'models' else MaterialDocument).model_validate(document).model_dump(mode='json')
    except ValidationError as error:
        raise InputError([{'field': '.'.join(str(x) for x in item['loc']), 'message': item['msg']}
                          for item in error.errors()]) from None
    encoded, timestamp = dumps(document), now()
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if identity:
            previous = db.execute('SELECT * FROM library_entries WHERE id=? AND kind=?', (identity, kind)).fetchone()
            if previous is None:
                raise NotFoundError('用户记录不存在')
            if previous['revision'] != expected_revision or previous['archived']:
                raise ConflictError('该用户记录已变化或已归档，请刷新或另存')
            revision = previous['revision'] + 1
            db.execute('UPDATE library_entries SET name=?,revision=?,updated_at=? WHERE id=?', (name, revision, timestamp, identity))
        else:
            if expected_revision is not None:
                raise ConflictError('新增记录不接受 expected_revision')
            identity, revision = 'ul_' + uuid.uuid4().hex, 1
            db.execute('INSERT INTO library_entries(id,kind,name,revision,created_at,updated_at) VALUES (?,?,?,?,?,?)',
                       (identity, kind, name, revision, timestamp, timestamp))
        db.execute('INSERT INTO library_revisions VALUES (?,?,?,?,?)', (identity, revision, name, encoded, timestamp))
        audit(db, 'library.saved', identity, {'kind': kind, 'revision': revision})
    return get(kind, identity)


def revisions(kind, identity):
    get(kind, identity)
    with connect() as db:
        rows = db.execute('SELECT revision,name,created_at FROM library_revisions WHERE entry_id=? ORDER BY revision DESC', (identity,)).fetchall()
    return {'items': [dict(row) for row in rows]}


def archive(kind, identity, expected_revision, archived):
    _kind(kind)
    with connect() as db:
        changed = db.execute('UPDATE library_entries SET archived=?,updated_at=? WHERE id=? AND kind=? AND revision=?',
                             (int(archived), now(), identity, kind, expected_revision)).rowcount
        if not changed:
            raise ConflictError('用户记录已变化，请刷新后重试')
        audit(db, 'library.archived' if archived else 'library.restored', identity)
    return get(kind, identity)


def from_reference(reference, kind):
    prefix = 'user-model:' if kind == 'models' else 'user-material:'
    match = re.fullmatch(re.escape(prefix) + r'(ul_[a-f0-9]{32})@([1-9][0-9]{0,9})', reference)
    if not match:
        raise NotFoundError('用户数据引用无效，请使用包含固定版本的引用')
    return get(kind, match[1], int(match[2]))


def model(reference):
    entry = from_reference(reference, 'models')
    parameters = input_parameters(entry['document']['parameters'])
    from .semantics import electrical
    return {'id': reference, 'model': entry['name'], 'series': '用户录入',
            'parameters': parameters, 'inputs': parameters, 'electrical': electrical(parameters), 'historical_results': {},
            'source': {'path': '用户型号库', 'description': entry['document']['source'],
                       'id': entry['id'], 'revision': entry['revision'], 'sha256': entry['document_sha256']}}


def material(reference):
    from .curves import material_plot
    entry = from_reference(reference, 'materials')
    doc = entry['document']
    return {'id': reference, 'name': entry['name'], 'calculation_key': reference,
            'compatibility': entry['compatibility'], 'frequency_hz': doc['frequency_hz'],
            'loss_curve_frequency_hz': doc['frequency_hz'], 'loss_unit': doc['loss_unit'],
            'source': {'path': '用户材料库', 'description': doc['source'], 'revision': entry['revision'], 'sha256': entry['document_sha256']},
            'warnings': entry['compatibility']['reasons'],
            'curves': [{'id': key, 'title': title, 'x_label': '磁密 B (T)', 'y_label': unit,
                        'series': [{'name': entry['name'], 'points': [{'x': x, 'y': y} for x, y in doc[key]]}],
                        'plot': material_plot([{'x': x, 'y': y} for x, y in doc[key]], key, doc['h_unit'], doc['loss_unit'])}
                       for key, title, unit in [('bh', '磁化曲线', 'H (' + doc['h_unit'] + ')'),
                                                ('loss', '损耗曲线', doc['loss_unit'])]]}


def material_choices():
    # Fixed references keep a running search independent of later manual edits.
    with connect() as db:
        rows = db.execute("SELECT e.*,r.document FROM library_entries e JOIN library_revisions r "
                          "ON r.entry_id=e.id AND r.revision=e.revision WHERE e.kind='materials' AND e.archived=0").fetchall()
    return [item['reference'] for row in rows if (item := _decode(row))['compatibility']['compatible']]


def _interpolate(points, axis):
    xs = [row[0] for row in points]
    result = []
    for x in axis:
        if not xs[0] <= x <= xs[-1]:
            raise ValueError('材料曲线不允许外推')
        i = min(max(bisect_right(xs, x) - 1, 0), len(xs) - 2)
        x0, y0 = points[i]; x1, y1 = points[i + 1]
        result.append(y0 + (y1 - y0) * (x - x0) / (x1 - x0))
    return result


def calculation_material(reference, frequency, kernel_materials):
    from .engine import CalculationError
    entry = from_reference(reference, 'materials')
    doc = entry['document']
    if not entry['compatibility']['compatible'] or not math.isclose(frequency, doc['frequency_hz'], rel_tol=0, abs_tol=1e-9):
        raise CalculationError('用户材料与当前计算条件不兼容：' + '；'.join(entry['compatibility']['reasons'] or ['设计频率与材料频率不同']))
    base = next((m for m in kernel_materials.values() if m['kind'] == 'mdb_custom'), None)
    if base is None:
        raise CalculationError('已安装资源不含兼容的用户材料计算槽位')
    material = deepcopy(base)
    bh_axis = [round(.5 + i * .05, 2) for i in range(15)] + [round(1.22 + i * .02, 2) for i in range(34)]
    loss_axis = [round(.5 + i * .1, 2) for i in range(8)] + [round(1.22 + i * .02, 2) for i in range(34)]
    h_values = [v / (100 if doc['h_unit'] == 'A/m' else 1) for v in _interpolate(doc['bh'], bh_axis)]
    p_values = _interpolate(doc['loss'], loss_axis)
    material.update(name=entry['name'], kind='user_native', slot=5, H_values=h_values, P_values=p_values,
                    user_reference=reference, user_document_sha256=entry['document_sha256'], user_source=doc['source'])
    material['const_values'][434:483] = h_values
    material['const_values'][651:693] = p_values
    return material
