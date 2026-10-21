"""Offline A4 PDF generation with embedded CJK fonts and vector engineering figures."""
from functools import lru_cache
from html import escape
from io import BytesIO
import hashlib
import math
import re
import threading

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether, Flowable

from . import pdf_fonts
from .execution import BusyError
from .curves import plot_data
from .parameters import field_info, validate, InputError, ELEMENT_KEYS

INK = colors.HexColor('#17323f')
TEAL = colors.HexColor('#26795e')
PALE = colors.HexColor('#edf5f2')
MUTED = colors.HexColor('#5a707b')
WIDTH = A4[0] - 32 * mm
_RENDER_SLOT = threading.BoundedSemaphore(1)


class PDFUnavailableError(RuntimeError):
    pass


class PDFContentError(ValueError):
    pass


def plain(value):
    if value is None:
        return '-'
    if isinstance(value, float):
        return f'{value:.8g}'
    # Strip controls prohibited in XML text, while preserving the actual visible input.
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', str(value))


def checked_paragraph(value, style, cmap):
    visible = plain(value)
    missing = sorted({ch for ch in visible if not ch.isspace() and (ord(ch) not in cmap or cmap[ord(ch)] == 0)})
    if missing:
        codes = ', '.join(f'U+{ord(ch):04X}' for ch in missing[:8])
        raise PDFContentError('PDF 字体不支持部分字符（' + codes + '），请调整文本或安装覆盖这些字符的字体。')
    return Paragraph(escape(visible).replace('\n', '<br/>'), style)


@lru_cache(maxsize=4)
def _load_font(path, modified, size):
    name = f'DCSJPdf-{hashlib.sha256(path.encode()).hexdigest()[:12]}-{modified}-{size}'
    try:
        registered = TTFont(name, path)
        pdfmetrics.registerFont(registered)
    except Exception as error:
        raise PDFUnavailableError('PDF 字体无法读取，请重新安装或检查 DCSJ_PDF_FONT。') from error
    return name, registered.face.charToGlyph


def _font():
    path = pdf_fonts.font_path()
    if not path.is_file():
        raise PDFUnavailableError('尚未安装 PDF 中文字体，请运行 python scripts/install_pdf_font.py；也可使用网页打印。')
    stat = path.stat()
    return _load_font(str(path.resolve()), stat.st_mtime_ns, stat.st_size)


class Geometry(Flowable):
    def __init__(self, parameters, font):
        super().__init__()
        self.p, self.font = validate(parameters), font
        self.width, self.height = WIDTH, 225

    def draw(self):
        p, c = self.p, self.canv
        outer, bore = p['D1'] / 2, p['Di1'] / 2
        rotor, shaft = bore - p['g'], p['Di2'] / 2
        scale = 93 / outer
        cx, cy = 125, 112
        for radius, fill, stroke in [(outer, '#dfe8ed', '#657b87'), (bore, '#ffffff', '#657b87'),
                                      (rotor, '#d8e9df', '#2e7960'), (shaft, '#ffffff', '#2e7960')]:
            c.setFillColor(colors.HexColor(fill)); c.setStrokeColor(colors.HexColor(stroke))
            c.circle(cx, cy, radius * scale, fill=1, stroke=1)
        for count, radius, depth, color in [
            (int(p['Q1']), bore, min(p['hs0'] + p['hs12'], outer - bore) * .8, '#647c9c'),
            (int(p['Q2']), rotor, -min(p['hr12'] + p['hr3'], rotor - shaft) * .65, '#b77c42')]:
            if count > 240:
                continue
            c.setStrokeColor(colors.HexColor(color)); c.setLineWidth(1.2)
            for index in range(count):
                angle = 2 * math.pi * index / count
                c.line(cx + radius * scale * math.cos(angle), cy + radius * scale * math.sin(angle),
                       cx + (radius + depth) * scale * math.cos(angle), cy + (radius + depth) * scale * math.sin(angle))
        c.setFillColor(INK); c.setFont(self.font, 10)
        rows = [('D1', p['D1']), ('Di1', p['Di1']), ('D2', rotor * 2), ('Di2', p['Di2']),
                ('g', p['g']), ('L', p['L']), ('Q1', p['Q1']), ('Q2', p['Q2'])]
        for index, (key, value) in enumerate(rows):
            c.drawString(285, 199 - index * 24, f'{key} = {value:g}' + ('' if key.startswith('Q') else ' mm'))


