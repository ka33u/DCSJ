"""Self-contained, printable TX calculation snapshot; no live resource lookup."""
from html import escape

METRICS=[('efficiency','效率','%'),('power_factor','功率因数',''),('maximum_torque_ratio','最大转矩','倍'),
         ('starting_torque_ratio','启动转矩','倍'),('starting_current_ratio','启动电流','倍'),('speed_rpm','转速','r/min')]


def coil_svg(coils):
    active=[c for c in coils if c['turns']>0]
    width=620;height=200
    if not active:return ''
    max_pitch=max(c['pitch'] for c in active)
    colors=['#126b7a','#4254bb','#a55f25','#884d98','#27774d','#a44455']
    paths=[]
    for index,c in enumerate(active):
        span=c['pitch']/max_pitch*340;left=(width-span)/2;top=20+index*13;bottom=170-index*8
        color=colors[index%len(colors)]
        paths.append(f'<path d="M {left} 100 V {top+18} Q {left} {top} {left+18} {top} H {left+span-18} Q {left+span} {top} {left+span} {top+18} V {bottom-18} Q {left+span} {bottom} {left+span-18} {bottom} H {left+18} Q {left} {bottom} {left} {bottom-18} Z" fill="none" stroke="{color}" stroke-width="2"/>')
        paths.append(f'<text x="8" y="{25+index*25}" fill="{color}" font-size="12">组 {index+1} · {c["turns"]:g} 匝 · y={c["pitch"]:g}</text>')
    return f'<svg role="img" aria-label="同心线圈节距示意" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">'+''.join(paths)+'</svg>'


def _number(value, digits=3):
    return '—' if value is None else f'{value:.{digits}f}'.rstrip('0').rstrip('.') if digits else str(value)


def _table(headers, rows, css=''):
    return '<table class="'+css+'"><thead><tr>'+''.join('<th>'+escape(str(v))+'</th>' for v in headers)+'</tr></thead><tbody>'+''.join(
        '<tr>'+''.join('<td>'+escape(str(v))+'</td>' for v in row)+'</tr>' for row in rows)+'</tbody></table>'


def _input_value(value):
    if value is None:return '—'
    if isinstance(value,(float,int)) and value==int(value):return str(int(value))
    return str(value)


def parameter_columns(parameters):
    """Keep every input once, grouped in two readable columns, with a coil matrix."""
    from .tx_parameters import schema
    fields=schema()['fields']
    def group(name):
        rows=[]
        for field in fields:
            if field['group']!=name or field['key']=='description':continue
            value=parameters.get(field['key'])
            text=_input_value(value)
            rows.append([field['label'],field['key'],text,'V' if field['key']=='U1' else field['unit']])
        return '<section class="parameter-group"><h3>'+name+'</h3>'+_table(['参数','符号','数值','单位'],rows,'parameter-table')+'</section>'
    winding=_table(['线圈组',*range(1,7)],[[label,*[_input_value(parameters.get(f'{key}({i})',0)) for i in range(1,7)]]
                    for label,key in [('匝数','NDXP'),('节距 / 槽','YDXP')]],'coil-input-table')
    left=group('额定参数')+group('铁心与槽型')
    right=group('导线与绕组')+'<section class="parameter-group"><h3>线圈组</h3>'+winding+'</section>'+group('计算系数')
    description=escape(str(parameters.get('description','')))
    return '<div class="parameter-columns"><div>'+left+'</div><div>'+right+'</div></div>'+(
        '<section class="description"><h3>设计说明</h3><p>'+description+'</p></section>' if description else '')


