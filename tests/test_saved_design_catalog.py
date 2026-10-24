"""Saved designs remain versioned user data and are discoverable by attribution."""
import json
import sqlite3
from dcsj import catalog, projects, storage


def create(client, parameters, name='个人方案', author='张工'):
    assert client.post('/api/v1/auth/login', json={'username':author.strip(),'password':'1234'}).status_code == 200
    response = client.post('/api/v1/projects', json={'name': name, 'author': author, 'parameters': parameters})
    assert response.status_code == 201, response.text
    return response.json()


def test_author_revisions_legacy_updates_and_catalog_loading(client, parameters):
    d = create(client, parameters, author='  张工  ')
    assert d['author'] == '张工' and 'author' not in d['parameters']
    # The design name and motor model are separately searchable.
    for q in ('张工', '个人方案', 'Synthetic test geometry'):
        result = client.get('/api/v1/models', params={'source': 'saved', 'q': q}).json()
        assert result['total'] == 1
        row = result['items'][0]
        assert row['project_id'] == d['id'] and row['author'] == '张工'
    reference = row['id']
    loaded = client.get('/api/v1/models/' + reference).json()
    assert loaded['project']['id'] == d['id'] and loaded['project']['revision'] == 1
    assert loaded['parameters'] == parameters and loaded['project']['lineage'] == {}
    # Omitted author is filled from the session; a new user signs only the new revision.
    body = {'name': '重新命名', 'parameters': parameters, 'expected_revision': 1}
    updated = client.put('/api/v1/projects/' + d['id'], json=body).json()
    assert updated['author'] == '张工'
    assert client.post('/api/v1/auth/login',json={'username':'李工','password':'1234'}).status_code == 200
    body.update(expected_revision=2, author='李工')
    assert client.put('/api/v1/projects/' + d['id'], json=body).json()['author'] == '李工'
    assert client.get('/api/v1/models/' + reference).json()['project']['author'] == '张工'
    versions = client.get(f"/api/v1/projects/{d['id']}/revisions").json()['items']
    assert [v['author'] for v in versions] == ['李工', '张工', '张工']
    body.update(expected_revision=3, author='')
    assert client.put('/api/v1/projects/' + d['id'], json=body).status_code == 403
    body.update(expected_revision=2, author='李工')
    assert client.put('/api/v1/projects/' + d['id'], json=body).status_code == 409
    assert client.post('/api/v1/projects', json={**body, 'author': 'x' * 121}).status_code == 422


def test_saved_search_duplicates_literal_queries_filters_pagination_and_archive(client, parameters, monkeypatch):
    first = create(client, {**parameters, 'SizeX': 'YE3-100L2-4'}, '同名', 'A%_工')
    second = create(client, {**parameters, 'SizeX': 'YE3-100L2-4'}, '同名', 'B')
    assert first['id'] != second['id']
    def search(**kwargs):
        r = client.get('/api/v1/models', params={'source': 'saved', **kwargs})
        assert r.status_code == 200, r.text
        return r.json()
    assert search(q='%_')['total'] == 1
    assert search(q='A%工')['total'] == 0
    assert search(q='同名')['total'] == 2
    assert search(poles=4, frequency_hz=50, voltage_v=230, power_min=12, power_max=12, frame='100')['total'] == 2
    for filters in ({'poles': 6}, {'frequency_hz': 60}, {'power_max': 5}, {'frame': '180'}, {'series': 'Y'}, {'source_table': 'legacy'}):
        assert search(**filters)['total'] == 0
    pages = [search(limit=1, offset=i)['items'][0]['id'] for i in (0, 1)]
    assert len(set(pages)) == 2
    # Union pagination crosses from saved records into originals without omission.
    originals = [{'id': 'original-a'}, {'id': 'original-b'}]
    monkeypatch.setattr(catalog, '_original_models', lambda q, series, limit, offset, history, **kw:
                        {'items': originals[offset:offset+limit], 'total': len(originals)})
    union = client.get('/api/v1/models?source=all&limit=3&offset=1').json()
    assert union['total'] == 4 and [r['id'] for r in union['items']] == [pages[1], 'original-a', 'original-b']
    assert client.get('/api/v1/models').json()['items'] == originals  # legacy default retained
    client.post(f"/api/v1/projects/{first['id']}/archive", json={'archived': True, 'expected_revision': 1})
    assert search(q='同名')['total'] == 1
    client.post(f"/api/v1/projects/{first['id']}/archive", json={'archived': False, 'expected_revision': 1})
    assert search(q='同名')['total'] == 2
    assert client.get('/api/v1/models/saved-design:broken').status_code == 404


