"""Nominal continuation uses isolated synthetic snapshots, never the user DB."""
import copy
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from dcsj import continuation as cont, jobs, optimization as opt
from dcsj.api import app
from dcsj.parameters import validate
from dcsj.storage import ConflictError, connect, dumps

ALGORITHMS = ['stratified_v1', 'hybrid_de_v1', 'adaptive_poll_v1',
              'coupled_poll_v1', 'ranked_poll_v1', 'curvature_rescue_v1']


@pytest.fixture
def setup(tmp_path, monkeypatch, parameters, snapshot):
    database = tmp_path / 'continuation.sqlite'
    monkeypatch.setenv('DCSJ_PROJECTS_DB', str(database))
    monkeypatch.setattr(jobs, 'engine_status', lambda: {'status': 'recovered'})
    monkeypatch.setattr(opt, 'allowed_material', lambda _: True)
    resources = {'kernel_sha256': 'synthetic-kernel', 'wrapper_sha256': 'synthetic-wrapper',
                 'resource_package': None, 'resource_version': 'synthetic.1',
                 'materials': {'SYNTHETIC': 'synthetic-material', 'OTHER': 'other-material'}}
    monkeypatch.setattr(cont, 'current_resources', lambda request: copy.deepcopy(resources))
    s = SimpleNamespace(database=database, calls=[], resources=resources, fail=False, callback=None)
    def compute(p, **kw):
        s.calls.append(copy.deepcopy(p))
        if s.callback:
            s.callback()
        if s.fail or len(s.calls) % 4 == 0:
            raise jobs.CalculationError('Synthetic failure')
        result = copy.deepcopy(snapshot)
        result['inputs'] = validate(p)
        result['values'].update(ATA1=.95-abs(p['L']-165)*.001, CostFx=p['L']*2,
            Sf=.6, Sn=.03, Pcu=100, Pal=80, Pfe=70, Pfw1=20, Ps1=10)
        result['provenance'].update({k:resources[k] for k in cont.RESOURCE_KEYS},
            material_sha256=resources['materials'][p['Paihao']], input_sha256=cont.fingerprint(result['inputs']))
        return result
    monkeypatch.setattr(jobs, 'compute', compute)
    s.worker = jobs.Worker(database); s.worker.touch()
    s.body = {'name': 'Synthetic continuation', 'parameters': {**parameters, 'hr0': .5},
        'variables': [{'key':'L', 'minimum':150, 'maximum':170}],
        'objective':'efficiency', 'secondary_objective':'material_cost',
        'constraints':[{'metric':'ATA1','minimum':.935}], 'max_evaluations':12, 'seed':19}
    def run(identity):
        assert s.worker.claim() == identity
        s.worker.run_job(identity)
        return jobs.get(identity)
    s.run = run
    def root(**changes):
        return run(jobs.create(opt.SearchInput.model_validate({**s.body, **changes}))['id'])
    s.root = root
    return s


def extra(n):
    return cont.ExtensionInput(additional_evaluations=n)


def direct_observation(search, candidate, database):
    assert search.candidate() == candidate['parameters']
    if candidate['error'] is None:
        with connect(database) as db:
            result = json.loads(db.execute('SELECT result FROM calculations WHERE id=?',
                                           (candidate['calculation_id'],)).fetchone()[0])
        search.consider(candidate['parameters'], opt.metrics(result, search.request), candidate['feasible'])


