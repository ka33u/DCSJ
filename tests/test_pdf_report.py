from io import BytesIO
import copy
import re
from pathlib import Path

import pytest
from pypdf import PdfReader

from dcsj import calculations, pdf_fonts, pdf_report, reports
from dcsj.storage import now


def saved_snapshot(snapshot):
    return {**copy.deepcopy(snapshot), 'calculation_id': '00000000-0000-0000-0000-000000000001',
            'name': '中文设计报告验证', 'created_at': now(), 'project_revision': None}


def pdf_text(data):
    reader = PdfReader(BytesIO(data))
    return reader, '\n'.join(page.extract_text() for page in reader.pages)


def test_manufacturing_report_reads_saved_specs_and_linkage(snapshot):
    saved=saved_snapshot(snapshot)
    assert '寻优规格与尺寸联动' not in reports.render(saved)
    saved['optimization']={'manufacturing':{'numeric_sets':[{'key':'Djj','allowed':[.71,.8,1.06],
        'actual':.8,'passed':True}], 'rotor_gap':{'reference_neck_bottom_mm':177.8,'rotor_outer_mm':178.6,
        'gap_mm':.7,'hr0_mm':.4,'neck_bottom_mm':177.8,'formula':'hr0 = hr0基准 + g基准 − g','passed':True}}}
    # Recorded values remain authoritative even when different from generic fixture inputs.
    _,text=pdf_text(pdf_report.render(saved))
    assert '寻优规格与尺寸联动' in text and '0.71 / 0.8 / 1.06' in text
    assert 'hr0=0.4 mm' in text and '177.8 mm' in text and '符合' in text
    assert '寻优规格与尺寸联动' in reports.render(saved)


def test_material_price_report_uses_frozen_quotes_and_preserves_old_reports(snapshot):
    saved=saved_snapshot(snapshot)
    assert '寻优材料计价' not in reports.render(saved)
    saved['optimization']={'material_pricing':{'method':'per-material-cfe-v1','material':'B <试制>',
        'cfe':2.5,'prices':{'A':4.,'B <试制>':2.5},'basis':'每 kg；同一币种'}}
    _,text=pdf_text(pdf_report.render(saved))
    assert '寻优材料计价' in text and 'Cfe=2.5 /kg' in text and 'CostFx=100' in text
    html=reports.render(saved)
    assert 'B &lt;试制&gt;' in html and 'B <试制>' not in html and '按牌号分别定价' in html


def test_pdf_download_snapshot_fidelity_chinese_and_embedded_font(client, snapshot):
    assert pdf_fonts.font_path().is_file(), 'Run python scripts/install_pdf_font.py before PDF tests'
    snapshot['inputs']['SizeX'] = '中文设计 <script>原样文字</script>'
    saved = calculations.save(snapshot, '中文工程报告')
    snapshot['values']['ATA1'] = .5
    snapshot['inputs']['kW'] = 99
    response = client.get(f'/api/v1/calculations/{saved["calculation_id"]}/report.pdf')
    assert response.status_code == 200
    assert response.headers['content-type'] == 'application/pdf'
    assert response.headers['cache-control'] == 'no-store'
    assert 'attachment' in response.headers['content-disposition']
    assert response.content.startswith(b'%PDF-')
    reader, text = pdf_text(response.content)
    assert '中文工程报告' in text and '<script>原样文字</script>' in text
    assert '0.9' in text and '0.5' not in text
    assert saved['calculation_id'] in text
    assert '完整输入' in text and '完整计算输出' in text and '计算来源与版本' in text
    assert 'synthetic' in text
    for page in reader.pages:
        assert float(page.mediabox.width) == pytest.approx(595.2756, abs=.01)
        assert float(page.mediabox.height) == pytest.approx(841.8898, abs=.01)
        fonts = [font.get_object() for font in page['/Resources']['/Font'].values()]
        embedded = [font for font in fonts if '/FontDescriptor' in font and '/FontFile2' in font['/FontDescriptor']]
        assert embedded, 'Every page must include an embedded TrueType font'
    assert '/JavaScript' not in reader.trailer['/Root']
    assert client.get('/api/v1/calculations/missing/report.pdf').status_code == 404


