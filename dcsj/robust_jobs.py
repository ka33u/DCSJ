"""Checkpoint every tolerance sample; publish a candidate only after assessment."""
from . import robustness, manufacturing, calculations, torque_refinement
from .optimization import Search, SOLVER_SHA256
from .parameters import InputError
from .engine import CalculationError
from .execution import BusyError
from .storage import connect, dumps, now
from .curves import sampled_torque_peak


def run(worker, identity, request, compute):
    group_size = robustness.sample_count(request)
    # Search scheduling uses the number of complete nominal candidates we can afford.
    search = Search(request.model_copy(update={'max_evaluations':request.max_evaluations // group_size}))
    evaluated, best_id, completed, fingerprint = 0, None, 0, None

    def status():
        with connect(worker.database) as db:
            row = db.execute('SELECT owner,status,cancel_requested FROM optimization_jobs WHERE id=?',(identity,)).fetchone()
        if row['owner'] != worker.owner or row['status'] not in {'running','cancelling'}: return 'lost'
        if worker.stop.is_set(): return 'interrupted'
        if row['cancel_requested']: return 'cancelled'
        return None

    def finish(reason, message=None):
        worker.finish(identity, reason, message or ('服务已停止，公差样本已保存。' if reason=='interrupted' else '已取消，已完成的公差样本保留；未完成组不参与最优选择。'))

    for ordinal in range(1, request.max_evaluations // group_size + 1):
        reason = status()
        if reason:
            if reason != 'lost': finish(reason)
            return
        nominal = search.candidate()
        if nominal is None: break
        with connect(worker.database) as db:
            db.execute('INSERT INTO optimization_candidates VALUES (?,?,?,?,?,?,?,?)',
                       (identity,ordinal,dumps(nominal),None,None,0,dumps(['公差评估未完成。']),None))
        samples, nominal_result, reason, drift = [], None, None, False
        for scenario in robustness.scenarios(request, nominal):
            reason = status()
            if reason: break
            try:
                p = robustness.validate_scenario(scenario['parameters'])
                extra = torque_refinement.options(request)
                result = compute(p, wait=True, stop_event=worker.stop, **extra)
                current = tuple(result['provenance'].get(k) for k in ('kernel_sha256','wrapper_sha256','resource_package'))
                if fingerprint is not None and current != fingerprint:
                    drift = True
                    raise CalculationError('计算资源发生变化，停止公差评估，避免混合不同资源。')
                fingerprint = current
                sample = robustness.measured(request, scenario, result)
                if status() == 'lost': return
                result['provenance'].update(optimization_job_id=identity, optimization_candidate=ordinal,
                                            optimization_sample_index=scenario['index'])
                saved = calculations.save(result, f"{request.name} / 候选{ordinal} / {scenario['label']}", database=worker.database)
                sample['calculation_id'] = saved['calculation_id']
                if scenario['index'] == 0: nominal_result = result
            except (InputError, CalculationError, BusyError) as error:
                sample = {**scenario, 'values':{}, 'violations':['输入无效或数值核未完成此情景。'],
                          'error':str(error)[:2000], 'calculation_id':None}
            samples.append(sample)
            with connect(worker.database) as db:
                row = db.execute('SELECT owner,status FROM optimization_jobs WHERE id=?',(identity,)).fetchone()
                if row['owner'] != worker.owner or row['status'] not in {'running','cancelling'}: return
                evaluated += 1
                db.execute('INSERT INTO optimization_samples VALUES (?,?,?,?)',
                           (identity,ordinal,scenario['index'],dumps(sample)))
                db.execute('UPDATE optimization_jobs SET evaluated=?,updated_at=? WHERE id=?', (evaluated,now(),identity))
            if drift:
                reason = 'interrupted'; break
        if reason == 'lost': return
        robust, values, violation = robustness.summarize(request, nominal, samples, complete=not drift)
        objective, improved = search.consider(nominal, values, robust['feasible'], violation=violation)
        baseline_sample = samples[0] if samples else {}
        evaluation = {'algorithm':request.algorithm, 'solver_sha256':SOLVER_SHA256,
                      'objectives':robust['objectives'], 'violation_score':violation,
                      'constraints':[c.model_dump() for c in request.constraints],
                      'constraint_values':{c.metric:baseline_sample.get('values',{}).get(c.metric) for c in request.constraints},
                      'load_cycle':baseline_sample.get('load_cycle'), 'robustness':robust,
                      'manufacturing':manufacturing.audit(request,nominal),
                      'refined_torque_peak':torque_refinement.compact(nominal_result or {}),
                      'continuous_torque_peak':torque_refinement.compact(nominal_result or {}, 'continuous_torque_peak'),
                      'torque_peak':sampled_torque_peak((nominal_result or {}).get('curves',[]))}
        calculation_id = None
        if nominal_result is not None:
            result = {**nominal_result, 'optimization':evaluation,
                      'provenance':{**nominal_result['provenance'], 'optimization_sample_index':None}}
            saved = calculations.save(result,f'{request.name} / 候选{ordinal} / 公差汇总',database=worker.database)
            calculation_id = saved['calculation_id']
        if robust['status'] == 'complete': completed += 1
        if improved: best_id = calculation_id
        with connect(worker.database) as db:
            row = db.execute('SELECT owner,status FROM optimization_jobs WHERE id=?',(identity,)).fetchone()
            if row['owner'] != worker.owner or row['status'] not in {'running','cancelling'}: return
            db.execute('INSERT INTO optimization_assessments VALUES (?,?,?)',(identity,ordinal,dumps(evaluation)))
            db.execute('UPDATE optimization_candidates SET calculation_id=?,objective=?,feasible=?,violations=?,error=? WHERE job_id=? AND ordinal=?',
                       (calculation_id,objective,int(robust['feasible']),dumps(robust['violations']),
                        '公差评估不完整或存在计算失败。' if robust['status'] != 'complete' else None,identity,ordinal))
            db.execute('UPDATE optimization_jobs SET best_id=?,updated_at=? WHERE id=?',(best_id,now(),identity))
        if reason:
            finish(reason, '计算资源发生变化，已停止；保留先前样本，未完成组不参与最优选择。' if drift else None)
            return
    reason = status()
    if reason:
        if reason != 'lost': finish(reason)
    else:
        worker.finish(identity,'succeeded' if completed else 'failed',
                      f'公差搜索结束：已评估 {evaluated} 次；每个名义方案需 {group_size} 次。剩余预算不足完整一组或已无新方案。'
                      + ('最优方案仅限全部采样满足约束的候选。' if best_id else '没有找到全部采样满足约束的候选。'))
