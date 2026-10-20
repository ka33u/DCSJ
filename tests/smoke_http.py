"""Run against a source-only container; separate invocations verify restart persistence."""
import json
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

url, mode, state_path = sys.argv[1:]
state_path = Path(state_path)


def request(path, body=None, method=None):
    data = None if body is None else json.dumps(body).encode()
    with urlopen(Request(url + path, data=data, method=method,
                         headers={'Content-Type': 'application/json'}), timeout=5) as response:
        return json.load(response)


for attempt in range(30):
    try:
        status = request('/api/v1/status')
        break
    except (URLError, TimeoutError):
        if attempt == 29:
            raise
        time.sleep(1)
assert status['engine']['status'] == 'unavailable'
assert status['catalog']['model_count'] == 0
if mode == 'create':
    saved = request('/api/v1/projects', {'name': 'Container smoke draft', 'parameters': {'kW': 12}})
    state_path.write_text(saved['id'])
    changed = request('/api/v1/projects/' + saved['id'], {
        'name': 'Container smoke revision', 'parameters': {'kW': 14}, 'expected_revision': 1}, 'PUT')
    assert changed['revision'] == 2
    assert '/api/v1/optimization/jobs' in request('/openapi.json')['paths']
    try:
        request('/api/v1/calculate', {'parameters': {}})
        raise AssertionError('Missing private resources must not calculate')
    except HTTPError as error:
        assert error.code == 503
else:
    saved = request('/api/v1/projects/' + state_path.read_text())
    assert saved['parameters']['kW'] == 14 and saved['revision'] == 2
    original = request('/api/v1/projects/' + saved['id'] + '/revisions/1')
    assert original['parameters']['kW'] == 12
print('Source-only HTTP and persistence smoke:', mode, 'passed')
