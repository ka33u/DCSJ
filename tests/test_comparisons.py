from io import BytesIO
import copy
import re

from pypdf import PdfReader
import pytest

from dcsj import calculations, comparisons, comparison_reports, pdf_report, storage
from dcsj.backup import backup, restore


def pair(snapshot):
    first = calculations.save(snapshot, '人工基准')
    snapshot['values']['ATA1'] = .92
    snapshot['inputs']['L'] += 10
    second = calculations.save(snapshot, '人工候选')
    return [first['calculation_id'], second['calculation_id']]


def test_comparison_preserves_union_of_metrics_and_missing_values(client, snapshot):
    snapshot['values']['CostFx'] = 0
    first = calculations.save(snapshot)
    snapshot['values'].pop('PF1')
    snapshot['values']['I1'] = 20
    snapshot['values']['CostFx'] = 50
    second = calculations.save(snapshot)
    data = calculations.compare([first['calculation_id'], second['calculation_id']])
    metrics = {m['key']: m['cells'] for m in data['metrics']}
    assert metrics['I1'][1] == {'id': second['calculation_id'], 'value': 20, 'delta': None, 'delta_percent': None}
    assert metrics['PF1'][1]['value'] is None
    assert metrics['CostFx'][1]['delta'] == 50 and metrics['CostFx'][1]['delta_percent'] is None
    assert any('缺少计算指标' in warning for warning in data['warnings'])


def test_saved_comparison_freezes_order_baseline_results_and_labels(client, snapshot, monkeypatch):
    ids = pair(snapshot)
    response = client.post('/api/v1/comparison-sets', json={
        'name': '  中文评审  ', 'notes': '第一行\n第二行', 'calculation_ids': ids[::-1], 'baseline_id': ids[0]})
    assert response.status_code == 201
    saved = response.json()
    assert saved['name'] == '中文评审'
    assert [s['id'] for s in saved['schemes']] == ids[::-1]
    assert saved['baseline_id'] == ids[0]
    monkeypatch.setattr(calculations, 'compare', lambda *a, **k: pytest.fail('Saved records must not be recalculated'))
    monkeypatch.setattr(calculations, 'field_info', lambda k: {'label': 'changed labels'})
    storage._initialized.clear()
    assert client.get('/api/v1/comparison-sets/' + saved['id']).json() == saved
    result = client.get('/api/v1/comparison-sets?limit=1').json()
    assert result['total'] == 1 and result['items'][0]['scheme_count'] == 2
    assert client.get('/api/v1/comparison-sets?offset=1').json()['items'] == []
    assert client.get('/api/v1/comparison-sets/missing').status_code == 404
    assert client.put('/api/v1/comparison-sets/' + saved['id'], json={'name': 'changed'}).status_code == 405


def test_save_validation_leaves_no_partial_record(client, snapshot):
    ids = pair(snapshot)
    for body in [
        {'name': ' ', 'calculation_ids': ids}, {'name': 'x', 'calculation_ids': ids * 5},
        {'name': 'x', 'calculation_ids': [ids[0]] * 2}, {'name': 'x', 'calculation_ids': ids, 'baseline_id': 'missing'},
        {'name': 'x', 'calculation_ids': ids, 'metrics': []},
    ]:
        assert client.post('/api/v1/comparison-sets', json=body).status_code == 422
    assert client.post('/api/v1/comparison-sets', json={'name': 'x', 'calculation_ids': [ids[0], 'missing']}).status_code == 404
    assert client.get('/api/v1/comparison-sets').json()['total'] == 0


def test_upgrade_and_backup_preserve_comparison_and_original_calculations(client, snapshot, tmp_path, monkeypatch):
    ids = pair(snapshot)
    with storage.connect() as db:
        db.execute('DROP TABLE comparison_sets')
        db.execute('PRAGMA user_version=2')
    storage._initialized.clear()
    saved = comparisons.save('升级后保存', ids)
    with storage.connect() as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == storage.SCHEMA_VERSION
        assert db.execute('SELECT count(*) FROM calculations').fetchone()[0] == 2
    info = backup(storage.database_path(), tmp_path / 'backup.sqlite')
    restore(tmp_path / 'backup.sqlite', tmp_path / 'restored.sqlite', info['sha256'])
    monkeypatch.setenv('DCSJ_PROJECTS_DB', str(tmp_path / 'restored.sqlite'))
    assert comparisons.get(saved['id']) == saved
    assert calculations.get(ids[0])['values']['ATA1'] == .9


