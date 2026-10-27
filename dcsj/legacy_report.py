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
from .parameters import GROUP_KEYS, ELEMENT_KEYS
from .slot_diagrams import slot_drawings
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


def classic_first(snapshot):
    p,v = snapshot['inputs'],snapshot['values']
    used = recorded(snapshot)['used'] or {}
    page = Sheet('电磁设计计算单')
    title='电 磁 设 计 计 算 单'+('（名义尺寸）' if snapshot.get('optimization',{}).get('robustness') else '')
    page.text(WIDTH/2,19,title,13,'middle')
    page.text(WIDTH/2,37,p.get('SizeX',snapshot['name']),11,'middle',510)
    page.rule(44)
    for index,panel in enumerate(slot_drawings(p)):
        page.panel(panel,33,48+155*index,.91)
    page.panel(winding_panel(),273,51,.94)
    page.rule(360)
    d2=p['Di1']-2*p['g'] if summary.finite(p.get('Di1')) and summary.finite(p.get('g')) else None
    geom={**p,'D2':d2}
    # Four compact columns follow the original stator / rotor / winding layout.
    columns=[
        [field(geom,k) for k in ('Q1','D1','Di1','L','L01','b01','bs1','bs2','hs0','hs12','zs1','hw','Si','Wi','czn','kfe')],
        [field(geom,k,l) for k,l in [('Q2','Q2'),('D2','D2'),('Di2','Di2'),('g','g'),('L02','L02'),('bc02','b02'),('br1','br1'),('br2','br2'),('br3','br3'),('br4','br4'),('hr0','hr0'),('hr12','hr12'),('hr3','hr3'),('hr4','hr4'),('zs2','zs2'),('zc','Zc')]],
        [field(p,k) for k in ('Ufai','a','ZZ','Y1')] +
        [f'JJ×Djj={plain(used.get("JJ"))}×{plain(used.get("Djj"))}',f'MM×Dmm={plain(used.get("MM"))}×{plain(used.get("Dmm"))}']+
        [field(v,k) for k in ('S1','Lz','R1a','J1','Sf','Gcu','Gfe','Gal','CostFx','Ty')],
        [field(v,k) for k in ('fd','Dr','hc1','hc2','bt1a','Bt1mi','bt2mi','btrmu','btrml','bsm','brm')]+
        [field(p,k) for k in ('d','sk','Srr')]+[field(v,k) for k in ('J2','Jr')]]
    page.text(12,374,'STATOR',9);page.text(142,374,'ROTOR',9)
    page.text(272,374,'WINDING',9);page.text(402,374,'DIMENSIONS',9)
    for col,items in enumerate(columns):
        for row,value in enumerate(items):page.text(12+130*col,388+11.8*row,value,9.5,width=124)
    page.rule(575)
    page.text(12,589,'磁路',10)
    for x,label in ((105,'定子齿'),(210,'定子轭'),(315,'转子齿'),(420,'转子轭')):page.text(x,589,label,9)
    for x,k in ((105,'Bts'),(210,'Bys'),(315,'Btru'),(420,'Byr')):page.text(x,606,field(v,k),9.4,width=102)
    page.text(12,606,'B / G',9)
    for x,k in ((12,'Bg'),(142,'Btrl'),(272,'Im'),(402,'I1')):page.text(x,623,field(v,k),9.4,width=124)
    page.rule(634)
    headings=('Eff','Pf','Tm','Tst','Ist','AJ','Sn')
    keys=('ATA1','PF1','TM1','TST1','IST1','Aj','Sn')
    targets=('ATA','PF','TM','TST','IST',None,None)
    for i,label in enumerate(headings):page.text(103+i*62,648,label,9.5,'middle')
    page.text(12,665,'计算值',9.5);page.text(12,682,'目标值',9.5)
    for i,key in enumerate(keys):page.text(103+i*62,665,plain(v.get(key)),9.5,'middle',59)
    for i,key in enumerate(targets):page.text(103+i*62,682,plain(p.get(key)) if key else '-',9.5,'middle',59)
    page.rule(692)
    page.text(12,706,f'功率 {plain(p.get("kW"))} kW   极数 {plain(p.get("p"))}   接法 {plain(p.get("Jiefa"))}   相电压 {plain(p.get("Ufai"))} V   频率 {plain(p.get("f"))} Hz',9.5,width=516)
    page.text(12,721,f'材料 {plain(p.get("Paihao"))}   绝缘 {plain(p.get("Jueyuan"))}   n={plain(v.get("n"))} r/min',9.5,width=516)
    for i,side in enumerate(('stator','rotor')):
        item=next((r for r in snapshot.get('slots',{}).get('entries',[]) if r['side']==side),None)
        label='定子' if side=='stator' else '转子'
        value=(f'{label}槽型 {item["name"]} / {item.get("drawing") or "-"}   面积 {plain(item["area"].get("value_mm2"))} mm²') if item else f'{label}槽型：按本次输入尺寸'
        page.text(12,736+i*14,value,8.5,width=516)
    displayed={'S1','Lz','R1a','J1','Sf','Gcu','Gfe','Gal','CostFx','Ty','fd','Dr','hc1','hc2','bt1a','Bt1mi','bt2mi','btrmu','btrml','bsm','brm','J2','Jr','Bts','Bys','Btru','Byr','Bg','Btrl','Im','I1','n',*keys}
    return page,displayed


def cell_rows(items, columns=4):
    result=[];cell_width=516/columns;size=9.8
    for start in range(0,len(items),columns):
        wrapped=[wrap(item,(cell_width-9)/(.6*size)) for item in items[start:start+columns]]
        for line in range(max(map(len,wrapped))):
            result.append(('cells',[column[line] if line<len(column) else '' for column in wrapped]))
    return result


