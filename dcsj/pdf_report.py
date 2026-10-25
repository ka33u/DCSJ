"""Offline A4 PDF generation with embedded CJK fonts and vector engineering figures."""
from functools import lru_cache
from html import escape
from io import BytesIO
import hashlib
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
from .parameters import field_info, ELEMENT_KEYS
from . import report_summary as summary
from .slot_diagrams import slot_drawings

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
    try:
        stat = path.stat()
        return _load_font(str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    except OSError as error:
        raise PDFUnavailableError('PDF 字体无法访问，请检查 DCSJ_PDF_FONT 和文件权限。') from error


class Slot(Flowable):
    def __init__(self, panel, font):
        super().__init__()
        self.panel, self.font = panel, font
        self.width, self.height = panel['width'], panel['height']

    def draw(self):
        c, panel = self.canv, self.panel
        c.saveState()
        for item in panel['paths']:
            path = c.beginPath()
            for op, *coords in item['commands']:
                coords = [self.height - n if i % 2 else n for i, n in enumerate(coords)]
                {'M': path.moveTo, 'L': path.lineTo, 'C': path.curveTo, 'Z': path.close}[op](*coords)
            c.setStrokeColor(colors.HexColor(item['stroke'])); c.setLineWidth(.85)
            fill = item.get('fill', 'none')
            if fill != 'none':
                c.setFillColor(colors.HexColor(fill))
            c.drawPath(path, stroke=1, fill=int(fill != 'none'))
        for line in panel['lines']:
            c.setStrokeColor(colors.HexColor(line.get('stroke', '#687984'))); c.setLineWidth(.45)
            c.setDash(line.get('dash', []))
            c.line(line['x1'], self.height-line['y1'], line['x2'], self.height-line['y2'])
        c.setDash([]); c.setFillColor(INK)
        for item in panel['texts']:
            c.setFont(self.font, item.get('size', 6.5))
            draw = {'start': c.drawString, 'middle': c.drawCentredString, 'end': c.drawRightString}[item.get('anchor', 'start')]
            draw(item['x'], self.height-item['y'], item['text'])
        c.restoreState()


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
            c.drawString(43, 180, '此曲线未产生有效采样点。')
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
        'title': ParagraphStyle('title', fontName=font, fontSize=16, leading=21, textColor=INK,
                                spaceAfter=4, wordWrap='CJK', keepWithNext=True),
        'h2': ParagraphStyle('h2', fontName=font, fontSize=13.5, leading=19, textColor=TEAL,
                             spaceBefore=13, spaceAfter=9, wordWrap='CJK', keepWithNext=True),
        'caption': ParagraphStyle('caption', fontName=font, fontSize=9, leading=13, textColor=INK,
                                  spaceBefore=10, spaceAfter=4, wordWrap='CJK', keepWithNext=True),
        'cell': ParagraphStyle('cell', fontName=font, fontSize=7.4, leading=10.8, textColor=INK,
                               wordWrap='CJK', splitLongWords=True),
        'sh2': ParagraphStyle('sh2', fontName=font, fontSize=10, leading=13, textColor=TEAL,
                              spaceBefore=7, spaceAfter=3, wordWrap='CJK', keepWithNext=True),
        'compact': ParagraphStyle('compact', fontName=font, fontSize=7.2, leading=10, textColor=INK,
                                  wordWrap='CJK', splitLongWords=True),
        'small': ParagraphStyle('small', fontName=font, fontSize=7.4, leading=10.5, textColor=MUTED,
                                spaceAfter=4, wordWrap='CJK', splitLongWords=True),
    }

    def paragraph(value, style='body'):
        return checked_paragraph(value, styles[style], cmap)

    def table(headers, rows, widths, compact=False):
        style = 'compact' if compact else 'cell'
        grid = ([[paragraph(h, style) for h in headers]] if headers else []) + [[paragraph(v, style) for v in row] for row in rows]
        result = Table(grid, colWidths=widths, repeatRows=int(bool(headers)), splitInRow=1, hAlign='LEFT')
        result.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 4 if compact else 6), ('RIGHTPADDING', (0, 0), (-1, -1), 4 if compact else 6),
            ('TOPPADDING', (0, 0), (-1, -1), 2 if compact else 3.5), ('BOTTOMPADDING', (0, 0), (-1, -1), 2 if compact else 3.5),
            ('LINEBELOW', (0, 0), (-1, -1), .3, colors.HexColor('#dce5e8')),
        ]))
        if headers:
            result.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), PALE), ('LINEBELOW', (0, 0), (-1, 0), .7, TEAL)]))
        return result

    def summary_table(items):
        result = table([], summary.grid(items), [WIDTH * .18, WIDTH * (1/3 - .18)] * 3, compact=True)
        result.setStyle(TableStyle([('BACKGROUND', (col, 0), (col, -1), colors.HexColor('#f4f7f6')) for col in (0, 2, 4)]))
        return result

    p, values = snapshot['inputs'], snapshot['values']
    nominal = '（名义尺寸）' if snapshot.get('optimization', {}).get('robustness') else ''
    story = [paragraph('DCSJ / 电磁设计计算报告' + nominal, 'small'), paragraph(snapshot['name'], 'title'),
             paragraph('计算时间（北京时间）：' + summary.local_time(snapshot['created_at']) +
                       '    设计版本：' + summary.number(snapshot.get('project_revision')), 'small'),
             paragraph('额定条件', 'sh2'), paragraph('型号：' + summary.number(p.get('SizeX')), 'compact'),
             summary_table(summary.ratings(p)), paragraph('铁心与槽型', 'sh2')]
    panels = slot_drawings(p)
    for panel in panels:
        for item in panel['texts']:
            paragraph(item['text'])
    dims = table([], [(label, value + (' ' + unit if unit else '')) for label, value, unit in summary.dimensions(p)],
                 [(WIDTH-360)*.55, (WIDTH-360)*.45], compact=True)
    figures = Table([[*(Slot(panel, font) for panel in panels), dims]], colWidths=[180, 180, WIDTH-360], hAlign='LEFT')
    figures.setStyle(TableStyle([
        ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('BOX', (0, 0), (-1, -1), .4, colors.HexColor('#dce5e8')),
        ('INNERGRID', (0, 0), (-1, -1), .3, colors.HexColor('#dce5e8'))]))
    story.append(figures)
    if snapshot.get('slots'):
        story += [table(summary.SLOT_HEADERS, summary.slot_rows(snapshot),
                        [WIDTH*f for f in (.06,.26,.22,.16,.15,.15)], compact=True),
                  paragraph(summary.SLOT_AREA_NOTE, 'small')]
    story += [paragraph('绕组与导线', 'sh2'), summary_table(summary.windings(snapshot))]
    if summary.wire_note(snapshot):
        story.append(paragraph(summary.wire_note(snapshot), 'small'))
    story += [paragraph('设计目标与计算结果', 'sh2'),
              table(['指标', '设计目标', '计算结果', '差值 / 峰值位置', '单位'], summary.targets(snapshot),
                    [WIDTH*.30, WIDTH*.18, WIDTH*.18, WIDTH*.24, WIDTH*.10], compact=True),
              paragraph('差值＝计算结果−设计目标；效率与槽满率的差值单位为百分点。s 表示峰值处转差率。', 'small'),
              paragraph('运行与电磁指标', 'sh2'), summary_table(summary.performance(values))]
    curves = [curve for curve in snapshot.get('curves', []) if curve.get('display') is not False]
    robust = summary.robustness_summary(snapshot)
    if robust:
        story += [PageBreak(), paragraph('公差情景评估', 'h2'), paragraph(summary.TOLERANCE_NOTE),
                  paragraph(robust['text']), paragraph(robust['note'], 'small'),
                  table(['目标', '最不利采样值', '情景编号'], robust['objectives'], [WIDTH/3]*3),
                  table(['指标', '采样最小值', '采样最大值', '完整'], robust['ranges'], [WIDTH/4]*4),
                  table(summary.TOLERANCE_HEADERS, robust['rows'], [WIDTH*f for f in (.15,.21,.20,.24,.20)])]
    manufacturing = summary.manufacturing_summary(snapshot)
    if manufacturing:
        story += [PageBreak(), paragraph('寻优规格与尺寸联动', 'h2'), paragraph(manufacturing['text']), paragraph(manufacturing['note'], 'small')]
        if manufacturing['rows']:
            story.append(table(summary.MANUFACTURING_HEADERS, manufacturing['rows'], [WIDTH*f for f in (.10,.56,.20,.14)]))
    cycle = summary.cycle_summary(snapshot)
    if cycle:
        story += ([] if manufacturing else [PageBreak()]) + [paragraph(('名义尺寸 · ' if robust else '') + '负载周期能耗', 'h2'), paragraph(cycle['text']), paragraph(cycle['note'], 'small')]
        if cycle['rows']:
            story.append(table(summary.CYCLE_HEADERS, cycle['rows'], [WIDTH/6]*6))
    if curves:
        story += ([] if cycle and not manufacturing and not robust else [PageBreak()]) + [paragraph(('名义尺寸 · ' if robust else '') + '性能曲线', 'h2')]
        story.append(paragraph(summary.CURVE_NOTE, 'small'))
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
    provenance = [paragraph('计算来源与版本', 'h2'), paragraph('记录 ID：' + snapshot['calculation_id'], 'small')]
    for key, value in snapshot.get('provenance', {}).items():
        provenance.append(paragraph(key + '：' + plain(value)))
    provenance.append(paragraph('报告直接读取保存的输入和结果，不使用工作台中后来修改的参数。数值以最多 8 位有效数字展示；完整精度可通过计算记录 API 获取。', 'small'))
    story.extend(provenance)

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
