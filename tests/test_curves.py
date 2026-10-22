"""Synthetic regression cases; no model or material library contents."""
from copy import deepcopy
import re

import pytest

from dcsj.curves import material_plot, natural_spline, plot_data, quadratic_fit, slip_plot, trend_points
from dcsj.reports import curve_svg
from dcsj.engine import extract_curves


def test_load_arrays_are_independent_and_keep_original_units():
    xs = [.3, .4, .5, .6, .75, .9, 1., 1.1, 1.2]
    eta = [.72 + .1 * x for x in xs]
    pf = [.55 + .2 * x for x in xs]
    power = [12 * x / e for x, e in zip(xs, eta)]
    current = [p * 1000 / (3 * 230 * f) for p, f in zip(power, pf)]
    slip = [.04 * x for x in xs]
    arrays = [(0x4AB89C, xs), (0x4AB830, eta), (0x4AB80C, pf),
              (0x4AB854, power), (0x4AB7C4, current), (0x4AB7A0, slip)]
    memory = {address + i * 4: value for address, values in arrays for i, value in enumerate(values)}
    class Memory:
        f32 = staticmethod(memory.__getitem__)
    warnings = []
    curves = extract_curves({'memory': Memory()}, {'_curve': 0, '_load_curve': 1}, warnings)
    curve = curves[0]
    for series, expected in zip(curve['series'], [power, slip, current, pf, eta]):
        assert [p['x'] for p in series['points']] == xs
        assert [p['y'] for p in series['points']] == expected
    assert warnings == []
    assert all(c['display'] is False for c in curves[1:])
    for s, expected in zip(curve['plot']['series'], [power, slip, current, pf, eta]):
        precision = curve['plot']['input_decimal_places'][s['axis']]
        assert s['points'] == trend_points(xs, [round(y, precision) for y in expected])


def test_load_efficiency_percent_display_preserves_geometry_and_snapshot():
    curve = {'id': 'load_characteristic', 'series': [{'name': '效率 η', 'points': [{'x': 1, 'y': .82}]}],
             'plot': {'x_min': 20, 'x_max': 150,
                      'y_axes': [{'id': 'pf', 'label': '功率因数 cosφ', 'min': 0, 'max': 1},
                                 {'id': 'efficiency', 'label': '效率 η', 'min': 0, 'max': 1}],
                      'series': [{'name': '功率因数 cosφ', 'axis': 'pf', 'points': [{'x': 100, 'y': .75}]},
                                 {'name': '效率 η', 'axis': 'efficiency', 'points': [{'x': 100, 'y': .82}]}]}}
    original = deepcopy(curve)
    normalized = plot_data(curve)
    assert normalized['y_axes'][0]['max'] == 1
    assert normalized['y_axes'][1]['max'] == 100
    assert normalized['y_axes'][1]['short_label'] == 'η (%)'
    assert normalized['series'][0]['points'][0]['y'] == .75
    assert normalized['series'][1]['points'][0]['y'] == 82
    assert curve == original
    # A normalized plot is already in percent; do not scale it a second time.
    assert plot_data({**curve, 'plot': normalized}) == normalized
    old_svg = curve_svg({**curve, 'id': 'original_ratio_display'})
    new_svg = curve_svg(curve)
    assert re.findall(r'<polyline points="([^"]+)"', old_svg) == re.findall(r'<polyline points="([^"]+)"', new_svg)
    assert '效率 η (%)' in new_svg and 'η (%)' in new_svg


def test_quadratic_trend_recovers_known_polynomial_and_explicit_extrapolation():
    xs = [.3, .4, .5, .6, .75, .9, 1., 1.1, 1.2]
    ys = [2 + 3 * x - .5 * x * x for x in xs]
    assert quadratic_fit(xs, ys) == pytest.approx([2, 3, -.5], abs=1e-12)
    plot = trend_points(xs[::-1], ys[::-1])
    assert len(plot) == 131
    assert plot[0] == pytest.approx({'x': 20, 'y': 2.58})
    assert plot[-1] == pytest.approx({'x': 150, 'y': 5.375})
    with pytest.raises(ValueError):
        quadratic_fit([1, 1, 1], [1, 2, 3])


