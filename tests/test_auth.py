"""Session enforcement and server-authoritative attribution on both workbenches."""
import hashlib
import time

import pytest
from fastapi.testclient import TestClient
from dcsj.api import app
from dcsj import auth, storage


@pytest.fixture
def anonymous(tmp_path, monkeypatch):
    monkeypatch.setenv('DCSJ_PROJECTS_DB', str(tmp_path/'auth.sqlite'))
    with TestClient(app) as client:
        yield client


def sign_in(client, username='测试作者', password='1234'):
    return client.post('/api/v1/auth/login', json={'username':username,'password':password},
                       headers={'X-DCSJ-Request':'1'})


def test_anonymous_pages_apis_reports_and_resource_import_are_protected(anonymous):
    for path in ('/', '/tx', '/slots', '/reports/old', '/tx/reports/old', '/docs'):
        r=anonymous.get(path,follow_redirects=False)
        assert r.status_code==303 and r.headers['location'].startswith('/login?next=')
    for path in ('/api/v1/status','/api/v1/models','/api/v1/tx/models','/api/v1/projects','/api/v1/slots','/openapi.json'):
        assert anonymous.get(path).status_code==401
    assert anonymous.post('/api/v1/resources/import',json={},headers={'X-DCSJ-Request':'1'}).status_code==401
    assert anonymous.get('/healthz').json()=={'status':'ok'}
    assert '用户名' in anonymous.get('/login').text


def test_first_login_hashes_password_session_and_canonical_identity(anonymous):
    assert sign_in(anonymous,password='incorrect').status_code==401
    with storage.connect() as db:
        assert db.execute('SELECT count(*) FROM auth_users').fetchone()[0]==0
    r=sign_in(anonymous,'  张工Ａ  ')
    assert r.status_code==200 and r.json()['username']=='张工A'
    cookie=r.headers['set-cookie'];assert 'HttpOnly' in cookie and 'SameSite=strict' in cookie
    token=anonymous.cookies.get(auth.COOKIE)
    with storage.connect() as db:
        user=db.execute('SELECT * FROM auth_users').fetchone()
        assert user['password_hash']!='1234' and auth.password_matches('1234',user['password_hash'])
        assert not auth.password_matches('other',user['password_hash'])
        row=db.execute('SELECT * FROM auth_sessions').fetchone()
        assert row['token_hash']==hashlib.sha256(token.encode()).hexdigest() and row['token_hash']!=token
    assert anonymous.get('/api/v1/auth/me').json()['username']=='张工A'
    assert sign_in(anonymous,'张工a').json()['username']=='张工A'
    assert auth.session(token) is None  # Logging in rotates the previous session.
    with storage.connect() as db:
        assert db.execute('SELECT count(*) FROM auth_users').fetchone()[0]==1


def test_wrong_password_and_invalid_username_do_not_create_accounts(anonymous):
    for name in ('   ','a\nname','x'*121):
        assert sign_in(anonymous,name).status_code==422
    assert sign_in(anonymous).status_code==200
    assert sign_in(anonymous,password='wrong').status_code==401


def test_cross_origin_and_form_login_or_writes_are_rejected(anonymous):
    url='/api/v1/auth/login';body={'username':'test','password':'1234'}
    assert anonymous.post(url,json=body).status_code==403
    assert anonymous.post(url,json=body,headers={'X-DCSJ-Request':'1','Origin':'https://elsewhere.invalid'}).status_code==403
    assert anonymous.post(url,data=body).status_code==403
    assert sign_in(anonymous).status_code==200
    assert anonymous.post('/api/v1/auth/logout').status_code==403
    assert anonymous.post('/api/v1/auth/logout',headers={'X-DCSJ-Request':'1','Origin':'null'}).status_code==403
    assert anonymous.get('/api/v1/auth/me').status_code==200


def test_logout_expiry_and_forged_tokens_fail_closed(anonymous):
    sign_in(anonymous);token=anonymous.cookies.get(auth.COOKIE)
    assert anonymous.post('/api/v1/auth/logout',headers={'X-DCSJ-Request':'1'}).status_code==200
    assert auth.session(token) is None and anonymous.get('/api/v1/auth/me').status_code==401
    sign_in(anonymous)
    with storage.connect() as db:db.execute('UPDATE auth_sessions SET expires_at=?',(time.time()-1,))
    assert anonymous.get('/api/v1/auth/me').status_code==401
    anonymous.cookies.set(auth.COOKIE,'invented-token')
    assert anonymous.get('/api/v1/auth/me').status_code==401


def test_https_cookie_secure_and_private_responses_not_cached(tmp_path,monkeypatch):
    monkeypatch.setenv('DCSJ_PROJECTS_DB',str(tmp_path/'https.sqlite'))
    with TestClient(app,base_url='https://testserver') as client:
        assert 'Secure' in sign_in(client).headers['set-cookie']
        assert client.get('/api/v1/auth/me').headers['cache-control']=='no-store'
        assert 'requestInterceptor' in client.get('/docs').text


def test_failed_login_is_rate_limited(anonymous):
    for _ in range(10):assert sign_in(anonymous,password='wrong').status_code==401
    assert sign_in(anonymous).status_code==429
    with storage.connect() as db:db.execute('UPDATE auth_attempts SET expires_at=?',(time.time()-1,))
    assert sign_in(anonymous).status_code==200


def test_auth_survives_reopening_database_without_changing_history(client,parameters):
    from dcsj import projects
    old=projects.save_project('Old',parameters,author='旧署名')
    cookie=client.cookies.get(auth.COOKIE)
    storage._initialized.clear()
    assert client.get('/api/v1/auth/me').json()['username']=='张工'
    assert auth.session(cookie)['username']=='张工'
    updated=client.put('/api/v1/projects/'+old['id'],json={'name':'Old','parameters':parameters,'expected_revision':1})
    assert updated.json()['author']=='张工'
    assert projects.get_revision(old['id'],1)['author']=='旧署名'


def test_tampered_author_is_rejected_and_cannot_clear_signature(client,parameters):
    for supplied in ('其他人','', '张工\n其他人'):
        assert client.post('/api/v1/projects',json={'name':'test','parameters':parameters,'author':supplied}).status_code==403
    created=client.post('/api/v1/projects',json={'name':'test','parameters':parameters}).json()
    assert created['author']=='张工'
    for supplied in ('其他人',''):
        assert client.put('/api/v1/projects/'+created['id'],json={'name':'test','parameters':parameters,'expected_revision':1,'author':supplied}).status_code==403
    assert client.get('/api/v1/projects/'+created['id']).json()['revision']==1
