"""Synthetic search contracts, not evidence of motor performance gains."""
import copy
import itertools
import math

import pytest

from dcsj import optimization as opt
from dcsj.curvature_ordering import CurvatureRescueSearch
from dcsj.poll_ordering import RankedPollSearch
from test_poll_ordering import request, values


def surface(point):
    x, y = point['L'] - 160, point['g'] - .6
    eta = .85 + .0003*x - .00001*x*x - .01*y
    cycle_eta = .84 + .0002*x - .00001*x*x - .02*y
    return {**values(eta, 100+x+5*y), 'PF1': .8+.002*x-.0001*x*x,
            'IST1': 7+.015*x-.0005*x*x+.0001*x*(x*x-100),
            'Gcu': 22+.2*x+3*y, 'total_loss': 12000*(1/eta-1),
            'cycle_energy': 66/cycle_eta, 'cycle_efficiency': cycle_eta,
            'TM_refined': None}


@pytest.mark.parametrize('metric,target,rescued', [('PF1', 155, True), ('IST1', 165, False)])
def test_curvature_can_rescue_false_rejection_but_never_demotes_linear_feasibility(parameters, metric, target, rescued):
    constraint = {'metric': 'PF1', 'minimum': .786} if metric == 'PF1' else {'metric': 'IST1', 'maximum': 7.055}
    req = request(parameters, algorithm='curvature_rescue_v1', constraints=[constraint])
    search = opt.Search(req)
    assert isinstance(search, CurvatureRescueSearch)
    for length in [160, 150, 170]:
        point = search._remember({**parameters, 'L': length})
        actual = surface(point)
        search.consider(point, actual, not opt.assess(req, actual))
    base = search.measurements[0]
    point = {**parameters, 'L': target}
    before = copy.deepcopy((search.records, search.measurements))
    linear = RankedPollSearch._prediction(search, point, base)
    quadratic = search._quadratic_prediction(point, base, linear)
    predicted = search._prediction(point, base)
    assert bool(opt.assess(req, linear)) == rescued
    assert bool(opt.assess(req, quadratic)) != rescued
    assert predicted == (quadratic if rescued else linear)
    assert not opt.assess(req, surface(point))
    assert (search.records, search.measurements) == before
    # A forecast never overrides a later failed constraint in a real observation.
    actual = {**surface(point), metric: .1 if metric == 'PF1' else 20.}
    search.consider(search._remember(point), actual, not opt.assess(req, actual))
    assert search.measurements[-1]['values'] == actual
    assert not search.records[-1]['feasible']


def test_rescue_needs_distinct_local_support_and_does_not_extrapolate(parameters):
    req = request(parameters, algorithm='curvature_rescue_v1', constraints=[{'metric': 'PF1', 'minimum': .786}])
    search = opt.Search(req)
    for length in [160, 150, 170]:
        point = search._remember({**parameters, 'L': length})
        actual = surface(point)
        search.consider(point, actual, not opt.assess(req, actual))
    base = search.measurements[0]
    assert search._prediction({**parameters, 'L': 155}, base)['PF1'] == pytest.approx(.7875)
    for point in [{**parameters, 'L': 175}, {**parameters, 'L': 155, 'g': .7}]:
        assert search._prediction(point, base) == RankedPollSearch._prediction(search, point, base)
    duplicate = copy.deepcopy(search)
    duplicate.measurements.append(copy.deepcopy(duplicate.measurements[1]))
    duplicate.measurements.append({'parameters': {**parameters, 'L': 160+1e-7}, 'values': surface(parameters)})
    assert duplicate._prediction({**parameters, 'L': 155}, base) == search._prediction({**parameters, 'L': 155}, base)
    search.measurements = search.measurements[:2]
    assert search._prediction({**parameters, 'L': 155}, base) == RankedPollSearch._prediction(search, {**parameters, 'L': 155}, base)


