"""Synthetic constraint relations, not private motor data or fitted predictions."""
import copy
import itertools
import random

import pytest

from dcsj import jobs, optimization, search_diagnosis as diagnosis, manufacturing


def saved_job(parameters, *, values=None):
    request = optimization.SearchInput.model_validate({'parameters': parameters,
        'variables': [{'key': 'L', 'values': [150., 160., 170.]}],
        'constraints': [{'metric': 'ATA1', 'minimum': .9}, {'metric': 'PF1', 'minimum': .8}],
        'algorithm': 'curvature_rescue_v1', 'max_evaluations': 4})
    data = values or [(.91, .7), (.85, .82), (.88, .79)]
    return {'status': 'succeeded', 'request': request.model_dump(), 'candidates': [
        {'parameters': {**request.parameters, 'L': size}, 'constraint_values': {'ATA1': eff, 'PF1': pf},
         'feasible': False, 'error': None, 'ordinal': i, 'calculation_id': f'synthetic-{i}'}
        for i, (size, (eff, pf)) in enumerate(zip((150., 160., 170.), data), 1)]}


def test_complete_grid_conflict_has_checkable_witnesses_and_no_mutation(parameters):
    job = saved_job(parameters)
    before = copy.deepcopy(job)
    result = diagnosis.explain(job)
    assert job == before
    assert result['complete_grid'] and result['scope'] == 'declared_finite_grid'
    assert result['grid_points'] == result['resolved_points'] == 3
    assert result['unresolved_points'] == 0 and result['conflict_count'] == 1
    group = result['conflicts'][0]
    assert [b['metric'] for b in group['constraints']] == ['ATA1', 'PF1']
    assert [(w['metric'], w['nearest_value'], w['ordinal']) for w in group['witnesses']] == [
        ('ATA1', .85, 2), ('PF1', .7, 1)]


@pytest.mark.parametrize('problem', ['missing', 'engine_error', 'missing_metric', 'missing_snapshot',
    'nan', 'boolean', 'outside_baseline', 'changed_fixed_input', 'changed_linkage', 'duplicate', 'ambiguous'])
def test_incomplete_evidence_never_certifies_whole_grid(parameters, problem):
    job = saved_job(parameters)
    row = job['candidates'][-1]
    if problem == 'missing': job['candidates'].pop()
    elif problem == 'engine_error': row['error'] = 'Synthetic failure'
    elif problem == 'missing_metric': row['constraint_values'].pop('PF1')
    elif problem == 'missing_snapshot': row['calculation_id'] = None
    elif problem == 'nan': row['constraint_values']['PF1'] = float('nan')
    elif problem == 'boolean': row['constraint_values']['PF1'] = True
    elif problem == 'outside_baseline': row['parameters']['L'] = 171.
    elif problem == 'changed_fixed_input': row['parameters']['kW'] += 1
    elif problem == 'changed_linkage':
        job['request']['rotor_gap_mode'] = 'fixed_neck_bottom'
        row['parameters']['hr0'] += 1
    elif problem == 'duplicate': job['candidates'][-1] = copy.deepcopy(job['candidates'][0])
    else:
        row = copy.deepcopy(row); row['constraint_values']['PF1'] += .01
        job['candidates'].append(row)
    result = diagnosis.explain(job)
    assert result['scope'] == 'observed_points' and not result['complete_grid']
    assert result['unresolved_points'] == 1 and result['resolved_points'] == 2
    assert result['conflict_count'] == 1


def test_identical_duplicate_is_not_extra_coverage_and_error_does_not_erase_success(parameters):
    job = saved_job(parameters)
    job['candidates'].append(copy.deepcopy(job['candidates'][0]))
    failed = copy.deepcopy(job['candidates'][1]); failed.update(error='failed', calculation_id=None)
    job['candidates'].append(failed)
    result = diagnosis.explain(job)
    assert result['complete_grid'] and result['resolved_points'] == 3


def test_continuous_range_and_large_grid_only_describe_observed_points(parameters):
    for variable in ({'key': 'L', 'minimum': 100., 'maximum': 180.},
                     {'key': 'L', 'minimum': 100., 'maximum': 180., 'step': .001}):
        job = saved_job(parameters); job['request']['variables'] = [variable]
        result = diagnosis.explain(job)
        assert result['grid_points'] is None and not result['complete_grid']
        assert result['scope'] == 'observed_points' and result['resolved_points'] == 3


def test_explicit_float32_alias_and_stepped_grid(parameters):
    job = saved_job(parameters)
    job['request']['variables'] = [{'key': 'L', 'minimum': 150., 'maximum': 170., 'step': 10.}]
    assert diagnosis.explain(job)['complete_grid']
    job['request']['variables'] = [{'key': 'Djj', 'values': [.7, .8, 1.]}]
    for row, value in zip(job['candidates'], (.7, .8, 1.)):
        row['parameters']['L'] = parameters['L']
        row['parameters']['Djj'] = manufacturing.single(value)
    assert diagnosis.explain(job)['complete_grid']
    job['candidates'][0]['parameters']['Djj'] += 1e-8
    assert not diagnosis.explain(job)['complete_grid']


