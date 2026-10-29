"""Optional finite-grid coordination of slot and winding inputs.

L*ZZ/a approximates relative magnetic loading at fixed voltage/frequency and
winding factor. Bare copper/gross slot area is a packing proxy, not measured Sf.
These proxies only schedule real evaluations; they never determine feasibility.
"""
import itertools
import math

from . import manufacturing, optimization as opt, slot_search_geometry as geometry
from .curvature_ordering import CurvatureRescueSearch

METHOD = 'coordinated_pair_poll_v1'
KEYS = ('bs1', 'hs12', 'br1', 'L', 'ZZ', 'Djj')
NOTE = ('槽型与绕组协调搜索需要同时选择 bs1、hs12、br1、L、ZZ、Djj，'
        '全部填写指定规格，基准在清单内且组合数不超过 4096；暂用于名义尺寸，不能设置公差。')


def validate_request(request):
    if (request.tolerances or {v.key for v in request.variables} != set(KEYS)
            or any(not v.values for v in request.variables)
            or not opt.in_region(request, request.parameters)
            or opt.finite_grid(request.variables) is None):
        raise ValueError(NOTE)
    input_proxies(request.parameters)


def input_proxies(p):
    slot = geometry._slot(p, 'stator')
    if not slot['passed']:
        raise ValueError('Invalid analytic stator geometry')
    copper = math.pi / 4 * (p['JJ'] * p['Djj']**2 + p['MM'] * p['Dmm']**2)
    magnetic = p['L'] * p['ZZ'] / p['a']
    packing = p['ZZ'] * copper / slot['area_mm2']
    if not all(math.isfinite(v) and v > 0 for v in (magnetic, packing)):
        raise ValueError('Invalid coordination proxy')
    return magnetic, packing, slot['area_mm2'], copper


class CoordinatedPairSearch(CurvatureRescueSearch):
    def __init__(self, request):
        validate_request(request)
        super().__init__(request)
        self.axes = {v.key: v.values for v in request.variables}
        self.coordination_trace = []
        self.bridge_count = 0
        self.observed = set()
        # Cache only submitted dimensions; no calculated outputs enter this grid.
        self.input_grid = []
        for levels in itertools.product(*(self.axes[k] for k in KEYS)):
            p = {**request.parameters, **dict(zip(KEYS, levels))}
            try:
                proxy = input_proxies(p)
            except ValueError:
                continue
            self.input_grid.append((levels, proxy))

    def consider(self, p, values, feasible, *, violation=None):
        key = self._identity(p)
        if key not in self.visited or key in self.observed:
            raise ValueError('Observation must belong to exactly one charged proposal')
        self.observed.add(key)
        return super().consider(p, values, feasible, violation=violation)

    def bridge(self, center, family):
        base = input_proxies(center)
        indices = {k: self.axes[k].index(manufacturing.nominal(
            next(v for v in self.request.variables if v.key == k), center[k])) for k in KEYS}
        scored = []
        for levels, proxy in self.input_grid:
            values = dict(zip(KEYS, levels))
            changed = [k for k in KEYS if values[k] != center[k]]
            if values['br1'] != center['br1'] or len(changed) < 2:
                continue
            if any(abs(self.axes[k].index(values[k]) - indices[k]) > 1 for k in KEYS):
                continue
            if family == 'magnetic':
                if not {'L', 'ZZ'} <= set(changed) or (values['L'] - center['L']) * (values['ZZ'] - center['ZZ']) >= 0:
                    continue
            else:
                if (values['L'] != center['L'] or values['ZZ'] != center['ZZ'] or 'Djj' not in changed
                        or not {'bs1', 'hs12'} & set(changed)):
                    continue
                if (proxy[2] - base[2]) * (proxy[3] - base[3]) <= 0:
                    continue
            p = {**center, **values}
            if self._identity(p) in self.visited:
                continue
            dm, df = abs(math.log(proxy[0] / base[0])), abs(math.log(proxy[1] / base[1]))
            score = (max(dm, df), dm + df, len(changed),
                     sum(abs(self.axes[k].index(values[k]) - indices[k]) for k in KEYS), levels)
            scored.append((score, p, proxy))
        if not scored:
            return None
        score, p, proxy = min(scored, key=lambda row: row[0])
        return p, {'family': family, 'center': {k: center[k] for k in KEYS},
                   'candidate': {k: p[k] for k in KEYS}, 'base_proxies': base,
                   'candidate_proxies': proxy, 'score': score,
                   'eligible_input_candidates': len(scored), 'proxy_is_not_a_measured_result': True}

    def _new_poll(self):
        keys = opt.objective_keys(self.request)
        key = keys[self.round % len(keys)]
        anchor = min(self.records, key=lambda r: self._rank(r, key)) if self.records else None
        super()._new_poll()
        # Preserve the first full poll and the parent's random restarts. Extra
        # trials share the total budget and can displace later parent proposals.
        if self.round < 2 or self.bridge_count >= self.request.max_evaluations // 8 or anchor is None:
            return
        family = 'magnetic' if (self.round - 2) % 2 == 0 else 'fill'
        chosen = self.bridge(self._project(anchor['parameters']), family)
        if chosen:
            p, trace = chosen
            self.poll_queue = [p, *self.poll_queue]
            self.bridge_count += 1
            self.coordination_trace.append({**trace, 'round': self.round, 'after': self.iteration,
                'objective': key, 'scheduled_index': self.bridge_count, 'observed_records': len(self.records)})
