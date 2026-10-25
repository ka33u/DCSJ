"""Durable local optimization queue, cooperative cancellation and crash recovery."""
import json
import logging
import threading
import time
import uuid

from . import calculations, load_cycle
from .engine import CalculationError, CalculationUnavailableError, engine_status
from .execution import BusyError, compute
from .optimization import (SearchInput, Search, assess, in_region, metrics, objective_keys,
                           objective_values, violation_score, pareto_front, SOLVER_SHA256)
from .parameters import InputError, RETIRED_INPUT_KEYS
from .curves import sampled_torque_peak
from .storage import ConflictError, NotFoundError, audit, connect, database_path, dumps, now

TERMINAL = {'succeeded', 'failed', 'cancelled', 'interrupted'}
LEASE_SECONDS = 30
logger = logging.getLogger(__name__)


def _decode(row):
    data = dict(row)
    data['request'] = json.loads(data['request'])
    data.pop('owner', None)
    return data


def get(identity, *, database=None, candidates=True):
    with connect(database) as db:
        row = db.execute('SELECT * FROM optimization_jobs WHERE id=?', (identity,)).fetchone()
        if row is None:
            raise NotFoundError('寻优任务不存在')
        result = _decode(row)
        if candidates:
            result['candidates'] = []
            for row in db.execute("SELECT c.*, json_extract(s.result,'$.optimization') AS evaluation "
                                  'FROM optimization_candidates c LEFT JOIN calculations s ON s.id=c.calculation_id '
                                  'WHERE c.job_id=? ORDER BY c.ordinal', (identity,)):
                item = dict(row)
                for key in ('parameters', 'violations'):
                    item[key] = json.loads(item[key])
                item['feasible'] = bool(item['feasible'])
                evaluation = json.loads(item.pop('evaluation') or '{}')
                item['objectives'] = evaluation.get('objectives', {})
                item['violation_score'] = evaluation.get('violation_score')
                item['constraint_values'] = evaluation.get('constraint_values', {})
                item['torque_peak'] = evaluation.get('torque_peak')
                item['load_cycle'] = evaluation.get('load_cycle')
                result['candidates'].append(item)
            keys = [key for key in (result['request']['objective'], result['request'].get('secondary_objective')) if key]
            result['pareto_ids'] = [c['calculation_id'] for c in pareto_front(result['candidates'], keys)] if len(keys) > 1 else []
            result['feasible_count'] = sum(c['feasible'] for c in result['candidates'])
    result['progress'] = result['evaluated'] / result['budget']
    return result


def list_jobs():
    with connect() as db:
        return [dict(row) for row in db.execute(
            'SELECT id,name,status,evaluated,budget,best_id,message,created_at,updated_at '
            'FROM optimization_jobs ORDER BY created_at DESC LIMIT 100')]


def create(request, *, database=None):
    if engine_status()['status'] == 'unavailable':
        raise CalculationUnavailableError('寻优需要先安装本地计算资源。')
    identity, timestamp = str(uuid.uuid4()), now()
    with connect(database) as db:
        db.execute('BEGIN IMMEDIATE')
        pending = db.execute("SELECT count(*) FROM optimization_jobs WHERE status IN ('queued','running','cancelling')").fetchone()[0]
        if pending >= 8:
            raise BusyError('待处理寻优任务已达 8 个，请等待或取消现有任务。')
        db.execute('INSERT INTO optimization_jobs(id,name,request,status,created_at,updated_at,budget) '
                   "VALUES (?,?,?,'queued',?,?,?)", (identity, request.name, dumps(request.model_dump()), timestamp, timestamp, request.max_evaluations))
        audit(db, 'optimization.created', identity, {'budget': request.max_evaluations, 'objective': request.objective})
    return get(identity, database=database)


def cancel(identity, *, database=None):
    with connect(database) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT status FROM optimization_jobs WHERE id=?', (identity,)).fetchone()
        if row is None:
            raise NotFoundError('寻优任务不存在')
        if row['status'] not in TERMINAL:
            state = 'cancelled' if row['status'] == 'queued' else 'cancelling'
            message = '已取消排队任务。' if state == 'cancelled' else '取消已请求，当前一次计算结束后停止。'
            db.execute('UPDATE optimization_jobs SET status=?,cancel_requested=1,message=?,updated_at=? WHERE id=?',
                       (state, message, now(), identity))
            audit(db, 'optimization.cancel_requested', identity)
    return get(identity, database=database)