def test_missing_font_is_explicit_and_html_remains_available(client, snapshot, monkeypatch, tmp_path):
    saved = calculations.save(snapshot)
    monkeypatch.setenv('DCSJ_PDF_FONT', str(tmp_path / 'missing.ttf'))
    assert client.get('/api/v1/status').json()['reports']['pdf']['available'] is False
    response = client.get(f'/api/v1/calculations/{saved["calculation_id"]}/report.pdf')
    assert response.status_code == 503
    assert '字体' in response.json()['detail']
    assert client.get('/reports/' + saved['calculation_id']).status_code == 200


def test_long_text_and_curve_content_survive_pagination(snapshot):
    value = saved_snapshot(snapshot)
    value['name'] = '中文长名称测试' * 15
    value['inputs']['SizeX'] = '含有长文字的设计参数' * 18
    value['warnings'] = ['长说明内容' * 150]
    value['curves'] = [
        {'title': '多系列性能曲线', 'x_label': '负荷比例', 'y_label': '相对值',
         'series': [{'name': f'系列 {i}', 'points': [{'x': 0, 'y': i}, {'x': 1, 'y': i + 1}]} for i in range(4)]},
        {'title': '未收敛曲线', 'x_label': '转差率', 'y_label': '倍数', 'series': [{'name': '转矩', 'points': []}]}]
    reader, text = pdf_text(pdf_report.render(value))
    assert '长说明内容' not in text
    # A repeated phrase may cross a page header in the full-input table.
    assert text.count('含') == 36
    assert '系列 3' in text and '未产生有效采样点' in text
    assert '计算来源与版本' in text and len(reader.pages) < 15


def test_compact_first_page_shows_targets_slots_and_distinct_torque_peak(snapshot):
    value = saved_snapshot(snapshot)
    value['created_at'] = '2026-01-01T20:00:00+00:00'
    value['inputs'].update(ATA=.85, PF=.75, IST=7, TST=2, TM=2.5,
                           b01=3, hs0=1, bs1=6, bs2=8, zs1=30,
                           zc=2, bc02=2, hr0=1, br1=5, br2=3, zs2=20)
    value['values'].update(ATA1=.9, PF1=.8, IST1=6, TST1=2.2, TM1=2.7, Gal=8.12345)
    value['engine']['limitations'] = ['不应输出的边界内容']
    value['curves'] = [{'id': 'slip', 'title': '合成测试曲线', 'x_label': 's', 'y_label': '倍数',
                        'series': [{'name': '电流', 'points': [{'x': 1, 'y': 6}]},
                                   {'name': '转矩', 'points': [{'x': .4, 'y': 3.1}, {'x': 1, 'y': 2.2}]}]}]
    reader, text = pdf_text(pdf_report.render(value))
    first = reader.pages[0].extract_text()
    for label in ('额定条件', '铁心与槽型', '绕组与导线', '设计目标与计算结果', '运行与电磁指标',
                  'b01=3', 'br2=3', '最大转矩（原公式）', '转矩曲线采样峰值', 's=0.4', '8.12345 kg',
                  '2026-01-02 04:00:00'):
        assert label in first
    assert re.search(r'效率 η\s+85\s+90\s+\+5\s+%', first)
    assert re.search(r'最大转矩（原公式）\s+2.5\s+2.7\s+\+0.2\s+倍', first)
    html = reports.render(value)
    assert '<td>效率 η</td><td>85</td><td>90</td><td>+5</td><td>%</td>' in html
    assert '<td>转矩曲线采样峰值</td><td>—</td><td>3.1</td><td>s=0.4</td>' in html
    assert 'viewBox="0 0 180 170"' in html
    for excluded in ('适用边界与计算提示', '不应输出的边界内容', value['warnings'][0], '计算结果须经工程校核'):
        assert excluded not in html and excluded not in text


def test_missing_design_targets_remain_blank_in_summary(snapshot):
    value = saved_snapshot(snapshot)
    del value['inputs']['ATA']
    del value['values']['PF1']
    html = reports.render(value)
    assert '<td>效率 η</td><td>—</td><td>90</td><td>—</td>' in html
    assert '<td>功率因数 cosφ</td><td>0.8</td><td>—</td><td>—</td>' in html


