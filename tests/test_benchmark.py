"""Synthetic comparisons test protocol/accounting, not real motor performance."""
import copy
import json
import sys

import pytest
from dcsj import benchmark
from dcsj.engine import CalculationError


@pytest.fixture
def suite(parameters):
    return {'cases':[{'id':'synthetic','request':{'parameters':parameters,'variables':[
        {'key':'L','minimum':150,'maximum':170,'step':5}], 'objective':'efficiency'}}],
        'algorithms':['stratified_v1','adaptive_poll_v1'],'seeds':[11],'budgets':[8]}


@pytest.fixture
def fake_engine(snapshot):
    calls=[]
    def compute(parameters):
        calls.append(copy.deepcopy(parameters));r=copy.deepcopy(snapshot);r['inputs']=dict(parameters)
        r['values'].update(ATA1=.9-abs(parameters['L']-165)*.001,CostFx=parameters['L'],
                           Sf=.6,Sn=.03,Pcu=100,Pal=80,Pfe=70,Pfw1=20,Ps1=10)
        return r
    return compute,calls


def test_matched_budgets_cache_accounting_and_measured_optimum(suite,fake_engine):
    compute,calls=fake_engine;reports=list(benchmark.compare(suite,compute))
    assert len(reports)==2
    assert sum(r['engine_calls'] for r in reports)==len(calls)==5
    assert sum(r['cached_evaluations'] for r in reports)==5
    for r in reports:
        assert r['evaluated']==5 and r['best_objective']==pytest.approx(.9)
        assert r['request']['max_evaluations']==8
        assert r['candidates'][r['best_ordinal']-1]['parameters']['L']==165
        assert all(c['values']['ATA1']==c['objectives']['efficiency'] for c in r['candidates'])
        assert len(r['solver_sha256'])==64


def test_all_protocols_validate_before_any_engine_call(suite,fake_engine):
    compute,calls=fake_engine;suite['algorithms'].append('not-an-algorithm')
    with pytest.raises(ValueError):list(benchmark.compare(suite,compute))
    assert not calls


def test_failed_points_still_consume_search_budget_and_are_cached(suite,fake_engine):
    compute,calls=fake_engine
    def partial(parameters):
        if parameters['L']<165:raise CalculationError('synthetic failure')
        return compute(parameters)
    reports=list(benchmark.compare(suite,partial))
    assert sum(r['engine_calls'] for r in reports)==5
    assert all(r['evaluated']==5 for r in reports)
    assert all(sum('error' in c for c in r['candidates'])==3 for r in reports)
    assert all(r['feasible']==2 and r['best_objective']==pytest.approx(.9) for r in reports)


def test_resource_drift_aborts_instead_of_comparing_different_models(suite,fake_engine):
    compute,calls=fake_engine
    def changing(parameters):
        r=compute(parameters);r['provenance']['resource_package']='initial' if len(calls)<3 else 'changed'
        return r
    with pytest.raises(RuntimeError,match='resources changed'):list(benchmark.compare(suite,changing))


def test_cli_never_overwrites_existing_evidence(tmp_path,monkeypatch,suite):
    source=tmp_path/'suite.json';source.write_text(json.dumps(suite));output=tmp_path/'results.jsonl';output.write_text('existing evidence')
    monkeypatch.setattr(sys,'argv',['benchmark',str(source),'--output',str(output)])
    with pytest.raises(FileExistsError):benchmark.main()
    assert output.read_text()=='existing evidence'


@pytest.mark.parametrize('key',['cases','budgets','seeds','algorithms'])
def test_empty_protocols_are_not_reported_as_success(suite,fake_engine,key):
    compute,calls=fake_engine;suite[key]=[]
    with pytest.raises(ValueError,match='nonempty'):list(benchmark.compare(suite,compute))
    assert not calls


def test_tolerance_benchmark_counts_samples_and_caches_whole_engine_results(suite,fake_engine):
    compute,calls=fake_engine
    suite['cases'][0]['request'].update(variables=[{'key':'L','values':[160,165]}],
        tolerances=[{'key':'g','delta':.05}])
    reports=list(benchmark.compare(suite,compute))
    assert sum(r['engine_calls'] for r in reports)==len(calls)==6
    assert sum(r['cached_evaluations'] for r in reports)==6
    for r in reports:
        assert r['evaluated']==6 and r['validation_failures']==0 and len(r['candidates'])==2
        assert r['best_objective']==pytest.approx(.9)
        assert all(c['robustness']['evaluated_samples']==3 for c in r['candidates'])


def test_invalid_tolerance_dimensions_are_counted_without_engine_call(suite,fake_engine):
    compute,calls=fake_engine
    suite['cases'][0]['request'].update(variables=[{'key':'L','values':[160,165]}],
        tolerances=[{'key':'g','delta':.05}],tolerance_gap_mode='fixed_neck_bottom')
    reports=list(benchmark.compare(suite,compute))
    assert sum(r['engine_calls'] for r in reports)==len(calls)==4
    for r in reports:
        assert r['evaluated']==6 and r['validation_failures']==2 and r['best_ordinal'] is None
        assert all(c['robustness']['status']=='failed' for c in r['candidates'])


def test_refined_mode_is_part_of_cache_identity(suite,fake_engine):
    compute,calls=fake_engine
    baseline=copy.deepcopy(suite['cases'][0]);baseline['request']['parameters']['_curve']=1
    refined=copy.deepcopy(baseline);refined['id']='refined'
    refined['request']['constraints']=[{'metric':'TM_refined','minimum':3}]
    suite['cases']=[baseline,refined];modes=[]
    def with_refinement(parameters,**kwargs):
        modes.append(kwargs);r=compute(parameters)
        if kwargs.get('refine_torque'):
            r['curve_metrics']={'refined_torque_peak':{'status':'available','value':3.2}}
        return r
    reports=list(benchmark.compare(suite,with_refinement))
    assert len(calls)==10 and sum(bool(m) for m in modes)==5
    assert all(r['feasible']==5 for r in reports if r['case_id']=='refined')
    assert all(c['values']['TM_refined'] is None for r in reports if r['case_id']=='synthetic' for c in r['candidates'])
