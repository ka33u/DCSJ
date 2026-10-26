"""Replay committed nominal observations before spending an explicit extra budget.

Initialization always uses the original budget. Later budgets take effect at
their recorded attempt boundaries; cached snapshots are never recalculated.
"""
import copy
import hashlib
import json
from pathlib import Path
import uuid

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from . import engine, optimization as opt
from .parameters import validate
from .storage import ConflictError, NotFoundError, audit, connect, dumps, now

MAX_ADDITIONAL = 128
MAX_TOTAL = 1024
METHOD = 'nominal-search-replay-v1'
CONTINUATION_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
# 0.27.0 used the same replay and budget rules. Its raw int/float fingerprint
# check rejected some valid discrete inputs; accepting normalized inputs does
# not invalidate histories that already passed that stricter check.
COMPATIBLE_SHA256 = {CONTINUATION_SHA256, 'ec2e294d6a240f2dbc09b29b607b03bdf0ab4720a2ba67bff5b85f7d8157df30'}
RESOURCE_KEYS = ('kernel_sha256', 'wrapper_sha256', 'resource_package', 'resource_version')


class ExtensionInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    additional_evaluations: StrictInt = Field(ge=1, le=MAX_ADDITIONAL)


class ContinuationSearch(opt.SearchInput):
    """Internal only: external root searches still have the 128-call limit."""
    max_evaluations: StrictInt = Field(ge=2, le=MAX_TOTAL)


def fingerprint(value):
    # Same JSON encoding as engine provenance, including ASCII escaping.
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ConflictError('续算记录的预算或候选序号无效。')
    return value


def current_resources(request):
    context = engine.resource_context()
    if engine.engine_status(context)['status'] == 'unavailable':
        raise ConflictError('续算需要原任务使用的计算资源。')
    refs = {request.parameters['Paihao']}
    refs.update(ref for v in request.variables if v.key == 'Paihao' for ref in v.choices)
    materials = {}
    for ref in sorted(refs):
        if ref.startswith('user-material:'):
            from .library import calculation_material
            material = calculation_material(ref, request.parameters['f'], context['materials'])
        else:
            material = context['materials'].get(ref)
        if material is None:
            raise ConflictError(f'续算所需材料 {ref} 不可用。')
        materials[ref] = fingerprint(material)
    return {'kernel_sha256': context['kernel_sha256'], 'wrapper_sha256': engine.WRAPPER_SHA256,
            'resource_package': context['package_id'], 'resource_version': context['package_version'],
            'materials': materials}


def check_resources(request, expected):
    if current_resources(request) != expected:
        raise ConflictError('计算资源或材料版本已变化，不能沿用原寻优结果继续计算。')


def check_result(result, parameters, resources):
    provenance = result.get('provenance', {})
    normalized = validate(parameters)
    if (result.get('inputs') != normalized or provenance.get('input_sha256') != fingerprint(normalized)
            or any(provenance.get(k) != resources[k] for k in RESOURCE_KEYS)
            or not resources['materials'].get(parameters['Paihao'])
            or provenance.get('material_sha256') != resources['materials'][parameters['Paihao']]):
        raise ConflictError('计算快照的输入、计算核或材料指纹不一致，已保留原结果并停止续算。')


