"""Dimensional relationships and quick modeling use synthetic templates."""
from copy import deepcopy

import pytest

from dcsj import modeling
from dcsj.parameters import InputError, validate


@pytest.fixture
def template(parameters, monkeypatch):
    record = {'id': 'synthetic-template', 'parameters': deepcopy(parameters), 'source': '人工模板'}
    monkeypatch.setattr(modeling.catalog, 'model', lambda identity: deepcopy(record) if identity == record['id'] else None)
    return record


@pytest.mark.parametrize('dimensions', [
    {'D1': 180, 'Di1': 180},
    {'D1': 300, 'Di1': 310, 'g': 10},
    {'Di2': 178.8},
    {'g': 0},
])
def test_all_concentric_diameters_are_strictly_ordered(client, parameters, dimensions):
    invalid = {**parameters, **dimensions}
    with pytest.raises(InputError):
        validate(invalid)
    response = client.post('/api/v1/geometry/preview', json={'parameters': invalid})
    assert response.status_code == 422


def test_rotor_diameter_derives_gap_from_changed_bore_and_discloses_formula(client, template):
    response = client.post('/api/v1/modeling/preview', json={
        'model_id': template['id'], 'changes': {'Di1': 182, 'L': 170}, 'rotor_outer_mm': 178.4})
    assert response.status_code == 200
    result = response.json()
    assert result['parameters']['g'] == pytest.approx(1.8)
    assert result['parameters']['L'] == 170
    assert result['geometry']['dimensions']['rotor_outer_mm'] == pytest.approx(178.4)
    changes = {item['key']: item for item in result['changes']}
    assert set(changes) == {'Di1', 'L', 'g'}
    assert changes['g']['before'] == .6 and changes['g']['after'] == pytest.approx(1.8)
    linkage = result['linkages'][0]
    assert linkage['formula'] == 'g = (Di1 - D2) / 2'
    assert linkage['inputs'] == {'Di1': 182, 'D2': 178.4}
    assert linkage['target'] == 'g' and linkage['value'] == pytest.approx(1.8)
    assert linkage['unit'] == 'mm'
    assert template['parameters']['g'] == .6
    for key in ('ZZ', 'JJ', 'Djj', 'hs12', 'hr12', 'Ufai', 'kW'):
        assert result['parameters'][key] == template['parameters'][key]


def test_direct_gap_mode_retains_existing_api_contract(client, template):
    result = client.post('/api/v1/modeling/preview', json={
        'model_id': template['id'], 'changes': {'g': .8}}).json()
    assert result['parameters']['g'] == .8 and result['linkages'] == []
    assert result['geometry']['dimensions']['rotor_outer_mm'] == pytest.approx(178.4)
    assert [item['key'] for item in result['changes']] == ['g']


@pytest.mark.parametrize('changes,diameter', [
    ({'g': .8}, 178.4),  # Two sources of truth for the same gap are rejected.
    ({}, 180), ({}, 181), ({}, 50), ({}, 0), ({}, -1),
    ({}, True), ({}, '178.4'), ({'Di1': None}, 170),
    ({'Di1': 310}, 290),  # Rotor fits in D1, but the stator bore does not.
])
def test_conflicting_or_impossible_linked_dimensions_rejected(client, template, changes, diameter):
    response = client.post('/api/v1/modeling/preview', json={
        'model_id': template['id'], 'changes': changes, 'rotor_outer_mm': diameter})
    assert response.status_code == 422


def test_linkage_can_repair_gap_after_bore_change_and_requires_valid_template(client, template):
    # Validate the final linked geometry, not an intermediate stale gap.
    result = client.post('/api/v1/modeling/preview', json={
        'model_id': template['id'], 'changes': {'Di1': 51}, 'rotor_outer_mm': 50.8})
    assert result.status_code == 200 and result.json()['parameters']['g'] == pytest.approx(.1)
    assert client.post('/api/v1/modeling/preview', json={
        'model_id': 'missing', 'rotor_outer_mm': 178.4}).status_code == 404
