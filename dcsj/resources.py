"""Versioned private resource packages, isolated from user-authored design data."""
from contextlib import closing, contextmanager
from concurrent.futures import ThreadPoolExecutor
from functools import wraps
import base64
import binascii
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import threading
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener
import zipfile

from .kernel_layout import INPUT_MAP, OUTPUT_MAP
from . import __version__
from .storage import ConflictError, audit, connect, dumps

ROOT = Path(__file__).resolve().parents[1]
FILES = {'catalog.sqlite': 'data/catalog.sqlite',
         'material_kernel_tables.json': 'data/material_kernel_tables.json',
         'numeric_kernel.json.gz': 'dcsj/generated/numeric_kernel.json.gz',
         'example.json': 'data/example.json'}
TX_FILES = {'tx_catalog.json': 'data/tx_catalog.json',
            'tx_kernel.json.gz': 'dcsj/generated/tx_kernel.json.gz'}
MAX_ARCHIVE = 64 * 1024 * 1024
MAX_EXPANDED = 512 * 1024 * 1024
ABI = hashlib.sha256(json.dumps({'input': INPUT_MAP, 'output': OUTPUT_MAP}, sort_keys=True).encode()).hexdigest()
_slot = threading.Lock()


class ResourceError(ValueError):
    pass


