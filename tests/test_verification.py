"""Post-selection checks must expose new failures without rewriting search evidence."""
import copy
import json
import pytest
from pydantic import ValidationError
from dcsj import jobs, verification, robustness, calculations, storage, optimization
from test_robustness import robust_body, robust_worker, execute


@pytest.mark.parametrize('dimensions', [1,2,3])
@pytest.mark.parametrize('count', [4,8,16,32])
def test_fresh_jittered_strata_reproduce_and_exclude_all_search_inputs(robust_body, dimensions, count):
    req=optimization.SearchInput.model_validate({**robust_body,'max_evaluations':128,'tolerance_interior_samples':4,
        'tolerances':[{'key':'g','delta':.05},{'key':'L','delta':1.},{'key':'Djj','delta':.01}][:dimensions],
        'tolerance_gap_mode':'fixed_neck_bottom'})
    previous=robustness.scenarios(req,req.parameters)
    banned={verification.fingerprint(s['parameters']) for s in previous}
    added=verification.additional_samples(req,req.parameters,count,43,banned)
    assert added==verification.additional_samples(req,req.parameters,count,43,banned)
    assert added==verification.additional_samples(req.model_copy(update={'tolerances':list(reversed(req.tolerances))}),req.parameters,count,43,banned)
    assert added!=verification.additional_samples(req,req.parameters,count,44,banned)
    assert len({verification.fingerprint(s['parameters']) for s in added})==count
    assert not {verification.fingerprint(s['parameters']) for s in added}&banned
    for t in req.tolerances:
        assert sorted(int((s['offsets'][t.key]/t.delta+1)*count/2) for s in added)==list(range(count))
    assert all(s['parameters']['g']+s['parameters']['hr0']==pytest.approx(1.1) for s in added)


def test_insufficient_float32_resolution_rejects_duplicate_budget(robust_body):
    req=optimization.SearchInput.model_validate({**robust_body,'tolerances':[{'key':'g','delta':1e-15}]})
    with pytest.raises(storage.ConflictError,match='计算精度'):
        verification.additional_samples(req,req.parameters,4,43,{verification.fingerprint(req.parameters)})


def test_input_fingerprint_normalizes_signed_zero_and_single_precision():
    assert verification.fingerprint({'MM':-0.0,'g':.3000000001}) == verification.fingerprint({'MM':0,'g':.3})


@pytest.mark.parametrize('body',[{'interior_samples':0},{'interior_samples':5},{'interior_samples':True},
    {'interior_samples':4.0},{'seed':True},{'seed':-1},{'candidate_ordinal':0},{'plan':[]}])
def test_public_options_are_strict_and_cannot_supply_plan(body):
    with pytest.raises(ValidationError):verification.VerificationInput.model_validate(body)


def check(worker,source,**options):
    child=verification.create(source['id'],verification.VerificationInput(**options))
    worker.run_job(worker.claim())
    return jobs.get(child['id'])


def test_fixed_candidate_rechecks_previous_inputs_and_keeps_parent_immutable(robust_worker,robust_body,monkeypatch):
    worker,calls=robust_worker;source=execute(worker,robust_body);before=calculations.get(source['best_id'])
    sampler=verification.additional_samples
    root_inputs={verification.fingerprint(s['parameters']) for s in source['samples']}
    def checked_sampler(req,nominal,count,seed,forbidden):
        assert root_inputs<=forbidden, 'Exclude every searched candidate, not only the selected one'
        return sampler(req,nominal,count,seed,forbidden)
    monkeypatch.setattr(verification,'additional_samples',checked_sampler)
    child=check(worker,source,interior_samples=4)
    assert child['evaluated']==child['budget']==7 and child['status']=='succeeded' and child['feasible_count']==1
    assert len(child['candidates'])==1 and len(calls)==13
    meta=child['request']['verification'];assert meta['seed']==43 and meta['prior_samples']==3
    assert child['request']['parameters']==before['inputs']==child['candidates'][0]['parameters']
    assert child['samples'][0]['parameters']==before['inputs']
    assert [s['phase'] for s in child['samples']]==['recheck']*3+['additional']*4
    assert all(s['parameters']['L']==170 for s in child['samples'])
    assert all(s['calculation_id'] not in {p['calculation_id'] for p in source['samples']} for s in child['samples'])
    assert calculations.get(source['best_id'])==before
    after=jobs.get(source['id']);assert after.pop('verifications')[0]['id']==child['id']
    assert after=={k:v for k,v in source.items() if k!='verifications'}
    again=check(worker,child,interior_samples=4)
    assert again['budget']==11 and again['request']['verification']['root_job_id']==source['id']
    assert again['request']['verification']['seed']==44
    old={verification.fingerprint(s['parameters']) for s in child['samples']}
    assert not old&{verification.fingerprint(s['parameters']) for s in again['samples'][7:]}
    retried=jobs.retry(again['id'])
    assert retried['request']['verification']==again['request']['verification']
    assert retried['samples']==[]


