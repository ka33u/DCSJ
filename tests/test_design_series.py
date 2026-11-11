"""Series-only edits must be atomic, owned, revisioned and independent of resources."""
import json
import pytest
from dcsj import projects, tx, storage


@pytest.fixture(params=['main', 'tx'])
def library(request, client, parameters, monkeypatch):
    module = request.param
    def create(name='Synthetic', owner='张工', series='旧系列', archived=False):
        if module == 'main':
            item = projects.save_project(name, parameters, 'Keep notes', {'kind':'legacy','copied_from':{'id':'parent','revision':3}},
                                         owner, {'rotor':'unavailable-original-reference'}, series)
            if archived:
                projects.set_archived(item['id'], True, 1, owner)
            return item
        identity = name + '-' + owner
        stamp = storage.now()
        with storage.connect() as db:
            db.execute('INSERT INTO tx_designs(id,name,revision,created_at,updated_at,author,series) VALUES(?,?,1,?,?,?,?)',
                       (identity,name,stamp,stamp,owner,series))
            # Intentionally an old/incomplete input and unavailable resource: metadata must still be editable.
            db.execute('INSERT INTO tx_revisions(design_id,revision,parameters,source,created_at,author,series,slot_bindings) '
                       'VALUES(?,1,?,?,?,?,?,?)', (identity,json.dumps({'TYP':name,'P2':3,'legacy_only':7}),
                       '{"kind":"legacy","copied_from":{"id":"parent","revision":3}}',stamp,owner,series,
                       '{"rotor":"unavailable-original-reference"}'))
        return tx.design(identity)
    def get(identity):
        return projects.get_project(identity) if module == 'main' else tx.design(identity)
    def revisions(identity):
        table, key = ('project_revisions', 'project_id') if module == 'main' else ('tx_revisions', 'design_id')
        with storage.connect() as db:
            return [dict(row) for row in db.execute(f'SELECT * FROM {table} WHERE {key}=? ORDER BY revision', (identity,))]
    def fail(*args, **kwargs):
        raise AssertionError('Series changes must not load resources or validate numeric parameters')
    monkeypatch.setattr(tx, 'context', fail)
    monkeypatch.setattr(tx, 'validate', fail)
    return module, '/api/v1/projects' if module == 'main' else '/api/v1/tx/designs', create, get, revisions


def payload(items, series='新系列'):
    return {'series':series,'items':[{'id':item['id'],'expected_revision':item['revision']} for item in items]}


def test_batch_series_preserves_history_and_all_inputs(client, library):
    module, url, create, get, versions = library
    first, second = create('A'), create('B')
    response = client.post(url+'/series', json=payload([first,second], ' 新系列 '))
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['changed_count'] == 2 and data['unchanged_count'] == 0
    for item in (first,second):
        saved = get(item['id']); assert saved['series']=='新系列' and saved['revision']==2
        before, after = versions(item['id'])
        for key in ('parameters','slot_bindings','name','notes','lineage','source'):
            if key in before:
                assert before[key] == after[key]
        assert before['series'] == '旧系列' and after['author'] == '张工'
        diff = projects.revisions(item['id'])[0] if module=='main' else saved
        assert diff['changes']==[{'field':'series','before':'旧系列','after':'新系列'}]
    assert {item['series'] for item in client.get(url).json()['items']} == {'新系列'}
    unchanged = client.post(url+'/series', json=payload([get(first['id']),get(second['id'])])).json()
    assert unchanged['changed_count']==0 and unchanged['unchanged_count']==2
    assert len(versions(first['id']))==2
    cleared = client.post(url+'/series', json=payload([get(first['id'])], '')).json()
    assert cleared['changed_count']==1 and get(first['id'])['series']==''
    with storage.connect() as db:
        events = db.execute("SELECT details FROM audit_events WHERE action LIKE '%.series_changed'").fetchall()
        assert len(events)==3
        assert all(json.loads(row[0])['actor']=='张工' for row in events)


@pytest.mark.parametrize('failure,status', [('foreign',403),('unowned',403),('stale',409),('missing',404)])
def test_batch_failure_rolls_back_every_item(client, library, failure, status):
    module, url, create, get, versions = library
    first = create('A')
    second = create('B',owner='李工' if failure=='foreign' else '' if failure=='unowned' else '张工')
    body = payload([first,second])
    if failure=='stale':body['items'][1]['expected_revision']=2
    if failure=='missing':body['items'][1]['id']='missing'
    response = client.post(url+'/series', json=body)
    assert response.status_code==status, response.text
    for item in (first,second):
        assert get(item['id'])['series']=='旧系列' and len(versions(item['id']))==1
    with storage.connect() as db:
        assert db.execute("SELECT count(*) FROM audit_events WHERE action LIKE '%.series_changed'").fetchone()[0]==0


def test_archived_design_rejects_whole_batch(client, library):
    module,url,create,get,versions=library
    if module=='tx':pytest.skip('TX has no archive state')
    first, second=create('A'),create('B',archived=True)
    assert client.post(url+'/series',json=payload([first,second])).status_code==409
    assert len(versions(first['id']))==1


@pytest.mark.parametrize('invalid', ['duplicate','empty','oversize','control','length','author','boolean_revision','zero_revision','null_series'])
def test_series_request_validation(client, library, invalid):
    module,url,create,get,versions=library
    item=create('A');body=payload([item])
    if invalid=='duplicate':body['items']*=2
    if invalid=='empty':body['items']=[]
    if invalid=='oversize':body['items']=[{'id':str(i),'expected_revision':1} for i in range(201)]
    if invalid=='control':body['series']='bad\nseries'
    if invalid=='length':body['series']='a'*81
    if invalid=='author':body['author']='李工'
    if invalid=='boolean_revision':body['items'][0]['expected_revision']=True
    if invalid=='zero_revision':body['items'][0]['expected_revision']=0
    if invalid=='null_series':body['series']=None
    assert client.post(url+'/series',json=body).status_code==422
    assert len(versions(item['id']))==1
