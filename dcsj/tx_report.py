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
    return '—' if value is None else f'{value:.{digits}f}'.rstrip('0').rstrip('.') if digits else f'{value:.0f}'


def _table(headers, rows, css=''):
    return '<table class="'+css+'"><thead><tr>'+''.join('<th>'+escape(str(v))+'</th>' for v in headers)+'</tr></thead><tbody>'+''.join(
        '<tr>'+''.join('<td>'+escape(str(v))+'</td>' for v in row)+'</tr>' for row in rows)+'</tbody></table>'


def _input_value(value):
    if value is None:return '—'
    if isinstance(value,(float,int)) and value==int(value):return str(int(value))
    return str(value)


def parameter_columns(parameters):
    """Keep every input once in three balanced groups, with a coil matrix."""
    from .tx_parameters import schema
    fields=schema()['fields']
    corrections={'FE','FXM','WDF'}
    def group(name, continuation=False):
        rows=[];model=''
        for field in fields:
            if field['group']!=name or field['key']=='description':continue
            if name=='计算系数' and (field['key'] in corrections)!=continuation:continue
            value=parameters.get(field['key'])
            text=_input_value(value)
            if field['key']=='TYP':
                model='<tr><td colspan="4" class="model-input"><span>型号 / TYP</span> '+escape(text)+'</td></tr>'
                continue
            rows.append([field['label'],field['key'],text,'V' if field['key']=='U1' else field['unit']])
        heading='修正系数（续）' if continuation else name
        table=_table(['参数','符号','数值','单位'],rows,'parameter-table').replace('<tbody>','<tbody>'+model,1)
        return '<section class="parameter-group"><h3>'+heading+'</h3>'+table+'</section>'
    winding=_table(['线圈组',*range(1,7)],[[label,*[_input_value(parameters.get(f'{key}({i})',0)) for i in range(1,7)]]
                    for label,key in [('匝数','NDXP'),('节距 / 槽','YDXP')]],'coil-input-table')
    left=group('额定参数')+group('导线与绕组')+'<section class="parameter-group"><h3>线圈组</h3>'+winding+'</section>'+group('计算系数',True)
    middle=group('铁心与槽型')
    right=group('计算系数')
    description=escape(str(parameters.get('description','')))
    return '<div class="parameter-columns"><div>'+left+'</div><div>'+middle+'</div><div>'+right+'</div></div>'+(
        '<section class="description"><h3>设计说明</h3><p>'+description+'</p></section>' if description else '')


def _pairs(items, columns=2, digits=3):
    rows=[]
    for start in range(0,len(items),columns):
        row=[]
        for label,value,unit in items[start:start+columns]:
            row.extend([label,_number(value,digits)+((' '+unit) if value is not None and unit else '')])
        rows.append(row+['']*(2*columns-len(row)))
    return _table(['项目','数值']*columns,rows,'pair-table')


def electromagnetic(result):
    from .tx_print_data import slot_fills
    details=result['details'];metrics=result['metrics']
    flux=[('定子齿 Bst','stator_tooth_flux_g'),('定子轭 Bsc','stator_yoke_flux_g'),('气隙 Bg','airgap_flux_g'),
          ('转子齿 Brt1','rotor_tooth1_flux_g'),('转子齿 Brt2','rotor_tooth2_flux_g'),('转子轭 Brc','rotor_yoke_flux_g')]
    flux_html=_table([label+' / G' for label,_ in flux],[[_number(details.get(key),0) for _,key in flux]],'flux-table')
    loads=[('热负荷 AJ',metrics.get('aj'),''),('电流密度 J',details.get('current_density_a_mm2'),'A/mm²'),
           ('定子电流 i1',details.get('current_a'),'A'),('励磁电流 im',details.get('magnetizing_current_a'),'A'),
           ('相电阻 R1',details.get('resistance_ohm'),'Ω'),('转差率 Sn',metrics.get('slip'),'')]
    fills=slot_fills(result)
    fill_html=''.join('<span><b>'+item['label']+'</b> '+_number(item['value']*100,2)+'%</span>' for item in fills) or '<span>SF —</span>'
    return '<section class="section electromagnetic"><h2>电磁负荷与槽满率</h2>'+_pairs(loads,3,4)+flux_html+'<div class="fill-row"><strong>槽满率</strong>'+fill_html+'</div></section>'