STYLE = '''
:root{font-family:"PingFang SC","Microsoft YaHei",system-ui,sans-serif;color:#263b43;font-size:12px;line-height:1.5}
*{box-sizing:border-box}body{margin:0;background:#e9eef0}a,button{font:inherit;color:#176a72}button{cursor:pointer}
.controls{position:sticky;top:0;z-index:1;background:#203d45;color:#fff;display:flex;justify-content:center;gap:12px;padding:12px;flex-wrap:wrap}.controls a,.controls button{padding:6px 12px;border:1px solid #d2e0e3;border-radius:5px;background:#fff;text-decoration:none}
.sheet{width:210mm;min-height:297mm;padding:14mm;margin:20px auto;background:#fff;box-shadow:0 2px 12px #243d4520;display:flex;flex-direction:column}
.report-header{border-bottom:2px solid #287d7a;padding-bottom:10px;margin-bottom:13px}.eyebrow{display:flex;justify-content:space-between;color:#568080;font-size:10px;letter-spacing:.8px}h1{font-size:23px;line-height:1.35;margin:7px 0;overflow-wrap:anywhere}h2{font-size:13px;margin:0 0 8px;color:#205c62}h3{font-size:11px;margin:0 0 5px;color:#376569}p{margin:5px 0}.meta{font-size:10px;color:#6d8388;overflow-wrap:anywhere}.rating{display:flex;gap:6px 16px;flex-wrap:wrap;font-size:11px}.rating b{font-weight:600}.section{margin-bottom:13px}.cards{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:7px;margin-bottom:15px}.card{padding:8px 7px;background:#f0f7f6;border-top:2px solid #3d8b81;min-width:0}.card small{display:block;color:#618080;font-size:9px}.card strong{display:block;font-size:21px;line-height:1.25;margin:4px 0;font-variant-numeric:tabular-nums}.card em{font-style:normal;font-size:9px;font-weight:400;margin-left:2px}.card .target{font-size:9px}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums;table-layout:fixed}td,th{padding:5px 6px;border-bottom:1px solid #dce6e7;text-align:right;font-size:10px;overflow-wrap:anywhere;vertical-align:top}th{background:#f1f6f6;font-weight:500;color:#567379}td:first-child,th:first-child{text-align:left}thead{display:table-header-group}tr{break-inside:avoid}
.slot-table th:nth-child(1){width:9%}.slot-table th:nth-child(2){width:36%}.slot-table th:nth-child(3){width:30%}.slot-table th:nth-child(4){width:25%}.slot-table td,.slot-table th{text-align:left}.slot-note{font-size:9px;color:#72878a;margin:5px 0 0}
.drawing-grid{display:grid;grid-template-columns:minmax(0,1.12fr) minmax(0,1fr);gap:16px;align-items:start;margin:1px 0 14px}.drawing-grid section{min-width:0}svg{display:block;width:100%;height:auto}.drawing-grid .figure{border:1px solid #e1eaea;border-radius:4px;padding:8px 3px;margin-top:12px}.drawing-grid .meta{text-align:center;font-size:9px}.loss-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:6px}.loss-grid div{padding:7px 8px;border:1px solid #e0e9e9;min-width:0}.loss-grid small{display:block;color:#6d8388;font-size:9px}.loss-grid b{font-size:12px;font-weight:550;font-variant-numeric:tabular-nums}.loss-grid span{color:#71858b;font-size:9px;margin-left:3px}
.report-footer{margin-top:auto;padding-top:12px;font-size:9px;color:#74868c;overflow-wrap:anywhere}.footer-line{display:flex;justify-content:space-between;border-top:1px solid #d8e2e4;padding-top:7px;gap:10px}.signature{display:flex;justify-content:space-around;color:#49656c;font-size:10px;padding:7px 0 14px}.parameter-columns{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:14px;align-items:start}.parameter-group{margin-bottom:10px}.parameter-group h3{padding:4px 6px;background:#eaf2f2;border-left:2px solid #4b8d88}.parameter-table td,.parameter-table th{padding:.4px 4px;font-size:10px;line-height:1.22}.parameter-table th:nth-child(1){width:39%}.parameter-table th:nth-child(2){width:19%}.parameter-table th:nth-child(3){width:28%}.parameter-table th:nth-child(4){width:14%}.parameter-table td:nth-child(2){font-family:ui-monospace,monospace;color:#69858b;font-size:8px}.coil-input-table td,.coil-input-table th{padding:4px 1px;font-size:9px}.coil-input-table th:first-child{width:24%}.description{margin-top:5px;white-space:pre-wrap;overflow-wrap:anywhere;font-size:10px}.parameter-sheet .report-header{margin-bottom:10px}.parameter-sheet h1{font-size:19px}.hash{font-family:ui-monospace,monospace;font-size:8px}
@page{size:A4 portrait;margin:14mm}
@media print{body{background:#fff}.controls{display:none}.sheet{width:auto;min-height:269mm;margin:0;padding:0;box-shadow:none;break-after:page}.sheet:last-child{break-after:auto}.cards,.drawing-grid,.loss-grid{break-inside:avoid}h2,h3{break-after:avoid}.report-footer{break-inside:avoid}*{-webkit-print-color-adjust:exact;print-color-adjust:exact}}
@media screen and (max-width:820px){.sheet{width:100%;min-height:0;padding:20px;margin:12px 0}.cards{grid-template-columns:repeat(3,1fr)}.drawing-grid,.parameter-columns{grid-template-columns:1fr}.loss-grid{grid-template-columns:repeat(2,1fr)}.report-footer{margin-top:16px}}
'''


