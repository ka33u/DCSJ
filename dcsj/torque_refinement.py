"""Independent SM samples with stricter iteration and bounded peak refinement.

Original outputs and display curves are never mutated. Only the iteration
comparison in each copied VM state uses a new tolerance; all electrical
calculations still execute the recovered SATU/SKIN/SM instructions.
"""
import copy
import math
import struct
from collections import deque
from .numeric_vm import NumericKernelError
from .curves import sampled_torque_peak

KERNEL_SHA256 = 'c8aac2556ddc6d145f9a525414fab64b0e3e91b3078b0c2e44d8d265f8c4eb5d'
CHECKPOINT = 0x40154c
POINT_COMPLETE = 0x401ab9
COMPARISON = 0x401901
TOLERANCE_ADDRESS = 0x491048
TOLERANCE = 1e-5
MAX_SAMPLES = 257
MAX_POINT_STEPS = 250_000
NOTE = ('在 s=0.01–1 内直接调用原核，迭代电流倍数差收紧至 0.00001，'
        '各点使用同一初值；对采样局部峰值加密。有限采样不保证连续全局峰值，'
        '不代表实测精度，不替代原公式 TM1 或原曲线采样峰值。')


class SamplingFailure(NumericKernelError):
    def __init__(self, diagnostic):
        self.diagnostic = diagnostic
        super().__init__(diagnostic['message'])


def failure_diagnostic(slip, iterations, residual, steps, history, category):
    """Describe recorded iteration states without altering convergence decisions."""
    history = [{key: (None if isinstance(value, float) and not math.isfinite(value) else value)
                for key, value in row.items()} for row in history]
    if residual is not None and not math.isfinite(residual):
        residual = None
    keys = ('input_current', 'input_factor', 'current', 'saturation_input',
            'saturation_factor', 'interpolation_window')
    states = [tuple(row.get(k) for k in keys) for row in history]
    period = None
    if category == 'iteration_limit':
        for size in range(1, 5):
            tail = states[-3*size:]
            if len(tail) == 3*size and all(None not in row for row in tail) and tail[:size] == tail[size:2*size] == tail[2*size:]:
                period = size
                category = 'iteration_cycle'
                break
    windows = {row.get('interpolation_window') for row in history[-(period or 1):]}
    switching = period is not None and None not in windows and len(windows) > 1
    delta = f'{residual:.7g}' if residual is not None else '未取得'
    message = (f'加密转矩采样未收敛：s={slip:.7g}，迭代 {iterations} 次，'
               f'末次电流倍数差 {delta}（要求小于 {TOLERANCE:g}）')
    if period:
        message += f'；末段状态出现周期 {period} 的重复'
        if switching:
            message += '，同时存在饱和系数插值换段'
    elif category == 'instruction_budget':
        message += '；已达到单点指令预算'
    return {'category': category, 'message': message, 'slip': slip, 'iterations': iterations,
            'residual': residual, 'tolerance': TOLERANCE, 'steps': steps,
            'cycle_period': period, 'saturation_window_switch': switching,
            'history': history,
            'note': '数值求解失败不能据此判定电机性能不达标；本记录仅描述末段迭代，未更改原插值或放宽收敛条件。'}


def compact(result):
    record = result.get('curve_metrics', {}).get('refined_torque_peak')
    return {k: v for k, v in record.items() if k != 'samples'} if record else None


def f32(value):
    return struct.unpack('<f', struct.pack('<f', value))[0]


class Sampler:
    def __init__(self, checkpoint):
        if checkpoint is None or checkpoint.pc != CHECKPOINT or checkpoint.memory.uint(0x4aa150) != 1:
            raise NumericKernelError('缺少原转矩扫描的首点状态')
        instruction = checkpoint.program['code'].get(COMPARISON)
        if instruction != (0x401907, 'fcomp', [['m', None, None, 1, TOLERANCE_ADDRESS, 4]]):
            raise NumericKernelError('转矩迭代指令与已验证版本不一致')
        self.checkpoint = checkpoint

    def __call__(self, slip):
        slip = f32(slip)
        if not math.isfinite(slip) or not f32(.01) <= slip <= 1:
            raise NumericKernelError('加密采样转差率超出 0.01–1')
        vm = copy.deepcopy(self.checkpoint, {id(self.checkpoint.program): self.checkpoint.program})
        vm.memory.set_float(vm.reg('edi'), slip)
        start, residual, interpolation_window = vm.steps, None, None
        history = deque(maxlen=12)
        while vm.pc != POINT_COMPLETE:
            if vm.done or vm.steps - start >= MAX_POINT_STEPS or vm.pc in (0x401c71, 0x401c44, 0x4019d1):
                category = ('instruction_budget' if vm.steps-start >= MAX_POINT_STEPS else
                            'iteration_limit' if vm.pc == 0x4019d1 else 'incomplete')
                raise SamplingFailure(failure_diagnostic(slip, vm.memory.uint(0x4aa14c), residual,
                                                        vm.steps-start, history, category))
            if vm.pc == 0x41ebde:
                # Capture SATU's LAQ1 window before subsequent calls reuse its scratch state.
                interpolation_window = vm.memory.uint(0x4aa544)
            vm.steps += 1
            if vm.callback():
                continue
            instruction = vm.program['code'].get(vm.pc)
            if instruction is None:
                raise NumericKernelError('加密转矩采样遇到未知指令')
            if vm.pc == COMPARISON:
                residual = vm.st()
                history.append({'iteration': vm.memory.uint(0x4aa14c), 'residual': residual,
                                'input_current': vm.memory.f32(0x4aa154),
                                'input_factor': vm.memory.f32(0x4aa164),
                                'current': vm.memory.f32(0x4aa148),
                                'saturation_input': vm.memory.f32(0x4ab4bc),
                                'saturation_factor': vm.memory.f32(0x4af548),
                                'interpolation_window': interpolation_window})
                original = vm.memory.read(TOLERANCE_ADDRESS, 4)
                vm.memory.set_float(TOLERANCE_ADDRESS, TOLERANCE)
                try:
                    vm.execute(*instruction)
                finally:
                    # This literal is shared elsewhere. Never leave it changed.
                    vm.memory.write(TOLERANCE_ADDRESS, original)
            else:
                vm.execute(*instruction)
        current, torque = vm.memory.f32(0x4aa148), vm.memory.f32(vm.reg('ebx'))
        if (residual is None or not math.isfinite(residual) or residual >= f32(TOLERANCE)
                or not all(math.isfinite(v) and v >= 0 for v in (current, torque))):
            raise NumericKernelError('加密转矩采样包含无效或未收敛数值')
        return {'slip': slip, 'current': current, 'torque': torque, 'residual': residual,
                'iterations': vm.memory.uint(0x4aa14c), 'steps': vm.steps - start}


