"""Plot coordinates are separate from immutable numerical samples and their units."""
import math
import re
from bisect import bisect_right

COLORS = ['#26795e', '#385faa', '#b46a16', '#8d4d91', '#222222']


def quadratic_fit(xs, ys):
    """Least-squares trend used by the legacy load chart; coefficients in x units.

    Centering/scaling and orthogonal elimination avoid forming ill-conditioned
    normal equations. These are display estimates, never new kernel samples.
    """
    if len(xs) != len(ys) or len(xs) < 3 or not all(math.isfinite(v) for v in [*xs, *ys]):
        raise ValueError('二次趋势线至少需要三个有限采样点')
    center = sum(xs) / len(xs)
    scale = max(abs(x - center) for x in xs)
    if not scale:
        raise ValueError('趋势线横坐标不能全部相同')
    z = [(x - center) / scale for x in xs]
    columns = [[1.] * len(xs), z, [x * x for x in z]]
    q, r = [], [[0.] * 3 for _ in range(3)]
    for j, col in enumerate(columns):
        v = col[:]
        for i, basis in enumerate(q):
            r[i][j] = sum(a * b for a, b in zip(basis, v))
            v = [a - r[i][j] * b for a, b in zip(v, basis)]
        r[j][j] = math.sqrt(sum(a * a for a in v))
        if r[j][j] < 1e-12:
            raise ValueError('趋势线至少需要三个不同横坐标')
        q.append([a / r[j][j] for a in v])
    c = [sum(a * b for a, b in zip(col, ys)) for col in q]
    for i in range(2, -1, -1):
        c[i] = (c[i] - sum(r[i][j] * c[j] for j in range(i + 1, 3))) / r[i][i]
    return [c[0] - c[1] * center / scale + c[2] * center * center / scale ** 2,
            c[1] / scale - 2 * c[2] * center / scale ** 2, c[2] / scale ** 2]


def trend_points(xs, ys):
    c = quadratic_fit(xs, ys)
    return [{'x': i, 'y': c[0] + c[1] * (i / 100) + c[2] * (i / 100) ** 2} for i in range(20, 151)]


def natural_spline(points, queries):
    """Bounded natural cubic display interpolation, with no implicit extrapolation."""
    knots = sorted(points, key=lambda p: p['x'])
    xs, ys = [p['x'] for p in knots], [p['y'] for p in knots]
    if len(xs) < 2 or any(b <= a for a, b in zip(xs, xs[1:])):
        raise ValueError('样条至少需要两个不同横坐标')
    second, rhs = [0.] * len(xs), [0.] * len(xs)
    for i in range(1, len(xs) - 1):
        ratio = (xs[i] - xs[i - 1]) / (xs[i + 1] - xs[i - 1])
        divisor = ratio * second[i - 1] + 2
        second[i] = (ratio - 1) / divisor
        slope_delta = (ys[i + 1] - ys[i]) / (xs[i + 1] - xs[i]) - (ys[i] - ys[i - 1]) / (xs[i] - xs[i - 1])
        rhs[i] = (6 * slope_delta / (xs[i + 1] - xs[i - 1]) - ratio * rhs[i - 1]) / divisor
    for i in range(len(xs) - 2, -1, -1):
        second[i] = second[i] * second[i + 1] + rhs[i]
    result = []
    for x in queries:
        if x < xs[0] or x > xs[-1]:
            raise ValueError('样条显示不允许隐式外推')
        i = min(max(bisect_right(xs, x) - 1, 0), len(xs) - 2)
        h = xs[i + 1] - xs[i]
        a, b = (xs[i + 1] - x) / h, (x - xs[i]) / h
        y = a * ys[i] + b * ys[i + 1] + ((a ** 3 - a) * second[i] + (b ** 3 - b) * second[i + 1]) * h * h / 6
        result.append({'x': x, 'y': y})
    return result


def slip_plot(rows):
    complete = len(rows) == 51
    series = []
    for col, key, name, color in [(1, 'current', '电流 / 额定相电流', '#e00000'), (2, 'torque', '转矩 / 额定转矩', '#222222')]:
        # The legacy graph used the first 49 samples and then an explicit origin.
        # Raw samples, including the discarded near-zero ones, remain in series.
        points = [{'x': r[0], 'y': r[col]} for r in rows]
        if complete:
            knots = [{'x': 1 - i / 50, 'y': rows[i][col]} for i in range(49)] + [{'x': 0., 'y': 0.}]
            points = natural_spline(knots, [1 - i / 100 for i in range(101)])
        series.append({'axis': key, 'name': name, 'color': color, 'points': points})
    return {'x_min': 0, 'x_max': 1, 'x_reverse': True, 'x_label': '转差率 s', 'aspect_ratio': 1, 'x_ticks': 10, 'y_ticks': 10,
            'y_axes': [{'id': 'current', 'label': '电流倍数', 'short_label': 'I 倍数', 'min': 0, 'max': 10},
                       {'id': 'torque', 'label': '转矩倍数', 'short_label': 'T 倍数', 'min': 0, 'max': 5}],
            'series': series, 'method': 'legacy-knots-natural-cubic',
            'note': ('沿用旧版前 49 点及显示原点 (0,0)，使用有界自然三次样条；原点不是计算结果。原版样条存在边界索引问题，平滑细节可能有微小差异。'
                     if complete else '采样未完整收敛，仅显示实际返回的计算点。')}


