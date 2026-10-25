"""Reproducible local optimizer comparisons using externally supplied case inputs.

Usage: python -m dcsj.benchmark suite.json --output benchmark-results.jsonl
The output must not exist. No library data is bundled or copied to another service.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time

from .engine import calculate_design, CalculationError
from .parameters import InputError
from . import load_cycle
from .optimization import (SearchInput, Search, assess, in_region, metrics, objective_values,
                           objective_keys, pareto_front, OBJECTIVES, SOLVER_SHA256)


def compare(suite, compute=calculate_design):
    """Yield completed matched runs; identical inputs reuse a same-process real result."""
    for key in ('cases', 'budgets', 'seeds', 'algorithms'):
        if not isinstance(suite.get(key), list) or not suite[key]:
            raise ValueError(f'Benchmark {key} must be a nonempty list')
        if key != 'cases' and len(suite[key]) != len(set(suite[key])):
            raise ValueError(f'Benchmark {key} must not contain duplicates')
    configurations = []
    case_ids = set()
    for case in suite['cases']:
        if not isinstance(case.get('id'), str) or not case['id'].strip():
            raise ValueError('Benchmark cases require a nonempty string ID')
        if case['id'] in case_ids:
            raise ValueError('Benchmark case IDs must be unique')
        case_ids.add(case['id'])
        for budget in suite['budgets']:
            for seed in suite['seeds']:
                for algorithm in suite['algorithms']:
                    request = SearchInput.model_validate({**case['request'], 'max_evaluations': budget,
                                                         'seed': seed, 'algorithm': algorithm})
                    configurations.append((case['id'], request))
    cache, identity = {}, None
    for case_id, request in configurations:
        search = Search(request)
        candidates = []
        new_calls = 0
        started = time.monotonic()
        for ordinal in range(1, request.max_evaluations + 1):
            p = search.candidate()
            if p is None: break
            encoded = json.dumps(p, sort_keys=True, ensure_ascii=False, allow_nan=False)
            entry = {'ordinal': ordinal, 'parameters': p, 'feasible': False, 'objectives': {}}
            if encoded not in cache:
                new_calls += 1
                try:
                    result = compute(p)
                    fingerprint = tuple(result['provenance'].get(key) for key in
                                        ('kernel_sha256', 'wrapper_sha256', 'resource_package'))
                    if identity is not None and identity != fingerprint:
                        raise RuntimeError('Benchmark resources changed; comparison stopped')
                    identity = fingerprint
                    cache[encoded] = {'result': result}
                except (CalculationError, InputError) as error:
                    cache[encoded] = {'error': str(error)}
            measured = cache[encoded]
            if 'error' in measured:
                entry['error'] = measured['error']
            else:
                result = measured['result']
                cycle = load_cycle.evaluate(result, request.load_cycle)
                values = metrics(result, cycle=cycle)
                violations = assess(request, values)
                if not in_region(request, p): violations.append('Reference outside search region')
                entry.update(feasible=not violations, violations=violations,
                             objectives=objective_values(request, values), values=values,
                             load_cycle=cycle, input_sha256=result['provenance'].get('input_sha256'))
                search.consider(p, values, not violations)
            candidates.append(entry)
        feasible = [c for c in candidates if c['feasible']]
        key = request.objective
        sign = 1 if OBJECTIVES[key][1] == 'max' else -1
        best = max(feasible, key=lambda c: sign*c['objectives'][key]) if feasible else None
        frontier = pareto_front(candidates, objective_keys(request))
        yield {'case_id': case_id, 'request': request.model_dump(), 'solver_sha256': SOLVER_SHA256,
               'resource_identity': identity, 'evaluated': len(candidates), 'engine_calls': new_calls,
               'cached_evaluations': len(candidates)-new_calls, 'feasible': len(feasible),
               'best_objective': best['objectives'][key] if best else None,
               'best_ordinal': best['ordinal'] if best else None,
               'pareto_ordinals': [c['ordinal'] for c in frontier], 'candidates': candidates,
               'elapsed_seconds': time.monotonic()-started}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('suite', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    document = args.suite.read_bytes()
    suite = json.loads(document)
    digest = hashlib.sha256(document).hexdigest()
    with args.output.open('x', encoding='utf-8') as stream:
        for report in compare(suite):
            report['suite_sha256'] = digest
            stream.write(json.dumps(report, ensure_ascii=False, allow_nan=False)+'\n'); stream.flush()
            print(json.dumps({'case':report['case_id'], 'algorithm':report['request']['algorithm'],
                              'seed':report['request']['seed'], 'budget':report['request']['max_evaluations'],
                              'feasible':report['feasible'], 'best':report['best_objective'],
                              'engine_calls':report['engine_calls']},ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()
