"""Search saved designs without copying them into immutable resource catalogs."""
from .storage import connect
import unicodedata


def series_name(value):
    if value is None:
        return None
    value = value.strip()
    if len(value) > 80 or any(unicodedata.category(c).startswith('C') for c in value):
        raise ValueError('系列名不能超过 80 字，不能包含换行或控制字符')
    return value


def series():
    with connect() as db:
        return [row[0] for row in db.execute("SELECT series FROM projects WHERE series<>'' "
                    "UNION SELECT series FROM tx_designs WHERE series<>'' ORDER BY series")]


def search(module, query='', limit=30, offset=0, **filters):
    # All identifiers and JSON paths below are application constants.
    if module == 'main':
        select = """SELECT id, name, author, revision, updated_at, parameters,
            json_extract(parameters,'$.SizeX') AS model_name,
            json_extract(parameters,'$.kW') AS rated_power_kw,
            json_extract(parameters,'$.p') AS poles,
            json_extract(parameters,'$.f') AS frequency_hz,
            json_extract(parameters,'$.Ufai') AS voltage_v,
            series, 'saved-design' AS source_table
            FROM projects WHERE archived=0"""
    else:
        select = """SELECT d.id, d.name, d.author, d.series, d.revision, d.updated_at, r.parameters,
            json_extract(r.parameters,'$.TYP') AS model_name
            FROM tx_designs d JOIN tx_revisions r ON r.design_id=d.id AND r.revision=d.revision"""
    conditions, args = [], []
    if query:
        escaped = query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        conditions.append("(name LIKE ? ESCAPE '\\' OR model_name LIKE ? ESCAPE '\\' OR author LIKE ? ESCAPE '\\' OR series LIKE ? ESCAPE '\\')")
        args.extend([f'%{escaped}%'] * 4)
    for column in ('series', 'frequency_hz', 'poles', 'voltage_v', 'source_table'):
        value = filters.get(column)
        if value is not None and value != '':
            conditions.append(column + '=?')
            args.append(value)
    for key, operator in [('power_min', '>='), ('power_max', '<=')]:
        if filters.get(key) is not None:
            conditions.append('rated_power_kw' + operator + '?')
            args.append(filters[key])
    if filters.get('frame') is not None:
        conditions.append('frame_size(model_name)=?')
        args.append(filters['frame'])
    where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
    with connect() as db:
        from .catalog import frame_size
        db.create_function('frame_size', 1, frame_size, deterministic=True)
        total = db.execute('SELECT count(*) FROM (' + select + ')' + where, args).fetchone()[0]
        rows = db.execute('SELECT * FROM (' + select + ')' + where + ' ORDER BY updated_at DESC,id LIMIT ? OFFSET ?',
                          [*args, limit, offset]).fetchall()
    return {'total': total, 'items': [dict(row) for row in rows]}


def reference(identity, revision):
    return f'saved-design:{identity}@{revision}'


def parse_reference(value):
    from .storage import NotFoundError
    try:
        identity, revision = value.removeprefix('saved-design:').rsplit('@', 1)
        if not identity or int(revision) < 1:
            raise ValueError
        return identity, int(revision)
    except ValueError:
        raise NotFoundError('保存的设计版本标识无效') from None