def report(record):
    from .report_summary import slot_rows, SLOT_HEADERS, local_time
    r=record['result'];p=record['parameters'];m=r['metrics'];identity=escape(str(record['id']),quote=True)
    title=escape(record['name']);stamp=escape(local_time(record['created_at']))
    cards=[]
    references={'efficiency':'E4','power_factor':'E6','maximum_torque_ratio':'TMN',
                'starting_torque_ratio':'TQN','starting_current_ratio':'ISN'}
    for key,label,unit in METRICS:
        value=m[key]*100 if unit=='%' else m[key]
        reference=''
        if key in references:
            target=p[references[key]]*(100 if unit=='%' else 1)
            reference=f'<small class="target">参考 {_number(target)}{unit}</small>'
        cards.append(f'<div class="card"><small>{label}</small><strong>{_number(value,4 if key=="power_factor" else 2)}<em>{unit}</em></strong>{reference}</div>')
    slots=''
    if r.get('slots'):
        slots='<section class="section"><h2>槽型名称与图号</h2>'+_table(SLOT_HEADERS,slot_rows(r),'slot-table')+'<p class="slot-note">根据计算快照显示，未匹配的尺寸标为未识别。</p></section>'
    harmonic_table=_table(['谐波次数','绕组系数 KDP','相对磁势 FV / %'],[
        [h['order'],_number(h['winding_factor'],6),_number(h['relative_mmf_percent'],6)] for h in r['harmonics']])
    coil_rows=[]
    for c in r['coils']:
        index=int(c['index'])
        coil_rows.append([index,_number(p.get(f'NDXP({index})')),_number(p.get(f'YDXP({index})')),
                          *[_number(c[key],4) for key in ('span_cm','radius_cm','end_angle_rad','height_cm')]])
    coils=_table(['组','匝数','节距 / 槽','TY / cm','R / cm','端角 / rad','H / cm'],coil_rows)
    losses=[('iron_loss_w','铁损','W'),('stator_copper_loss_w','定子铜损','W'),('rotor_copper_loss_w','转子铜损','W'),
            ('total_loss_w','总损耗','W'),('current_a','定子电流','A'),('copper_mass_kg','铜重','kg'),
            ('iron_mass_kg','铁重','kg'),('mean_turn_length_mm','平均匝长','mm')]
    loss_html=''.join(f'<div><small>{label}</small><b>{_number(r["details"].get(key))}</b><span>{unit}</span></div>' for key,label,unit in losses)
    ratings=''.join(f'<span>{label} <b>{_number(p[key])}</b> {unit}</span>' for key,label,unit in
                    [('P2','功率','kW'),('P','极数',''),('U1','线电压','V'),('HZ','频率','Hz'),('Q1','定子槽数',''),('Q2','转子槽数','')])
    version=escape(str(r.get('application_version','')));resource=escape(str(r.get('resource_version','')))
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title} · TX 计算报告</title><style>{STYLE}</style></head><body>
<nav class="controls"><a href="/tx">返回 TX 工作台</a><button onclick="print()">新版打印</button><a href="/tx/reports/{identity}?layout=legacy">旧版打印</a><a href="/api/v1/tx/calculations/{identity}">JSON 数据</a></nav>
<main><article class="sheet overview-sheet"><header class="report-header"><div class="eyebrow"><span>TX / 双层同心式绕组电磁计算</span><span>结果汇总</span></div><h1>{title}</h1><div class="rating">{ratings}</div></header>
<div class="cards">{''.join(cards)}</div>{slots}
<div class="drawing-grid"><section><h2>同心线圈组</h2><div class="figure">{coil_svg(r['harmonics'])}</div><p class="meta">按节距展开，实际尺寸见下表。</p></section><section><h2>绕组系数与谐波</h2>{harmonic_table}</section></div>
<section class="section"><h2>线圈匝数、节距与尺寸</h2>{coils}</section>
<section class="section"><h2>损耗与用料</h2><div class="loss-grid">{loss_html}</div></section>
<footer class="report-footer"><div class="signature"><span>计算：________________</span><span>校核：________________</span><span>日期：________________</span></div><div class="footer-line"><span>计算时间 {stamp}</span><span>程序 {version} · 资源 {resource}</span></div><p>计算编号 {identity}</p></footer></article>
<article class="sheet parameter-sheet"><header class="report-header"><div class="eyebrow"><span>TX / 双层同心式绕组电磁计算</span><span>输入参数快照</span></div><h1>{title}</h1><p class="meta">尺寸采用 cm，线径采用 mm。各项单位随参数列示。</p></header>{parameter_columns(p)}
<footer class="report-footer"><div class="footer-line"><span>计算时间 {stamp}</span><span>计算编号 {identity}</span></div><p class="hash">输入 SHA-256 {escape(str(r.get('input_sha256','')))}</p></footer></article></main></body></html>'''
