"""Upgrade guards use synthetic databases and package metadata only."""
from contextlib import closing
from importlib import metadata
from pathlib import Path
import sqlite3

import pytest

from dcsj.backup import backup, restore
from dcsj.storage import SCHEMA_VERSION, UnsupportedDatabaseVersion, connect
from scripts import check_dependencies


def test_dependency_check_detects_missing_and_wrong_pins():
    installed = {'current': '1.2.3', 'older': '1.0', 'newer': '3.0'}

    def version(name):
        if name not in installed:
            raise metadata.PackageNotFoundError(name)
        return installed[name]

    pins = '# Exact runtime versions\ncurrent==1.2.3\nolder==2.0\nnewer==2.0\nmissing==1.0\n'
    assert check_dependencies.mismatches(pins, version) == [
        ('older', '2.0', '1.0'), ('newer', '2.0', '3.0'), ('missing', '1.0', None)]
    with pytest.raises(ValueError, match='Unsupported dependency pin'):
        check_dependencies.mismatches('current>=1.0', version)


def test_dependency_check_exit_codes_and_old_interpreter(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(check_dependencies, 'ROOT', tmp_path)
    monkeypatch.setattr(check_dependencies.sys, 'version_info', (3, 12, 0))
    monkeypatch.setattr(check_dependencies.metadata, 'version', lambda name: '1.0')
    (tmp_path / 'requirements.txt').write_text('example==1.0\n', encoding='utf-8')
    assert check_dependencies.main() == 0
    assert 'ready' in capsys.readouterr().out
    (tmp_path / 'requirements.txt').write_text('example==2.0\n', encoding='utf-8')
    assert check_dependencies.main() == 1
    assert 'requires 2.0; installed 1.0' in capsys.readouterr().out
    (tmp_path / 'requirements.txt').write_text('example>=1.0\n', encoding='utf-8')
    assert check_dependencies.main() == 2
    assert 'Unsupported dependency pin' in capsys.readouterr().err
    monkeypatch.setattr(check_dependencies.sys, 'version_info', (3, 11, 0))
    assert check_dependencies.main() == 2
    assert 'Python 3.12 or newer' in capsys.readouterr().err


def test_future_database_rejected_without_changing_file_or_journal(tmp_path):
    path = tmp_path / 'future.sqlite'
    with closing(sqlite3.connect(path)) as db:
        db.execute(f'PRAGMA user_version={SCHEMA_VERSION + 1}')
        db.execute('CREATE TABLE future_feature(value TEXT)')
        db.execute("INSERT INTO future_feature VALUES ('keep this data')")
        db.commit()
    before = path.read_bytes()
    with pytest.raises(UnsupportedDatabaseVersion, match='不会降级'):
        with connect(path):
            pytest.fail('Future database must not be opened for application use')
    assert path.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ['future.sqlite']
    with closing(sqlite3.connect(path)) as db:
        assert db.execute('PRAGMA journal_mode').fetchone()[0] == 'delete'
        assert db.execute('PRAGMA user_version').fetchone()[0] == SCHEMA_VERSION + 1
        assert db.execute('SELECT value FROM future_feature').fetchone()[0] == 'keep this data'


def test_initialized_database_still_checks_version_on_each_connection(tmp_path):
    path = tmp_path / 'upgraded.sqlite'
    with connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == SCHEMA_VERSION
    with closing(sqlite3.connect(path)) as db:
        db.execute(f'PRAGMA user_version={SCHEMA_VERSION + 1}')
    with pytest.raises(UnsupportedDatabaseVersion):
        with connect(path):
            pytest.fail('The migration cache must not bypass the version check')


def test_backup_and_checksum_restore_do_not_read_entire_database(tmp_path, monkeypatch):
    source, saved, restored = [tmp_path / name for name in ('live.sqlite', 'backup.sqlite', 'restored.sqlite')]
    with closing(sqlite3.connect(source)) as db:
        db.execute('CREATE TABLE sample(value TEXT)')
        db.execute("INSERT INTO sample VALUES ('synthetic design')")
        db.commit()

    def disallow_read_bytes(self):
        raise AssertionError('Database checksums must stream the file')

    monkeypatch.setattr(Path, 'read_bytes', disallow_read_bytes)
    receipt = backup(source, saved)
    restore(saved, restored, receipt['sha256'])
    with closing(sqlite3.connect(restored)) as db:
        assert db.execute('SELECT value FROM sample').fetchone()[0] == 'synthetic design'
    with pytest.raises(ValueError, match='校验和'):
        restore(saved, tmp_path / 'invalid.sqlite', 'wrong')
    assert not (tmp_path / 'invalid.sqlite').exists()
