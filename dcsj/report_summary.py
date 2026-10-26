"""Shared first-page values derived only from the immutable calculation snapshot."""
from datetime import datetime, timezone, timedelta
import math
from .wire_selection import recorded


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def number(value, digits=6):
    if value is None:
        return '—'
    return f'{value:.{digits}g}' if finite(value) else str(value)


def local_time(value):
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
    except (ValueError, TypeError, AttributeError):
        return str(value)


CURVE_NOTE = '平滑曲线用于观察趋势；默认校核使用 IST1、TST1、TM1。寻优可另选完整转矩采样的峰值约束，不使用平滑插值值。'

CYCLE_HEADERS = ['负载 %', '小时', '输入 kW', '输出 kW', '耗电 kWh', '取值']
MANUFACTURING_HEADERS = ['变量', '允许规格', '本次输入', '规格匹配']
TOLERANCE_HEADERS = ['情景', '输入尺寸 mm', '采样目标指标', '校核结果', '原始快照 ID']
TOLERANCE_NOTE = '常规指标、性能曲线和负载周期为名义尺寸结果；公差寻优目标取最不利采样。'


def robustness_summary(snapshot):
    record = snapshot.get('optimization', {}).get('robustness')
    if not record:
        return None
    specs = record.get('objective_specs', {})
    objectives = [[specs.get(k, {}).get('label', k), number(v, 8), record['worst_samples'].get(k)]
                  for k, v in record['objectives'].items()]
    ranges = [[k, number(r['minimum'], 8), number(r['maximum'], 8), '是' if r['complete'] else '否']
              for k, r in record['metric_ranges'].items()]
    keys = list(dict.fromkeys([t['key'] for t in record['tolerances']] + ['hr0']))
    rows = [[f"{s['index']} · { {'recheck':'复算 · ', 'retained':'继承情景 · '}.get(s.get('phase'),'')}{s['label']}",
             '；'.join(f"{k}={number(s['parameters'].get(k),8)}" for k in keys),
             '；'.join(f"{o['metric']}={number(s.get('values',{}).get(o['metric']),8)}" for o in specs.values()),
             s.get('error') or '；'.join(s.get('violations', [])) or '满足约束', s.get('calculation_id') or '—']
            for s in record['samples']]
    state = '未完成' if record['status']=='incomplete' else '存在计算失败' if record['status']=='failed' else '全部采样满足约束' if record['feasible'] else '存在约束不满足'
    tolerances = '；'.join(f"{t['key']} ± {number(t['delta'],8)} mm" for t in record['tolerances'])
    gap = '气隙偏差时保持槽颈底位置，hr0 反向变化' if record['gap_mode']=='fixed_neck_bottom' else '气隙偏差时保持名义 hr0'
    return {'title':'补充采样复核' if record.get('verification') else '复核情景参与搜索' if record.get('feedback') else '公差情景评估',
            'text':f"{tolerances}；{gap}。已评估 {record['evaluated_samples']}/{record['expected_samples']} 个情景 · {state}。",
            'note':record['note'], 'objectives':objectives, 'ranges':ranges, 'rows':rows}


def manufacturing_summary(snapshot):
    record = snapshot.get('optimization', {}).get('manufacturing')
    if not record:
        return None
    rows = [[r['key'], ' / '.join(number(x, 9) for x in r['allowed']), number(r['actual'], 9),
             '符合' if r['passed'] else '不符合'] for r in record['numeric_sets']]
    gap = record.get('rotor_gap')
    text = '' if not gap else (
        f"保持槽颈底直径 {number(gap['reference_neck_bottom_mm'])} mm；"
        f"本次 D2={number(gap['rotor_outer_mm'])} mm，g={number(gap['gap_mm'])} mm，"
        f"hr0={number(gap['hr0_mm'])} mm，槽颈底={number(gap['neck_bottom_mm'])} mm。"
        + gap['formula'] + ('；联动符合。' if gap['passed'] else '；联动不符合。'))
    return {'rows': rows, 'text': text,
            'note': '规格清单由任务指定；匹配原库单精度存储值时保留原始输入。'}


