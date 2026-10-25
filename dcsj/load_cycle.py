"""Steady-state duty energy from complete, unrounded motor load samples."""
import math
import hashlib
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field

SAMPLE_LOADS = (1.2, 1.1, 1., .9, .75, .6, .5, .4, .3)
METHOD = 'raw-input-power-linear-v1'
SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
NOTE = ('按固定频率、电压下各稳态负载段累计：耗电量 = Σ(P1 × 小时)，'
        '周期效率 = 总输出电量 / 总输入电量。原始采样间对输入功率线性插值，'
        '不使用绘图拟合，不外推；不计启停过渡与热积累。')


class LoadStage(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, strict=True)
    load_fraction: float = Field(ge=.3, le=1.2, description='轴输出功率 / 额定功率，0.3–1.2')
    hours: float = Field(gt=0, le=1000000, description='该稳态负载段的运行小时数')


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def evaluate(result, stages):
    """Return auditable energy or an explicit invalid result, never a rated fallback."""
    if not stages:
        return None
    profile = [s.model_dump() if isinstance(s, LoadStage) else dict(s) for s in stages]
    output = {'status': 'unavailable', 'method': METHOD, 'method_sha256': SHA256,
              'source': 'load_characteristic.series[0].points', 'note': NOTE,
              'profile': profile, 'input_energy_kwh': None, 'output_energy_kwh': None,
              'efficiency': None, 'total_hours': None, 'stages': [], 'sample_domain': [.3, 1.2]}

    def invalid(reason):
        return {**output, 'reason': reason}

    rating = result.get('inputs', {}).get('kW')
    if not finite(rating) or rating <= 0:
        return invalid('额定输出功率无效')
    curves = [c for c in result.get('curves', []) if c.get('id') == 'load_characteristic']
    if len(curves) != 1 or len(curves[0].get('series', [])) != 5:
        return invalid('缺少完整原始负载特性')
    series = curves[0]['series']
    if any(len(s.get('points', [])) != 9 for s in series):
        return invalid('负载特性必须包含全部 9 个原始采样点')
    for i, load in enumerate(SAMPLE_LOADS):
        for s in series:
            p = s['points'][i]
            if not finite(p.get('x')) or not math.isclose(p['x'], load, rel_tol=0, abs_tol=1e-6):
                return invalid('负载采样坐标不完整或顺序错误')
            if not finite(p.get('y')):
                return invalid('负载采样包含无效数值')
        power, slip, current, pf, eta = (s['points'][i]['y'] for s in series)
        if not (power > 0 and current > 0 and 0 < slip < 1 and 0 < pf <= 1 and 0 < eta <= 1):
            return invalid('负载采样的功率、电流、转差率或效率超出有效范围')
        # Raw values are stored in single precision by the recovered kernel.
        shaft = rating * series[0]['points'][i]['x']
        if power < shaft or not math.isclose(power * eta, shaft, rel_tol=2e-5, abs_tol=1e-7):
            return invalid('负载采样的输入功率与输出功率、效率不一致')

    samples = list(reversed([{'load_fraction': load, 'raw_load_fraction': series[0]['points'][i]['x'],
                             'input_power_kw': series[0]['points'][i]['y']}
                            for i, load in enumerate(SAMPLE_LOADS)]))
    rows = []
    for stage in profile:
        load, hours = stage.get('load_fraction'), stage.get('hours')
        if not finite(load) or not .3 <= load <= 1.2 or not finite(hours) or not 0 < hours <= 1000000:
            return invalid('负载须为 30%–120%，运行小时须为正数')
        exact = next((s for s in samples if s['load_fraction'] == load), None)
        if exact:
            weights = [{'load_fraction': load, 'weight': 1.}]
            power = exact['input_power_kw']
        else:
            low, high = next((a, b) for a, b in zip(samples, samples[1:])
                             if a['load_fraction'] < load < b['load_fraction'])
            fraction = (load-low['load_fraction'])/(high['load_fraction']-low['load_fraction'])
            weights = [{'load_fraction': low['load_fraction'], 'weight': 1-fraction},
                       {'load_fraction': high['load_fraction'], 'weight': fraction}]
            power = low['input_power_kw']*(1-fraction) + high['input_power_kw']*fraction
        shaft = rating*load
        rows.append({**stage, 'input_power_kw': power, 'output_power_kw': shaft,
                     'input_energy_kwh': power*hours, 'output_energy_kwh': shaft*hours,
                     'efficiency': shaft/power, 'basis': 'sample' if exact else 'linear', 'weights': weights})
    consumed = math.fsum(r['input_energy_kwh'] for r in rows)
    delivered = math.fsum(r['output_energy_kwh'] for r in rows)
    total_hours = math.fsum(r['hours'] for r in rows)
    if not all(finite(v) and v > 0 for v in (consumed, delivered, total_hours)):
        return invalid('负载周期累计值无效')
    return {**output, 'status': 'available', 'input_energy_kwh': consumed, 'output_energy_kwh': delivered,
            'efficiency': delivered/consumed, 'total_hours': total_hours, 'stages': rows, 'samples': samples}