@pytest.mark.parametrize('algorithm', ALGORITHMS)
@pytest.mark.parametrize('interrupted', [False, True])
def test_replays_original_initialization_and_multiple_budget_boundaries(setup, algorithm, interrupted):
    s = setup
    if interrupted:
        def cancel():
            if len(s.calls) == 5:
                with connect(s.database) as db:
                    db.execute("UPDATE optimization_jobs SET cancel_requested=1 WHERE status='running'")
        s.callback = cancel
    source = s.root(algorithm=algorithm)
    s.callback = None
    control = opt.Search(opt.SearchInput.model_validate(source['request']))
    for candidate in source['candidates']:
        direct_observation(control, candidate, s.database)
    assert source['evaluated'] == (5 if interrupted else 12)
    for addition in (3, 5):
        old = cont.read_history(source['id']); calls = len(s.calls)
        child = cont.create(source['id'], extra(addition))
        assert len(s.calls) == calls, 'Queueing must perform no engine calls'
        assert cont.read_history(source['id']) == old, 'Original rows and snapshots are immutable'
        assert child['best_id'] == source['best_id'] and child['pareto_ids'] == source['pareto_ids']
        assert child['evaluated'] == source['evaluated'] and child['new_evaluated'] == 0
        assert [c['calculation_id'] for c in child['candidates']] == [c['calculation_id'] for c in source['candidates']]
        control.request = control.request.model_copy(update={'max_evaluations':source['evaluated']+addition})
        completed = s.run(child['id'])
        assert len(s.calls)-calls == addition
        assert completed['new_evaluated'] == addition and completed['new_progress'] == 1
        for candidate in completed['candidates'][source['evaluated']:]:
            direct_observation(control, candidate, s.database)
        assert len({dumps(c['parameters']) for c in completed['candidates']}) == completed['evaluated']
        assert any(c['error'] for c in completed['candidates']), 'Failed attempts remain in the trajectory'
        assert jobs.get(source['id'])['extensions'][0]['id'] == child['id']
        assert [c['calculation_id'] for c in completed['candidates'][:source['evaluated']]] == [c['calculation_id'] for c in source['candidates']]
        source = completed


def test_material_prices_stock_sizes_gap_linkage_and_assessments_survive(setup):
    s = setup
    root = s.root(max_evaluations=5, algorithm='coupled_poll_v1', material_prices={'SYNTHETIC':4.0,'OTHER':8.0},
        rotor_gap_mode='fixed_neck_bottom', variables=[{'key':'L','values':[150,155,160,165,170]},
        {'key':'g','values':[.4,.6,.8]}, {'key':'Paihao','choices':['SYNTHETIC','OTHER']}])
    with connect(s.database) as db:
        for c in root['candidates']:
            if c['calculation_id']:
                record = json.loads(db.execute('SELECT result FROM calculations WHERE id=?', (c['calculation_id'],)).fetchone()[0])['optimization']
                db.execute('INSERT INTO optimization_assessments VALUES (?,?,?)',(root['id'],c['ordinal'],dumps(record)))
    child = s.run(cont.create(root['id'], extra(8))['id'])
    assert child['evaluated'] == 13
    for c in child['candidates']:
        p = c['parameters']
        assert p['Cfe'] == {'SYNTHETIC':4.0,'OTHER':8.0}[p['Paihao']]
        assert p['g']+p['hr0'] == pytest.approx(1.1)
    assert len(cont.read_history(child['id'])['assessments']) == 4
    cont.state(cont.read_history(child['id']))


@pytest.mark.parametrize('algorithm', ALGORITHMS)
def test_integer_candidates_reuse_normalized_engine_inputs(setup, algorithm):
    s = setup
    root = s.root(algorithm=algorithm, variables=[{'key':'L','minimum':150,'maximum':170},
        {'key':'ZZ','values':[14,16,18]}, {'key':'JJ','values':[1,2,3]}])
    assert any(type(c['parameters']['ZZ']) is int for c in root['candidates'])
    assert any(type(c['parameters']['JJ']) is int for c in root['candidates'])
    calls = len(s.calls)
    child = s.run(cont.create(root['id'], extra(4))['id'])
    assert len(s.calls) == calls+4 and child['new_evaluated'] == 4
    cont.state(cont.read_history(child['id']))
    assert child['candidates'][:root['evaluated']] == [{**c,'job_id':child['id']} for c in root['candidates']]


def test_normalized_fingerprint_accepts_numeric_types_but_rejects_changed_values(setup):
    s = setup
    p = validate(s.body['parameters']); p.update(ZZ=16, JJ=1)
    result = jobs.compute(p)
    assert type(result['inputs']['ZZ']) is float
    assert cont.fingerprint(p) != result['provenance']['input_sha256']
    cont.check_result(result,p,s.resources)
    with pytest.raises(ConflictError): cont.check_result(result,{**p,'ZZ':18},s.resources)
    result['provenance']['input_sha256'] = cont.fingerprint(p)
    with pytest.raises(ConflictError): cont.check_result(result,p,s.resources)


