"""Read-only explanations of saved bounds; never relax constraints or propose points.

A finite-domain conclusion requires a successful, unambiguous saved result at
every declared grid point. Partial searches only describe their observed points.
Physical validity is deliberately not used to discard observations: a conflict
over all calculated points is stronger than one over a selected valid subset.
"""
import math
from types import SimpleNamespace

from . import manufacturing
from .optimization import Variable, finite_grid, in_region

METHOD = 'saved-constraint-conflicts-v1'
MAX_EXPLANATIONS = 12


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _passes(bound, value):
    return ((bound.get('minimum') is None or value >= bound['minimum']) and
            (bound.get('maximum') is None or value <= bound['maximum']))


def _distance(bound, value):
    return max(0, (bound.get('minimum') if bound.get('minimum') is not None else value) - value,
               value - (bound.get('maximum') if bound.get('maximum') is not None else value))


def _conflicts(bounds, rows):
    full = (1 << len(rows)) - 1
    masks = [sum(1 << i for i, row in enumerate(rows)
                 if _passes(bound, row['constraint_values'][bound['metric']])) for bound in bounds]
    # Reuse intersections of smaller subsets; at most 2^15 subsets.
    intersections = [full] * (1 << len(bounds))
    minimal = []
    for subset in range(1, len(intersections)):
        bit = subset & -subset
        intersections[subset] = intersections[subset ^ bit] & masks[bit.bit_length() - 1]
        if not intersections[subset] and all(intersections[subset ^ (1 << i)]
                for i in range(len(bounds)) if subset & (1 << i)):
            minimal.append(subset)
    minimal.sort(key=lambda subset: (subset.bit_count(), subset))
    explanations = []
    for subset in minimal[:MAX_EXPLANATIONS]:
        indices = [i for i in range(len(bounds)) if subset & (1 << i)]
        witnesses = []
        for i in indices:
            others = intersections[subset ^ (1 << i)]
            candidates = [row for j, row in enumerate(rows) if others & (1 << j)]
            bound = bounds[i]
            nearest = min(candidates, key=lambda row: (
                _distance(bound, row['constraint_values'][bound['metric']]), row['ordinal']))
            witnesses.append({'metric': bound['metric'], 'other_bounds_satisfied': len(candidates),
                'nearest_value': nearest['constraint_values'][bound['metric']],
                'ordinal': nearest['ordinal'], 'calculation_id': nearest['calculation_id']})
        explanations.append({'constraints': [dict(bounds[i]) for i in indices], 'witnesses': witnesses})
    return len(minimal), explanations


def explain(job):
    """Use saved nominal results, including inherited ones, without material lookup."""
    document = job['request']
    bounds = document.get('constraints') or []
    if (job['status'] not in {'succeeded', 'failed', 'cancelled', 'interrupted'} or
            not bounds or document.get('tolerances') or document.get('verification') or
            document.get('feedback') or any(row['feasible'] for row in job['candidates'])):
        return None
    result = {'method': METHOD, 'scope': 'observed_points', 'complete_grid': False,
              'grid_points': None, 'resolved_points': 0, 'unresolved_points': None,
              'conflict_count': 0, 'conflicts': [], 'status': 'no_evidence'}
    if len(bounds) > 15 or not 1 <= len(document.get('variables', [])) <= 6:
        return {**result, 'status': 'unavailable'}
    # Do not revalidate saved requests against today's installed material list.
    try:
        variables = [Variable.model_construct(**v) for v in document['variables']]
        request = SimpleNamespace(variables=variables, parameters=document['parameters'],
            rotor_gap_mode=document.get('rotor_gap_mode', 'independent'),
            material_prices=document.get('material_prices'))
        grid = finite_grid(variables)
        expected = set(grid) if grid is not None else None
        result['grid_points'] = len(expected) if expected is not None else None
        changing = {v.key for v in variables}
        if request.rotor_gap_mode == 'fixed_neck_bottom':
            changing.add('hr0')
        if request.material_prices is not None:
            changing.add('Cfe')
        fixed = {k: v for k, v in request.parameters.items() if k not in changing}
        observations, ambiguous = {}, set()
        for row in job['candidates']:
            p = row['parameters']
            if ({k: v for k, v in p.items() if k not in changing} != fixed or not in_region(request, p)):
                continue
            point = tuple(manufacturing.nominal(v, p[v.key]) if v.values else p[v.key] for v in variables)
            if expected is not None and point not in expected:
                continue
            if row.get('error') or not row.get('calculation_id'):
                continue
            values = row.get('constraint_values') or {}
            if not all(_finite(values.get(bound['metric'])) for bound in bounds):
                continue
            vector = tuple(values[bound['metric']] for bound in bounds)
            if point in observations and observations[point][0] != vector:
                ambiguous.add(point)
            else:
                observations.setdefault(point, (vector, row))
        for point in ambiguous:
            observations.pop(point, None)
        rows = [row for _, row in observations.values()]
        result['resolved_points'] = len(rows)
        result['ambiguous_points'] = len(ambiguous)
        if expected is not None:
            result['unresolved_points'] = len(expected - observations.keys())
            result['complete_grid'] = not result['unresolved_points']
        if result['complete_grid']:
            result['scope'] = 'declared_finite_grid'
        if rows:
            count, conflicts = _conflicts(bounds, rows)
            result.update(conflict_count=count, conflicts=conflicts,
                          status='constraint_conflict' if count else 'no_explicit_conflict')
        return result
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        # Old or incomplete records stay readable and never gain a certificate.
        return {**result, 'status': 'unavailable', 'complete_grid': False,
                'scope': 'observed_points', 'conflict_count': 0, 'conflicts': []}
