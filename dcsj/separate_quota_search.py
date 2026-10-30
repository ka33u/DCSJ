"""Budgeted global model trials and one local trial after measured improvement."""
import math
from . import optimization as opt
from .balanced_search import BalancedModelSearch, STRATEGIES, selection_score, validate_request as validate_balanced
from .coordinated_search import CoordinatedPairSearch, KEYS
from .quadratic_model import QuadraticModel

METHOD = 'separate_quota_infill_v1'
CADENCE = 4
NOTE = ('效率与成本模型搜索及邻点细化需要同时选择 bs1、hs12、br1、L、ZZ、Djj，'
        '每项至少两种指定规格，基准在清单内且组合数不超过 4096；'
        '主目标为效率、第二目标为材料成本，暂用于不设置公差的名义搜索。')


def validate_request(request):
    try:
        validate_balanced(request)
    except ValueError:
        raise ValueError(NOTE) from None


class CadencedModelSearch(BalancedModelSearch):
    def __init__(self,request):
        super().__init__(request)
        self.last_refresh_attempt=20
        self.refresh_trace=[]
        self.model_objective='efficiency'

    def _new_poll(self):
        self.model_objective=opt.objective_keys(self.request)[self.round%2]
        # Parent coordination and ordinary polling remain the fallback. Model
        # refresh is scheduled below rather than only at neighborhood boundaries.
        CoordinatedPairSearch._new_poll(self)

    def _coordination_pending(self):
        return bool(self.coordination_trace and self.coordination_trace[-1]['after']==self.iteration
                    and self.poll_queue and self.coordination_trace[-1]['candidate']==
                    {k:self.poll_queue[0][k] for k in KEYS})

    def _refresh(self):
        if (self.iteration<24 or len(self.measurements)<24 or
                self.iteration-self.last_refresh_attempt<CADENCE or
                self.infill_count>=self.request.max_evaluations//8 or
                self.iteration>=self.request.max_evaluations or self._coordination_pending()):
            return
        self.last_refresh_attempt=self.iteration
        log={'after':self.iteration,'measurements':len(self.measurements),'status':'no_front'}
        self.refresh_trace.append(log)
        context=self._frontier_context()
        if context is None:return
        anchor=min(self.records,key=lambda r:self._rank(r,self.model_objective))
        try:model=QuadraticModel(self.request.model_dump(),self.measurements)
        except (ValueError,OverflowError):log['status']='fit_unavailable';return
        if len(model.rows)<24:log['status']='incomplete_training';return
        center=model.coordinates(anchor['parameters']);scored=[]
        strategy=STRATEGIES[self.infill_count%len(STRATEGIES)]
        for p in self.model_grid:
            if self._identity(p) in self.visited:continue
            predicted=model.predict(p)
            if not all(math.isfinite(v) for v in predicted.values()) or opt.assess(self.request,predicted):continue
            objectives=opt.objective_values(self.request,predicted)
            point=tuple((sign*objectives[k]-ref)/scale for k,sign,ref,scale in
                        zip(context['keys'],context['signs'],context['reference'],context['scales']))
            gain=self._area(context['points']+[point])-context['base_area']
            if not math.isfinite(gain) or gain<=1e-12:continue
            distance=math.fsum((a-b)**2 for a,b in zip(model.coordinates(p),center))
            score=selection_score(strategy,predicted,gain,distance,tuple(p[k] for k in KEYS))
            scored.append((score,p,predicted,gain))
        log['eligible_predictions']=len(scored)
        if not scored:log['status']='no_predicted_gain';return
        score,p,predicted,gain=min(scored,key=lambda r:r[0]);chosen=self._identity(p)
        was_in_poll=any(self._identity(q)==chosen for q in self.poll_queue)
        self.poll_queue=[p,*[q for q in self.poll_queue if self._identity(q)!=chosen]]
        self.infill_count+=1;log['status']='scheduled'
        self.infill_trace.append({'round':self.round,'after':self.iteration,'training_after':self.iteration,
            'reserved_coordination':False,'scheduled_index':self.infill_count,
            'training_records':len(model.rows),'training_rank':model.rank,'columns':model.columns,
            'candidate':{k:p[k] for k in KEYS},'predicted':predicted,'predicted_gain':gain,
            'selection_strategy':strategy,'selection_score':score,
            'was_in_original_poll':was_in_poll,'eligible_predictions':len(scored),
            'anchor':{k:anchor['parameters'][k] for k in KEYS},'prediction_is_not_observation':True,
            'cadence':CADENCE,'parent_coordination_pending':False})

    def candidate(self):
        if not self.iteration or self.exhaustive:
            return opt.AdaptivePollSearch.candidate(self)
        for _ in range(50):
            if not self.poll_queue:self._new_poll()
            self._refresh()
            while self.poll_queue:
                p=self._remember(self.poll_queue.pop(0))
                if p is not None:return p
        return self._grid_candidate() if self.grid is not None else None


