"""Bounded derivative-free search with real engine evaluations and explicit feasibility."""
import math
import random
import itertools
import hashlib
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .parameters import INTEGER_KEYS, validate
from .engine import available_materials
from .curves import sampled_torque_peak

SOLVER_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def available_choices():
    from .library import material_choices
    return set(available_materials()) | set(material_choices())


def allowed_material(reference):
    if reference in available_materials():
        return True
    from .library import from_reference
    try:
        return from_reference(reference, 'materials')['compatibility']['compatible']
    except ValueError:
        return False

# Rating, prices, empirical loss factors and target efficiency are deliberately fixed.
VARIABLES = {
    'L': '铁心长度 mm', 'g': '气隙 mm', 'ZZ': '每槽导体数',
    'a': '并联支路数', 'JJ': '第一组并绕根数', 'Djj': '第一组线径 mm',
    'MM': '第二组并绕根数', 'Dmm': '第二组线径 mm', 'Paihao': '材料牌号',
}
DISCRETE = INTEGER_KEYS | {'ZZ'}
OBJECTIVES = {'efficiency': ('ATA1', 'max', '效率最高'),
              'material_cost': ('CostFx', 'min', '材料成本最低'),
              'power_factor': ('PF1', 'max', '功率因数最高'),
              'copper_mass': ('Gcu', 'min', '铜重最低'),
              'total_loss': ('total_loss', 'min', '总损耗最低')}
CONSTRAINTS = {'ATA1', 'PF1', 'IST1', 'TST1', 'TM1', 'TM_curve', 'Sf', 'Sn', 'J1', 'Bg',
               'Gcu', 'Gfe', 'Gal', 'CostFx', 'total_loss'}


