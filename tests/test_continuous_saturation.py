"""Only synthetic tables; original resources are not needed by these tests."""
from copy import deepcopy
import math
import pytest
from dcsj.continuous_saturation import MonotoneCubic, Saturation, NOTE
from dcsj.numeric_vm import NumericKernelError
from dcsj import optimization, torque_refinement as tr, robustness, report_summary, jobs, calculations


def test_shape_preservation_knots_and_continuous_derivatives():
    x = [0., .2, .9, 2., 4.]
    for y in ([3., 2., 1., .8, .7], [0., 1., 1., 2., 3.], [0., 2., -1., 4., 0.]):
        f = MonotoneCubic(x, y)
        assert [f(q) for q in x] == y
        for i in range(len(x)-1):
            values = [f(x[i]+(x[i+1]-x[i])*j/100) for j in range(101)]
            assert min(values) >= min(y[i:i+2])-1e-14
            assert max(values) <= max(y[i:i+2])+1e-14
        for q in x[1:-1]:
            h = 1e-7
            assert (f(q)-f(q-h))/h == pytest.approx((f(q+h)-f(q))/h, abs=2e-5)
    assert MonotoneCubic([1., 3.], [2., 6.])(2.) == 4.


@pytest.mark.parametrize('x,y', [([], []), ([1], [1]), ([0, 1], [1]), ([0, 0], [1, 2]),
                                  ([1, 0], [1, 2]), ([0, math.nan], [1, 2]), ([0, 1], [1, math.inf]),
                                  ([False, 1], [1, 2])])
def test_invalid_tables_are_rejected(x, y):
    with pytest.raises(NumericKernelError): MonotoneCubic(x, y)


def test_no_extrapolation_and_runtime_anchor_joins_original_analytic_branch():
    class Memory:
        def __init__(self):
            self.values = {0x4915a4: .1, 0x49159c: 1.}
            self.values.update({0x4ab4e4+4*i: .2+i*.2 for i in range(7)})
            self.values.update({0x4ab4c8+4*i: .9-i*.1 for i in range(7)})
        def f32(self, key): return self.values[key]
        def set_float(self, key, value): self.values[key] = value
    m = Memory();s = Saturation()
    m.values.update({0x4ab4bc: .0999, 0x4af548: 99.})
    assert s.apply(m) == -1 and m.f32(0x4af548) == 99.  # Original analytic branch owns low region.
    m.values[0x4ab4bc] = .1;s.apply(m);assert m.f32(0x4af548) == 1.
    for q in (-.1, 1.5, math.nan):
        m.values[0x4ab4bc] = q
        with pytest.raises(NumericKernelError): s.apply(m)
    for q in (-1., 4., math.inf):
        with pytest.raises(NumericKernelError): MonotoneCubic([0, 1], [1, 0])(q)


def request(parameters, **extra):
    return optimization.SearchInput.model_validate({'parameters':parameters,
        'variables':[{'key':'L','values':[160.,170.]}], 'max_evaluations':6,
        'constraints':[{'metric':'TM_continuous','minimum':3.}], **extra})


def test_explicit_metric_options_no_fallback_and_failed_tolerance_aggregation(parameters, snapshot):
    r = request(parameters, tolerances=[{'key':'L','delta':1}])
    assert r.parameters['_curve'] == 1 and tr.options(r) == {'continuous_torque':True}
    both = request(parameters, constraints=[{'metric':k,'minimum':3} for k in ('TM_refined','TM_continuous')])
    assert tr.options(both) == {'continuous_torque':True,'refine_torque':True}
    snapshot['curve_metrics'] = {'refined_torque_peak':{'status':'available','value':8.},
                                'continuous_torque_peak':{'status':'unavailable','value':9.,'reason':'表外不外推'}}
    snapshot['values'].update(Pcu=1,Pal=1,Pfe=1,Pfw1=1,Ps1=1)
    values = optimization.metrics(snapshot)
    assert values['TM_refined'] == 8 and values['TM_continuous'] is None
    scenario = robustness.scenarios(r, r.parameters)[0]
    sample = robustness.measured(r, scenario, snapshot)
    assert '表外' in sample['error']
    aggregate, _, _ = robustness.summarize(r, r.parameters, [sample]*3)
    assert aggregate['status']=='failed' and aggregate['objectives']['efficiency'] is None