def refine(sample, slips):
    """Refine every observed local peak, retaining all samples and the budget."""
    samples, active = {}, None

    def at(x):
        nonlocal active
        x = f32(x)
        active = x
        if x not in samples:
            if len(samples) >= MAX_SAMPLES:
                raise NumericKernelError('加密峰值采样预算已用尽')
            row = sample(x)
            if row['slip'] != x or not math.isfinite(row['torque']) or row['torque'] < 0:
                raise NumericKernelError('加密峰值采样无效')
            samples[x] = row
        return samples[x]

    try:
        coarse = [at(x) for x in sorted(slips)]
        peaks = [i for i, row in enumerate(coarse)
                 if (i == 0 or row['torque'] >= coarse[i-1]['torque'])
                 and (i == len(coarse)-1 or row['torque'] >= coarse[i+1]['torque'])
                 and (i == 0 or i == len(coarse)-1 or row['torque'] > min(coarse[i-1]['torque'], coarse[i+1]['torque']))]
        brackets = []
        for index in peaks:
            low, high = coarse[max(0,index-1)]['slip'], coarse[min(len(coarse)-1,index+1)]['slip']
            original = [low, high]
            for _ in range(4):
                grid = [at(low+(high-low)*i/8) for i in range(9)]
                winner = max(range(9), key=lambda i: grid[i]['torque'])
                low, high = grid[max(0,winner-1)]['slip'], grid[min(8,winner+1)]['slip']
                if high-low <= .0002:
                    break
            brackets.append({'initial': original, 'final': [low, high]})
        winner = max(samples.values(), key=lambda row: row['torque'])
        return {'status': 'available', 'value': winner['torque'], 'slip': winner['slip'],
                'peak_sample': winner, 'brackets': brackets, 'samples': sorted(samples.values(), key=lambda r: -r['slip'])}
    except (NumericKernelError, ArithmeticError, ValueError, KeyError, struct.error) as error:
        return {'status': 'unavailable', 'value': None, 'slip': None, 'reason': str(error),
                'failed_slip': active,
                'diagnostic': getattr(error, 'diagnostic', None),
                'samples': sorted(samples.values(), key=lambda r: -r['slip'])}


def evaluate(checkpoint, curves, kernel_sha256):
    legacy = sampled_torque_peak(curves)
    info = {'status': 'unavailable', 'value': None, 'slip': None, 'unit': '倍',
            'method': 'independent-sm-tight-grid-v1', 'note': NOTE,
            'iteration_tolerance': TOLERANCE, 'maximum_samples': MAX_SAMPLES,
            'legacy_peak': legacy, 'samples': [], 'kernel_sha256': kernel_sha256}
    if kernel_sha256 != KERNEL_SHA256:
        return {**info, 'reason': '当前数值核尚未验证加密采样接口'}
    if legacy['status'] != 'available':
        return {**info, 'reason': '原始转矩采样不完整，无法开展峰值校核'}
    try:
        sampler = Sampler(checkpoint)
    except NumericKernelError as error:
        return {**info, 'reason': str(error)}
    curve = next(c for c in curves if c.get('id') == 'slip')
    result = {**info, **refine(sampler, [p['x'] for p in curve['series'][1]['points']])}
    result['sample_count'] = len(result['samples'])
    result['steps'] = sum(s['steps'] for s in result['samples']) + (result.get('diagnostic') or {}).get('steps', 0)
    result['domain'] = legacy['domain']
    result['delta_from_legacy'] = result['value']-legacy['value'] if result['status'] == 'available' else None
    return result