class RefinedCadenceSearch(CadencedModelSearch):
    def __init__(self, request):
        super().__init__(request)
        self.local_trace = []
        self.local_attempts = []
        self.pending_local = None
        self.allowed_model_inputs = {self._identity(p) for p in self.model_grid}

    def _extremes(self):
        good = [r for r in self.records if r['feasible']]
        return {'efficiency': max((r['objectives']['efficiency'] for r in good), default=None),
                'material_cost': min((r['objectives']['material_cost'] for r in good), default=None)}

    def consider(self, p, values, feasible, *, violation=None):
        before = self._extremes()
        result = super().consider(p, values, feasible, violation=violation)
        is_global = self.infill_trace and self.infill_trace[-1]['after'] == self.iteration - 1
        if not is_global or not feasible or opt.assess(self.request, values):
            return result
        after = self._extremes()
        improved = [k for k in before if after[k] is not None and (before[k] is None or
                    (after[k] > before[k] if k == 'efficiency' else after[k] < before[k]))]
        if improved:
            preferred = self.infill_trace[-1]['selection_strategy']
            self.pending_local = {'parameters': dict(p), 'ordinal': self.iteration,
                                  'improved': improved, 'prior_best': before, 'posterior_best': after,
                                  'strategy': preferred if preferred in improved else improved[0]}
        return result

    def _try_local(self):
        trigger = self.pending_local
        self.pending_local = None  # One opportunity, including a failed fit or empty eligible set.
        log = {'after': self.iteration, 'trigger': trigger, 'status': 'no_front'}
        self.local_attempts.append(log)
        context = self._frontier_context()
        if context is None:
            return False
        try:
            model = QuadraticModel(self.request.model_dump(), self.measurements)
        except (ValueError, OverflowError):
            log['status'] = 'fit_unavailable'
            return False
        if len(model.rows) < 24:
            log['status'] = 'incomplete_training'
            return False
        anchor = trigger['parameters']
        center = model.coordinates(anchor)
        strategy = trigger['strategy']
        metric, direction, _ = opt.OBJECTIVES[strategy]
        incumbent = self._extremes()[strategy]
        scored = []
        for k in KEYS:
            index = self.axes[k].index(anchor[k])
            for j in (index - 1, index + 1):
                if not 0 <= j < len(self.axes[k]):
                    continue
                p = {**anchor, k: self.axes[k][j]}
                identity = self._identity(p)
                if identity in self.visited or identity not in self.allowed_model_inputs:
                    continue
                predicted = model.predict(p)
                if not all(math.isfinite(v) for v in predicted.values()) or opt.assess(self.request, predicted):
                    continue
                improvement = (predicted[metric] - incumbent) * (1 if direction == 'max' else -1)
                if improvement <= 1e-12:
                    continue
                objectives = opt.objective_values(self.request, predicted)
                point = tuple((sign * objectives[key] - ref) / scale for key, sign, ref, scale in
                              zip(context['keys'], context['signs'], context['reference'], context['scales']))
                gain = self._area(context['points'] + [point]) - context['base_area']
                if not math.isfinite(gain) or gain <= 1e-12:
                    continue
                distance = math.fsum((a - b) ** 2 for a, b in zip(model.coordinates(p), center))
                score = selection_score(strategy, predicted, gain, distance, tuple(p[key] for key in KEYS))
                scored.append((score, p, predicted, gain, k, improvement))
        log['eligible_predictions'] = len(scored)
        if not scored:
            log['status'] = 'no_predicted_local_improvement'
            return False
        score, p, predicted, gain, axis, improvement = min(scored, key=lambda r: r[0])
        chosen = self._identity(p)
        in_poll = any(self._identity(q) == chosen for q in self.poll_queue)
        self.poll_queue = [p, *[q for q in self.poll_queue if self._identity(q) != chosen]]
        log['status'] = 'scheduled'
        self.local_trace.append({'after': self.iteration, 'training_after': self.iteration,
                                 'training_records': len(model.rows), 'training_rank': model.rank,
                                 'columns': model.columns, 'trigger_ordinal': trigger['ordinal'],
                                 'actual_improved_extremes': trigger['improved'],
                                 'selection_strategy': strategy, 'selection_score': score,
                                 'anchor': {k: anchor[k] for k in KEYS}, 'changed_axis': axis,
                                 'candidate': {k: p[k] for k in KEYS}, 'predicted': predicted,
                                 'predicted_gain': gain, 'predicted_objective_improvement': improvement,
                                 'incumbent_objective': incumbent, 'was_in_original_poll': in_poll,
                                 'eligible_predictions': len(scored), 'prediction_is_not_observation': True})
        return True

    def _refresh(self):
        if (self.iteration >= self.request.max_evaluations or self._coordination_pending() or
                self.infill_count + len(self.local_trace) >= self.request.max_evaluations // 8):
            return
        if self.pending_local is not None and self._try_local():
            return
        super()._refresh()


class SeparateQuotaSearch(RefinedCadenceSearch):
    def __init__(self, request):
        validate_request(request)
        super().__init__(request)

    def _refresh(self):
        if self.iteration >= self.request.max_evaluations or self._coordination_pending():
            return
        if (self.pending_local is not None and
                len(self.local_trace) < self.request.max_evaluations // 16 and self._try_local()):
            return
        # Parent controls its own global quota and cycle. A local trial does not
        # decrement that allowance, but every trial still consumes a real slot.
        CadencedModelSearch._refresh(self)
