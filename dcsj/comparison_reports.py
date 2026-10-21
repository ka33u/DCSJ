"""Review reports from frozen comparison sets, with a repeated baseline in every group."""
from io import BytesIO

from . import pdf_fonts
from .execution import BusyError
from .pdf_report import _font, _RENDER_SLOT, checked_paragraph, plain
from .reports import STYLE, table, text

EXPLANATION = ('Δ 为候选值减基准值；变化率为差值除以基准绝对值。正负不代表优劣，'
               '基准为 0 时不计算变化率；缺失值以 - 显示，不作零值处理。')
BOUNDARY = '本报告固定保存时的方案、顺序、基准、结果和计算来源。工程结论须结合原始计算记录及实物或第三方结果校核。'


def groups(saved):
    baseline = next(s for s in saved['schemes'] if s['id'] == saved['baseline_id'])
    candidates = [s for s in saved['schemes'] if s['id'] != saved['baseline_id']]
    return [[baseline, *candidates[i:i + 3]] for i in range(0, len(candidates), 3)]


def metric_rows(saved, schemes):
    rows = []
    for metric in saved['metrics']:
        cells = {cell['id']: cell for cell in metric['cells']}
        row = [f'{metric["label"]} ({metric["key"]})\n{metric["unit"]}']
        for scheme in schemes:
            cell = cells[scheme['id']]
            value = plain(cell['value'])
            if scheme['id'] != saved['baseline_id']:
                value += '\nΔ ' + plain(cell['delta']) + ' (' + (
                    plain(cell['delta_percent']) + '%' if cell['delta_percent'] is not None else '-') + ')'
            row.append(value)
        rows.append(row)
    return rows


def input_rows(saved, schemes):
    rows = []
    for field in saved['parameter_differences']:
        cells = {cell['id']: cell for cell in field['cells']}
        rows.append([f'{field["label"]} ({field["key"]})\n{field["unit"]}',
                     *[plain(cells[s['id']]['value']) for s in schemes]])
    return rows


def headers(saved, schemes, first='指标 / 单位'):
    numbers = {s['id']: i + 1 for i, s in enumerate(saved['schemes'])}
    label = lambda s: s['name'] if len(s['name']) <= 28 else s['name'][:28] + '...'
    return [first, *[f'S{numbers[s["id"]]} {label(s)}' + (
        '\n（基准）' if s['id'] == saved['baseline_id'] else '') for s in schemes]]


def warnings(saved):
    common = set.intersection(*(set(s['warnings']) for s in saved['schemes']))
    shared = [f'全部方案：{w}' for w in saved['schemes'][0]['warnings'] if w in common]
    specific = [f'{s["name"]}：{w}' for s in saved['schemes'] for w in s['warnings'] if w not in common]
    return list(dict.fromkeys([*saved['warnings'], *shared, *specific, BOUNDARY]))


def render_html(saved):
    content = [f'<header><span class="tag">DCSJ / COMPARISON REVIEW</span><h1>{text(saved["name"])}</h1>',
               f'<p>方案对比评审报告 · {len(saved["schemes"])} 个固定计算快照</p>',
               f'<small>记录 ID：{text(saved["id"])}<br>保存时间（UTC）：{text(saved["created_at"])}</small></header>',
               '<h2>评审说明</h2><p class="preserve">' + text(saved['notes'] or '未填写评审说明') + '</p>',
               '<p>' + EXPLANATION + '</p>', '<section class="warning">' + ''.join(
                   '<p>' + text(w) + '</p>' for w in warnings(saved)) + '</section>',
               '<h2>方案目录</h2>', table(['编号', '方案', '计算时间（UTC）', '计算记录 ID'], [
                   [f'S{i + 1}' + (' 基准' if s['id'] == saved['baseline_id'] else ''), s['name'], s['created_at'], s['id']]
                   for i, s in enumerate(saved['schemes'])])]
    for i, schemes in enumerate(groups(saved), 1):
        content += [f'<h2 class="break">性能指标 · 第 {i} 组</h2><p>每组重复基准，最多并列三个候选。完整方案名称见方案目录。</p>',
                    table(headers(saved, schemes), metric_rows(saved, schemes)),
                    '<h2>变化的输入参数</h2>', table(headers(saved, schemes, '参数 / 单位'), input_rows(saved, schemes))
                    if saved['parameter_differences'] else '<p>这些计算使用相同输入。</p>']
    content.append('<h2 class="break">计算来源</h2>')
    for i, scheme in enumerate(saved['schemes'], 1):
        content.append(f'<h3>S{i} {text(scheme["name"])}</h3><p>记录：{text(scheme["id"])}</p>')
        content.extend('<p>' + text(k) + '：<code>' + text(v) + '</code></p>' for k, v in scheme['provenance'].items())
    content.append(f'<p>对比格式版本：{text(saved["comparison_version"])} · 保存时软件版本：{text(saved["application_version"])}</p>')
    pdf = f'<a href="/api/v1/comparison-sets/{text(saved["id"])}/report.pdf">下载 PDF</a>' if pdf_fonts.status()['available'] else ''
    style = STYLE + 'main{max-width:297mm}td,.preserve{white-space:pre-wrap}.toolbar a{color:white;margin-left:18px}@page{size:A4 landscape}'
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>' + text(saved['name']) + ' · 方案对比</title><style>' + style + '</style></head><body>'
            '<div class="toolbar"><button onclick="window.print()">网页打印</button>' + pdf +
            '　A4 横向</div><main>' + ''.join(content) + '</main></body></html>')


