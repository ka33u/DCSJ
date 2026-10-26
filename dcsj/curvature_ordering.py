"""Optional curvature rescue of nominal poll candidates near constraints.

Only a linearly predicted infeasible candidate can use three-point curvature to
recover its priority. Already feasible linear predictions remain unchanged.
Predictions order existing candidates; actual values determine every result.
"""
import itertools
import math

from . import manufacturing, optimization as opt
from .poll_ordering import RankedPollSearch


class CurvatureRescueSearch(RankedPollSearch):
    def _prediction(self, parameters, base):
        linear = super()._prediction(parameters, base)
        if linear is None or not opt.assess(self.request, linear):
            return linear
        quadratic = self._quadratic_prediction(parameters, base, linear)
        return quadratic if quadratic is not None and not opt.assess(self.request, quadratic) else linear

    def _quadratic_prediction(self, parameters, base, linear):
        center = dict(self._identity(base['parameters']))
        target = dict(self._identity(parameters))
        if center == target:
            return linear
        corrections = []
        for variable in self.request.variables:
            key = variable.key
            if key == 'Paihao' or target[key] == center[key]:
                continue
            offset = target[key] - center[key]
            neighbors = []
            for measured in self.measurements:
                point = dict(self._identity(measured['parameters']))
                if [k for k in center if point[k] != center[k]] != [key]:
                    continue
                delta = point[key] - center[key]
                if not delta or manufacturing.single(point[key]) == manufacturing.single(center[key]):
                    continue
                neighbors.append((delta, measured))
            matches = [(abs(math.log(offset / delta)), measured, offset / delta)
                       for delta, measured in neighbors if 0 < offset / delta <= 2]
            if not matches:
                continue
            _, donor, ratio = min(matches, key=lambda item: item[0])
            pairs = []
            for (first, left), (second, right) in itertools.combinations(neighbors, 2):
                if manufacturing.single(center[key] + first) == manufacturing.single(center[key] + second):
                    continue
                # Interpolate only inside measured support; never extrapolate
                # quadratic terms or interpolate between material labels.
                if not min(0., first, second) <= offset <= max(0., first, second):
                    continue
                scale = max(abs(first), abs(second))
                u, v, x = first / scale, second / scale, offset / scale
                weights = ((x - u) * (x - v) / (u * v),
                           x * (x - v) / (u * (u - v)),
                           x * (x - u) / (v * (v - u)))
                # Numerical amplification guard, not a confidence bound.
                if not all(math.isfinite(w) for w in weights) or sum(abs(w) for w in weights) > 4:
                    continue
                pairs.append(((scale, abs(first - offset) + abs(second - offset)), left, right, weights))
            if not pairs:
                continue
            _, left, right, weights = min(pairs, key=lambda item: item[0])
            changes = {}
            for metric in linear.keys() & base['values'].keys() & left['values'].keys() & right['values'].keys():
                points = [m['values'][metric] for m in (base, left, right)]
                if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in points):
                    continue
                old_term = ratio * (donor['values'][metric] - base['values'][metric])
                new_term = math.fsum(w * (value - points[0]) for w, value in zip(weights, points))
                correction = new_term - old_term
                if math.isfinite(correction):
                    changes[metric] = correction
            corrections.append(changes)
        predicted = {metric: value + math.fsum(c.get(metric, 0.) for c in corrections)
                     for metric, value in linear.items()}
        if not all(math.isfinite(v) for v in predicted.values()):
            return linear
        return predicted
