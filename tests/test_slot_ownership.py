"""Authenticated slot ownership, distinct copies and revision concurrency."""
import copy
import sqlite3
import pytest
from dcsj import library,slots,storage


def document():
    return {'schema_version':2,'side':'stator','module':'main','drawing':'TEST-1',
            'parameters':{'Q1':36,'b01':2,'hs0':1,'zs1':30,'bs1':4,'bs2':6,'hs12':14},
            'geometry':{'stator_outer_mm':200,'bore_mm':120},'aliases':[]}


def save(client,**kw):
    return client.post('/api/v1/slots/save',json={'name':'Synthetic','document':document(),**kw})


def test_author_is_account_locked_updates_are_versioned_and_copy_is_always_independent(client,monkeypatch):
    monkeypatch.setattr(slots,'resource_entries',lambda:[])
    account=client.get('/api/v1/auth/me').json()
    one=save(client,operation='create').json()
    assert one['author']==account['username'] and one['owner_id']==account['id'] and one['can_edit']
    assert one['revision']==1 and one['operation']=='create'
    changed=document();changed['parameters']['bs1']=4.5
    response=save(client,operation='update',identity=one['id'],expected_revision=1,document=changed)
    assert response.status_code==200,response.text
    two=response.json();assert two['id']==one['id'] and two['revision']==2 and two['author']==one['author']
    assert two['changes']==[{'field':'parameters.bs1','before':4.0,'after':4.5}]
    old=client.get('/api/v1/library/slots/'+one['id'],params={'revision':1}).json()
    assert old['document']['parameters']['bs1']==4 and not old['can_edit'] and old['latest_revision']==2
    unchanged=save(client,operation='update',identity=one['id'],expected_revision=2,document=changed).json()
    assert unchanged['unchanged'] and unchanged['revision']==2
    assert save(client,operation='update',identity=one['id'],expected_revision=1,document=changed).status_code==409
    copied=save(client,operation='copy',copied_from=two['reference'],document=changed).json()
    assert copied['id']!=one['id'] and copied['revision']==1 and copied['owner_id']==one['owner_id']
    assert copied['copied_from']==two['reference'] and library.get('slots',one['id'])['revision']==2
    assert client.get('/api/v1/slots/profiles').json()['total']==2
    history=client.get('/api/v1/library/slots/'+one['id']+'/revisions').json()['items']
    assert history[0]['changes']==two['changes'] and all(v['author']==one['author'] for v in history)


def test_other_account_cannot_update_archive_or_claim_through_legacy_routes(client,monkeypatch):
    monkeypatch.setattr(slots,'resource_entries',lambda:[])
    one=save(client).json();before=library.get('slots',one['id'])
    bob=client.post('/api/v1/auth/login',json={'username':'李工','password':'1234'}).json()
    listing=client.get('/api/v1/slots/profiles').json()['items'][0]
    assert not listing['can_edit'] and not listing['can_archive'] and listing['author']=='张工'
    changed=document();changed['parameters']['bs1']=5
    assert save(client,identity=one['id'],expected_revision=1,document=changed).status_code==403
    assert client.put('/api/v1/library/slots/'+one['id'],json={'name':'Hijack','document':changed,'expected_revision':1}).status_code==403
    for archived in (True,False):
        assert client.post('/api/v1/library/slots/'+one['id']+'/archive',json={'expected_revision':1,'archived':archived}).status_code==403
    assert library.get('slots',one['id'])==before
    mine=save(client,operation='copy',copied_from=one['reference']).json()
    assert mine['id']!=one['id'] and mine['owner_id']==bob['id'] and mine['author']=='李工'
    listing=client.get('/api/v1/slots/profiles').json()
    assert listing['total']==2 and sum(i['can_edit'] for i in listing['items'])==1
    # Older write endpoints also attach the authenticated account.
    legacy=client.post('/api/v1/library/slots',json={'name':'Via legacy API','document':document()}).json()
    assert legacy['owner_id']==bob['id'] and legacy['author']=='李工'
    assert client.post('/api/v1/library/slots/'+mine['id']+'/archive',json={'expected_revision':1,'archived':True}).status_code==200
    assert client.get('/api/v1/slots/profiles').json()['total']==2
    assert client.post('/api/v1/library/slots/'+mine['id']+'/archive',json={'expected_revision':1,'archived':False}).status_code==200


def test_unsigned_history_is_not_assigned_to_the_next_logged_in_account(client,monkeypatch):
    monkeypatch.setattr(slots,'resource_entries',lambda:[])
    old=library.save('slots','Old unsigned',document())
    assert not old['owner_id'] and old['author']==''
    assert save(client,identity=old['id'],expected_revision=1).status_code==403
    assert client.post('/api/v1/library/slots/'+old['id']+'/archive',json={'expected_revision':1,'archived':True}).status_code==403
    mine=save(client,operation='copy',copied_from=old['reference']).json()
    assert mine['author']=='张工' and mine['owner_id']
    assert library.get('slots',old['id'])['owner_id'] is None


def test_invalid_action_and_client_author_cannot_override_server_identity(client):
    one=save(client).json()
    for extra in ({'author':'伪造作者'},{'owner_id':'fake'},{'operation':'copy','identity':one['id']},
                  {'operation':'update'},{'operation':'create','expected_revision':1}):
        assert save(client,**extra).status_code==422
    assert save(client,operation='copy',copied_from='resource-slot:'+'f'*64+':missing').status_code==404
    assert library.get('slots',one['id'])['revision']==1
    client.post('/api/v1/auth/logout')
    assert save(client).status_code==401


def test_schema_11_migration_keeps_unsigned_records_and_immutable_revision_content(tmp_path,monkeypatch):
    path=tmp_path/'old.sqlite';monkeypatch.setenv('DCSJ_PROJECTS_DB',str(path))
    with sqlite3.connect(path) as db:
        db.executescript("""CREATE TABLE library_entries (id TEXT PRIMARY KEY,kind TEXT,name TEXT,revision INTEGER,archived INTEGER,created_at TEXT,updated_at TEXT);
        CREATE TABLE library_revisions (entry_id TEXT,revision INTEGER,name TEXT,document TEXT,created_at TEXT,PRIMARY KEY(entry_id,revision));
        INSERT INTO library_entries VALUES ('legacy','slots','Original',1,0,'before','before');
        INSERT INTO library_revisions VALUES ('legacy',1,'Original','{"side":"stator"}','before');
        PRAGMA user_version=11;""")
    with storage.connect() as db:
        row=db.execute("SELECT * FROM library_entries WHERE id='legacy'").fetchone()
        assert row['owner_id'] is None and row['author']=='' and row['revision']==1
        old=db.execute("SELECT document,author FROM library_revisions WHERE entry_id='legacy'").fetchone()
        assert old['document']=='{"side":"stator"}' and old['author']==''
        assert db.execute('PRAGMA user_version').fetchone()[0]==12
