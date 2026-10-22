"""Synthetic user records, revision protection and explicit material-unit semantics."""
import copy
import pytest
from dcsj import library, optimization, storage
from dcsj.engine import CalculationError


def material_document(**changes):
    return {'source': 'Synthetic test only', 'notes': '', 'frequency_hz': 50,
            'h_unit': 'A/m', 'loss_unit': 'kernel_native',
            'bh': [[.5, 100], [1, 200], [1.88, 400]],
            'loss': [[.5, 1], [1.88, 3]], **changes}


def test_model_revision_conflicts_and_duplicate_names(client, parameters):
    parameters['UnknownField'] = 37
    body = {'name': 'Same synthetic name', 'document': {'parameters': parameters, 'source': 'Manual test'}}
    assert client.post('/api/v1/library/models', json=body).status_code == 422
    parameters.pop('UnknownField')
    first = client.post('/api/v1/library/models', json=body).json()
    second = client.post('/api/v1/library/models', json=body).json()
    assert first['id'] != second['id'] and first['revision'] == 1
    body['document']['parameters']['L'] = 170
    updated = client.put('/api/v1/library/models/' + first['id'], json={**body, 'expected_revision': 1})
    assert updated.status_code == 200 and updated.json()['revision'] == 2
    assert client.put('/api/v1/library/models/' + first['id'], json={**body, 'expected_revision': 1}).status_code == 409
    old = library.from_reference(first['reference'], 'models')
    assert old['document']['parameters']['L'] == 160
    assert client.get('/api/v1/models/' + first['reference']).json()['parameters']['L'] == 160
    assert len(client.get('/api/v1/library/models/' + first['id'] + '/revisions').json()['items']) == 2
    assert client.get('/api/v1/library/models?limit=1&offset=1').json()['total'] == 2


def test_material_revision_archive_and_fixed_reference(client):
    first = library.save('materials', 'Synthetic curve', material_document())
    second = library.save('materials', 'Synthetic curve', material_document(loss_unit='W/kg'), first['id'], 1)
    assert first['compatibility']['compatible'] and not second['compatibility']['compatible']
    assert first['reference'] not in library.material_choices()  # choices only show current revisions
    assert optimization.allowed_material(first['reference'])  # queued fixed references remain valid
    library.archive('materials', first['id'], 2, True)
    assert library.list_entries('materials')['total'] == 0
    assert library.list_entries('materials', True)['total'] == 1
    assert library.from_reference(first['reference'], 'materials')['document']['loss_unit'] == 'kernel_native'
    assert client.get('/api/v1/materials/' + first['reference']).json()['curves'][0]['y_label'] == 'H (A/m)'
    assert client.put('/api/v1/library/materials/' + first['id'], json={'name': 'change', 'document': material_document(), 'expected_revision': 2}).status_code == 409
    library.archive('materials', first['id'], 2, False)
    assert library.list_entries('materials')['total'] == 1


@pytest.mark.parametrize('changes', [
    {'bh': [[.5, 1], [.5, 2]]}, {'bh': [[1, 1], [.5, 2]]},
    {'loss': [[.5, 2], [1.88, 1]]}, {'loss': [[.5, -1], [1.88, 2]]},
    {'bh': [[.5, 1]]}, {'source': '  '}, {'frequency_hz': 0}, {'h_unit': 'unspecified'},
])
def test_invalid_materials_rejected_without_partial_records(client, changes):
    result = client.post('/api/v1/library/materials', json={'name': 'Invalid test', 'document': material_document(**changes)})
    assert result.status_code == 422
    assert library.list_entries('materials')['total'] == 0


@pytest.mark.parametrize('changes', [{'loss_unit': 'W/kg'}, {'loss_unit': 'W/m3'}, {'frequency_hz': 60}, {'bh': [[.6, 1], [1.88, 2]]}])
def test_manage_incompatible_curves_but_never_silently_compute(client, changes):
    saved = library.save('materials', 'Managed only', material_document(**changes))
    assert not saved['compatibility']['compatible']
    with pytest.raises(CalculationError):
        library.calculation_material(saved['reference'], 50, {})


def test_native_material_interpolation_unit_conversion_and_immutability(client):
    entry = library.save('materials', 'Synthetic native', material_document())
    base = {'kind': 'mdb_custom', 'slot': 5, 'const_values': [7.] * 693}
    original = copy.deepcopy(base)
    compiled = library.calculation_material(entry['reference'], 50, {'Synthetic': base})
    assert base == original
    assert len(compiled['H_values']) == 49 and len(compiled['P_values']) == 42
    assert compiled['H_values'][0] == 1 and compiled['H_values'][-1] == 4
    assert compiled['H_values'][1] == pytest.approx(1.1)
    assert compiled['const_values'][434:483] == compiled['H_values']
    assert compiled['const_values'][651:693] == compiled['P_values']
    assert compiled['const_values'][433] == 7 and compiled['const_values'][650] == 7
    assert compiled['user_document_sha256'] == entry['document_sha256']
    with pytest.raises(CalculationError):
        library.calculation_material(entry['reference'], 60, {'Synthetic': base})


def test_schema_three_upgrade_preserves_existing_design_and_history(tmp_path, monkeypatch, parameters):
    from dcsj import projects
    monkeypatch.setenv('DCSJ_PROJECTS_DB', str(tmp_path / 'upgrade.sqlite'))
    project = projects.save_project('Before migration', parameters)
    with storage.connect() as db:
        db.execute('DROP TABLE library_revisions'); db.execute('DROP TABLE library_entries')
        db.execute('PRAGMA user_version=3')
    entry = library.save('materials', 'After migration', material_document())
    assert projects.get_project(project['id'])['parameters'] == parameters
    assert library.get('materials', entry['id'])['revision'] == 1
    with storage.connect() as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == storage.SCHEMA_VERSION
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