@pytest.mark.parametrize('tolerances', [None, [{'key':'L','delta':1}]])
def test_worker_snapshots_both_models_independently_and_excludes_failed_model(tmp_path, monkeypatch, parameters, snapshot, tolerances):
    path=tmp_path/'jobs.sqlite';monkeypatch.setenv('DCSJ_PROJECTS_DB',str(path))
    monkeypatch.setattr(jobs,'engine_status',lambda:{'status':'recovered'})
    def compute(p, **kwargs):
        assert kwargs['continuous_torque'] and kwargs['refine_torque']
        r=deepcopy(snapshot);r['inputs']=dict(p)
        r['values'].update(ATA1=.91 if p['L']>165 else .9,Sf=.6,Sn=.03,Pcu=1,Pal=1,Pfe=1,Pfw1=1,Ps1=1)
        r['curve_metrics']={key:{'status':'unavailable' if p['L']>165 and key.startswith('continuous') else 'available',
            'value':3.5 if key.startswith('refined') else 3.4, 'slip':.3,'samples':[], 'sample_count':70, 'note':NOTE}
            for key in ('refined_torque_peak','continuous_torque_peak')}
        return r
    monkeypatch.setattr(jobs,'compute',compute)
    r=request(parameters, tolerances=tolerances, constraints=[{'metric':k,'minimum':3} for k in ('TM_refined','TM_continuous')])
    job=jobs.create(r);worker=jobs.Worker(path);worker.touch();worker.run_job(worker.claim());result=jobs.get(job['id'])
    assert result['feasible_count']==1
    best=calculations.get(result['best_id'])
    assert best['inputs']['L']==160 and len(best['curve_metrics'])==2
    assert result['candidates'][0]['continuous_torque_peak']['value']==3.4
    assert result['candidates'][0]['refined_torque_peak']['value']==3.5
    assert result['candidates'][1]['feasible'] is False
    if tolerances:
        failed=result['candidates'][1]['robustness']
        assert failed['status']=='failed' and failed['objectives']['efficiency'] is None
        assert failed['metric_ranges']['TM_refined']['complete'] is True
        assert failed['metric_ranges']['TM_continuous']['complete'] is False
    assert 'samples' not in result['candidates'][0]['continuous_torque_peak']
    labels=[row[0] for row in report_summary.targets(best)]
    assert '加密转矩峰值' in labels and '连续饱和插值峰值（试验）' in labels


def test_dual_model_reports_keep_disclosure_and_second_state_residual(snapshot):
    from dcsj import reports, pdf_report
    from pypdf import PdfReader
    from io import BytesIO
    snapshot.update(calculation_id='synthetic',name='双模型合成试验',created_at='2020-01-01T00:00:00Z')
    row={'slip':.32,'torque':3.31,'current':5.,'iterations':8,'residual':1e-6,'factor_residual':2e-7}
    peak={'status':'available','value':3.31,'slip':.32,'samples':[row],'note':NOTE}
    snapshot['curve_metrics']={'refined_torque_peak':{**peak,'value':3.30},'continuous_torque_peak':peak}
    html=reports.render(snapshot)
    assert '连续饱和插值峰值（试验）校核' in html and '第二状态差' in html
    assert '加密转矩峰值校核' in html and '插值规则已改变' in html
    pages=PdfReader(BytesIO(pdf_report.render(snapshot))).pages
    assert '运行与电磁指标' in pages[0].extract_text()
    text='\n'.join(page.extract_text() for page in pages)
    assert '第二状态差' in text and '连续饱和插值' in text and '加密转矩峰值校核' in text


def coupled_checkpoint(update_factor=True):
    """Small actual VM loop: current converges first, the other state one step later."""
    from dcsj.numeric_vm import _VM
    mem=lambda address:['m',None,None,1,address,4]
    imm=lambda value:['i',value,4]
    program={'image_base':0x400000,'image_size':0x100000,'stack_base':0x600000,'stack_size':0x10000,
             'initial_ebp':0x608000,'entry':tr.CHECKPOINT,'initial_segments':[],'function_names':{},'code':{
        tr.CHECKPOINT:(tr.COMPARISON,'fld',[mem(0x4a0000)]),
        tr.COMPARISON:(0x401907,'fcomp',[mem(tr.TOLERANCE_ADDRESS)]),
        0x401907:(0x40190c,'fnstsw',[['r','ax',2]]),
        0x40190c:(0x401911,'and',[['r','ah',1],imm(65)]),
        0x401911:(0x401913,'jnp',[imm(tr.POINT_COMPLETE)]),
        0x401913:(0x401920,'mov',[mem(0x4aa164),imm(0x3f800000 if update_factor else 0x3f000000)]),
        0x401920:(0x401926,'add',[mem(0x4aa14c),imm(1)]),
        0x401926:(tr.CHECKPOINT,'jmp',[imm(tr.CHECKPOINT)]),
    }}
    vm=_VM(program,{0x4a0000:1e-6,0x4a0004:3.,0x4aa134:1.,0x4aa13c:0.,0x4aa138:1.,
                   0x4aa164:.5,0x4aa148:7.,0x4aa154:7.,tr.TOLERANCE_ADDRESS:.01})
    vm.memory.set_uint(0x4aa150,1);vm.set_reg('edi',0x4a0008);vm.set_reg('ebx',0x4a0004)
    return vm


def test_continuous_loop_waits_for_both_states_without_changing_original_rule():
    seed=coupled_checkpoint();original=tr.Sampler(seed)(.1);continuous=tr.Sampler(seed,continuous=True)(.1)
    assert original['iterations']==0
    assert continuous['iterations']==1 and continuous['factor_residual']==0
    assert continuous['current']==original['current']==7
    assert continuous['torque']==original['torque']==3
    assert continuous['residual']<tr.TOLERANCE
    assert seed.memory.f32(0x4aa164)==.5 and seed.memory.uint(0x4aa14c)==0
    assert seed.memory.f32(tr.TOLERANCE_ADDRESS)==tr.f32(.01)


def test_second_state_failure_keeps_budget_and_diagnostic_even_if_current_is_converged(monkeypatch):
    monkeypatch.setattr(tr,'MAX_POINT_STEPS',45)
    with pytest.raises(tr.SamplingFailure) as caught:tr.Sampler(coupled_checkpoint(False),continuous=True)(.1)
    d=caught.value.diagnostic
    assert d['category']=='instruction_budget' and d['steps']==45
    assert d['residual']<tr.TOLERANCE and d['factor_residual']==.5
    assert '末次第二状态差 0.5' in d['message']
    assert d['history'][-1]['factor_residual']==.5
