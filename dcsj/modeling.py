"""Template-led modeling with explicit changes and a dimensional preview."""
from html import escape
import json
import math

from . import catalog
from .parameters import InputError, field_info, input_parameters, validate
from .storage import NotFoundError

QUICK_FIELDS = {"SizeX", "kW", "f", "Ufai", "Jiefa", "Paihao", "D1", "Di1", "Di2",
                "g", "L", "Q1", "Q2", "ZZ", "a", "JJ", "Djj", "MM", "Dmm"}


def templates(power_kw, poles, frequency_hz=50, phase_voltage=380, limit=12):
    with catalog.connect() as db:
        rows = db.execute(
            "SELECT record_id,model,series,rated_power_kw,poles,frequency_hz,voltage_v,raw_json,source_file "
            "FROM model_catalog WHERE is_primary=1 AND poles=? AND rated_power_kw>0", (poles,)).fetchall()
    candidates = []
    for row in rows:
        frequency = row["frequency_hz"] or 0
        voltage = row["voltage_v"] or 0
        score = abs(math.log(row["rated_power_kw"] / power_kw))
        score += abs(frequency - frequency_hz) / frequency_hz
        score += .25 * abs(voltage - phase_voltage) / phase_voltage
        candidates.append((score, row))
    found = []
    for score, row in sorted(candidates, key=lambda pair: (pair[0], pair[1]["record_id"])):
        try:
            validate(input_parameters(json.loads(row["raw_json"])))
        except InputError:
            continue
        found.append({"id": row["record_id"], "model": row["model"], "series": row["series"],
                      "power_kw": row["rated_power_kw"], "poles": row["poles"],
                      "frequency_hz": row["frequency_hz"], "phase_voltage": row["voltage_v"],
                      "source": row["source_file"], "distance": round(score, 5),
                      "power_difference_percent": (row["rated_power_kw"] / power_kw - 1) * 100})
        if len(found) == limit:
            break
    return {"items": found, "method": "按极数筛选，按功率、频率及相电压接近程度排序",
            "warnings": ["模板用于建立设计起点，额定值接近不代表几何、温升或机械性能已经符合要求。"]}


def build(model_id, changes, rotor_outer_mm=None):
    original = catalog.model(model_id)
    if original is None:
        raise NotFoundError("模板型号不存在")
    unknown = set(changes) - QUICK_FIELDS
    if unknown:
        raise InputError([{"field": key, "message": "此字段请在完整参数编辑器中修改"} for key in sorted(unknown)])
    before = validate(original["parameters"])
    candidate = {**before, **changes}
    linkages = []
    if rotor_outer_mm is not None:
        if "g" in changes:
            raise InputError([{"field": "g", "message": "转子外径联动与直接指定气隙不能同时使用"}])
        dimensions = {"Di1": candidate["Di1"], "rotor_outer_mm": rotor_outer_mm}
        for key, raw in dimensions.items():
            try:
                value = float(raw)
                if isinstance(raw, bool) or not math.isfinite(value) or not 0 < value <= 1e9:
                    raise ValueError
                dimensions[key] = value
            except (ValueError, TypeError):
                raise InputError([{"field": key, "message": "尺寸必须是大于 0 且不超过 10⁹ 的有限数值"}]) from None
        candidate["g"] = (dimensions["Di1"] - dimensions["rotor_outer_mm"]) / 2
        linkages.append({"target": "g", "formula": "g = (Di1 - D2) / 2",
                         "inputs": {"Di1": dimensions["Di1"], "D2": dimensions["rotor_outer_mm"]},
                         "value": candidate["g"], "unit": "mm"})
    after = validate(candidate)
    differences = [{**field_info(key), "before": before[key], "after": after[key]}
                   for key in sorted(QUICK_FIELDS) if after[key] != before[key]]
    warnings = ["本次保留了模板的槽形、绕组等完整输入；任何改动都需要重新计算并核验。"]
    if any(key in changes and after[key] != before[key] for key in ("kW", "f", "Ufai", "Jiefa")):
        warnings.append("额定条件已变更，绕组与几何尺寸未作未经验证的自动推算。")
    return {"name": after["SizeX"], "parameters": after, "source": original["source"],
            "template_id": model_id, "changes": differences, "linkages": linkages, "warnings": warnings,
            "geometry": geometry(after)}


def geometry(parameters):
    p = validate(parameters)
    outer, bore = p["D1"] / 2, p["Di1"] / 2
    rotor, shaft = bore - p["g"], p["Di2"] / 2
    scale = 135 / outer
    # Slot locations are drawn schematically. Their widths/profile are not CAD geometry.
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 520 370" role="img">',
        f'<title>{escape(p["SizeX"])} 铁心尺寸预览</title>',
        '<rect width="520" height="370" fill="white"/>',
        '<g transform="translate(180 175)">',
    ]
    for radius, fill, stroke in [(outer, "#dfe8ed", "#657b87"), (bore, "#fff", "#657b87"),
                                  (rotor, "#d8e9df", "#2e7960"), (shaft, "#fff", "#2e7960")]:
        parts.append(f'<circle r="{radius * scale:.5f}" fill="{fill}" stroke="{stroke}" stroke-width="1.2"/>')
    for count, radius, depth, color in [
        (int(p["Q1"]), bore, min(p["hs0"] + p["hs12"], outer - bore) * .8, "#647c9c"),
        (int(p["Q2"]), rotor, -min(p["hr12"] + p["hr3"], rotor - shaft) * .65, "#b77c42"),
    ]:
        # Display bounded marks even if a malformed design uses a huge count.
        if count > 240:
            continue
        for index in range(count):
            angle = 2 * math.pi * index / count
            x1, y1 = radius * scale * math.cos(angle), radius * scale * math.sin(angle)
            x2, y2 = (radius + depth) * scale * math.cos(angle), (radius + depth) * scale * math.sin(angle)
            parts.append(f'<line x1="{x1:.5f}" y1="{y1:.5f}" x2="{x2:.5f}" y2="{y2:.5f}" '
                         f'stroke="{color}" stroke-width="2"/>')
    parts.append('</g><g fill="#253d4d" font-family="sans-serif" font-size="13">')
    labels = [("D1", p["D1"]), ("Di1", p["Di1"]), ("D2", rotor * 2), ("Di2", p["Di2"]),
              ("g", p["g"]), ("L", p["L"]), ("Q1", p["Q1"]), ("Q2", p["Q2"])]
    for i, (key, value) in enumerate(labels):
        suffix = "" if key.startswith("Q") else " mm"
        parts.append(f'<text x="340" y="{70 + i * 27}">{key} = {value:g}{suffix}</text>')
    parts.append('<text x="25" y="340">尺寸按比例；槽位为示意，不能作为加工图。</text></g></svg>')
    return {"svg": "".join(parts),
            "dimensions": {"stator_outer_mm": outer * 2, "stator_bore_mm": bore * 2,
                           "rotor_outer_mm": rotor * 2, "shaft_bore_mm": shaft * 2,
                           "gap_mm": p["g"], "stack_length_mm": p["L"]},
            "warnings": ["预览展示铁心外形及槽位，槽形截面、端部和公差需专门的机械设计。"]}
