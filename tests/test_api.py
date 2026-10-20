from dcsj import calculations, engine


def test_revision_conflict_and_immutable_history(client):
    old = client.post('/api/v1/projects', json={'name': 'A', 'parameters': {'kW': 12}}).json()
    update = {'name': 'B', 'parameters': {'kW': 14}, 'expected_revision': 1}
    changed = client.put('/api/v1/projects/' + old['id'], json=update)
    assert changed.status_code == 200 and changed.json()['revision'] == 2
    assert client.put('/api/v1/projects/' + old['id'], json=update).status_code == 409
    history = client.get(f"/api/v1/projects/{old['id']}/revisions/1").json()
    assert history['parameters']['kW'] == 12 and history['name'] == 'A'
    assert client.post(f"/api/v1/projects/{old['id']}/archive", json={
        'archived': True, 'expected_revision': 2}).status_code == 200
    assert client.get('/api/v1/projects').json()['items'] == []
    assert len(client.get('/api/v1/projects?include_archived=true').json()['items']) == 1


def test_comparison_uses_snapshots_and_ratings(client, snapshot):
    a = calculations.save(snapshot, 'A')
    snapshot['values']['ATA1'] = .92
    snapshot['inputs']['kW'] = 15
    b = calculations.save(snapshot, 'B')
    response = client.post('/api/v1/comparisons', json={'calculation_ids': [a['calculation_id'], b['calculation_id']]})
    result = response.json()
    assert response.status_code == 200
    assert result['metrics'][0]['cells'][0]['value'] == .9
    assert abs(result['metrics'][0]['cells'][1]['delta'] - .02) < 1e-10
    assert result['warnings'] and result['parameter_differences'][0]['key'] == 'kW'
    assert client.post('/api/v1/comparisons', json={'calculation_ids': [a['calculation_id']] * 2}).status_code == 422
    assert client.get('/api/v1/calculations/missing').status_code == 404


def test_geometry_and_input_validation(client, parameters):
    parameters['SizeX'] = '<script>alert(1)</script>'
    result = client.post('/api/v1/geometry/preview', json={'parameters': parameters})
    assert result.status_code == 200
    assert '<script>' not in result.json()['svg']
    assert result.json()['dimensions']['rotor_outer_mm'] == 178.8
    parameters['Di2'] = 200
    assert client.post('/api/v1/geometry/preview', json={'parameters': parameters}).status_code == 422


def test_missing_calculation_resources_are_explicit(client, parameters, monkeypatch):
    monkeypatch.setattr(engine, 'KERNEL_SHA256', None)
    assert client.get('/api/v1/status').json()['engine']['status'] == 'unavailable'
    assert client.post('/api/v1/calculate', json={'parameters': parameters}).status_code == 503


def test_input_boundaries_and_local_documentation(client):
    for content in ['{"parameters":{"kW":NaN}}', '{"parameters":', '{"name":"  ","parameters":{}}']:
        route = '/api/v1/projects' if 'name' in content else '/api/v1/calculate'
        assert client.post(route, content=content, headers={'Content-Type': 'application/json'}).status_code == 422
    assert client.post('/api/v1/projects', content=' ' * (513 * 1024)).status_code == 413
    assert client.get('/api/v1/models?limit=201').status_code == 422
    assert client.get('/api/v1/modeling/templates?power_kw=12&poles=3').status_code == 422
    assert client.get('/').status_code == 200
    assert 'cdn.jsdelivr.net' not in client.get('/docs').text
    assert client.get('/api-assets/swagger-ui.css').status_code == 200
    assert '/api/v1/comparisons' in client.get('/openapi.json').json()['paths']


def test_calculation_cannot_claim_unrelated_project_revision(client, parameters, snapshot):
    from dcsj import projects
    from dcsj.storage import ConflictError
    import pytest
    saved = projects.save_project('A', parameters)
    snapshot['inputs'] = {**parameters, 'L': 170}
    with pytest.raises(ConflictError):
        calculations.save(snapshot, project_id=saved['id'], project_revision=1)
    response = client.post('/api/v1/calculate', json={
        'parameters': snapshot['inputs'], 'project_id': saved['id'], 'project_revision': 1})
    assert response.status_code == 409
