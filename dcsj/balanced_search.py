"""Cycle objective extremes and hypervolume within a fixed model trial quota."""
import itertools
import math
from .quadratic_model import QuadraticModel

from . import optimization as opt, slot_search_geometry as geometry
from .parameters import InputError
from .coordinated_search import CoordinatedPairSearch, KEYS, validate_request as validate_coordination

METHOD='balanced_quadratic_infill_v1'
NOTE=('效率与成本模型搜索需要同时选择 bs1、hs12、br1、L、ZZ、Djj，'
      '每项至少两种指定规格，基准在清单内且组合数不超过 4096；'
      '主目标为效率、第二目标为材料成本，暂用于不设置公差的名义搜索。')


def validate_request(request):
    try:validate_coordination(request)
    except ValueError:raise ValueError(NOTE) from None
    if (opt.objective_keys(request)!=['efficiency','material_cost']
            or any(len(v.values)<2 or v.values[0]==v.values[-1] for v in request.variables)):
        raise ValueError(NOTE)

STRATEGIES=('efficiency','material_cost','hypervolume')


def selection_score(strategy,predicted,gain,distance,levels):
    tail=(-gain,distance,levels)
    if strategy=='efficiency':return (-predicted['ATA1'],predicted['CostFx'],*tail)
    if strategy=='material_cost':return (predicted['CostFx'],-predicted['ATA1'],*tail)
    assert strategy=='hypervolume'
    return tail


class BalancedModelSearch(CoordinatedPairSearch):
    def __init__(self,request):
        validate_request(request)
        super().__init__(request)
        self.infill_trace=[];self.infill_count=0;self.model_grid=[]
        axes={v.key:v.values for v in request.variables}
        for levels in itertools.product(*(axes[k] for k in KEYS)):
            p={**request.parameters,**dict(zip(KEYS,levels))}
            try:geometry.validate_candidate(request,p)
            except InputError:continue
            self.model_grid.append(p)

    def _new_poll(self):
        key=opt.objective_keys(self.request)[self.round%2]
        anchor=min(self.records,key=lambda r:self._rank(r,key)) if self.records else None
        before=len(self.coordination_trace)
        super()._new_poll()
        # The parent's newly scheduled coordination point keeps first position.
        reserved=self.poll_queue[:1] if len(self.coordination_trace)>before else []
        scheduled_after=self.iteration+len(reserved)
        if (len(self.measurements)<24 or self.infill_count>=self.request.max_evaluations//8
                or anchor is None or scheduled_after>=self.request.max_evaluations):return
        context=self._frontier_context()
        if context is None:return
        try:model=QuadraticModel(self.request.model_dump(),self.measurements)
        except (ValueError,OverflowError):return
        if len(model.rows)<24:return
        protected={self._identity(p) for p in reserved}
        points=[p for p in self.model_grid if self._identity(p) not in self.visited|protected]
        if not points:return
        x=[model.coordinates(p) for p in points]
        forecasts=[model.predict(p) for p in points]
        center=model.coordinates(anchor['parameters'])
        scored=[]
        strategy=STRATEGIES[self.infill_count%len(STRATEGIES)]
        for p,coordinates,predicted in zip(points,x,forecasts):
            if not all(math.isfinite(v) for v in predicted.values()) or opt.assess(self.request,predicted):continue
            objectives=opt.objective_values(self.request,predicted)
            point=tuple((sign*objectives[k]-ref)/scale for k,sign,ref,scale in
                zip(context['keys'],context['signs'],context['reference'],context['scales']))
            gain=self._area(context['points']+[point])-context['base_area']
            if not math.isfinite(gain) or gain<=1e-12:continue
            distance=math.fsum((a-b)**2 for a,b in zip(coordinates,center))
            score=selection_score(strategy,predicted,gain,distance,tuple(p[k] for k in KEYS))
            scored.append((score,p,predicted,gain))
        if not scored:return
        score,p,predicted,gain=min(scored,key=lambda r:r[0]);chosen=self._identity(p)
        was_in_poll=any(self._identity(q)==chosen for q in self.poll_queue)
        self.poll_queue=[*reserved,p,*[q for q in self.poll_queue[len(reserved):] if self._identity(q)!=chosen]]
        self.infill_count+=1
        self.infill_trace.append({'round':self.round,'after':scheduled_after,'training_after':self.iteration,
            'reserved_coordination':bool(reserved),'scheduled_index':self.infill_count,
            'training_records':len(model.rows),'training_rank':model.rank,'columns':model.columns,
            'candidate':{k:p[k] for k in KEYS},'predicted':predicted,'predicted_gain':gain,
            'selection_strategy':strategy,'selection_score':score,
            'was_in_original_poll':was_in_poll,'eligible_predictions':len(scored),
            'anchor':{k:anchor['parameters'][k] for k in KEYS},'prediction_is_not_observation':True})
