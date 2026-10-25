"""Continue a bounded search with every previously verified tolerance scenario."""
import math
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from .storage import ConflictError

METHOD = 'verification-scenarios-search-v1'


class FeedbackInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    max_evaluations: StrictInt | None = Field(default=None, ge=2, le=128)
    seed: StrictInt | None = Field(default=None, ge=0, le=2**32-1)


def create(identity, options, *, database=None):
    from . import jobs
    from .optimization import SearchInput
    source = jobs.get(identity, database=database)
    verification = source['request'].get('verification')
    if not verification or source['status'] not in {'succeeded','failed'}:
        raise ConflictError('请选择已完成全部情景的补充复核任务；取消或中断的复核不能用于继续搜索。')
    plan = verification['plan']
    if not plan or [s['index'] for s in plan] != list(range(len(plan))):
        raise ConflictError('复核情景计划不完整。')
    if len(source['candidates']) != 1 or source['evaluated'] != len(plan) or len(source.get('samples', [])) != len(plan):
        raise ConflictError('复核情景记录不完整，请完成复核后再继续搜索。')
    samples = source['samples']
    if [s['index'] for s in samples] != list(range(len(plan))) or any(s['ordinal'] != 1 for s in samples):
        raise ConflictError('复核情景索引不完整。')
    minimum = 2*len(plan)
    budget = options.max_evaluations if options.max_evaluations is not None else max(minimum, source['request']['max_evaluations'])
    if minimum > 128 or budget < minimum:
        raise ConflictError(f'保留 {len(plan)} 个情景后，基准和一个候选至少需 {minimum} 次评估；单次上限 128。')
    document = {k:v for k,v in source['request'].items() if k not in {'verification','feedback'}}
    document.update(name=source['name'][:102]+' · 纳入情景寻优', max_evaluations=budget,
                    seed=options.seed if options.seed is not None else (verification['seed']+1) % 2**32)
    request = SearchInput.model_validate(document)
    # Freeze relative deviations; each candidate is tested at the same offsets.
    retained = []
    limits = {t.key:t.delta for t in request.tolerances}
    for item, sample in zip(plan, samples):
        offsets = dict(item['offsets'])
        if (set(offsets)-set(limits) or any(not math.isfinite(v) or abs(v)>limits[k] for k,v in offsets.items())
                or sample['parameters'] != item['parameters'] or sample['offsets'] != offsets):
            raise ConflictError('复核情景与已保存计划不一致，不能继续搜索。')
        retained.append({'index':item['index'], 'label':item['label'], 'offsets':offsets,
                         'phase':'retained', 'source_sample_index':sample['index'],
                         'source_calculation_id':sample.get('calculation_id'),
                         'source_failed':bool(sample.get('error') or sample.get('violations'))})
    if retained[0]['offsets']:
        raise ConflictError('复核缺少首个名义情景。')
    metadata = {'method':METHOD, 'source_job_id':identity,
                'source_candidate_ordinal':1,
                'source_calculation_id':source['candidates'][0]['calculation_id'],
                'training_job_ids':verification.get('training_job_ids', [verification['root_job_id']]),
                'resource_fingerprint':verification['resource_fingerprint'],
                'source_material':request.parameters['Paihao'],
                'retained_samples':len(retained), 'source_failed_samples':sum(s['source_failed'] for s in retained),
                'plan':retained}
    return jobs.create(request, database=database, feedback=metadata)


def scenarios(request, nominal, metadata):
    result = []
    for item in metadata['plan']:
        offsets = item['offsets']
        p = {**nominal, **{k:nominal[k]+v for k,v in offsets.items()}}
        if 'g' in offsets and request.tolerance_gap_mode == 'fixed_neck_bottom':
            p['hr0'] = nominal['hr0'] - offsets['g']
        result.append({**item, 'parameters':p})
    return result


def annotate(record, metadata):
    record.update(method=METHOD, feedback={k:v for k,v in metadata.items() if k!='plan'},
        sampling_plan={'method':METHOD, 'retained_samples':metadata['retained_samples']},
        note=f"纳入复核情景寻优：保留任务 {metadata['source_job_id']} 的全部 {metadata['retained_samples']} 个相对偏差情景，"
             f"其中 {metadata['source_failed_samples']} 个在源复核中超限或计算失败。所有新候选使用同一组偏差，沿用原变量范围、目标、约束和公差联动。"
             '这些情景已经参与候选选择，不能再作为独立新点复核；选定结果后应另做补充采样复核。有限采样不保证整个公差区间合格。')
