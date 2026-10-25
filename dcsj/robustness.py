"""Finite tolerance sampling with optional stratified interior points."""
import hashlib
import itertools
import math
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictFloat

NOTE = ('按名义尺寸和公差角点逐一计算，以采样中最不利的目标值寻优，全部采样均须满足约束。'
        '不推定公差分布或合格概率，也不保证角点之间没有更差值；气隙偏差表示均匀气隙变化，不模拟偏心。')
INTERIOR_COUNTS = (0, 4, 8, 16, 32)


class Tolerance(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    key: Literal['L', 'g', 'Djj', 'Dmm']
    delta: StrictFloat = Field(gt=0, le=1000000)


def sample_count(request):
    return 1 + 2**len(request.tolerances) + request.tolerance_interior_samples if request.tolerances else 1


def interior_offsets(request):
    """Centered LHS: one midpoint per stratum in each dimension.

    Hash-ordered permutations give each dimension its own reproducible stream,
    independent of the search RNG, candidate order and tolerance field order.
    Even sample counts exclude the nominal coordinate and all corner coordinates.
    These are coverage points, not draws from a manufacturing distribution.
    """
    count = request.tolerance_interior_samples
    if not count:
        return []
    columns = {}
    for tolerance in sorted(request.tolerances, key=lambda t: t.key):
        prefix = f'dcsj-tolerance-lhs-v1:{request.seed}:{count}:{tolerance.key}:'
        bins = sorted(range(count), key=lambda i: hashlib.sha256((prefix+str(i)).encode()).digest())
        columns[tolerance.key] = [((i+.5)*2/count-1)*tolerance.delta for i in bins]
    return [{key: values[i] for key, values in columns.items()} for i in range(count)]


def scenarios(request, nominal):
    result = [{'index': 0, 'label': '名义尺寸', 'offsets': {}, 'parameters': dict(nominal)}]
    if not request.tolerances:
        return result
    def append(offsets, label):
        p = {**nominal, **{key: nominal[key] + offset for key, offset in offsets.items()}}
        if 'g' in offsets and request.tolerance_gap_mode == 'fixed_neck_bottom':
            p['hr0'] = nominal['hr0'] - offsets['g']
        result.append({'index': len(result), 'label': label, 'offsets': offsets, 'parameters': p})

    for signs in itertools.product((-1, 1), repeat=len(request.tolerances)):
        offsets = {t.key: sign*t.delta for t, sign in zip(request.tolerances, signs)}
        append(offsets, '；'.join(f'{k}{v:+g}' for k,v in offsets.items()))
    for i, offsets in enumerate(interior_offsets(request), 1):
        append(offsets, f'内部采样 {i}')
        result[-1]['kind'] = 'interior'
    return result


def validate_scenario(parameters):
    from .parameters import validate, InputError
    p = validate(parameters, strict=True)
    if not 0 <= p['hr0'] < (p['Di1'] - 2*p['g']) / 2:
        raise InputError([{'field':'hr0','message':'公差情景的槽颈高须非负且小于转子半径'}])
    return p


def measured(request, scenario, result):
    from . import load_cycle
    from .optimization import metrics, assess
    cycle = load_cycle.evaluate(result, request.load_cycle)
    values = metrics(result, cycle=cycle)
    error = None
    for metric, key, label in [('TM_refined', 'refined_torque_peak', '加密转矩'),
                               ('TM_continuous', 'continuous_torque_peak', '连续饱和插值转矩')]:
        if any(c.metric == metric for c in request.constraints) and values[metric] is None:
            refined = result.get('curve_metrics', {}).get(key) or {}
            message = label + '数值校核失败：' + refined.get('reason', '未取得有效采样峰值')
            error = (error + '；' + message) if error else message
    return {**scenario, 'values':values, 'load_cycle':cycle, 'violations':assess(request,values),
            'error':error, 'calculation_id':None}


def summarize(request, nominal, samples, *, complete=True):
    from .optimization import OBJECTIVES, objective_keys, in_region, violation_score
    expected = sample_count(request)
    complete = complete and len(samples) == expected
    valid = complete and all(not s.get('error') for s in samples)
    violations = [f"{s['label']}：{message}" for s in samples for message in s.get('violations', [])]
    if not complete: violations.append('公差情景尚未完整评估，不能作为可行候选。')
    if not in_region(request, nominal): violations.append('名义方案不在指定搜索区域内，基准仅作为参考。')
    keys = objective_keys(request)
    objectives, worst_samples = {}, {}
    for key in keys:
        metric, direction, _ = OBJECTIVES[key]
        values = [s.get('values', {}).get(metric) for s in samples]
        if valid and all(isinstance(v,(int,float)) and math.isfinite(v) for v in values):
            index = (min if direction == 'max' else max)(range(len(values)), key=values.__getitem__)
            objectives[key], worst_samples[key] = values[index], samples[index]['index']
        else:
            objectives[key], worst_samples[key] = None, None
    if any(value is None for value in objectives.values()):
        violations.append('缺少完整有效的公差目标值。')
    metrics = {c.metric for c in request.constraints} | {OBJECTIVES[k][0] for k in keys} | {'Sf','Sn'}
    ranges = {}
    for metric in sorted(metrics):
        values = [s.get('values', {}).get(metric) for s in samples]
        finite = [v for v in values if isinstance(v,(int,float)) and math.isfinite(v)]
        ranges[metric] = {'minimum':min(finite) if finite else None, 'maximum':max(finite) if finite else None,
                          # A different metric may fail while this metric has every sample.
                          # Group validity and ranking remain governed by all required results.
                          'complete':complete and len(finite)==expected}
    values = dict(samples[0].get('values', {})) if samples else {}
    values.update({OBJECTIVES[k][0]:v for k,v in objectives.items()})
    score = max((violation_score(request,s.get('values',{})) for s in samples), default=1e6) if valid else 1e6
    record = {'method':'center-corners-v1', 'note':NOTE, 'tolerances':[t.model_dump() for t in request.tolerances],
              'objective_specs':{k:dict(zip(('metric','direction','label'),OBJECTIVES[k])) for k in keys},
              'gap_mode':request.tolerance_gap_mode, 'expected_samples':expected, 'evaluated_samples':len(samples),
              'status':'incomplete' if not complete else 'complete' if valid else 'failed',
              'objectives':objectives, 'worst_samples':worst_samples, 'metric_ranges':ranges,
              'samples':samples, 'feasible':not violations, 'violations':violations}
    if request.tolerance_interior_samples:
        record.update(method='center-corners-lhs-v1',
                      note=f'名义尺寸与全部角点之外，增加 {request.tolerance_interior_samples} 个区间内部分层点（种子 {request.seed}）。'
                           '每个尺寸的允许区间等分，每段取一个中点，各尺寸的组合按种子打乱；所有候选共用同一组相对偏差。'
                           '以全部采样中最不利的目标值寻优，全部采样均须满足约束。'
                           '有限采样不保证整个区间合格，不推定公差分布或合格概率；气隙偏差不模拟偏心。',
                      sampling_plan={'interior_method':'centered-lhs-sha256-v1', 'seed':request.seed,
                                     'interior_samples':request.tolerance_interior_samples})
    return record, values, score
