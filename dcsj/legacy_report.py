"""Classic engineering calculation sheets, drawn identically in HTML and PDF.

Only saved inputs/outputs are used. This renderer never executes the numerical
kernel or resolves a current material, slot, or model resource.
"""
from copy import deepcopy
from html import escape
from io import BytesIO
import math
import unicodedata
from itertools import groupby

from . import report_summary as summary
from .kernel_layout import OUTPUT_MAP
from .parameters import ELEMENT_KEYS
from .wire_selection import recorded

WIDTH, HEIGHT = 540, 790


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
    def __init__(self, title):
        self.title, self.items = title, []
        self.border = True

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

    def panel(self, panel, x, y, scale=1):
        self.items.append(dict(kind='panel', panel=panel, x=x, y=y, scale=scale))

    def footer(self, identity, page, count):
        self.rule(763)
        self.text(12, 778, 'DCSJ · ' + identity, 7.2, width=440)
        self.text(528, 778, f'{page} / {count}', 9, 'end')


def winding_panel():
    """Original-style symbolic coil/end-ring views; no invented dimensions."""
    p = dict(width=230, height=300, title='绕组与端环示意', description='符号示意，数值见尺寸栏', paths=[], lines=[], texts=[])
    def text(x,y,label,size=9,anchor='middle'):
        p['texts'].append(dict(x=x,y=y,text=label,size=size,anchor=anchor))
    def path(commands):
        p['paths'].append(dict(commands=commands,stroke='#000',fill='none'))
    def line(x1,y1,x2,y2,dash=False):
        p['lines'].append(dict(x1=x1,y1=y1,x2=x2,y2=y2,**({'dash':[3,2]} if dash else {})))
    def dim(x1,y1,x2,y2,label):
        line(x1,y1,x2,y2)
        if y1==y2:
            line(x1,y1-3,x1,y1+3);line(x2,y2-3,x2,y2+3);text((x1+x2)/2,y1+12,label)
        else:
            line(x1-3,y1,x1+3,y1);line(x2-3,y2,x2+3,y2);text(x1+8,(y1+y2)/2,label,8,'start')
    text(115,16,'定子线圈')
    path([['M',50,32],['L',180,32],['C',218,32,218,83,180,83],['L',50,83],['C',12,83,12,32,50,32],['Z']])
    dim(50,94,180,94,'L');dim(115,37,115,79,'Ty')
    path([['M',47,150],['L',94,150],['C',120,138,151,114,173,125],['C',197,137,186,155,170,161],['L',94,176],['L',47,176]])
    for x in (47,65,94):line(x,133,x,191)
    dim(47,190,65,190,'d');dim(94,190,183,190,'fd')
    text(115,222,'转子导条与端环')
    path([['M',45,240],['L',173,240],['L',173,270],['L',45,270]])
    path([['M',139,229],['L',139,281],['L',173,281],['L',173,229],['Z']])
    for x in range(141,172,8):line(x,241,min(x+14,173),268)
    line(30,255,196,255,True);dim(190,240,190,270,'Dr')
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


def _classic_text(page, text, y, size):
    from .classic_sheet import byte_width
    # Chinese occupies two original report cells, ASCII one. Separate runs keep
    # all numeric columns aligned in both SVG and embedded-font PDF output.
    cell=size*.6;x=(WIDTH-76*cell)/2
    for ascii_run,chars in groupby(text,key=lambda ch:ord(ch)<128):
        run=''.join(chars);page.text(x,y,run,size)
        item=page.items[-1];item['width']=byte_width(run)*cell
        item['advance_scale']=1 if ascii_run else 1.2
        x+=item['width']


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
    from .classic_sheet import native_pages, material_line, byte_width, frame
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
    if recorded(snapshot)['mode']=='target_fill':
        extra.extend(_classic_wrap('自动选线目标 Sf='+plain(snapshot['inputs'].get('_wire_target'))+'；原输入线规 '+
            ' / '.join(f'{plain(snapshot["inputs"].get(a))}×{plain(snapshot["inputs"].get(b))}' for a,b in [('JJ','Djj'),('MM','Dmm')])+'；CC 为实际采用线规。'))
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
    for number,lines in enumerate([first,*remaining]):
        page=Sheet('电磁计算单 · 原版'+('（名义尺寸）' if snapshot.get('optimization',{}).get('robustness') else ''))
        page.border=False
        # Native first sheet has 21 drawing rows and 34 compact numeric rows.
        # Fill each A4 page vertically; font width is stable across both pages.
        size=11.2;step=min(15.2,742/max(1,len(lines)-1))
        for index,line in enumerate(lines):
            line=material_line(line,snapshot)
            if line.startswith('*') and line.endswith('*') and line.strip('* .'):
                inside=line[1:-1].rstrip()
                # Long user identifiers are preserved in the continuation below.
                if byte_width(inside)>74:inside=_classic_wrap(inside,71)[0]+'...'
                line='*'+inside+' '*max(0,74-byte_width(inside))+'*'
            _classic_text(page,line,18+index*step,size)
        if number==0:
            graphic_height=step*20.6
            page.panel(combined_slots(snapshot['inputs']),15,24,graphic_height/300)
            coils=winding_panel()
            for item in coils['texts']:
                if item['text']=='L':item['text']='LB'
                if item['text']=='fd':item['text']='Fd'
                if item['text']=='转子导条与端环':item['text']='转子线圈'
            page.panel(coils,287,24,graphic_height/300)
        pages.append(page)
    for index,page in enumerate(pages,1):
        page.text(12,781,'DCSJ · '+snapshot['calculation_id'],6.5,width=430)
        page.text(528,781,f'{index} / {len(pages)}',8,'end')
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
    return svg(monochrome).replace('<svg ',f'<svg width="{panel["width"]}" height="{panel["height"]}" ',1).replace('fill="#263942"','fill="#000"')


