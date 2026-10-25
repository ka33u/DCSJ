"""Analytic Pareto rectangles and paired protocols, without private motor data."""
import itertools
import json
import random
from types import SimpleNamespace

import pytest

from dcsj import benchmark_quality as quality


def plan(keys=('efficiency','material_cost'), reference=None, scales=None):
    case = {'reference_point':reference, 'objective_scales':scales}
    request = SimpleNamespace(objective=keys[0], secondary_objective=keys[1] if len(keys)>1 else None)
    return quality.protocol(case, request)


def candidates(*points):
    return [{'feasible':True,'objectives':dict(zip(('efficiency','material_cost'),p))} for p in points]


def test_exact_union_mixed_directions_duplicates_and_dominated_points():
    protocol = plan(reference={'efficiency':.8,'material_cost':200},
                    scales={'efficiency':.2,'material_cost':200})
    records = candidates((.9,120),(.91,150),(.9,120),(.89,170))
    records += [{'feasible':False,'objectives':{'efficiency':1,'material_cost':0}}]
    result = quality.measure(records, protocol)
    assert result['status']=='available' and result['unique_front_count']==2
    assert result['hypervolume']==pytest.approx((.1*80+.01*50)/40)
    assert result['best_objectives']=={'efficiency':.91,'material_cost':120}
    for ordering in itertools.permutations(records[:3]):
        assert quality.measure(ordering,protocol)['hypervolume']==pytest.approx(result['hypervolume'])


def test_objective_axis_order_and_units_do_not_change_scaled_area():
    points=candidates((.9,120),(.91,150))
    p=plan(reference={'efficiency':.8,'material_cost':200},scales={'efficiency':.2,'material_cost':200})
    expected=quality.measure(points,p)['hypervolume']
    swapped=plan(('material_cost','efficiency'),reference=p['reference_point'],scales=p['objective_scales'])
    assert quality.measure(points,swapped)['hypervolume']==pytest.approx(expected)
    for c in points:c['objectives']['material_cost']*=1000
    converted=plan(reference={'efficiency':.8,'material_cost':200000},scales={'efficiency':.2,'material_cost':200000})
    assert quality.measure(points,converted)['hypervolume']==pytest.approx(expected)


def test_area_matches_independent_cell_coverage_for_many_front_shapes():
    rng=random.Random(714)
    p=plan(reference={'efficiency':0,'material_cost':10},scales={'efficiency':.1,'material_cost':1})
    for _ in range(30):
        points=[(rng.randrange(11),rng.randrange(11)) for _ in range(15)]
        # Count covered unit cells, independently of Pareto filtering / sweep order.
        covered=sum(any(x+.5 < efficiency and y+.5 < 10-cost for efficiency,cost in points)
                    for x in range(10) for y in range(10))
        result=quality.measure(candidates(*[(e/10,c) for e,c in points]),p)
        assert result['hypervolume']==pytest.approx(covered)


@pytest.mark.parametrize('key,values,reference,expected',[
    ('efficiency',[.8,.9,.85],.7,.2),('material_cost',[100,120,130],150,50)])
def test_single_objective_length_handles_maximum_and_minimum(key,values,reference,expected):
    c=[{'feasible':True,'objectives':{key:v}} for v in values]
    result=quality.measure(c,plan((key,),reference={key:reference}))
    assert result['hypervolume']==pytest.approx(expected)


def test_empty_front_zero_missing_reference_unknown_and_bad_reference_unavailable():
    p=plan(reference={'efficiency':.8,'material_cost':200})
    assert quality.measure([],p)['hypervolume']==0
    assert quality.measure(candidates((.8,200)),p)['hypervolume']==0
    absent=quality.measure(candidates((.9,120)),plan())
    assert absent['hypervolume'] is None and absent['status']=='missing_reference'
    # A tradeoff outside the fixed reference must not silently disappear.
    outside=quality.measure(candidates((.9,120),(.95,210)),p)
    assert outside['status']=='reference_does_not_bound_front' and outside['hypervolume'] is None
    assert outside['unique_front_count']==2


@pytest.mark.parametrize('bad',[None,float('nan'),float('inf'),True,'0.9'])
def test_invalid_feasible_values_do_not_get_reported_as_an_empty_front(bad):
    result=quality.measure(candidates((bad,120)),plan(reference={'efficiency':.8,'material_cost':200}))
    assert result['status']=='invalid_objectives' and result['hypervolume'] is None
    assert result['invalid_feasible_count']==1


def test_numeric_overflow_is_unavailable_not_invalid_json():
    result=quality.measure(candidates((1e308,-1e308)),plan(reference={'efficiency':-1e308,'material_cost':1e308}))
    assert result['status']=='numeric_overflow' and result['hypervolume'] is None
    json.dumps(result,allow_nan=False)


