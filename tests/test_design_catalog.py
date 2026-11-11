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


def test_rating_columns_read_current_saved_version_and_keep_raw_voltage(client, catalog):
    import json
    module, url = catalog
    table, revisions, key = ('projects','project_revisions','project_id') if module == 'main' else ('tx_designs','tx_revisions','design_id')
    old = {'kW':1.1,'Ufai':220,'f':50,'Jiefa':'Y'} if module == 'main' else {'P2':1.1,'U1':380,'HZ':50}
    current = {'kW':.125,'Ufai':230.5,'f':60,'Jiefa':'Y'} if module == 'main' else {'P2':.125,'U1':415.5,'HZ':60}
    with storage.connect() as db:
        db.execute(f'UPDATE {revisions} SET parameters=? WHERE {key}=?', (json.dumps(old),'design-0000'))
        columns = f'{key},revision,parameters,created_at,author,series'
        values = ['design-0000',2,json.dumps(current),'2026-01-02T00:00:00Z','张工','试制Ａ']
        if module == 'main':
            columns += ',name,notes';values += ['保存的新方案','']
            db.execute('UPDATE projects SET parameters=? WHERE id=?',(json.dumps(current),'design-0000'))
        else:
            columns += ',source';values += ['{}']
        db.execute(f'INSERT INTO {revisions}({columns}) VALUES({",".join("?" for _ in values)})',values)
        db.execute(f'UPDATE {table} SET revision=2 WHERE id=?',('design-0000',))
    row=client.get(url,params={'q':'design-0000'}).json()['items'][0]
    assert row['revision']==2
    assert row['power_kw']==.125
    assert row['voltage_v']==(230.5 if module=='main' else 415.5)
    assert row['frequency_hz']==60
    assert 'parameters' not in row and 'rating_parameters' not in row
    with storage.connect() as db:
        before=json.loads(db.execute(f'SELECT parameters FROM {revisions} WHERE {key}=? AND revision=1',('design-0000',)).fetchone()[0])
    assert before==old


def test_rating_columns_preserve_zero_and_mark_absent_or_invalid_values_unknown(client,catalog):
    import json
    module,url=catalog
    values={'kW':0,'Ufai':None,'f':'bad'} if module=='main' else {'P2':0,'U1':None,'HZ':'bad'}
    with storage.connect() as db:
        if module=='main':db.execute('UPDATE projects SET parameters=? WHERE id=?',(json.dumps(values),'design-0000'))
        else:db.execute('UPDATE tx_revisions SET parameters=? WHERE design_id=?',(json.dumps(values),'design-0000'))
    row=client.get(url,params={'q':'design-0000'}).json()['items'][0]
    assert row['power_kw']==0 and row['voltage_v'] is None and row['frequency_hz'] is None
    missing=client.get(url,params={'q':'design-0003'}).json()['items'][0]
    assert all(missing[field] is None for field in ['power_kw','voltage_v','frequency_hz'])
