"""Synthetic application contracts for slot search, not motor performance evidence."""
import copy
import pytest
from pydantic import ValidationError
from dcsj import jobs, optimization as opt, continuation, slot_search_geometry as geometry
from dcsj.storage import ConflictError


@pytest.fixture
def slot_parameters(parameters):
    return {**parameters,'b01':2.,'hs0':.5,'zs1':30.,'bs1':5.,'bs2':7.,'hs12':20.,
            'zc':2.,'bc02':1.,'hr0':.5,'zs2':20.,'br1':6.,'br2':2.,'hr12':16.}


@pytest.fixture
def slot_worker(tmp_path, monkeypatch, snapshot):
    database=tmp_path/'slots.sqlite';monkeypatch.setenv('DCSJ_PROJECTS_DB',str(database))
    monkeypatch.setattr(jobs,'engine_status',lambda:{'status':'recovered'})
    seen=[]
    resources={'kernel_sha256':'synthetic','wrapper_sha256':'synthetic','resource_package':None,
               'resource_version':None,'materials':{'SYNTHETIC':'synthetic'}}
    monkeypatch.setattr(continuation,'current_resources',lambda request:resources)
    def compute(p,**kwargs):
        seen.append(copy.deepcopy(p));result=copy.deepcopy(snapshot);result['inputs']=dict(p)
        result['values'].update(ATA1=.9+(p['br1']-6)*.001,Sf=.6,Sn=.03,Btru=16000+p['br1']*10,
                              Pcu=100.,Pal=80.,Pfe=70.,Pfw1=20.,Ps1=10.)
        result['provenance'].update(input_sha256=continuation.fingerprint(p),resource_package=None,resource_version=None)
        return result
    monkeypatch.setattr(jobs,'compute',compute)
    worker=jobs.Worker(database);worker.touch()
    return worker,seen


def request(p,**extra):
    return opt.SearchInput.model_validate({'name':'Synthetic slots','parameters':p,
        'variables':[{'key':'br1','values':[6.,8.,50.]}],'objective':'efficiency',
        'max_evaluations':8,'algorithm':'curvature_rescue_v1',**extra})


def run(worker,req):
    job=jobs.create(req,database=worker.database);assert worker.claim()==job['id']
    worker.run_job(job['id']);return jobs.get(job['id'],database=worker.database)


def test_geometry_failure_is_charged_but_not_computed(slot_parameters,slot_worker):
    worker,seen=slot_worker;job=run(worker,request(slot_parameters))
    assert job['evaluated']==3 and len(seen)==2
    bad=next(c for c in job['candidates'] if c['parameters']['br1']==50)
    assert bad['error'] and '相邻槽相交' in bad['error'] and not bad['feasible']
    assert bad['calculation_id'] is None
    for c in job['candidates']:
        if c['error'] is None:assert c['slot_geometry']==geometry.validate_candidate(request(slot_parameters),c['parameters'])
    state,summary,*_=continuation.state(continuation.read_history(job['id'],database=worker.database))
    assert summary['inherited_attempts']==3 and summary['successful_attempts']==2
    with pytest.raises(ConflictError,match='没有新的不重复候选'):
        continuation.create(job['id'],continuation.ExtensionInput(additional_evaluations=4),database=worker.database)


def test_slot_search_extension_preserves_geometry_and_failed_prefix(slot_parameters,slot_worker):
    worker,seen=slot_worker
    job=run(worker,request(slot_parameters,variables=[{'key':'br1','minimum':6.,'maximum':50.}],max_evaluations=4))
    count=len(seen)
    extended=continuation.create(job['id'],continuation.ExtensionInput(additional_evaluations=4),database=worker.database)
    assert worker.claim()==extended['id'];worker.run_job(extended['id'])
    finished=jobs.get(extended['id'],database=worker.database)
    assert finished['status']=='succeeded' and finished['evaluated']==8
    for old,new in zip(job['candidates'],finished['candidates'][:4]):
        assert (old['parameters'],old['error'],old['slot_geometry'])==(new['parameters'],new['error'],new['slot_geometry'])
    assert len(seen)-count==sum(c['calculation_id'] is not None for c in finished['candidates'][4:])


def test_slot_tolerance_failures_are_charged_and_kept(slot_parameters,slot_worker):
    worker,seen=slot_worker
    req=request(slot_parameters,variables=[{'key':'br1','values':[6.,8.]}],
                tolerances=[{'key':'br1','delta':50.}],max_evaluations=6)
    job=run(worker,req)
    assert job['evaluated']==6 and len(seen)==2 and job['best_id'] is None
    assert len(job['samples'])==6 and sum(bool(s.get('error')) for s in job['samples'])==4
    assert all(not c['feasible'] for c in job['candidates'])
    assert all(s['slot_geometry']['passed'] for s in job['samples'] if not s['error'])


