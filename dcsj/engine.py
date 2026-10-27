"""Typed engineering boundary around the recovered numerical program.

The program runs from fresh memory for every request. Original database result
columns are never read by this computation.
"""
import hashlib
import json
import math
import struct
import time
from pathlib import Path
from functools import lru_cache
from . import __version__

from .numeric_vm import NumericKernelError, run
from .parameters import ELEMENT_KEYS, INPUT_MAP, OUTPUT_MAP, ROOT, field_info, validate
from .curves import trend_points, slip_plot
from . import semantics, wire_selection

_material_path = ROOT / "data/material_kernel_tables.json"
MATERIALS = {item["name"]: item for item in json.loads(_material_path.read_text())["materials"]} if _material_path.is_file() else {}
ENGINE_NAME = "DCSJ Python 数值核心"
_kernel_path = ROOT / "dcsj/generated/numeric_kernel.json.gz"
KERNEL_SHA256 = hashlib.sha256(_kernel_path.read_bytes()).hexdigest() if _kernel_path.is_file() else None
WRAPPER_SHA256 = hashlib.sha256(b"".join((ROOT / "dcsj" / name).read_bytes()
    for name in ("engine.py", "numeric_vm.py", "parameters.py", "kernel_layout.py", "library.py", "curves.py", "semantics.py", "wire_selection.py", "torque_refinement.py", "continuous_saturation.py", "cost_compatibility.py", "legacy_output.py"))).hexdigest()
LIMITATIONS = [
    "由原 Fortran 程序的计算指令恢复；使用双精度中间运算和原单精度存储。",
    "已恢复三相异步电动机计算；历史型号保存值可能使用不同版本的材料表。",
    "材料损耗表保留原数值语义，原表标注单位仍需工程核验。",
]


class CalculationError(ValueError):
    pass


class CalculationUnavailableError(RuntimeError):
    pass


@lru_cache(maxsize=4)
def _package_context(identity, root):
    path = Path(root)
    material_data = json.loads((path / 'material_kernel_tables.json').read_text(encoding='utf-8'))
    manifest = json.loads((path / 'manifest.json').read_text(encoding='utf-8'))
    return {'materials': {item['name']: item for item in material_data['materials']},
            'kernel_sha256': manifest['files']['numeric_kernel.json.gz']['sha256'],
            'kernel_path': path / 'numeric_kernel.json.gz', 'package_id': identity,
            'package_version': manifest['version']}


def resource_context():
    from . import resources
    package = resources.current()
    if package:
        return _package_context(package['package_id'], str(resources.directory() / package['package_id']))
    return {'materials': MATERIALS, 'kernel_sha256': KERNEL_SHA256, 'kernel_path': _kernel_path,
            'package_id': None, 'package_version': 'legacy-local'}


def available_materials():
    return resource_context()['materials']


def engine_status(context=None):
    context = context or resource_context()
    missing = []
    if not context['kernel_sha256']:
        missing.append("dcsj/generated/numeric_kernel.json.gz")
    if not context['materials']:
        missing.append("data/material_kernel_tables.json")
    return {"name": ENGINE_NAME, "status": "unavailable" if missing else "recovered",
            "missing_assets": missing,
            "version": __version__, "kernel_sha256": context['kernel_sha256'],
            "resource_package": context['package_id'], "resource_version": context['package_version'],
            "implementation": "pure-python-numeric-ir", "limitations": LIMITATIONS,
            "native_runtime_required": False}


def f32(value):
    return struct.unpack("<f", struct.pack("<f", value))[0]


