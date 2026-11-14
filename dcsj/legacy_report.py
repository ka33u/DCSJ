"""Classic engineering calculation sheets, drawn identically in HTML and PDF.

Only saved inputs/outputs are used. This renderer never executes the numerical
kernel or resolves a current material, slot, or model resource.
"""
from base64 import b64encode
from copy import deepcopy
from html import escape
from io import BytesIO
import math
import unicodedata
from itertools import groupby

from . import classic_type
from . import report_summary as summary
from .kernel_layout import OUTPUT_MAP
from .parameters import ELEMENT_KEYS
from .wire_selection import recorded

WIDTH, HEIGHT = 540, 790
A4_WIDTH_MM, A4_HEIGHT_MM = 210, 297
CLASSIC_BODY_WIDTH_MM = 150
CLASSIC_FIRST_BASELINE_MM = 19
CLASSIC_LAST_BASELINE_MM = 280
CLASSIC_PAGE_NUMBER_MM = 286


def plain(value):
    if value is None:
        return '-'
    if isinstance(value, float):
        return f'{value:.6g}' if math.isfinite(value) else '-'
    return ''.join(ch for ch in str(value) if ch in '\n\t' or unicodedata.category(ch)[0] != 'C')


def units(value):
    return sum(1 if ord(ch) < 128 else 1 / .6 for ch in value)


def wrap(value, limit):
    """Wrap at measured monospace cells without throwing away a single character."""
    result = []
    for original in plain(value).expandtabs(4).split('\n'):
        line = ''; used = 0
        for ch in original:
            advance = 1 if ord(ch) < 128 else 1 / .6
            if line and used + advance > limit:
                result.append(line); line = ''; used = 0
            line += ch; used += advance
        result.append(line)
    return result


class Sheet:
    def __init__(self, title, *, height=HEIGHT, scale_mm=None):
        self.title, self.items = title, []
        self.border = True
        self.height = height
        self.scale_mm = scale_mm if scale_mm is not None else min(186 / WIDTH, 272 / HEIGHT)

    @property
    def paper_origin(self):
        """Identical physical placement in the browser preview, print and PDF."""
        return ((A4_WIDTH_MM - WIDTH * self.scale_mm) / 2,
                (A4_HEIGHT_MM - self.height * self.scale_mm) / 2)

    def text(self, x, y, value, size=10, anchor='start', width=None):
        value = plain(value)
        natural = units(value) * size * .6
        if width and natural > width:
            size *= width / natural
        self.items.append(dict(kind='text', x=x, y=y, value=value, size=size,
                               anchor=anchor, width=units(value)*size*.6))

    def line(self, x1, y1, x2, y2, dash=False):
        self.items.append(dict(kind='line', x1=x1, y1=y1, x2=x2, y2=y2, dash=dash))

    def rule(self, y):
        self.line(10, y, WIDTH-10, y, True)

    def panel(self, panel, x, y, scale=1, scale_y=None):
        self.items.append(dict(kind='panel', panel=panel, x=x, y=y, scale=scale, scale_y=scale if scale_y is None else scale_y))

    def footer(self, identity, page, count):
        self.rule(763)
        self.text(12, 778, 'DCSJ · ' + identity, 7.2, width=440)
        self.text(528, 778, f'{page} / {count}', 9, 'end')


