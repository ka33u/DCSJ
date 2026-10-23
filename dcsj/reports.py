"""Self-contained A4 reports rendered exclusively from immutable calculations."""
from html import escape
import math
import hashlib

from .parameters import field_info
from .pdf_fonts import status as pdf_status
from .curves import plot_data
from . import report_summary as summary
from .slot_diagrams import slot_drawings, svg as slot_svg


def text(value):
    if value is None:
        return '—'
    if isinstance(value, float):
        return f'{value:.7g}'
    return escape(str(value))


def table(headers, rows, css=''):
    heading = '<thead><tr>' + ''.join('<th>' + text(h) + '</th>' for h in headers) + '</tr></thead>' if headers else ''
    return '<table class="' + escape(css, quote=True) + '">' + heading + '<tbody>' + ''.join(
        '<tr>' + ''.join('<td>' + text(cell) + '</td>' for cell in row) + '</tr>' for row in rows) + '</tbody></table>'


def curve_svg(curve):
    plot = plot_data(curve)
    if not plot:
        return '<p>无有效曲线点</p>'
    series, axes = plot['series'], plot['y_axes']
    axis_by_id = {axis['id']: axis for axis in axes}
    xmin, xmax = plot['x_min'], plot['x_max']
    left, right = 25 + 52 * len(axes), 540
    height = (right - left) / plot['aspect_ratio'] if plot['aspect_ratio'] else 192
    bottom = 43 + height
    total_height = bottom + 50 + 14 * len(series)
    axis_colors = {a['id']: next(s['color'] for s in series if s['axis'] == a['id']) for a in axes}
    clip_id = 'plot-' + hashlib.sha256(repr(plot).encode()).hexdigest()[:12]
    def project(point, axis):
        fraction = (point['x'] - xmin) / (xmax - xmin)
        return (left + (1 - fraction if plot['x_reverse'] else fraction) * (right - left),
                bottom - (point['y'] - axis['min']) / (axis['max'] - axis['min']) * height)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 560 {total_height}" role="img">',
             '<title>' + text(curve.get('title', '曲线')) + '</title>',
             f'<rect width="560" height="{total_height}" fill="white"/>',
             f'<defs><clipPath id="{clip_id}"><rect x="{left}" y="43" width="{right-left}" height="{height}"/></clipPath></defs>']
    for i in range(plot['y_ticks'] + 1):
        y = bottom - height * i / plot['y_ticks']
        parts.append(f'<path d="M{left} {y}H{right}" stroke="#d9e2e5"/>')
        for j, axis in enumerate(axes):
            value = axis['min'] + (axis['max'] - axis['min']) * i / plot['y_ticks']
            parts.append(f'<text x="{left - 8 - 52 * (len(axes) - 1 - j)}" y="{y + 4}" text-anchor="end" fill="{axis_colors[axis["id"]]}">{value:.3g}</text>')
    for i in range(plot['x_ticks'] + 1):
        x = left + (right - left) * i / plot['x_ticks']
        value = xmin + (xmax - xmin) * (1 - i / plot['x_ticks'] if plot['x_reverse'] else i / plot['x_ticks'])
        parts.append(f'<path d="M{x} 43V{bottom}" stroke="#d9e2e5"/>')
        parts.append(f'<text x="{x}" y="{bottom + 17}" text-anchor="middle">{value:.3g}</text>')
    for j, axis in enumerate(axes):
        label = axis['label'] if len(axes) == 1 else axis.get('short_label', axis['label'])
        label_x = left if len(axes) == 1 else left - 8 - 52 * (len(axes) - 1 - j)
        anchor = 'start' if len(axes) == 1 else 'end'
        parts.append(f'<text x="{label_x}" y="24" text-anchor="{anchor}" fill="{axis_colors[axis["id"]]}">{text(label)}</text>')
    for i, s in enumerate(series):
        line = ' '.join(f'{x:.3f},{y:.3f}' for p in s['points'] for x, y in [project(p, axis_by_id[s['axis']])])
        parts.append(f'<polyline points="{line}" stroke="{s["color"]}" stroke-width="2" fill="none" clip-path="url(#{clip_id})"/>')
        parts.append(f'<text x="25" y="{bottom + 49 + i * 14}" fill="{s["color"]}">{text(s["name"])}</text>')
    parts.append(f'<text x="{right}" y="{bottom + 33}" text-anchor="end">{text(plot["x_label"])}</text></svg>')
    if plot['note']:
        parts.append('<p class="muted">' + text(plot['note']) + '</p>')
    return ''.join(parts)