@pytest.mark.parametrize('reference,scales',[
    ({'efficiency':.8},None),({'efficiency':True,'material_cost':200},None),
    ({'efficiency':float('inf'),'material_cost':200},None),
    ({'efficiency':.8,'material_cost':200},{'efficiency':1,'material_cost':0}),
    ({'efficiency':.8,'material_cost':200},{'efficiency':1,'material_cost':float('nan')}),
    (None,{'efficiency':1,'material_cost':1}),
])
def test_protocol_rejects_incomplete_or_nonfinite_coordinates(reference,scales):
    with pytest.raises(ValueError):plan(reference=reference,scales=scales)


def experiment():
    p=plan(reference={'efficiency':.8,'material_cost':200},scales={'efficiency':.2,'material_cost':200})
    request={'objective':'efficiency','secondary_objective':'material_cost','max_evaluations':24,'seed':11}
    suite={'cases':[{'id':'synthetic','request':request}], 'budgets':[24], 'seeds':[11],
           'algorithms':['adaptive_poll_v1','hybrid_de_v1']}
    rows=[]
    for a,pts in zip(suite['algorithms'],[candidates((.9,120),(.91,150)),candidates((.92,180),(.89,160))]):
        rows.append({'case_id':'synthetic','request':{**request,'algorithm':a},
                     'quality':quality.measure(pts,p),'solver_sha256':'solver','benchmark_sha256':'benchmark',
                     'resource_identity':['kernel','wrapper','package'],
                     'assessment_policy':'physical_v1','evaluated':24,'feasible':2})
    return suite,rows


def test_higher_efficiency_can_lose_cost_and_front_quality_without_being_hidden():
    suite,rows=experiment();r=quality.summarize(rows,suite)
    assert r['complete'] and r['completed_runs']==2
    pair=r['pairs'][0];assert pair['status']=='comparable'
    assert pair['objectives']['efficiency']['outcome']=='win'
    assert pair['objectives']['material_cost']['outcome']=='loss'
    assert pair['hypervolume']['outcome']=='loss'
    assert r['totals']['hybrid_de_v1']['hypervolume']=={'win':0,'tie':0,'loss':1,'unavailable':0}
    assert r['totals']['hybrid_de_v1']['hypervolume_by_objectives']['efficiency,material_cost']['loss']==1


@pytest.mark.parametrize('field',['solver_sha256','benchmark_sha256','resource_identity','assessment_policy','request','reference_point','objective_scales'])
def test_incompatible_comparisons_are_explicitly_unavailable(field):
    suite,rows=experiment()
    if field=='request':rows[1]['request']['constraints']=[{'metric':'ATA1','minimum':.9}]
    elif field in ('reference_point','objective_scales'):rows[1]['quality'][field]['efficiency']+=.1
    else:rows[1][field]='different'
    r=quality.summarize(rows,suite)
    assert r['pairs'][0]['status']=='incomparable'
    assert r['totals']['hybrid_de_v1']['hypervolume']['unavailable']==1
    assert r['totals']['hybrid_de_v1']['objectives']['efficiency']['unavailable']==1


def test_missing_entire_pairs_and_runs_are_not_dropped_from_denominator():
    suite,rows=experiment();suite['seeds'].append(73)
    r=quality.summarize(rows[:1],suite)
    assert not r['complete'] and r['expected_runs']==4 and r['completed_runs']==1
    assert len(r['pairs'])==2 and all(p['reasons']==['missing_run'] for p in r['pairs'])
    assert r['totals']['hybrid_de_v1']['expected_pairs']==2
    assert r['totals']['hybrid_de_v1']['hypervolume']['unavailable']==2
    with pytest.raises(ValueError):quality.summarize(rows+rows,suite)
    rows[0]['case_id']='undeclared'
    with pytest.raises(ValueError):quality.summarize(rows,suite)


def test_no_reference_still_compares_objective_extremes():
    suite,rows=experiment()
    for r in rows:r['quality'].update(status='missing_reference',reference_point=None,hypervolume=None)
    pair=quality.summarize(rows,suite)['pairs'][0]
    assert pair['status']=='comparable' and pair['objectives']['efficiency']['outcome']=='win'
    assert pair['hypervolume']['outcome']=='unavailable'


def test_no_feasible_solution_does_not_become_an_objective_tie():
    suite,rows=experiment()
    for r in rows:r['quality']=quality.measure([],plan(reference={'efficiency':.8,'material_cost':200}));r['feasible']=0
    pair=quality.summarize(rows,suite)['pairs'][0]
    assert pair['objectives']['efficiency']['outcome']=='unavailable'
    assert pair['hypervolume']['outcome']=='tie' and pair['feasible']=={'baseline':0,'candidate':0}


def test_historical_sampler_assessment_is_not_equated_to_current_physical_checks():
    assert quality.assessment_policy({'algorithm':'stratified_v1'})=='legacy_v1'
    assert quality.assessment_policy({'algorithm':'adaptive_poll_v1'})=='physical_v1'
    assert quality.assessment_policy({'algorithm':'stratified_v1','tolerances':[{}]})=='physical_v1'