def winding_panel():
    """Vector equivalents of the three symbolic views on the original winding plate."""
    p = dict(width=234, height=353, title='定子与转子线圈', description='线圈端部、端环及尺寸符号',
             paths=[], lines=[], texts=[])
    def line(x1, y1, x2, y2):
        p['lines'].append(dict(x1=x1, y1=y1, x2=x2, y2=y2))
    def path(*commands, fill='none'):
        p['paths'].append(dict(commands=commands, stroke='#000', fill=fill))
    def label(x, y, value, rotate=0):
        p['texts'].append(dict(x=x, y=y, text=value, size=13, anchor='middle', rotate=rotate))
    def arrow(x, y, dx, dy):
        length, half = 8, 1.2
        path(['M',x,y], ['L',x+length*dx-half*dy,y+length*dy+half*dx],
             ['L',x+length*dx+half*dy,y+length*dy-half*dx], ['Z'], fill='#000')
    def horizontal(x1, x2, y, value, outside=False):
        line(x1-15 if outside else x1,y,x2+15 if outside else x2,y)
        arrow(x1,y,-1 if outside else 1,0)
        arrow(x2,y,1 if outside else -1,0)
        label((x1+x2)/2,y-4,value)

    line(14,6,229,6)
    label(118,26,'定子线圈')
    path(['M',59,39], ['L',179,39], ['C',217,39,217,96,179,96],
         ['L',59,96], ['C',21,96,21,39,59,39], ['Z'])
    for x in (59,179):
        line(x,96,x,129)
    horizontal(59,179,122,'LB')
    line(119,39,119,96)
    arrow(119,39,0,1)
    arrow(119,96,0,-1)
    label(112,67,'Ty',-90)

    path(['M',104,166], ['L',120,166], ['L',178,142], ['C',202,135,216,174,191,183],
         ['L',120,192], ['L',104,192])
    line(31,206,104,206)
    for x, y in [(49,194),(68,180),(86,167),(104,154)]:
        line(x,y,x,206)
    for x, y in [(104,192),(120,192),(205,165)]:
        line(x,y,x,225)
    horizontal(104,120,219,'d',outside=True)
    horizontal(120,205,219,'Fd')

    label(116,253,'转子线圈')
    path(['M',35,265], ['L',132,265], ['L',132,333])
    path(['M',132,279], ['L',169,279], ['L',169,319], ['L',132,319], ['Z'])
    path(['M',169,285], ['L',198,285], ['L',198,313], ['L',169,313])
    for x, y in [(132,290),(132,304),(132,318),(145,319),(159,319)]:
        n=min(169-x,y-279)
        line(x,y,x+n,y-n)
    line(70,299,219,299)
    for x, h in [(87,9),(101,16),(117,23)]:
        line(x,299-h,x,299+h)
    line(211,299,211,334)
    arrow(211,299,0,1)
    label(206,327,'Dr',-90)
    line(14,346,229,346)
    return p

def field(data, key, label=None):
    return f'{label or key}={plain(data.get(key))}'



def supplemental_rows(snapshot):
    """Retain newer saved search/verification semantics after the classic sheets."""
    rows=[]
    def paragraph(value):
        if value:rows.extend(('cells',[line]) for line in _classic_wrap(value))
    def table(headers,values):
        from .classic_sheet import byte_width
        for values_row in values:
            line=''
            for header,value in zip(headers,values_row):
                cell=f'{header}={plain(value)}'
                if line and byte_width(line+'；'+cell)>74:
                    paragraph(line);line=''
                line+=(('；' if line else '')+cell)
            paragraph(line)
    for key,_,_ in summary.TORQUE_MODES:
        result=summary.torque_summary(snapshot,key)
        if not result:continue
        rows.append(('heading',result['title']));paragraph(result['text']);paragraph(result['note'])
        table(result['headers'],result['rows'])
        if result['diagnostic_note']:
            paragraph(result['diagnostic_note']);table(summary.TORQUE_DIAGNOSTIC_HEADERS,result['diagnostic_rows'])
    robust=summary.robustness_summary(snapshot)
    if robust:
        rows.append(('heading',robust['title']));paragraph(robust['text']);paragraph(robust['note'])
        table(['目标','最不利采样值','情景编号'],robust['objectives'])
        table(['指标','采样最小值','采样最大值','完整'],robust['ranges'])
        table(summary.TOLERANCE_HEADERS,robust['rows'])
    geometry=summary.slot_search_summary(snapshot)
    if geometry:
        rows.append(('heading',geometry['title']));paragraph(geometry['note'])
        table(summary.SLOT_SEARCH_HEADERS,geometry['rows'])
        if geometry['sampled_rows']:
            paragraph('已保存情景的几何余量')
            table(geometry['sampled_headers'],geometry['sampled_rows'])
    constraints=summary.search_constraints_summary(snapshot)
    if constraints:
        rows.append(('heading','寻优约束校核'));paragraph(constraints['note'])
        table(summary.SEARCH_CONSTRAINT_HEADERS,constraints['rows'])
    manufacturing=summary.manufacturing_summary(snapshot)
    if manufacturing:
        rows.append(('heading','寻优规格与尺寸联动'));paragraph(manufacturing['text']);paragraph(manufacturing['note'])
        table(summary.MANUFACTURING_HEADERS,manufacturing['rows'])
    cycle=summary.cycle_summary(snapshot)
    if cycle:
        rows.append(('heading','负载周期能耗'));paragraph(cycle['text']);paragraph(cycle['note'])
        table(summary.CYCLE_HEADERS,cycle['rows'])
    pricing=summary.material_price_summary(snapshot)
    if pricing:
        rows.append(('heading','寻优材料计价'));paragraph(pricing['text'])
        table(['材料引用','硅钢单价 /kg','本次采用'],pricing['rows'])
    return rows