def test_previous_continuation_fingerprint_can_extend_and_retry_without_changing_trajectory(setup):
    s = setup; root = s.root(); child = s.run(cont.create(root['id'], extra(3))['id'])
    document = child['request']
    document['continuation']['continuation_sha256'] = 'ec2e294d6a240f2dbc09b29b607b03bdf0ab4720a2ba67bff5b85f7d8157df30'
    with connect(s.database) as db:
        db.execute('UPDATE optimization_jobs SET request=? WHERE id=?',(dumps(document),child['id']))
    retry = jobs.retry(child['id'])
    assert retry['request']['continuation']['continuation_sha256'] == cont.CONTINUATION_SHA256
    assert retry['evaluated'] == root['evaluated'] and retry['budget'] == child['budget']
    jobs.cancel(retry['id'])
    following = cont.create(child['id'],extra(2))
    assert following['evaluated'] == child['evaluated']
    jobs.cancel(following['id'])
    document['continuation']['continuation_sha256'] = 'unrecognized-replay-code'
    with connect(s.database) as db:
        db.execute('UPDATE optimization_jobs SET request=? WHERE id=?',(dumps(document),child['id']))
    with pytest.raises(ConflictError): cont.create(child['id'],extra(2))


def test_all_new_failures_and_retry_preserve_original_best_and_prefix(setup):
    s = setup; root = s.root(); s.fail = True
    child = s.run(cont.create(root['id'], extra(3))['id'])
    assert child['status'] == 'succeeded' and child['best_id'] == root['best_id']
    assert child['pareto_ids'] == root['pareto_ids']
    assert all(c['error'] for c in child['candidates'][root['evaluated']:])
    retry = jobs.retry(child['id'])
    assert retry['request']['continuation'] == child['request']['continuation']
    assert retry['evaluated'] == root['evaluated'] and retry['budget'] == child['budget']
    assert retry['candidates'] != child['candidates']
    repeated = s.run(retry['id'])
    assert [c['parameters'] for c in repeated['candidates']] == [c['parameters'] for c in child['candidates']]


@pytest.mark.parametrize('event', ['cancel', 'stop', 'lease', 'resource', 'returned_resource'])
def test_inflight_interruptions_do_not_erase_inherited_archive(setup, event, monkeypatch):
    s = setup; root = s.root(); child = cont.create(root['id'], extra(3)); calls = len(s.calls)
    def callback():
        if event == 'cancel': jobs.cancel(child['id'])
        if event == 'stop': s.worker.stop.set()
        if event == 'lease':
            with connect(s.database) as db:
                db.execute("UPDATE optimization_jobs SET owner='another-worker',status='interrupted' WHERE id=?", (child['id'],))
        if event == 'resource': s.resources['materials']['OTHER'] = 'changed-even-if-not-selected'
    s.callback = callback
    if event == 'returned_resource':
        compute = jobs.compute
        def mismatch(p, **kwargs):
            r = compute(p, **kwargs); r['provenance']['wrapper_sha256'] = 'other'; return r
        monkeypatch.setattr(jobs, 'compute', mismatch)
    completed = s.run(child['id'])
    assert len(s.calls) == calls+1
    assert completed['status'] in {'cancelled','interrupted'}
    assert completed['best_id'] is not None
    for c, old in zip(completed['candidates'], root['candidates']):
        assert c['calculation_id'] == old['calculation_id'] and c['parameters'] == old['parameters']
    if event in {'lease','resource','returned_resource'}:
        assert completed['evaluated'] == root['evaluated'] and completed['best_id'] == root['best_id']


def test_resource_change_after_queue_stops_before_computation(setup):
    s = setup; root = s.root(); child = cont.create(root['id'], extra(4)); calls = len(s.calls)
    s.resources['materials']['SYNTHETIC'] = 'changed'
    result = s.run(child['id'])
    assert result['status'] == 'interrupted' and result['best_id'] == root['best_id']
    assert len(s.calls) == calls
    with pytest.raises(ConflictError): cont.create(root['id'], extra(2))


