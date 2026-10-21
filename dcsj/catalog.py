"""Read-only, parameterized access to the migrated engineering catalog."""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from .parameters import normalize_keys
from .catalog_bundle import catalog_path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "data/catalog.sqlite"
from .engine import available_materials


@contextmanager
def connect():
    db = sqlite3.connect(catalog_path().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
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
    with connect() as db:
        return [r[0] for r in db.execute(
            "SELECT DISTINCT series FROM models WHERE is_primary=1 ORDER BY series")]


def models(q="", series_name="", limit=30, offset=0, history=False):
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
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    with connect() as db:
        count = db.execute("SELECT count(*) FROM model_catalog" + where, args).fetchone()[0]
        rows = db.execute(
            "SELECT record_id AS id,model,series,rated_power_kw,poles,voltage_v,"
            "frequency_hz,source_file AS source,source_table,row_number,is_primary "
            "FROM model_catalog" + where + " ORDER BY series,model,record_id LIMIT ? OFFSET ?",
            [*args, limit, offset],
        ).fetchall()
    return {"total": count, "items": [dict(r) for r in rows],
            "offset": offset, "limit": limit, "voltage_semantics": "phase_voltage"}


def model(record_id):
    if record_id.startswith('user-model:'):
        from .library import model as user_model
        return user_model(record_id)
    with connect() as db:
        row = db.execute("SELECT * FROM model_catalog WHERE record_id=?", (record_id,)).fetchone()
    if row is None:
        return None
    return {
        "id": row["record_id"], "model": row["model"], "series": row["series"],
        "parameters": normalize_keys(json.loads(row["raw_json"])),
        "raw_parameters": json.loads(row["raw_json"]),
        "historical_results": json.loads(row["normalized_json"]).get("historical_results", {}),
        "source": {"path": row["source_file"], "table": row["source_table"],
                   "row": row["row_number"], "sha256": row["source_sha256"]},
    }


def materials():
    kernel_materials = available_materials()
    with connect() as db:
        items = [dict(r) for r in db.execute(
            "SELECT id,name,source_file AS source,bh_point_count AS point_count,"
            "loss_point_count,frequency_hz,frequency_status,loss_unit_status "
            "FROM material_catalog WHERE is_primary=1 ORDER BY name")]
    for item in items:
        if not item["point_count"] and item["name"] in kernel_materials:
            item["point_count"], item["loss_point_count"] = 49, 42
            item["curve_origin"] = "recovered_embedded_table"
    return items


def material(material_id):
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
        "curves": [
            {"id": "bh", "title": "磁化曲线", "x_label": "磁密 B (T)",
             "y_label": "磁场强度 H (A/m)", "series": [{"name": row["name"], "points": bh}]},
            {"id": "loss", "title": "原始损耗曲线", "x_label": "磁密 B (T)",
             "y_label": "原表损耗数值（单位待核实）", "series": [{"name": row["name"], "points": loss}]},
        ],
    }
