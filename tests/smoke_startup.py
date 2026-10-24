"""Exercise the shipped launcher twice in a clean, source-only temporary directory.

Requires network access for the first dependency installation and a preinstalled
PDF font (python scripts/install_pdf_font.py). Never opens the user's design DB.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener, HTTPCookieProcessor
from http.cookiejar import CookieJar

ROOT = Path(__file__).resolve().parents[1]
HTTP = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))


def request(base, path, body=None, method=None):
    data = None if body is None else json.dumps(body).encode('utf-8')
    with HTTP.open(Request(base + path, data=data, method=method,
                           headers={'Content-Type': 'application/json','X-DCSJ-Request':'1'}), timeout=5) as response:
        return json.load(response)


def copy_sources(destination):
    files = ['requirements.txt', 'run.py', 'start.command', 'start.bat',
             'scripts/check_dependencies.py', 'scripts/install_pdf_font.py', 'data/schema.sql']
    files += [str(path.relative_to(ROOT)) for path in (ROOT / 'dcsj').glob('*.py')]
    for name in files:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    shutil.copytree(ROOT / 'frontend', destination / 'frontend')


def stop(process):
    if process.poll() is not None:
        return
    if os.name == 'nt':
        # Kill only the test's command process and its server child.
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    else:
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        if os.name != 'nt':
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


@contextmanager
def running(directory, env, log_path):
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    if os.name == 'nt':
        command = [os.environ.get('COMSPEC', 'cmd.exe'), '/d', '/c', 'start.bat', '--port', str(port)]
        options = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP}
    else:
        command = [str(directory / 'start.command'), '--port', str(port)]
        options = {'start_new_session': True}
    with log_path.open('w', encoding='utf-8') as log:
        process = subprocess.Popen(command, cwd=directory, env=env, stdout=log, stderr=subprocess.STDOUT, **options)
        try:
            base = f'http://127.0.0.1:{port}'
            deadline = time.monotonic() + 240
            while True:
                if process.poll() is not None:
                    raise RuntimeError(f'Launcher exited early with code {process.returncode}')
                try:
                    request(base, '/api/v1/auth/login', {'username':'Startup test','password':'1234'})
                    status = request(base, '/api/v1/status')
                    break
                except (URLError, OSError):
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Launcher did not become ready within 240 seconds')
                    time.sleep(.25)
            assert status['engine']['status'] == 'unavailable'
            assert status['catalog']['model_count'] == 0
            assert status['reports']['pdf']['available']
            yield base
        finally:
            stop(process)


def main():
    font = Path(os.environ.get('DCSJ_PDF_FONT', str(ROOT / '.runtime/fonts/DCSJPdfSans.ttf'))).resolve()
    if not font.is_file():
        raise RuntimeError('Install the test font first: python scripts/install_pdf_font.py')
    with tempfile.TemporaryDirectory(prefix='dcsj-launcher-') as temporary:
        directory = Path(temporary) / '中文 source with spaces'
        copy_sources(directory)
        env = os.environ.copy()
        env.update(DCSJ_DATA_DIR=str(directory / '.runtime'),
                   DCSJ_PROJECTS_DB=str(directory / '.runtime/projects.sqlite'),
                   DCSJ_PDF_FONT=str(font), PYTHONUTF8='1',
                   # Have the launcher select the same installed Python as this test.
                   PY_PYTHON3=f'{sys.version_info.major}.{sys.version_info.minor}')
        env['PATH'] = str(Path(sys.executable).parent) + os.pathsep + env.get('PATH', '')
        logs = [directory / 'first.log', directory / 'restart.log']
        try:
            with running(directory, env, logs[0]) as base:
                assert '/api/v1/comparison-sets' in request(base, '/openapi.json')['paths']
                item = request(base, '/api/v1/projects', {'name': '启动验证：人工方案', 'parameters': {'kW': 12}})
                changed = request(base, '/api/v1/projects/' + item['id'], {
                    'name': '重启验证：第二版', 'parameters': {'kW': 14}, 'expected_revision': 1}, 'PUT')
                assert changed['revision'] == 2
            assert 'installed missing' in logs[0].read_text(encoding='utf-8')
            with running(directory, env, logs[1]) as base:
                item = request(base, '/api/v1/projects/' + item['id'])
                assert item['name'] == '重启验证：第二版' and item['parameters']['kW'] == 14
                assert item['revision'] == 2
                original = request(base, '/api/v1/projects/' + item['id'] + '/revisions/1')
                assert original['parameters']['kW'] == 12
            restart_log = logs[1].read_text(encoding='utf-8')
            assert 'pinned application dependencies are ready' in restart_log
            assert 'installed missing' not in restart_log
            print('Launcher smoke passed: fresh installation, UTF-8 paths, HTTP, project history and restart.')
        except BaseException:
            for path in logs:
                if path.is_file():
                    print(path.name + ':\n' + path.read_text(encoding='utf-8', errors='replace')[-8000:], file=sys.stderr)
            raise


if __name__ == '__main__':
    main()
