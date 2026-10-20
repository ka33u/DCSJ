import copy
import time

import pytest
from pydantic import ValidationError
from dcsj import jobs, optimization, calculations
from dcsj.storage import connect


@pytest.fixture
def request_body(parameters):
    return {'name': 'Synthetic search', 'parameters': parameters,
            'variables': [{'key': 'L', 'minimum': 150, 'maximum': 170, 'step': 5}],
            'objective': 'efficiency', 'max_evaluations': 8, 'seed': 19}


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
