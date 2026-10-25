"""Previously discovered failures must remain in every subsequent candidate check."""
import copy
import json
import pytest
from pydantic import ValidationError
from dcsj import feedback, jobs, verification, calculations, storage, reports, report_summary
from test_robustness import robust_body, robust_worker, execute
from test_verification import check


@pytest.fixture
def discovered(robust_worker,robust_body,monkeypatch):
    worker,calls=robust_worker
    original=jobs.compute
    def compute(p,**kw):
        r=original(p,**kw)
        interior=1e-8<abs(p['g']-.6)<.049
        r['values']['ATA1']=.7 if p['L']==160 and interior else .96 if p['L']==160 else .94
        return r
    monkeypatch.setattr(jobs,'compute',compute)
    source=execute(worker,{**robust_body,'constraints':[{'metric':'ATA1','minimum':.9}],
        'tolerance_gap_mode':'fixed_neck_bottom'})
    child=check(worker,source,interior_samples=4)
    assert child['evaluated']==7 and not child['best_id'] and child['status']=='succeeded'
    return worker,source,child


@pytest.mark.parametrize('body',[{'max_evaluations':1},{'max_evaluations':129},{'max_evaluations':14.0},
    {'seed':True},{'seed':-1},{'plan':[]},{'constraints':[]},{'parameters':{}}])
def test_options_cannot_rewrite_original_scope(body):
    with pytest.raises(ValidationError):feedback.FeedbackInput.model_validate(body)


def test_retained_failures_change_selection_and_fresh_checks_exclude_every_ancestor(discovered,monkeypatch):
    worker,root,source=discovered
    before=copy.deepcopy(source);snapshot=calculations.get(source['candidates'][0]['calculation_id'])
    child=feedback.create(source['id'],feedback.FeedbackInput(max_evaluations=21))
    meta=child['request']['feedback']
    assert meta['retained_samples']==7 and meta['source_failed_samples']==4
    assert [s['offsets'] for s in meta['plan']]==[s['offsets'] for s in source['samples']]
    assert meta['source_candidate_ordinal']==1
    for key in ('parameters','variables','constraints','objective','rotor_gap_mode','tolerance_gap_mode','tolerances'):
        assert child['request'][key]==source['request'][key]
    worker.run_job(worker.claim());child=jobs.get(child['id'])
    assert child['evaluated']==14 and len(child['candidates'])==2
    best=calculations.get(child['best_id']);assert best['inputs']['L']==170
    assert not child['candidates'][0]['feasible'] and child['candidates'][1]['feasible']
    assert all(c['robustness']['expected_samples']==7 for c in child['candidates'])
    for ordinal in (1,2):
        samples=[s for s in child['samples'] if s['ordinal']==ordinal]
        assert [s['offsets'] for s in samples]==[s['offsets'] for s in source['samples']]
        assert all(s['parameters']['g']+s['parameters']['hr0']==pytest.approx(1.1) for s in samples)
    assert calculations.get(source['candidates'][0]['calculation_id'])==snapshot
    updated=jobs.get(source['id']);assert updated.pop('continuations')[0]['id']==child['id']
    assert updated=={k:v for k,v in before.items() if k!='continuations'}
    assert report_summary.robustness_summary(best)['title']=='复核情景参与搜索'
    assert '继承情景' in reports.render(best) and '不能再作为独立新点复核' in reports.render(best)
    banned={verification.fingerprint(s['parameters']) for job in (root,child) for s in job['samples']}
    sampler=verification.additional_samples
    def checked(*args):
        assert banned<=args[-1]
        return sampler(*args)
    monkeypatch.setattr(verification,'additional_samples',checked)
    again=check(worker,child,interior_samples=4)
    assert again['request']['verification']['training_job_ids']==[root['id'],child['id']]
    assert again['budget']==11 and again['best_id']
    assert not banned&{verification.fingerprint(s['parameters']) for s in again['samples'][7:]}
    retry=jobs.retry(child['id']);assert retry['request']['feedback']==meta
    worker.run_job(worker.claim());retried=jobs.get(retry['id'])
    assert [s['parameters'] for s in retried['samples']]==[s['parameters'] for s in child['samples']]


@pytest.mark.parametrize('status',['running','cancelled','interrupted'])
def test_incomplete_verification_cannot_continue(discovered,status):
    _,_,source=discovered
    with storage.connect() as db:db.execute('UPDATE optimization_jobs SET status=? WHERE id=?',(status,source['id']))
    with pytest.raises(storage.ConflictError):feedback.create(source['id'],feedback.FeedbackInput())


