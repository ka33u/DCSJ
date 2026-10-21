"""Saved comparison results remain stable across edits and application upgrades."""
import json
import uuid

from . import __version__, calculations
from .storage import NotFoundError, audit, connect, dumps, now


def save(name, identities, baseline_id=None, notes=''):
    # Resolve trusted snapshots on the server. Never accept client-supplied results.
    result = calculations.compare(identities, baseline_id)
    identity, timestamp = str(uuid.uuid4()), now()
    result['comparison_version'] = 1
    result['application_version'] = __version__
    with connect() as db:
        db.execute('INSERT INTO comparison_sets VALUES (?,?,?,?,?,?)',
                   (identity, name, notes, result['baseline_id'], dumps(result), timestamp))
        audit(db, 'comparison.saved', identity, {'calculation_ids': identities, 'baseline_id': result['baseline_id']})
    return {**result, 'id': identity, 'name': name, 'notes': notes, 'created_at': timestamp}


def get(identity):
    with connect() as db:
        row = db.execute('SELECT * FROM comparison_sets WHERE id=?', (identity,)).fetchone()
    if row is None:
        raise NotFoundError('对比记录不存在')
    return {**json.loads(row['result']), 'id': row['id'], 'name': row['name'],
            'notes': row['notes'], 'created_at': row['created_at']}


def list_saved(limit=20, offset=0):
    with connect() as db:
        total = db.execute('SELECT count(*) FROM comparison_sets').fetchone()[0]
        rows = db.execute("SELECT id,name,created_at,baseline_id,json_array_length(result,'$.schemes') AS scheme_count "
                          'FROM comparison_sets ORDER BY created_at DESC,id LIMIT ? OFFSET ?', (limit, offset)).fetchall()
    return {'items': [dict(row) for row in rows], 'total': total, 'limit': limit, 'offset': offset}