def material_price_summary(snapshot):
    record = snapshot.get('optimization', {}).get('material_pricing')
    if not record:
        return None
    separate = record['method'] == 'per-material-cfe-v1'
    mode = '按牌号分别定价' if separate else '所有候选牌号统一定价'
    prices = record['prices'] if separate else {record['material']: record['cfe']}
    return {'text': f"{mode}；本次材料 {record['material']}，Cfe={number(record['cfe'],8)} /kg；"
                    f"计算材料成本 CostFx={number(snapshot['values'].get('CostFx'),8)}。" + record['basis'] + '。',
            'rows': [[name,number(price,8),'是' if name==record['material'] else ''] for name,price in prices.items()]}


def cycle_summary(snapshot):
    """Read stored period energy; never derive a new result for an old report."""
    cycle = snapshot.get('optimization', {}).get('load_cycle')
    if not cycle:
        return None
    if cycle['status'] != 'available':
        return {'text': cycle.get('reason', '没有有效的周期能耗结果'), 'note': cycle.get('note', ''), 'rows': []}
    text = (f"运行 {number(cycle['total_hours'])} 小时 · 输入 {number(cycle['input_energy_kwh'], 8)} kWh · "
            f"输出 {number(cycle['output_energy_kwh'], 8)} kWh · 周期效率 {number(cycle['efficiency']*100)}%")
    rows = [[number(s['load_fraction']*100), number(s['hours']), number(s['input_power_kw']),
             number(s['output_power_kw']), number(s['input_energy_kwh']),
             '原始采样' if s['basis'] == 'sample' else '线性插值'] for s in cycle['stages']]
    return {'text': text, 'note': cycle.get('note', ''), 'rows': rows}


def targets(snapshot):
    p, v = snapshot['inputs'], snapshot['values']
    specs = [('效率 η', 'ATA', 'ATA1', 100, '%'),
             ('功率因数 cosφ', 'PF', 'PF1', 1, '—'),
             ('启动电流倍数', 'IST', 'IST1', 1, '倍'),
             ('启动转矩倍数', 'TST', 'TST1', 1, '倍'),
             ('最大转矩（原公式）', 'TM', 'TM1', 1, '倍')]
    if finite(p.get('_wire_target')) and p['_wire_target'] > 0:
        specs.append(('槽满率', '_wire_target', 'Sf', 100, '%'))
    rows = []
    for label, target_key, result_key, factor, unit in specs:
        # Preserve targets recorded in 0.7.0 snapshots. New calculations only
        # contain the original ATA/PF/IST/TST/TM inputs.
        target, result = p.get(target_key + '_req', p.get(target_key)), v.get(result_key)
        target = target * factor if finite(target) else None
        result = result * factor if finite(result) else None
        delta = result - target if result is not None and target is not None else None
        rows.append((label, number(target), number(result), f'{delta:+.5g}' if delta is not None else '—', unit))
    curve = next((c for c in snapshot.get('curves', []) if c.get('id') == 'slip'), None)
    recorded_peak = snapshot.get('curve_metrics', {}).get('torque_peak')
    if recorded_peak is not None and curve:
        target = next((c for c in snapshot.get('optimization', {}).get('constraints', []) if c['metric'] == 'TM_curve'), {})
        bounds = [symbol + number(target[key]) for key, symbol in [('minimum', '≥'), ('maximum', '≤')] if target.get(key) is not None]
        rows.append(('转矩曲线采样峰值', ' / '.join(bounds) or '—', number(recorded_peak.get('value')),
                     's=' + number(recorded_peak['slip'], 4) if recorded_peak.get('status') == 'available' else '采样无效', '倍'))
    elif curve and len(curve.get('series', [])) > 1:
        points = [p for p in curve['series'][1].get('points', []) if finite(p.get('x')) and finite(p.get('y'))]
        if points:
            peak = max(points, key=lambda p: p['y'])
            rows.append(('转矩曲线采样峰值', '—', number(peak['y']), 's=' + number(peak['x'], 4), '倍'))
    for key, metric, label in TORQUE_MODES:
        refined = snapshot.get('curve_metrics', {}).get(key)
        if not refined:
            continue
        target = next((c for c in snapshot.get('optimization', {}).get('constraints', []) if c['metric'] == metric), {})
        bounds = [symbol + number(target[key]) for key, symbol in [('minimum', '≥'), ('maximum', '≤')] if target.get(key) is not None]
        rows.append((label, ' / '.join(bounds) or '—', number(refined.get('value')),
                     's=' + number(refined['slip'], 5) if refined.get('status') == 'available' else '采样无效', '倍'))
    return rows