@pytest.mark.parametrize('mutation', ['missing', 'parameters', 'values', 'solver', 'best', 'objective', 'constraint', 'assessment'])
def test_inconsistent_history_fails_closed_without_new_job_or_engine_calls(setup, mutation):
    s = setup; root = s.root(); calls = len(s.calls); first = root['candidates'][0]
    with connect(s.database) as db:
        if mutation == 'missing': db.execute('DELETE FROM optimization_candidates WHERE job_id=? AND ordinal=2',(root['id'],))
        elif mutation == 'best': db.execute('UPDATE optimization_jobs SET best_id=NULL WHERE id=?',(root['id'],))
        elif mutation == 'objective': db.execute('UPDATE optimization_candidates SET objective=.1 WHERE job_id=? AND ordinal=1',(root['id'],))
        elif mutation == 'parameters':
            p = dict(first['parameters'], L=151)
            db.execute('UPDATE optimization_candidates SET parameters=? WHERE job_id=? AND ordinal=1',(dumps(p),root['id']))
        elif mutation == 'assessment': db.execute('INSERT INTO optimization_assessments VALUES (?,1,?)',(root['id'],'{}'))
        else:
            result = json.loads(db.execute('SELECT result FROM calculations WHERE id=?',(first['calculation_id'],)).fetchone()[0])
            if mutation == 'values': result['values']['ATA1'] = .1
            if mutation == 'solver': result['optimization']['solver_sha256'] = 'old'
            if mutation == 'constraint': result['optimization']['constraints'] = []
            db.execute('UPDATE calculations SET result=? WHERE id=?',(dumps(result),first['calculation_id']))
    with pytest.raises(ConflictError): cont.create(root['id'], extra(3))
    assert len(s.calls) == calls
    assert len(jobs.list_jobs()) == 1


def test_prefix_tampering_and_duplicate_run_are_rejected(setup):
    s = setup; root = s.root(); child = cont.create(root['id'], extra(3))
    s.run(child['id'])
    with pytest.raises(ConflictError): cont.prepare(child['id'])
    with connect(s.database) as db:
        db.execute("UPDATE optimization_candidates SET error='changed' WHERE job_id=? AND error IS NOT NULL", (child['id'],))
    with pytest.raises(ConflictError): cont.create(child['id'], extra(3))


def test_exhausted_grid_empty_all_failed_and_running_tasks_rejected(setup):
    s = setup
    grid = s.root(variables=[{'key':'L','values':[150,160,170]}], max_evaluations=8)
    with pytest.raises(ConflictError, match='不重复'): cont.create(grid['id'], extra(2))
    s.fail = True
    failed = s.root(max_evaluations=2)
    with pytest.raises(ConflictError, match='全部计算失败'): cont.create(failed['id'], extra(2))
    pending = jobs.create(opt.SearchInput.model_validate(s.body))
    with pytest.raises(ConflictError, match='等待'): cont.create(pending['id'], extra(2))
    jobs.cancel(pending['id'])
    with pytest.raises(ConflictError): cont.create(pending['id'], extra(2))


def test_cumulative_over_128_and_cancelled_unspent_boundary(setup):
    s = setup; root = s.root(max_evaluations=3)
    child = cont.create(root['id'], extra(128))
    assert child['budget'] == 131
    jobs.cancel(child['id'])
    replacement = cont.create(child['id'], extra(2))
    assert replacement['request']['continuation']['transitions'] == [{'after':3,'budget':5}]
    result = s.run(replacement['id'])
    assert result['evaluated'] == 5 and result['new_evaluated'] == 2
    with pytest.raises(ValidationError): opt.SearchInput.model_validate({**s.body, 'max_evaluations':131})
    with pytest.raises(ValidationError): cont.ContinuationSearch.model_validate({**s.body, 'max_evaluations':1025})


def test_repeated_extensions_reach_cumulative_cap_without_resetting_history(setup):
    s = setup; source = s.root(max_evaluations=128)
    baseline_id = source['candidates'][0]['calculation_id']
    for _ in range(7):
        source = s.run(cont.create(source['id'], extra(128))['id'])
        assert source['status'] == 'succeeded'
        assert source['candidates'][0]['calculation_id'] == baseline_id
    assert source['evaluated'] == 1024 and len(s.calls) == 1024
    calls = len(s.calls)
    with pytest.raises(ConflictError): cont.create(source['id'], extra(1))
    assert len(s.calls) == calls


