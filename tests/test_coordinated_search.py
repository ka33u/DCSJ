"""Synthetic public contracts for coordination; no motor performance benchmarks."""
import copy
import itertools

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from dcsj import optimization as opt, coordinated_search as coordinated, continuation, jobs
from dcsj.api import app
from dcsj.parameters import validate
from dcsj.storage import ConflictError


@pytest.fixture
def body(parameters):
    p = {**parameters, 'b01':2., 'hs0':.5, 'zs1':30., 'bs1':5., 'bs2':7., 'hs12':20.,
         'zc':2., 'bc02':1., 'hr0':.5, 'zs2':20., 'br1':6., 'br2':2., 'hr12':16.}
    return {'name':'Synthetic coordination', 'parameters':p, 'algorithm':coordinated.METHOD,
            'variables':[{'key':k,'values':[float(p[k]+d) for d in (-2,0,2)] if k=='ZZ'
                          else [float(p[k]*f) for f in (.95,1.,1.05)]} for k in coordinated.KEYS],
            'objective':'efficiency', 'secondary_objective':'material_cost',
            'constraints':[{'metric':'PF1','minimum':.78}], 'seed':19, 'max_evaluations':48}


def metrics(p):
    return {'ATA1':.9 + (p['L']-160)*.0001, 'PF1':.8, 'Sf':.6, 'Sn':.03,
            'CostFx':100+p['L']*.1+p['Djj'], 'Pcu':100., 'Pal':80., 'Pfe':70., 'Pfw1':20., 'Ps1':10.}


@pytest.mark.parametrize('change', ['missing_axis','extra_axis','range','outside','large','tolerance'])
def test_invalid_inputs_rejected_before_queue(body, change):
    if change == 'missing_axis': body['variables'].pop()
    elif change == 'extra_axis': body['variables'][0] = {'key':'g','values':[.5,.6,.7]}
    elif change == 'range': body['variables'][0] = {'key':'bs1','minimum':4.,'maximum':6.}
    elif change == 'outside': body['variables'][-1]['values'] = [.5,.6]
    elif change == 'large':
        for v in body['variables']:
            x=body['parameters'][v['key']]
            v['values']=[float(x+i) for i in range(5)]
    else: body['tolerances'] = [{'key':'L','delta':.1}]
    with pytest.raises(ValidationError, match='协调搜索'):
        opt.SearchInput.model_validate(body)


def test_first_poll_is_preserved_and_coordinated_inputs_are_measured(body):
    req = opt.SearchInput.model_validate(body)
    search = opt.Search(req)
    control = opt.Search(req.model_copy(update={'algorithm':'curvature_rescue_v1'}))
    proposals = []
    for i in range(48):
        p = search.candidate(); proposals.append(p)
        assert opt.in_region(req,p)
        assert {k:v for k,v in p.items() if k not in coordinated.KEYS} == {
            k:v for k,v in req.parameters.items() if k not in coordinated.KEYS}
        if i < 13:
            assert control.candidate() == p
            control.consider(p,metrics(p),True)
        values = metrics(p)
        # An input proxy cannot override an adverse real measurement.
        if i == 13: values['PF1'] = .5
        search.consider(p,values,not opt.assess(req,values))
    assert len({tuple(p[k] for k in coordinated.KEYS) for p in proposals}) == 48
    assert search.coordination_trace and len(search.coordination_trace) <= 48//8
    assert any(not r['feasible'] for r in search.records)
    for trace in search.coordination_trace:
        assert trace['after'] >= 13 and trace['proxy_is_not_a_measured_result']
        assert trace['candidate'] == {k:proposals[trace['after']][k] for k in coordinated.KEYS}
        assert trace['candidate']['br1'] == trace['center']['br1']
    assert search.best != proposals[13]


def test_observations_must_follow_unique_charged_proposals(body):
    search = opt.Search(opt.SearchInput.model_validate(body))
    with pytest.raises(ValueError,match='charged'): search.consider(body['parameters'],metrics(body['parameters']),True)
    p=search.candidate();search.consider(p,metrics(p),True)
    with pytest.raises(ValueError,match='charged'): search.consider(p,metrics(p),True)


def test_all_failed_attempts_exhaust_the_declared_grid_without_duplicates(body):
    req=opt.SearchInput.model_validate(body);search=opt.Search(req);points=[]
    while (p:=search.candidate()) is not None:
        points.append(tuple(p[k] for k in coordinated.KEYS))
        assert len(points)<=729
    expected=set(itertools.product(*(v.values for v in req.variables)))
    assert len(points)==len(set(points))==len(expected)==729 and set(points)==expected
    assert not search.records and not search.coordination_trace and search.best is None