def _classic_text(page, text, y):
    from .classic_sheet import COLUMNS, byte_width
    x = (WIDTH - COLUMNS * classic_type.CELL) / 2
    for _, chars in groupby(text, key=lambda ch: ord(ch) < 128):
        run = ''.join(chars)
        page.text(x, y, run, classic_type.SIZE)
        item = page.items[-1]
        item.update(classic=True, width=byte_width(run) * classic_type.CELL)
        x += item['width']


def _classic_wrap(text, width=74):
    from .classic_sheet import byte_width
    lines=[];current=''
    for ch in plain(text).expandtabs(4):
        if ch=='\n':
            lines.append(current);current='';continue
        if byte_width(current+ch)>width:
            lines.append(current);current=''
        current+=ch
    return lines+[current]


def main_sheets(snapshot):
    from .classic_sheet import native_pages, display_band_line, byte_width
    from .classic_drawing import combined_slots
    (first,second),complete=native_pages(snapshot)
    extra=[]
    for side in snapshot.get('slots',{}).get('entries',[]):
        extra.extend(_classic_wrap(f'{"定子" if side["side"]=="stator" else "转子"}槽型：{side["name"]} / {side.get("drawing") or "-"}；槽面积 {plain(side["area"].get("value_mm2"))} mm2'))
    if not complete:extra.append('历史记录未保存原版中间明细，未记录项以 - 表示；重新计算可补齐。')
    for key in ('SizeX','Paihao'):
        if byte_width(str(snapshot['inputs'].get(key,'')))>(12 if key=='SizeX' else 9):
            extra.extend(_classic_wrap(key+'='+str(snapshot['inputs'][key])))
    if any(snapshot['inputs'].get(key) not in (0,None) for key in ELEMENT_KEYS):
        extra.append('自定义槽形元素（本次输入）')
        extra.extend('  '.join(field(snapshot['inputs'],key) for key in ELEMENT_KEYS[i:i+4]) for i in range(0,len(ELEMENT_KEYS),4))
    wire = recorded(snapshot)
    if wire['mode']=='target_fill':
        if complete:
            wire_note = '首页及 CC 线规取自保存的原版报表。'
        elif wire.get('used'):
            wire_note = 'CC 为实际采用线规。'
        else:
            wire_note = '旧记录未保存实际采用线规，首页及 CC 线规以 - 表示；重新计算可补齐。'
        extra.extend(_classic_wrap('自动选线目标 Sf='+plain(snapshot['inputs'].get('_wire_target'))+'；原输入线规 '+
            ' / '.join(f'{plain(snapshot["inputs"].get(a))}×{plain(snapshot["inputs"].get(b))}' for a,b in [('JJ','Djj'),('MM','Dmm')])+'；'+wire_note))
    for key,resolved in snapshot.get('loss_semantics',{}).items():
        if resolved.get('mode')!='legacy' or resolved.get('speed_scaling',{}).get('enabled'):
            extra.extend(_classic_wrap(f'原输入 {key}={plain(resolved.get("input"))} {resolved.get("resolved_unit","")}；BB 核内比例={plain(resolved.get("kernel_pu"))}'))
    for key in snapshot['values']:
        if key not in OUTPUT_MAP:extra.extend(_classic_wrap(field(snapshot['values'],key)))
    for kind,row in supplemental_rows(snapshot):
        extra.extend(_classic_wrap(row if kind=='heading' else '  '.join(row)))
    second=second+['']+extra if extra else second
    pages=[]
    # Keep the original second sheet intact; balance only additional sheets.
    remaining=[second[:60]]
    tail=second[60:]
    if tail:
        count=math.ceil(len(tail)/60);length=math.ceil(len(tail)/count)
        remaining.extend(tail[i:i+length] for i in range(0,len(tail),length))
    from .classic_print_assets import drawings, slot_picture
    art = drawings()
    sheets = [first, *remaining]
    frame_width = 76 * classic_type.CELL
    scale_mm = CLASSIC_BODY_WIDTH_MM / frame_width
    first_y = CLASSIC_FIRST_BASELINE_MM / scale_mm
    # Reserve the same physical margins and footer on every A4 page. Extra
    # sheets can shorten the grid, but can never push it into the page number.
    span = (CLASSIC_LAST_BASELINE_MM - CLASSIC_FIRST_BASELINE_MM) / scale_mm
    step = min(13.25, span / max(1, max(map(len, sheets)) - 1))
    left = (WIDTH - frame_width) / 2
    for number,lines in enumerate(sheets):
        page=Sheet('电磁计算单 · 原版'+('（名义尺寸）' if snapshot.get('optimization',{}).get('robustness') else ''),
                   height=A4_HEIGHT_MM / scale_mm, scale_mm=scale_mm)
        page.border=False
        if 'paper' in art and number < 2:
            page.items.append(dict(kind='image', picture=art['paper'], title='',
                                   x=left, y=first_y-12, width=frame_width, height=56 * step + 10))
        for index,line in enumerate(lines):
            line=display_band_line(line,snapshot)
            if line.startswith('*') and line.endswith('*') and line.strip('* .'):
                inside=line[1:-1].rstrip()
                # Long user identifiers are preserved in the continuation below.
                if byte_width(inside)>74:inside=_classic_wrap(inside,71)[0]+'...'
                line='*'+inside+' '*max(0,74-byte_width(inside))+'*'
            _classic_text(page,line,first_y+index*step)
        if number==0:
            height = step * 20.6
            top = first_y + .68 * step
            # Picture2 (slot) and Picture1 (winding) share the 353-pixel
            # height in the original report. Their left edges are 262.57 pixels apart.
            parts = [
                (slot_picture(snapshot['inputs'], art), '定转子槽型组合图',
                 left + .057 * frame_width, lambda: combined_slots(snapshot['inputs'])),
                (art.get('x1'), '定子线圈与转子线圈',
                 left + .057 * frame_width + 262.57 * height / 353, winding_panel),
            ]
            for picture, title, x, fallback in parts:
                if picture:
                    page.items.append(dict(kind='image', picture=picture, title=title, x=x, y=top,
                                           width=height * picture['width'] / picture['height'], height=height))
                else:
                    panel = fallback()
                    page.panel(panel, x, top, height * 234 / (353 * panel['width']), height / panel['height'])
        pages.append(page)
    for index,page in enumerate(pages,1):
        page.text(WIDTH/2,CLASSIC_PAGE_NUMBER_MM / scale_mm,str(index),8,'middle')
    return pages


