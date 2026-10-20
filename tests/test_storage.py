from contextlib import closing
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from dcsj import projects
from dcsj.backup import backup, restore
from dcsj.storage import ConflictError, connect


def test_existing_database_migration_preserves_designs(tmp_path, monkeypatch):
    path = tmp_path / 'legacy.sqlite'
    monkeypatch.setenv('DCSJ_PROJECTS_DB', str(path))
    with closing(sqlite3.connect(path)) as db:
        db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT NOT NULL,parameters TEXT NOT NULL,updated_at TEXT NOT NULL)')
        db.execute('INSERT INTO projects VALUES (?,?,?,?)', ('existing', 'Draft', '{"kW":12}', '2020-01-01T00:00:00Z'))
        db.commit()
    current = projects.get_project('existing')
    assert current['parameters'] == {'kW': 12} and current['revision'] == 1
    assert projects.get_revision('existing', 1)['name'] == 'Draft'
    projects.update_project('existing', 1, 'Updated', {'kW': 14})
    assert projects.get_revision('existing', 1)['parameters']['kW'] == 12


def test_concurrent_edits_do_not_silently_overwrite(client):
    item = projects.save_project('base', {'kW': 12})
    def update(name):
        try:
            return projects.update_project(item['id'], 1, name, {'kW': 14})['revision']
        except ConflictError:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, ['A', 'B']))
    assert sorted(map(str, results)) == ['2', 'conflict']
    assert len(projects.revisions(item['id'])) == 2


def test_live_wal_backup_restore_and_overwrite_protection(tmp_path, monkeypatch):
    source, destination = tmp_path / 'live.sqlite', tmp_path / 'backup.sqlite'
    monkeypatch.setenv('DCSJ_PROJECTS_DB', str(source))
    item = projects.save_project('backup test', {'kW': 12})
    with connect() as live:
        assert live.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
        info = backup(source, destination)
    assert info['quick_check'] == 'ok'
    projects.update_project(item['id'], 1, 'changed', {'kW': 14})
    restored = tmp_path / 'restored.sqlite'
    restore(destination, restored, info['sha256'])
    with closing(sqlite3.connect(restored)) as db:
        assert db.execute('SELECT revision FROM projects').fetchone()[0] == 1
    with pytest.raises(FileExistsError):
        backup(source, destination)
    with pytest.raises(ValueError, match='校验和'):
        restore(destination, tmp_path / 'invalid.sqlite', 'wrong')
    assert not (tmp_path / 'invalid.sqlite').exists()
    assert not list(tmp_path.glob('*.tmp'))