def test_old_fingerprints_apply_only_to_old_algorithm_identifiers(body):
    req=opt.SearchInput.model_validate(body)
    for old in ('bcd5639c44213a527ef2ea7a67e0024f570bc1f8584ce17005b3b1c12034ad98',
                '67bf550870423be48a422574381de429e2c4c2125fd130a1ad0b4dcedd3ba2c4'):
        assert not opt.compatible_solver(req,old)
        assert opt.compatible_solver(req.model_copy(update={'algorithm':'curvature_rescue_v1'}),old)
    assert opt.compatible_solver(req,opt.SOLVER_SHA256)


@pytest.mark.parametrize('interrupted', [False,True])
def test_public_api_queue_failure_budget_and_repeated_extensions(body,snapshot,tmp_path,monkeypatch,interrupted):
    database=tmp_path/'public-coordination.sqlite';monkeypatch.setenv('DCSJ_PROJECTS_DB',str(database))
    monkeypatch.setattr(jobs,'engine_status',lambda:{'status':'recovered'})
    monkeypatch.setattr(jobs.Worker,'start',lambda worker:worker)
    resources={'kernel_sha256':'synthetic','wrapper_sha256':'synthetic','resource_package':None,
               'resource_version':None,'materials':{'SYNTHETIC':'synthetic'}}
    monkeypatch.setattr(continuation,'current_resources',lambda request:resources)
    calls=[];cancel_id=None
    def compute(p,**kwargs):
        calls.append(dict(p))
        if interrupted and len(calls)==17:jobs.cancel(cancel_id)
        if len(calls)%7==0:raise jobs.CalculationError('Synthetic engine failure')
        result=copy.deepcopy(snapshot);result['inputs']=validate(p);result['values'].update(metrics(p))
        result['provenance'].update(input_sha256=continuation.fingerprint(result['inputs']),resource_package=None,resource_version=None)
        return result
    monkeypatch.setattr(jobs,'compute',compute)
    worker=jobs.Worker(database);worker.touch()
    with TestClient(app,headers={'X-DCSJ-Request':'1'}) as client:
        assert client.post('/api/v1/auth/login',json={'username':'synthetic-author','password':'1234'}).status_code==200
        config=client.get('/api/v1/optimization/config').json()
        assert any(a['id']==coordinated.METHOD for a in config['algorithms'])
        schema=client.get('/openapi.json').json()
        assert coordinated.METHOD in schema['components']['schemas']['SearchInput']['properties']['algorithm']['enum']
        invalid={**body,'variables':body['variables'][:-1]}
        assert client.post('/api/v1/optimization/jobs',json=invalid).status_code==422 and not calls
        response=client.post('/api/v1/optimization/jobs',json=body)
        assert response.status_code==202;cancel_id=response.json()['id']
        assert not calls;assert worker.claim()==cancel_id;worker.run_job(cancel_id)
        job=client.get('/api/v1/optimization/jobs/'+cancel_id).json()
        assert job['evaluated']==(17 if interrupted else 48)
        control=opt.Search(opt.SearchInput.model_validate(body));consumed=0
        for extra in (24,32,None):
            for row in job['candidates'][consumed:]:
                assert control.candidate()==row['parameters']
                if not row['error']:
                    control.consider(row['parameters'],metrics(row['parameters']),row['feasible'])
            consumed=len(job['candidates'])
            assert any(row['error'] for row in job['candidates'])
            for row in job['candidates']:
                if row['calculation_id']:
                    saved=client.get('/api/v1/calculations/'+row['calculation_id']).json()
                    assert saved['optimization']['algorithm']==coordinated.METHOD
            if extra is None:break
            prior=copy.deepcopy(job);before=len(calls)
            response=client.post('/api/v1/optimization/jobs/'+job['id']+'/extend',json={'additional_evaluations':extra})
            assert response.status_code==202,response.text
            child=response.json();assert len(calls)==before
            assert [r['calculation_id'] for r in child['candidates']]==[r['calculation_id'] for r in prior['candidates']]
            control.request=control.request.model_copy(update={'max_evaluations':prior['evaluated']+extra})
            assert worker.claim()==child['id'];worker.run_job(child['id'])
            job=client.get('/api/v1/optimization/jobs/'+child['id']).json()
            assert len(calls)-before==extra and job['evaluated']==prior['evaluated']+extra
            assert job['request']['algorithm']==coordinated.METHOD
            original=client.get('/api/v1/optimization/jobs/'+prior['id']).json()
            assert original['candidates']==prior['candidates']
        history=continuation.read_history(job['id'],database=database)
        state,summary,*_=continuation.state(history)
        assert state.coordination_trace==control.coordination_trace
        assert summary['inherited_attempts']==job['evaluated']
        damaged=copy.deepcopy(history)
        damaged['rows'][0]['parameters']='{}'
        with pytest.raises(ConflictError):continuation.state(damaged)