def slot_dimensions(parameters,result):
    from .tx_print_data import dimensions
    p=dimensions(parameters)
    core=[('定子外径 D1','D1'),('定子内径 Di1','D3'),('转子外径 D2','D2'),('轴孔 D2i','D4'),('铁心长度 L','L'),('单边气隙 g','G')]
    core_html=_pairs([(label,p.get(key),'mm') for label,key in core],3)
    stator=[('槽口宽 b01','b01','mm'),('槽口高 Hs0','Hs0','mm'),('上槽宽 bs1','bs1','mm'),('下槽宽 bs2','bs2','mm'),
            ('槽身高 hs12','hs12','mm'),('槽角 Zs1','DXJJ','°'),('槽楔厚 Hw','H','mm'),('绝缘纸 Si','CI','mm')]
    rotor=[('槽口宽 b02','b02','mm'),('槽口高 hr0','hr0','mm'),('上槽宽 br1','br1','mm'),('上槽底宽 br2','br2','mm'),
           ('下槽宽 br3','br3','mm'),('下槽底宽 br4','br4','mm'),('上槽高 hr12','hr12','mm'),('下槽高 hr3','hr3','mm'),('槽角 Zs2','ZXJJ','°')]
    stator_values=[(label,p.get(key),unit) for label,key,unit in stator]+[('单槽面积 Se',result['details'].get('stator_slot_area_mm2'),'mm²'),('叠压系数',p.get('DYXS'),'')]
    rotor_values=[(label,p.get(key),unit) for label,key,unit in rotor]+[('槽型代码 M2',p.get('M2'),'')]
    return '<section class="section slot-dimensions"><h2>铁心与槽型基本尺寸</h2>'+core_html+'<div class="dimension-columns"><div><h3>定子槽 · Q1 = '+_number(p.get('Q1'))+'</h3>'+_pairs(stator_values)+'</div><div><h3>转子槽 · Q2 = '+_number(p.get('Q2'))+'</h3>'+_pairs(rotor_values)+'</div></div></section>'


