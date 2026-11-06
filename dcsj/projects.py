"""Revisioned designs; updates use optimistic concurrency control."""
import json
import uuid

from .storage import ConflictError, NotFoundError, audit, connect, dumps, now
from .parameters import input_parameters
from .saved_catalog import series_name
from .design_history import MAIN_OWNER, require_owner, describe


def _project(row):
    if row is None:
        return None
    result = dict(row)
    raw = json.loads(result['parameters'])
    result['parameters'] = input_parameters(raw)
    if raw != result['parameters']:
        result['raw_parameters'] = raw
    result['lineage'] = json.loads(result.get('lineage') or '{}')
    result['slot_bindings'] = json.loads(result.get('slot_bindings') or '{}')
    return result


def list_projects(include_archived=False):
    with connect() as db:
        return [dict(r) for r in db.execute(
            f'SELECT id,name,author,series,revision,archived,created_at,updated_at,{MAIN_OWNER} AS owner FROM projects '
            + ('' if include_archived else 'WHERE archived=0 ') + 'ORDER BY updated_at DESC LIMIT 500')]


def get_project(project_id):
    with connect() as db:
        return _project(db.execute(f'SELECT *,{MAIN_OWNER} AS owner FROM projects WHERE id=?', (project_id,)).fetchone())


def save_project(name, parameters, notes='', lineage=None, author='', slot_bindings=None, series=None):
    series = series_name(series) or ''
    project_id, timestamp = str(uuid.uuid4()), now()
    encoded = dumps(parameters)
    with connect() as db:
        db.execute('INSERT INTO projects(id,name,parameters,updated_at,created_at,revision,notes,lineage,author) '
                   'VALUES (?,?,?,?,?,1,?,?,?)', (project_id, name, encoded, timestamp, timestamp, notes, dumps(lineage or {}), author))
        db.execute('INSERT INTO project_revisions(project_id,revision,name,parameters,notes,created_at,lineage,author) VALUES (?,1,?,?,?,?,?,?)',
                   (project_id, name, encoded, notes, timestamp, dumps(lineage or {}), author))
        audit(db, 'project.created', project_id, {'revision': 1})
        for table, key in [('projects','id'), ('project_revisions','project_id')]:
            db.execute(f'UPDATE {table} SET slot_bindings=?,series=? WHERE {key}=?', (dumps(slot_bindings or {}), series, project_id))
    return get_project(project_id)


def update_project(project_id, expected_revision, name, parameters, notes='', lineage=None, author=None, slot_bindings=None, series=None):
    series = series_name(series)
    timestamp, encoded = now(), dumps(parameters)
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute(f'SELECT revision,archived,lineage,slot_bindings,series,{MAIN_OWNER} AS owner FROM projects WHERE id=?', (project_id,)).fetchone()
        if row is None:
            raise NotFoundError('设计不存在')
        require_owner(row['owner'], author)
        if row['revision'] != expected_revision or row['archived']:
            raise ConflictError('设计已有新版本或已归档，请重新打开后再保存；当前修改可以另存为新设计。')
        revision = expected_revision + 1
        previous_origin = json.loads(row['lineage'])
        origin = dict(previous_origin if lineage is None else lineage)
        if 'copied_from' in previous_origin:
            origin['copied_from'] = previous_origin['copied_from']
        origin = dumps(origin)
        db.execute('UPDATE projects SET name=?,parameters=?,notes=?,updated_at=?,revision=?,lineage=?,author=? WHERE id=?',
                   (name, encoded, notes, timestamp, revision, origin, author, project_id))
        db.execute('INSERT INTO project_revisions(project_id,revision,name,parameters,notes,created_at,lineage,author) VALUES (?,?,?,?,?,?,?,?)',
                   (project_id, revision, name, encoded, notes, timestamp, origin, author))
        audit(db, 'project.updated', project_id, {'revision': revision})
        bindings = row['slot_bindings'] if slot_bindings is None else dumps(slot_bindings)
        series = row['series'] if series is None else series
        db.execute('UPDATE projects SET slot_bindings=?,series=? WHERE id=?', (bindings, series, project_id))
        db.execute('UPDATE project_revisions SET slot_bindings=?,series=? WHERE project_id=? AND revision=?', (bindings, series, project_id, revision))
    return get_project(project_id)


def revisions(project_id):
    with connect() as db:
        if db.execute('SELECT 1 FROM projects WHERE id=?', (project_id,)).fetchone() is None:
            raise NotFoundError('设计不存在')
        rows = db.execute('SELECT * FROM project_revisions WHERE project_id=? ORDER BY revision',
                          (project_id,)).fetchall()
        items, previous = [], None
        for row in rows:
            metadata = {k: row[k] for k in ('revision','name','author','series','notes','created_at')}
            items.append({**metadata, **describe(row, previous)})
            previous = row
        return items[::-1]


def get_revision(project_id, revision):
    with connect() as db:
        row = db.execute('SELECT * FROM project_revisions WHERE project_id=? AND revision=?',
                         (project_id, revision)).fetchone()
        if row is None:
            raise NotFoundError('设计版本不存在')
        return _project(row)


def set_archived(project_id, archived, expected_revision, author):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute(f'SELECT {MAIN_OWNER} AS owner FROM projects WHERE id=?', (project_id,)).fetchone()
        if row is None:
            raise NotFoundError('设计不存在')
        require_owner(row['owner'], author)
        changed = db.execute('UPDATE projects SET archived=?,updated_at=? WHERE id=? AND revision=?',
                             (int(archived), now(), project_id, expected_revision)).rowcount
        if not changed:
            raise ConflictError('设计不存在或版本已变化，请刷新列表。')
        audit(db, 'project.archived' if archived else 'project.restored', project_id)
    return get_project(project_id)
