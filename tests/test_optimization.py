import copy
import time

import pytest
from pydantic import ValidationError
from dcsj import jobs, optimization, calculations
from dcsj.storage import connect
from dcsj import manufacturing


@pytest.fixture
def request_body(parameters):
    return {'name': 'Synthetic search', 'parameters': parameters,
            'variables': [{'key': 'L', 'minimum': 150, 'maximum': 170, 'step': 5}],
            'objective': 'efficiency', 'max_evaluations': 8, 'seed': 19, 'algorithm':'hybrid_de_v1'}


@pytest.fixture
def worker(tmp_path, monkeypatch, snapshot):
    path = tmp_path / 'jobs.sqlite'
    monkeypatch.setenv('DCSJ_PROJECTS_DB', str(path))
    monkeypatch.setattr(jobs, 'engine_status', lambda: {'status': 'recovered'})
    def simulated(parameters, **kwargs):
        # Explicit deterministic test double, never used by production.
        result = copy.deepcopy(snapshot)
        result['inputs'] = dict(parameters)
        result['values'].update(ATA1=.95 - abs(parameters['L'] - 165) * .001,
                                Sf=.6, Sn=.03, Pcu=100, Pal=80, Pfe=70, Pfw1=20, Ps1=10)
        return result
    monkeypatch.setattr(jobs, 'compute', simulated)
    item = jobs.Worker(path); item.touch()
    return item


def test_budget_bounds_feasibility_and_snapshot_link(worker, request_body):
    request_body['constraints'] = [{'metric': 'ATA1', 'minimum': .945}]
    request = optimization.SearchInput.model_validate(request_body)
    queued = jobs.create(request)
    assert worker.claim() == queued['id']
    worker.run_job(queued['id'])
    result = jobs.get(queued['id'])
    assert result['status'] == 'succeeded'
    assert result['evaluated'] <= 6  # Discrete region contains five unique candidates.
    candidates = result['candidates']
    assert len({c['parameters']['L'] for c in candidates}) == len(candidates)
    assert all(c['parameters']['L'] in {150, 155, 160, 165, 170} for c in candidates)
    best = calculations.get(result['best_id'])
    assert best['inputs']['L'] == 165
    assert any(not c['feasible'] for c in candidates)
    assert best['values']['ATA1'] == .95


@pytest.mark.parametrize('algorithm', ['stratified_v1','hybrid_de_v1','adaptive_poll_v1'])
def test_explicit_sizes_exhaust_real_nominal_grid_preserving_legacy_baseline(request_body, algorithm):
    p = {**request_body['parameters'], 'Djj':manufacturing.single(.8), 'hr0':.5}
    req = optimization.SearchInput.model_validate({**request_body,'parameters':p,'algorithm':algorithm,
        'variables':[{'key':'Djj','values':[1.06,.8,.71]}, {'key':'g','values':[.5,.6,.8]}],
        'rotor_gap_mode':'fixed_neck_bottom','max_evaluations':16})
    assert req.model_dump()==optimization.SearchInput.model_validate(req.model_dump()).model_dump()
    runs=[]
    for _ in range(2):
        search=optimization.Search(req);seen=[]
        while (candidate:=search.candidate()) is not None:
            seen.append(candidate)
            assert optimization.in_region(req,candidate)
            assert manufacturing.audit(req,candidate)['passed']
            assert candidate['Di1']-2*(candidate['g']+candidate['hr0'])==pytest.approx(177.8)
            assert {k:v for k,v in candidate.items() if k not in {'Djj','g','hr0'}}=={
                k:v for k,v in req.parameters.items() if k not in {'Djj','g','hr0'}}
            search.consider(candidate,{'ATA1':.9,'PF1':.8,'Sf':.6,'Sn':.03},True)
        assert seen[0]==req.parameters
        assert len(seen)==9
        assert all(c['Djj'] in {.71,.8,1.06} for c in seen[1:])
        assert len({(manufacturing.nominal(req.variables[0],c['Djj']),c['g']) for c in seen})==9
        runs.append(seen)
    assert runs[0]==runs[1]