def test_guards_budget_records_and_api(discovered):
    from fastapi.testclient import TestClient
    from dcsj.api import app
    _,root,source=discovered
    with pytest.raises(storage.ConflictError):feedback.create(root['id'],feedback.FeedbackInput())
    with pytest.raises(storage.ConflictError,match='14'):feedback.create(source['id'],feedback.FeedbackInput(max_evaluations=13))
    client=TestClient(app);route=f"/api/v1/optimization/jobs/{source['id']}/resume-search"
    assert client.post(route,json={}).status_code==403
    client.headers['X-DCSJ-Request']='1';assert client.post(route,json={}).status_code==401
    assert client.post('/api/v1/auth/login',json={'username':'情景验收','password':'1234'}).status_code==200
    assert client.post(route,json={'plan':[]}).status_code==422
    response=client.post(route,json={});assert response.status_code==202 and response.json()['budget']==14
    with storage.connect() as db:db.execute('DELETE FROM optimization_samples WHERE job_id=? AND sample_index=6',(source['id'],))
    with pytest.raises(storage.ConflictError,match='不完整'):feedback.create(source['id'],feedback.FeedbackInput())


@pytest.mark.parametrize('mode',['cancel','lease','stop','material','kernel','wrapper','package'])
def test_partial_or_changed_resource_never_promotes_a_candidate(discovered,monkeypatch,mode):
    worker,_,source=discovered;child=feedback.create(source['id'],feedback.FeedbackInput());original=jobs.compute
    def changed(p,**kw):
        r=original(p,**kw)
        if mode=='cancel':jobs.cancel(child['id'])
        elif mode=='stop':worker.stop.set()
        elif mode=='lease':
            with storage.connect() as db:db.execute('UPDATE optimization_jobs SET owner=? WHERE id=?',('other',child['id']))
        else:r['provenance'][{'material':'material_sha256','kernel':'kernel_sha256','wrapper':'wrapper_sha256','package':'resource_package'}[mode]]='changed'
        return r
    monkeypatch.setattr(jobs,'compute',changed);worker.run_job(worker.claim());child=jobs.get(child['id'])
    assert not child['best_id'] and not child['candidates'][0]['feasible']
    assert child['evaluated']==(0 if mode=='lease' else 1)
    if mode in {'material','kernel','wrapper','package'}:assert child['status']=='interrupted'


def test_numerical_failure_is_retained_not_dropped(discovered,monkeypatch):
    worker,_,source=discovered
    # A fully attempted verification with calculation failures can still supply search scenarios.
    with storage.connect() as db:
        row=db.execute('SELECT record FROM optimization_samples WHERE job_id=? AND sample_index=3',(source['id'],)).fetchone()
        record=json.loads(row[0]);record.update(error='synthetic numerical failure',calculation_id=None)
        db.execute('UPDATE optimization_samples SET record=? WHERE job_id=? AND sample_index=3',(json.dumps(record),source['id']))
        db.execute("UPDATE optimization_jobs SET status='failed' WHERE id=?",(source['id'],))
    child=feedback.create(source['id'],feedback.FeedbackInput())
    assert len(child['request']['feedback']['plan'])==7
    assert child['request']['feedback']['plan'][3]['source_calculation_id'] is None
    worker.run_job(worker.claim());assert jobs.get(child['id'])['best_id']


@pytest.mark.parametrize('drift',[False,True])
def test_other_materials_have_distinct_but_stable_fingerprints(robust_worker,robust_body,monkeypatch,drift):
    from dcsj import optimization
    worker,_=robust_worker;original=jobs.compute
    monkeypatch.setattr(optimization,'allowed_material',lambda value:True)
    counts={}
    def compute(p,**kw):
        r=original(p,**kw);ref=p['Paihao'];counts[ref]=counts.get(ref,0)+1
        r['values']['ATA1']=.96 if ref=='A' else .94
        r['provenance']['material_sha256']='hash-'+ref
        if drift and active[0] and ref=='B' and counts[ref]>1:r['provenance']['material_sha256']='changed-B'
        return r
    active=[False];monkeypatch.setattr(jobs,'compute',compute)
    body={**robust_body,'parameters':{**robust_body['parameters'],'Paihao':'A'},
        'variables':[{'key':'Paihao','choices':['A','B']}]}
    source=check(worker,execute(worker,body),interior_samples=4)
    child=feedback.create(source['id'],feedback.FeedbackInput());active[0]=True;counts.clear()
    worker.run_job(worker.claim());child=jobs.get(child['id'])
    assert child['candidates'][0]['feasible']
    assert child['status']==('interrupted' if drift else 'succeeded')
    assert child['evaluated']==(9 if drift else 14)
    assert child['candidates'][1]['feasible'] is (not drift)


def test_excessive_retained_plan_is_rejected(robust_worker,robust_body):
    worker,_=robust_worker;source=execute(worker,robust_body)
    for _ in range(2):source=check(worker,source,interior_samples=32)
    assert source['budget']==67
    with pytest.raises(storage.ConflictError,match='128'):feedback.create(source['id'],feedback.FeedbackInput())