def restore(root_request, candidates, transitions, *, solver_sha256):
    """Restore deterministic search state from authenticated actual observations."""
    if solver_sha256 != opt.SOLVER_SHA256:
        raise ConflictError('寻优算法版本已变化，请按当前配置重新运行。')
    request = opt.SearchInput.model_validate(root_request)
    if request.tolerances or not candidates or len(candidates) > MAX_TOTAL:
        raise ConflictError('续算需要完整的名义候选记录。')
    timeline, previous = {}, 0
    for change in transitions:
        if not isinstance(change, dict) or set(change) != {'after', 'budget'}:
            raise ConflictError('续算预算变更记录不完整。')
        after = integer(change['after'], 1, len(candidates))
        budget = integer(change['budget'], after+1, min(MAX_TOTAL, after+MAX_ADDITIONAL))
        if after <= previous:
            raise ConflictError('续算预算变更顺序不一致。')
        timeline[after], previous = budget, after
    search, best_ordinal, successful, best_after = opt.Search(request), None, 0, []
    for index, candidate in enumerate(candidates):
        if index in timeline:
            search.request = search.request.model_copy(update={'max_evaluations': timeline[index]})
        if index >= search.request.max_evaluations or candidate['ordinal'] != index+1:
            raise ConflictError('已保存的候选序号或预算不完整。')
        expected = search.candidate()
        if expected is None or expected != candidate['parameters']:
            raise ConflictError('已保存的候选与原搜索轨迹不一致，不能续算。')
        if candidate.get('error') is not None:
            if (not isinstance(candidate['error'], str) or not candidate['error'] or candidate['feasible']
                    or candidate.get('calculation_id') is not None or candidate.get('objective') is not None
                    or candidate['violations'] != ['输入无效或数值核未完成此候选。']):
                raise ConflictError('失败候选记录不完整。')
            best_after.append(best_ordinal)
            continue
        values = candidate['values']
        violations = opt.assess(search.request, values)
        if not opt.in_region(search.request, expected):
            violations.append('方案不满足指定范围、步长、规格或尺寸联动；基准仅作为参考。')
        if (candidate['feasible'] != (not violations) or candidate['violations'] != violations
                or candidate['objectives'] != opt.objective_values(search.request, values)):
            raise ConflictError('候选约束或目标与原始计算快照不一致。')
        objective, improved = search.consider(expected, values, not violations)
        if objective != candidate['objective']:
            raise ConflictError('候选第一目标与计算快照不一致。')
        if improved:
            best_ordinal = index+1
        best_after.append(best_ordinal)
        successful += 1
    if len(candidates) in timeline:
        search.request = search.request.model_copy(update={'max_evaluations': timeline[len(candidates)]})
    return search, {'inherited_attempts': len(candidates), 'successful_attempts': successful,
                    'best_ordinal': best_ordinal, 'best_after': best_after,
                    'effective_budget': search.request.max_evaluations}


def read_history(identity, *, database=None, db=None):
    if db is None:
        with connect(database) as connection:
            connection.execute('BEGIN')
            return read_history(identity, db=connection)
    row = db.execute('SELECT * FROM optimization_jobs WHERE id=?', (identity,)).fetchone()
    if row is None:
        raise NotFoundError('寻优任务不存在')
    job = dict(row)
    rows = [dict(r) for r in db.execute('SELECT * FROM optimization_candidates WHERE job_id=? ORDER BY ordinal', (identity,))]
    assessments = [dict(r) for r in db.execute('SELECT * FROM optimization_assessments WHERE job_id=? ORDER BY ordinal', (identity,))]
    snapshots = {r['id']: r['result'] for r in db.execute('SELECT s.id,s.result FROM calculations s JOIN '
        'optimization_candidates c ON s.id=c.calculation_id WHERE c.job_id=?', (identity,))}
    return {'job': job, 'rows': rows, 'assessments': assessments, 'snapshots': snapshots}


def prefix_hash(history, count):
    rows = [{k:v for k,v in r.items() if k != 'job_id'} for r in history['rows'] if r['ordinal'] <= count]
    return fingerprint({'rows': rows,
        'assessments': [{k:v for k,v in r.items() if k != 'job_id'} for r in history['assessments'] if r['ordinal'] <= count],
        'snapshots': {r['calculation_id']: history['snapshots'].get(r['calculation_id']) for r in rows if r['calculation_id']}})