def tx_sheets(record):
    raw=record['result'].get('report_text','')
    lines=[line for original in raw.splitlines() for line in wrap(original,82)] or ['此历史记录没有保存原始文本报表。']
    count=max(1,math.ceil(len(lines)/58));per_page=math.ceil(len(lines)/count)
    pages=[]
    for start in range(0,len(lines),per_page):
        chunk=lines[start:start+per_page];page=Sheet('双层同心式绕组计算单')
        page.text(270,21,record['name'],12,'middle',516);page.rule(32)
        step=min(20,714/max(1,len(chunk)))
        size=min(12.5,step*.9,516/(max(1,max(units(line) for line in chunk))*.6))
        width=max((units(line)*size*.6 for line in chunk),default=0)
        left=(WIDTH-width)/2
        for i,line in enumerate(chunk):page.text(left,48+i*step,line,size)
        pages.append(page)
    for index,page in enumerate(pages,1):page.footer(record['id'],index,len(pages))
    return pages


def panel_svg(panel):
    from .slot_diagrams import svg
    monochrome=deepcopy(panel)
    for item in [*monochrome['paths'],*monochrome['lines']]:item['stroke']='#000'
    return svg(monochrome).replace('<svg ',f'<svg width="{panel["width"]}" height="{panel["height"]}" ',1).replace('fill="#263942"','fill="#000"').replace('font-family="sans-serif"', 'font-family="SimSun, Songti SC, serif"')