def render_pdf(saved):
    if not _RENDER_SLOT.acquire(blocking=False):
        raise BusyError('PDF 正在生成，请稍后重试。')
    try:
        return _pdf(saved)
    finally:
        _RENDER_SLOT.release()


def _pdf(saved):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, PageBreak
    font, cmap = _font()
    page_size = landscape(A4)
    width = page_size[0] - 32 * mm
    styles = {
        'body': ParagraphStyle('body', fontName=font, fontSize=8.5, leading=12.5, textColor='#17323f',
                               spaceAfter=6, wordWrap='CJK'),
        'title': ParagraphStyle('title', fontName=font, fontSize=21, leading=28, textColor='#17323f',
                                spaceAfter=10, wordWrap='CJK', keepWithNext=True),
        'h2': ParagraphStyle('h2', fontName=font, fontSize=13, leading=19, textColor='#26795e',
                             spaceBefore=10, spaceAfter=8, wordWrap='CJK', keepWithNext=True),
        'cell': ParagraphStyle('cell', fontName=font, fontSize=8, leading=11.5, textColor='#17323f', wordWrap='CJK'),
    }
    paragraph = lambda value, style='body': checked_paragraph(value, styles[style], cmap)

    def grid(headings, rows, widths=None):
        if widths is None:
            widths = [width * .22] + [width * .78 / (len(headings) - 1)] * (len(headings) - 1)
        item = Table([[paragraph(h, 'cell') for h in headings]] + [
            [paragraph(v, 'cell') for v in row] for row in rows], colWidths=widths, repeatRows=1, splitInRow=1)
        item.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#edf5f2')),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6), ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('LINEBELOW', (0, 0), (-1, 0), .7, colors.HexColor('#26795e')),
            ('LINEBELOW', (0, 1), (-1, -1), .3, colors.HexColor('#dce5e8')),
        ]))
        return item

    story = [paragraph('DCSJ / COMPARISON REVIEW'), paragraph(saved['name'], 'title'),
             paragraph(f'方案对比评审报告 - {len(saved["schemes"])} 个固定计算快照'),
             paragraph('对比记录：' + saved['id']), paragraph('保存时间（UTC）：' + saved['created_at']),
             paragraph('评审说明', 'h2')]
    # Short blocks allow a long note to start below the cover instead of moving
    # the entire first paragraph to a new page because of the heading's keep rule.
    notes = saved['notes'] or '未填写评审说明'
    story.extend(paragraph(notes[i:i + 600]) for i in range(0, len(notes), 600))
    story += [paragraph(EXPLANATION), paragraph('适用边界与提示', 'h2')]
    story.extend(paragraph(w) for w in warnings(saved))
    story += [paragraph('方案目录', 'h2'), grid(['编号', '方案', '计算时间（UTC）', '计算记录 ID'], [
        [f'S{i + 1}' + (' 基准' if s['id'] == saved['baseline_id'] else ''), s['name'], s['created_at'], s['id']]
        for i, s in enumerate(saved['schemes'])], [width * n for n in (.08, .32, .25, .35)])]
    for index, schemes in enumerate(groups(saved), 1):
        story += [PageBreak(), paragraph(f'性能指标 - 第 {index} 组', 'h2'),
                  paragraph('每组重复基准，最多并列三个候选。完整名称见方案目录；数值显示最多 8 位有效数字，完整精度保留在记录 API 中。'),
                  grid(headers(saved, schemes), metric_rows(saved, schemes)),
                  paragraph('变化的输入参数', 'h2')]
        story.append(grid(headers(saved, schemes, '参数 / 单位'), input_rows(saved, schemes)) if saved['parameter_differences']
                     else paragraph('这些计算使用相同输入。'))
    story += [PageBreak(), paragraph('计算来源与版本', 'h2')]
    for i, scheme in enumerate(saved['schemes'], 1):
        story += [paragraph(f'S{i} {scheme["name"]}', 'h2'), paragraph('记录：' + scheme['id'])]
        story.extend(paragraph(key + '：' + plain(value)) for key, value in scheme['provenance'].items())
    story.append(paragraph(f'对比格式版本：{saved["comparison_version"]} · 保存时软件版本：{saved["application_version"]}'))

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor('#dce5e8')); canvas.setLineWidth(.5)
        canvas.line(16 * mm, 14 * mm, page_size[0] - 16 * mm, 14 * mm)
        canvas.setFont(font, 7); canvas.setFillColor(colors.HexColor('#5a707b'))
        canvas.drawString(16 * mm, 9 * mm, 'DCSJ / ' + saved['id'][:18])
        canvas.drawRightString(page_size[0] - 16 * mm, 9 * mm, f'第 {doc.page} 页')
        canvas.restoreState()

    stream = BytesIO()
    doc = SimpleDocTemplate(stream, pagesize=page_size, leftMargin=16 * mm, rightMargin=16 * mm,
                            topMargin=14 * mm, bottomMargin=20 * mm, title=plain(saved['name']), author='DCSJ')
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return stream.getvalue()