@pytest.mark.parametrize('algorithm', ['stratified_v1','hybrid_de_v1','adaptive_poll_v1'])
def test_specifications_with_continuous_dimension_and_outside_reference(request_body,algorithm):
    req=optimization.SearchInput.model_validate({**request_body,'algorithm':algorithm,'max_evaluations':32,
        'variables':[{'key':'L','minimum':140,'maximum':190}, {'key':'Djj','values':[.71,.8,1.06]}]})
    search=optimization.Search(req);reference=search.candidate()
    assert not optimization.in_region(req,reference)
    assert not search.consider(reference,{'ATA1':.999},True)[1]
    for _ in range(31):
        p=search.candidate();assert p is not None
        assert optimization.in_region(req,p) and p['Djj'] in {.71,.8,1.06}
        search.consider(p,{'ATA1':.9-abs(p['L']-165)*.001,'PF1':.8,'Sf':.6,'Sn':.03},True)
    assert search.best is not None


@pytest.mark.parametrize('variable', [
    {'key':'Djj','values':[]}, {'key':'Djj','values':[.8,.8]}, {'key':'Djj','values':[True]},
    {'key':'Djj','values':['.8']}, {'key':'Djj','values':[float('nan')]},
    {'key':'Djj','values':[float('inf')]}, {'key':'Djj','values':[.8,.800000001]},
    {'key':'Djj','values':[0]}, {'key':'MM','values':[-1]}, {'key':'ZZ','values':[15.5]},
    {'key':'L','values':[160], 'step':1}, {'key':'L','values':[160], 'minimum':150,'maximum':170},
    {'key':'L','values':list(range(1,66))}, {'key':'L','values':[1000001]},
    {'key':'kW','values':[1,2]}, {'key':'L','values':[160], 'choices':['A']},
])
def test_invalid_specifications_rejected_before_calculation(request_body,variable):
    with pytest.raises(ValidationError):
        optimization.SearchInput.model_validate({**request_body,'variables':[variable]})


def test_gap_linkage_requires_valid_range_and_does_not_modify_input(request_body):
    body={**request_body,'parameters':{**request_body['parameters'],'hr0':.2},
          'rotor_gap_mode':'fixed_neck_bottom'}
    for variables in ([{'key':'L','values':[160]}], [{'key':'g','values':[.6,.81]}]):
        with pytest.raises(ValidationError):optimization.SearchInput.model_validate({**body,'variables':variables})
    req=optimization.SearchInput.model_validate({**body,'variables':[{'key':'g','values':[.4,.6,.8]}]})
    p=manufacturing.linked_parameters(req,{**req.parameters,'g':.8})
    assert p['hr0']==pytest.approx(0,abs=1e-15) and body['parameters']['hr0']==.2
    assert not optimization.in_region(req,{**p,'hr0':.1})
    req=optimization.SearchInput.model_validate({**body,'rotor_gap_mode':'independent','variables':[{'key':'g','values':[.6,.9]}]})
    assert manufacturing.linked_parameters(req,{**req.parameters,'g':.9})['hr0']==.2


def test_manufacturing_snapshot_is_preserved_in_jobs_and_reports(worker,request_body,monkeypatch):
    from dcsj import reports, report_summary
    req=optimization.SearchInput.model_validate({**request_body,
        'parameters':{**request_body['parameters'],'hr0':.5},
        'variables':[{'key':'L','values':[150,165,170]},{'key':'g','values':[.5,.7]}],
        'rotor_gap_mode':'fixed_neck_bottom'})
    job=jobs.create(req);worker.run_job(worker.claim());saved=jobs.get(job['id'])
    assert saved['evaluated']==7 and not saved['candidates'][0]['feasible']
    best=calculations.get(saved['best_id'])
    stored=copy.deepcopy(best['optimization']['manufacturing'])
    monkeypatch.setattr(manufacturing,'audit',lambda *args: (_ for _ in ()).throw(AssertionError('must not recompute')))
    assert jobs.get(job['id'])['candidates'][0]['manufacturing']['passed'] is False
    assert calculations.get(saved['best_id'])['optimization']['manufacturing']==stored
    assert report_summary.manufacturing_summary(best)['rows'][0][3]=='符合'
    assert '寻优规格与尺寸联动' in reports.render(best)


