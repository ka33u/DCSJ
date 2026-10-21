"""Tiny synthetic packages exercise import without shipping any private resource."""
import base64
import hashlib
import gzip
import json
from pathlib import Path
import sqlite3
import zipfile
import pytest
from dcsj import resources, library, storage
from scripts.build_resources import build


@pytest.fixture
def bundle(tmp_path, monkeypatch, parameters):
    monkeypatch.setenv('DCSJ_DATA_DIR', str(tmp_path / 'runtime'))
    monkeypatch.setenv('DCSJ_PROJECTS_DB', str(tmp_path / 'user.sqlite'))
    monkeypatch.delenv('DCSJ_CATALOG_PATH', raising=False)
    source = tmp_path / 'source'; (source / 'data').mkdir(parents=True)
    (source / 'dcsj/generated').mkdir(parents=True)
    with sqlite3.connect(source / 'data/catalog.sqlite') as db:
        db.executescript((resources.ROOT / 'data/schema.sql').read_text())
    material = {'name': 'Synthetic', 'kind': 'mdb_custom', 'slot': 5,
                'const_values': [0.] * 693, 'H_values': [0.] * 49, 'P_values': [0.] * 42}
    (source / 'data/material_kernel_tables.json').write_text(json.dumps({'format_version': 1, 'materials': [material]}))
    (source / 'data/example.json').write_text(json.dumps({'name': 'Synthetic only', 'parameters': parameters}))
    with gzip.open(source / 'dcsj/generated/numeric_kernel.json.gz', 'wt') as f:
        # Package-validation fixture only; this program must not be used for motor prediction.
        json.dump({'format_version': 1, 'instructions': [[4096, 1, 'ret', []]],
                   'image_base': 4096, 'image_size': 1024, 'stack_base': 8192, 'stack_size': 1024,
                   'initial_ebp': 8704, 'entry': 4096, 'functions': [], 'segments': []}, f)
    output = tmp_path / 'release'
    release = build(source, output, 'synthetic.1')
    return source, output, release


def test_install_idempotency_switch_rollback_and_user_isolation(bundle, parameters):
    source, output, release = bundle
    user = library.save('models', 'Synthetic', {'parameters': parameters})
    first = resources.install(output / 'resource-bundle.zip', release['sha256'])
    assert resources.current() is None
    assert resources.activate(first['package_id'], None)['changed']
    assert not resources.activate(first['package_id'], None)['changed']
    assert resources.install(output / 'resource-bundle.zip')['package_id'] == first['package_id']
    assert len(resources.installed()) == 1
    from dcsj.catalog_bundle import catalog_path
    from dcsj.parameters import example_data
    from dcsj.engine import resource_context
    pinned = resource_context()
    assert catalog_path() == resources.asset('catalog.sqlite')
    assert example_data()['name'] == 'Synthetic only'
    build(source, output, 'synthetic.2')
    second = resources.install(output / 'resource-bundle.zip')
    with pytest.raises(storage.ConflictError):
        resources.activate(second['package_id'], '0' * 64)
    resources.activate(second['package_id'], first['package_id'])
    assert pinned['package_id'] == first['package_id'] and pinned['kernel_path'].is_file()
    assert resource_context()['package_id'] == second['package_id']
    resources.activate(first['package_id'], second['package_id'])
    assert library.get('models', user['id']) == user
    assert len(resources.installed()) == 2


def test_running_job_blocks_resource_switch(bundle):
    _, output, _ = bundle
    package = resources.install(output / 'resource-bundle.zip')
    with storage.connect() as db:
        db.execute("INSERT INTO optimization_jobs(id,name,request,status,created_at,updated_at,budget) VALUES ('synthetic-job','test','{}','running','now','now',2)")
    with pytest.raises(storage.ConflictError):
        resources.activate(package['package_id'], None)
    assert resources.current() is None


