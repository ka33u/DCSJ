"""Energy conservation, raw-sample scope, missing objectives and durable duty profiles."""
from copy import deepcopy
import math

import pytest
from pydantic import ValidationError
from dcsj import benchmark, calculations, jobs, load_cycle, optimization, report_summary, reports


def load_result(snapshot, extra_loss=0):
    result = deepcopy(snapshot)
    result['values'].update(ATA1=.9, PF1=.8, Sf=.6, Sn=.03, Pcu=1, Pal=1, Pfe=1, Pfw1=1, Ps1=1)
    xs = load_cycle.SAMPLE_LOADS
    rating = result['inputs']['kW']
    powers = [rating*x + .5 + 1.5*x*x + extra_loss for x in xs]
    ys = [powers, [.03*x for x in xs], [20*x for x in xs], [.8]*9,
          [rating*x/power for x,power in zip(xs,powers)]]
    result['curves'] = [{'id':'load_characteristic','display':False,'series':[
        {'points':[{'x':x,'y':y} for x,y in zip(xs,values)]} for values in ys],
        'plot':{'series':[{'points':[{'x':150,'y':.01}]}]}}]
    return result


def request(parameters, **kwargs):
    return optimization.SearchInput(parameters=parameters, variables=[dict(key='L',minimum=150,maximum=170,step=5)],
        objective='cycle_energy',load_cycle=[dict(load_fraction=.5,hours=2),dict(load_fraction=1,hours=1)],**kwargs)


def test_energy_is_sum_of_powers_not_average_efficiencies_and_ignores_plot(parameters,snapshot):
    r=load_result(snapshot); original=deepcopy(r)
    cycle=load_cycle.evaluate(r,request(parameters).load_cycle)
    assert cycle['input_energy_kwh']==27.75 and cycle['output_energy_kwh']==24
    assert cycle['efficiency']==pytest.approx(24/27.75)
    assert cycle['efficiency']!=pytest.approx((2*6/6.875+12/14)/3)
    assert cycle['total_hours']==3 and all(s['basis']=='sample' for s in cycle['stages'])
    assert r==original
    halfway=load_cycle.evaluate(r,[dict(load_fraction=.55,hours=4)])
    assert halfway['stages'][0]['input_power_kw']==pytest.approx((6.875+8.24)/2)
    assert halfway['stages'][0]['basis']=='linear'
    assert [w['weight'] for w in halfway['stages'][0]['weights']]==pytest.approx([.5,.5])
    endpoints=load_cycle.evaluate(r,[dict(load_fraction=.3,hours=1),dict(load_fraction=1.2,hours=1)])
    assert endpoints['status']=='available'


@pytest.mark.parametrize('damage',['missing','short','order','axis','nan','boolean','negative','slip','pf','balance','duplicate'])
def test_invalid_load_samples_cannot_produce_a_period_value(snapshot,damage):
    r=load_result(snapshot);series=r['curves'][0]['series']
    if damage=='missing':r['curves']=[]
    elif damage=='short':series[4]['points'].pop()
    elif damage=='order':series[0]['points'].reverse()
    elif damage=='axis':series[2]['points'][4]['x']=.8
    elif damage=='nan':series[0]['points'][4]['y']=math.inf
    elif damage=='boolean':series[0]['points'][4]['y']=True
    elif damage=='negative':series[0]['points'][4]['y']=-1
    elif damage=='slip':series[1]['points'][4]['y']=1
    elif damage=='pf':series[3]['points'][4]['y']=1.1
    elif damage=='balance':series[4]['points'][4]['y']=.99
    else:r['curves'].append(deepcopy(r['curves'][0]))
    c=load_cycle.evaluate(r,[dict(load_fraction=.5,hours=1)])
    assert c['status']=='unavailable' and c['input_energy_kwh'] is None and c['reason']


@pytest.mark.parametrize('stages',[[],[dict(load_fraction=.2,hours=1)],[dict(load_fraction=1.21,hours=1)],
    [dict(load_fraction=.5,hours=0)],[dict(load_fraction=.5,hours=math.inf)],
    [dict(load_fraction=True,hours=1)],[dict(load_fraction=.5,hours=1)]*33])