@pytest.mark.parametrize('algorithm', ['stratified_v1','hybrid_de_v1','adaptive_poll_v1'])
def test_numeric_specs_integer_choices_and_material_categories_mix(request_body,monkeypatch,algorithm):
    monkeypatch.setattr(optimization,'allowed_material',lambda x:x in {'A','B'})
    req=optimization.SearchInput.model_validate({**request_body,'algorithm':algorithm,'max_evaluations':16,
        'variables':[{'key':'Djj','values':[.8,1.0]},{'key':'JJ','values':[2,4]},
                     {'key':'Paihao','choices':['A','B']}]})
    search=optimization.Search(req);assert search.candidate()==req.parameters
    seen=[]
    while (p:=search.candidate()) is not None:
        assert isinstance(p['JJ'],int) and optimization.in_region(req,p)
        seen.append((p['Djj'],p['JJ'],p['Paihao']))
    assert len(seen)==len(set(seen))==8


def test_adaptive_poll_uses_adjacent_nonuniform_specs_and_precise_gap_endpoint(request_body):
    req=optimization.SearchInput.model_validate({**request_body,'algorithm':'adaptive_poll_v1',
        'parameters':{**request_body['parameters'],'g':manufacturing.single(.6),'hr0':.2},
        'variables':[{'key':'L','minimum':150,'maximum':180},{'key':'Djj','values':[.5,.51,.7,.8,.9,1,1.5,2]},
                     {'key':'g','values':[.4,.6,.8]}], 'rotor_gap_mode':'fixed_neck_bottom'})
    search=optimization.Search(req);p=search.candidate();search.consider(p,{'ATA1':.9,'PF1':.8,'Sf':.6,'Sn':.03},True)
    search._new_poll()
    assert {.9,1.5}<={p['Djj'] for p in search.poll_queue}
    for _ in range(7):
        p=search.candidate();assert optimization.in_region(req,p)
        assert p['g'] in {.4,.6,.8}
    # The exact allowed upper bound must never turn into negative round-off.
    req=optimization.SearchInput.model_validate({**request_body,'parameters':{**request_body['parameters'],'hr0':.2},
        'variables':[{'key':'g','values':[.6,.8]}], 'rotor_gap_mode':'fixed_neck_bottom'})
    endpoint=manufacturing.linked_parameters(req,{**req.parameters,'g':.8})
    assert endpoint['hr0']==0 and optimization.in_region(req,endpoint)


def test_no_feasible_result_is_not_replaced_by_unqualified_best(worker, request_body):
    request_body['constraints'] = [{'metric': 'ATA1', 'minimum': .99}]
    queued = jobs.create(optimization.SearchInput.model_validate(request_body))
    worker.run_job(worker.claim())
    result = jobs.get(queued['id'])
    assert result['best_id'] is None and not any(c['feasible'] for c in result['candidates'])


def test_cancelled_queue_never_executes_and_running_cancel_preserves_results(worker, request_body, monkeypatch):
    request = optimization.SearchInput.model_validate(request_body)
    first = jobs.create(request)
    assert jobs.cancel(first['id'])['status'] == 'cancelled'
    assert worker.claim() is None
    second = jobs.create(request)
    identity = worker.claim()
    original = jobs.compute
    def cancel_during_calculation(parameters, **kwargs):
        jobs.cancel(identity)
        return original(parameters, **kwargs)
    monkeypatch.setattr(jobs, 'compute', cancel_during_calculation)
    worker.run_job(identity)
    result = jobs.get(second['id'])
    assert result['status'] == 'cancelled' and result['evaluated'] == 1
    assert len(result['candidates']) == 1


def test_lost_worker_recovery_and_live_worker_exclusion(worker, request_body):
    first = jobs.create(optimization.SearchInput.model_validate(request_body))
    assert worker.claim() == first['id']
    second_worker = jobs.Worker(worker.database); second_worker.touch()
    assert second_worker.claim() is None  # Existing owner is live.
    with connect() as db:
        db.execute('UPDATE optimization_workers SET heartbeat=? WHERE id=?',
                   (time.time() - jobs.LEASE_SECONDS - 1, worker.owner))
    assert second_worker.claim() is None
    assert jobs.get(first['id'])['status'] == 'interrupted'
    retry = jobs.retry(first['id'])
    assert retry['id'] != first['id'] and retry['status'] == 'queued'
    assert second_worker.claim() == retry['id']