def _state(history):
    from . import jobs
    job = history['job']
    document = json.loads(job['request'])
    request = jobs.saved_request(document)
    if request.tolerances or document.get('verification') or document.get('feedback'):
        raise ConflictError('追加预算续算仅适用于名义寻优；公差复核请使用原复核流程。')
    count = integer(job['evaluated'], 1, MAX_TOTAL)
    if len(history['rows']) != count or job['budget'] != request.max_evaluations or count > job['budget']:
        raise ConflictError('任务预算与候选历史不一致。')
    metadata = document.get('continuation')
    root = metadata['root_request'] if metadata else request.model_dump()
    transitions = metadata['transitions'] if metadata else []
    resources = current_resources(request)
    if metadata:
        inherited = integer(metadata['inherited_attempts'], 1, count)
        additional = integer(metadata['additional_evaluations'], 1, MAX_ADDITIONAL)
        if (metadata['method'] != METHOD or metadata['continuation_sha256'] not in COMPATIBLE_SHA256
                or metadata['solver_sha256'] != opt.SOLVER_SHA256 or metadata['resources'] != resources
                or metadata['prefix_sha256'] != prefix_hash(history, inherited)
                or not transitions or transitions[-1] != {'after': inherited, 'budget': job['budget']}
                or job['budget'] != inherited+additional
                or {k:v for k,v in root.items() if k not in {'name','max_evaluations'}} !=
                   {k:v for k,v in request.model_dump().items() if k not in {'name','max_evaluations'}}):
            raise ConflictError('续算配置、资源或继承记录不一致，请重新运行。')
    assessments = {r['ordinal']: json.loads(r['record']) for r in history['assessments']}
    if len(assessments) != len(history['assessments']) or set(assessments)-set(range(1, count+1)):
        raise ConflictError('候选评价记录的序号不一致。')
    candidates = []
    for raw in history['rows']:
        candidate = dict(raw)
        candidate.update(parameters=json.loads(raw['parameters']), violations=json.loads(raw['violations']))
        if raw['feasible'] not in (0, 1):
            raise ConflictError('候选可行性记录无效。')
        candidate['feasible'] = bool(raw['feasible'])
        if raw['calculation_id']:
            result = json.loads(history['snapshots'][raw['calculation_id']])
            check_result(result, candidate['parameters'], resources)
            evaluation = result.get('optimization', {})
            values = opt.metrics(result, request)
            if (evaluation.get('solver_sha256') != opt.SOLVER_SHA256
                    or evaluation.get('algorithm') != request.algorithm
                    or evaluation.get('constraints') != [c.model_dump() for c in request.constraints]
                    or evaluation.get('objectives') != opt.objective_values(request, values)
                    or evaluation.get('constraint_values') != {c.metric: values.get(c.metric) for c in request.constraints}
                    or evaluation.get('violation_score') != opt.violation_score(request, values)
                    or evaluation.get('material_pricing') != opt.material_pricing(request, candidate['parameters'])
                    or (raw['ordinal'] in assessments and assessments[raw['ordinal']] != evaluation)):
                raise ConflictError('历史快照与寻优配置或算法版本不一致。')
            candidate.update(values=values, objectives=evaluation['objectives'])
        elif raw['error'] is None or raw['ordinal'] in assessments:
            raise ConflictError('候选缺少原始计算快照。')
        candidates.append(candidate)
    search, summary = restore(root, candidates, transitions, solver_sha256=opt.SOLVER_SHA256)
    best = candidates[summary['best_ordinal']-1]['calculation_id'] if summary['best_ordinal'] else None
    if not summary['successful_attempts']:
        raise ConflictError('原任务全部计算失败，缺少可核验的计算资源指纹；请重新运行。')
    if job['best_id'] != best or summary['effective_budget'] != job['budget']:
        raise ConflictError('已保存的最佳候选或预算与历史重放不一致。')
    if metadata:
        ordinal = summary['best_after'][inherited-1]
        inherited_best = candidates[ordinal-1]['calculation_id'] if ordinal else None
        if metadata['inherited_best_id'] != inherited_best:
            raise ConflictError('继承的最佳候选与原候选历史不一致。')
    return search, summary, resources, root, transitions