STYLE = '''
:root{font-family:"PingFang SC","Microsoft YaHei",system-ui,sans-serif;color:#263b43;font-size:12px;line-height:1.5}
*{box-sizing:border-box}body{margin:0;background:#e9eef0}a,button{font:inherit;color:#176a72}button{cursor:pointer}
.controls{position:sticky;top:0;z-index:1;background:#203d45;color:#fff;display:flex;justify-content:center;gap:12px;padding:12px;flex-wrap:wrap}.controls a,.controls button{padding:6px 12px;border:1px solid #d2e0e3;border-radius:5px;background:#fff;text-decoration:none}
.sheet{width:210mm;min-height:297mm;padding:12mm;margin:20px auto;background:#fff;box-shadow:0 2px 12px #243d4520;display:flex;flex-direction:column}
.report-header{border-bottom:2px solid #287d7a;padding-bottom:10px;margin-bottom:13px}.eyebrow{display:flex;justify-content:space-between;color:#568080;font-size:10px;letter-spacing:.8px}h1{font-size:23px;line-height:1.35;margin:7px 0;overflow-wrap:anywhere}h2{font-size:13px;margin:0 0 8px;color:#205c62}h3{font-size:11px;margin:0 0 5px;color:#376569}p{margin:5px 0}.meta{font-size:10px;color:#6d8388;overflow-wrap:anywhere}.rating{display:flex;gap:6px 16px;flex-wrap:wrap;font-size:11px}.rating b{font-weight:600}.section{margin-bottom:13px}.cards{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:7px;margin-bottom:15px}.card{padding:8px 7px;background:#f0f7f6;border-top:2px solid #3d8b81;min-width:0}.card small{display:block;color:#618080;font-size:9px}.card strong{display:block;font-size:21px;line-height:1.25;margin:4px 0;font-variant-numeric:tabular-nums}.card em{font-style:normal;font-size:9px;font-weight:400;margin-left:2px}.card .target{font-size:9px}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums;table-layout:fixed}td,th{padding:5px 6px;border-bottom:1px solid #dce6e7;text-align:right;font-size:10px;overflow-wrap:anywhere;vertical-align:top}th{background:#f1f6f6;font-weight:500;color:#567379}td:first-child,th:first-child{text-align:left}thead{display:table-header-group}tr{break-inside:avoid}
.slot-table th:nth-child(1){width:9%}.slot-table th:nth-child(2){width:36%}.slot-table th:nth-child(3){width:30%}.slot-table th:nth-child(4){width:25%}.slot-table td,.slot-table th{text-align:left}.slot-note{font-size:9px;color:#72878a;margin:5px 0 0}
.drawing-grid{display:grid;grid-template-columns:minmax(0,1.12fr) minmax(0,1fr);gap:16px;align-items:start;margin:1px 0 14px}.drawing-grid section{min-width:0}svg{display:block;width:100%;height:auto}.drawing-grid .figure{border:1px solid #e1eaea;border-radius:4px;padding:8px 3px;margin-top:12px}.drawing-grid .meta{text-align:center;font-size:9px}.loss-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:6px}.loss-grid div{padding:7px 8px;border:1px solid #e0e9e9;min-width:0}.loss-grid small{display:block;color:#6d8388;font-size:9px}.loss-grid b{font-size:12px;font-weight:550;font-variant-numeric:tabular-nums}.loss-grid span{color:#71858b;font-size:9px;margin-left:3px}
.report-footer{margin-top:auto;padding-top:12px;font-size:9px;color:#74868c;overflow-wrap:anywhere}.footer-line{display:flex;justify-content:space-between;border-top:1px solid #d8e2e4;padding-top:7px;gap:10px}.signature{display:flex;justify-content:space-around;color:#49656c;font-size:10px;padding:7px 0 14px}.parameter-columns{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:14px;align-items:start}.parameter-group{margin-bottom:10px}.parameter-group h3{padding:4px 6px;background:#eaf2f2;border-left:2px solid #4b8d88}.parameter-table td,.parameter-table th{padding:.4px 4px;font-size:10px;line-height:1.22}.parameter-table th:nth-child(1){width:39%}.parameter-table th:nth-child(2){width:19%}.parameter-table th:nth-child(3){width:28%}.parameter-table th:nth-child(4){width:14%}.parameter-table td:nth-child(2){font-family:ui-monospace,monospace;color:#69858b;font-size:8px}.coil-input-table{break-inside:avoid}.coil-input-table td,.coil-input-table th{padding:4px 1px;font-size:9px}.coil-input-table th:first-child{width:24%}.description{margin-top:5px;white-space:pre-wrap;overflow-wrap:anywhere;font-size:10px}.parameter-sheet .report-header{margin-bottom:10px}.parameter-sheet h1{font-size:19px}.hash{font-family:ui-monospace,monospace;font-size:8px}
@media print{body{background:#fff}.controls{display:none}.sheet{width:auto;min-height:272.5mm;margin:0;padding:0;box-shadow:none;break-after:page}.sheet:last-child{break-after:auto}.cards,.drawing-grid,.loss-grid{break-inside:avoid}h2,h3{break-after:avoid}.report-footer{break-inside:avoid}*{-webkit-print-color-adjust:exact;print-color-adjust:exact}}
@media screen and (max-width:820px){.sheet{width:100%;min-height:0;padding:20px;margin:12px 0}.cards{grid-template-columns:repeat(3,1fr)}.drawing-grid,.parameter-columns{grid-template-columns:1fr}.loss-grid{grid-template-columns:repeat(2,1fr)}.report-footer{margin-top:16px}}
.parameter-columns{grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}.parameter-table th:nth-child(1){width:36%}.parameter-table th:nth-child(2){width:22%}.parameter-table th:nth-child(3){width:28%}.parameter-table th:nth-child(4){width:14%}
/* The paper owns the margin exactly once. Keep a rounding reserve in print. */
.sheet{padding:12mm;min-height:297mm}.report-header{padding-bottom:6px;margin-bottom:9px}.report-header h1{font-size:21px;margin:5px 0}.rating{font-size:10px;gap:3px 13px}.material-line{font-size:11px;margin-top:5px;color:#375f65}.material-line b{color:#243d43}.section{margin-bottom:9px}.cards{gap:6px;margin-bottom:10px}.card{padding:6px}.card strong{font-size:19px;margin:3px 0}.card small,.card .target{font-size:9px}h2{font-size:12px;margin-bottom:5px}td,th{font-size:10px;padding:3px 4px;line-height:1.35}.slot-table th:nth-child(2){width:35%}.slot-table th:nth-child(3){width:31%}.slot-note{display:none}
.pair-table thead{display:none}.pair-table td:nth-child(odd){text-align:left;color:#5c767d;font-size:9px;width:17%}.pair-table td:nth-child(even){text-align:right;color:#253f46;font-size:10px;font-weight:550}.pair-table td{padding:3px 4px}.flux-table{margin-top:5px}.flux-table td,.flux-table th{text-align:center}.flux-table td{font-weight:550}.flux-table th{font-size:9px}.fill-row{display:flex;align-items:center;gap:10px 25px;flex-wrap:wrap;background:#f0f6f5;padding:5px 7px;margin-top:5px;font-size:10px}.fill-row strong{font-size:10px;color:#3d6868}.fill-row b{font-size:9px;font-weight:500;color:#658084;margin-right:5px}.dimension-columns{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:7px}.dimension-columns h3{font-size:10px;margin:0 0 3px}.dimension-columns .pair-table td:nth-child(odd){width:25%;font-size:9px}.dimension-columns .pair-table td{padding:2px 3px}.drawing-grid{gap:12px;margin:0 0 8px;grid-template-columns:minmax(0,1fr) minmax(0,1.05fr)}.drawing-grid .figure{margin-top:4px;padding:3px}.drawing-grid .figure svg{max-height:31mm}.drawing-grid .meta{margin:2px 0;font-size:8px}.drawing-grid td,.drawing-grid th{font-size:9px;padding:2px 4px}.loss-grid{grid-template-columns:repeat(6,minmax(0,1fr));gap:5px}.loss-grid div{padding:4px 5px}.loss-grid b{font-size:11px}.loss-grid small,.loss-grid span{font-size:8px}.report-footer{padding-top:8px}.signature{padding:5px 0 9px}.footer-line{padding-top:5px}.parameter-group{margin-bottom:8px}.parameter-table td,.parameter-table th{padding:.35px 4px;font-size:10px;line-height:1.25}.parameter-sheet .report-header{margin-bottom:8px}.parameter-sheet h1{font-size:18px}.model-input{text-align:left!important;line-height:1.4!important;padding:4px!important}.model-input span{color:#69858b;margin-right:5px;font-size:9px}.print-guide{align-self:center;font-size:11px;color:#d8e8e9}
.dense-overview h1{font-size:16px;line-height:1.25}.dense-overview .slot-table td{font-size:9px;line-height:1.25}.dense-overview .section{margin-bottom:5px}.dense-overview .card{padding:4px}.dense-overview .loss-grid div{padding:3px}.dense-overview .pair-table td{padding-top:1px;padding-bottom:1px}.dense-overview .drawing-grid td,.dense-overview .drawing-grid th{padding-top:1px;padding-bottom:1px}.dense-overview .drawing-grid .figure svg{max-height:26mm}.dense-overview .report-footer{padding-top:4px}
@page{size:A4 portrait;margin:12mm;@bottom-center{content:counter(page);font:9px system-ui;color:#6f8085}}
@media print{.sheet{padding:0;min-height:272.5mm}.controls{display:none}.section,.dimension-columns{break-inside:avoid}.sheet{display:block;position:relative}.overview-sheet{display:flex}.overview-sheet>*{flex-shrink:0}.parameter-sheet .report-footer{margin-top:12px}.report-header{break-inside:avoid}}
@media screen and (max-width:820px){.sheet{padding:20px;min-height:0}.dimension-columns{grid-template-columns:1fr}.loss-grid{grid-template-columns:repeat(3,1fr)}.cards{grid-template-columns:repeat(3,1fr)}.drawing-grid,.parameter-columns{grid-template-columns:1fr}}

'''


