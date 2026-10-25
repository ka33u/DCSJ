"""Frozen post-selection checks on fresh, jittered tolerance strata.

This is finite coverage testing, not an estimate of manufacturing yield.
The server constructs and persists the plan; API callers cannot supply it.
"""
import hashlib
import json
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator
from .optimization import SearchInput
from . import manufacturing
from .storage import ConflictError, connect

COUNTS = (4, 8, 16, 32)
METHOD = 'post-selection-jittered-lhs-v1'
RESOURCE_KEYS = ('kernel_sha256', 'wrapper_sha256', 'resource_package', 'material_sha256')


class VerificationInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    candidate_ordinal: StrictInt | None = Field(default=None, ge=1)
    interior_samples: StrictInt = 8
    seed: StrictInt | None = Field(default=None, ge=0, le=2**32-1)

    @model_validator(mode='after')
    def valid_count(self):
        if self.interior_samples not in COUNTS:
            raise ValueError('补充采样数须为 4、8、16 或 32')
        return self


class VerificationSearch(SearchInput):
    minimum_groups: ClassVar[int] = 1


def fingerprint(parameters):
    """Reject inputs that coincide at the numerical kernel's float32 precision."""
    p = {k: (manufacturing.single(v) or 0.0) if isinstance(v, (int, float)) else v for k, v in parameters.items()}
    return json.dumps(p, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def additional_samples(request, nominal, count, seed, forbidden):
    columns = {}
    for t in sorted(request.tolerances, key=lambda t: t.key):
        prefix = f'{METHOD}:{seed}:{count}:{t.key}:'
        columns[t.key] = (t.delta, prefix, sorted(range(count), key=lambda i: hashlib.sha256((prefix+str(i)).encode()).digest()))
    used, result = set(forbidden), []
    for row in range(count):
        for attempt in range(128):
            offsets = {}
            for key, (delta, prefix, bins) in columns.items():
                digest = hashlib.sha256(f'{prefix}jitter:{row}:{attempt}'.encode()).digest()
                # Open cell interior; 32 bits leave margin from floating-point endpoints.
                jitter = (int.from_bytes(digest[:4], 'big') + .5) / 2**32
                offsets[key] = (2*(bins[row]+jitter)/count-1)*delta
            p = {**nominal, **{k: nominal[k]+v for k,v in offsets.items()}}
            if 'g' in offsets and request.tolerance_gap_mode == 'fixed_neck_bottom':
                p['hr0'] = nominal['hr0'] - offsets['g']
            key = fingerprint(p)
            if key not in used:
                used.add(key)
                result.append({'label':f'补充采样 {row+1}', 'kind':'interior', 'phase':'additional',
                               'offsets':offsets, 'parameters':p})
                break
        else:
            raise ConflictError('此公差范围在计算精度内无法生成足够的新采样点，请增大公差或减少补充点数。')
    return result


def create(identity, options, *, database=None):
    from . import jobs
    source = jobs.get(identity, database=database)
    if source['status'] not in jobs.TERMINAL:
        raise ConflictError('请等待源任务结束，再选择候选进行补充复核。')
    candidate = next((c for c in source['candidates'] if
        c['ordinal'] == options.candidate_ordinal), None) if options.candidate_ordinal is not None else next(
        (c for c in source['candidates'] if c['calculation_id'] == source['best_id'] and c['calculation_id']), None)
    robust = (candidate or {}).get('robustness') or {}
    if not source['request'].get('tolerances') or not candidate or not candidate['feasible'] or not candidate['calculation_id'] or robust.get('status') != 'complete':
        raise ConflictError('请选择已完成全部公差情景、满足约束且保存了快照的候选。')
    samples = robust.get('samples', [])
    if not samples or len(samples) != robust.get('expected_samples') or any(s.get('error') or not s.get('calculation_id') for s in samples):
        raise ConflictError('源候选的公差快照不完整，不能进行补充复核。')
    budget = len(samples) + options.interior_samples
    if budget > 128:
        raise ConflictError('旧情景与新点合计超过单次 128 次预算，请减少补充点数。')
    ancestor = source['request'].get('verification') or {}
    seed = options.seed if options.seed is not None else (ancestor.get('seed', source['request']['seed'])+1) % 2**32
    document = {k:v for k,v in source['request'].items() if k != 'verification'}
    document.update(parameters=candidate['parameters'], max_evaluations=budget, name=source['name'][:105]+' · 补充复核')
    request = VerificationSearch.model_validate(document)
    root_id = ancestor.get('root_job_id', identity)
    forbidden = {fingerprint(s['parameters']) for s in samples}
    with connect(database) as db:
        row = db.execute('SELECT result FROM calculations WHERE id=?', (candidate['calculation_id'],)).fetchone()
        if row is None:
            raise ConflictError('源计算快照不存在。')
        snapshot = json.loads(row['result'])
        for row in db.execute('SELECT record FROM optimization_samples WHERE job_id=?', (root_id,)):
            forbidden.add(fingerprint(json.loads(row['record'])['parameters']))
    provenance = snapshot['provenance']
    if any(not provenance.get(k) for k in ('kernel_sha256', 'wrapper_sha256', 'material_sha256')):
        raise ConflictError('源快照缺少计算核或材料指纹，请先重新运行搜索。')
    plan = [{k:s[k] for k in ('label','offsets','parameters')} | {
        'phase':'recheck', 'source_calculation_id':s['calculation_id'], 'source_sample_index':s['index']}
        for s in samples]
    plan.extend(additional_samples(request, candidate['parameters'], options.interior_samples, seed, forbidden))
    for index, s in enumerate(plan):
        s['index'] = index
    metadata = {'method':METHOD, 'seed':seed, 'interior_samples':options.interior_samples, 'prior_samples':len(samples),
                'source_job_id':identity, 'root_job_id':root_id, 'source_candidate_ordinal':candidate['ordinal'],
                'source_calculation_id':candidate['calculation_id'],
                'source_solver_sha256':snapshot.get('optimization',{}).get('solver_sha256'),
                'resource_fingerprint':{k:provenance.get(k) for k in RESOURCE_KEYS}, 'plan':plan}
    return jobs.create(request, database=database, verification=metadata)


def summary_metadata(metadata):
    return {k:v for k,v in metadata.items() if k != 'plan'}


def annotate(record, metadata):
    record.update(method=METHOD, verification=summary_metadata(metadata),
        sampling_plan={'interior_method':METHOD, 'seed':metadata['seed'], 'interior_samples':metadata['interior_samples']},
        note=f"补充采样复核：固定源任务 {metadata['source_job_id']} 的候选 #{metadata['source_candidate_ordinal']}，"
             f"重新计算 {metadata['prior_samples']} 个已有情景，再增加 {metadata['interior_samples']} 个未参与原搜索的新点（种子 {metadata['seed']}）。"
             '每个公差维度分层后在层内取点；新点与原搜索全部已计算输入在计算精度内不重合。'
             '旧情景与新点均须满足约束；原搜索结论独立保留。有限采样不保证整个区间合格，不推定合格概率。')