def state(history):
    try:
        return _state(history)
    except (KeyError, TypeError, ValueError, IndexError, AttributeError) as exc:
        if isinstance(exc, ConflictError):
            raise
        raise ConflictError('原任务记录不完整或当前资源无法验证，请重新运行。') from exc


def prepare(job_id, *, database=None):
    """Worker initialization; queued continuation rows already contain the prefix."""
    history = read_history(job_id, database=database)
    search, summary, resources, _, _ = state(history)
    metadata = json.loads(history['job']['request'])['continuation']
    if history['job']['evaluated'] != metadata['inherited_attempts']:
        raise ConflictError('任务已有新增候选，不能在同一任务内重复执行；请追加预算或重试。')
    return search, summary, resources


def create(identity, options, *, database=None, expected_metadata=None):
    from . import jobs
    from .execution import BusyError
    history = read_history(identity, database=database)
    source = history['job']
    if source['status'] not in jobs.TERMINAL:
        raise ConflictError('请先等待任务结束，或取消当前任务后再追加预算。')
    search, summary, resources, root, transitions = state(history)
    count = source['evaluated']
    budget = integer(count + options.additional_evaluations, 2, MAX_TOTAL)
    search.request = search.request.model_copy(update={'max_evaluations': budget})
    if copy.deepcopy(search).candidate() is None:
        raise ConflictError('此搜索已没有新的不重复候选；追加预算不会产生新计算。')
    previous = json.loads(source['request']).get('continuation')
    # A child cancelled before its first new attempt has not used its boundary.
    transitions = [change for change in transitions if change['after'] < count]
    transitions.append({'after': count, 'budget': budget})
    metadata = {'method': METHOD, 'continuation_sha256': CONTINUATION_SHA256,
        'solver_sha256': opt.SOLVER_SHA256, 'source_job_id': identity,
        'root_job_id': previous['root_job_id'] if previous else identity,
        'inherited_attempts': count, 'additional_evaluations': options.additional_evaluations,
        'inherited_best_id': source['best_id'], 'root_request': root, 'transitions': transitions,
        'resources': resources, 'prefix_sha256': prefix_hash(history, count)}
    if expected_metadata is not None and metadata != {**expected_metadata, 'continuation_sha256': CONTINUATION_SHA256}:
        raise ConflictError('原续算的源任务或固定配置已变化，不能以不同历史重试。')
    document = search.request.model_dump()
    document['name'] = root['name'][:100] + f' · 续算至 {budget} 次'
    document['continuation'] = metadata
    child_id, timestamp = str(uuid.uuid4()), now()
    with connect(database) as db:
        db.execute('BEGIN IMMEDIATE')
        if read_history(identity, db=db) != history:
            raise ConflictError('源任务记录已变化，请重新读取后追加预算。')
        if db.execute("SELECT count(*) FROM optimization_jobs WHERE status IN ('queued','running','cancelling')").fetchone()[0] >= 8:
            raise BusyError('待处理寻优任务已达 8 个，请等待或取消现有任务。')
        db.execute('INSERT INTO optimization_jobs(id,name,request,status,created_at,updated_at,evaluated,budget,best_id) '
                   "VALUES (?,?,?,'queued',?,?,?,?,?)",
                   (child_id, document['name'], dumps(document), timestamp, timestamp, count, budget, source['best_id']))
        db.execute('INSERT INTO optimization_candidates SELECT ?,ordinal,parameters,calculation_id,objective,feasible,violations,error '
                   'FROM optimization_candidates WHERE job_id=?', (child_id, identity))
        db.execute('INSERT INTO optimization_assessments SELECT ?,ordinal,record FROM optimization_assessments WHERE job_id=?', (child_id, identity))
        audit(db, 'optimization.extended', child_id, {'source_job_id': identity, 'inherited': count,
                                                    'additional': options.additional_evaluations, 'budget': budget})
    return jobs.get(child_id, database=database)
