"""Synthetic tolerance cases prove ranking/accounting, not motor performance."""
import copy
import json
import pytest
from pydantic import ValidationError
from dcsj import robustness, optimization, jobs, calculations, storage


@pytest.fixture
def robust_body(parameters):
    return {'parameters':{**parameters,'hr0':.5}, 'variables':[{'key':'L','values':[160,170]}],
            'objective':'efficiency','tolerances':[{'key':'g','delta':.05}], 'max_evaluations':8}


@pytest.fixture
def robust_worker(tmp_path,monkeypatch,snapshot):
    monkeypatch.setenv('DCSJ_PROJECTS_DB',str(tmp_path/'robust.sqlite'))
    monkeypatch.setattr(jobs,'engine_status',lambda:{'status':'recovered'})
    calls=[]
    def compute(p,**kwargs):
        calls.append(dict(p));r=copy.deepcopy(snapshot);r['inputs']=dict(p)
        slope=2 if p['L']==160 else .1
        r['values'].update(ATA1=.95-(p['L']-160)*.0005+slope*(p['g']-.6),
                           PF1=.8,Sf=.6,Sn=.03,CostFx=p['L'],Pcu=100,Pal=80,Pfe=70,Pfw1=20,Ps1=10)
        return r
    monkeypatch.setattr(jobs,'compute',compute)
    worker=jobs.Worker();worker.touch()
    return worker,calls


def execute(worker,body):
    job=jobs.create(optimization.SearchInput.model_validate(body));worker.run_job(worker.claim())
    return jobs.get(job['id'])


@pytest.mark.parametrize('algorithm',['stratified_v1','adaptive_poll_v1','hybrid_de_v1'])
def test_robust_ranking_uses_worst_samples_and_counts_every_attempt(robust_worker,robust_body,algorithm):
    worker,calls=robust_worker
    job=execute(worker,{**robust_body,'algorithm':algorithm})
    assert job['evaluated']==len(calls)==6 and job['status']=='succeeded'
    assert len(job['samples'])==6 and len(job['candidates'])==2
    best=calculations.get(job['best_id'])
    assert best['inputs']['L']==170
    assert best['values']['ATA1']==pytest.approx(.945)  # Nominal output is never overwritten.
    assert best['optimization']['objectives']['efficiency']==pytest.approx(.94)
    assert best['optimization']['robustness']['worst_samples']['efficiency']==1
    assert all(c['parameters']['hr0']==.5 for c in job['candidates'])
    for sample in job['samples']:
        saved=calculations.get(sample['calculation_id'])
        assert sample['values']['ATA1']==saved['values']['ATA1']
        assert saved['inputs']==sample['parameters']
        with pytest.raises(storage.ConflictError):calculations.delete(sample['calculation_id'])


def test_center_and_all_corners_and_gap_mode(robust_body):
    req=optimization.SearchInput.model_validate({**robust_body,'max_evaluations':18,
        'tolerances':[{'key':'g','delta':.05},{'key':'L','delta':1},{'key':'Djj','delta':.01}],
        'tolerance_gap_mode':'fixed_neck_bottom'})
    scenarios=robustness.scenarios(req,req.parameters)
    assert len(scenarios)==9 and scenarios[0]['parameters']==req.parameters
    assert len({tuple(s['offsets'].values()) for s in scenarios[1:]})==8
    for s in scenarios:
        p=s['parameters'];assert p['g']+p['hr0']==pytest.approx(1.1)
    assert req.parameters['hr0']==.5


@pytest.mark.parametrize('extra',[{'tolerances':[]},{'tolerances':[{'key':'a','delta':1}]},
    {'tolerances':[{'key':'g','delta':0}]},{'tolerances':[{'key':'g','delta':True}]},
    {'tolerances':[{'key':'g','delta':float('nan')}]}, {'tolerances':[{'key':'g','delta':.1}]*2},
    {'max_evaluations':5},{'tolerance_gap_mode':'unknown'},
    {'tolerances':[{'key':'L','delta':1}],'tolerance_gap_mode':'fixed_neck_bottom'}])
def test_invalid_tolerance_requests_fail_before_execution(robust_body,extra):
    with pytest.raises(ValidationError):optimization.SearchInput.model_validate({**robust_body,**extra})


