"""Search lightweight saved-design metadata before applying the page limit."""
import json
import math
import re
import unicodedata

from .storage import connect
from .design_history import MAIN_OWNER, TX_OWNER


def _rating_number(value):
    """Read legacy decimal strings without changing the saved input snapshot."""
    if isinstance(value, str):
        value = value.strip()
        if not re.fullmatch(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?', value):
            return None
        value = float(value)
    try:
        return value if type(value) in (int, float) and math.isfinite(value) else None
    except OverflowError:
        return None


def _ratings(raw, module):
    try:
        parameters = json.loads(raw or '{}')
    except (TypeError, ValueError):
        parameters = {}
    if not isinstance(parameters, dict):
        parameters = {}
    keys = ('kW', 'Ufai', 'f') if module == 'main' else ('P2', 'U1', 'HZ')
    result = {}
    for field, key in zip(('power_kw', 'voltage_v', 'frequency_hz'), keys):
        result[field] = _rating_number(parameters.get(key))
    return result


def search(module, *, q='', series='', owner=None, include_archived=False, limit=100, offset=0):
    table, ownership = ('projects', MAIN_OWNER) if module == 'main' else ('tx_designs', TX_OWNER)
    columns = 'id,name,author,series,revision,created_at,updated_at'
    if module == 'main':
        columns += ',archived'
    base = f'SELECT {columns},{ownership} AS owner FROM {table}'
    if module == 'main' and not include_archived:
        base += ' WHERE archived=0'
    normalize = lambda value: unicodedata.normalize('NFKC', value or '').casefold()
    conditions, args = [], []
    for word in normalize(q).split():
        conditions.append("instr(design_search(name||' '||coalesce(series,'')||' '||coalesce(author,'')||' '||id),?)>0")
        args.append(word)
    if series:
        conditions.append('series=?')
        args.append(series)
    if owner is not None:
        conditions.append('owner=?')
        args.append(owner)
    clause = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
    with connect() as db:
        db.create_function('design_search', 1, normalize, deterministic=True)
        prefix = f'WITH designs AS ({base}) '
        total = db.execute(prefix + 'SELECT count(*) FROM designs' + clause, args).fetchone()[0]
        # A filter or deletion can move the last page; return the nearest existing page.
        offset = min(offset, max(0, (total - 1) // limit * limit))
        parameters = '(SELECT parameters FROM projects p WHERE p.id=designs.id)' if module == 'main' else (
            '(SELECT parameters FROM tx_revisions r WHERE r.design_id=designs.id AND r.revision=designs.revision)')
        items = [dict(row) for row in db.execute(prefix + f'SELECT *,{parameters} AS rating_parameters FROM designs' + clause +
                  ' ORDER BY updated_at DESC,id LIMIT ? OFFSET ?', [*args, limit, offset])]
        for item in items:
            item.update(_ratings(item.pop('rating_parameters'), module))
        series_names = [row[0] for row in db.execute(prefix +
                       "SELECT DISTINCT series FROM designs WHERE coalesce(series,'')<>'' ORDER BY series")]
    return {'items': items, 'total': total, 'offset': offset, 'limit': limit, 'series': series_names}