TORQUE_MODES = [('refined_torque_peak', 'TM_refined', '加密转矩峰值'),
                ('continuous_torque_peak', 'TM_continuous', '连续饱和插值峰值（试验）')]


def torque_summary(snapshot, key='refined_torque_peak'):
    refined = snapshot.get('curve_metrics', {}).get(key)
    if not refined:
        return None
    text = ('峰值 ' + number(refined['value'], 8) + ' 倍 · s=' + number(refined['slip'], 7)
            + ' · 相对原采样峰值差 ' + number(refined.get('delta_from_legacy'), 8) + ' 倍'
            if refined['status'] == 'available' else refined.get('reason', '加密采样未完成'))
    diagnostic = refined.get('diagnostic') or {}
    continuous = key == 'continuous_torque_peak'
    return {'title': next(label for k, _, label in TORQUE_MODES if k == key) + '校核',
            'headers': TORQUE_HEADERS + (['第二状态差'] if continuous else []),
            'text': text, 'note': refined.get('note', ''), 'diagnostic_note': diagnostic.get('note', ''),
            'diagnostic_rows': [[str(r['iteration']), number(r.get('input_current'), 8),
                                 number(r.get('current'), 8), number(r.get('residual'), 7),
                                 number(r.get('saturation_input'), 8), number(r.get('saturation_factor'), 8),
                                 str(r['interpolation_window']) if r.get('interpolation_window') is not None else '—']
                                for r in diagnostic.get('history', [])],
            'rows': [[number(r['slip'], 7), number(r['torque'], 8), number(r['current'], 8),
                      str(r['iterations']), number(r['residual'], 5)] +
                     ([number(r.get('factor_residual'), 5)] if continuous else []) for r in refined.get('samples', [])]}


TORQUE_HEADERS = ['转差率 s', '转矩倍数', '电流倍数', '迭代次数', '末次电流倍数差']
TORQUE_DIAGNOSTIC_HEADERS = ['迭代', '输入电流倍数', '输出电流倍数', '电流倍数差', '饱和插值输入', '饱和系数', '插值段']


def cells(data, specs):
    return [(label, number(data.get(key)), unit) for key, label, unit in specs]


def ratings(p):
    return cells(p, [('kW', '额定功率', 'kW'), ('p', '极数', ''), ('f', '频率', 'Hz'),
                     ('Ufai', '相电压', 'V'), ('Jiefa', '接法', ''), ('Jueyuan', '绝缘等级', ''),
                     ('Paihao', '硅钢牌号', ''), ('kfe', '迭压系数', ''), ('czn', '绕组层数', '')])


def slot_rows(snapshot):
    statuses = {'matched':'已带入尺寸', 'modified':'尺寸已修改', 'reference_only':'仅图号参考', 'manual':'手工参数'}
    rows = []
    for item in snapshot.get('slots', {}).get('entries', []):
        area = item['area']
        name = item['name'] + (f" · v{item['revision']}" if item.get('revision') else '')
        reference = item.get('document') or {}
        rows.append(('定子' if item['side'] == 'stator' else '转子', name, item.get('drawing') or '—',
                     statuses.get(item['status'], item['status']),
                     f"{area['value_mm2']:.3f}" if area['value_mm2'] is not None else '未计算',
                     f"{reference['reference_area_mm2']:.3f}" if reference.get('reference_area_mm2') is not None else '—'))
    return rows


SLOT_HEADERS = ['侧别', '槽型 / 版本', '冲片图号', '使用状态', '本次面积 mm²', '图纸面积 mm²']
SLOT_AREA_NOTE = '主程序为含槽口的单槽几何面积（不扣绝缘，圆弧近似）；TX 定子面积取原核输出。图纸面积仅供参考。'