def test_constraints_require_every_sample_not_average(robust_worker,robust_body):
    worker,calls=robust_worker
    job=execute(worker,{**robust_body,'constraints':[{'metric':'ATA1','minimum':.9,'maximum':.96}]})
    base,best=job['candidates'];assert not base['feasible'] and best['feasible']
    assert base['robustness']['metric_ranges']['ATA1']['minimum']==pytest.approx(.85)
    assert base['robustness']['metric_ranges']['ATA1']['maximum']==pytest.approx(1.05)
    assert any('g-0.05' in v for v in base['violations'])


def test_failed_and_cancelled_samples_never_promote_partial_groups(robust_worker,robust_body,monkeypatch):
    worker,calls=robust_worker;original=jobs.compute
    def cancel(p,**kwargs):
        result=original(p,**kwargs)
        with storage.connect() as db:identity=db.execute("SELECT id FROM optimization_jobs WHERE status='running'").fetchone()[0]
        jobs.cancel(identity);return result
    monkeypatch.setattr(jobs,'compute',cancel)
    job=execute(worker,robust_body)
    assert job['status']=='cancelled' and job['evaluated']==1 and job['best_id'] is None
    assert len(job['samples'])==1 and job['candidates'][0]['robustness']['status']=='incomplete'
    assert job['candidates'][0]['objective'] is None


def test_failed_corner_is_counted_and_preserves_other_snapshots(robust_worker,robust_body,monkeypatch):
    worker,calls=robust_worker;original=jobs.compute
    def fail(p,**kwargs):
        if p['g']<.6:raise jobs.CalculationError('synthetic corner failure')
        return original(p,**kwargs)
    monkeypatch.setattr(jobs,'compute',fail)
    job=execute(worker,robust_body)
    assert job['evaluated']==6 and job['best_id'] is None and job['status']=='failed'
    assert len([s for s in job['samples'] if s['error']])==2
    assert len([s for s in job['samples'] if s['calculation_id']])==4
    assert all(c['robustness']['status']=='failed' and c['objective'] is None for c in job['candidates'])


def test_resource_drift_interrupts_and_does_not_mix_assessments(robust_worker,robust_body,monkeypatch):
    worker,calls=robust_worker;original=jobs.compute
    def drift(p,**kwargs):
        r=original(p,**kwargs);r['provenance']['resource_package']='a' if len(calls)==1 else 'b';return r
    monkeypatch.setattr(jobs,'compute',drift)
    job=execute(worker,robust_body)
    assert job['status']=='interrupted' and job['evaluated']==2 and job['best_id'] is None
    assert job['samples'][-1]['error'] and job['samples'][-1]['calculation_id'] is None


def test_negative_linked_neck_is_rejected_before_engine(robust_worker,robust_body):
    worker,calls=robust_worker
    job=execute(worker,{**robust_body,'parameters':{**robust_body['parameters'],'hr0':.01},
        'tolerance_gap_mode':'fixed_neck_bottom'})
    assert job['evaluated']==6 and len(calls)==4 and not job['best_id']
    assert all('hr0' in s['error'] for s in job['samples'] if s['error'])


def test_nominal_failure_retains_corner_evidence_without_false_snapshot(robust_worker,robust_body,monkeypatch):
    worker,_=robust_worker;original=jobs.compute
    def fail(p,**kwargs):
        if p['g']==.6:raise jobs.CalculationError('nominal failed')
        return original(p,**kwargs)
    monkeypatch.setattr(jobs,'compute',fail)
    job=execute(worker,robust_body)
    assert job['evaluated']==6 and job['status']=='failed' and not job['best_id']
    assert all(c['calculation_id'] is None and c['robustness']['status']=='failed' for c in job['candidates'])
    assert sum(bool(s['calculation_id']) for s in job['samples'])==4
    frozen=copy.deepcopy(job)
    monkeypatch.setattr(robustness,'summarize',lambda *a,**kw:pytest.fail('Historical jobs must not recompute'))
    assert jobs.get(job['id'])==frozen
    next_job=jobs.retry(job['id'])
    assert next_job['id']!=job['id'] and next_job['request']['tolerances']==job['request']['tolerances']
    assert next_job['samples']==[]