def test_retry_rejects_a_different_parent_prefix(setup):
    s = setup; root = s.root(); child = cont.create(root['id'], extra(2)); jobs.cancel(child['id'])
    other = s.root(seed=20)
    with connect(s.database) as db:
        document = child['request']
        document['continuation']['source_job_id'] = other['id']
        db.execute('UPDATE optimization_jobs SET request=? WHERE id=?',(dumps(document),child['id']))
    with pytest.raises(ConflictError): jobs.retry(child['id'])
    assert len(jobs.list_jobs()) == 3


def test_source_race_and_full_queue_do_not_partially_copy_a_child(setup, monkeypatch):
    s = setup; root = s.root(); original = cont.state
    def mutate(history):
        restored = original(history)
        with connect(s.database) as db:
            db.execute("UPDATE optimization_jobs SET message='changed during validation' WHERE id=?", (root['id'],))
        return restored
    monkeypatch.setattr(cont, 'state', mutate)
    with pytest.raises(ConflictError, match='源任务记录'): cont.create(root['id'], extra(2))
    assert len(jobs.list_jobs()) == 1
    monkeypatch.setattr(cont, 'state', original)
    for _ in range(8): jobs.create(opt.SearchInput.model_validate(s.body))
    with pytest.raises(jobs.BusyError): cont.create(root['id'], extra(2))
    assert len(jobs.list_jobs()) == 9


def test_current_resource_identity_includes_each_resolved_material(parameters, monkeypatch):
    from dcsj import engine, library
    context = {'kernel_sha256':'kernel','kernel_path':'unused','package_id':'package','package_version':'v1',
               'materials':{'SYNTHETIC':{'name':'SYNTHETIC','table':[1,2]},'OTHER':{'name':'OTHER','table':[3,4]}}}
    monkeypatch.setattr(engine,'resource_context',lambda:context)
    monkeypatch.setattr(opt,'allowed_material',lambda _:True)
    resolved = {'name':'User table','table':[5,6],'user_reference':'user-material:test@1'}
    monkeypatch.setattr(library,'calculation_material',lambda ref,f,materials:resolved)
    request = opt.SearchInput.model_validate({'parameters':parameters,'variables':[{'key':'Paihao','choices':['OTHER','user-material:test@1']}]})
    resources = cont.current_resources(request)
    assert resources['materials'] == {k:cont.fingerprint(v) for k,v in {**context['materials'],'user-material:test@1':resolved}.items()}
    context['materials']['OTHER']['table'].append(7)
    with pytest.raises(ConflictError): cont.check_resources(request,resources)


@pytest.mark.parametrize('value', [0, -1, 129, 1.5, '3', True, None])
def test_strict_additional_budget(value):
    with pytest.raises(ValidationError): extra(value)


def test_api_authentication_body_isolation_and_no_calculations_when_queued(setup, monkeypatch):
    s = setup; root = s.root(); calls = len(s.calls)
    monkeypatch.setattr(jobs.Worker, 'start', lambda self: self)
    with TestClient(app) as client:
        url = f"/api/v1/optimization/jobs/{root['id']}/extend"
        assert client.post(url,json={'additional_evaluations':2}).status_code == 403
        client.headers['X-DCSJ-Request'] = '1'
        assert client.post(url,json={'additional_evaluations':2}).status_code == 401
        assert client.post('/api/v1/auth/login',json={'username':'Synthetic continuation tester','password':'1234'}).status_code == 200
        for body in ({'additional_evaluations':True}, {'additional_evaluations':2,'seed':7}, {'additional_evaluations':2,'continuation':{}}, {'max_evaluations':3}):
            assert client.post(url,json=body).status_code == 422
        response = client.post(url,json={'additional_evaluations':2})
        assert response.status_code == 202, response.text
        assert response.json()['new_evaluated'] == 0 and len(s.calls) == calls
        assert client.post('/api/v1/optimization/jobs',json={**s.body,'continuation':{}}).status_code == 422
