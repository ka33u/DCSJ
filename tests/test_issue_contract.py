"""Issue regressions use synthetic inputs; no private catalog data is published."""
import copy
import json
import math
import sqlite3
from contextlib import closing

import pytest

from dcsj import api, calculations, catalog, comparisons, pdf_fonts, projects, semantics, storage
from dcsj.parameters import InputError, check_keys, input_parameters, schema, validate


def test_exact_external_keys_and_legacy_projection(client, parameters):
    legacy = {**parameters, 'PFE': 99}
    legacy['Kw'] = legacy.pop('kW')
    assert validate(legacy)['kW'] == 12
    for bad in ({**parameters, 'Kw': 18.5}, legacy, {**parameters, 'ATA1': .1}):
        response = client.post('/api/v1/calculate', json={'parameters': bad})
        assert response.status_code == 422
    response = client.post('/api/v1/projects', json={'name': 'typo', 'parameters': {'Kw': 18}})
    assert response.status_code == 422 and 'kW' in response.text
    assert client.get('/api/v1/projects').json()['items'] == []
    assert set(input_parameters(legacy)) == set(parameters)
    with pytest.raises(InputError):
        check_keys({'not_a_parameter': 1})


def test_loss_units_threshold_and_opt_in_speed(parameters):
    p = validate({**parameters, 'pfw': .8, 'ps': 1, 'pfw_unit': 'W', 'ps_unit': 'W'})
    losses = semantics.losses(p)
    assert losses['pfw']['rated_w'] == pytest.approx(.8, rel=1e-7)
    assert losses['ps']['rated_w'] == pytest.approx(1, rel=1e-7)
    assert losses['ps']['load_dependence'] == 'proportional_to_output_power'
    legacy = semantics.losses({**p, 'pfw': 1.00000001, 'pfw_unit': 'legacy'})['pfw']
    assert legacy['resolved_unit'] == 'pu' and legacy['kernel_pu'] == 1
    assert semantics.losses({**p, 'pfw': 2, 'pfw_unit': 'legacy'})['pfw']['rated_w'] == pytest.approx(2)
    scaled = semantics.losses({**p, 'f': 60, 'pfw_ref_rpm': 1500})['pfw']
    assert scaled['speed_scaling']['factor'] == pytest.approx(1.44)
    assert scaled['rated_w'] == pytest.approx(.8 * 1.44, rel=1e-7)
    assert semantics.losses({**p, 'f': 60})['pfw']['rated_w'] == losses['pfw']['rated_w']
    with pytest.raises(InputError):
        semantics.losses({**p, 'ps': 1.1, 'ps_unit': 'pu'})
    for overrides in ({'ps_unit': 'watt'}, {'pfw': -1}, {'ATA_req': 1.1}, {'pfw_speed_exponent': 7}):
        with pytest.raises(InputError):
            validate({**parameters, **overrides}, strict=True)


def test_response_values_si_requirements_and_independent_curve(parameters):
    values = {'ATA1': .9, 'PF1': .81, 'IST1': 6, 'TST1': 2, 'TM1': 2.8, 'Bg': 6000}
    curves = [{'id': 'slip', 'series': [
        {'points': [{'x': 1, 'y': 6.2}]}, {'points': [{'x': 1, 'y': 2.1}, {'x': .3, 'y': 3.3}]}]}]
    p = validate({**parameters, 'f': 60})
    first = semantics.result_metadata(p, values, {'kind': 'mdb_custom'}, curves)
    second = semantics.result_metadata({**p, 'ATA_req': .95}, values, {'kind': 'mdb_custom'}, copy.deepcopy(curves))
    assert first['input_fingerprints']['core_input_sha256'] == second['input_fingerprints']['core_input_sha256']
    assert first['input_fingerprints']['requirements_sha256'] != second['input_fingerprints']['requirements_sha256']
    assert second['requirements'][0]['passed'] is False
    assert second['requirements'][0]['target_source'] == 'ATA_req'
    assert first['values_si']['Bg'] == {'value': .6, 'unit': 'T'}
    assert semantics.metric_info('Bg', 6000)['si'] == first['values_si']['Bg']
    assert first['material_semantics']['loss_curve_frequency_hz'] is None
    assert first['material_semantics']['loss_frequency_model']['factor'] == pytest.approx(1.274415546)
    assert first['electrical']['line_voltage_v'] == pytest.approx(230 * math.sqrt(3))
    assert first['curve_checks'][2]['curve_value'] == 3.3 and first['curve_checks'][2]['check_value'] == 2.8
    assert curves[0]['authoritative'] is False and curves[0]['series'][0]['points'][0]['y'] == 6.2
    assert semantics.electrical({'Ufai': '230', 'Jiefa': '△'})['line_voltage_v'] == 230
    assert semantics.electrical({'Ufai': '230'})['line_voltage_v'] is None


