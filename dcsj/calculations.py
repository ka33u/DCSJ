"""Immutable calculation snapshots shared by comparisons, reports and history."""
import json
import uuid

from .parameters import field_info, validate, input_parameters
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
            if validate(input_parameters(json.loads(version['parameters']))) != result['inputs']:
                raise ConflictError("本次输入与关联设计版本不同，请先保存新版本，或不关联版本进行计算。")
        db.execute("INSERT INTO calculations(id,name,project_id,project_revision,parameters,result,created_at) VALUES (?,?,?,?,?,?,?)",
                   (identity, name, project_id, project_revision, dumps(result["inputs"]), dumps(result), timestamp))
        audit(db, "calculation.completed", identity, {"project_id": project_id,
              "project_revision": project_revision, "input_sha256": result["provenance"]["input_sha256"]})
    return {**result, "calculation_id": identity, "name": name, "created_at": timestamp,
            "project_id": project_id, "project_revision": project_revision, 'persisted': True, 'archived': False}


def get(identity):
    with connect() as db:
        row = db.execute("SELECT * FROM calculations WHERE id=?", (identity,)).fetchone()
    if row is None:
        raise NotFoundError("计算记录不存在")
    return {**json.loads(row["result"]), "calculation_id": row["id"], "name": row["name"],
            "created_at": row["created_at"], "project_id": row["project_id"],
            "project_revision": row["project_revision"], 'persisted': True, 'archived': bool(row['archived'])}


def list_calculations(limit=30, offset=0, project_id=None, include_archived=False):
    conditions, args = ([] if include_archived else ['archived=0']), []
    if project_id:
        conditions.append('project_id=?')
        args.append(project_id)
    where = 'WHERE ' + ' AND '.join(conditions) if conditions else ''
    with connect() as db:
        count = db.execute("SELECT count(*) FROM calculations " + where, args).fetchone()[0]
        rows = db.execute("SELECT id,name,project_id,project_revision,created_at,archived,"
                          "json_extract(parameters,'$.kW') AS power_kw,"
                          "json_extract(result,'$.values.ATA1') AS efficiency,"
                          "json_extract(result,'$.values.CostFx') AS material_cost "
                          "FROM calculations " + where + " ORDER BY created_at DESC,id LIMIT ? OFFSET ?",
                          [*args, limit, offset]).fetchall()
    return {"total": count, "items": [dict(r) for r in rows], "limit": limit, "offset": offset}


def archive(identity, archived=True):
    with connect() as db:
        if not db.execute('UPDATE calculations SET archived=? WHERE id=?', (int(archived), identity)).rowcount:
            raise NotFoundError('计算记录不存在')
        audit(db, 'calculation.archived' if archived else 'calculation.restored', identity)
    return get(identity)


def _deletable(db, identity):
    if not db.execute('SELECT 1 FROM calculations WHERE id=?', (identity,)).fetchone():
        raise NotFoundError('计算记录不存在')
    if db.execute('SELECT 1 FROM optimization_candidates WHERE calculation_id=? LIMIT 1', (identity,)).fetchone():
        raise ConflictError('计算记录被寻优任务引用，可归档但不能删除')
    if db.execute("SELECT 1 FROM calculations WHERE id=? AND json_extract(result,'$.provenance.optimization_job_id') IS NOT NULL",
                  (identity,)).fetchone():
        raise ConflictError('计算记录属于寻优任务，可归档但不能删除')
    if db.execute("SELECT 1 FROM comparison_sets, json_each(comparison_sets.result,'$.schemes') s "
                  "WHERE json_extract(s.value,'$.id')=? OR baseline_id=? LIMIT 1", (identity, identity)).fetchone():
        raise ConflictError('计算记录被保存的对比引用，可归档但不能删除')


def delete(identity):
    """Delete only unreferenced snapshots; preserve comparison and job lineage."""
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        _deletable(db, identity)
        db.execute('DELETE FROM calculations WHERE id=?', (identity,))
        audit(db, 'calculation.deleted', identity)


