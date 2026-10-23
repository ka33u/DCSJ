"""Synthetic regressions for selected wire provenance, API and reports."""
import copy
from io import BytesIO

import pytest
from pypdf import PdfReader

from dcsj import calculations, engine, pdf_report, report_summary, reports, wire_selection
from dcsj.kernel_layout import INPUT_MAP, OUTPUT_MAP
from dcsj.numeric_vm import Memory, NumericKernelError
from dcsj.storage import now


def memory(used):
    state = Memory({'image_base': 0x400000, 'image_size': 0x100000,
                    'stack_base': 0x1000000, 'stack_size': 4096, 'initial_segments': []})
    for key, value in used.items():
        state.set_float(INPUT_MAP[key], value)
    return state


@pytest.mark.parametrize('target', [0, .8])
def test_kernel_wire_reaches_api_snapshot_and_report(client, parameters, monkeypatch, target):
    p = {**parameters, '_wire_target': target}
    used = {'JJ': 3, 'Djj': 1.25, 'MM': 1, 'Dmm': .75} if target else {k: p[k] for k in wire_selection.KEYS}
    mem = memory(used)
    raw = [0.] * 80
    for key, value in {'ATA1': .9, 'PF1': .8, 'Sf': .775}.items():
        raw[OUTPUT_MAP[key]] = value
    monkeypatch.setattr(engine, 'resource_context', lambda: {
        'kernel_path': 'synthetic', 'kernel_sha256': 'synthetic',
        'package_id': None, 'package_version': 'synthetic'})
    monkeypatch.setattr(engine, 'engine_status', lambda context: {'name': 'synthetic', 'missing_assets': []})
    monkeypatch.setattr(engine, 'prepare_memory', lambda p, context: ({}, {'name': 'synthetic', 'kind': 'test', 'slot': 1}))
    monkeypatch.setattr(engine, 'run', lambda *args, **kwargs: {
        'outputs': {'101': raw}, 'memory': mem, 'steps': 1, 'precision': 'synthetic'})
    request = {'parameters': p, 'persist': False}
    result = client.post('/api/v1/calculate', json=request)
    assert result.status_code == 200, result.text
    data = result.json()
    wire = data['wire_selection']
    assert wire['used'] == used and wire['input'] == {k: p[k] for k in wire_selection.KEYS}
    assert wire['mode'] == ('target_fill' if target else 'fixed')
    assert wire['ignored_input_keys'] == (list(wire_selection.KEYS) if target else [])
    assert wire['target_fill'] == (target or None)
    assert wire['actual_fill'] == data['values']['Sf'] == .775
    assert wire['fill_delta'] == (pytest.approx(-.025) if target else None)
    assert wire['units']['Dmm'] == 'mm' and wire['source'] == 'kernel-final-state'
    assert data['inputs']['JJ'] == p['JJ'] == 4
    assert data['contract']['effective_wire'] == 'wire_selection.used'
    assert client.get('/api/v1/calculations').json()['total'] == 0
    request['persist'] = True
    saved = client.post('/api/v1/calculate', json=request).json()
    mem.set_float(INPUT_MAP['JJ'], 9)  # Subsequent state cannot alter stored wire.
    reopened = client.get('/api/v1/calculations/' + saved['calculation_id']).json()
    assert reopened['wire_selection'] == wire and reopened['inputs'] == data['inputs']
    html = client.get('/reports/' + saved['calculation_id']).text
    assert f'<td>采用根数 JJ</td><td>{used["JJ"]}</td>' in html
    if target:
        assert '原输入（未采用）：JJ=4' in html
    spec = client.get('/openapi.json').json()['components']['schemas']
    assert 'wire_selection' in spec['CalculationResult']['properties']
    assert set(spec['WireSpecification']['properties']) == set(wire_selection.KEYS)


def test_invalid_or_missing_kernel_wire_cannot_masquerade_as_input(parameters):
    for bad in ({'JJ': 0, 'Djj': 1, 'MM': 0, 'Dmm': 1},
                {'JJ': 1.5, 'Djj': 1, 'MM': 0, 'Dmm': 0},
                {'JJ': 1, 'Djj': float('nan'), 'MM': 0, 'Dmm': 0},
                {'JJ': 1, 'Djj': 0, 'MM': 0, 'Dmm': 0}):
        with pytest.raises(NumericKernelError, match='线规无效'):
            wire_selection.extract(parameters, {'Sf': .7}, memory(bad))
    used = {'JJ': 1, 'Djj': 1.25, 'MM': 0, 'Dmm': 1.5}
    assert wire_selection.extract(parameters, {'Sf': .7}, memory(used))['used'] == used
    used['Dmm'] = -1  # Preserve an unused legacy placeholder, just as validation does.
    assert wire_selection.extract(parameters, {'Sf': .7}, memory(used))['used'] == used


@pytest.mark.parametrize('target', [0, .8])
def test_old_snapshots_never_invent_selected_wire(snapshot, target):
    snapshot['inputs']['_wire_target'] = target
    before = copy.deepcopy(snapshot)
    rows = dict((label, value) for label, value, unit in report_summary.windings(snapshot))
    assert rows['采用根数 JJ'] == ('—' if target else '4')
    note = report_summary.wire_note(snapshot)
    if target:
        assert '旧记录未保存反算线规' in note and '原输入（未采用）：JJ=4' in note
    else:
        assert note == ''
    assert snapshot == before


def test_selected_wire_first_page_html_and_pdf(snapshot):
    snapshot['inputs']['_wire_target'] = .8
    snapshot['values']['Sf'] = .775
    snapshot['wire_selection'] = wire_selection.extract(snapshot['inputs'], snapshot['values'],
        memory({'JJ': 3, 'Djj': 1.25, 'MM': 1, 'Dmm': .75}))
    snapshot.update(calculation_id='synthetic', name='线规对照', created_at=now())
    before = copy.deepcopy(snapshot)
    first_html = reports.render(snapshot).split('</section>')[0]
    first_pdf = PdfReader(BytesIO(pdf_report.render(snapshot))).pages[0].extract_text()
    for text in [first_html, first_pdf]:
        for label in ('采用根数 JJ', '采用线径 Djj', '原输入（未采用）',
                      '设计目标与计算结果', '运行与电磁指标', '铝重量'):
            assert label in text
    assert '<td>采用线径 Djj</td><td>1.25 mm</td>' in first_html
    assert '<td>槽满率</td><td>80</td><td>77.5</td><td>-2.5</td>' in first_html
    assert snapshot == before
