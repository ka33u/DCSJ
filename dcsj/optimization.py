"""Bounded derivative-free search with real engine evaluations and explicit feasibility."""
import math
import random
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .parameters import INTEGER_KEYS, validate
from .engine import available_materials


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
CONSTRAINTS = {'ATA1', 'PF1', 'IST1', 'TST1', 'TM1', 'Sf', 'Sn', 'J1', 'Bg',
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
    constraints: list[Constraint] = Field(default_factory=list, max_length=15)
    max_evaluations: int = Field(default=24, ge=2, le=128)
    seed: int = Field(default=42, ge=0, le=2**32-1)

    @model_validator(mode='after')
    def valid_search(self):
        if self.objective not in OBJECTIVES:
            raise ValueError('不支持的目标')
        if len({v.key for v in self.variables}) != len(self.variables):
            raise ValueError('搜索变量不能重复')
        if len({c.metric for c in self.constraints}) != len(self.constraints):
            raise ValueError('同一指标的约束请写在一项中')
        self.parameters = validate(self.parameters, strict=True)
        if self.parameters['_wire_target'] != 0:
            raise ValueError('寻优前请将目标槽满率设为 0，以固定线规，避免内核覆盖搜索变量')
        return self


def config():
    return {'variables': [{'key': k, 'label': v, 'integer': k in DISCRETE} for k, v in VARIABLES.items()],
            'objectives': [{'id': k, 'metric': v[0], 'direction': v[1], 'label': v[2]} for k, v in OBJECTIVES.items()],
            'constraint_metrics': sorted(CONSTRAINTS), 'materials': sorted(available_choices()),
            'max_evaluations': 128,
            'method': '固定随机种子的分层采样 + 局部扰动，使用真实数值核逐项评估；不保证全局最优。'}


def metrics(result):
    return {**result['values'], 'total_loss': sum(result['values'][k] for k in ('Pcu', 'Pal', 'Pfe', 'Pfw1', 'Ps1'))}


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
    return int(round(value)) if variable.key in DISCRETE else round(value, 12)


def in_region(request, parameters):
    return all(parameters[v.key] in v.choices if v.key == 'Paihao' else
               v.minimum <= parameters[v.key] <= v.maximum and (
                   not v.step or abs((parameters[v.key] - v.minimum) / v.step -
                                     round((parameters[v.key] - v.minimum) / v.step)) < 1e-7)
               for v in request.variables)


class Search:
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
