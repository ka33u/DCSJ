"""Order existing nominal poll candidates using measured local neighbors.

Predictions affect evaluation order only. Dual objectives use additional covered
area relative to the actual feasible archive; all accepted results remain actual
engine observations. This heuristic is not a full MADS or Bayesian optimizer.
"""
import math

from . import manufacturing, optimization as opt


class RankedPollSearch(opt.CoupledPollSearch):
    def _prediction(self, parameters, base):
        center = dict(self._identity(base['parameters']))
        target = dict(self._identity(parameters))
        changed = [v for v in self.request.variables if center[v.key] != target[v.key]]
        if not changed:
            return dict(base['values'])
        terms = []
        for variable in changed:
            matches = []
            for measured in self.measurements:
                point = dict(self._identity(measured['parameters']))
                if [k for k in center if center[k] != point[k]] != [variable.key]:
                    continue
                if variable.key == 'Paihao':
                    if point[variable.key] != target[variable.key]:
                        continue
                    ratio = 1.
                else:
                    delta = point[variable.key] - center[variable.key]
                    if not delta or manufacturing.single(point[variable.key]) == manufacturing.single(center[variable.key]):
                        continue
                    ratio = (target[variable.key] - center[variable.key]) / delta
                    if not 0 < ratio <= 2:
                        continue
                matches.append((abs(math.log(ratio)), measured, ratio))
            if not matches:
                return None
            _, neighbor, ratio = min(matches, key=lambda item: item[0])
            terms.append((neighbor, ratio))
        common = set(base['values'])
        for measured, _ in terms:
            common &= set(measured['values'])
        predicted = {
            key: base['values'][key] + sum(ratio * (measured['values'][key] - base['values'][key])
                                          for measured, ratio in terms)
            for key in common
            if all(isinstance(values.get(key), (int, float)) and math.isfinite(values[key])
                   for values in [base['values'], *[m['values'] for m, _ in terms]])
        }
        if any(value is None or not math.isfinite(value)
               for value in opt.objective_values(self.request, predicted).values()):
            return None
        return predicted

    @staticmethod
    def _area(points):
        height = 0.
        pieces = []
        for x, y in sorted(set(points), reverse=True):
            if x > 0 and y > height:
                pieces.append(x * (y - height))
                height = y
        return math.fsum(pieces)

    def _frontier_context(self):
        keys = opt.objective_keys(self.request)
        if len(keys) != 2:
            return None
        actual = [r for r in self.records if r['feasible'] and all(
            isinstance(r['objectives'].get(k), (int, float)) and math.isfinite(r['objectives'][k]) for k in keys)]
        if not actual:
            return None
        signs = [1 if opt.OBJECTIVES[k][1] == 'max' else -1 for k in keys]
        raw = [tuple(sign * r['objectives'][k] for sign, k in zip(signs, keys)) for r in actual]
        reference, scales = [], []
        for axis, key in enumerate(keys):
            lower = min(p[axis] for p in raw)
            span = max(p[axis] for p in raw) - lower
            metric, direction, _ = opt.OBJECTIVES[key]
            bounds = [signs[axis] * (c.minimum if direction == 'max' else c.maximum)
                      for c in self.request.constraints if c.metric == metric and
                      (c.minimum if direction == 'max' else c.maximum) is not None]
            reference.append(max(bounds) if bounds else lower - max(.05 * span, 1e-6 * max(1., abs(lower))))
            scales.append(span if span > 0 else max(1., abs(lower)))
        points = [tuple((p[i] - reference[i]) / scales[i] for i in range(2)) for p in raw]
        return {'keys': keys, 'signs': signs, 'reference': reference, 'scales': scales,
                'points': points, 'base_area': self._area(points)}

    def _priority(self, predicted, key, anchor, context):
        if predicted is None:
            return (1, 0., 0.)
        record = {'objectives': opt.objective_values(self.request, predicted),
                  'feasible': not opt.assess(self.request, predicted),
                  'violation': opt.violation_score(self.request, predicted)}
        rank = self._rank(record, key)
        if context is not None and record['feasible']:
            point = tuple((sign * record['objectives'][k] - ref) / scale for k, sign, ref, scale in
                          zip(context['keys'], context['signs'], context['reference'], context['scales']))
            gain = self._area(context['points'] + [point]) - context['base_area']
            return (0, -gain, 0.) if math.isfinite(gain) and gain > 0 else (2, 0., 0.)
        priority = 0 if record['feasible'] and rank < self._rank(anchor, key) else 2 if record['feasible'] else 3
        return (priority, *rank[1:])

    def _new_poll(self):
        keys = opt.objective_keys(self.request)
        key = keys[self.round % len(keys)]
        anchor = min(self.records, key=lambda r: self._rank(r, key)) if self.records else None
        super()._new_poll()
        # Complete the first poll normally; scenario aggregates cannot be used as
        # nominal measurements for this predictor. Exhaustive grids bypass polls.
        if self.request.tolerances or self.round < 2 or anchor is None:
            return
        center = self._identity(anchor['parameters'])
        base = next((m for m in reversed(self.measurements) if self._identity(m['parameters']) == center), None)
        if base is None:
            return
        context = self._frontier_context()
        scored = []
        predicted_count = 0
        for index, parameters in enumerate(self.poll_queue):
            predicted = self._prediction(parameters, base)
            predicted_count += predicted is not None
            scored.append((self._priority(predicted, key, anchor, context), index, parameters))
        scored.sort(key=lambda item: item[0])
        self.poll_queue = [p for _, _, p in scored]
        self.enrichments.append({'round': self.round, 'objective': key, 'kind': 'poll_ordering',
                                 'pairs': [], 'proposals': 0, 'before_poll': self.iteration,
                                 'order': [i for _, i, _ in scored], 'predicted_count': predicted_count,
                                 'prediction_only_for_ordering': True})
