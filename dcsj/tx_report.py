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


def report(record):
    r=record['result'];p=record['parameters'];m=r['metrics']
    cards=[]
    references={'efficiency':'E4','power_factor':'E6','maximum_torque_ratio':'TMN',
                'starting_torque_ratio':'TQN','starting_current_ratio':'ISN'}
    for key,label,unit in METRICS:
        digits=4 if key=='power_factor' else 2
        value=m[key]*100 if unit=='%' else m[key]
        reference=''
        if key in references:
            target=p[references[key]]*(100 if unit=='%' else 1)
            reference=f'<small>参考 {target:g}{unit}</small>'
        cards.append(f'<div><small>{label}</small><strong>{value:.{digits}f}<em>{unit}</em></strong>{reference}</div>')
    cards=''.join(cards)
    harmonics=''.join(f'<tr><td>{h["order"]:g}</td><td>{h["winding_factor"]:.6f}</td><td>{h["relative_mmf_percent"]:.6f}</td><td>{h["turns"]:g}</td><td>{h["pitch"]:g}</td></tr>' for h in r['harmonics'])
    coils=''.join(f'<tr><td>{c["index"]:g}</td><td>{c["span_cm"]:g}</td><td>{c["radius_cm"]:g}</td><td>{c["end_angle_rad"]:g}</td><td>{c["height_cm"]:g}</td></tr>' for c in r['coils'])
    from .tx_parameters import schema
    inputs=''.join(f'<tr><td>{escape(f["label"])} <small>{escape(f["key"])}</small></td><td>{escape(str(p[f["key"]]))}</td><td>{escape(f["unit"])}</td></tr>' for f in schema()['fields'])
    return f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>{escape(record['name'])} · TX 计算报告</title>
<style>body{{font:14px system-ui,"PingFang SC",sans-serif;color:#20333a;max-width:1040px;margin:28px auto;padding:0 22px}}h1{{font-size:25px;margin:8px 0}}h2{{font-size:17px;margin-top:24px}}.meta,small{{color:#60757b}}.cards{{display:grid;grid-template-columns:repeat(6,1fr);gap:10px;margin:22px 0}}.cards>div{{border-top:3px solid #126b7a;background:#f0f6f6;padding:12px}}strong{{display:block;font-size:23px;margin-top:8px}}em{{font-size:12px;font-style:normal;margin-left:3px}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:22px}}table{{border-collapse:collapse;width:100%;font-size:12px}}td,th{{padding:6px;border-bottom:1px solid #d6e1e3;text-align:right}}td:first-child,th:first-child{{text-align:left}}svg{{width:100%}}pre{{font:12px/1.5 ui-monospace,monospace;white-space:pre-wrap;background:#f7f9fa;padding:16px}}a,button{{color:#126b7a}}footer{{font-size:11px;color:#60757b;margin-top:24px;overflow-wrap:anywhere}}@page{{size:A4;margin:12mm}}@media print{{body{{font-size:10px;margin:0;padding:0}}.controls{{display:none}}h1{{font-size:20px}}.cards{{gap:5px;margin:12px 0}}.cards>div{{padding:7px}}strong{{font-size:18px}}h2{{margin-top:14px;font-size:14px}}td,th{{padding:4px;font-size:10px}}pre{{font-size:9px;line-height:1.35;background:none;padding:0}}.appendix{{break-before:page}}.cards,.grid{{break-inside:avoid}}}}</style>
<div class="controls"><a href="/tx">返回 TX 工作台</a> · <button onclick="print()">打印 / 保存 PDF</button> · <a href="/api/v1/tx/calculations/{escape(record['id'])}">JSON 数据</a></div>
<p class="meta">TX / 双层同心式绕组电磁设计</p><h1>{escape(record['name'])}</h1>
<p>{p['P2']:g} kW · {p['P']:g} 极 · {p['U1']:g} V 线电压 · {p['HZ']:g} Hz · 定/转子槽数 {p['Q1']:g}/{p['Q2']:g}</p>
<p class="meta">{escape(p['description'])}</p><div class="cards">{cards}</div>
<div class="grid"><section><h2>线圈组节距示意</h2>{coil_svg(r['harmonics'])}<p class="meta">示意按节距展开；线圈尺寸见右表。</p></section><section><h2>线圈尺寸</h2><table><tr><th>组</th><th>TY / cm</th><th>R / cm</th><th>端角 / rad</th><th>H / cm</th></tr>{coils}</table></section></div>
<h2>绕组系数与谐波</h2><table><tr><th>谐波次数</th><th>绕组系数 KDP</th><th>相对磁势 FV / %</th><th>组匝数</th><th>组节距</th></tr>{harmonics}</table>
<p>总损耗 {r['details']['total_loss_w']:g} W · 定子电流 {r['details']['current_a']:g} A · 铜重 {r['details']['copper_mass_kg']:g} kg · 铁重 {r['details']['iron_mass_kg']:g} kg</p>
<footer>计算时间 {escape(record['created_at'])} · 程序 {escape(r['application_version'])} · 资源 {escape(r['resource_version'])}<br>计算编号 {escape(record['id'])} · 输入 SHA-256 {escape(r['input_sha256'])}</footer>
<section class="appendix"><h2>完整计算明细</h2><pre>{escape(r['report_text'])}</pre></section>
<section class="appendix"><h2>输入参数快照</h2><table><tr><th>参数</th><th>数值</th><th>单位</th></tr>{inputs}</table></section></html>'''