def test_profile_validation(parameters,stages):
    body=request(parameters).model_dump();body['load_cycle']=stages
    with pytest.raises(ValidationError):optimization.SearchInput.model_validate(body)


def test_cycle_requirement_equivalent_objectives_and_caller_unchanged(parameters):
    original=deepcopy(parameters);r=request(parameters)
    assert parameters==original and r.parameters['_load_curve']==1
    assert optimization.SearchInput.model_validate(r.model_dump())==r
    body=r.model_dump();body.pop('load_cycle')
    with pytest.raises(ValidationError,match='负载周期'):optimization.SearchInput.model_validate(body)
    body['objective']='efficiency';body['parameters']['_load_curve']=0
    old=optimization.SearchInput.model_validate(body);assert old.parameters['_load_curve']==0
    body['constraints']=[dict(metric='cycle_efficiency',minimum=.8)]
    with pytest.raises(ValidationError,match='负载周期'):optimization.SearchInput.model_validate(body)
    with pytest.raises(ValidationError,match='等价'):request(parameters,secondary_objective='cycle_efficiency')


@pytest.mark.parametrize('algorithm',['stratified_v1','hybrid_de_v1','adaptive_poll_v1'])
def test_missing_objective_does_not_crash_any_search(parameters,snapshot,algorithm):
    req=request(parameters,algorithm=algorithm);r=load_result(snapshot);r['curves']=[]
    values=optimization.metrics(r,req)
    assert values['cycle_energy'] is None and optimization.assess(req,values)
    search=optimization.Search(req)
    assert search.consider(search.candidate(),values,False)==(None,False)


def test_worker_excludes_incomplete_better_candidate_and_preserves_snapshot(tmp_path,monkeypatch,parameters,snapshot):
    path=tmp_path/'jobs.sqlite';monkeypatch.setenv('DCSJ_PROJECTS_DB',str(path))
    monkeypatch.setattr(jobs,'engine_status',lambda:{'status':'recovered'})
    def compute(p,**kwargs):
        assert p['_load_curve']==1
        r=load_result(snapshot,(170-p['L'])*.01);r['inputs']=dict(p)
        if p['L']==165:
            r['values']['ATA1']=.99;r['curves'][0]['series'][0]['points'].pop()
        return r
    monkeypatch.setattr(jobs,'compute',compute)
    req=request(parameters,algorithm='adaptive_poll_v1',max_evaluations=8)
    item=jobs.create(req);worker=jobs.Worker(path);worker.touch();worker.run_job(worker.claim())
    result=jobs.get(item['id']);assert result['status']=='succeeded' and result['evaluated']==5
    invalid=next(c for c in result['candidates'] if c['parameters']['L']==165)
    assert invalid['objective'] is None and not invalid['feasible'] and invalid['load_cycle']['reason']
    best=calculations.get(result['best_id']);assert best['inputs']['L']==170
    assert best['optimization']['load_cycle']['input_energy_kwh']==27.75
    assert best['optimization']['load_cycle']['profile']==[s.model_dump() for s in req.load_cycle]
    assert '27.75' in report_summary.cycle_summary(best)['text']
    html=reports.render(best);assert '负载周期能耗' in html and '27.75' in html
    monkeypatch.setattr(jobs,'compute',lambda *args,**kwargs:pytest.fail('No historical recalculation'))
    assert jobs.get(item['id'])==result


def test_benchmark_cache_reuses_kernel_but_not_another_duty_profile(parameters,snapshot):
    req=request(parameters).model_dump()
    other={**req,'load_cycle':[dict(load_fraction=1,hours=10)]}
    calls=[]
    def compute(p):
        calls.append(p);r=load_result(snapshot);r['inputs']=dict(p);return r
    suite={'cases':[{'id':'partial','request':req},{'id':'full','request':other}],
           'budgets':[2],'seeds':[42],'algorithms':['stratified_v1']}
    first,second=list(benchmark.compare(suite,compute))
    assert len(calls)==2 and second['cached_evaluations']==2
    assert first['candidates'][0]['objectives']['cycle_energy']==27.75
    assert second['candidates'][0]['objectives']['cycle_energy']==140
    assert second['candidates'][0]['load_cycle']['total_hours']==10