def test_objectives_cannot_optimize_rating_or_prices(request_body):
    for variable in ({'key': 'Ccu', 'minimum': 1, 'maximum': 2},
                     {'key': 'kW', 'minimum': 1, 'maximum': 2},
                     {'key': 'JJ', 'minimum': 1.1, 'maximum': 4},
                     {'key': 'L', 'minimum': 10, 'maximum': 5}):
        with pytest.raises(ValidationError):
            optimization.SearchInput.model_validate({**request_body, 'variables': [variable]})
    with pytest.raises(ValidationError):
        optimization.SearchInput.model_validate({**request_body, 'max_evaluations': 129})


def test_repeatable_search_and_out_of_region_baseline(request_body):
    request_body['variables'][0].update(minimum=162, maximum=172, step=2)
    req = optimization.SearchInput.model_validate(request_body)
    a, b = optimization.Search(req), optimization.Search(req)
    first = a.candidate()
    assert not optimization.in_region(req, first)
    assert first == b.candidate()
    for _ in range(7):
        assert a.candidate() == b.candidate()


def test_mixed_manufacturing_grid_is_exhausted_without_duplicates(request_body, monkeypatch):
    monkeypatch.setattr(optimization, 'allowed_material', lambda name: name in {'A', 'B'})
    req=optimization.SearchInput.model_validate({**request_body,'variables':[
        {'key':'L','minimum':150,'maximum':160,'step':5},
        {'key':'JJ','minimum':2,'maximum':3,'step':1},
        {'key':'Paihao','choices':['A','B']}], 'max_evaluations':20})
    search=optimization.Search(req);seen=[]
    while (candidate:=search.candidate()) is not None:
        seen.append(candidate)
        assert candidate['kW']==req.parameters['kW'] and candidate['Ufai']==req.parameters['Ufai']
    assert seen[0]==req.parameters  # An out-of-region baseline remains a separate reference.
    assert len(seen)==13
    assert {(p['L'],p['JJ'],p['Paihao']) for p in seen[1:]}=={
        (length,wires,material) for length in (150,155,160) for wires in (2,3) for material in ('A','B')}
    assert len({tuple(p[v.key] for v in req.variables) for p in seen})==len(seen)


def test_pareto_directions_ties_and_invalid_candidates():
    rows=[{'id':name,'feasible':valid,'objectives':{'efficiency':eta,'material_cost':cost}}
          for name,eta,cost,valid in [('a',.9,100,True),('b',.91,110,True),('c',.89,105,True),
                                    ('d',.95,90,False),('e',.9,100,True),('f',float('nan'),80,True)]]
    assert {r['id'] for r in optimization.pareto_front(rows,['efficiency','material_cost'])}=={'a','b','e'}


def test_feasibility_first_guides_search_and_outside_baseline_is_not_promoted(request_body):
    req=optimization.SearchInput.model_validate({**request_body,'variables':[{'key':'L','minimum':162,'maximum':172}],
              'constraints':[{'metric':'ATA1','minimum':.94}], 'secondary_objective':'material_cost'})
    search=optimization.Search(req)
    for length,eta in [(164,.90),(168,.93),(170,.95)]:
        p={**req.parameters,'L':length};v={'ATA1':eta,'CostFx':100,'PF1':.8,'Sf':.6,'Sn':.03}
        search.consider(p,v,not optimization.assess(req,v))
    population=search._population()
    assert population[0]['parameters']['L']==170
    assert [r['parameters']['L'] for r in population if not r['feasible']]==[168,164]
    search.consider(req.parameters,{'ATA1':.99,'CostFx':1},True)
    assert search.best['L']==170
    assert all(r['parameters']['L']!=req.parameters['L'] for r in search.records)


def test_hybrid_repeatability_quantization_and_fixed_inputs(request_body):
    req=optimization.SearchInput.model_validate({**request_body,'variables':[
        {'key':'L','minimum':150,'maximum':170}, {'key':'ZZ','minimum':12,'maximum':20,'step':2}],
        'max_evaluations':32,'secondary_objective':'material_cost'})
    runs=[]
    for _ in range(2):
        search=optimization.Search(req);run=[]
        for i in range(32):
            p=search.candidate();assert p is not None
            v={'ATA1':.95-abs(p['L']-165)*.001, 'CostFx':p['L'], 'PF1':.8,'Sf':.6,'Sn':.03}
            search.consider(p,v,not optimization.assess(req,v));run.append(p)
            assert optimization.in_region(req,p)
            assert p['ZZ'] in {12,14,16,18,20}
            assert {k:v for k,v in p.items() if k not in {'L','ZZ'}}=={k:v for k,v in req.parameters.items() if k not in {'L','ZZ'}}
        runs.append(run)
    assert runs[0]==runs[1]
    assert len({(p['L'],p['ZZ']) for p in runs[0]})==32