class Curve(Flowable):
    def __init__(self, curve, font):
        super().__init__()
        self.curve, self.font = curve, font
        try:
            self.plot = plot_data(curve)
        except ValueError as error:
            raise PDFContentError(str(error)) from error
        self.extra_legend_height = 12 * max(0, len((self.plot or curve)['series']) - 2)
        self.plot_height = (WIDTH - 26 - 39 * len(self.plot['y_axes'])) / self.plot['aspect_ratio'] if self.plot and self.plot['aspect_ratio'] else 180
        self.width, self.height = WIDTH, self.plot_height + 90 + self.extra_legend_height

    def draw(self):
        c, curve = self.canv, self.curve
        plot = self.plot
        if not plot:
            c.setFont(self.font, 9); c.setFillColor(MUTED)
            c.drawString(43, 180, '此曲线未产生有效采样点，请查看计算提示。')
            return
        xmin, xmax = plot['x_min'], plot['x_max']
        axes = plot['y_axes']
        axis_by_id = {axis['id']: axis for axis in axes}
        left, bottom, height = 13 + 39 * len(axes), 59 + self.extra_legend_height, self.plot_height
        width = WIDTH - left - 13
        axis_colors = {a['id']: next(s['color'] for s in plot['series'] if s['axis'] == a['id']) for a in axes}
        c.setFont(self.font, 8); c.setFillColor(MUTED)
        for j, axis in enumerate(axes):
            c.setFillColor(colors.HexColor(axis_colors[axis['id']]))
            if len(axes) == 1:
                c.drawString(left, bottom + height + 17, plain(axis['label']))
            else:
                c.drawRightString(left - 6 - 39 * (len(axes) - 1 - j), bottom + height + 17,
                                  plain(axis.get('short_label', axis['label'])))
        for index in range(plot['y_ticks'] + 1):
            y = bottom + height * index / plot['y_ticks']
            c.setStrokeColor(colors.HexColor('#dbe5e8')); c.setLineWidth(.4)
            c.line(left, y, left + width, y)
            for j, axis in enumerate(axes):
                c.setFillColor(colors.HexColor(axis_colors[axis['id']]))
                value = axis['min'] + (axis['max'] - axis['min']) * index / plot['y_ticks']
                c.drawRightString(left - 6 - 39 * (len(axes) - 1 - j), y - 3, f'{value:.4g}')
        c.setFillColor(MUTED)
        for index in range(plot['x_ticks'] + 1):
            x = left + width * index / plot['x_ticks']
            c.line(x, bottom, x, bottom + height)
            value = xmin + (xmax - xmin) * (1 - index / plot['x_ticks'] if plot['x_reverse'] else index / plot['x_ticks'])
            c.drawCentredString(x, bottom - 14, f'{value:.4g}')
        c.drawRightString(left + width, bottom - 30, plain(plot['x_label']))
        for index, series in enumerate(plot['series']):
            axis = axis_by_id[series['axis']]
            c.setStrokeColor(colors.HexColor(series['color'])); c.setLineWidth(1.4)
            path = c.beginPath()
            for ordinal, point in enumerate(series['points']):
                fraction = (point['x'] - xmin) / (xmax - xmin)
                x = left + (1 - fraction if plot['x_reverse'] else fraction) * width
                y = bottom + (point['y'] - axis['min']) / (axis['max'] - axis['min']) * height
                (path.moveTo if ordinal == 0 else path.lineTo)(x, y)
            c.saveState()
            clip = c.beginPath(); clip.rect(left, bottom, width, height)
            c.clipPath(clip, stroke=0, fill=0)
            c.drawPath(path)
            c.restoreState()
            y = 17 + self.extra_legend_height - index * 12
            c.line(13, y + 3, 30, y + 3)
            c.setFillColor(INK); c.drawString(36, y, plain(series['name']))


