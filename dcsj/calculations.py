"""Immutable calculation snapshots shared by comparisons, reports and history."""
import json
import uuid

from .parameters import field_info, validate
from .storage import ConflictError, NotFoundError, audit, connect, dumps, now


def save(result, name=None, project_id=None, project_revision=None, *, database=None):
    identity, timestamp = str(uuid.uuid4()), now()
    name = name or str(result["inputs"]["SizeX"])
    with connect(database) as db:
        if project_id is not None:
            version = db.execute("SELECT parameters FROM project_revisions WHERE project_id=? AND revision=?",
                                 (project_id, project_revision)).fetchone()
            if version is None:
                raise NotFoundError("关联的设计版本不存在")
            if validate(json.loads(version['parameters'])) != result['inputs']:
                raise ConflictError("本次输入与关联设计版本不同，请先保存新版本，或不关联版本进行计算。")
        db.execute("INSERT INTO calculations VALUES (?,?,?,?,?,?,?)",
                   (identity, name, project_id, project_revision, dumps(result["inputs"]), dumps(result), timestamp))
        audit(db, "calculation.completed", identity, {"project_id": project_id,
              "project_revision": project_revision, "input_sha256": result["provenance"]["input_sha256"]})
    return {**result, "calculation_id": identity, "name": name, "created_at": timestamp,
            "project_id": project_id, "project_revision": project_revision}


def get(identity):
    with connect() as db:
        row = db.execute("SELECT * FROM calculations WHERE id=?", (identity,)).fetchone()
    if row is None:
        raise NotFoundError("计算记录不存在")
    return {**json.loads(row["result"]), "calculation_id": row["id"], "name": row["name"],
            "created_at": row["created_at"], "project_id": row["project_id"],
            "project_revision": row["project_revision"]}


def list_calculations(limit=30, offset=0, project_id=None):
    where, args = ("WHERE project_id=?", [project_id]) if project_id else ("", [])
    with connect() as db:
        count = db.execute("SELECT count(*) FROM calculations " + where, args).fetchone()[0]
        rows = db.execute("SELECT id,name,project_id,project_revision,created_at,"
                          "json_extract(parameters,'$.kW') AS power_kw,"
                          "json_extract(result,'$.values.ATA1') AS efficiency,"
                          "json_extract(result,'$.values.CostFx') AS material_cost "
                          "FROM calculations " + where + " ORDER BY created_at DESC,id LIMIT ? OFFSET ?",
                          [*args, limit, offset]).fetchall()
    return {"total": count, "items": [dict(r) for r in rows], "limit": limit, "offset": offset}


def compare(identities, baseline_id=None):
    if not 2 <= len(identities) <= 8 or len(set(identities)) != len(identities):
        raise ValueError("请选择 2 至 8 个不同的计算方案")
    baseline_id = baseline_id or identities[0]
    if baseline_id not in identities:
        raise ValueError("基准方案必须在对比列表中")
    snapshots = [get(identity) for identity in identities]
    baseline = next(r for r in snapshots if r["calculation_id"] == baseline_id)
    metrics, differences, warnings = [], [], []
    for key, base in baseline["values"].items():
        cells = []
        for snapshot in snapshots:
            value = snapshot["values"].get(key)
            delta = None if value is None else value - base
            cells.append({"id": snapshot["calculation_id"], "value": value, "delta": delta,
                          "delta_percent": delta / abs(base) * 100 if delta is not None and base else None})
        metrics.append({**field_info(key), "cells": cells})
    keys = sorted(set().union(*(s["inputs"] for s in snapshots)))
    for key in keys:
        cells = [{"id": s["calculation_id"], "value": s["inputs"].get(key)} for s in snapshots]
        if any(c["value"] != cells[0]["value"] for c in cells[1:]):
            differences.append({**field_info(key), "cells": cells})
    for key in ("kW", "p", "f", "Ufai", "Jiefa"):
        if any(s["inputs"].get(key) != baseline["inputs"].get(key) for s in snapshots):
            warnings.append(f"额定条件 {field_info(key)['label']} 不同，效率、成本及电流等结果不能作为同工况优劣结论。")
    fingerprints = {tuple(s.get("provenance", {}).get(key) for key in ("kernel_sha256", "wrapper_sha256")) for s in snapshots}
    if len(fingerprints) > 1 or any(None in fingerprint for fingerprint in fingerprints):
        warnings.append("计算核心版本不同或早期记录未保存版本指纹，建议统一重算后比较。")
    return {"baseline_id": baseline_id,
            "schemes": [{"id": s["calculation_id"], "name": s["name"], "created_at": s["created_at"],
                         "warnings": s["warnings"], "provenance": s["provenance"]} for s in snapshots],
            "metrics": metrics, "parameter_differences": differences, "warnings": warnings}