def test_dry_run_model_override_and_frozen_project_lineage(client, parameters, snapshot, monkeypatch):
    source = {'id': 'synthetic', 'model': 'Synthetic-180M-4', 'source': {'path': 'test'}, 'parameters': parameters}
    monkeypatch.setattr(catalog, 'model', lambda identity: copy.deepcopy(source) if identity == 'synthetic' else None)
    def compute(inputs):
        return {**copy.deepcopy(snapshot), 'inputs': validate(inputs)}
    monkeypatch.setattr(api, 'compute', compute)
    before = client.get('/api/v1/calculations').json()['total']
    response = client.post('/api/v1/models/synthetic/calculate', json={'overrides': {'L': 170}, 'persist': False})
    assert response.status_code == 200
    trial = response.json()
    assert trial['inputs']['L'] == 170 and trial['calculation_id'] is None and trial['persisted'] is False
    assert trial['provenance']['base_model']['id'] == 'synthetic'
    with storage.connect() as db:
        assert db.execute('SELECT count(*) FROM audit_events').fetchone()[0] == 0
    assert client.get('/api/v1/calculations').json()['total'] == before
    assert client.post('/api/v1/models/synthetic/calculate', json={'overrides': {'Kw': 18}}).status_code == 422
    assert client.post('/api/v1/models/missing/calculate', json={}).status_code == 404
    project = client.post('/api/v1/projects', json={'name': 'test', 'parameters': parameters, 'base_model_id': 'synthetic'}).json()
    original = project['lineage']
    source['parameters'] = {**parameters, 'L': 190}  # Simulate a new catalog version.
    update = client.put('/api/v1/projects/' + project['id'], json={
        'name': 'revised', 'parameters': parameters, 'expected_revision': 1, 'base_model_id': 'synthetic'})
    assert update.status_code == 200 and update.json()['lineage'] == original
    result = client.post('/api/v1/calculate', json={'parameters': parameters, 'project_id': project['id'], 'project_revision': 2}).json()
    assert result['persisted'] and result['provenance']['base_model'] == original
    assert projects.get_revision(project['id'], 1)['lineage'] == original
    assert client.post('/api/v1/calculate', json={'parameters': parameters, 'project_id': project['id'],
           'project_revision': 2, 'base_model_id': 'different'}).status_code == 409


def test_archive_delete_and_bulk_preview_protect_references(client, snapshot):
    a = calculations.save(snapshot, 'trial_A')['calculation_id']
    b = calculations.save(snapshot, 'trial_B')['calculation_id']
    c = calculations.save(snapshot, 'trial_C')['calculation_id']
    comparisons.save('saved', [a, b])
    assert client.delete('/api/v1/calculations/' + b).status_code == 409  # Non-baseline protected too.
    preview = client.post('/api/v1/calculations/cleanup', json={'name_prefix': 'trial_', 'action': 'delete'}).json()
    assert preview['matched'] == 3 and preview['eligible_ids'] == [c] and preview['changed'] == 0
    assert len(preview['skipped']) == 2
    assert client.get('/api/v1/calculations').json()['total'] == 3
    assert client.post('/api/v1/calculations/cleanup', json={}).status_code == 422
    assert client.post('/api/v1/calculations/cleanup', json={'before': '2026-01-01'}).status_code == 422
    actual = client.post('/api/v1/calculations/cleanup', json={'name_prefix': 'trial_', 'action': 'delete', 'dry_run': False}).json()
    assert actual['changed'] == 1 and client.get('/api/v1/calculations/' + c).status_code == 404
    archived = client.post('/api/v1/calculations/' + a + '/archive', json={}).json()
    assert archived['archived']
    assert client.get('/api/v1/calculations').json()['total'] == 1
    assert client.get('/api/v1/calculations?include_archived=true').json()['total'] == 2
    assert client.post('/api/v1/calculations/' + a + '/archive', json={'archived': False}).json()['archived'] is False
    assert client.delete('/api/v1/calculations/missing').status_code == 404
    worker_result = copy.deepcopy(snapshot)
    worker_result['provenance']['optimization_job_id'] = 'pending-worker'
    identity = calculations.save(worker_result)['calculation_id']
    assert client.delete('/api/v1/calculations/' + identity).status_code == 409