def sheet_svg(page):
    out=[f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-label="{escape(page.title,quote=True)}">',
         '<rect x="4" y="4" width="532" height="782" fill="white" stroke="'+('black' if page.border else 'none')+'" stroke-width=".55" stroke-dasharray="1 3"/>']
    for item in page.items:
        if item['kind']=='text':
            out.append(f'<text x="{item["x"]}" y="{item["y"]}" font-size="{item["size"]}" text-anchor="{item["anchor"]}"'+
                       (f' textLength="{item["width"]}" lengthAdjust="spacingAndGlyphs"' if item['value'] else '')+
                       ' xml:space="preserve">'+escape(item['value'])+'</text>')
        elif item['kind']=='line':
            out.append(f'<line x1="{item["x1"]}" y1="{item["y1"]}" x2="{item["x2"]}" y2="{item["y2"]}" stroke="black" stroke-width=".55"'+(' stroke-dasharray="1 3"' if item['dash'] else '')+'/>')
        else:
            out.append(f'<g transform="translate({item["x"]} {item["y"]}) scale({item["scale"]})">'+panel_svg(item['panel'])+'</g>')
    return ''.join(out)+'</svg>'


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
        'body{margin:0;background:#e8e8e8;color:#000;font-family:"Courier New","SimSun",monospace}.toolbar{position:sticky;top:0;z-index:2;padding:12px;text-align:center;background:#20343d;color:white;font:14px system-ui}.toolbar a,.toolbar button{display:inline-block;margin:0 5px;padding:8px 16px;border:1px solid #c8d1d5;border-radius:4px;background:white;color:#17323f;text-decoration:none;cursor:pointer;font:inherit}.toolbar small{display:block;margin-top:8px}.sheet{box-sizing:border-box;width:186mm;height:272mm;margin:18px auto;background:white}.sheet>svg{display:block;width:100%;height:100%;font-family:"Courier New","SimSun",monospace}'+\
        '@page{size:A4 portrait;margin:12mm}@media print{body{background:white}.toolbar{display:none}.sheet{margin:0 auto;break-after:page;break-inside:avoid}.sheet:last-child{break-after:auto}*{-webkit-print-color-adjust:exact;print-color-adjust:exact}}'+\
        '@media screen and (max-width:740px){.sheet{width:100%;height:auto;aspect-ratio:186/272}}'+\
        '</style></head><body><nav class="toolbar"><a href="'+escape(base,quote=True)+'">新版打印</a><button type="button" onclick="window.print()">旧版打印</button>'+download+\
        '<small>A4 纵向 · 100% 比例 · 关闭浏览器页眉页脚'+old_note+'</small></nav><main>'+''.join('<section class="sheet">'+sheet_svg(p)+'</section>' for p in pages)+'</main></body></html>'


def render_pdf(record, *, tx=False):
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from .pdf_report import _font, _RENDER_SLOT, Slot, PDFContentError
    from .execution import BusyError
    if not _RENDER_SLOT.acquire(blocking=False):raise BusyError('PDF 正在生成，请稍后重试。')
    try:
        font,cmap=_font();pages=tx_sheets(record) if tx else main_sheets(record)
        stream=BytesIO();c=canvas.Canvas(stream,pagesize=A4,pageCompression=1)
        c.setTitle(plain(record['name'])+' · 旧版打印');c.setAuthor('DCSJ')
        scale=min(186*mm/WIDTH,272*mm/HEIGHT)
        left=(A4[0]-WIDTH*scale)/2;bottom=(A4[1]-HEIGHT*scale)/2
        for page in pages:
            c.saveState();c.translate(left,bottom);c.scale(scale,scale)
            if page.border:
                c.setLineWidth(.55);c.setDash([1,3]);c.rect(4,4,532,782);c.setDash([])
            for item in page.items:
                if item['kind']=='text':
                    x=item['x']-({'start':0,'middle':.5,'end':1}[item['anchor']])*item['width']
                    text=c.beginText()
                    for ascii_run,chars in groupby(item['value'],key=lambda ch:ord(ch)<128):
                        run=''.join(chars);face='Courier' if ascii_run else font
                        for ch in run:
                            if not ascii_run and not ch.isspace() and not cmap.get(ord(ch)):
                                raise PDFContentError(f'PDF 字体不支持字符 U+{ord(ch):04X}')
                        advance=item['size']*len(run)*(.6 if ascii_run else 1)*item.get('advance_scale',1)
                        actual=stringWidth(run,face,item['size'])
                        text.setTextOrigin(x,HEIGHT-item['y']);text.setFont(face,item['size'])
                        text.setHorizScale(100*advance/actual if actual else 100)
                        text.textOut(run);x+=advance
                    c.drawText(text)
                elif item['kind']=='line':
                    c.setDash([1,3] if item['dash'] else []);c.setLineWidth(.55)
                    c.line(item['x1'],HEIGHT-item['y1'],item['x2'],HEIGHT-item['y2']);c.setDash([])
                else:
                    panel=deepcopy(item['panel'])
                    for part in [*panel['paths'],*panel['lines']]:part['stroke']='#000'
                    c.saveState();c.translate(item['x'],HEIGHT-item['y']-panel['height']*item['scale']);c.scale(item['scale'],item['scale'])
                    Slot(panel,font).drawOn(c,0,0);c.restoreState()
            c.restoreState();c.showPage()
        c.save();return stream.getvalue()
    finally:_RENDER_SLOT.release()
