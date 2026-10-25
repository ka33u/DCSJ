"""Objective-aware quality measures for matched local optimizer experiments.

Reference points and positive scales are supplied before running a case, never
fitted to an algorithm's results. Hypervolume is only comparable within a case.
"""
import copy
import math

from .optimization import OBJECTIVES, objective_keys

QUALITY_VERSION = 'fixed_reference_hv_v1'


def finite(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def protocol(case, request):
    keys = objective_keys(request)
    reference = case.get('reference_point')
    scales = case.get('objective_scales')
    if reference is None:
        if scales is not None:
            raise ValueError('objective_scales requires a reference_point')
    elif not isinstance(reference, dict) or set(reference) != set(keys) or not all(finite(v) for v in reference.values()):
        raise ValueError('reference_point must contain one finite number for each objective')
    if scales is None:
        scales = dict.fromkeys(keys, 1.0)
    if not isinstance(scales, dict) or set(scales) != set(keys) or not all(finite(v) and v > 0 for v in scales.values()):
        raise ValueError('objective_scales must contain one positive finite number for each objective')
    return {'version': QUALITY_VERSION, 'reference_point': copy.deepcopy(reference), 'objective_scales': dict(scales),
            'directions': {key: OBJECTIVES[key][1] for key in keys}}


def measure(candidates, plan):
    """Exact union of reference rectangles for at most two objectives."""
    keys = list(plan['directions'])
    if not 1 <= len(keys) <= 2:
        raise ValueError('Quality measurement supports one or two objectives')
    feasible = [c for c in candidates if c['feasible']]
    valid = [c for c in feasible if all(finite(c['objectives'].get(k)) for k in keys)]
    signs = [1 if plan['directions'][k] == 'max' else -1 for k in keys]
    vectors = {tuple(sign * c['objectives'][k] for k, sign in zip(keys, signs)) for c in valid}
    front = sorted(p for p in vectors if not any(
        q != p and all(a >= b for a, b in zip(q, p)) for q in vectors))
    report = {**copy.deepcopy(plan), 'feasible_count': len(feasible), 'invalid_feasible_count': len(feasible)-len(valid),
              'unique_front_count': len(front), 'hypervolume': None,
              'best_objectives': {k: sign * max(p[i] for p in vectors) if vectors else None
                                  for i, (k, sign) in enumerate(zip(keys, signs))}}
    if len(valid) != len(feasible):
        return {**report, 'status': 'invalid_objectives'}
    reference = plan['reference_point']
    if reference is None:
        return {**report, 'status': 'missing_reference'}
    points = [tuple((p[i]-sign*reference[k])/plan['objective_scales'][k]
                    for i, (k, sign) in enumerate(zip(keys, signs))) for p in front]
    if any(not finite(x) for p in points for x in p):
        return {**report, 'status': 'numeric_overflow'}
    # Do not silently discard out-of-reference tradeoffs and call the remaining
    # area the quality of the entire front. Correct the protocol for all methods.
    if any(x < 0 for p in points for x in p):
        return {**report, 'status': 'reference_does_not_bound_front'}
    area = previous = 0.0
    for p in points:
        area += (p[0]-previous) * (p[1] if len(keys) == 2 else 1)
        previous = p[0]
    if not finite(area):
        return {**report, 'status': 'numeric_overflow'}
    return {**report, 'status': 'available', 'hypervolume': area}


def assessment_policy(request):
    # The original sampler's historical assessment is deliberately preserved.
    return 'physical_v1' if request.get('tolerances') or request['algorithm'] != 'stratified_v1' else 'legacy_v1'


def _scope(report):
    return {k: v for k, v in report['request'].items() if k != 'algorithm'}


def _difference(old, new, direction):
    if not finite(old) or not finite(new):
        return {'baseline': old, 'candidate': new, 'improvement': None, 'outcome': 'unavailable'}
    delta = (new-old) * (1 if direction == 'max' else -1)
    if not finite(delta):
        return {'baseline': old, 'candidate': new, 'improvement': None, 'outcome': 'unavailable'}
    outcome = 'tie' if math.isclose(old, new, rel_tol=1e-12, abs_tol=1e-12) else ('win' if delta > 0 else 'loss')
    return {'baseline': old, 'candidate': new, 'improvement': delta, 'outcome': outcome}


def summarize(reports, suite):
    """Pair every declared case/budget/seed against the first declared algorithm.

Missing or incompatible pairs stay in the denominator as unavailable, rather
than disappearing from the results. Raw hypervolumes are never pooled.
"""
    baseline = suite['algorithms'][0]
    index = {}
    expected = {(c['id'], b, s, a) for c in suite['cases'] for b in suite['budgets']
                for s in suite['seeds'] for a in suite['algorithms']}
    for r in reports:
        request = r['request']
        key = (r['case_id'], request['max_evaluations'], request['seed'], request['algorithm'])
        if key in index or key not in expected:
            raise ValueError('Duplicate or undeclared benchmark run')
        index[key] = r
    pairs = []
    totals = {}
    for algorithm in suite['algorithms'][1:]:
        counts = {'expected_pairs': 0, 'comparable_pairs': 0, 'incomparable_pairs': 0, 'objectives': {},
                  'hypervolume': dict.fromkeys(('win','tie','loss','unavailable'), 0), 'hypervolume_by_objectives': {}}
        totals[algorithm] = counts
        for case in suite['cases']:
            keys = [k for k in (case['request'].get('objective','efficiency'), case['request'].get('secondary_objective')) if k]
            signature = ','.join(sorted(keys))
            counts['hypervolume_by_objectives'].setdefault(signature, dict.fromkeys(('win','tie','loss','unavailable'), 0))
            for k in keys: counts['objectives'].setdefault(k, dict.fromkeys(('win','tie','loss','unavailable'), 0))
            for budget in suite['budgets']:
                for seed in suite['seeds']:
                    old, new = (index.get((case['id'],budget,seed,a)) for a in (baseline,algorithm))
                    pair = {'case_id':case['id'], 'budget':budget, 'seed':seed, 'algorithm':algorithm,
                            'objective_keys':keys, 'objectives':{}, 'reasons':[]}
                    counts['expected_pairs'] += 1
                    if old is None or new is None:
                        pair['reasons'].append('missing_run')
                    else:
                        if _scope(old) != _scope(new): pair['reasons'].append('request_mismatch')
                        for field in ('solver_sha256', 'benchmark_sha256', 'resource_identity', 'assessment_policy'):
                            if not old.get(field) or old.get(field) != new.get(field):
                                pair['reasons'].append(field+'_mismatch_or_missing')
                        if not old.get('quality') or not new.get('quality'):
                            pair['reasons'].append('missing_quality')
                        else:
                            for field in ('version','directions','reference_point','objective_scales'):
                                if old['quality'].get(field) != new['quality'].get(field):
                                    pair['reasons'].append('quality_'+field+'_mismatch')
                            if any(r['quality'].get('invalid_feasible_count') for r in (old,new)):
                                pair['reasons'].append('invalid_objectives')
                    pair['status'] = 'incomparable' if pair['reasons'] else 'comparable'
                    counts[pair['status']+'_pairs'] += 1
                    for k in keys:
                        left = old['quality']['best_objectives'].get(k) if not pair['reasons'] else None
                        right = new['quality']['best_objectives'].get(k) if not pair['reasons'] else None
                        pair['objectives'][k] = _difference(left, right, OBJECTIVES[k][1])
                        counts['objectives'][k][pair['objectives'][k]['outcome']] += 1
                    usable = not pair['reasons'] and all(r['quality']['status'] == 'available' for r in (old,new))
                    pair['hypervolume'] = _difference(old['quality']['hypervolume'] if usable else None,
                                                     new['quality']['hypervolume'] if usable else None, 'max')
                    counts['hypervolume'][pair['hypervolume']['outcome']] += 1
                    counts['hypervolume_by_objectives'][signature][pair['hypervolume']['outcome']] += 1
                    if old is not None and new is not None:
                        pair['evaluated'] = {'baseline':old['evaluated'],'candidate':new['evaluated']}
                        pair['feasible'] = {'baseline':old['feasible'],'candidate':new['feasible']}
                        pair['quality_status'] = {label:r.get('quality',{}).get('status')
                                                  for label,r in [('baseline',old),('candidate',new)]}
                    pairs.append(pair)
    return {'baseline_algorithm':baseline, 'expected_runs':len(expected), 'completed_runs':len(index),
            'complete':len(index)==len(expected), 'totals':totals, 'pairs':pairs}