def test_catalog_filters_return_pure_inputs_and_electrical_semantics(client, parameters, tmp_path, monkeypatch):
    path = tmp_path / 'catalog.sqlite'
    with closing(sqlite3.connect(path)) as db:
        db.execute('CREATE TABLE model_catalog(record_id,model,series,rated_power_kw,poles,voltage_v,frequency_hz,source_file,source_table,row_number,is_primary,raw_json,normalized_json,source_sha256)')
        for i, frequency, power in [(1, 50, 12), (2, 60, 15), (3, 60, 18)]:
            raw = {**parameters, 'Kw': power, 'ATA1': .8}
            raw.pop('kW')
            db.execute('INSERT INTO model_catalog VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (
                str(i), 'TEST-180M-4', 'TEST', power, 4, 230, frequency, 'synthetic', 'A', i, 1,
                json.dumps(raw), '{"historical_results":{"ATA1":0.8}}', 'synthetic'))
        db.commit()
    monkeypatch.setattr(catalog, 'catalog_path', lambda: path)
    response = client.get('/api/v1/models?frequency_hz=60&poles=4&voltage_v=230&power_min=14&power_max=16&frame=180&source_table=A')
    assert response.status_code == 200
    items = response.json()['items']
    assert len(items) == 1 and items[0]['id'] == '2'
    assert items[0]['connection'] == 'Y' and items[0]['line_voltage_v'] == pytest.approx(230 * math.sqrt(3))
    assert client.get('/api/v1/models?q=%25').json()['total'] == 0
    assert client.get('/api/v1/models?power_min=20&power_max=10').status_code == 422
    assert client.get('/api/v1/models?poles=3').status_code == 422
    model = client.get('/api/v1/models/2').json()
    assert 'ATA1' not in model['parameters'] and 'Kw' not in model['inputs']
    assert model['inputs']['kW'] == 15 and model['raw_parameters']['ATA1'] == .8
    assert model['historical_results']['ATA1'] == .8


def test_font_failure_is_actionable_and_documented(client, snapshot, tmp_path, monkeypatch):
    path = tmp_path / 'corrupt.ttf'
    path.write_bytes(b'not-a-font')
    monkeypatch.setenv('DCSJ_PDF_FONT', str(path))
    assert pdf_fonts.status()['available'] is False and pdf_fonts.status()['embedded'] is False
    saved = calculations.save(snapshot)
    result = client.get('/api/v1/calculations/' + saved['calculation_id'] + '/report.pdf')
    assert result.status_code == 503 and result.json()['retryable'] is False
    assert result.json()['code'] == 'PDF_FONT_UNAVAILABLE'
    assert result.headers['content-location'] == result.json()['html_report_url']
    spec = client.get('/openapi.json').json()
    assert '503' in spec['paths']['/api/v1/calculations/{calculation_id}/report.pdf']['get']['responses']
    assert 'CalculationResult' in spec['components']['schemas']


def test_schema_and_v4_migration_preserve_snapshots(client, parameters, snapshot):
    item = projects.save_project('before', {**parameters, 'PFE': 999})
    saved = calculations.save(snapshot)
    with storage.connect() as db:
        for table, column in [('projects', 'lineage'), ('project_revisions', 'lineage'), ('calculations', 'archived')]:
            db.execute(f'ALTER TABLE {table} DROP COLUMN {column}')
        db.execute('PRAGMA user_version=4')
    storage._initialized.clear()
    assert projects.get_project(item['id'])['lineage'] == {}
    assert projects.get_project(item['id'])['raw_parameters']['PFE'] == 999
    assert 'PFE' not in projects.get_project(item['id'])['parameters']
    assert calculations.get(saved['calculation_id'])['values'] == snapshot['values']
    fields = {f['key']: f for f in schema()['fields']}
    assert fields['ATA_req']['participates_in_calculation'] is False
    assert fields['ATA']['purpose'] == 'legacy_iteration_seed'
    assert fields['Bg']['unit'] == 'G'
