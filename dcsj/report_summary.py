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
    return rows


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
