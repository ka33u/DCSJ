from io import BytesIO
import copy
import re
from pathlib import Path

import pytest
from pypdf import PdfReader

from dcsj import calculations, pdf_fonts, pdf_report
from dcsj.storage import now


def saved_snapshot(snapshot):
    return {**copy.deepcopy(snapshot), 'calculation_id': '00000000-0000-0000-0000-000000000001',
            'name': '中文设计报告验证', 'created_at': now(), 'project_revision': None}


def pdf_text(data):
    reader = PdfReader(BytesIO(data))
    return reader, '\n'.join(page.extract_text() for page in reader.pages)


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
    assert re.sub(r'\s+', '', text).count('长说明内容') == 150
    assert '系列 3' in text and '未产生有效采样点' in text
    assert '计算来源与版本' in text and len(reader.pages) < 15


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
