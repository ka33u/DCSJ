"""Numerical refinement must never substitute display values or partial sweeps."""
from copy import deepcopy
import math
import pytest
from dcsj import torque_refinement as tr, optimization, jobs, calculations, report_summary
from dcsj.numeric_vm import NumericKernelError
from test_torque_constraints import sweep


def sample(function):
    return lambda x: {'slip':x,'torque':function(x),'current':2.,'iterations':5,'residual':1e-7,'steps':10}


def test_refines_multiple_observed_peaks_and_retains_exact_sample_value():
    fn=lambda x:max(3-(x-.237)**2*20,4-(x-.743)**2*50)
    r=tr.refine(sample(fn),[.01]+[i/50 for i in range(1,51)])
    assert r['status']=='available' and len(r['brackets'])==2
    assert r['value']==r['peak_sample']['torque']==fn(r['slip'])
    assert r['slip']==pytest.approx(.743,abs=.0002)
    assert len(r['samples'])<=tr.MAX_SAMPLES
    assert all(b['final'][1]-b['final'][0]<=.0002 for b in r['brackets'])
    assert len({r['slip'] for r in r['samples']})==len(r['samples'])


def test_boundary_peaks_and_constant_curve_are_bounded():
    for fn,expected in [(lambda x:x,1),(lambda x:1-x,tr.f32(.01)),(lambda x:2,None)]:
        r=tr.refine(sample(fn),[.01]+[i/50 for i in range(1,51)])
        assert r['status']=='available'
        if expected is not None:assert r['slip']==expected
        assert all(tr.f32(.01)<=s['slip']<=1 for s in r['samples'])


def test_failed_or_budget_limited_refinement_never_returns_a_partial_peak(monkeypatch):
    def fail(x):
        if x>.5:raise NumericKernelError('not converged')
        return sample(lambda x:x)(x)
    r=tr.refine(fail,[.01,.5,1]);assert r['status']=='unavailable' and r['value'] is None
    assert len(r['samples'])==2
    monkeypatch.setattr(tr,'MAX_SAMPLES',2)
    r=tr.refine(sample(lambda x:x),[.01,.5,1]);assert r['status']=='unavailable' and '预算' in r['reason']


def test_kernel_and_complete_original_sweep_are_required():
    original=sweep();saved=deepcopy(original)
    assert '数值核' in tr.evaluate(None,original,'unknown')['reason']
    original[0]['series'][1]['points'].pop()
    assert '不完整' in tr.evaluate(None,original,tr.KERNEL_SHA256)['reason']
    assert '首点状态' in tr.evaluate(None,saved,tr.KERNEL_SHA256)['reason']


def test_refined_constraint_is_explicit_and_unavailable_does_not_fall_back(parameters,snapshot):
    body={'parameters':parameters,'variables':[{'key':'L','values':[160.,170.]}]}
    legacy=optimization.SearchInput.model_validate(body)
    request=optimization.SearchInput.model_validate({**body,'constraints':[{'metric':'TM_refined','minimum':3.}]})
    assert legacy.parameters['_curve']==0 and request.parameters['_curve']==1
    snapshot['values'].update(TM1=99,Pcu=1,Pal=1,Pfe=1,Pfw1=1,Ps1=1)
    snapshot['curves']=sweep(5)
    snapshot['curve_metrics']={'refined_torque_peak':{'status':'unavailable','value':9}}
    v=optimization.metrics(snapshot)
    assert v['TM_curve']==5 and v['TM_refined'] is None
    assert 'TM_refined 无有效结果' in optimization.assess(request,v)