STYLE = '''
:root{color:#17323f;font-family:system-ui,-apple-system,"Microsoft YaHei",sans-serif;font-size:12px}
body{margin:0;background:#e9eef0}main{box-sizing:border-box;max-width:210mm;margin:24px auto;background:white;padding:16mm}
h1{font-size:25px;margin:8px 0}h2{font-size:17px;border-bottom:2px solid #247b62;padding-bottom:7px;margin:24px 0 12px}
h3{margin:12px 0 0}p{line-height:1.6}small,.muted{color:#526975}header{border-bottom:1px solid #9eafb7;padding-bottom:14px}
.tag{letter-spacing:2px;color:#247b62}table{border-collapse:collapse;width:100%;margin:10px 0;font-size:11px;table-layout:fixed}
th,td{padding:6px;border:1px solid #d8e2e6;text-align:left;vertical-align:top;overflow-wrap:anywhere}th{background:#eef4f2}
figure{margin:10px 0;break-inside:avoid}svg{width:100%;height:auto;font:10px sans-serif}figcaption{font-weight:600}
.toolbar{position:sticky;top:0;background:#17323f;color:white;padding:12px;text-align:center;z-index:1}
button{padding:8px 20px;border:0;border-radius:5px;cursor:pointer}code{overflow-wrap:anywhere;font-size:10px}
.fingerprints{font-size:10px}.fingerprints p{margin:3px 0}.break{break-before:page}
.summary header{padding-bottom:7px}.summary h1{font-size:22px;margin:3px 0}
.summary .tag{font-size:9px}.summary p{margin:4px 0}.summary h2{font-size:13px;margin:10px 0 4px;padding-bottom:3px}
.summary table{font-size:10px;margin:3px 0}.summary th,.summary td{padding:3px 4px;line-height:1.35}
.summary .pairs td:nth-child(odd){background:#f4f7f6;color:#405762;width:18%}
.summary .pairs td:nth-child(even){width:15.333%}.summary .model{white-space:pre-wrap;font-size:10px}
.slots{display:grid;grid-template-columns:180fr 180fr 144fr;align-items:center;border:1px solid #d8e2e6}
.slots figure{margin:0;border-right:1px solid #d8e2e6}.slots .dimensions{padding:0 5px}
.slots .dimensions table{font-size:9px}.slots .dimensions td{padding:3px 2px;border-width:0 0 1px}
.targets th:first-child{width:30%}.summary .legend{font-size:9px;color:#526975}
@media screen and (max-width:600px){main{padding:18px}.slots{grid-template-columns:1fr 1fr}
  .slots .dimensions{grid-column:1/-1}.summary .pairs{font-size:9px}}
@page{size:A4;margin:14mm 13mm 16mm}
@media print{body{background:white}main{margin:0;padding:0;max-width:none}.toolbar{display:none}
  thead{display:table-header-group}tr{break-inside:avoid}h2,h3{break-after:avoid}header{break-inside:avoid}
  *{-webkit-print-color-adjust:exact;print-color-adjust:exact}a{color:inherit;text-decoration:none}}
'''


def render(snapshot):
    p, v = snapshot['inputs'], snapshot['values']
    title = snapshot['name']
    pdf_link = (f'<a href="/api/v1/calculations/{escape(snapshot["calculation_id"])}/report.pdf" '
                'style="color:white;margin-left:18px">下载 PDF</a>') if pdf_status()['available'] else ''
    content = [f'<section class="summary"><header><span class="tag">DCSJ / 电磁设计计算报告</span><h1>{text(title)}</h1>',
               f'<small>计算时间（北京时间）：{text(summary.local_time(snapshot["created_at"]))}　',
               f'设计版本：{text(snapshot.get("project_revision"))}</small></header>',
               '<h2>额定条件</h2><p class="model">型号：' + text(p.get('SizeX')) + '</p>',
               table([], summary.grid(summary.ratings(p)), 'pairs'),
               '<h2>铁心与槽型</h2><div class="slots">']
    content.extend('<figure>' + slot_svg(panel) + '</figure>' for panel in slot_drawings(p))
    content += ['<div class="dimensions">', table([], [(label, value + (' ' + unit if unit else ''))
                for label, value, unit in summary.dimensions(p)]), '</div></div>',
                table(summary.SLOT_HEADERS, summary.slot_rows(snapshot), 'pairs') if snapshot.get('slots') else '',
                '<p class="legend">' + text(summary.SLOT_AREA_NOTE) + '</p>' if snapshot.get('slots') else '',
                '<h2>绕组与导线</h2>', table([], summary.grid(summary.windings(snapshot)), 'pairs'),
                '<p class="legend">' + text(summary.wire_note(snapshot)) + '</p>' if summary.wire_note(snapshot) else '',
                '<h2>设计目标与计算结果</h2>', table(['指标', '设计目标', '计算结果', '差值 / 峰值位置', '单位'], summary.targets(snapshot), 'targets'),
                '<p class="legend">差值＝计算结果−设计目标；效率与槽满率的差值单位为百分点。s 表示峰值处转差率。</p>',
                '<h2>运行与电磁指标</h2>', table([], summary.grid(summary.performance(v)), 'pairs'), '</section>']
    curves = [curve for curve in snapshot.get('curves', []) if curve.get('display') is not False]
    if curves:
        content.append('<h2 class="break">性能曲线</h2><p>' + text(summary.CURVE_NOTE) + '</p>')
        for curve in curves:
            content.append('<figure><figcaption>' + text(curve['title']) + '</figcaption>' + curve_svg(curve) + '</figure>')
    content += ['<h2 class="break">完整输入</h2>', table(['字段', '参数', '输入', '单位'], [
        (k, field_info(k)['label'], value, field_info(k)['unit']) for k, value in p.items()])]
    content += ['<h2 class="break">完整计算输出</h2>', table(['字段', '指标', '计算值', '单位'], [
        (k, field_info(k)['label'], value, field_info(k)['unit']) for k, value in v.items()])]
    content.append('<h2>计算来源与版本</h2><div class="fingerprints"><p>记录 ID：' + text(snapshot['calculation_id']) + '</p>')
    for key, value in snapshot.get('provenance', {}).items():
        content.append('<p>' + text(key) + '：<code>' + text(value) + '</code></p>')
    content.append('</div><p class="muted">本报告读取保存的输入和结果，不使用工作台中后来修改的参数。</p>')
    return '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">' + \
        '<title>' + text(title) + ' · DCSJ 报告</title><style>' + STYLE + '</style></head><body>' + \
        '<div class="toolbar"><button type="button" onclick="window.print()">网页打印</button>' + pdf_link + '　A4 · 建议关闭浏览器默认页眉页脚</div>' + \
        '<main>' + ''.join(content) + '</main></body></html>'