def detail_sheets(snapshot, displayed):
    p,v=snapshot['inputs'],snapshot['values']
    rows=[('heading','计算明细 · 电流、电阻、电抗与损耗')]
    keys=[k for k in OUTPUT_MAP if k in v and k not in displayed]+[k for k in v if k not in OUTPUT_MAP]
    rows+=cell_rows([field(v,k) for k in keys])
    rows += [('heading','输入参数 · 保存时的原始数值')]
    # Complete saved inputs, including custom slot elements when actually used.
    consumed=set()
    for group,label in [('rating','AA · 额定条件与目标'),('geometry','BB · 铁心尺寸'),('stator','CC · 定子槽'),('rotor','DD · 转子槽'),('winding','EE · 绕组输入'),('material','FF · 材料与损耗')]:
        keys=[k for k in GROUP_KEYS[group].split() if k in p]
        consumed.update(keys)
        rows.append(('heading',label));rows+=cell_rows([field(p,k) for k in keys])
    has_elements=any(p.get(k) not in (0,None) for k in ELEMENT_KEYS)
    remaining=[k for k in p if k not in consumed and (k not in ELEMENT_KEYS or has_elements)]
    if remaining:rows.append(('heading','GG · 槽形元素与计算选项'));rows+=cell_rows([field(p,k) for k in remaining])
    if not has_elements:rows.append(('cells',['自定义槽形元素 N/A/B/L：全部为 0 或未设置']))
    if recorded(snapshot)['mode']=='target_fill':
        rows.append(('heading','线规：EE 保留输入值，首页 JJ×Djj / MM×Dmm 使用已记录的实际线规'))
    if snapshot.get('slots',{}).get('entries'):
        rows.append(('heading','槽型记录 · 本次单槽面积 / 图纸参考面积 mm²'))
        for slot in summary.slot_rows(snapshot):
            rows+=cell_rows([f'{slot[0]} {slot[1]}',f'图号 {slot[2]}',f'{slot[3]}',f'{slot[4]} / {slot[5]}'])
    rows+=supplemental_rows(snapshot)
    return paginate(rows,'电磁计算明细',snapshot['name'])


def supplemental_rows(snapshot):
    """Retain newer saved search/verification semantics after the classic sheets."""
    rows=[]
    def paragraph(value):
        if value:rows.extend(('cells',[line]) for line in wrap(value,85))
    def table(headers,values):
        for values_row in values:
            paragraph('；'.join(f'{header}={plain(value)}' for header,value in zip(headers,values_row)))
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


def paginate(rows,title,name):
    # Balance sheets instead of leaving a small tail on an almost empty page.
    count=max(1,math.ceil(len(rows)/56));per_page=math.ceil(len(rows)/count)
    sheets=[]
    for start in range(0,len(rows),per_page):
        chunk=rows[start:start+per_page];page=Sheet(title)
        page.text(270,20,title,13,'middle');page.text(270,38,name,9.8,'middle',510);page.rule(45)
        step=700/max(1,len(chunk))
        for index,(kind,value) in enumerate(chunk):
            y=60+index*step
            if kind=='heading':
                page.rule(y-10);page.text(12,y+2,value,9.8,width=516)
            else:
                for col,cell in enumerate(value):page.text(12+129*col,y+2,cell,9.8,width=123 if len(value)>1 else 516)
        sheets.append(page)
    return sheets


def main_sheets(snapshot):
    first,displayed=classic_first(snapshot)
    pages=[first,*detail_sheets(snapshot,displayed)]
    for index,page in enumerate(pages,1):page.footer(snapshot['calculation_id'],index,len(pages))
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
         '<rect x="4" y="4" width="532" height="782" fill="white" stroke="black" stroke-width=".55" stroke-dasharray="1 3"/>']
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
    download=f'<a href="{escape(pdf,quote=True)}">下载旧版 PDF</a>' if status()['available'] else ''
    return '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'+\
        '<title>'+escape(record['name'])+' · 旧版打印</title><style>'+\
        'body{margin:0;background:#e8e8e8;color:#000;font-family:"Courier New","SimSun",monospace}.toolbar{position:sticky;top:0;z-index:2;padding:12px;text-align:center;background:#20343d;color:white;font:14px system-ui}.toolbar a,.toolbar button{display:inline-block;margin:0 5px;padding:8px 16px;border:1px solid #c8d1d5;border-radius:4px;background:white;color:#17323f;text-decoration:none;cursor:pointer;font:inherit}.toolbar small{display:block;margin-top:8px}.sheet{box-sizing:border-box;width:186mm;height:272mm;margin:18px auto;background:white}.sheet>svg{display:block;width:100%;height:100%;font-family:"Courier New","SimSun",monospace}'+\
        '@page{size:A4 portrait;margin:12mm}@media print{body{background:white}.toolbar{display:none}.sheet{margin:0 auto;break-after:page;break-inside:avoid}.sheet:last-child{break-after:auto}*{-webkit-print-color-adjust:exact;print-color-adjust:exact}}'+\
        '@media screen and (max-width:740px){.sheet{width:100%;height:auto;aspect-ratio:186/272}}'+\
        '</style></head><body><nav class="toolbar"><a href="'+escape(base,quote=True)+'">新版打印</a><button type="button" onclick="window.print()">旧版打印</button>'+download+\
        '<small>A4 纵向 · 100% 比例 · 关闭浏览器页眉页脚</small></nav><main>'+''.join('<section class="sheet">'+sheet_svg(p)+'</section>' for p in pages)+'</main></body></html>'


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
                        advance=item['size']*len(run)*(.6 if ascii_run else 1)
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