def test_multiobjective_job_preserves_snapshot_values_and_front(worker,request_body,monkeypatch):
    original=jobs.compute
    def tradeoff(p,**kwargs):
        r=original(p,**kwargs);r['values'].update(ATA1=.8+p['L']*.001,CostFx=p['L']);return r
    monkeypatch.setattr(jobs,'compute',tradeoff)
    req=optimization.SearchInput.model_validate({**request_body,'secondary_objective':'material_cost'})
    queued=jobs.create(req);worker.run_job(worker.claim());result=jobs.get(queued['id'])
    assert result['feasible_count']==5 and len(result['pareto_ids'])==5
    assert calculations.get(result['best_id'])['inputs']['L']==170
    for c in result['candidates']:
        saved=calculations.get(c['calculation_id'])
        assert c['objectives']==saved['optimization']['objectives']
        assert c['objectives']['material_cost']==c['parameters']['L']
        assert saved['optimization']['algorithm']=='hybrid_de_v1'
        assert c['violation_score']==0


def test_resource_change_interrupts_without_mixing_snapshots(worker,request_body,monkeypatch):
    original=jobs.compute;calls=[]
    def changing(p,**kwargs):
        calls.append(p);r=original(p,**kwargs)
        r['provenance']['resource_package']='one' if len(calls)==1 else 'two'
        return r
    monkeypatch.setattr(jobs,'compute',changing)
    queued=jobs.create(optimization.SearchInput.model_validate(request_body));worker.run_job(worker.claim())
    result=jobs.get(queued['id'])
    assert result['status']=='interrupted' and '资源' in result['message']
    assert result['evaluated']==1 and len(result['candidates'])==1
    with connect() as db:assert db.execute('SELECT count(*) FROM calculations').fetchone()[0]==1


def test_old_job_retry_retains_original_algorithm_and_invalid_objectives_reject(request_body):
    legacy={k:v for k,v in request_body.items() if k!='algorithm'}
    assert jobs.saved_request(legacy).algorithm=='stratified_v1'
    assert optimization.SearchInput.model_validate(legacy).algorithm=='stratified_v1'
    assert optimization.SearchInput.model_validate(request_body).algorithm=='hybrid_de_v1'
    for extra in ({'secondary_objective':'efficiency'},{'secondary_objective':'unknown'},{'algorithm':'unknown'}):
        with pytest.raises(ValidationError):optimization.SearchInput.model_validate({**request_body,**extra})
    req=optimization.SearchInput.model_validate(request_body)
    assert optimization.assess(req,{'ATA1':1.2,'PF1':.8,'Sf':.6,'Sn':.03})
    assert optimization.assess(req,{'ATA1':float('nan'),'PF1':.8,'Sf':.6,'Sn':.03})


def test_low_budget_screening_covers_axis_edges_before_adaptive_search(request_body):
    req=optimization.SearchInput.model_validate({**request_body,'variables':[
        {'key':'L','minimum':150,'maximum':170},{'key':'g','minimum':.4,'maximum':.8}], 'max_evaluations':24})
    search=optimization.Search(req)
    assert search.candidate()==req.parameters
    edges=[search.candidate() for _ in range(4)]
    assert {(p['L'],p['g']) for p in edges}=={(150,.6),(170,.6),(160,.4),(160,.8)}
    assert all(optimization.in_region(req,p) for p in edges)


def test_adaptive_poll_improves_a_constrained_quadratic_and_keeps_baseline(request_body):
    req=optimization.SearchInput.model_validate({**request_body,'algorithm':'adaptive_poll_v1','variables':[
        {'key':'L','minimum':150,'maximum':170},{'key':'g','minimum':.4,'maximum':.8}],
        'constraints':[{'metric':'PF1','minimum':.82}], 'max_evaluations':32})
    baseline=dict(req.parameters);search=optimization.Search(req);seen=[];feasible=0
    for _ in range(32):
        p=search.candidate();assert p is not None
        v={'ATA1':.96-(p['L']-166)**2*.001-(p['g']-.62)**2,'PF1':.9-.01*(p['L']-160),
           'CostFx':100,'Sf':.6,'Sn':.03}
        ok=not optimization.assess(req,v);feasible+=ok
        search.consider(p,v,ok);seen.append(p)
    assert search.best_score>.95 and feasible>=20
    assert req.parameters==baseline and seen[0]==baseline
    assert len({(p['L'],p['g']) for p in seen})==len(seen)


