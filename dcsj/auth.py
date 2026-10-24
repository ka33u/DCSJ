"""Local accounts and opaque, revocable sessions. Authors come from the session."""
import hashlib
import hmac
import secrets
import time
import unicodedata
import uuid
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.concurrency import run_in_threadpool

from .storage import ROOT, connect, now, audit

COOKIE = 'dcsj_session'
SESSION_SECONDS = 12 * 60 * 60
PASSWORD_ITERATIONS = 600_000
PUBLIC = {'/login', '/login.js', '/auth.js', '/auth.css', '/healthz', '/api/v1/auth/login',
          '/app.js', '/styles.css', '/tx.js', '/tx.css', '/slots.js', '/slots.css', '/slot-picker.js'}
router = APIRouter(tags=['登录与账号'])


def normalize_username(value):
    value = unicodedata.normalize('NFKC', value).strip()
    if not value or len(value) > 120 or any(unicodedata.category(c).startswith('C') for c in value):
        raise ValueError('用户名须为 1–120 个可显示字符')
    return value


class LoginInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=256)

    @field_validator('username')
    @classmethod
    def username_value(cls, value):
        return normalize_username(value)


def password_hash(password):
    salt = secrets.token_hex(16)
    value = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), PASSWORD_ITERATIONS)
    return f'pbkdf2_sha256${PASSWORD_ITERATIONS}${salt}${value.hex()}'


def password_matches(password, encoded):
    algorithm, iterations, salt, expected = encoded.split('$')
    if algorithm != 'pbkdf2_sha256':
        return False
    value = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), int(iterations))
    return hmac.compare_digest(value.hex(), expected)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def session(token):
    if not token or len(token) > 200:
        return None
    with connect() as db:
        row = db.execute('SELECT u.id,u.username,s.expires_at FROM auth_sessions s JOIN auth_users u '
                         'ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>?',
                         (digest(token), time.time())).fetchone()
        return dict(row) if row else None


def login(username, password, peer, previous_token=None):
    key, stamp = username.casefold(), time.time()
    attempt_keys = [(digest('ip:'+peer), 50), (digest('user:'+key), 10)]
    failure = False
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('DELETE FROM auth_attempts WHERE expires_at<=?', (stamp,))
        db.execute('DELETE FROM auth_sessions WHERE expires_at<=?', (stamp,))
        for attempt_key, limit in attempt_keys:
            row = db.execute('SELECT count FROM auth_attempts WHERE key=?', (attempt_key,)).fetchone()
            if row and row['count'] >= limit:
                raise HTTPException(429, '登录尝试过多，请 15 分钟后重试', headers={'Retry-After':'900'})
        user = db.execute('SELECT * FROM auth_users WHERE username_key=?', (key,)).fetchone()
        valid = password_matches(password, user['password_hash']) if user else hmac.compare_digest(password.encode(), b'1234')
        if not valid:
            for attempt_key, _ in attempt_keys:
                db.execute('INSERT INTO auth_attempts(key,count,expires_at) VALUES (?,1,?) '
                           'ON CONFLICT(key) DO UPDATE SET count=count+1', (attempt_key, stamp+900))
            failure = True
        else:
            if user is None:
                identity = str(uuid.uuid4())
                db.execute('INSERT INTO auth_users VALUES (?,?,?,?,?)',
                           (identity, username, key, password_hash(password), now()))
                audit(db, 'auth.user.created', identity, {'username':username})
                user = {'id':identity, 'username':username}
            db.execute('DELETE FROM auth_attempts WHERE key=?', (attempt_keys[1][0],))
            if previous_token:
                db.execute('DELETE FROM auth_sessions WHERE token_hash=?', (digest(previous_token),))
            token = secrets.token_urlsafe(32)
            expires = stamp + SESSION_SECONDS
            db.execute('INSERT INTO auth_sessions VALUES (?,?,?)', (digest(token),user['id'],expires))
            result = {'id':user['id'], 'username':user['username'], 'expires_at':expires}
    if failure:
        raise HTTPException(401, '用户名或密码错误；新账号的初始密码为 1234')
    return result, token


def author(request, supplied=None):
    username = request.state.user['username']
    if supplied is not None and supplied.strip() != username:
        raise HTTPException(403, '作者署名由当前登录用户确定，不能修改；请重新登录后重试')
    return username


async def protect(request, call_next):
    path = request.url.path
    if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
        # Non-simple header plus same-origin validation protects cookie-based writes,
        # including login. No cross-origin API or credentialed CORS is enabled.
        origin = request.headers.get('origin')
        if request.headers.get('x-dcsj-request') != '1' or (origin and origin != str(request.base_url).rstrip('/')):
            return JSONResponse({'detail':'请求来源校验失败，请从本站页面或带 X-DCSJ-Request: 1 的 API 客户端操作'}, status_code=403)
    if path not in PUBLIC:
        user = await run_in_threadpool(session, request.cookies.get(COOKIE))
        if user is None:
            if path.startswith('/api/') or path == '/openapi.json':
                return JSONResponse({'detail':'请先登录；登录失效时可在新窗口登录后重试', 'code':'login_required'},
                                    status_code=401, headers={'Cache-Control':'no-store'})
            return RedirectResponse('/login?next='+quote(path+('?' + request.url.query if request.url.query else ''), safe=''),
                                    status_code=303, headers={'Cache-Control':'no-store'})
        request.state.user = user
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Frame-Options'] = 'DENY'
    return response


@router.get('/healthz', include_in_schema=False)
def health():
    return {'status':'ok'}


@router.get('/login', include_in_schema=False)
def page():
    return FileResponse(ROOT/'frontend/login.html')


@router.get('/login.js', include_in_schema=False)
def login_script():
    return FileResponse(ROOT/'frontend/login.js', media_type='application/javascript')


@router.get('/auth.js', include_in_schema=False)
def script():
    return FileResponse(ROOT/'frontend/auth.js', media_type='application/javascript')


@router.get('/auth.css', include_in_schema=False)
def styles():
    return FileResponse(ROOT/'frontend/auth.css', media_type='text/css')


@router.post('/api/v1/auth/login')
def sign_in(body:LoginInput, request:Request, response:Response):
    user, token = login(body.username, body.password, request.client.host if request.client else 'unknown', request.cookies.get(COOKIE))
    response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, httponly=True,
                        secure=request.url.scheme == 'https', samesite='strict', path='/')
    return user


@router.get('/api/v1/auth/me')
def current_user(request:Request):
    return request.state.user


@router.post('/api/v1/auth/logout')
def sign_out(request:Request, response:Response):
    with connect() as db:
        db.execute('DELETE FROM auth_sessions WHERE token_hash=?', (digest(request.cookies[COOKIE]),))
    response.delete_cookie(COOKIE, path='/', secure=request.url.scheme == 'https', httponly=True, samesite='strict')
    return {'logged_out':True}
