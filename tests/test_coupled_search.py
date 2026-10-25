"""Synthetic neighbor interactions; these are not motor performance claims."""
import copy

import pytest

from dcsj import optimization as opt


@pytest.fixture
def search_request(parameters):
    return opt.SearchInput.model_validate({'parameters':parameters,'algorithm':'coupled_poll_v1',
        'objective':'material_cost','constraints':[{'metric':'PF1','minimum':.8}],
        'variables':[{'key':'L','minimum':140,'maximum':180},
                     {'key':'ZZ','minimum':12,'maximum':20,'step':2},
                     {'key':'g','minimum':.4,'maximum':.8}], 'max_evaluations':48,'seed':11})


def values(p, scale=1):
    x=(p['L']-160)/10; z=(p['ZZ']-16)/2
    return {'ATA1':.9,'PF1':.8+.01*(x+z),'CostFx':scale*(100+10*x+8*z),'Sf':.6,'Sn':.03}


def measured_axes(search_request, missing=False):
    search=opt.Search(search_request)
    p=search.candidate();v=values(p);search.consider(p,v,not opt.assess(search_request,v))
    search._new_poll()
    for changes in ({'L':150},{'ZZ':18}):
        p=search._remember({**search_request.parameters,**changes});v=values(p)
        if missing and 'L' in changes:v.pop('PF1')
        search.consider(p,v,not opt.assess(search_request,v))
    search.poll_queue.clear()
    return search


def test_screening_crosses_a_single_variable_constraint_trap_without_inventing_results(search_request):
    search=measured_axes(search_request);before=len(search.records);search._new_poll();p=search.candidate()
    assert p['L']==150 and p['ZZ']==18
    assert len(search.records)==before and search.enrichments
    # Deliberately falsify the additive estimate with a costly measured result.
    v=values(p);v['CostFx']=200;search.consider(p,v,True)
    assert search.best_score==-100
    assert search.records[-1]['objectives']['material_cost']==200


def test_missing_constraint_measurement_does_not_create_an_optimistic_proposal(search_request):
    search=measured_axes(search_request,missing=True);search._new_poll()
    assert not search.enrichments


def test_predicted_feasibility_does_not_override_a_measured_constraint_failure(search_request):
    search=measured_axes(search_request);search._new_poll();p=search.candidate()
    v=values(p);v['PF1']=.7
    assert opt.assess(search_request,v)
    search.consider(p,v,False)
    assert search.best_score==-100
    assert not search.records[-1]['feasible']
    assert search.measurements[-1]['values']['PF1']==.7


def test_screened_candidates_are_unique_reproducible_and_respect_declared_inputs(search_request):
    original=copy.deepcopy(search_request.model_dump());runs=[]
    for scale in (1,1,1000000):
        search=opt.Search(search_request);sequence=[]
        for _ in range(search_request.max_evaluations):
            p=search.candidate();assert opt.in_region(search_request,p)
            v=values(p,scale);search.consider(p,v,not opt.assess(search_request,v));sequence.append(p)
            assert {k:v for k,v in p.items() if k not in {'L','ZZ','g'}}=={
                k:v for k,v in search_request.parameters.items() if k not in {'L','ZZ','g'}}
        assert search.enrichments
        assert len({(p['L'],p['ZZ'],p['g']) for p in sequence})==search_request.max_evaluations
        runs.append(sequence)
    assert runs[0]==runs[1]==runs[2] and search_request.model_dump()==original


@pytest.mark.parametrize('tolerances',[False,True])
def test_fallback_keeps_parent_sequence_when_no_screening_is_applicable(search_request,tolerances):
    if tolerances:
        search_request=opt.SearchInput.model_validate({**search_request.model_dump(),'tolerances':[{'key':'g','delta':.01}]})
    runs=[]
    for algorithm in ('adaptive_poll_v1','coupled_poll_v1'):
        req=search_request.model_copy(update={'algorithm':algorithm});search=opt.Search(req);sequence=[]
        for _ in range(16):
            p=search.candidate()
            # Nonconstant interacting measurements would enable nominal screening.
            v=values(p) if tolerances else {'ATA1':.9,'PF1':.8,'CostFx':100,'Sf':.6,'Sn':.03}
            search.consider(p,v,not opt.assess(req,v));sequence.append(p)
        runs.append(sequence)
        if algorithm=='coupled_poll_v1':assert not search.enrichments
    assert runs[0]==runs[1]


def test_nonuniform_specs_and_linked_rotor_geometry_remain_valid(search_request):
    req=opt.SearchInput.model_validate({**search_request.model_dump(),'parameters':{**search_request.parameters,'hr0':.5},
        'rotor_gap_mode':'fixed_neck_bottom','variables':[{'key':'L','values':[140,151,160,167,180]},
        {'key':'ZZ','values':[12,14,16,18,20]},{'key':'g','values':[.4,.55,.6,.8]}]})
    search=opt.Search(req)
    for _ in range(48):
        p=search.candidate();assert opt.in_region(req,p)
        assert p['g']+p['hr0']==pytest.approx(1.1)
        v=values(p);search.consider(p,v,not opt.assess(req,v))


def test_selected_method_does_not_change_default_or_legacy_saved_requests(search_request):
    from dcsj import jobs
    old={k:v for k,v in search_request.model_dump().items() if k!='algorithm'}
    assert opt.SearchInput.model_validate(old).algorithm=='stratified_v1'
    assert jobs.saved_request(old).algorithm=='stratified_v1'
    assert isinstance(opt.Search(search_request),opt.CoupledPollSearch)
