"""Synthetic inputs for application tests; no original design records."""
import pytest
from fastapi.testclient import TestClient
from dcsj.api import app
from dcsj.parameters import INPUT_KEYS


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('DCSJ_PROJECTS_DB', str(tmp_path / 'designs.sqlite'))
    with TestClient(app) as c:
        yield c


@pytest.fixture
def parameters():
    p = dict.fromkeys(INPUT_KEYS, 0)
    p.update(SizeX='Synthetic test geometry', Jiefa='Y', Jueyuan='F', Paihao='SYNTHETIC',
             kW=12, p=4, f=50, Ufai=230, Q1=36, Q2=28, D1=300, Di1=180, Di2=50,
             g=.6, L=160, a=1, ZZ=16, kfe=.95, ATA=.9, PF=.8, kt=1, ky=1,
             L01=10, L02=10, czn=1, JJ=4, Djj=1, hs12=20, hr12=16)
    return p


@pytest.fixture
def snapshot(parameters):
    return {'inputs': parameters, 'values': {'ATA1': .9, 'CostFx': 100, 'PF1': .8},
            'metrics': [], 'curves': [], 'warnings': ['Synthetic result; not a motor prediction.'],
            'engine': {'name': 'test double'}, 'trace': [],
            'provenance': {'input_sha256': 'synthetic', 'kernel_sha256': 'synthetic',
                           'wrapper_sha256': 'synthetic', 'material_sha256': 'synthetic'}}