def plot_data(curve):
    """Normalize both versioned plot metadata and pre-existing saved calculations."""
    source = curve.get('plot') or {}
    series = source.get('series', curve.get('series', []))
    points = [p for s in series for p in s.get('points', [])]
    if not points:
        return None
    if not all(isinstance(p.get(k), (int, float)) and math.isfinite(p[k]) for p in points for k in ('x', 'y')):
        raise ValueError('曲线数据包含非有限数值')
    xmin = source.get('x_min', min(p['x'] for p in points))
    xmax = source.get('x_max', max(p['x'] for p in points))
    if xmax == xmin:
        xmax = xmin + 1
    axes = source.get('y_axes') or [{'id': 'y', 'label': curve.get('y_label', ''),
            'min': min(0, min(p['y'] for p in points)), 'max': max(p['y'] for p in points)}]
    axes = [dict(a) for a in axes]
    for axis in axes:
        if axis['max'] == axis['min']:
            axis['max'] += 1
        if not all(math.isfinite(axis[k]) for k in ('min', 'max')) or axis['max'] < axis['min']:
            raise ValueError('曲线纵轴范围无效')
    if not math.isfinite(xmin) or not math.isfinite(xmax) or xmin >= xmax:
        raise ValueError('曲线横轴范围无效')
    ids = {a['id'] for a in axes}
    normalized = []
    for i, item in enumerate(series):
        color = item.get('color', COLORS[i % len(COLORS)])
        if not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
            color = COLORS[i % len(COLORS)]
        axis = item.get('axis', axes[0]['id'])
        if axis not in ids:
            raise ValueError('曲线引用未知纵轴')
        normalized.append({**item, 'color': color, 'axis': axis})
    return {'series': normalized, 'y_axes': axes, 'x_min': xmin, 'x_max': xmax,
            'x_reverse': bool(source.get('x_reverse')), 'x_label': source.get('x_label', curve.get('x_label', '')),
            'aspect_ratio': source.get('aspect_ratio') if isinstance(source.get('aspect_ratio'), (int, float)) and .5 <= source['aspect_ratio'] <= 4 else None,
            'x_ticks': min(20, max(2, int(source.get('x_ticks', 4)))), 'y_ticks': min(20, max(2, int(source.get('y_ticks', 4)))),
            'note': source.get('note', '')}


def material_plot(points, kind, h_unit='A/m', loss_unit='kernel_native', legacy_origin=False):
    """Preserve the original editor's independent B1 and B2 grids (G, A/cm)."""
    factor = .01 if kind == 'bh' and h_unit == 'A/m' else 1
    display = [{'x': p['y'] * factor, 'y': p['x'] * 10000} for p in points]
    if legacy_origin and display and display[0] != {'x': 0, 'y': 0}:
        display.insert(0, {'x': 0, 'y': 0})
    xlabel = 'H1 (A/cm)' if kind == 'bh' else ('Pfe（原表数值）' if loss_unit == 'kernel_native' else 'Pfe (' + loss_unit + ')')
    return {'x_min': 0, 'x_max': max((p['x'] for p in display), default=1) or 1,
            'x_label': xlabel, 'aspect_ratio': 802 / 567, 'x_ticks': 8, 'y_ticks': 9 if legacy_origin else 8,
            'y_axes': [{'id': 'b', 'label': 'B1 (G)' if kind == 'bh' else 'B2 (G)',
                        'min': 0, 'max': 21150 if legacy_origin else (max((p['y'] for p in display), default=1) or 1)}],
            'series': [{'name': 'B-H' if kind == 'bh' else 'B-P', 'axis': 'b', 'color': '#168016', 'points': display}],
            'note': 'B1 与 B2 分别采用各自的采样磁密。' + ('沿用旧版连到原点的显示方式；原点不计入材料数据。' if legacy_origin else '') + ('旧版损耗轴标作 W/cm³，物理单位仍待核验。' if kind == 'loss' and loss_unit == 'kernel_native' else '')}
