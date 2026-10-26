"""Synthetic objective surfaces for queue ordering, not motor predictions."""
import copy
import math

import pytest

from dcsj import optimization as opt
from dcsj.poll_ordering import RankedPollSearch


def request(parameters, **kwargs):
    return opt.SearchInput.model_validate({
        'parameters': parameters, 'algorithm': 'ranked_poll_v1', 'seed': 29,
        'variables': [{'key': 'L', 'minimum': 140, 'maximum': 180},
                      {'key': 'g', 'minimum': .4, 'maximum': .8}],
        'max_evaluations': 48, 'objective': 'efficiency',
        'secondary_objective': 'material_cost', **kwargs})


def values(efficiency, cost):
    return {'ATA1': efficiency, 'CostFx': cost, 'PF1': .8, 'Sf': .5, 'Sn': .03}


@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('scale', [.001, 1., 1000.])
@pytest.mark.parametrize('bounded', [False, True])
def test_tradeoff_priority_is_not_single_objective_improvement(parameters, reverse, scale, bounded):
    req = request(parameters, objective='material_cost' if reverse else 'efficiency',
                  secondary_objective='efficiency' if reverse else 'material_cost',
                  constraints=[{'metric': 'ATA1', 'minimum': .7},
                               {'metric': 'CostFx', 'maximum': 200 * scale}] if bounded else [])
    search = RankedPollSearch(req)
    for length, efficiency, cost in [(160, .9, 120), (150, .8, 80)]:
        p = search._remember({**parameters, 'L': length})
        search.consider(p, values(efficiency, cost * scale), True)
    before = copy.deepcopy(search.records)
    context = search._frontier_context()
    anchor = search.records[0]
    # Lower efficiency can still add a useful, less expensive tradeoff.
    rank = search._priority(values(.86, 95 * scale), 'efficiency', anchor, context)
    assert rank[0] == 0
    # Identical or dominated predictions add no new covered area.
    for efficiency, cost in [(.9, 120), (.8, 80), (.85, 130)]:
        assert search._priority(values(efficiency, cost * scale), 'efficiency', anchor, context) == (2, 0., 0.)
    assert search._priority(None, 'efficiency', anchor, context) == (1, 0., 0.)
    if bounded:
        assert search._priority(values(.99, 250 * scale), 'efficiency', anchor, context)[0] == 3
        assert -rank[1] == pytest.approx(.375)
    assert search.records == before


def test_predictor_requires_measured_direction_and_complete_neighbor_support(parameters):
    search = RankedPollSearch(request(parameters))
    for length, cost in [(160, 100), (170, 110)]:
        p = search._remember({**parameters, 'L': length})
        search.consider(p, values(.8 + (length - 160) * .001, cost), True)
    base = search.measurements[0]
    predicted = search._prediction({**parameters, 'L': 175}, base)
    assert predicted['ATA1'] == pytest.approx(.815)
    assert predicted['CostFx'] == 115
    for p in ({**parameters, 'L': 150}, {**parameters, 'L': 181}, {**parameters, 'L': 175, 'g': .7}):
        assert search._prediction(p, base) is None
    assert len(search.records) == len(search.measurements) == 2


@pytest.mark.parametrize('objectives', [('efficiency', None), ('material_cost', None),
                                      ('efficiency', 'material_cost'), ('material_cost', 'efficiency')])
def test_queue_is_only_a_stable_permutation_and_never_changes_observations(parameters, objectives):
    req = request(parameters, objective=objectives[0], secondary_objective=objectives[1])
    search = RankedPollSearch(req)
    events = []
    original = search._new_poll

    def inspect():
        control = copy.deepcopy(search)
        opt.CoupledPollSearch._new_poll(control)
        original()
        assert search.records == control.records and search.measurements == control.measurements
        assert search.visited == control.visited and search.rng.getstate() == control.rng.getstate()
        assert sorted(search.poll_queue, key=repr) == sorted(control.poll_queue, key=repr)
        if search.round == 1:
            assert search.poll_queue == control.poll_queue
        events.extend(e for e in search.enrichments[len(control.enrichments):] if e.get('kind') == 'poll_ordering')

    search._new_poll = inspect
    for _ in range(48):
        p = search.candidate()
        assert p is not None
        measured = values(.8 + .001 * (p['L'] - 140) - .01 * p['g'], 100 + p['L'] + 5 * p['g'])
        # Predictions must not fabricate a feasible result after actual failure.
        feasible = p['g'] < .7
        search.consider(p, measured, feasible)
        assert search.records[-1]['feasible'] == feasible
        assert search.measurements[-1]['values'] == measured
    assert events and all(e['proposals'] == 0 for e in events)
    assert len(search.records) == len(search.measurements) == 48


def test_tolerance_fallback_and_no_neighbor_order_match_existing_method(parameters):
    for tolerance in (None, [{'key': 'g', 'delta': .01}]):
        req = request(parameters, tolerances=tolerance)
        control, ranked = opt.CoupledPollSearch(req), RankedPollSearch(req)
        for i in range(48):
            a, b = control.candidate(), ranked.candidate()
            if tolerance or i < 5:
                assert a == b
            for search, p in ((control, a), (ranked, b)):
                search.consider(p, values(.85, 100), True)
        if tolerance:
            assert control.enrichments == ranked.enrichments


def test_area_clips_and_unions_rectangles_without_counting_dominated_points_twice():
    points = [(2, 3), (3, 2), (2, 3), (1, 1), (-2, 7), (4, -1), (0, 9)]
    assert math.isclose(RankedPollSearch._area(points), 8.)