@pytest.mark.parametrize('failure',['constraint','numerical','material','kernel','wrapper','package'])
def test_new_failure_or_resource_drift_cannot_pass(robust_worker,robust_body,monkeypatch,failure):
    worker,calls=robust_worker
    source=execute(worker,{**robust_body,'constraints':[{'metric':'ATA1','minimum':.9}]})
    before=calculations.get(source['best_id']);original=jobs.compute
    def changed(p,**kwargs):
        r=original(p,**kwargs)
        if failure in {'material','kernel','wrapper','package'}:
            key={'material':'material_sha256','kernel':'kernel_sha256','wrapper':'wrapper_sha256','package':'resource_package'}[failure]
            r['provenance'][key]='changed'
        elif p['g'] not in {.55,.6,.65} and abs(p['g']-.55)>1e-12:
            if failure=='constraint':r['values']['ATA1']=.5
            else:raise jobs.CalculationError('synthetic new-point failure')
        return r
    monkeypatch.setattr(jobs,'compute',changed)
    child=check(worker,source,interior_samples=4)
    assert child['best_id'] is None and not child['candidates'][0]['feasible']
    if failure in {'material','kernel','wrapper','package'}:
        assert child['status']=='interrupted' and child['evaluated']==1
        assert child['samples'][0]['calculation_id'] is None
    else:
        assert child['evaluated']==7
        assert child['status']==('succeeded' if failure=='constraint' else 'failed')
    assert calculations.get(source['best_id'])==before and jobs.get(source['id'])['best_id']==source['best_id']


@pytest.mark.parametrize('mode',['cancel','lease','stop'])
def test_partial_verification_is_never_a_pass(robust_worker,robust_body,monkeypatch,mode):
    worker,_=robust_worker;source=execute(worker,robust_body)
    child=verification.create(source['id'],verification.VerificationInput(interior_samples=4))
    original=jobs.compute
    def interrupted(p,**kw):
        r=original(p,**kw)
        if mode=='cancel':jobs.cancel(child['id'])
        elif mode=='stop':worker.stop.set()
        else:
            with storage.connect() as db:db.execute('UPDATE optimization_jobs SET owner=? WHERE id=?',('other',child['id']))
        return r
    monkeypatch.setattr(jobs,'compute',interrupted);worker.run_job(worker.claim());child=jobs.get(child['id'])
    assert not child['best_id'] and not child['candidates'][0]['feasible']
    assert child['evaluated']==(0 if mode=='lease' else 1)
    if mode!='lease':assert child['candidates'][0]['robustness']['status']=='incomplete'


def test_source_guards_and_route_authorization(robust_worker,robust_body):
    from fastapi.testclient import TestClient
    from dcsj.api import app
    worker,_=robust_worker;source=jobs.create(optimization.SearchInput.model_validate(robust_body))
    with pytest.raises(storage.ConflictError,match='结束'):verification.create(source['id'],verification.VerificationInput())
    worker.run_job(worker.claim())
    with pytest.raises(storage.ConflictError):verification.create(source['id'],verification.VerificationInput(candidate_ordinal=99))
    client=TestClient(app)
    route=f"/api/v1/optimization/jobs/{source['id']}/verify"
    assert client.post(route,json={}).status_code==403
    client.headers['X-DCSJ-Request']='1'
    assert client.post(route,json={}).status_code==401
    assert client.post('/api/v1/auth/login',json={'username':'复核测试','password':'1234'}).status_code==200
    assert client.post(route,json={'plan':[]}).status_code==422
    assert client.post(route,json={'interior_samples':4,'candidate_ordinal':1}).status_code==409
    response=client.post(route,json={'interior_samples':4,'candidate_ordinal':2})
    assert response.status_code==202 and response.json()['budget']==7
    assert response.json()['request']['verification']['source_candidate_ordinal']==2
    # Ordinary search clients may not bypass the two-group budget or inject frozen metadata.
    assert client.post('/api/v1/optimization/jobs',json={**robust_body,'max_evaluations':3}).status_code==422
    assert client.post('/api/v1/optimization/jobs',json={**robust_body,'verification':{}}).status_code==422


def test_verification_report_reads_frozen_provenance(robust_worker,robust_body,monkeypatch):
    from dcsj import reports,report_summary
    worker,_=robust_worker;child=check(worker,execute(worker,robust_body),interior_samples=4)
    saved=calculations.get(child['best_id'])
    monkeypatch.setattr(verification,'additional_samples',lambda *a,**kw:pytest.fail('Do not regenerate historical plans'))
    assert jobs.get(child['id'])==child
    html=reports.render(saved)
    for value in ('补充采样复核','种子 43','补充采样 4',child['request']['verification']['source_job_id']):
        assert value in html
    assert report_summary.robustness_summary(saved)['title']=='补充采样复核'


def test_chained_budget_limit_and_non_tolerance_sources(robust_worker,robust_body):
    worker,_=robust_worker
    plain=execute(worker,{k:v for k,v in robust_body.items() if k!='tolerances'})
    with pytest.raises(storage.ConflictError):verification.create(plain['id'],verification.VerificationInput())
    source=execute(worker,robust_body)
    for expected in (35,67,99):
        source=check(worker,source,interior_samples=32)
        assert source['budget']==expected and source['best_id']
    with pytest.raises(storage.ConflictError,match='128'):verification.create(source['id'],verification.VerificationInput(interior_samples=32))
