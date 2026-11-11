"""Search the whole metadata library, preserving ownership and revision boundaries."""
import pytest
from dcsj import storage


@pytest.fixture(params=['main', 'tx'])
def catalog(request, client):
    module = request.param
    table, revisions, key = ('projects', 'project_revisions', 'project_id') if module == 'main' else ('tx_designs', 'tx_revisions', 'design_id')
    with storage.connect() as db:
        for i in range(605):
            identity = f'design-{i:04}'
            name = 'YE3 原方案 100%' if i == 0 else f'方案 {i}'
            series = '试制Ａ' if i == 0 else '普通系列'
            owner = '李工' if i == 1 else '张工'
            stamp = f'2026-01-01T00:{i:04}:00Z'
            columns = 'id,name,revision,created_at,updated_at,author,series'
            values = [identity, name, 1, stamp, stamp, '张工', series]
            if module == 'main':
                columns += ',parameters'
                values += ['{}']
            db.execute(f'INSERT INTO {table}({columns}) VALUES({",".join("?" for _ in values)})', values)
            columns = f'{key},revision,parameters,created_at,author,series'
            values = [identity, 1, '{}', stamp, owner, series]
            if module == 'main':
                columns += ',name,notes'
                values += [name, '']
            else:
                columns += ',source'
                values += ['{}']
            db.execute(f'INSERT INTO {revisions}({columns}) VALUES({",".join("?" for _ in values)})', values)
        if module == 'main':
            db.execute("UPDATE projects SET archived=1 WHERE id='design-0002'")
    return module, '/api/v1/projects' if module == 'main' else '/api/v1/tx/designs'


def test_search_precedes_limit_and_normalizes_words(client, catalog):
    module, url = catalog
    initial = client.get(url).json()
    assert len(initial['items']) == 500
    assert 'design-0000' not in [item['id'] for item in initial['items']]
    result = client.get(url, params={'q':' ｙｅ３　试制a 张工 100% ', 'limit':100}).json()
    assert result['total'] == 1
    assert result['items'][0]['id'] == 'design-0000'
    assert set(result['series']) == {'普通系列', '试制Ａ'}
    assert client.get(url, params={'q':"%' OR 1=1 --"}).json()['total'] == 0
    assert client.get(url, params={'q':'design-0000'}).json()['items'][0]['name'] == 'YE3 原方案 100%'


def test_pagination_filters_and_original_owner(client, catalog):
    module, url = catalog
    first = client.get(url, params={'limit':100}).json()
    second = client.get(url, params={'limit':100,'offset':100}).json()
    assert len(first['items']) == len(second['items']) == 100
    assert not {item['id'] for item in first['items']} & {item['id'] for item in second['items']}
    assert client.get(url, params={'q':'design-0001','owned_only':True}).json()['total'] == 0
    assert client.get(url, params={'q':'design-0001'}).json()['items'][0]['owner'] == '李工'
    assert client.get(url, params={'series':'试制Ａ'}).json()['items'][0]['id'] == 'design-0000'
    result = client.get(url, params={'q':'design-0000','offset':10000,'limit':100}).json()
    assert result['offset'] == 0 and result['total'] == 1
    assert 'parameters' not in result['items'][0]
    if module == 'main':
        assert client.get(url, params={'q':'design-0002'}).json()['total'] == 0
        assert client.get(url, params={'q':'design-0002','include_archived':True}).json()['total'] == 1


def test_search_bounds(client, catalog):
    _, url = catalog
    for params in ({'q':'x'*121}, {'series':'x'*81}, {'limit':0}, {'limit':501}, {'offset':-1}):
        assert client.get(url, params=params).status_code == 422