class Variable(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    key: str
    minimum: float | None = Field(default=None, ge=0, le=1000000)
    maximum: float | None = Field(default=None, ge=0, le=1000000)
    step: float | None = Field(default=None, gt=0, le=1000000)
    choices: list[str] | None = Field(default=None, min_length=1, max_length=20)

    @model_validator(mode='after')
    def bounds(self):
        if self.key not in VARIABLES:
            raise ValueError('寻优只允许修改几何、绕组与材料变量，不允许修改额定值、价格或经验系数')
        if self.key == 'Paihao':
            if not self.choices or any(not allowed_material(x) for x in self.choices):
                raise ValueError('请选择本机已安装的材料牌号')
            if len(set(self.choices)) != len(self.choices):
                raise ValueError('材料候选不能重复')
            if any(v is not None for v in (self.minimum, self.maximum, self.step)):
                raise ValueError('材料变量使用 choices，不使用数值范围')
        else:
            if self.choices is not None or None in (self.minimum, self.maximum):
                raise ValueError('数值变量必须提供 minimum 与 maximum')
            if self.maximum <= self.minimum:
                raise ValueError('maximum 必须大于 minimum')
            if self.key not in {'MM', 'Dmm'} and self.minimum <= 0:
                raise ValueError('此变量下限必须大于零')
            if self.step and self.step > self.maximum - self.minimum:
                raise ValueError('步长不能大于范围')
            if self.key in DISCRETE:
                if any(not float(v).is_integer() for v in (self.minimum, self.maximum, self.step or 1)):
                    raise ValueError('绕组数量与匝数变量必须使用整数边界和整数步长')
                self.step = self.step or 1
        return self


class Constraint(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    metric: str
    minimum: float | None = None
    maximum: float | None = None

    @model_validator(mode='after')
    def bounds(self):
        if self.metric not in CONSTRAINTS:
            raise ValueError('不支持的约束指标')
        if self.minimum is None and self.maximum is None:
            raise ValueError('约束至少提供一个边界')
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError('约束下限不能超过上限')
        return self


class SearchInput(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    name: str = Field(default='自动寻优', min_length=1, max_length=120)
    parameters: dict[str, str | float | int | None]
    variables: list[Variable] = Field(min_length=1, max_length=6)
    objective: str = 'efficiency'
    secondary_objective: str | None = None
    algorithm: Literal['hybrid_de_v1', 'stratified_v1', 'adaptive_poll_v1'] = 'stratified_v1'
    constraints: list[Constraint] = Field(default_factory=list, max_length=15)
    max_evaluations: int = Field(default=24, ge=2, le=128)
    seed: int = Field(default=42, ge=0, le=2**32-1)

    @model_validator(mode='after')
    def valid_search(self):
        if self.objective not in OBJECTIVES:
            raise ValueError('不支持的目标')
        if self.secondary_objective is not None and (
                self.secondary_objective not in OBJECTIVES or self.secondary_objective == self.objective):
            raise ValueError('第二目标须为不同的有效指标')
        if len({v.key for v in self.variables}) != len(self.variables):
            raise ValueError('搜索变量不能重复')
        if len({c.metric for c in self.constraints}) != len(self.constraints):
            raise ValueError('同一指标的约束请写在一项中')
        self.parameters = validate(self.parameters, strict=True)
        if self.parameters['_wire_target'] != 0:
            raise ValueError('寻优前请将目标槽满率设为 0，以固定线规，避免内核覆盖搜索变量')
        if any(c.metric == 'TM_curve' for c in self.constraints):
            # Store the effective option in the queued request and every snapshot.
            self.parameters['_curve'] = 1
        return self


def config():
    return {'variables': [{'key': k, 'label': v, 'integer': k in DISCRETE} for k, v in VARIABLES.items()],
            'objectives': [{'id': k, 'metric': v[0], 'direction': v[1], 'label': v[2]} for k, v in OBJECTIVES.items()],
            'constraint_metrics': sorted(CONSTRAINTS),
            'constraint_labels': {'TM_curve': '转矩曲线采样峰值（倍）', 'TM1': '最大转矩（原公式估计，倍）'},
            'materials': sorted(available_choices()),
            'max_evaluations': 128,
            'algorithms': [{'id': 'stratified_v1', 'label': '分层采样 + 局部搜索'},
                           {'id': 'adaptive_poll_v1', 'label': '自适应邻域搜索'},
                           {'id': 'hybrid_de_v1', 'label': '混合差分进化（新方法）'}],
            'method': '可选择分层采样、自适应邻域搜索或混合差分进化。邻域搜索围绕当前较优方案试探，无改善时缩小步长；各方法均保留可行的折中方案，每个候选使用真实计算核。'}


def metrics(result):
    peak = sampled_torque_peak(result.get('curves', []))
    return {**result['values'], 'total_loss': sum(result['values'][k] for k in ('Pcu', 'Pal', 'Pfe', 'Pfw1', 'Ps1')),
            'TM_curve': peak['value']}


def assess(request, values):
    violations = []
    for key in ('Sf', 'Sn'):
        if not 0 < values.get(key, math.nan) < 1:
            violations.append(f'{key} 必须在 (0, 1) 范围')
    for constraint in request.constraints:
        value = values.get(constraint.metric)
        if value is None or not math.isfinite(value):
            violations.append(f'{constraint.metric} 无有效结果')
        elif ((constraint.minimum is not None and value < constraint.minimum) or
              (constraint.maximum is not None and value > constraint.maximum)):
            violations.append(f'{constraint.metric}={value:.7g} 不满足约束')
    for key in objective_keys(request):
        value = values.get(OBJECTIVES[key][0])
        if value is None or not math.isfinite(value):
            violations.append(f'{key} 目标没有有效结果')
    if request.algorithm != 'stratified_v1':
        for key in ('ATA1', 'PF1'):
            if not 0 < values.get(key, math.nan) <= 1:
                violations.append(f'{key} 必须在 (0, 1] 范围')
        for key in ('Pcu', 'Pal', 'Pfe', 'Pfw1', 'Ps1', 'CostFx'):
            if key in values and (not math.isfinite(values[key]) or values[key] < 0):
                violations.append(f'{key} 不能为负数或无效值')
    # Baseline may lie outside the requested region. It remains visible but is not eligible as best.
    return violations


def snap(variable, value):
    if variable.key == 'Paihao':
        return value
    value = min(variable.maximum, max(variable.minimum, value))
    if variable.step:
        count = math.floor((variable.maximum - variable.minimum) / variable.step + 1e-10)
        index = min(count, max(0, round((value - variable.minimum) / variable.step)))
        value = variable.minimum + variable.step * index
    # Rounding to a fixed decimal count can push either endpoint outside bounds
    # and destroy fine manufacturing steps. Keep the computed grid value instead.
    return int(round(value)) if variable.key in DISCRETE else min(variable.maximum, max(variable.minimum, value))


def in_region(request, parameters):
    return all(parameters[v.key] in v.choices if v.key == 'Paihao' else
               v.minimum <= parameters[v.key] <= v.maximum and (
                   not v.step or abs((parameters[v.key] - v.minimum) / v.step -
                                     round((parameters[v.key] - v.minimum) / v.step)) < 1e-7)
               for v in request.variables)


class StratifiedSearch:
    def __init__(self, request):
        self.request = request
        self.rng = random.Random(request.seed)
        self.best = None
        self.best_score = None
        self.visited = set()
        self.sample_count = max(2, request.max_evaluations // 2)
        self.strata = {}
        for variable in request.variables:
            order = list(range(self.sample_count)); self.rng.shuffle(order)
            self.strata[variable.key] = order
        self.iteration = 0

    def candidate(self):
        request = self.request
        for attempt in range(200):
            p = dict(request.parameters)
            if self.iteration:
                for variable in request.variables:
                    if variable.key == 'Paihao':
                        p[variable.key] = self.rng.choice(variable.choices)
                    elif self.iteration <= self.sample_count:
                        index = (self.iteration - 1 + attempt) % self.sample_count
                        fraction = (self.strata[variable.key][index] + self.rng.random()) / self.sample_count
                        p[variable.key] = snap(variable, variable.minimum + fraction * (variable.maximum - variable.minimum))
                    else:
                        center = (self.best or request.parameters)[variable.key]
                        radius = (variable.maximum - variable.minimum) * max(.025, .25 * (1 - self.iteration / request.max_evaluations))
                        value = center + self.rng.uniform(-radius, radius)
                        # Occasional full-range proposals preserve exploration and avoid rounding plateaus.
                        if attempt > 10 or self.rng.random() < .2:
                            value = self.rng.uniform(variable.minimum, variable.maximum)
                        p[variable.key] = snap(variable, value)
            identity = tuple((v.key, p[v.key]) for v in request.variables)
            if identity not in self.visited:
                self.visited.add(identity); self.iteration += 1
                return p
        return None

    def consider(self, parameters, values, feasible):
        metric, direction, _ = OBJECTIVES[self.request.objective]
        value = values[metric]
        score = value if direction == 'max' else -value
        improved = feasible and (self.best_score is None or score > self.best_score)
        if improved:
            self.best, self.best_score = parameters, score
        return value, improved


def objective_keys(request):
    return [key for key in (request.objective, request.secondary_objective) if key]


def objective_values(request, values):
    return {key: values.get(OBJECTIVES[key][0]) for key in objective_keys(request)}


def dominates(left, right, objectives):
    """Strict Pareto dominance; equal objective vectors remain separate designs."""
    a = [left[key] * (1 if OBJECTIVES[key][1] == 'max' else -1) for key in objectives]
    b = [right[key] * (1 if OBJECTIVES[key][1] == 'max' else -1) for key in objectives]
    return all(x >= y for x, y in zip(a, b)) and any(x > y for x, y in zip(a, b))


def pareto_front(records, objectives):
    valid = [r for r in records if r['feasible'] and all(
        isinstance(r['objectives'].get(k), (int, float)) and math.isfinite(r['objectives'][k]) for k in objectives)]
    return [r for r in valid if not any(dominates(s['objectives'], r['objectives'], objectives) for s in valid)]


def violation_score(request, values):
    """Dimensionless distance to explicit bounds; never a weighted objective penalty."""
    score = 0.0
    bounds = [('Sf', 0, 1, True), ('Sn', 0, 1, True)]
    bounds += [(c.metric, c.minimum, c.maximum, False) for c in request.constraints]
    for key, low, high, strict in bounds:
        value = values.get(key)
        if value is None or not math.isfinite(value):
            score += 1e6
            continue
        scale = max(abs(low or 0), abs(high or 0), 1e-9)
        if low is not None and (value < low or strict and value == low):
            score += max(1e-12, (low - value) / scale)
        if high is not None and (value > high or strict and value == high):
            score += max(1e-12, (value - high) / scale)
    # Missing physical/objective validity must never rank as a zero-violation design.
    if not score and assess(request, values):
        score = 1e6
    return score


class HybridSearch(StratifiedSearch):
    """Budgeted mixed-variable DE with local polling and feasibility-first selection.

    Uses its own RNG and rounds every proposal to the declared manufacturing grid.
    Materials use categorical crossover, never arithmetic on material identifiers.
    """
    def __init__(self, request):
        super().__init__(request)
        self.records = []
        self.population_size = max(6, 4 * len(request.variables))
        self.sample_count = min(max(6, 2 * len(request.variables)), max(2, request.max_evaluations // 3))
        self.strata = {}
        for v in request.variables:
            order = list(range(self.sample_count)); self.rng.shuffle(order)
            self.strata[v.key] = order
        self.grid = None
        count = 1
        for v in request.variables:
            if v.key != 'Paihao' and not v.step:
                count = 4097; break
            count *= len(v.choices) if v.key == 'Paihao' else math.floor((v.maximum - v.minimum) / v.step + 1e-10) + 1
            if count > 4096: break
        if count <= 4096:
            axes = [v.choices if v.key == 'Paihao' else [snap(v, v.minimum + i * v.step)
                    for i in range(math.floor((v.maximum - v.minimum) / v.step + 1e-10) + 1)] for v in request.variables]
            self.grid = list(itertools.product(*axes)); self.rng.shuffle(self.grid)
        self.grid_cursor = 0
        self.exhaustive = self.grid is not None and len(self.grid) <= request.max_evaluations - 1
        # Under small budgets, population-only proposals can miss every boundary.
        # Screen each axis against the projected baseline before adapting the population.
        center = dict(request.parameters)
        for v in request.variables:
            center[v.key] = (center[v.key] if center[v.key] in v.choices else v.choices[0]) if v.key == 'Paihao' else snap(v, center[v.key])
        self.screening = []
        for v in request.variables:
            edges = [x for x in v.choices if x != center[v.key]][:2] if v.key == 'Paihao' else [snap(v, v.minimum), snap(v, v.maximum)]
            for value in edges:
                self.screening.append({**center, v.key: value})
        self.rng.shuffle(self.screening)
        self.screen_cursor = 0

    def _remember(self, p):
        identity = tuple((v.key, p[v.key]) for v in self.request.variables)
        if identity in self.visited:
            return None
        self.visited.add(identity); self.iteration += 1
        return p

    def _grid_candidate(self):
        while self.grid_cursor < len(self.grid):
            values = self.grid[self.grid_cursor]; self.grid_cursor += 1
            p = dict(self.request.parameters)
            p.update({v.key: value for v, value in zip(self.request.variables, values)})
            accepted = self._remember(p)
            if accepted is not None: return accepted
        return None

    def _population(self):
        keys = objective_keys(self.request)
        remaining = [r for r in self.records if r['feasible']]
        population = []
        while remaining and len(population) < self.population_size:
            front = pareto_front(remaining, keys)
            # Crowding distance retains both objective extremes when a front is large.
            distances = {id(r): 0.0 for r in front}
            for key in keys:
                ordered = sorted(front, key=lambda r: r['objectives'][key])
                distances[id(ordered[0])] = distances[id(ordered[-1])] = math.inf
                span = ordered[-1]['objectives'][key] - ordered[0]['objectives'][key]
                if span:
                    for i in range(1, len(ordered) - 1):
                        distances[id(ordered[i])] += (ordered[i+1]['objectives'][key] - ordered[i-1]['objectives'][key]) / span
            front.sort(key=lambda r: -distances[id(r)])
            population.extend(front[:self.population_size-len(population)])
            selected = {id(r) for r in front}
            remaining = [r for r in remaining if id(r) not in selected]
        infeasible = sorted((r for r in self.records if not r['feasible']), key=lambda r: r['violation'])
        return population + infeasible[:self.population_size-len(population)]

    def candidate(self):
        if not self.iteration:
            return self._remember(dict(self.request.parameters))
        if self.exhaustive:
            return self._grid_candidate()
        while self.screen_cursor < len(self.screening):
            p = self.screening[self.screen_cursor]; self.screen_cursor += 1
            accepted = self._remember(p)
            if accepted is not None: return accepted
        request = self.request
        population = self._population()
        front = pareto_front(population, objective_keys(request))
        guide = self.rng.choice(front) if front else (population[0] if population else None)
        for attempt in range(200):
            p = dict(request.parameters)
            exploring = self.iteration <= self.sample_count + len(self.screening) or len(population) < 3 or attempt > 20 or self.iteration % 7 == 0
            if exploring:
                for v in request.variables:
                    if v.key == 'Paihao':
                        p[v.key] = self.rng.choice(v.choices)
                    else:
                        index = (self.iteration - 1 + attempt) % self.sample_count
                        fraction = (self.strata[v.key][index] + self.rng.random()) / self.sample_count
                        p[v.key] = snap(v, v.minimum + fraction * (v.maximum - v.minimum))
            elif self.iteration % 3 == 0:
                p.update(guide['parameters'])
                v = request.variables[(self.iteration // 3 + attempt) % len(request.variables)]
                if v.key == 'Paihao': p[v.key] = self.rng.choice(v.choices)
                else:
                    radius = max(v.step or 0, (v.maximum - v.minimum) * max(.02, .2 * (1 - self.iteration/request.max_evaluations)))
                    p[v.key] = snap(v, p[v.key] + self.rng.choice([-1, 1]) * radius)
            else:
                target, first, second = self.rng.sample(population, 3)
                forced = self.rng.randrange(len(request.variables)); weight = self.rng.uniform(.5, 1.0)
                for i, v in enumerate(request.variables):
                    if i != forced and self.rng.random() > .8:
                        p[v.key] = target['parameters'][v.key]
                    elif v.key == 'Paihao':
                        p[v.key] = self.rng.choice([guide['parameters'][v.key], first['parameters'][v.key], self.rng.choice(v.choices)])
                    else:
                        value = guide['parameters'][v.key] + weight * (first['parameters'][v.key] - second['parameters'][v.key])
                        p[v.key] = snap(v, value)
            accepted = self._remember(p)
            if accepted is not None: return accepted
        return self._grid_candidate() if self.grid is not None else None

    def consider(self, parameters, values, feasible):
        objectives = objective_values(self.request, values)
        if not all(v is not None and math.isfinite(v) for v in objectives.values()):
            return None, False
        feasible = feasible and in_region(self.request, parameters)
        value, improved = super().consider(parameters, values, feasible)
        if in_region(self.request, parameters):
            self.records.append({'parameters': dict(parameters), 'objectives': objectives,
                                 'feasible': feasible, 'violation': violation_score(self.request, values)})
        return value, improved


def Search(request):
    return {'hybrid_de_v1': HybridSearch, 'stratified_v1': StratifiedSearch,
            'adaptive_poll_v1': AdaptivePollSearch}[request.algorithm](request)


class AdaptivePollSearch(HybridSearch):
    """Feasible-first coordinate/pattern polling, not a full MADS implementation.

    Every round uses one fixed center. Failed polls shrink its normalized radius;
    successful moves can extend the observed direction. Dual objectives alternate
    their incumbents, without a hidden objective weighting. Unsuccessful coupled
    directions and occasional restarts avoid restricting proposals to one axis.
    """
    def __init__(self, request):
        super().__init__(request)
        self.rng = random.Random(request.seed)
        self.poll_queue = []
        self.round = 0
        self.poll_states = {key: {'radius': .25, 'anchor': None} for key in objective_keys(request)}

    def _rank(self, record, key):
        direction = 1 if OBJECTIVES[key][1] == 'max' else -1
        score = -direction * record['objectives'][key]
        return (0, 0, score) if record['feasible'] else (1, record['violation'], score)

    def _project(self, p):
        p = dict(p)
        for v in self.request.variables:
            if v.key == 'Paihao':
                if p[v.key] not in v.choices: p[v.key] = v.choices[0]
            else: p[v.key] = snap(v, p[v.key])
        return p

    def _new_poll(self):
        keys = objective_keys(self.request)
        key = keys[self.round % len(keys)]
        state = self.poll_states[key]
        incumbent = min(self.records, key=lambda r: self._rank(r, key)) if self.records else None
        previous = state['anchor']
        progress = incumbent is not None and previous is not None and self._rank(incumbent, key) < self._rank(previous, key)
        if previous is not None:
            state['radius'] = min(.5, state['radius'] * 1.25) if progress else max(.0005, state['radius'] * .5)
        center = self._project(incumbent['parameters'] if incumbent else self.request.parameters)
        self.poll_queue = []
        if progress:
            extended = dict(center)
            for v in self.request.variables:
                if v.key != 'Paihao':
                    extended[v.key] = center[v.key] + (center[v.key] - previous['parameters'][v.key])
            self.poll_queue.append(self._project(extended))
        directions = []
        for v in self.request.variables:
            if v.key == 'Paihao':
                directions.extend({**center, v.key: value} for value in v.choices if value != center[v.key])
            else:
                distance = max(v.step or 0, (v.maximum - v.minimum) * state['radius'])
                directions.extend(self._project({**center, v.key: center[v.key] + sign * distance}) for sign in (-1, 1))
        self.rng.shuffle(directions)
        self.poll_queue.extend(directions)
        if previous is not None and not progress:
            # Coupled directions can cross a narrow feasible valley that axis polls miss.
            signs = {v.key: self.rng.choice([-1, 1]) for v in self.request.variables}
            for sign in (-1, 1):
                p = dict(center)
                for v in self.request.variables:
                    if v.key != 'Paihao':
                        p[v.key] += sign * signs[v.key] * max(v.step or 0, (v.maximum-v.minimum)*state['radius'])
                self.poll_queue.append(self._project(p))
        state['anchor'] = incumbent
        self.round += 1
        if self.round % 5 == 0 or not self.records:
            p = dict(center)
            for v in self.request.variables:
                p[v.key] = self.rng.choice(v.choices) if v.key == 'Paihao' else snap(v, self.rng.uniform(v.minimum, v.maximum))
            self.poll_queue.append(p)

    def candidate(self):
        if not self.iteration:
            return self._remember(dict(self.request.parameters))
        if self.exhaustive:
            return self._grid_candidate()
        # Try a projected seed once when the reference lies outside the requested grid.
        if self.iteration == 1 and not in_region(self.request, self.request.parameters):
            candidate = self._remember(self._project(self.request.parameters))
            if candidate is not None: return candidate
        for _ in range(50):
            if not self.poll_queue: self._new_poll()
            while self.poll_queue:
                candidate = self._remember(self.poll_queue.pop(0))
                if candidate is not None: return candidate
        return self._grid_candidate() if self.grid is not None else None