def test_magnetic_bound_uses_real_metric_for_ranking(slot_parameters,slot_worker):
    worker,_=slot_worker
    job=run(worker,request(slot_parameters,constraints=[{'metric':'Btru','maximum':16070.}]))
    best=next(c for c in job['candidates'] if c['calculation_id']==job['best_id'])
    assert best['parameters']['br1']==6
    higher=next(c for c in job['candidates'] if c['parameters']['br1']==8)
    assert not higher['feasible'] and higher['constraint_values']['Btru']==16080.


def test_stator_tolerance_cannot_switch_slot_bottom_shape(slot_parameters,slot_worker):
    worker,seen=slot_worker
    req=request(slot_parameters,variables=[{'key':'br1','values':[6.,8.]}],
                tolerances=[{'key':'bs2','delta':8.}],max_evaluations=6)
    job=run(worker,req)
    assert job['evaluated']==6 and len(seen)==4 and job['best_id'] is None
    negative=[s for s in job['samples'] if s['parameters']['bs2']<0]
    assert len(negative)==2
    assert all('槽底形状' in s['error'] and s['calculation_id'] is None for s in negative)
    assert all(p['bs2']>0 for p in seen)


def test_new_features_cannot_masquerade_as_legacy_solver(slot_parameters):
    old='f5aabb02e0b09821d06ba8920e53a4813f72f7408f961b1f6a72cfac4c9e63f2'
    assert not opt.compatible_solver(request(slot_parameters),old)
    legacy=request(slot_parameters,variables=[{'key':'L','values':[160.,170.]}])
    assert opt.compatible_solver(legacy,old)
    assert not opt.compatible_solver(legacy.model_copy(update={'constraints':[opt.Constraint(metric='Btru',maximum=18000)]}),old)


def test_http_config_exposes_dimensions_and_magnetic_bounds(client):
    response=client.get('/api/v1/optimization/config');assert response.status_code==200
    data=response.json()
    assert set(geometry.VARIABLES)<={v['key'] for v in data['variables']}
    assert set(geometry.VARIABLES)<=set(data['tolerances']['keys'])
    assert set(opt.MAGNETIC_CONSTRAINTS)<=set(data['constraint_metrics'])


def test_reports_use_saved_geometry_and_constraints(slot_parameters,slot_worker,monkeypatch):
    from io import BytesIO
    from pypdf import PdfReader
    from dcsj import calculations,reports,pdf_report,report_summary,legacy_report
    worker,_=slot_worker
    req=request(slot_parameters,variables=[{'key':'br1','values':[6.,8.]}],
                tolerances=[{'key':'br1','delta':.1}],max_evaluations=6,
                constraints=[{'metric':'Btru','maximum':16100.}])
    job=run(worker,req);saved=calculations.get(job['best_id'])
    before=copy.deepcopy(saved['optimization']['slot_geometry'])
    monkeypatch.setattr(geometry,'validate_candidate',lambda *a:pytest.fail('Reports must read saved geometry'))
    summary=report_summary.slot_search_summary(saved)
    assert summary['title']=='名义尺寸 · 槽型几何校核'
    assert [r[1] for r in summary['sampled_rows']]==['3/3','3/3']
    bounds=report_summary.search_constraints_summary(saved)
    assert bounds['rows'][0][0]=='Btru 转子上齿磁密 G'
    assert bounds['rows'][0][-1]=='通过'
    html=reports.render(saved)
    pdf='\n'.join(p.extract_text() for p in PdfReader(BytesIO(pdf_report.render(saved))).pages)
    legacy_html=legacy_report.render_html(saved)
    legacy_pdf='\n'.join(p.extract_text() for p in PdfReader(BytesIO(legacy_report.render_pdf(saved))).pages)
    for text in (html,pdf,legacy_html,legacy_pdf):
        assert '槽型几何校核' in text and '径向余量' in text and '寻优约束校核' in text
        assert '16100' in text and 'Btru' in text
    # Appendices must keep numerical fields intact instead of double wrapping
    # at incompatible Chinese/ASCII widths.
    for row in summary['rows']:
        for value in row[1:5]:
            assert value in legacy_pdf
    for value in bounds['rows'][0][1:4]:
        assert value in legacy_pdf
    assert saved['optimization']['slot_geometry']==before


@pytest.mark.parametrize('change',[{'zc':4.},{'bs2':-7.},{'D1':181.}])
def test_unsupported_or_invalid_geometry_rejected_before_job(slot_parameters,change):
    with pytest.raises(ValidationError):
        request({**slot_parameters,**change},variables=[{'key':'bs2','values':[6.,7.]}])