def cleanup(name_prefix=None, before=None, after=None, action='archive', dry_run=True, limit=100):
    conditions, args = [], []
    if name_prefix:
        conditions.append('substr(name,1,?)=?')
        args += [len(name_prefix), name_prefix]
    for operator, value in [('<', before), ('>=', after)]:
        if value:
            conditions.append('julianday(created_at)' + operator + 'julianday(?)')
            args.append(value)
    if not conditions or action not in {'archive', 'delete'}:
        raise ValueError('清理必须指定名称前缀或时间条件，以及 archive/delete 动作')
    where = ' AND '.join(conditions) + (' AND archived=0' if action == 'archive' else '')
    eligible, skipped = [], []
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        total = db.execute('SELECT count(*) FROM calculations WHERE ' + where, args).fetchone()[0]
        rows = db.execute('SELECT id FROM calculations WHERE ' + where + ' ORDER BY created_at,id LIMIT ?', [*args, limit]).fetchall()
        for row in rows:
            identity = row['id']
            try:
                if action == 'delete':
                    _deletable(db, identity)
                eligible.append(identity)
                if not dry_run:
                    if action == 'delete':
                        db.execute('DELETE FROM calculations WHERE id=?', (identity,))
                    else:
                        db.execute('UPDATE calculations SET archived=1 WHERE id=?', (identity,))
                    audit(db, 'calculation.deleted' if action == 'delete' else 'calculation.archived', identity)
            except ConflictError as error:
                skipped.append({'id': identity, 'reason': str(error)})
    return {'dry_run': dry_run, 'action': action, 'matched': total, 'examined': len(rows),
            'eligible_ids': eligible, 'changed': 0 if dry_run else len(eligible), 'skipped': skipped,
            'truncated': total > len(rows)}


def compare(identities, baseline_id=None):
    if not 2 <= len(identities) <= 8 or len(set(identities)) != len(identities):
        raise ValueError("请选择 2 至 8 个不同的计算方案")
    baseline_id = baseline_id or identities[0]
    if baseline_id not in identities:
        raise ValueError("基准方案必须在对比列表中")
    snapshots = [get(identity) for identity in identities]
    baseline = next(r for r in snapshots if r["calculation_id"] == baseline_id)
    metrics, differences, warnings = [], [], []
    keys = dict.fromkeys(key for snapshot in snapshots for key in snapshot['values'])
    for key in keys:
        base = baseline['values'].get(key)
        cells = []
        for snapshot in snapshots:
            value = snapshot["values"].get(key)
            delta = None if value is None or base is None else value - base
            cells.append({"id": snapshot["calculation_id"], "value": value, "delta": delta,
                          "delta_percent": delta / abs(base) * 100 if delta is not None and base else None})
        metrics.append({**field_info(key), "cells": cells})
    if any(set(snapshot['values']) != set(baseline['values']) for snapshot in snapshots):
        warnings.append('部分方案缺少计算指标；缺失值保留为空，基准或候选缺值时不计算差值。')
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
    material_versions = {}
    for snapshot in snapshots:
        material_versions.setdefault(snapshot['inputs'].get('Paihao'), set()).add(
            snapshot.get('provenance', {}).get('material_sha256'))
    for name, versions in material_versions.items():
        if len(versions) > 1 or None in versions:
            warnings.append(f"同牌号材料 {name} 数据版本不同或未记录，请确认材料版本后再作同条件比较。")
    return {"baseline_id": baseline_id,
            "schemes": [{"id": s["calculation_id"], "name": s["name"], "created_at": s["created_at"],
                         "warnings": list(dict.fromkeys(s.get('warnings', []) + s.get('engine', {}).get('limitations', []))),
                         "provenance": s["provenance"]} for s in snapshots],
            "metrics": metrics, "parameter_differences": differences, "warnings": warnings}