def test_material_price_is_part_of_coverage_without_current_material_validation(parameters, monkeypatch):
    job = saved_job(parameters)
    job['request']['variables'] = [{'key': 'Paihao', 'choices': ['A', 'B', 'C']}]
    job['request']['material_prices'] = {'A': 1., 'B': 2., 'C': 3.}
    for i, row in enumerate(job['candidates']):
        row['parameters'].update(L=parameters['L'], Paihao='ABC'[i], Cfe=float(i+1))
    monkeypatch.setattr(optimization, 'allowed_material', lambda _: pytest.fail('Must not query current resources'))
    assert diagnosis.explain(job)['complete_grid']
    job['candidates'][0]['parameters']['Cfe'] = 10.
    assert not diagnosis.explain(job)['complete_grid']


def test_no_results_no_bounds_valid_candidate_and_robust_jobs(parameters):
    job = saved_job(parameters)
    for field in ('tolerances', 'verification', 'feedback'):
        changed = copy.deepcopy(job); changed['request'][field] = [1]
        assert diagnosis.explain(changed) is None
    changed = copy.deepcopy(job); changed['candidates'][0]['feasible'] = True
    assert diagnosis.explain(changed) is None
    changed = copy.deepcopy(job); changed['status'] = 'running'
    assert diagnosis.explain(changed) is None
    changed = copy.deepcopy(job); changed['request']['constraints'] = []
    assert diagnosis.explain(changed) is None
    job['candidates'] = []
    result = diagnosis.explain(job)
    assert result['status'] == 'no_evidence' and not result['complete_grid']


def test_cancelled_extension_uses_actual_coverage_not_status_or_budget(parameters):
    job = saved_job(parameters); job['status'] = 'cancelled'
    job['request']['continuation'] = {'inherited_attempts': 2, 'additional_evaluations': 10}
    assert diagnosis.explain(job)['complete_grid']
    job['candidates'].pop()
    assert not diagnosis.explain(job)['complete_grid']


def test_joint_bounds_can_pass_while_other_physical_checks_fail(parameters):
    job = saved_job(parameters, values=[(.91,.9),(.85,.82),(.88,.79)])
    result = diagnosis.explain(job)
    assert result['status'] == 'no_explicit_conflict' and result['conflict_count'] == 0


def test_interval_witness_is_nearest_to_interval_including_zero_boundary():
    bounds = [{'metric': 'J1', 'minimum': 0., 'maximum': 1.}]
    rows = [{'constraint_values': {'J1': value}, 'ordinal': i, 'calculation_id': str(i)}
            for i, value in enumerate((-2., 1.2, 4.), 1)]
    count, groups = diagnosis._conflicts(bounds, rows)
    assert count == 1 and groups[0]['witnesses'][0]['nearest_value'] == 1.2


def test_minimal_conflicts_match_independent_subset_enumeration():
    rng = random.Random(71)
    for _ in range(50):
        bounds = [{'metric': str(i), 'minimum': .4, 'maximum': .7} for i in range(6)]
        rows = [{'constraint_values': {str(i): rng.random() for i in range(6)},
                 'ordinal': j, 'calculation_id': str(j)} for j in range(12)]
        expected = []
        for size in range(1, 7):
            for subset in itertools.combinations(range(6), size):
                if any(set(previous) <= set(subset) for previous in expected): continue
                if not any(all(.4 <= row['constraint_values'][str(i)] <= .7 for i in subset) for row in rows):
                    expected.append(subset)
        count, groups = diagnosis._conflicts(bounds, rows)
        assert count == len(expected)
        assert {tuple(int(b['metric']) for b in group['constraints']) for group in groups} <= set(expected)


def test_durable_job_api_uses_saved_results_and_does_not_recalculate(parameters, snapshot, tmp_path, monkeypatch):
    path = tmp_path / 'diagnosis.sqlite'
    monkeypatch.setenv('DCSJ_PROJECTS_DB', str(path))
    monkeypatch.setattr(jobs, 'engine_status', lambda: {'status': 'recovered'})
    def simulated(p, **kwargs):
        result = copy.deepcopy(snapshot); result['inputs'] = dict(p)
        eff, pf = {150: (.91,.7), 160: (.85,.82), 170: (.88,.79)}[p['L']]
        result['values'].update(ATA1=eff, PF1=pf, Sf=.6, Sn=.03, Pcu=100, Pal=80, Pfe=70, Pfw1=20, Ps1=10)
        return result
    monkeypatch.setattr(jobs, 'compute', simulated)
    request = optimization.SearchInput.model_validate(saved_job(parameters)['request'])
    job = jobs.create(request); worker = jobs.Worker(path); worker.touch(); worker.run_job(worker.claim())
    monkeypatch.setattr(jobs, 'compute', lambda *a, **kw: pytest.fail('Read must not calculate'))
    data = jobs.get(job['id'])
    assert data['constraint_diagnosis']['complete_grid'] and data['feasible_count'] == 0
    assert data['constraint_diagnosis']['conflict_count'] == 1
    assert 'constraint_diagnosis' not in jobs.get(job['id'], candidates=False)