def _render(snapshot):
    font, cmap = _font()
    styles = {
        'body': ParagraphStyle('body', fontName=font, fontSize=8.4, leading=12.8, textColor=INK,
                               spaceAfter=5, wordWrap='CJK', splitLongWords=True),
        'title': ParagraphStyle('title', fontName=font, fontSize=23, leading=30, textColor=INK,
                                spaceAfter=10, wordWrap='CJK', keepWithNext=True),
        'h2': ParagraphStyle('h2', fontName=font, fontSize=13.5, leading=19, textColor=TEAL,
                             spaceBefore=13, spaceAfter=9, wordWrap='CJK', keepWithNext=True),
        'caption': ParagraphStyle('caption', fontName=font, fontSize=9, leading=13, textColor=INK,
                                  spaceBefore=10, spaceAfter=4, wordWrap='CJK', keepWithNext=True),
        'cell': ParagraphStyle('cell', fontName=font, fontSize=7.4, leading=10.8, textColor=INK,
                               wordWrap='CJK', splitLongWords=True),
        'warning': ParagraphStyle('warning', fontName=font, fontSize=8, leading=12, textColor=colors.HexColor('#72501e'),
                                  backColor=colors.HexColor('#fff9ed'), borderPadding=6, spaceAfter=8, wordWrap='CJK'),
        'small': ParagraphStyle('small', fontName=font, fontSize=7.4, leading=10.5, textColor=MUTED,
                                spaceAfter=4, wordWrap='CJK', splitLongWords=True),
    }

    def paragraph(value, style='body'):
        return checked_paragraph(value, styles[style], cmap)

    def table(headers, rows, widths):
        grid = [[paragraph(h, 'cell') for h in headers]] + [[paragraph(v, 'cell') for v in row] for row in rows]
        result = Table(grid, colWidths=widths, repeatRows=1, splitInRow=1, hAlign='LEFT')
        result.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), PALE), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 6), ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), 3.5), ('BOTTOMPADDING', (0, 0), (-1, -1), 3.5),
            ('LINEBELOW', (0, 0), (-1, 0), .7, TEAL),
            ('LINEBELOW', (0, 1), (-1, -1), .3, colors.HexColor('#dce5e8')),
        ]))
        return result

    def paired_table(keys, data):
        cells = [(field_info(k)['label'], data[k], field_info(k)['unit']) for k in keys if k in data]
        rows = [(*cells[i], *(cells[i + 1] if i + 1 < len(cells) else ('', '', ''))) for i in range(0, len(cells), 2)]
        return table(['参数 / 指标', '数值', '单位'] * 2, rows,
                     [WIDTH * .205, WIDTH * .215, WIDTH * .08] * 2)

    p, values = snapshot['inputs'], snapshot['values']
    story = [paragraph('DCSJ / ENGINEERING REPORT', 'small'), paragraph(snapshot['name'], 'title'),
             paragraph('电磁设计计算报告 - 固定计算快照'),
             paragraph('记录 ID：' + snapshot['calculation_id'], 'small'),
             paragraph('计算时间（UTC）：' + snapshot['created_at'], 'small'),
             paragraph('关联设计版本：' + plain(snapshot.get('project_revision')), 'small')]
    story.append(paragraph('额定条件', 'h2'))
    keys = ['SizeX', 'kW', 'p', 'f', 'Ufai', 'Jiefa', 'Paihao', 'Jueyuan']
    story.append(paired_table(keys, p))
    story.append(paragraph('关键性能', 'h2'))
    keys = ['ATA1', 'PF1', 'I1', 'n', 'Sn', 'TST1', 'TM1', 'IST1', 'CostFx', 'Pcu', 'Pal', 'Pfe', 'Pfw1', 'Ps1']
    story.append(paired_table(keys, values))
    story.append(paragraph('适用边界与计算提示', 'h2'))
    warnings = list(dict.fromkeys(snapshot.get('warnings', []) + snapshot.get('engine', {}).get('limitations', []) +
                                 ['计算结果须经工程校核；尺寸预览不能直接用于加工。']))
    story.extend(paragraph(warning, 'warning') for warning in warnings)
    story += [PageBreak(), paragraph('铁心与尺寸', 'h2')]
    try:
        story.append(Geometry(p, font))
        story.append(paragraph('铁心尺寸按比例绘制，槽位为示意；图中尺寸单位为 mm。', 'small'))
    except InputError:
        story.append(paragraph('此历史记录不满足当前几何校验，保留原记录，不绘制尺寸图。', 'warning'))
    geometry_keys = ['D1', 'Di1', 'Di2', 'g', 'L', 'Q1', 'Q2', 'a', 'ZZ', 'JJ', 'Djj', 'MM', 'Dmm']
    story.append(table(['参数', '输入', '单位'], [(field_info(k)['label'], p.get(k), field_info(k)['unit']) for k in geometry_keys],
                       [WIDTH * .48, WIDTH * .32, WIDTH * .2]))
    curves = [curve for curve in snapshot.get('curves', []) if curve.get('display') is not False]
    if curves:
        story += [PageBreak(), paragraph('性能曲线', 'h2')]
        for index, curve in enumerate(curves):
            if index and index % 2 == 0:
                story += [PageBreak(), paragraph('性能曲线（续）', 'h2')]
            # Validate all figure strings before drawing on the canvas.
            plot = curve.get('plot', {})
            for label in [curve.get('x_label', ''), curve.get('y_label', ''), plot.get('x_label', ''),
                          *(axis['label'] for axis in plot.get('y_axes', [])),
                          *(series['name'] for series in plot.get('series', curve['series']))]:
                paragraph(label)
            story.append(KeepTogether([paragraph(curve['title'], 'caption'), Curve(curve, font)]))
            if plot.get('note'):
                story.append(paragraph(plot['note'], 'small'))
    story += [PageBreak(), paragraph('完整输入', 'h2'),
              table(['字段', '参数', '输入', '单位'], [(k, field_info(k)['label'], v, field_info(k)['unit']) for k, v in p.items() if k not in ELEMENT_KEYS],
                    [WIDTH * .16, WIDTH * .38, WIDTH * .30, WIDTH * .16])]
    if any(key in p for key in ELEMENT_KEYS):
        story += [paragraph('自定义槽形元素', 'h2'),
                  table(['元素索引', 'N 元素号', 'A 元素底', 'B 元素宽', 'L 元素高'],
                        [(i, *(p.get(f'{key}{i}', '-') for key in ('N', 'A', 'B', 'L'))) for i in range(1, 10)],
                        [WIDTH / 5] * 5)]
    story += [PageBreak(), paragraph('完整计算输出', 'h2'),
              table(['字段', '指标', '计算值', '单位'], [(k, field_info(k)['label'], v, field_info(k)['unit']) for k, v in values.items()],
                    [WIDTH * .16, WIDTH * .42, WIDTH * .26, WIDTH * .16])]
    provenance = [paragraph('计算来源与版本', 'h2')]
    for key, value in snapshot.get('provenance', {}).items():
        provenance.append(paragraph(key + '：' + plain(value)))
    provenance.append(paragraph('报告直接读取保存的输入和结果，不使用工作台中后来修改的参数。数值以最多 8 位有效数字展示；完整精度可通过计算记录 API 获取。', 'small'))
    story.append(KeepTogether(provenance))

    def page(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor('#dce5e8')); canvas.setLineWidth(.5)
        canvas.line(16 * mm, 16 * mm, A4[0] - 16 * mm, 16 * mm)
        canvas.setFont(font, 7); canvas.setFillColor(MUTED)
        canvas.drawString(16 * mm, 11 * mm, 'DCSJ / ' + snapshot['calculation_id'][:18])
        canvas.drawRightString(A4[0] - 16 * mm, 11 * mm, f'第 {doc.page} 页')
        canvas.restoreState()

    stream = BytesIO()
    document = SimpleDocTemplate(stream, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm,
                                 topMargin=16 * mm, bottomMargin=22 * mm, title=plain(snapshot['name']),
                                 author='DCSJ', pageCompression=1)
    document.build(story, onFirstPage=page, onLaterPages=page)
    return stream.getvalue()


def render(snapshot):
    if not _RENDER_SLOT.acquire(blocking=False):
        raise BusyError('PDF 正在生成，请稍后重试。')
    try:
        return _render(snapshot)
    finally:
        _RENDER_SLOT.release()
