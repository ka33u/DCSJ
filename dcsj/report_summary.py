"""Shared first-page values derived only from the immutable calculation snapshot."""
from datetime import datetime, timezone, timedelta
import math


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
        target, result = p.get(target_key), v.get(result_key)
        target = target * factor if finite(target) else None
        result = result * factor if finite(result) else None
        delta = result - target if result is not None and target is not None else None
        rows.append((label, number(target), number(result), f'{delta:+.5g}' if delta is not None else '—', unit))
    curve = next((c for c in snapshot.get('curves', []) if c.get('id') == 'slip'), None)
    if curve and len(curve.get('series', [])) > 1:
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


def dimensions(p):
    data = dict(p)
    data['D2'] = p['Di1'] - 2 * p['g'] if finite(p.get('Di1')) and finite(p.get('g')) else None
    return cells(data, [('D1', 'D1 定子外径', 'mm'), ('Di1', 'Di1 定子内径', 'mm'),
                        ('D2', 'D2 转子外径', 'mm'), ('Di2', 'Di2 转子内径', 'mm'),
                        ('g', 'g 气隙', 'mm'), ('L', 'L 铁心长度', 'mm'),
                        ('Q1', 'Q1 定子槽数', ''), ('Q2', 'Q2 转子槽数', '')])


def windings(p, v):
    return cells(p, [('a', '并联路数 a', ''), ('ZZ', '每槽导体 ZZ', ''), ('Y1', '跨距 Y1', '槽'),
                     ('JJ', '并绕根数 JJ', ''), ('Djj', '线径 Djj', 'mm'), ('MM', '并绕根数 MM', ''),
                     ('Dmm', '线径 Dmm', 'mm'), ('sk', '斜槽比 sk', ''), ('Srr', '端环面积 Srr', 'mm²')]) + \
           cells(v, [('Sf', '槽满率 Sf', ''), ('R1a', '相电阻 R1a', 'Ω'), ('Lz', '半匝长 Lz', 'mm')])


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