def test_schema_six_migration_retains_old_designs_and_tx_versions(tmp_path, parameters):
    path = tmp_path / 'old.sqlite'
    with sqlite3.connect(path) as db:
        db.executescript('''
            PRAGMA user_version=6;
            CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT,parameters TEXT,updated_at TEXT);
            CREATE TABLE tx_designs(id TEXT PRIMARY KEY,name TEXT,revision INTEGER,created_at TEXT,updated_at TEXT);
            CREATE TABLE tx_revisions(design_id TEXT,revision INTEGER,parameters TEXT,source TEXT,created_at TEXT,PRIMARY KEY(design_id,revision));
        ''')
        db.execute('INSERT INTO projects VALUES(?,?,?,?)', ('old', '旧设计', json.dumps(parameters), '2020'))
        db.execute('INSERT INTO tx_designs VALUES(?,?,?,?,?)', ('tx', '旧TX', 1, '2020', '2020'))
        db.execute('INSERT INTO tx_revisions VALUES(?,?,?,?,?)', ('tx', 1, '{"TYP":"旧TX"}', '{}', '2020'))
    with storage.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == storage.SCHEMA_VERSION
        for table in ('projects', 'project_revisions', 'tx_designs', 'tx_revisions'):
            assert db.execute(f'SELECT author FROM {table}').fetchone()[0] == ''
            assert db.execute(f'SELECT series FROM {table}').fetchone()[0] == ''
        assert json.loads(db.execute('SELECT parameters FROM projects').fetchone()[0]) == parameters
        assert db.execute('SELECT name FROM project_revisions').fetchone()[0] == '旧设计'


def test_series_is_versioned_searchable_metadata_and_legacy_updates_preserve_it(client,parameters):
    body={'name':'方案一','parameters':parameters,'series':'  独立系列%_  '}
    response=client.post('/api/v1/projects',json=body)
    assert response.status_code==201
    item=response.json();assert item['series']=='独立系列%_' and 'series' not in item['parameters']
    ref=f"saved-design:{item['id']}@1"
    assert '独立系列%_' in client.get('/api/v1/series').json()['items']
    for filters in ({'series':'独立系列%_'},{'q':'独立系列%_'},{'q':'%_'}):
        result=client.get('/api/v1/models',params={'source':'saved',**filters}).json()
        assert result['total']==1 and result['items'][0]['series']=='独立系列%_'
    assert client.get('/api/v1/models',params={'source':'saved','series':'独立系列'}).json()['total']==0
    update={'name':'方案二','parameters':parameters,'expected_revision':1}
    assert client.put('/api/v1/projects/'+item['id'],json=update).json()['series']=='独立系列%_'
    update.update(expected_revision=2,series='新系列')
    assert client.put('/api/v1/projects/'+item['id'],json=update).json()['series']=='新系列'
    assert client.get('/api/v1/models/'+ref).json()['series']=='独立系列%_'
    history=client.get(f"/api/v1/projects/{item['id']}/revisions").json()['items']
    assert [x['series'] for x in history]==['新系列','独立系列%_','独立系列%_']
    update.update(expected_revision=3,series='')
    assert client.put('/api/v1/projects/'+item['id'],json=update).json()['series']==''
    assert client.get('/api/v1/models',params={'source':'saved','series':'新系列'}).json()['total']==0
    assert client.get('/api/v1/projects').json()['items'][0]['series']==''


def test_series_input_validation_is_separate_from_numeric_parameters(client,parameters):
    for value in ('x'*81,'line\nline',[],42):
        assert client.post('/api/v1/projects',json={'name':'Test','parameters':parameters,'series':value}).status_code==422
    assert client.post('/api/v1/projects',json={'name':'Test','parameters':parameters}).json()['series']==''