def test_comparison_reports_use_saved_content_and_escape_text(client, snapshot):
    snapshot['engine']['limitations'] = ['人工计算边界说明']
    saved = comparisons.save('<script>评审</script>', pair(snapshot), notes='<img src="https://example.invalid/private">')
    html = client.get('/comparison-reports/' + saved['id'])
    assert html.status_code == 200 and html.headers['cache-control'] == 'no-store'
    assert '<script>' not in html.text and '<img src=' not in html.text
    assert '&lt;script&gt;' in html.text and 'A4 landscape' in html.text
    assert saved['id'] in html.text and 'synthetic' in html.text and '人工计算边界说明' in html.text
    response = client.get('/api/v1/comparison-sets/' + saved['id'] + '/report.pdf')
    assert response.status_code == 200 and response.headers['content-type'] == 'application/pdf'
    assert response.headers['cache-control'] == 'no-store'
    reader = PdfReader(BytesIO(response.content))
    content = '\n'.join(page.extract_text() for page in reader.pages)
    assert '<script>评审</script>' in content and 'synthetic' in content and '人工计算边界说明' in content
    assert saved['id'] in content and '变化的输入参数' in content
    for page in reader.pages:
        assert float(page.mediabox.width) == pytest.approx(841.8898, abs=.01)
        assert float(page.mediabox.height) == pytest.approx(595.2756, abs=.01)
        assert any('/FontFile2' in font.get_object().get('/FontDescriptor', {}) for font in page['/Resources']['/Font'].values())
    assert client.get('/comparison-reports/missing').status_code == 404


def test_eight_scheme_report_repeats_baseline_without_losing_candidates(client, snapshot):
    ids = []
    for i in range(8):
        item = copy.deepcopy(snapshot)
        item['inputs']['L'] += i
        item['values'].update({f'synthetic_{n}': n + i for n in range(40)})
        ids.append(calculations.save(item, f'人工方案{i + 1}的较长中文名称' * 3)['calculation_id'])
    saved = comparisons.save('八方案中文评审报告', ids, ids[5], notes='仅供分页验证' * 100)
    groups = comparison_reports.groups(saved)
    assert len(groups) == 3 and all(group[0]['id'] == ids[5] for group in groups)
    assert [s['id'] for group in groups for s in group[1:]] == ids[:5] + ids[6:]
    reader = PdfReader(BytesIO(comparison_reports.render_pdf(saved)))
    assert '仅供分页验证' in reader.pages[0].extract_text()
    content = re.sub(r'\s+', '', '\n'.join(page.extract_text() for page in reader.pages))
    assert content.count('仅供分页验证') == 100
    assert all(identity in content for identity in ids)
    assert all(f'人工方案{i}的较长中文名称' in content for i in range(1, 9))
    assert 'synthetic_39' in content and '第3组' in content and len(reader.pages) < 30


def test_comparison_pdf_shares_render_capacity_and_missing_font_errors(client, snapshot, monkeypatch, tmp_path):
    saved = comparisons.save('资源限制测试', pair(snapshot))
    path = '/api/v1/comparison-sets/' + saved['id'] + '/report.pdf'
    assert pdf_report._RENDER_SLOT.acquire(blocking=False)
    try:
        response = client.get(path)
        assert response.status_code == 429 and response.headers['retry-after'] == '2'
    finally:
        pdf_report._RENDER_SLOT.release()
    monkeypatch.setenv('DCSJ_PDF_FONT', str(tmp_path / 'missing.ttf'))
    assert client.get(path).status_code == 503
    html = client.get('/comparison-reports/' + saved['id'])
    assert html.status_code == 200 and '下载 PDF</a>' not in html.text