def sheet_svg(page):
    left, top = page.paper_origin
    out=[f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {A4_WIDTH_MM} {A4_HEIGHT_MM}" role="img" aria-label="{escape(page.title,quote=True)}">',
         f'<rect width="{A4_WIDTH_MM}" height="{A4_HEIGHT_MM}" fill="white"/>',
         f'<g transform="translate({left} {top}) scale({page.scale_mm})">']
    if page.border:
        out.append('<rect x="4" y="4" width="532" height="782" fill="white" stroke="black" stroke-width=".55" stroke-dasharray="1 3"/>')
    for item in page.items:
        if item['kind']=='image':
            data = b64encode(item['picture']['png']).decode('ascii')
            out.append(f'<image x="{item["x"]}" y="{item["y"]}" width="{item["width"]}" height="{item["height"]}" '
                       f'preserveAspectRatio="none" href="data:image/png;base64,{data}"><title>{escape(item["title"])}</title></image>')
        elif item.get('classic'):
            x = item['x']
            out.append(f'<text y="{item["y"]}" font-size="{item["size"]}" font-family="{escape(classic_type.FAMILY, quote=True)}" xml:space="preserve">')
            for word, advance in classic_type.runs(item['value']):
                if len(word) > 1 and word == '.' * len(word):
                    centers = ' '.join(f'{x+(i+.5)*classic_type.CELL:.3f}' for i in range(len(word)))
                    out.append(f'<tspan x="{centers}" text-anchor="middle">{word}</tspan>')
                    x += advance
                    continue
                out.append(f'<tspan x="{x:.4f}" textLength="{advance:.4f}" lengthAdjust="spacingAndGlyphs">{escape(word)}</tspan>')
                x += advance
            out.append('</text>')
        elif item['kind']=='text':
            out.append(f'<text x="{item["x"]}" y="{item["y"]}" font-size="{item["size"]}" text-anchor="{item["anchor"]}"'+
                       (f' textLength="{item["width"]}" lengthAdjust="spacingAndGlyphs"' if item['value'] else '')+
                       ' xml:space="preserve">'+escape(item['value'])+'</text>')
        elif item['kind']=='line':
            out.append(f'<line x1="{item["x1"]}" y1="{item["y1"]}" x2="{item["x2"]}" y2="{item["y2"]}" stroke="black" stroke-width=".55"'+(' stroke-dasharray="1 3"' if item['dash'] else '')+'/>')
        else:
            out.append(f'<g transform="translate({item["x"]} {item["y"]}) scale({item["scale"]} {item["scale_y"]})">'+panel_svg(item['panel'])+'</g>')
    return ''.join(out)+'</g></svg>'


def render_html(record, *, tx=False):
    from .pdf_fonts import status
    identity=record['id'] if tx else record['calculation_id']
    base=('/tx/reports/' if tx else '/reports/')+identity
    pdf=('/api/v1/tx/calculations/' if tx else '/api/v1/calculations/')+identity+'/report.pdf?layout=legacy'
    pages=tx_sheets(record) if tx else main_sheets(record)
    old_note='' if tx or record.get('legacy_report',{}).get('status')=='complete' else ' · 此历史记录未保存原版明细，重新计算可补齐'
    download=f'<a href="{escape(pdf,quote=True)}">下载旧版 PDF</a>' if status()['available'] else ''
    return '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'+\
        '<title>'+escape(record['name'])+' · 旧版打印</title><style>'+\
        'body{margin:0;background:#e8e8e8;color:#000;font-family:"Courier New","SimSun",monospace}.toolbar{position:sticky;top:0;z-index:2;padding:12px;text-align:center;background:#20343d;color:white;font:14px system-ui}.toolbar a,.toolbar button{display:inline-block;margin:0 5px;padding:8px 16px;border:1px solid #c8d1d5;border-radius:4px;background:white;color:#17323f;text-decoration:none;cursor:pointer;font:inherit}.toolbar small{display:block;margin-top:8px}.sheet{box-sizing:border-box;width:210mm;height:297mm;margin:18px auto;background:white;box-shadow:0 1px 4px #aaa}.sheet>svg{display:block;width:100%;height:100%;font-family:"Courier New","SimSun",monospace}'+\
        '@page{size:A4 portrait;margin:0}@media print{body{background:white}.toolbar{display:none}.sheet{margin:0 auto;box-shadow:none;break-after:page;break-inside:avoid}.sheet:last-child{break-after:auto}*{-webkit-print-color-adjust:exact;print-color-adjust:exact}}'+\
        '@media screen and (max-width:820px){.sheet{width:100%;height:auto;aspect-ratio:210/297}}'+\
        '</style></head><body><nav class="toolbar"><a href="'+escape(base,quote=True)+'">新版打印</a><button type="button" onclick="window.print()">旧版打印</button>'+download+\
        '<small>A4 纵向 · 100% 比例 · 已内置纸张留白 · 关闭浏览器页眉页脚'+old_note+'</small></nav><main>'+''.join('<section class="sheet">'+sheet_svg(p)+'</section>' for p in pages)+'</main></body></html>'