def checked(operation):
    @wraps(operation)
    def run(*args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except (ResourceError, ConflictError):
            raise
        except (OSError, EOFError, zipfile.BadZipFile, sqlite3.Error, binascii.Error,
                json.JSONDecodeError, KeyError, TypeError, AttributeError):
            raise ResourceError('资源文件缺失、损坏或结构无效；当前版本未被替换') from None
    return run


def directory():
    return Path(os.getenv('DCSJ_DATA_DIR', str(ROOT / '.runtime'))) / 'resources'


def digest(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def _json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _identity(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{64}', value):
        raise ResourceError('资源包标识无效')
    return value


@checked
def current():
    pointer = directory() / 'active.json'
    if not pointer.is_file():
        return None
    identity = _identity(_json(pointer)['package_id'])
    manifest = _json(directory() / identity / 'manifest.json')
    return {**manifest, 'package_id': identity}


def asset(name, package=None):
    """A supplied package pins a request to one immutable directory."""
    if name not in {**FILES, **TX_FILES}:
        raise ResourceError('未知资源类型')
    package = current() if package is None else package
    return directory() / _identity(package['package_id']) / name if package else ROOT / {**FILES, **TX_FILES}[name]


def installed():
    root = directory()
    items = []
    if root.exists():
        for path in sorted(root.iterdir()):
            if re.fullmatch(r'[a-f0-9]{64}', path.name) and (path / 'manifest.json').is_file():
                manifest = _json(path / 'manifest.json')
                items.append({'package_id': path.name, 'version': manifest['version'], 'counts': manifest['counts']})
    return items


def status():
    active = current()
    return {'active': active, 'installed': installed(),
            'repository': os.getenv('DCSJ_RESOURCE_REPOSITORY', 'ka33u/DCSJ-SYSJ'),
            'ref': os.getenv('DCSJ_RESOURCE_REF', 'main'),
            'remote_configured': bool(os.getenv('DCSJ_RESOURCE_TOKEN') or os.getenv('DCSJ_RESOURCE_TOKEN_FILE')),
            'user_data_policy': '基础库只读；用户型号、材料与项目独立存储，同名并存，更新不覆盖用户数据。',
            'catalog_override': bool(os.getenv('DCSJ_CATALOG_PATH'))}


def _schema(db):
    return [(kind, name, re.sub(r'\s+', ' ', sql.strip()).lower()) for kind, name, sql in db.execute(
        "SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")]


@checked
def validate_files(root, manifest):
    if manifest.get('format') != 'dcsj-private-resources' or manifest.get('format_version') not in (1, 2) or manifest.get('abi') != ABI:
        raise ResourceError('资源格式或计算接口版本不兼容，请升级应用或使用兼容资源包')
    minimum = manifest.get('minimum_application', '')
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', minimum):
        raise ResourceError('资源包最低应用版本无效')
    if tuple(map(int, minimum.split('.'))) > tuple(map(int, __version__.split('.'))):
        raise ResourceError('此资源包需要应用版本 ' + minimum + ' 或更新版本')
    if not isinstance(manifest.get('version'), str) or not re.fullmatch(r'[A-Za-z0-9._-]{1,80}', manifest['version']):
        raise ResourceError('资源版本无效')
    expected_files = {**FILES, **TX_FILES} if manifest['format_version'] == 2 else FILES
    if set(manifest.get('files', {})) != set(expected_files):
        raise ResourceError('资源包文件清单不完整或含额外文件')
    total = 0
    for name, info in manifest['files'].items():
        if not isinstance(info.get('size'), int) or not 0 < info['size'] <= MAX_EXPANDED:
            raise ResourceError('资源文件大小无效')
        total += info['size']
        path = root / name
        if not path.is_file() or path.stat().st_size != info['size'] or digest(path) != info.get('sha256'):
            raise ResourceError('资源校验失败：' + name)
    if total > MAX_EXPANDED:
        raise ResourceError('资源解压体积超过限制')
    with closing(sqlite3.connect((root / 'catalog.sqlite').resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('PRAGMA trusted_schema=OFF')
        with closing(sqlite3.connect(':memory:')) as reference:
            reference.executescript((ROOT / 'data/schema.sql').read_text(encoding='utf-8'))
            if _schema(db) != _schema(reference):
                raise ResourceError('型号数据库结构不兼容')
        if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok' or db.execute('PRAGMA foreign_key_check').fetchone():
            raise ResourceError('型号数据库完整性检查失败')
        counts = {'model_versions': db.execute('SELECT count(*) FROM models').fetchone()[0],
                  'models': db.execute('SELECT count(*) FROM models WHERE is_primary=1').fetchone()[0],
                  'materials': db.execute('SELECT count(*) FROM materials WHERE is_primary=1').fetchone()[0]}
    if manifest['format_version'] == 2:
        from .tx import validate_resources
        counts['tx_model_versions'] = validate_resources(root, manifest['files'])
    if counts != manifest.get('counts'):
        raise ResourceError('资源记录数量与清单不一致')
    materials = _json(root / 'material_kernel_tables.json')
    if materials.get('format_version') != 1 or not isinstance(materials.get('materials'), list) or not materials['materials']:
        raise ResourceError('材料计算表格式不兼容')
    names = set()
    for item in materials['materials']:
        if not isinstance(item.get('name'), str) or not item['name'] or item['name'] in names:
            raise ResourceError('材料名称缺失或重复')
        names.add(item['name'])
        if item.get('slot') not in range(1, 6) or item.get('kind') not in {'builtin', 'mdb_custom', 'builtin_custom_slot'}:
            raise ResourceError('材料计算表槽位不兼容')
        for key, length in [('const_values', 693), ('H_values', 49), ('P_values', 42)]:
            values = item.get(key)
            if not isinstance(values, list) or len(values) != length or not all(
                    isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values):
                raise ResourceError('材料计算表数值无效')
    with gzip.open(root / 'numeric_kernel.json.gz', 'rb') as incoming:
        content = incoming.read(32 * 1024 * 1024 + 1)
    if len(content) > 32 * 1024 * 1024:
        raise ResourceError('计算资源解压体积超过限制')
    kernel = json.loads(content)
    if kernel.get('format_version') != 1 or not isinstance(kernel.get('instructions'), list) or not kernel['instructions']:
        raise ResourceError('计算资源格式不兼容')
    for key in ('image_size', 'stack_size'):
        if not isinstance(kernel.get(key), int) or not 0 < kernel[key] <= 16 * 1024 * 1024:
            raise ResourceError('计算资源内存范围无效')
    for key in ('image_base', 'stack_base', 'initial_ebp', 'entry'):
        if type(kernel.get(key)) is not int or not 0 <= kernel[key] < 2 ** 32:
            raise ResourceError('计算资源地址无效')
    if len(kernel['instructions']) > 200000 or any(
            not isinstance(row, list) or len(row) != 4 or type(row[0]) is not int or
            type(row[1]) is not int or not 0 <= row[1] < 2 ** 32 or not isinstance(row[2], str) or
            not isinstance(row[3], list) for row in kernel['instructions']):
        raise ResourceError('计算指令结构无效')
    addresses = {row[0] for row in kernel['instructions']}
    if len(addresses) != len(kernel['instructions']) or kernel['entry'] not in addresses:
        raise ResourceError('计算入口缺失或指令地址重复')
    if not isinstance(kernel.get('functions'), list) or any(
            type(item.get('address')) is not int or not isinstance(item.get('name'), str) for item in kernel['functions']):
        raise ResourceError('计算函数表无效')
    if not isinstance(kernel.get('segments'), list):
        raise ResourceError('计算初始数据结构无效')
    for segment in kernel['segments']:
        address = segment['address']
        initial = base64.b64decode(segment['data_base64'], validate=True)
        if type(address) is not int or not any(
                kernel[area + '_base'] <= address and address + len(initial) <= kernel[area + '_base'] + kernel[area + '_size']
                for area in ('image', 'stack')):
            raise ResourceError('计算初始数据超出内存范围')
    from .parameters import validate
    validate(_json(root / 'example.json')['parameters'])
    return counts


@contextmanager
def import_slot():
    if not _slot.acquire(blocking=False):
        raise ConflictError('已有资源导入正在进行，请稍后重试')
    try:
        yield
    finally:
        _slot.release()


@checked
def install(archive, expected_sha256=None):
    """Stage an immutable package; activation is a separate atomic operation."""
    archive = Path(archive)
    if not 0 < archive.stat().st_size <= MAX_ARCHIVE:
        raise ResourceError('资源压缩包大小超过 64 MiB 或为空')
    identity = digest(archive)
    if expected_sha256 and identity != expected_sha256:
        raise ResourceError('下载资源包 SHA-256 校验失败')
    root = directory(); root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='staging-', dir=root) as temp:
        target = Path(temp)
        with zipfile.ZipFile(archive) as bundle:
            entries = bundle.infolist()
            names = {item.filename for item in entries}
            if len(names) != len(entries) or names not in ({*FILES, 'manifest.json'}, {*FILES, *TX_FILES, 'manifest.json'}):
                raise ResourceError('压缩包含额外、重复或路径不安全的文件')
            if sum(item.file_size for item in entries) > MAX_EXPANDED or bundle.getinfo('manifest.json').file_size > 65536:
                raise ResourceError('资源包声明体积超过限制')
            for entry in entries:
                if entry.is_dir() or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ResourceError('资源包不允许目录或符号链接')
                with bundle.open(entry) as source, (target / entry.filename).open('wb') as output:
                    copied = 0
                    while chunk := source.read(1024 * 1024):
                        copied += len(chunk)
                        if copied > entry.file_size:
                            raise ResourceError('资源包实际体积超过声明值')
                        output.write(chunk)
        manifest = _json(target / 'manifest.json')
        validate_files(target, manifest)
        final = root / identity
        if final.exists():
            validate_files(final, _json(final / 'manifest.json'))
        else:
            try:
                os.rename(target, final)
            except FileExistsError:
                validate_files(final, _json(final / 'manifest.json'))
        return {**manifest, 'package_id': identity}


@checked
def activate(identity, expected_package_id):
    identity = _identity(identity)
    root = directory()
    manifest = _json(root / identity / 'manifest.json')
    validate_files(root / identity, manifest)
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        old = current()
        old_id = old['package_id'] if old else None
        if old_id == identity:
            return {**manifest, 'package_id': identity, 'changed': False}
        if old_id != expected_package_id:
            raise ConflictError('当前资源版本已变化，请刷新后重试')
        if db.execute("SELECT count(*) FROM optimization_jobs WHERE status IN ('queued','running','cancelling')").fetchone()[0]:
            raise ConflictError('请等待或取消寻优任务后再切换资源，避免一次搜索混用资源版本')
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=root, suffix='.json', delete=False) as output:
            temporary = Path(output.name)
            output.write(dumps({'package_id': identity})); output.flush(); os.fsync(output.fileno())
        try:
            os.replace(temporary, root / 'active.json')
        finally:
            temporary.unlink(missing_ok=True)
        audit(db, 'resources.activated', identity, {'previous': old_id, 'version': manifest['version']})
    return {**manifest, 'package_id': identity, 'changed': True}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _download(api_path, output, limit, raw=False):
    token = os.getenv('DCSJ_RESOURCE_TOKEN')
    if not token and os.getenv('DCSJ_RESOURCE_TOKEN_FILE'):
        token = Path(os.environ['DCSJ_RESOURCE_TOKEN_FILE']).read_text(encoding='utf-8').strip()
    if not token:
        raise ResourceError('请在服务端配置仅能读取私有资源仓库的 DCSJ_RESOURCE_TOKEN 或 DCSJ_RESOURCE_TOKEN_FILE')
    request = Request('https://api.github.com/' + api_path, headers={
        'Authorization': 'Bearer ' + token, 'User-Agent': 'DCSJ-resource-import',
        'Accept': 'application/vnd.github.raw+json' if raw else 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2026-03-10'})
    try:
        with build_opener(_NoRedirect).open(request, timeout=45) as response, Path(output).open('wb') as destination:
            size = 0
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise ResourceError('远程资源超过大小限制')
                destination.write(chunk)
    except HTTPError as error:
        raise ResourceError(f'私有仓库读取失败（HTTP {error.code}），请检查仓库与只读令牌权限') from None
    except (URLError, TimeoutError, OSError):
        raise ResourceError('私有仓库连接失败，请检查服务端网络与令牌文件') from None


def _download_file(repository, name, commit, temp, output, limit):
    """JSON/base64 preserves binary bytes through text-oriented HTTP gateways."""
    metadata_path = temp / (name + '.metadata.json')
    _download(f'repos/{repository}/contents/{name}?ref={commit}', metadata_path, 2 * limit + 65536)
    metadata = _json(metadata_path)
    identity, size = metadata.get('sha'), metadata.get('size')
    if metadata.get('type') != 'file' or metadata.get('path') != name or not re.fullmatch(r'[a-f0-9]{40}', identity or ''):
        raise ResourceError('远端文件对象无效')
    if not isinstance(size, int) or not 0 < size <= limit:
        raise ResourceError('远端文件体积超过限制')
    if metadata.get('encoding') == 'base64' and metadata.get('content'):
        blob = metadata
    else:
        blob_path = temp / (name + '.blob.json')
        _download(f'repos/{repository}/git/blobs/{identity}', blob_path, 2 * limit + 65536)
        blob = _json(blob_path)
    if blob.get('sha') != identity or blob.get('size') != size or blob.get('encoding') != 'base64':
        raise ResourceError('远端文件对象与版本清单不一致')
    encoded = blob['content'].replace('\n', '').replace('\r', '')
    if len(encoded) != 4 * ((size + 2) // 3):
        raise ResourceError('远端文件编码大小无效')
    checksum = hashlib.sha1(f'blob {size}\0'.encode())
    written = 0
    with Path(output).open('wb') as destination:
        for start in range(0, len(encoded), 65536):
            chunk = base64.b64decode(encoded[start:start + 65536], validate=True)
            written += len(chunk); checksum.update(chunk); destination.write(chunk)
    if written != size or checksum.hexdigest() != identity:
        raise ResourceError('远端文件对象完整性校验失败')


def _download_archive(repository, commit, temp, release):
    parts = release.get('parts')
    archive = temp / 'bundle.zip'
    if parts is None:  # Compatibility with existing single-file v1 releases.
        _download_file(repository, 'resource-bundle.zip', commit, temp, archive, MAX_ARCHIVE)
        return archive
    if not isinstance(parts, list) or not 1 <= len(parts) <= 32:
        raise ResourceError('分段资源清单无效')
    for index, part in enumerate(parts, 1):
        if part.get('name') != f'resource-bundle.zip.part{index:03}' or not isinstance(part.get('size'), int) or not 0 < part['size'] <= 4 * 1024 * 1024 or not re.fullmatch(r'[a-f0-9]{64}', part.get('sha256', '')):
            raise ResourceError('分段资源名称、体积或校验值无效')
    total = sum(part['size'] for part in parts)
    if total != release.get('size') or total > MAX_ARCHIVE:
        raise ResourceError('分段资源总大小不一致或超过限制')
    def fetch(part):
        path = temp / (part['name'] + '.data')
        for attempt in range(3):
            try:
                _download_file(repository, part['name'], commit, temp, path, part['size'])
                break
            except json.JSONDecodeError:
                if attempt == 2:
                    raise ResourceError('分段资源响应不完整，请检查网络后重试') from None
            except ResourceError as error:
                transient = any(text in str(error) for text in ('连接失败', '完整性校验失败', '编码大小无效', 'HTTP 502', 'HTTP 503', 'HTTP 504'))
                if attempt == 2 or not transient:
                    raise
        if path.stat().st_size != part['size'] or digest(path) != part['sha256']:
            raise ResourceError('分段资源校验失败')
        return path
    with ThreadPoolExecutor(max_workers=3) as pool:
        # Executor.map preserves the manifest order regardless of completion order.
        paths = list(pool.map(fetch, parts))
    with archive.open('wb') as output:
        for path in paths:
            with path.open('rb') as source:
                shutil.copyfileobj(source, output, 1024 * 1024)
    return archive


@checked
def import_remote(expected_package_id):
    repository = os.getenv('DCSJ_RESOURCE_REPOSITORY', 'ka33u/DCSJ-SYSJ')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
        raise ResourceError('资源仓库配置无效')
    root = directory(); root.mkdir(parents=True, exist_ok=True)
    with import_slot(), tempfile.TemporaryDirectory(prefix='download-', dir=root) as temp:
        temp = Path(temp)
        _download('repos/' + repository, temp / 'repo.json', 65536)
        if _json(temp / 'repo.json').get('private') is not True:
            raise ResourceError('资源仓库必须保持私有，已停止导入')
        ref = quote(os.getenv('DCSJ_RESOURCE_REF', 'main'), safe='')
        _download(f'repos/{repository}/commits/{ref}', temp / 'commit.json', 1024 * 1024)
        commit = _json(temp / 'commit.json')['sha']
        if not re.fullmatch(r'[a-f0-9]{40}', commit):
            raise ResourceError('资源提交标识无效')
        _download_file(repository, 'manifest.json', commit, temp, temp / 'release.json', 65536)
        release = _json(temp / 'release.json')
        if release.get('archive') != 'resource-bundle.zip' or not re.fullmatch(r'[a-f0-9]{64}', release.get('sha256', '')):
            raise ResourceError('远程资源清单无效')
        archive = _download_archive(repository, commit, temp, release)
        package = install(archive, release['sha256'])
        if release.get('size') != archive.stat().st_size or any(
                release.get(key) != package.get(key) for key in ('version', 'abi', 'counts', 'files', 'minimum_application')):
            raise ResourceError('远端清单与资源包内清单不一致')
        result = activate(package['package_id'], expected_package_id)
        return {**result, 'repository': repository, 'commit': commit}