def test_load_efficiency_percent_axis_is_consistent_in_pdf_and_html(snapshot):
    value = saved_snapshot(snapshot)
    value['curves'] = [{'id': 'load_characteristic', 'title': '人工负荷图', 'series': [],
        'plot': {'x_min': 20, 'x_max': 150,
                 'y_axes': [{'id': 'pf', 'label': '功率因数 cosφ', 'min': 0, 'max': 1},
                            {'id': 'efficiency', 'label': '效率 η', 'min': 0, 'max': 1}],
                 'series': [{'name': '功率因数 cosφ', 'axis': 'pf', 'points': [{'x': 100, 'y': .75}]},
                            {'name': '效率 η', 'axis': 'efficiency', 'points': [{'x': 100, 'y': .82}]}]}}]
    original = copy.deepcopy(value)
    reader, text = pdf_text(pdf_report.render(value))
    assert '效率 η (%)' in text and 'η (%)' in text
    html = reports.render(value)
    assert '效率 η (%)' in html and 'η (%)' in html
    assert value == original


@pytest.mark.parametrize('zc,extra,label', [
    (4, {'zs2':-25}, 'br3=7.2'),
    (6, {'zs2':0}, 'hr12=3.2'),
    (22, {'N1':4,'A1':0,'B1':4,'L1':2,'N2':1,'A2':6,'B2':3,'L2':12}, '1: 4/0/4/2'),
])
def test_extended_slot_diagrams_fit_both_report_formats(snapshot, zc, extra, label):
    value = saved_snapshot(snapshot)
    value['inputs'].update(dict(zc=zc,bc02=1.2,hr0=.8,zs2=25,br1=5.6,br2=2.1,br3=7.2,
                                br4=3.4,hr12=3.2,hr3=24) | extra)
    reader, text = pdf_text(pdf_report.render(value))
    first = reader.pages[0].extract_text()
    assert label in first and '运行与电磁指标' in first and '铝重量' in first
    assert '尺寸表' not in first.split('转子槽')[1].split('绕组与导线')[0]
    html = reports.render(value)
    assert label in html and '适用边界与计算提示' not in html
    assert '完整输入' in text and '计算来源与版本' in text


def test_unsupported_characters_fail_instead_of_silent_missing_glyph(snapshot):
    value = saved_snapshot(snapshot)
    value['name'] = 'unsupported \U0010fffd'
    with pytest.raises(pdf_report.PDFContentError, match='U\\+10FFFD'):
        pdf_report.render(value)


def test_pdf_generation_capacity_is_bounded(client, snapshot):
    saved = calculations.save(snapshot)
    assert pdf_report._RENDER_SLOT.acquire(blocking=False)
    try:
        response = client.get(f'/api/v1/calculations/{saved["calculation_id"]}/report.pdf')
        assert response.status_code == 429 and response.headers['retry-after'] == '2'
    finally:
        pdf_report._RENDER_SLOT.release()


def test_corrupt_font_download_never_overwrites_previous_file(tmp_path, monkeypatch):
    destination = tmp_path / 'font.ttf'
    destination.write_bytes(b'previous resource')
    monkeypatch.setattr(pdf_fonts, 'urlopen', lambda *args, **kwargs: BytesIO(b'corrupt download'))
    with pytest.raises(RuntimeError, match='校验失败'):
        pdf_fonts.verified_download('https://example.invalid/font', destination, '0' * 64, 1024)
    assert destination.read_bytes() == b'previous resource'
    assert not list(tmp_path.glob('*.download'))


def test_verified_installed_font_can_be_reused_offline(monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError('Verified local font must not trigger a network request')
    monkeypatch.setattr(pdf_fonts, 'urlopen', no_network)
    manifest = pdf_fonts.install()
    assert manifest['source_sha256'] == pdf_fonts.SOURCE_SHA256
    assert manifest['sha256'] == pdf_fonts.digest(pdf_fonts.font_path())


def test_multiline_parameter_can_split_inside_a_table_row(snapshot):
    value = saved_snapshot(snapshot)
    value['inputs']['SizeX'] = '\n'.join('测试行' for _ in range(49))
    reader, text = pdf_text(pdf_report.render(value))
    # The complete design name appears in both rating and full-input tables.
    assert text.count('测试行') == 98
    assert '计算来源与版本' in text and len(reader.pages) < 15