@pytest.mark.parametrize('tolerances',[None,[{'key':'L','delta':1}]])
def test_worker_requests_refinement_per_sample_and_keeps_failed_candidate_out(tmp_path,monkeypatch,parameters,snapshot,tolerances):
    path=tmp_path/'jobs.sqlite';monkeypatch.setenv('DCSJ_PROJECTS_DB',str(path))
    monkeypatch.setattr(jobs,'engine_status',lambda:{'status':'recovered'})
    calls=[]
    def compute(p,**kwargs):
        assert kwargs['refine_torque'] is True;calls.append(p['L'])
        r=deepcopy(snapshot);r['inputs']=dict(p);r['curves']=sweep(4)
        r['values'].update(ATA1=.91 if p['L']>165 else .9,Sf=.6,Sn=.03,Pcu=1,Pal=1,Pfe=1,Pfw1=1,Ps1=1)
        failed=p['L']>165
        r['curve_metrics']={'refined_torque_peak':{'status':'unavailable' if failed else 'available','value':None if failed else 3.2,
            'slip':None if failed else .32,'samples':[],'reason':'未收敛' if failed else '', 'sample_count':70}}
        return r
    monkeypatch.setattr(jobs,'compute',compute)
    request=optimization.SearchInput.model_validate({'parameters':parameters,'variables':[{'key':'L','values':[160.,170.]}],
        'constraints':[{'metric':'TM_refined','minimum':3.}],'tolerances':tolerances,'max_evaluations':6})
    job=jobs.create(request);worker=jobs.Worker(path);worker.touch();worker.run_job(worker.claim());result=jobs.get(job['id'])
    assert result['feasible_count']==1
    assert len(calls)==(6 if tolerances else 2)
    best=calculations.get(result['best_id']);assert best['inputs']['L']==160
    assert best['curve_metrics']['refined_torque_peak']['value']==3.2
    assert result['candidates'][1]['feasible'] is False
    assert next(r for r in report_summary.targets(best) if r[0]=='加密转矩峰值')[1:4]==('≥3','3.2','s=0.32')
    assert report_summary.torque_summary(best)['rows']==[]
    monkeypatch.setattr(jobs,'compute',lambda *a,**k:pytest.fail('No recomputation on reopening'))
    assert jobs.get(job['id'])==result


def test_vm_checkpoint_is_isolated_and_normal_execution_is_identical(monkeypatch):
    from dcsj import numeric_vm as vm
    program={'image_base':0,'image_size':1024,'stack_base':4096,'stack_size':4096,'initial_segments':[],
             'initial_ebp':6000,'entry':1,'function_names':{},'code':{
                 1:(2,'mov',[['m',None,None,1,100,4],['i',0x40400000,4]]),
                 2:(3,'jmp',[['i',0xffff000,4]])}}
    monkeypatch.setattr(vm,'_program',lambda path:program)
    normal=vm.run({100:2.});captured=vm.run({100:2.},capture_at=1)
    assert normal['outputs']==captured['outputs'] and normal['steps']==captured['steps']
    assert normal['memory'].regions==captured['memory'].regions
    checkpoint=captured['checkpoint'];assert checkpoint.memory.f32(100)==2
    assert checkpoint.program is program
    checkpoint.memory.set_float(100,9)
    assert captured['memory'].f32(100)==3 and normal['memory'].f32(100)==3
    assert 'checkpoint' not in normal
    with pytest.raises(vm.NumericKernelError,match='Checkpoint'):vm.run({},capture_at=9)


def test_html_and_pdf_keep_refined_samples_and_original_formula_separate(snapshot):
    from dcsj import reports,pdf_report
    from pypdf import PdfReader
    from io import BytesIO
    snapshot['calculation_id']='synthetic-refined';snapshot['name']='合成峰值验收';snapshot['created_at']='2020-01-01T00:00:00Z'
    snapshot['values']['TM1']=2.7
    snapshot['curve_metrics']={'refined_torque_peak':{'status':'available','value':3.32158,'slip':.3238,
        'delta_from_legacy':-.0011,'note':'有限采样不保证连续全局峰值。','samples':[
            {'slip':.3238,'torque':3.32158,'current':4.976,'iterations':7,'residual':1e-6}]}}
    original=deepcopy(snapshot)
    html=reports.render(snapshot)
    text='\n'.join(p.extract_text() for p in PdfReader(BytesIO(pdf_report.render(snapshot))).pages)
    for content in (html,text):
        assert '加密转矩峰值校核' in content and '3.32158' in content and '2.7' in content
        assert '末次电流倍数差' in content and '0.3238' in content
    assert snapshot==original