def test_material_rescue_uses_exact_categorical_neighbor(parameters, monkeypatch):
    monkeypatch.setattr(opt, 'allowed_material', lambda value: value in {'SYNTHETIC', 'OTHER', 'UNSEEN'})
    req = request(parameters, algorithm='curvature_rescue_v1', constraints=[{'metric': 'PF1', 'minimum': .836}],
                  variables=[{'key': 'L', 'minimum': 140, 'maximum': 180},
                             {'key': 'Paihao', 'choices': ['SYNTHETIC', 'OTHER', 'UNSEEN']}])
    search = opt.Search(req)
    for length, material in [(160, 'SYNTHETIC'), (150, 'SYNTHETIC'), (170, 'SYNTHETIC'), (160, 'OTHER')]:
        point = search._remember({**parameters, 'L': length, 'Paihao': material})
        actual = surface(point)
        actual['PF1'] += .05 if material == 'OTHER' else 0
        search.consider(point, actual, not opt.assess(req, actual))
    base = search.measurements[0]
    assert search._prediction({**parameters, 'L': 155, 'Paihao': 'OTHER'}, base)['PF1'] == pytest.approx(.8375)
    assert search._prediction({**parameters, 'Paihao': 'UNSEEN'}, base) is None


OBJECTIVE_PAIRS = [(key, None) for key in opt.OBJECTIVES] + [
    (a, b) for a, b in itertools.permutations(opt.OBJECTIVES, 2) if {a, b} != opt.CYCLE_METRICS]


@pytest.mark.parametrize('objective,secondary', OBJECTIVE_PAIRS)
def test_all_objectives_keep_actual_values_budget_and_linked_geometry(parameters, objective, secondary):
    p = {**parameters, 'hr0': .4}
    budget = 128 if (objective, secondary) == ('efficiency', 'material_cost') else 32
    req = request(p, algorithm='curvature_rescue_v1', objective=objective, secondary_objective=secondary,
                  load_cycle=[{'load_fraction': 1., 'hours': 1.}] if {objective, secondary} & opt.CYCLE_METRICS else None,
                  rotor_gap_mode='fixed_neck_bottom', max_evaluations=budget,
                  constraints=[{'metric': 'PF1', 'minimum': .786}, {'metric': 'IST1', 'maximum': 7.055}])
    search = opt.Search(req)
    original = search._new_poll
    polls = []
    def inspect():
        control = copy.deepcopy(search)
        opt.CoupledPollSearch._new_poll(control)
        original()
        assert search.records == control.records and search.measurements == control.measurements
        assert search.rng.getstate() == control.rng.getstate() and search.visited == control.visited
        assert sorted(search.poll_queue, key=repr) == sorted(control.poll_queue, key=repr)
        if search.round == 1:
            assert search.poll_queue == control.poll_queue
        polls.append(search.round)
    search._new_poll = inspect
    seen = set()
    for ordinal in range(budget):
        point = search.candidate()
        assert point is not None
        assert search._identity(point) not in seen
        seen.add(search._identity(point))
        assert point['g']+point['hr0'] == pytest.approx(p['g']+p['hr0'])
        assert opt.in_region(req, point)
        if ordinal == 16:  # Failed calculation consumes a candidate without fabricating values.
            continue
        actual = surface(point)
        search.consider(point, actual, not opt.assess(req, actual))
        assert search.measurements[-1]['values'] == actual
        assert search.records[-1]['feasible'] == (not opt.assess(req, actual))
    assert len(seen) == budget and len(search.measurements) == budget-1 and polls


@pytest.mark.parametrize('extra', [{'tolerances': [{'key': 'g', 'delta': .01}]},
                                  {'variables': [{'key': 'L', 'values': [150., 160., 170.]},
                                                 {'key': 'g', 'values': [.5, .6, .7]}]}])
def test_tolerance_and_exhaustive_grid_match_existing_search(parameters, extra):
    req = request(parameters, algorithm='curvature_rescue_v1', **extra)
    a, b = opt.Search(req), RankedPollSearch(req)
    for _ in range(req.max_evaluations):
        x, y = a.candidate(), b.candidate()
        assert x == y
        if x is None:
            break
        for search, point in [(a, x), (b, y)]:
            actual = surface(point)
            search.consider(point, actual, not opt.assess(req, actual))
    assert a.enrichments == b.enrichments


def test_new_strategy_is_opt_in_and_advertised(parameters):
    assert opt.SearchInput.model_validate({'parameters': parameters,
        'variables': [{'key': 'L', 'minimum': 150, 'maximum': 170}]}).algorithm == 'stratified_v1'
    assert any(a['id'] == 'curvature_rescue_v1' and '试验' in a['label'] for a in opt.config()['algorithms'])
