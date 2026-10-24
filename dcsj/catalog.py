"""Read-only, parameterized access to the migrated engineering catalog."""
import json
import sqlite3
import re
from contextlib import contextmanager
from pathlib import Path
from .parameters import input_parameters
from .semantics import electrical, frequency_model
from .catalog_bundle import catalog_path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "data/catalog.sqlite"
from .engine import available_materials


@contextmanager
def connect(path=None):
    db = sqlite3.connect((path or catalog_path()).as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    db.create_function('frame_size', 1, frame_size, deterministic=True)
    try:
        yield db
    finally:
        db.close()


def stats():
    with connect() as db:
        return {
            "model_count": db.execute("SELECT count(*) FROM models WHERE is_primary=1").fetchone()[0],
            "model_version_count": db.execute("SELECT count(*) FROM models").fetchone()[0],
            "material_count": db.execute("SELECT count(*) FROM materials WHERE is_primary=1").fetchone()[0],
        }


def series():
    from . import saved_catalog
    with connect() as db:
        original = [r[0] for r in db.execute(
            "SELECT DISTINCT series FROM models WHERE is_primary=1 ORDER BY series")]
    return sorted(set(original + saved_catalog.series()) - {None, ''})


def frame_size(name):
    match = re.search(r'(?:^|[-_])(\d{2,4})(?=[A-Za-z])', name or '')
    return match[1] if match else None


def models(q="", series_name="", limit=30, offset=0, history=False, *, source='original', **filters):
    from . import saved_catalog
    saved = saved_catalog.search('main', q, limit, offset, series=series_name, **filters) if source != 'original' else {'items': [], 'total': 0}
    items = []
    for row in saved['items']:
        p = input_parameters(json.loads(row.pop('parameters')))
        row.update(project_id=row['id'], id=saved_catalog.reference(row['id'], row['revision']),
                   kind='saved_design', model=row['name'], frame=frame_size(row['model_name']),
                   source='已保存设计', is_primary=True)
        row.update(electrical(p))
        items.append(row)
    original = _original_models(q, series_name, max(0, limit-len(items)), max(0, offset-saved['total']), history,
                                **filters) if source != 'saved' else {'items': [], 'total': 0}
    return {'items': items + original['items'], 'total': saved['total'] + original['total'],
            'offset': offset, 'limit': limit, 'voltage_semantics': 'phase_voltage'}


def _original_models(q="", series_name="", limit=30, offset=0, history=False, *, frequency_hz=None,
           poles=None, voltage_v=None, power_min=None, power_max=None, frame=None, source_table=None):
    conditions, args = [], []
    if not history:
        conditions.append("is_primary=1")
    if q:
        # Treat the user's query literally, including SQL LIKE wildcard characters.
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        conditions.append("model LIKE ? ESCAPE '\\'")
        args.append(f"%{escaped}%")
    if series_name:
        conditions.append("series=?")
        args.append(series_name)
    for column, value in [('frequency_hz', frequency_hz), ('poles', poles), ('voltage_v', voltage_v), ('source_table', source_table)]:
        if value is not None:
            conditions.append(column + '=?')
            args.append(value)
    for operator, value in [('>=', power_min), ('<=', power_max)]:
        if value is not None:
            conditions.append('rated_power_kw' + operator + '?')
            args.append(value)
    if frame is not None:
        conditions.append('frame_size(model)=?')
        args.append(frame)
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    with connect() as db:
        count = db.execute("SELECT count(*) FROM model_catalog" + where, args).fetchone()[0]
        rows = db.execute(
            "SELECT record_id AS id,model,series,rated_power_kw,poles,voltage_v,"
            "frequency_hz,source_file AS source,source_table,row_number,is_primary,raw_json "
            "FROM model_catalog" + where + " ORDER BY series,model,record_id LIMIT ? OFFSET ?",
            [*args, limit, offset],
        ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        p = input_parameters(json.loads(item.pop('raw_json')))
        item.update(electrical({'Ufai': item['voltage_v'], 'Jiefa': p.get('Jiefa')}))
        item['frame'] = frame_size(item['model'])
        items.append(item)
    return {"total": count, "items": items,
            "offset": offset, "limit": limit, "voltage_semantics": "phase_voltage"}


def model(record_id):
    if record_id.startswith('saved-design:'):
        from . import projects, saved_catalog
        identity, revision = saved_catalog.parse_reference(record_id)
        project = projects.get_revision(identity, revision)
        current = projects.get_project(identity)
        project.update(id=identity, archived=current['archived'])
        return {'id': record_id, 'model': project['name'], 'parameters': project['parameters'],
                'inputs': project['parameters'], 'electrical': electrical(project['parameters']),
                'kind': 'saved_design', 'project': project, 'author': project['author'], 'series': project['series'],
                'source': {'description': '已保存设计', 'project_id': identity, 'revision': revision,
                           'author': project['author']}}
    if record_id.startswith('user-model:'):
        from .library import model as user_model
        return user_model(record_id)
    path = catalog_path()
    with connect(path) as db:
        row = db.execute("SELECT * FROM model_catalog WHERE record_id=?", (record_id,)).fetchone()
    if row is None:
        return None
    raw = json.loads(row['raw_json'])
    parameters = input_parameters(raw)
    from .resources import directory
    # Identify the immutable catalog actually read, even if import/activation
    # changes the active pointer while this request is running.
    package_id = path.parent.name if (re.fullmatch(r'[a-f0-9]{64}', path.parent.name)
                                      and path.parent.parent.resolve() == directory().resolve()) else None
    return {
        "id": row["record_id"], "model": row["model"], "series": row["series"],
        "parameters": parameters, 'inputs': parameters, 'electrical': electrical(parameters),
        'resource_package': package_id,
        "raw_parameters": raw,
        "historical_results": json.loads(row["normalized_json"]).get("historical_results", {}),
        "source": {"path": row["source_file"], "table": row["source_table"],
                   "row": row["row_number"], "sha256": row["source_sha256"]},
    }


def lineage(record_id):
    from .storage import NotFoundError
    item = model(record_id)
    if item is None:
        raise NotFoundError('来源型号不存在')
    return lineage_of(item)


def lineage_of(item):
    from .semantics import fingerprint
    return {'id': item['id'], 'model': item['model'], 'source': item['source'],
            'resource_package': item.get('resource_package'),
            'base_input_sha256': fingerprint(item['parameters'])}


def materials():
    kernel_materials = available_materials()
    with connect() as db:
        items = [dict(r) for r in db.execute(
            "SELECT id,name,source_file AS source,bh_point_count AS point_count,"
            "loss_point_count,frequency_hz,frequency_status,loss_unit_status "
            "FROM material_catalog WHERE is_primary=1 ORDER BY name")]
    for item in items:
        item.update(loss_curve_frequency_hz=item['frequency_hz'],
                    loss_frequency_model=frequency_model(50), loss_unit='kernel_native')
        if not item["point_count"] and item["name"] in kernel_materials:
            item["point_count"], item["loss_point_count"] = 49, 42
            item["curve_origin"] = "recovered_embedded_table"
    return items


def material(material_id):
    from .curves import material_plot
    with connect() as db:
        row = db.execute("SELECT * FROM material_catalog WHERE id=?", (material_id,)).fetchone()
        if row is None:
            return None
        points = db.execute(
            "SELECT * FROM curve_points WHERE material_id=? ORDER BY curve_type,b_tesla,point_index",
            (material_id,),
        ).fetchall()
    bh = [{"x": r["b_tesla"], "y": r["h_a_per_m"]} for r in points if r["curve_type"] == "bh"]
    loss = [{"x": r["b_tesla"], "y": r["loss_value_source"]} for r in points if r["curve_type"] == "loss"]
    warnings = []
    if not bh:
        recovered = available_materials().get(row["name"])
        if recovered:
            # ATB / PFB use a shared piecewise uniform induction axis (kG → T).
            bh_axis = [0.5 + i * .05 for i in range(15)] + [1.22 + i * .02 for i in range(34)]
            loss_axis = [0.5 + i * .1 for i in range(8)] + [1.22 + i * .02 for i in range(34)]
            bh = [{"x": round(x, 2), "y": y * 100}
                  for x, y in zip(bh_axis, recovered["H_values"])]
            loss = [{"x": round(x, 2), "y": y}
                    for x, y in zip(loss_axis, recovered["P_values"])]
            warnings.append("曲线从原程序内置常量恢复；H 由 A/cm 换算为 A/m。")
        else:
            warnings.append("未恢复此牌号的数值曲线。")
    if loss:
        warnings.append("损耗曲线保留原表数值；原标注单位存在疑问，尚未直接换算为 W/kg 或 W/m³。")
    return {
        "id": row["id"], "name": row["name"], "warnings": warnings,
        "source": {"path": row["source_file"], "table": row["source_table"],
                   "sha256": row["source_sha256"]},
        "frequency_hz": row["frequency_hz"], "frequency_status": row["frequency_status"],
        'loss_curve_frequency_hz': row['frequency_hz'], 'loss_frequency_model': frequency_model(50),
        'loss_unit': 'kernel_native',
        "curves": [
            {"id": "bh", "title": "磁化曲线", "x_label": "磁密 B (T)",
             "y_label": "磁场强度 H (A/m)", "series": [{"name": row["name"], "points": bh}],
             "plot": material_plot(bh, 'bh', legacy_origin=True)},
            {"id": "loss", "title": "原始损耗曲线", "x_label": "磁密 B (T)",
             "y_label": "原表损耗数值（单位待核实）", "series": [{"name": row["name"], "points": loss}],
             "plot": material_plot(loss, 'loss', legacy_origin=True)},
        ],
    }