def prepare_memory(parameters, context=None):
    p = parameters
    context = context or resource_context()
    material = context['materials'].get(p["Paihao"])
    if p['Paihao'].startswith('user-material:'):
        from .library import calculation_material
        material = calculation_material(p['Paihao'], p['f'], context['materials'])
    if material is None:
        raise CalculationError(f"未知硅钢片牌号 {p['Paihao']}；请从材料库选择已恢复的牌号。")
    memory = {addr: f32(p[key]) for key, addr in INPUT_MAP.items() if key != "Paihao"}
    memory[INPUT_MAP["Paihao"]] = float(material["slot"])
    # MAIN normalizes absolute mechanical/stray loss before the selected entry.
    for key, resolved in semantics.losses(p).items():
        memory[INPUT_MAP[key]] = resolved['kernel_pu']
    offset = 0
    for address, count in [(0x4B0740, 142), (0x4B05C0, 96),
                           (0x4AFEA0, 245), (0x4B0274, 210)]:
        memory[address] = struct.pack(f"<{count}f", *material["const_values"][offset:offset + count])
        offset += count
    # DD[0] is INTEGER*4; DD[1:6] are REAL*4, including CORR.
    for address, value in zip(
        [0x4AA29C, 0x4AA298, 0x4AA294, 0x4AA290, 0x4AA28C, 0x4AA288],
        [0, -1 if p["_load_curve"] else 1, 1, -1 if p["_curve"] else 1, 0, p["CORR"]],
    ):
        memory[address] = float(value)
    memory[0x4AA29C] = struct.pack("<i", 0)
    for index, key in enumerate(ELEMENT_KEYS):
        memory[0x4AFCC0 + index * 4] = f32(p[key])
    memory[0x4AFE90] = p["SizeX"].encode("ascii", errors="replace")[:12].ljust(12, b" ")
    memory[0x4AFE9C] = b"Y" if p["Jiefa"] == "Y" else b"$"
    memory[0x4AFE9D] = p["Jueyuan"].encode("ascii", errors="replace")[:1]
    return memory, material


def extract_curves(result, parameters, warnings):
    curves = []
    if parameters["_curve"]:
        rows = result["outputs"].get("10", [])
        if len(rows) % 3 or not all(math.isfinite(v) for v in rows):
            raise CalculationError("转矩电流曲线包含不完整或非有限数值")
        if len(rows) != 153:
            warnings.append(f"转矩电流曲线仅收敛 {len(rows) // 3}/51 个采样点。")
        points = [rows[i:i + 3] for i in range(0, len(rows), 3)]
        curves.append({
            "id": "slip", "title": "转差率特性", "x_label": "转差率 s",
            "y_label": "相对额定值（倍）",
            "series": [{"name": name, "points": [{"x": r[0], "y": r[col]} for r in points]}
                       for name, col in [("电流 / 额定相电流", 1), ("转矩 / 额定转矩", 2)]],
            'plot': slip_plot(points),
        })
    if parameters["_load_curve"]:
        memory = result["memory"]
        arrays = {key: [memory.f32(address + i * 4) for i in range(9)]
                  for key, address in {
                      "load": 0x4AB89C, "efficiency": 0x4AB830, "pf": 0x4AB80C,
                      "current": 0x4AB7C4, "slip": 0x4AB7A0, "input_power": 0x4AB854,
                  }.items()}
        if not all(math.isfinite(x) for values in arrays.values() for x in values):
            raise CalculationError("负荷特性计算包含非有限数值")
        # Match the decimal input precision of the original graph's text reader.
        precision = {'load': 2, 'input_power': 2, 'current': 2, 'slip': 4, 'pf': 4, 'efficiency': 4}
        display_arrays = {key: [round(v, precision[key]) for v in values] for key, values in arrays.items()}
        specs = [('input_power', '输入功率 P1 (kW)', 'P1', '#db00db',
                  math.floor(min(display_arrays['input_power']) / 3), math.ceil(max(display_arrays['input_power']) * 1.5)),
                 ('slip', '转差率 Sn', 'Sn', '#168016', 0, math.ceil(max(display_arrays['slip']) * 300) / 100),
                 ('current', '相电流 I1 (A)', 'I1', '#0000cc', 0, math.ceil(max(display_arrays['current']) * 2)),
                 ('pf', '功率因数 cosφ', 'cosφ', '#e00000', 0, 1),
                 ('efficiency', '效率 η', 'η', '#222222', 0, 1)]
        curves.append({
            'id': 'load_characteristic', 'title': '负荷特性（旧版默认趋势线）',
            'x_label': '负荷 / 额定功率', 'y_label': '数值（单位见曲线名）',
            'series': [{'name': label, 'points': [{'x': x, 'y': y} for x, y in zip(arrays['load'], arrays[key])]}
                       for key, label, *_ in specs],
            'plot': {'x_min': 20, 'x_max': 150, 'x_label': 'P2 / Pn (%)', 'aspect_ratio': 1, 'x_ticks': 13, 'y_ticks': 13,
                     'y_axes': [{'id': key, 'label': label, 'short_label': short, 'min': low, 'max': high}
                                for key, label, short, color, low, high in specs],
                     'series': [{'name': label, 'axis': key, 'color': color, 'points': trend_points(display_arrays['load'], display_arrays[key])}
                                for key, label, short, color, low, high in specs],
                     'method': 'quadratic-least-squares', 'input_decimal_places': precision, 'sample_domain': [min(arrays['load']) * 100, max(arrays['load']) * 100],
                     'note': '按旧版默认规则：9 个计算点作二次最小二乘拟合，显示 20%–150%。30% 以下和 120% 以上为趋势线外推，不是新增计算点。I1 为相电流。'},
        })
        for curve_id, title, unit, lines in [
            ("load_performance", "负荷特性", "比例",
             [("efficiency", "效率"), ("pf", "功率因数")]),
            ("load_current", "负荷与电流", "相电流 (A)", [("current", "相电流")]),
            ("load_slip", "负荷与转差率", "转差率 s", [("slip", "转差率")]),
        ]:
            curves.append({
                "id": curve_id, "title": title, "x_label": "负荷 / 额定功率", "y_label": unit, 'display': False,
                "series": [{"name": label, "points": [{"x": x, "y": y}
                            for x, y in zip(arrays["load"], arrays[key])]}
                           for key, label in lines],
            })
    return curves