def dimensions(p):
    data = dict(p)
    data['D2'] = p['Di1'] - 2 * p['g'] if finite(p.get('Di1')) and finite(p.get('g')) else None
    return cells(data, [('D1', 'D1 定子外径', 'mm'), ('Di1', 'Di1 定子内径', 'mm'),
                        ('D2', 'D2 转子外径', 'mm'), ('Di2', 'Di2 转子内径', 'mm'),
                        ('g', 'g 气隙', 'mm'), ('L', 'L 铁心长度', 'mm'),
                        ('Q1', 'Q1 定子槽数', ''), ('Q2', 'Q2 转子槽数', '')])


def windings(snapshot):
    p, v = snapshot['inputs'], snapshot['values']
    # Do not put pre-selection wire inputs beside a post-selection fill factor.
    used = recorded(snapshot)['used'] or dict.fromkeys(('JJ', 'Djj', 'MM', 'Dmm'))
    data = {**p, **used}
    return cells(data, [('a', '并联路数 a', ''), ('ZZ', '每槽导体 ZZ', ''), ('Y1', '跨距 Y1', '槽'),
                     ('JJ', '采用根数 JJ', ''), ('Djj', '采用线径 Djj', 'mm'), ('MM', '采用根数 MM', ''),
                     ('Dmm', '采用线径 Dmm', 'mm'), ('sk', '斜槽比 sk', ''), ('Srr', '端环面积 Srr', 'mm²')]) + \
           cells(v, [('Sf', '槽满率 Sf', ''), ('R1a', '相电阻 R1a', 'Ω'), ('Lz', '半匝长 Lz', 'mm')])


def wire_note(snapshot):
    wire = recorded(snapshot)
    if wire['mode'] != 'target_fill':
        return ''
    p = wire['input']
    submitted = '；'.join(f'{count}={number(p.get(count))} / {diameter}={number(p.get(diameter))} mm'
                         for count, diameter in (('JJ', 'Djj'), ('MM', 'Dmm')))
    if wire['used'] is None:
        return '旧记录未保存反算线规，采用规格留空；重新计算后可获取。原输入（未采用）：' + submitted + '。'
    return '按目标槽满率反算线规；原输入（未采用）：' + submitted + '。根数为 0 的导线组不参与计算。'


def performance(v):
    return cells(v, [('I1', '线电流 I1', 'A'), ('n', '转速 n', 'r/min'), ('Sn', '转差率 Sn', ''),
                     ('Tn', '额定转矩 Tn', 'N·m'), ('IstA', '启动电流 Ist', 'A'), ('P1', '输入功率 P1', 'kW'),
                     ('Pcu', '定子铜耗', 'W'), ('Pal', '转子铝耗', 'W'), ('Pfe', '铁耗', 'W'),
                     ('Pfw1', '机械损耗', 'W'), ('Ps1', '杂散损耗', 'W'), ('P0', '空载功率 P0', 'W'),
                     ('Bg', '气隙磁密 Bg', 'G'), ('Bts', '定子齿磁密', 'G'), ('Bys', '定子轭磁密', 'G'),
                     ('Byr', '转子轭磁密', 'G'), ('A11', '线负荷 A11', 'A/cm'), ('Aj', '热负荷 Aj', ''),
                     ('J1', '定子电密 J1', ''), ('J2', '导条电密 J2', ''), ('Jr', '端环电密 Jr', ''),
                     ('Gcu', '铜重量', 'kg'), ('Gfe', '硅钢片重量', 'kg'), ('Gal', '铝重量', 'kg')])


def grid(items, columns=3):
    """Pairs of label/value cells; the value carries its unit for narrow tables."""
    pairs = [(label, value + (' ' + unit if unit else '')) for label, value, unit in items]
    return [tuple(cell for pair in (pairs[i:i + columns] + [('', '')] * (columns - len(pairs[i:i + columns]))) for cell in pair)
            for i in range(0, len(pairs), columns)]
