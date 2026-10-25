"""Curve-derived optimizer constraints use actual complete samples, not estimates."""
from copy import deepcopy
import math

import pytest
from dcsj import calculations, jobs, optimization, report_summary, semantics
from dcsj.curves import sampled_torque_peak


def sweep(peak=3.2):
    xs = [1-i/50 for i in range(50)] + [.01]
    return [{'id':'slip', 'series':[{'points':[]},
             {'points':[{'x':x,'y':peak-(x-.3)**2} for x in xs]}],
             'plot':{'series':[{'points':[{'x':.3,'y':99}]}]}}]


def test_sampled_peak_uses_raw_axis_and_ignores_display_overshoot():
    curves=sweep();original=deepcopy(curves);peak=sampled_torque_peak(curves)
    assert peak['status']=='available'
    assert peak['value']==pytest.approx(3.2) and peak['slip']==pytest.approx(.3)
    assert peak['domain']==[.01,1] and peak['sample_count']==51
    assert peak['adjacent_slips']==pytest.approx([.32,.28])
    assert curves==original
    # The last real point is at s=.01; never substitute the drawn origin.
    curves[0]['series'][1]['points'][-1]['y']=4
    assert sampled_torque_peak(curves)['slip']==.01


@pytest.mark.parametrize('damage',['missing','short','duplicate','nonfinite','negative','boolean','false_origin'])
def test_unavailable_sweep_is_not_a_valid_peak(damage):
    curves=sweep();points=curves[0]['series'][1]['points']
    if damage=='missing':curves=[]
    elif damage=='short':points.pop()
    elif damage=='duplicate':points[20]['x']=points[19]['x']
    elif damage=='nonfinite':points[20]['y']=math.inf
    elif damage=='negative':points[20]['y']=-.1
    elif damage=='boolean':points[20]['y']=True
    else:points[-1]['x']=0
    peak=sampled_torque_peak(curves)
    assert peak['status']=='unavailable' and peak['value'] is None and peak['reason']


@pytest.mark.parametrize('algorithm',['stratified_v1','hybrid_de_v1','adaptive_poll_v1'])
def test_constraint_enables_curve_without_changing_caller_or_legacy_requests(parameters,algorithm):
    original=deepcopy(parameters)
    body={'parameters':parameters,'algorithm':algorithm,'variables':[{'key':'L','minimum':150,'maximum':170}]}
    default=optimization.SearchInput.model_validate(body)
    request=optimization.SearchInput.model_validate({**body,'constraints':[{'metric':'TM_curve','minimum':3}]})
    assert parameters==original and default.parameters['_curve']==0
    assert request.parameters['_curve']==1 and request.parameters['_load_curve']==0
    assert request.parameters['TM']==default.parameters['TM']
    assert optimization.SearchInput.model_validate(request.model_dump())==request


def test_legacy_estimate_cannot_replace_missing_curve_constraint(parameters,snapshot):
    request=optimization.SearchInput.model_validate({'parameters':parameters,'variables':[{'key':'L','minimum':150,'maximum':170}],
        'constraints':[{'metric':'TM_curve','minimum':3}]})
    snapshot['values'].update(TM1=99,Sf=.6,Sn=.03,Pcu=1,Pal=1,Pfe=1,Pfw1=1,Ps1=1)
    values=optimization.metrics(snapshot)
    assert values['TM1']==99 and values['TM_curve'] is None
    assert optimization.assess(request,values)==['TM_curve 无有效结果']
    assert optimization.violation_score(request,values)>0


def test_incomplete_better_candidate_is_excluded_and_results_reopen_without_recompute(tmp_path,monkeypatch,parameters,snapshot):
    path=tmp_path/'curve-jobs.sqlite';monkeypatch.setenv('DCSJ_PROJECTS_DB',str(path))
    monkeypatch.setattr(jobs,'engine_status',lambda:{'status':'recovered'})
    def compute(p,**kwargs):
        assert p['_curve']==1
        r=deepcopy(snapshot);r['inputs']=dict(p)
        r['values'].update(ATA1=.95-abs(p['L']-165)*.001,TM1=2.7,Sf=.6,Sn=.03,Pcu=1,Pal=1,Pfe=1,Pfw1=1,Ps1=1)
        r['curves']=sweep(2.8+(p['L']-160)*.1)
        if p['L']==165:r['curves'][0]['series'][1]['points'].pop()
        r['curve_metrics']={'torque_peak':sampled_torque_peak(r['curves'])}
        return r
    monkeypatch.setattr(jobs,'compute',compute)
    request=optimization.SearchInput.model_validate({'parameters':parameters,'algorithm':'adaptive_poll_v1',
        'variables':[{'key':'L','minimum':150,'maximum':170,'step':5}],
        'constraints':[{'metric':'TM_curve','minimum':3.1},{'metric':'TM1','maximum':2.9}], 'max_evaluations':8})
    item=jobs.create(request);worker=jobs.Worker(path);worker.touch();worker.run_job(worker.claim())
    result=jobs.get(item['id']);assert result['status']=='succeeded' and result['evaluated']==5
    invalid=next(c for c in result['candidates'] if c['parameters']['L']==165)
    assert not invalid['feasible'] and invalid['constraint_values']['TM_curve'] is None
    assert invalid['torque_peak']['reason']=='转矩曲线采样不完整：50/51'
    best=calculations.get(result['best_id']);assert best['inputs']['L']==170
    assert best['optimization']['constraint_values']['TM_curve']==pytest.approx(3.8)
    assert best['values']['TM1']==2.7 and 'TM_curve' not in best['values']
    assert best['optimization']['constraints'][0]['minimum']==3.1
    row=next(r for r in report_summary.targets(best) if r[0]=='转矩曲线采样峰值')
    assert row[1]=='≥3.1' and row[2]=='3.8' and row[3]=='s=0.3'
    monkeypatch.setattr(jobs,'compute',lambda *args,**kwargs:pytest.fail('Saved results must not recalculate'))
    assert jobs.get(item['id'])==result


def test_snapshot_contract_keeps_original_decision_values_and_adds_separate_curve_observation(parameters):
    values={'ATA1':.9,'PF1':.8,'IST1':6,'TST1':2,'TM1':2.8}
    curves=sweep();curves[0]['series'][0]['points']=[{'x':1,'y':6}]
    meta=semantics.result_metadata(parameters,values,{'kind':'mdb_custom'},curves)
    assert meta['curve_metrics']['torque_peak']['value']==pytest.approx(3.2)
    assert meta['contract']['decision_values']==['IST1','TST1','TM1']
    assert values['TM1']==2.8 and curves[0]['authoritative'] is False