def calculate_design(values, *, refine_torque=False, continuous_torque=False):
    context = resource_context()
    if engine_status(context)["missing_assets"]:
        raise CalculationUnavailableError("计算资源尚未安装，请在资源管理中导入授权的私有资源包。")
    p = validate(values)
    memory, material = prepare_memory(p, context)
    started = time.perf_counter()
    try:
        from . import torque_refinement
        capture = {'capture_at': torque_refinement.CHECKPOINT} if refine_torque or continuous_torque else {}
        result = run(memory, max_steps=15_000_000, program_path=str(context['kernel_path']), **capture)
    except NumericKernelError as error:
        raise CalculationError(
            "当前参数未能完成计算，请检查槽形、绕组及材料参数。数值核心诊断：" + str(error)
        ) from error
    raw = result["outputs"].get("101", [])
    if len(raw) != 80 or not all(math.isfinite(x) for x in raw):
        raise CalculationError("计算未返回完整的有限数值结果；请检查输入组合。")
    output = {key: raw[index] for key, index in OUTPUT_MAP.items()}
    try:
        wire = wire_selection.extract(p, output, result['memory'])
    except NumericKernelError as error:
        raise CalculationError(str(error)) from error
    if not 0 < output["ATA1"] <= 1 or not 0 < output["PF1"] <= 1:
        raise CalculationError("当前参数产生了超出物理范围的效率或功率因数，请检查输入。")
    warnings = []
    if material['kind'] == 'user_native':
        warnings.append('本次使用用户材料的固定版本；损耗以原计算核数值输入，曲线在原采样轴内线性插值，仍需独立工程校核。')
    if material["kind"] == "mdb_custom":
        warnings.append("本次使用原数据库硅钢曲线，损耗数据沿用原表数值。")
    if p["f"] != 50:
        warnings.append("材料源表未明确全部频率条件；非 50 Hz 设计需核对材料的适用频率。")
    if output["TST1"] > output["TM1"]:
        warnings.append(
            "原算法输出的启动转矩倍数大于其最大转矩倍数，两项存在不一致；"
            "保留原计算值，最大转矩指标需另行核验。"
        )
    curves = extract_curves(result, p, warnings)
    metadata = semantics.result_metadata(p, output, material, curves)
    if refine_torque:
        metadata['curve_metrics']['refined_torque_peak'] = torque_refinement.evaluate(
            result.get('checkpoint'), curves, context['kernel_sha256'])
        metadata['contract']['optional_optimizer_metrics']['TM_refined'] = 'curve_metrics.refined_torque_peak.value'
    if continuous_torque:
        metadata['curve_metrics']['continuous_torque_peak'] = torque_refinement.evaluate(
            result.get('checkpoint'), curves, context['kernel_sha256'], continuous=True)
        metadata['contract']['optional_optimizer_metrics']['TM_continuous'] = 'curve_metrics.continuous_torque_peak.value'
    for key, resolved in metadata['loss_semantics'].items():
        if resolved['mode'] == 'legacy':
            warnings.append(f"{key}={p[key]:g} 按 {resolved['resolved_unit']} 解析，额定点损耗 {resolved['rated_w']:.7g} W。")
    if p['f'] != 50:
        model = metadata['material_semantics']['loss_frequency_model']
        warnings.append(f"铁耗沿用原核 (f/50)^1.33 折算，本次因子 {model['factor']:.7g}；源表实测频率未获验证。")
        if not p.get('pfw_ref_rpm'):
            warnings.append('机械损耗未按转速自动折算；可按已知参考同步转速设置 pfw_ref_rpm。')
    slip_curve = next((c for c in curves if c["id"] == "slip"), None)
    if slip_curve and slip_curve["series"][1]["points"]:
        peak = max(point["y"] for point in slip_curve["series"][1]["points"])
        if abs(peak - output["TM1"]) > .05 * max(abs(output["TM1"]), 1e-6):
            warnings.append(
                f"转矩曲线峰值 {peak:.4g} 倍与原最大转矩输出 {output['TM1']:.4g} 倍不同；"
                "两者来自原程序的不同计算路径，不能相互替代。"
            )
    metrics = [semantics.metric_info(key, value) for key, value in output.items()]
    from . import slots
    return {
        'slots': slots.snapshot(p, resolved={}),
        **metadata, "engine": engine_status(context),
        "metrics": metrics, "values": output, "warnings": warnings,
        "curves": curves, "inputs": p, "wire_selection": wire,
        "legacy_report": result.get("legacy_report", {"format_version": 1, "status": "unavailable"}),
        "trace": [
            f"材料：{material['name']}；常量组 {material['slot']}。",
            *[f"{key} 输入单位 {entry['resolved_unit']}；额定点损耗 {entry['rated_w']:.7g} W；"
              f"转速折算因子 {entry['speed_scaling']['factor']:.7g}。"
              for key, entry in metadata['loss_semantics'].items()],
            f"执行 {result['steps']:,} 条恢复指令，耗时 {time.perf_counter() - started:.3f} 秒。",
            *[f"成本计算：两组线径均为 {entry['diameter_mm']:.7g} mm；完成两组查表后进入原成本公式。"
              for entry in result.get('compatibility_repairs', [])],
            "所有结果由本次输入重新计算，未使用型号库中的历史结果列。",
        ],
        "provenance": {
            "engine_version": __version__,
            "kernel_sha256": context['kernel_sha256'], "wrapper_sha256": WRAPPER_SHA256,
            "resource_package": context['package_id'], "resource_version": context['package_version'],
            "material_sha256": hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest(),
            "input_sha256": hashlib.sha256(json.dumps(p, sort_keys=True).encode()).hexdigest(),
            **metadata['input_fingerprints'],
            'loss_semantics': metadata['loss_semantics'],
            'material_semantics': metadata['material_semantics'],
            "material": material["name"], "steps": result["steps"],
            "user_material_reference": material.get('user_reference'),
            "user_material_document_sha256": material.get('user_document_sha256'),
            "user_material_source": material.get('user_source'),
            "elapsed_seconds": time.perf_counter() - started,
            "precision": result["precision"],
            "compatibility_repairs": result.get('compatibility_repairs', []),
        },
    }