def report(record):
    from .report_summary import slot_rows, SLOT_HEADERS, local_time
    from .tx_print_data import material
    r=record['result'];p=record['parameters'];m=r['metrics'];identity=escape(str(record['id']),quote=True)
    title=escape(record['name']);stamp=escape(local_time(record['created_at']))
    entries=r.get('slots',{}).get('entries',[])
    dense=len(record['name'])>48 or any(len(str(e.get('name','')))+len(str(e.get('drawing','')))>100 for e in entries)
    overview_class='sheet overview-sheet'+(' dense-overview' if dense else '')
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
        [_number(h['order'],0),_number(h['winding_factor'],6),_number(h['relative_mmf_percent'],6)] for h in r['harmonics']])
    coil_rows=[]
    for c in r['coils']:
        index=int(c['index'])
        coil_rows.append([index,_number(p.get(f'NDXP({index})')),_number(p.get(f'YDXP({index})')),
                          *[_number(c[key],4) for key in ('span_cm','radius_cm','end_angle_rad','height_cm')]])
    coils=_table(['组','匝数','节距 / 槽','TY / cm','R / cm','端角 / rad','H / cm'],coil_rows)
    losses=[('iron_loss_w','铁损','W'),('stator_copper_loss_w','定子铜损','W'),('rotor_copper_loss_w','转子铜损','W'),
            ('mechanical_loss_w','机械损耗','W'),('stray_loss_w','杂散损耗','W'),('total_loss_w','总损耗','W'),
            ('copper_mass_kg','铜重','kg'),('iron_mass_kg','铁重','kg'),('rotor_conductor_mass_kg','转子导体重','kg'),
            ('mean_turn_length_mm','平均匝长','mm'),('slot_conductors','每槽导体数',''),('phase_conductors','每相导体数','')]
    loss_html=''.join(f'<div><small>{label}</small><b>{_number(r["details"].get(key))}</b><span>{unit}</span></div>' for key,label,unit in losses)
    ratings=''.join(f'<span>{label} <b>{_number(p[key])}</b> {unit}</span>' for key,label,unit in
                    [('P2','功率','kW'),('P','极数',''),('U1','线电压','V'),('HZ','频率','Hz'),('Q1','定子槽数',''),('Q2','转子槽数','')])
    version=escape(str(r.get('application_version','')));resource=escape(str(r.get('resource_version','')))
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title} · TX 计算报告</title><style>{STYLE}</style></head><body>
<nav class="controls"><a href="/tx">返回 TX 工作台</a><button onclick="print()">新版打印</button><a href="/tx/reports/{identity}?layout=legacy">旧版打印</a><a href="/api/v1/tx/calculations/{identity}">JSON 数据</a><span class="print-guide">A4 纵向 · 比例 100% · 关闭浏览器页眉页脚</span></nav>
<main><article class="{overview_class}"><header class="report-header"><div class="eyebrow"><span>TX / 双层同心式绕组电磁计算</span><span>结果汇总</span></div><h1>{title}</h1><div class="rating">{ratings}</div><div class="material-line">硅钢片牌号 <b>{escape(material(p))}</b>　计算温度 {_number(p.get("WENDU"))} °C　并联路数 {_number(p.get("A1"))}</div></header>
<div class="cards">{''.join(cards)}</div>{electromagnetic(r)}{slots}{slot_dimensions(p,r)}
<div class="drawing-grid"><section><h2>同心线圈组</h2><div class="figure">{coil_svg(r['harmonics'])}</div><p class="meta">按节距展开，实际线圈尺寸见次页。</p></section><section><h2>绕组系数与谐波</h2>{harmonic_table}</section></div>
<section class="section"><h2>损耗与用料</h2><div class="loss-grid">{loss_html}</div></section>
<footer class="report-footer"><div class="signature"><span>计算：________________</span><span>校核：________________</span><span>日期：________________</span></div><div class="footer-line"><span>计算时间 {stamp}</span><span>程序 {version} · 资源 {resource}</span></div><p>计算编号 {identity}</p></footer></article>
<article class="sheet parameter-sheet"><header class="report-header"><div class="eyebrow"><span>TX / 双层同心式绕组电磁计算</span><span>线圈尺寸与输入参数快照</span></div><h1>{title}</h1><p class="meta">尺寸采用 cm，线径采用 mm。各项单位随参数列示。</p></header><section class="section coil-sizes"><h2>线圈匝数、节距与尺寸</h2>{coils}</section>{parameter_columns(p)}
<footer class="report-footer"><div class="footer-line"><span>计算时间 {stamp}</span><span>计算编号 {identity}</span></div><p class="hash">输入 SHA-256 {escape(str(r.get('input_sha256','')))}</p></footer></article></main></body></html>'''
