"""Print contracts use invented snapshots, never private catalog data."""
import copy
from io import BytesIO
import json

from pypdf import PdfReader

from dcsj import calculations, legacy_report, storage


def text_of(pdf):
    reader=PdfReader(BytesIO(pdf))
    return reader,'\n'.join(page.extract_text() for page in reader.pages)


def test_old_and_new_routes_keep_saved_values_and_validate_layout(client,snapshot,monkeypatch):
    snapshot['values'].update(TM1=2.73,IST1=6.8,Sf=.7)
    snapshot['inputs'].update(TM=2.3,IST=7,SizeX='测试 <script>型号</script>')
    saved=calculations.save(snapshot,'自定义 <img src=x> 名称')
    snapshot['values']['TM1']=9.99
    monkeypatch.setattr('dcsj.engine.calculate_design',lambda *a,**k: (_ for _ in ()).throw(AssertionError('Printing must not calculate')))
    base='/reports/'+saved['calculation_id']
    modern=client.get(base);old=client.get(base+'?layout=legacy')
    assert modern.status_code==old.status_code==200
    assert '完整计算输出' in modern.text and '旧版打印' in modern.text
    assert '新版打印' in old.text and '旧版打印' in old.text
    assert '<script>' not in old.text and '<img src=x>' not in old.text
    assert '&lt;script&gt;' in old.text and '&lt;img' in old.text
    assert '2.73' in old.text and '9.99' not in old.text
    assert old.headers['cache-control']=='no-store'
    url=f'/api/v1/calculations/{saved["calculation_id"]}/report.pdf'
    pdf=client.get(url+'?layout=legacy');reader,text=text_of(pdf.content)
    assert pdf.status_code==200 and '-legacy.pdf' in pdf.headers['content-disposition']
    assert '2.73' in text and '目标值' in text and '2.3' in text
    assert len(reader.pages)==2
    assert client.get(base+'?layout=wrong').status_code==422
    assert client.get(url+'?layout=wrong').status_code==422
    assert client.get('/reports/missing?layout=legacy').status_code==404
    assert client.get('/api/v1/calculations/missing/report.pdf?layout=legacy').status_code==404
    assert calculations.get(saved['calculation_id'])['values']['TM1']==2.73


def test_effective_wire_is_used_and_old_unknown_selection_is_not_invented(snapshot):
    saved={**copy.deepcopy(snapshot),'name':'人工线规','calculation_id':'wire','created_at':'2026-01-01'}
    saved['inputs'].update(_wire_target=.73,JJ=9,Djj=9.9)
    saved['wire_selection']={'mode':'target_fill','used':{'JJ':2,'Djj':.8,'MM':0,'Dmm':0}}
    first=legacy_report.main_sheets(saved)[0]
    values=[i.get('value') for i in first.items if i['kind']=='text']
    assert 'JJ×Djj=2×0.8' in values and 'JJ×Djj=9×9.9' not in values
    del saved['wire_selection']
    values=[i.get('value') for i in legacy_report.main_sheets(saved)[0].items if i['kind']=='text']
    assert 'JJ×Djj=-×-' in values


def test_long_inputs_zero_and_custom_elements_survive_balanced_pagination(snapshot):
    saved={**copy.deepcopy(snapshot),'name':'人工分页测试','calculation_id':'long','created_at':'2026-01-01'}
    saved['inputs'].update(SizeX='q'*300,Paihao='麟'*160,N1=12,A1=1.23456,L1=0)
    saved['values'].update(TM1=0,CostFx=0,custom=123.456)
    before=copy.deepcopy(saved);pages=legacy_report.main_sheets(saved)
    assert saved==before and 2<=len(pages)<=4
    texts=''.join(i['value'] for p in pages[1:] for i in p.items if i['kind']=='text')
    assert 'N1=12' in texts and 'A1=1.23456' in texts and 'L1=0' in texts and 'custom=123.456' in texts
    assert texts.count('q')==300 and texts.count('麟')==160
    for page in pages:
        for item in page.items:
            if item['kind']=='text':
                left=item['x']-{'start':0,'middle':.5,'end':1}[item['anchor']]*item['width']
                assert left>=4 and left+item['width']<=536.001
                assert 4<=item['y']<=782


def test_tx_native_report_keeps_every_line_and_centers_each_sheet(client,monkeypatch):
    native=' 双层同心绕组\n'+'\n'.join(f' Q1={i:03d}    Pf=0.8000    Eff=0.9000' for i in range(90))+'\n <script>text</script>'
    record={'id':'tx-print','name':'人工 TX 打印','parameters':{},'result':{'report_text':native},'created_at':'2026-01-01'}
    with storage.connect() as db:
        db.execute('INSERT INTO tx_calculations VALUES(?,?,?,?,?)',('tx-print',record['name'],'{}',json.dumps(record['result']),record['created_at']))
    monkeypatch.setattr('dcsj.tx.calculate',lambda *a,**k: (_ for _ in ()).throw(AssertionError('No TX rerun')))
    old=client.get('/tx/reports/tx-print?layout=legacy')
    assert old.status_code==200 and '&lt;script&gt;text&lt;/script&gt;' in old.text
    pdf=client.get('/api/v1/tx/calculations/tx-print/report.pdf?layout=legacy')
    reader,text=text_of(pdf.content)
    assert pdf.status_code==200 and len(reader.pages)==2
    assert all(f'Q1={i:03d}' in text for i in range(90))
    assert 'Q1=000' in text and 'Q1=089' in text
    for page in legacy_report.tx_sheets(record):
        rows=[i for i in page.items if i['kind']=='text' and 40<i['y']<763]
        left=min(i['x'] for i in rows);right=max(i['x']+i['width'] for i in rows)
        assert abs((left+right)/2-legacy_report.WIDTH/2)<.01
        assert min(i['size'] for i in rows)>=9
    assert client.get('/tx/reports/tx-print?layout=wrong').status_code==422
    assert client.get('/api/v1/tx/calculations/tx-print/report.pdf?layout=modern').status_code==422


def test_old_pdf_font_failure_preserves_correct_html_fallback(client,snapshot,monkeypatch,tmp_path):
    saved=calculations.save(snapshot)
    monkeypatch.setenv('DCSJ_PDF_FONT',str(tmp_path/'missing.ttf'))
    response=client.get(f'/api/v1/calculations/{saved["calculation_id"]}/report.pdf?layout=legacy')
    assert response.status_code==503
    assert response.json()['html_report_url']==f'/reports/{saved["calculation_id"]}?layout=legacy'
    assert client.get(response.json()['html_report_url']).status_code==200


def test_classic_layout_retains_saved_search_supplements_without_new_blank_page(snapshot):
    saved={**copy.deepcopy(snapshot),'name':'人工附加信息','calculation_id':'saved-extra','created_at':'2026-01-01'}
    saved['optimization']={'manufacturing':{'numeric_sets':[{'key':'Djj','allowed':[.71,.8,1.06],'actual':.8,'passed':True}],
        'rotor_gap':{'reference_neck_bottom_mm':177.8,'rotor_outer_mm':178.6,'gap_mm':.7,
                    'hr0_mm':.4,'neck_bottom_mm':177.8,'formula':'hr0 = hr0基准 + g基准 - g','passed':True}}}
    pages=legacy_report.main_sheets(saved)
    assert len(pages)==2
    _,text=text_of(legacy_report.render_pdf(saved))
    assert '寻优规格与尺寸联动' in text and '177.8' in text and '0.71 / 0.8 / 1.06' in text
    assert 'hr0=0.4 mm' in text