@pytest.mark.parametrize('mutation', ['traversal', 'duplicate', 'tamper', 'abi', 'minimum', 'corrupt'])
def test_invalid_package_does_not_replace_active(bundle, mutation):
    _, output, _ = bundle
    archive = output / 'resource-bundle.zip'
    installed = resources.install(archive); resources.activate(installed['package_id'], None)
    with zipfile.ZipFile(archive) as original:
        entries = {name: original.read(name) for name in original.namelist()}
    if mutation == 'traversal': entries['../escape'] = b'x'
    if mutation == 'tamper': entries['example.json'] += b' '
    if mutation in {'abi', 'minimum'}:
        manifest = json.loads(entries['manifest.json'])
        manifest['abi' if mutation == 'abi' else 'minimum_application'] = 'invalid' if mutation == 'abi' else '99.0.0'
        entries['manifest.json'] = json.dumps(manifest).encode()
    bad = output / 'bad.zip'
    with zipfile.ZipFile(bad, 'w') as target:
        for name, value in entries.items(): target.writestr(name, value)
        if mutation == 'duplicate':
            with pytest.warns(UserWarning): target.writestr('manifest.json', entries['manifest.json'])
    if mutation == 'corrupt': bad.write_bytes(b'not a ZIP')
    with pytest.raises(resources.ResourceError): resources.install(bad)
    assert resources.current()['package_id'] == installed['package_id']
    assert not (resources.directory() / 'escape').exists()
    assert not list(resources.directory().glob('staging-*'))


def test_remote_import_pins_commit_and_validates_private_status(bundle, monkeypatch):
    _, output, release = bundle
    calls = []; private = True
    def download(path, destination, limit, raw=False):
        calls.append(path)
        if '/commits/' in path: Path(destination).write_text(json.dumps({'sha': 'a' * 40}))
        elif '/contents/' in path or '/git/blobs/' in path:
            is_manifest = '/contents/manifest.json' in path
            content = json.dumps(release).encode() if is_manifest else (output / ('resource-bundle.zip.part001' if release.get('parts') else 'resource-bundle.zip')).read_bytes()
            blob = {'sha': hashlib.sha1(f'blob {len(content)}\0'.encode() + content).hexdigest(), 'size': len(content),
                    'encoding': 'base64', 'content': base64.b64encode(content).decode()}
            if '/contents/' in path:
                blob.update(type='file', path='manifest.json' if is_manifest else ('resource-bundle.zip.part001' if release.get('parts') else 'resource-bundle.zip'))
                if not is_manifest: blob.update(encoding='none', content='')
            Path(destination).write_text(json.dumps(blob))
        else: Path(destination).write_text(json.dumps({'private': private}))
    monkeypatch.setattr(resources, '_download', download)
    result = resources.import_remote(None)
    assert result['commit'] == 'a' * 40 and result['package_id'] == release['sha256']
    assert all('?ref=' + 'a' * 40 in path for path in calls if '/contents/' in path)
    private = False; calls.clear()
    with pytest.raises(resources.ResourceError, match='私有'): resources.import_remote(result['package_id'])
    assert len(calls) == 1
    assert resources.current()['package_id'] == result['package_id']


def test_http_errors_and_missing_token_do_not_leak_credentials(bundle, monkeypatch):
    from urllib.error import HTTPError
    monkeypatch.setenv('DCSJ_RESOURCE_TOKEN', 'synthetic-secret-never-print')
    class Opener:
        def open(self, request, timeout):
            assert request.full_url.startswith('https://api.github.com/')
            raise HTTPError(request.full_url, 403, 'synthetic-secret-never-print', {}, None)
    monkeypatch.setattr(resources, 'build_opener', lambda *args: Opener())
    with pytest.raises(resources.ResourceError) as error:
        resources._download('repos/synthetic/test', bundle[1] / 'out', 65536)
    assert '403' in str(error.value) and 'synthetic-secret' not in str(error.value)
    monkeypatch.delenv('DCSJ_RESOURCE_TOKEN'); monkeypatch.delenv('DCSJ_RESOURCE_TOKEN_FILE', raising=False)
    with pytest.raises(resources.ResourceError, match='服务端配置'):
        resources.import_remote(None)


