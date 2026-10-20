"""Transactional local application storage and compatible schema migration."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import threading

ROOT = Path(__file__).resolve().parents[1]
_migration_lock = threading.Lock()
_initialized = set()


class NotFoundError(ValueError):
    pass


class ConflictError(ValueError):
    pass


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
        if identity in _initialized:
            return
        db.execute("BEGIN IMMEDIATE")
        try:
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
            ]:
                db.execute(statement)
            db.execute("INSERT OR IGNORE INTO project_revisions "
                       "SELECT id,revision,name,parameters,notes,updated_at FROM projects")
            db.execute("PRAGMA user_version=1")
            db.commit()
            _initialized.add(identity)
        except BaseException:
            db.rollback()
            raise


@contextmanager
def connect():
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=15)
    db.row_factory = sqlite3.Row
    try:
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