def saved_request(document):
    """Reopen old jobs without reviving the removed comparison-only inputs."""
    return SearchInput.model_validate({'algorithm': 'stratified_v1', **document, 'parameters': {
        key: value for key, value in document['parameters'].items() if key not in RETIRED_INPUT_KEYS}})


def retry(identity):
    previous = get(identity)
    if previous['status'] not in TERMINAL:
        raise ConflictError('只能重新运行已结束的任务。')
    request = saved_request(previous['request'])
    request.name = (request.name[:110] + ' · 重试')
    return create(request)


class Worker:
    def __init__(self, database=None):
        self.database = database or database_path()
        self.owner = str(uuid.uuid4())
        self.stop = threading.Event()
        self.thread = None
        self.heartbeat_thread = None

    def touch(self):
        with connect(self.database) as db:
            db.execute('INSERT INTO optimization_workers VALUES (?,?) ON CONFLICT(id) DO UPDATE SET heartbeat=excluded.heartbeat',
                       (self.owner, time.time()))

    def claim(self):
        with connect(self.database) as db:
            db.execute('BEGIN IMMEDIATE')
            stale = db.execute("SELECT id FROM optimization_jobs WHERE status IN ('running','cancelling') AND "
                               '(owner IS NULL OR owner NOT IN (SELECT id FROM optimization_workers WHERE heartbeat>?))',
                               (time.time() - LEASE_SECONDS,)).fetchall()
            for row in stale:
                db.execute("UPDATE optimization_jobs SET status='interrupted',message=?,updated_at=? WHERE id=?",
                           ('上次执行进程已退出或失去心跳；已完成的候选保留，可重新运行。', now(), row['id']))
                audit(db, 'optimization.interrupted', row['id'])
            # One search at a time per local database, even if a second app instance starts.
            if db.execute("SELECT 1 FROM optimization_jobs WHERE status IN ('running','cancelling') LIMIT 1").fetchone():
                return None
            row = db.execute("SELECT id FROM optimization_jobs WHERE status='queued' ORDER BY created_at,id LIMIT 1").fetchone()
            if row is None:
                return None
            db.execute("UPDATE optimization_jobs SET status='running',owner=?,updated_at=? WHERE id=?", (self.owner, now(), row['id']))
            audit(db, 'optimization.started', row['id'])
            return row['id']

    def finish(self, identity, status, message):
        with connect(self.database) as db:
            db.execute('UPDATE optimization_jobs SET status=?,message=?,updated_at=? WHERE id=? AND owner=? '
                       "AND status IN ('running','cancelling')", (status, message, now(), identity, self.owner))
            audit(db, 'optimization.' + status, identity)

    def run_job(self, identity):
        job = get(identity, database=self.database)
        request = saved_request(job['request'])
        search = Search(request)
        best_id, completed = None, 0
        resource_identity = None
        for ordinal in range(1, request.max_evaluations + 1):
            current = get(identity, database=self.database, candidates=False)
            if self.stop.is_set():
                self.finish(identity, 'interrupted', '服务已停止，保留已完成候选；可重新运行任务。'); return
            if current['status'] not in {'running', 'cancelling'}:
                return
            if current['cancel_requested']:
                self.finish(identity, 'cancelled', '已按请求停止，保留已完成候选。'); return
            parameters = search.candidate()
            if parameters is None:
                self.finish(identity, 'succeeded' if completed else 'failed',
                            '搜索已结束：未生成新的不重复候选。' + ('已有满足约束的候选。' if best_id else '没有找到满足全部约束的候选。')); return
            error, objective, calculation_id, violations = None, None, None, []
            try:
                result = compute(parameters, wait=True, stop_event=self.stop)
                fingerprint = tuple(result['provenance'].get(key) for key in
                                    ('kernel_sha256', 'wrapper_sha256', 'resource_package'))
                if resource_identity is not None and fingerprint != resource_identity:
                    self.finish(identity, 'interrupted', '计算资源在寻优期间发生变化，已停止；先前候选保留，请按新资源重新运行。'); return
                resource_identity = fingerprint
                cycle = load_cycle.evaluate(result, request.load_cycle)
                values = metrics(result, cycle=cycle)
                violations = assess(request, values)
                if not in_region(request, parameters):
                    violations.append('基准方案位于指定搜索范围或步长网格之外，仅作为参考。')
                objective, improved = search.consider(parameters, values, not violations)
                result['provenance']['optimization_job_id'] = identity
                result['optimization'] = {'algorithm': request.algorithm,
                                         'solver_sha256': SOLVER_SHA256,
                                         'objectives': objective_values(request, values),
                                         'violation_score': violation_score(request, values),
                                         'constraint_values': {c.metric: values.get(c.metric) for c in request.constraints},
                                         'constraints': [c.model_dump() for c in request.constraints],
                                         'torque_peak': sampled_torque_peak(result.get('curves', [])),
                                         'load_cycle': cycle}
                saved = calculations.save(result, f'{request.name} / {ordinal}', database=self.database)
                calculation_id = saved['calculation_id']
                completed += 1
                if improved:
                    best_id = calculation_id
            except (InputError, CalculationError, BusyError) as exc:
                error = str(exc)[:2000]
                violations = ['输入无效或数值核未完成此候选。']
            with connect(self.database) as db:
                # A worker that lost its lease cannot publish into a job reclaimed by another process.
                row = db.execute('SELECT owner,status FROM optimization_jobs WHERE id=?', (identity,)).fetchone()
                if row['owner'] != self.owner or row['status'] not in {'running', 'cancelling'}:
                    return
                db.execute('INSERT INTO optimization_candidates VALUES (?,?,?,?,?,?,?,?)',
                           (identity, ordinal, dumps(parameters), calculation_id, objective,
                            int(not violations and error is None), dumps(violations), error))
                db.execute('UPDATE optimization_jobs SET evaluated=?,best_id=?,updated_at=? WHERE id=?',
                           (ordinal, best_id, now(), identity))
        current = get(identity, database=self.database, candidates=False)
        if self.stop.is_set():
            self.finish(identity, 'interrupted', '服务已停止，结果保留。')
        elif current['cancel_requested']:
            self.finish(identity, 'cancelled', '已按请求停止，结果保留。')
        else:
            self.finish(identity, 'succeeded' if completed else 'failed',
                        ('搜索预算已完成；' + ('最优候选仅限本次已评估且满足约束的方案。' if best_id else '没有找到满足全部约束的候选。'))
                        if completed else '所有候选均计算失败，请检查基准设计和变量范围。')

    def _heartbeat(self):
        while not self.stop.wait(2):
            try:
                self.touch()
            except Exception:
                logger.exception('Optimization worker heartbeat failed')

    def _loop(self):
        while not self.stop.is_set():
            identity = None
            try:
                identity = self.claim()
                if identity:
                    self.run_job(identity)
                    continue
            except Exception as error:
                logger.exception('Optimization worker failed')
                if identity:
                    self.finish(identity, 'failed', ('任务异常：' + str(error))[:2000])
            self.stop.wait(.5)
        with connect(self.database) as db:
            db.execute("UPDATE optimization_jobs SET status='interrupted',message=?,updated_at=? WHERE owner=? AND status IN ('running','cancelling')",
                       ('服务已停止，可重新运行任务。', now(), self.owner))
            db.execute('DELETE FROM optimization_workers WHERE id=?', (self.owner,))

    def start(self):
        self.touch()
        self.heartbeat_thread = threading.Thread(target=self._heartbeat, daemon=True, name='dcsj-job-heartbeat')
        self.thread = threading.Thread(target=self._loop, daemon=True, name='dcsj-optimizer')
        self.heartbeat_thread.start(); self.thread.start()
        return self

    def close(self):
        self.stop.set()
        if self.heartbeat_thread:
            self.heartbeat_thread.join(3)
        if self.thread:
            self.thread.join(5)