def test_api_import_conflict_and_activation_errors(client, bundle, monkeypatch):
    assert client.post('/api/v1/resources/activate', json={'package_id': '0' * 64}).status_code == 422
    assert client.get('/api/v1/resources').json()['active'] is None
    with resources.import_slot():
        assert client.post('/api/v1/resources/import', json={}).status_code == 409


def test_downloaded_blob_integrity_and_redirects_are_rejected(bundle, monkeypatch):
    output = bundle[1]
    content = b'synthetic zip bytes'
    identity = hashlib.sha1(f'blob {len(content)}\0'.encode() + content).hexdigest()
    metadata = {'type': 'file', 'path': 'resource-bundle.zip', 'sha': identity,
                'size': len(content), 'encoding': 'base64', 'content': base64.b64encode(b'X' + content[1:]).decode()}
    monkeypatch.setattr(resources, '_download', lambda path, destination, *args, **kwargs: Path(destination).write_text(json.dumps(metadata)))
    with pytest.raises(resources.ResourceError, match='完整性'):
        resources._download_file('synthetic/private', 'resource-bundle.zip', 'a' * 40, output, output / 'bad-output', 1024)
    assert resources._NoRedirect().redirect_request(None, None, 302, '', {}, 'https://untrusted.invalid/') is None


def test_segmented_download_reassembles_in_manifest_order_and_rejects_tampering(bundle, monkeypatch):
    output = bundle[1]
    chunks = [b'first part', b'second part', b'last part']
    parts = [{'name': f'resource-bundle.zip.part{i:03}', 'size': len(chunk), 'sha256': hashlib.sha256(chunk).hexdigest()} for i, chunk in enumerate(chunks, 1)]
    release = {'parts': parts, 'size': sum(map(len, chunks))}
    corrupt = False
    def fetch(repository, name, commit, temp, destination, limit):
        index = int(name[-3:]) - 1
        Path(destination).write_bytes(b'X' * limit if corrupt else chunks[index])
    monkeypatch.setattr(resources, '_download_file', fetch)
    archive = resources._download_archive('synthetic/private', 'a' * 40, output, release)
    assert archive.read_bytes() == b''.join(chunks)
    corrupt = True
    with pytest.raises(resources.ResourceError, match='分段资源校验'):
        resources._download_archive('synthetic/private', 'a' * 40, output, release)
    parts[0]['name'] = '../escape'
    with pytest.raises(resources.ResourceError, match='名称'):
        resources._download_archive('synthetic/private', 'a' * 40, output, release)


def test_segment_retry_is_bounded_and_does_not_accept_truncated_content(bundle, monkeypatch):
    output = bundle[1]; attempts = 0
    content = b'synthetic part'; part = {'name': 'resource-bundle.zip.part001', 'size': len(content), 'sha256': hashlib.sha256(content).hexdigest()}
    def download(*args):
        nonlocal attempts
        attempts += 1
        if attempts < 3: raise json.JSONDecodeError('truncated', '', 0)
        Path(args[4]).write_bytes(content)
    monkeypatch.setattr(resources, '_download_file', download)
    assert resources._download_archive('synthetic/private', 'a' * 40, output, {'parts': [part], 'size': len(content)}).read_bytes() == content
    assert attempts == 3
    def always_truncated(*args):
        raise json.JSONDecodeError('truncated', '', 0)
    monkeypatch.setattr(resources, '_download_file', always_truncated)
    with pytest.raises(resources.ResourceError, match='不完整'):
        resources._download_archive('synthetic/private', 'a' * 40, output, {'parts': [part], 'size': len(content)})