def render_pdf(record, *, tx=False):
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from .pdf_report import _font, _RENDER_SLOT, Slot, PDFContentError
    from .execution import BusyError
    if not _RENDER_SLOT.acquire(blocking=False):raise BusyError('PDF 正在生成，请稍后重试。')
    try:
        font,cmap=_font();pages=tx_sheets(record) if tx else main_sheets(record)
        song = None if tx else classic_type.song_font()
        stream=BytesIO();c=canvas.Canvas(stream,pagesize=A4,pageCompression=1)
        c.setTitle(plain(record['name'])+' · 旧版打印');c.setAuthor('DCSJ')
        for page in pages:
            scale = page.scale_mm * mm
            left_mm, top_mm = page.paper_origin
            left = left_mm * mm
            height = page.height
            bottom = A4[1] - top_mm * mm - height * scale
            c.saveState();c.translate(left,bottom);c.scale(scale,scale)
            if page.border:
                c.setLineWidth(.55);c.setDash([1,3]);c.rect(4,4,532,782);c.setDash([])
            for item in page.items:
                if item['kind']=='image':
                    c.drawImage(ImageReader(BytesIO(item['picture']['png'])), item['x'], height-item['y']-item['height'],
                                width=item['width'], height=item['height'])
                elif item.get('classic'):
                    x = item['x']
                    text = c.beginText(x, height-item['y'])
                    for word, advance in classic_type.runs(item['value']):
                        if song and all(song[1].get(ord(ch)) for ch in word):
                            face = song[0]
                        elif word.isascii():
                            face = 'Times-Roman'
                        else:
                            face = font
                            for ch in word:
                                if not cmap.get(ord(ch)):
                                    raise PDFContentError(f'PDF 字体不支持字符 U+{ord(ch):04X}')
                        size = item['size']
                        text.setFont(face,size)
                        if len(word) > 1 and word == '.' * len(word):
                            gap = classic_type.CELL - stringWidth('.',face,size)
                            text.setTextOrigin(x + gap/2, height-item['y'])
                            text.setHorizScale(100)
                            text.setCharSpace(gap)
                            text.textOut(word)
                            text.setCharSpace(0)
                            text.setTextOrigin(x + advance, height-item['y'])
                        else:
                            text.setHorizScale(100 * advance / stringWidth(word,face,size))
                            text.textOut(word)
                        x += advance
                    c.drawText(text)
                elif item['kind']=='text':
                    x=item['x']-({'start':0,'middle':.5,'end':1}[item['anchor']])*item['width']
                    text=c.beginText()
                    for ascii_run,chars in groupby(item['value'],key=lambda ch:ord(ch)<128):
                        run=''.join(chars);face='Courier' if ascii_run else font
                        for ch in run:
                            if not ascii_run and not ch.isspace() and not cmap.get(ord(ch)):
                                raise PDFContentError(f'PDF 字体不支持字符 U+{ord(ch):04X}')
                        advance=item['size']*len(run)*(.6 if ascii_run else 1)*item.get('advance_scale',1)
                        actual=stringWidth(run,face,item['size'])
                        text.setTextOrigin(x,height-item['y']);text.setFont(face,item['size'])
                        text.setHorizScale(100*advance/actual if actual else 100)
                        text.textOut(run);x+=advance
                    c.drawText(text)
                elif item['kind']=='line':
                    c.setDash([1,3] if item['dash'] else []);c.setLineWidth(.55)
                    c.line(item['x1'],height-item['y1'],item['x2'],height-item['y2']);c.setDash([])
                else:
                    panel=deepcopy(item['panel'])
                    for part in [*panel['paths'],*panel['lines']]:part['stroke']='#000'
                    c.saveState();c.translate(item['x'],height-item['y']-panel['height']*item['scale_y']);c.scale(item['scale'],item['scale_y'])
                    Slot(panel, song[0] if song and all(song[1].get(ord(ch)) for t in panel['texts'] for ch in t['text']) else font).drawOn(c,0,0);c.restoreState()
            c.restoreState();c.showPage()
        c.save();return stream.getvalue()
    finally:_RENDER_SLOT.release()