def test_adaptive_poll_recovers_feasibility_without_promoting_infeasible_objectives(request_body):
    req=optimization.SearchInput.model_validate({**request_body,'algorithm':'adaptive_poll_v1',
        'variables':[{'key':'L','minimum':150,'maximum':170}],
        'constraints':[{'metric':'PF1','minimum':.8}], 'max_evaluations':24})
    search=optimization.Search(req)
    for _ in range(24):
        p=search.candidate()
        if p is None:break
        v={'ATA1':.98-p['L']*.001,'PF1':.75+.01*(p['L']-160),'CostFx':100,'Sf':.6,'Sn':.03}
        search.consider(p,v,not optimization.assess(req,v))
    assert search.best is not None and search.best['L']==165


def test_adaptive_poll_alternates_objectives_without_hidden_unit_weights(request_body):
    req=optimization.SearchInput.model_validate({**request_body,'algorithm':'adaptive_poll_v1',
        'variables':[{'key':'L','minimum':150,'maximum':170}],
        'secondary_objective':'material_cost','max_evaluations':24})
    runs=[]
    for scale in (1,1000000):
        search=optimization.Search(req);seen=[]
        for _ in range(24):
            p=search.candidate()
            if p is None:break
            v={'ATA1':.8+p['L']*.001,'CostFx':p['L']*scale,'PF1':.8,'Sf':.6,'Sn':.03}
            search.consider(p,v,True);seen.append(p)
        assert min(p['L'] for p in seen)<=155 and max(p['L'] for p in seen)>=165
        runs.append(seen)
    assert runs[0]==runs[1]


def test_adaptive_poll_survives_failed_evaluations_and_preserves_mixed_domains(request_body,monkeypatch):
    monkeypatch.setattr(optimization,'allowed_material',lambda name:name in {'A','B'})
    req=optimization.SearchInput.model_validate({**request_body,'algorithm':'adaptive_poll_v1','variables':[
        {'key':'L','minimum':150,'maximum':170},{'key':'JJ','minimum':2,'maximum':6,'step':2},
        {'key':'Paihao','choices':['A','B']}],'max_evaluations':32})
    search=optimization.Search(req);seen=[]
    # The worker does not call consider when the numerical kernel fails.
    for _ in range(32):
        p=search.candidate();assert p is not None;seen.append(p)
        if len(seen)>1:
            assert optimization.in_region(req,p)
            assert p['JJ'] in {2,4,6} and p['Paihao'] in {'A','B'}
        assert p['kW']==req.parameters['kW'] and p['Ufai']==req.parameters['Ufai']
    assert len({tuple(p[v.key] for v in req.variables) for p in seen})==32


def test_adaptive_poll_worker_budget_and_snapshot_integration(worker,request_body):
    request=optimization.SearchInput.model_validate({**request_body,'algorithm':'adaptive_poll_v1','max_evaluations':4})
    job=jobs.create(request);worker.run_job(worker.claim());result=jobs.get(job['id'])
    assert result['status']=='succeeded' and result['evaluated']==4
    assert calculations.get(result['best_id'])['optimization']['algorithm']=='adaptive_poll_v1'


def test_snap_preserves_nondecimal_boundaries_and_fine_manufacturing_grid(request_body):
    for spec in ({'key':'g','minimum':1/30,'maximum':4/15},
                 {'key':'L','minimum':1/7,'maximum':1.5/7,'step':.01},
                 {'key':'g','minimum':1e-13,'maximum':1e-11,'step':1e-13}):
        v=optimization.Variable(**spec)
        for raw in (v.minimum-1,v.minimum,v.maximum,v.maximum+1,(v.minimum+v.maximum)/2):
            value=optimization.snap(v,raw)
            assert v.minimum<=value<=v.maximum
            if v.step:assert (value-v.minimum)/v.step==pytest.approx(round((value-v.minimum)/v.step),abs=1e-7)