def test_least_squares_uses_all_samples_instead_of_interpolating_them():
    xs, ys = [-2, -1, 0, 1, 2], [4, 1, 1, 1, 4]
    c = quadratic_fit(xs, ys)
    # Known projection of a unit impulse on the constant/quadratic basis.
    assert c == pytest.approx([17 / 35, 0, 6 / 7])
    assert c[0] != ys[2]


def test_slip_plot_preserves_raw_values_and_distinguishes_artificial_origin():
    rows = [[1 - i / 50, 6 - i / 12, 2 + i / 20] for i in range(50)] + [[.01, .3, .2]]
    original = deepcopy(rows)
    plot = slip_plot(rows)
    assert rows == original
    assert plot['x_reverse'] is True
    assert [a['max'] for a in plot['y_axes']] == [10, 5]
    assert len(plot['series'][0]['points']) == 101
    assert plot['series'][0]['points'][-1] == {'x': 0, 'y': 0}
    assert plot['series'][0]['points'][96]['y'] == pytest.approx(rows[48][1])
    assert slip_plot(rows[:5])['series'][0]['points'][-1]['y'] == rows[4][1]


def test_natural_spline_keeps_knots_and_linear_data_and_rejects_extrapolation():
    p = [{'x': x, 'y': 2 * x + 3} for x in [0, .04, .06, .5, 1]]
    q = [i / 100 for i in range(101)]
    assert [p['y'] for p in natural_spline(p, q)] == pytest.approx([2 * x + 3 for x in q])
    with pytest.raises(ValueError, match='外推'):
        natural_spline(p, [-.01])


def test_material_plot_transposes_units_without_pairing_independent_grids():
    bh = [{'x': .5, 'y': 120}, {'x': .55, 'y': 240}]
    loss = [{'x': .5, 'y': 2}, {'x': .6, 'y': 4}, {'x': .7, 'y': 8}]
    original = deepcopy(bh)
    chart = material_plot(bh, 'bh', legacy_origin=True)
    assert chart['series'][0]['points'] == [{'x': 0, 'y': 0}, {'x': 1.2, 'y': 5000}, {'x': 2.4, 'y': 5500}]
    assert material_plot(loss, 'loss')['series'][0]['points'][1] == {'x': 4, 'y': 6000}
    assert bh == original
    assert material_plot([{'x': .5, 'y': 1.2}], 'bh', 'A/cm')['series'][0]['points'][0]['x'] == 1.2


def test_svg_uses_independent_axes_reverse_x_and_keeps_old_snapshots_supported():
    curve = {'title': 'Synthetic', 'series': [{'name': 'old', 'points': [{'x': 0, 'y': 1}, {'x': 1, 'y': 2}]}]}
    assert plot_data(curve)['x_reverse'] is False
    curve['plot'] = {'x_min': 0, 'x_max': 1, 'x_reverse': True, 'x_label': 's',
        'y_axes': [{'id': 'i', 'label': 'I', 'min': 0, 'max': 10}, {'id': 't', 'label': 'T', 'min': 0, 'max': 5}],
        'series': [{'name': 'I', 'axis': 'i', 'points': [{'x': 1, 'y': 5}, {'x': 0, 'y': 0}]},
                   {'name': 'T', 'axis': 't', 'points': [{'x': 1, 'y': 2.5}, {'x': 0, 'y': 0}]}]}
    output = curve_svg(curve)
    lines = re.findall(r'<polyline points="([^"]+)"', output)
    assert len(lines) == 2 and lines[0] == lines[1]
    first, last = lines[0].split()
    assert float(first.split(',')[0]) < float(last.split(',')[0])


def test_metadata_colors_are_not_interpreted_as_markup():
    curve = {'series': [{'name': '<script>', 'color': 'red" onload="bad', 'points': [{'x': 0, 'y': 1}]}]}
    assert 'onload' not in curve_svg(curve)
    assert '<script>' not in curve_svg(curve)