def test_crash_recovery_preserves_completed_samples_but_excludes_partial_group(robust_worker,robust_body,monkeypatch):
    worker,calls=robust_worker;original=jobs.compute
    def crash(p,**kwargs):
        if calls:raise RuntimeError('simulated process death')
        return original(p,**kwargs)
    monkeypatch.setattr(jobs,'compute',crash)
    job=jobs.create(optimization.SearchInput.model_validate(robust_body));identity=worker.claim()
    with pytest.raises(RuntimeError):worker.run_job(identity)
    with storage.connect() as db:db.execute('UPDATE optimization_workers SET heartbeat=0 WHERE id=?',(worker.owner,))
    replacement=jobs.Worker();replacement.touch();assert replacement.claim() is None
    recovered=jobs.get(job['id'])
    assert recovered['status']=='interrupted' and recovered['evaluated']==1 and len(recovered['samples'])==1
    assert recovered['best_id'] is None and not recovered['candidates'][0]['feasible']
    assert recovered['samples'][0]['calculation_id']


def test_shutdown_preserves_samples(robust_worker,robust_body,monkeypatch):
    worker,_=robust_worker;original=jobs.compute
    def stop(p,**kwargs):
        r=original(p,**kwargs);worker.stop.set();return r
    monkeypatch.setattr(jobs,'compute',stop)
    job=execute(worker,robust_body)
    assert job['status']=='interrupted' and job['evaluated']==1 and not job['best_id']
    assert job['candidates'][0]['robustness']['status']=='incomplete'


def test_objectives_can_have_different_worst_samples_and_two_sided_constraints(robust_body,snapshot):
    req=optimization.SearchInput.model_validate({**robust_body,'secondary_objective':'material_cost',
        'constraints':[{'metric':'PF1','minimum':.7,'maximum':.9}]})
    samples=[]
    for i,scenario in enumerate(robustness.scenarios(req,req.parameters)):
        result=copy.deepcopy(snapshot)
        result['values'].update(ATA1=[.9,.8,.85][i],CostFx=[100,90,110][i],PF1=[.8,.6,.95][i],Sf=.6,Sn=.03,Pcu=100,Pal=80,Pfe=70,Pfw1=20,Ps1=10)
        samples.append(robustness.measured(req,scenario,result))
    record,values,score=robustness.summarize(req,req.parameters,samples)
    assert record['worst_samples']=={'efficiency':1,'material_cost':2}
    assert values['ATA1']==.8 and values['CostFx']==110
    assert not record['feasible'] and len(record['violations'])==2 and score>0
    assert record['metric_ranges']['PF1']=={'minimum':.6,'maximum':.95,'complete':True}


def test_lost_lease_cannot_publish_a_sample_after_compute(robust_worker,robust_body,monkeypatch):
    worker,_=robust_worker;original=jobs.compute
    def lose(p,**kwargs):
        r=original(p,**kwargs)
        with storage.connect() as db:db.execute("UPDATE optimization_jobs SET owner='replacement' WHERE status='running'")
        return r
    monkeypatch.setattr(jobs,'compute',lose)
    job=execute(worker,robust_body)
    assert job['status']=='running' and job['evaluated']==0 and job['samples']==[] and job['best_id'] is None
    with storage.connect() as db:assert db.execute('SELECT count(*) FROM calculations').fetchone()[0]==0


def test_saved_tolerance_report_preserves_nominal_and_sample_results(robust_worker,robust_body,monkeypatch):
    from io import BytesIO
    from pypdf import PdfReader
    from dcsj import reports,pdf_report,report_summary
    worker,_=robust_worker;job=execute(worker,robust_body);saved=calculations.get(job['best_id'])
    monkeypatch.setattr(robustness,'summarize',lambda *a,**kw:pytest.fail('Reports must not rerun assessment'))
    summary=report_summary.robustness_summary(saved)
    assert summary['objectives'][0][1]=='0.94'
    assert saved['values']['ATA1']==pytest.approx(.945)
    html=reports.render(saved);reader=PdfReader(BytesIO(pdf_report.render(saved)))
    text='\n'.join(p.extract_text() for p in reader.pages)
    for output in (html,text):
        assert '公差情景评估' in output and '最不利采样值' in output and '0.94' in output
        assert '不保证角点之间没有更差值' in output
        assert 'g=0.55' in output and 'g=0.65' in output
    assert '公差情景评估' in reader.pages[1].extract_text()
