"""Transactional local application storage and compatible schema migration."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import threading

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = 8
_migration_lock = threading.Lock()
_initialized = set()


class NotFoundError(ValueError):
    pass


class ConflictError(ValueError):
    pass


class UnsupportedDatabaseVersion(RuntimeError):
    pass


def _check_version(db):
    version = db.execute('PRAGMA user_version').fetchone()[0]
    if version > SCHEMA_VERSION:
        raise UnsupportedDatabaseVersion(
            f'设计库结构版本 {version} 高于当前程序支持的 {SCHEMA_VERSION}。'
            '请使用对应的新版本程序，或将升级前备份恢复到新路径；当前程序不会降级此数据库。')
    return version


def now():
    return datetime.now(timezone.utc).isoformat()


def dumps(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def database_path():
    directory = Path(os.getenv("DCSJ_DATA_DIR", str(ROOT / ".runtime")))
    return Path(os.getenv("DCSJ_PROJECTS_DB", str(directory / "projects.sqlite")))


def _migrate(db, path):
    identity = (str(path.resolve()), path.stat().st_ino)
    with _migration_lock:
        if _check_version(db) == SCHEMA_VERSION and identity in _initialized:
            return
        db.execute("BEGIN IMMEDIATE")
        try:
            _check_version(db)
            db.execute("CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, "
                       "parameters TEXT NOT NULL, updated_at TEXT NOT NULL)")
            fields = {row[1] for row in db.execute("PRAGMA table_info(projects)")}
            for name, declaration in [("revision", "INTEGER NOT NULL DEFAULT 1"),
                                      ("created_at", "TEXT"), ("notes", "TEXT NOT NULL DEFAULT ''"),
                                      ("archived", "INTEGER NOT NULL DEFAULT 0")]:
                if name not in fields:
                    db.execute(f"ALTER TABLE projects ADD COLUMN {name} {declaration}")
            db.execute("UPDATE projects SET created_at=updated_at WHERE created_at IS NULL")
            for statement in [
                "CREATE TABLE IF NOT EXISTS project_revisions (project_id TEXT NOT NULL REFERENCES projects(id), "
                "revision INTEGER NOT NULL, name TEXT NOT NULL, parameters TEXT NOT NULL, notes TEXT NOT NULL, "
                "created_at TEXT NOT NULL, PRIMARY KEY(project_id, revision))",
                "CREATE TABLE IF NOT EXISTS calculations (id TEXT PRIMARY KEY, name TEXT NOT NULL, "
                "project_id TEXT REFERENCES projects(id), project_revision INTEGER, parameters TEXT NOT NULL, "
                "result TEXT NOT NULL, created_at TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS audit_events (id INTEGER PRIMARY KEY, action TEXT NOT NULL, "
                "entity_id TEXT NOT NULL, created_at TEXT NOT NULL, details TEXT NOT NULL)",
                "CREATE INDEX IF NOT EXISTS calculations_created ON calculations(created_at DESC)",
                "CREATE INDEX IF NOT EXISTS calculations_project ON calculations(project_id, created_at DESC)",
                "CREATE TABLE IF NOT EXISTS optimization_jobs (id TEXT PRIMARY KEY, name TEXT NOT NULL, request TEXT NOT NULL, "
                "status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, evaluated INTEGER NOT NULL DEFAULT 0, "
                "budget INTEGER NOT NULL, best_id TEXT, message TEXT NOT NULL DEFAULT '', owner TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0)",
                "CREATE TABLE IF NOT EXISTS optimization_candidates (job_id TEXT NOT NULL REFERENCES optimization_jobs(id), "
                "ordinal INTEGER NOT NULL, parameters TEXT NOT NULL, calculation_id TEXT, objective REAL, feasible INTEGER NOT NULL, "
                "violations TEXT NOT NULL, error TEXT, PRIMARY KEY(job_id, ordinal))",
                "CREATE TABLE IF NOT EXISTS optimization_workers (id TEXT PRIMARY KEY, heartbeat REAL NOT NULL)",
                "CREATE TABLE IF NOT EXISTS comparison_sets (id TEXT PRIMARY KEY, name TEXT NOT NULL, "
                "notes TEXT NOT NULL, baseline_id TEXT NOT NULL REFERENCES calculations(id), "
                "result TEXT NOT NULL, created_at TEXT NOT NULL)",
                "CREATE INDEX IF NOT EXISTS comparison_sets_created ON comparison_sets(created_at DESC)",
                "CREATE TABLE IF NOT EXISTS library_entries (id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL, "
                "revision INTEGER NOT NULL, archived INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS library_revisions (entry_id TEXT NOT NULL REFERENCES library_entries(id), "
                "revision INTEGER NOT NULL, name TEXT NOT NULL, document TEXT NOT NULL, created_at TEXT NOT NULL, "
                "PRIMARY KEY(entry_id,revision))",
                "CREATE INDEX IF NOT EXISTS library_kind ON library_entries(kind,archived,updated_at DESC)",
                "CREATE TABLE IF NOT EXISTS tx_designs (id TEXT PRIMARY KEY, name TEXT NOT NULL, "
                "revision INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS tx_revisions (design_id TEXT NOT NULL REFERENCES tx_designs(id), "
                "revision INTEGER NOT NULL, parameters TEXT NOT NULL, source TEXT NOT NULL, "
                "created_at TEXT NOT NULL, PRIMARY KEY(design_id,revision))",
                "CREATE TABLE IF NOT EXISTS tx_calculations (id TEXT PRIMARY KEY, name TEXT NOT NULL, "
                "parameters TEXT NOT NULL, result TEXT NOT NULL, created_at TEXT NOT NULL)",
                "CREATE INDEX IF NOT EXISTS tx_calculations_created ON tx_calculations(created_at DESC)",
            ]:
                db.execute(statement)
            for table, column, declaration in [('projects', 'lineage', "TEXT NOT NULL DEFAULT '{}'"),
                                               ('projects', 'slot_bindings', "TEXT NOT NULL DEFAULT '{}'"),
                                               ('project_revisions', 'slot_bindings', "TEXT NOT NULL DEFAULT '{}'"),
                                               ('tx_revisions', 'slot_bindings', "TEXT NOT NULL DEFAULT '{}'"),
                                               ('project_revisions', 'lineage', "TEXT NOT NULL DEFAULT '{}'"),
                                               ('projects', 'author', "TEXT NOT NULL DEFAULT ''"),
                                               ('project_revisions', 'author', "TEXT NOT NULL DEFAULT ''"),
                                               ('tx_designs', 'author', "TEXT NOT NULL DEFAULT ''"),
                                               ('tx_revisions', 'author', "TEXT NOT NULL DEFAULT ''"),
                                               ('calculations', 'archived', 'INTEGER NOT NULL DEFAULT 0')]:
                existing = {row[1] for row in db.execute(f'PRAGMA table_info({table})')}
                if column not in existing:
                    db.execute(f'ALTER TABLE {table} ADD COLUMN {column} {declaration}')
            db.execute("INSERT OR IGNORE INTO project_revisions(project_id,revision,name,parameters,notes,created_at,lineage,author) "
                       "SELECT id,revision,name,parameters,notes,updated_at,lineage,author FROM projects")
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            db.commit()
            _initialized.add(identity)
        except BaseException:
            db.rollback()
            raise


@contextmanager
def connect(path=None):
    path = Path(path) if path is not None else database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=15)
    db.row_factory = sqlite3.Row
    try:
        _check_version(db)
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        _migrate(db, path)
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def audit(db, action, entity_id, details=None):
    db.execute("INSERT INTO audit_events(action,entity_id,created_at,details) VALUES (?,?,?,?)",
               (action, entity_id, now(), dumps(details or {})))
