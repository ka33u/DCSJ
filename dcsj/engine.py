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
from . import __version__

from .numeric_vm import NumericKernelError, run
from .parameters import ELEMENT_KEYS, INPUT_MAP, OUTPUT_MAP, ROOT, field_info, validate

_material_path = ROOT / "data/material_kernel_tables.json"
MATERIALS = {item["name"]: item for item in json.loads(_material_path.read_text())["materials"]} if _material_path.is_file() else {}
ENGINE_NAME = "DCSJ Python 数值核心"
_kernel_path = ROOT / "dcsj/generated/numeric_kernel.json.gz"
KERNEL_SHA256 = hashlib.sha256(_kernel_path.read_bytes()).hexdigest() if _kernel_path.is_file() else None
WRAPPER_SHA256 = hashlib.sha256(b"".join((ROOT / "dcsj" / name).read_bytes()
    for name in ("engine.py", "numeric_vm.py", "parameters.py", "kernel_layout.py"))).hexdigest()
LIMITATIONS = [
    "由原 Fortran 程序的计算指令恢复；使用双精度中间运算和原单精度存储。",
    "已恢复三相异步电动机计算；历史型号保存值可能使用不同版本的材料表。",
    "材料损耗表保留原数值语义，原表标注单位仍需工程核验。",
]


class CalculationError(ValueError):
    pass


class CalculationUnavailableError(RuntimeError):
    pass


def engine_status():
    missing = []
    if not KERNEL_SHA256:
        missing.append("dcsj/generated/numeric_kernel.json.gz")
    if not MATERIALS:
        missing.append("data/material_kernel_tables.json")
    return {"name": ENGINE_NAME, "status": "unavailable" if missing else "recovered",
            "missing_assets": missing,
            "version": __version__, "kernel_sha256": KERNEL_SHA256,
            "implementation": "pure-python-numeric-ir", "limitations": LIMITATIONS,
            "native_runtime_required": False}


def f32(value):
    return struct.unpack("<f", struct.pack("<f", value))[0]


def prepare_memory(parameters):
    p = parameters
    material = MATERIALS.get(p["Paihao"])
    if material is None:
        raise CalculationError(f"未知硅钢片牌号 {p['Paihao']}；请从材料库选择已恢复的牌号。")
    memory = {addr: f32(p[key]) for key, addr in INPUT_MAP.items() if key != "Paihao"}
    memory[INPUT_MAP["Paihao"]] = float(material["slot"])
    # MAIN normalizes absolute mechanical/stray loss before the selected entry.
    for key in ("pfw", "ps"):
        value = f32(p[key])
        if value > 1:
            value = f32(value / f32(p["kW"]) / 1000)
        memory[INPUT_MAP[key]] = value
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
        })
    if parameters["_load_curve"]:
        memory = result["memory"]
        arrays = {key: [memory.f32(address + i * 4) for i in range(9)]
                  for key, address in {
                      "load": 0x4AB89C, "efficiency": 0x4AB830, "pf": 0x4AB80C,
                      "current": 0x4AB7C4, "slip": 0x4AB7A0,
                  }.items()}
        if not all(math.isfinite(x) for values in arrays.values() for x in values):
            raise CalculationError("负荷特性计算包含非有限数值")
        for curve_id, title, unit, lines in [
            ("load_performance", "负荷特性", "比例",
             [("efficiency", "效率"), ("pf", "功率因数")]),
            ("load_current", "负荷与电流", "相电流 (A)", [("current", "相电流")]),
            ("load_slip", "负荷与转差率", "转差率 s", [("slip", "转差率")]),
        ]:
            curves.append({
                "id": curve_id, "title": title, "x_label": "负荷 / 额定功率", "y_label": unit,
                "series": [{"name": label, "points": [{"x": x, "y": y}
                            for x, y in zip(arrays["load"], arrays[key])]}
                           for key, label in lines],
            })
    return curves


def calculate_design(values):
    if engine_status()["missing_assets"]:
        raise CalculationUnavailableError("计算资源尚未安装，请在本机安装授权的私有计算资源后重启服务。")
    p = validate(values)
    memory, material = prepare_memory(p)
    started = time.perf_counter()
    try:
        result = run(memory, max_steps=15_000_000)
    except NumericKernelError as error:
        raise CalculationError(
            "当前参数未能完成计算，请检查槽形、绕组及材料参数。数值核心诊断：" + str(error)
        ) from error
    raw = result["outputs"].get("101", [])
    if len(raw) != 80 or not all(math.isfinite(x) for x in raw):
        raise CalculationError("计算未返回完整的有限数值结果；请检查输入组合。")
    output = {key: raw[index] for key, index in OUTPUT_MAP.items()}
    if not 0 < output["ATA1"] <= 1 or not 0 < output["PF1"] <= 1:
        raise CalculationError("当前参数产生了超出物理范围的效率或功率因数，请检查输入。")
    warnings = []
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
    slip_curve = next((c for c in curves if c["id"] == "slip"), None)
    if slip_curve and slip_curve["series"][1]["points"]:
        peak = max(point["y"] for point in slip_curve["series"][1]["points"])
        if abs(peak - output["TM1"]) > .05 * max(abs(output["TM1"]), 1e-6):
            warnings.append(
                f"转矩曲线峰值 {peak:.4g} 倍与原最大转矩输出 {output['TM1']:.4g} 倍不同；"
                "两者来自原程序的不同计算路径，不能相互替代。"
            )
    metrics = []
    for key, value in output.items():
        info = field_info(key)
        info["group"] = "计算结果"
        metrics.append({**info, "value": value})
    return {
        "engine": engine_status(),
        "metrics": metrics, "values": output, "warnings": warnings,
        "curves": curves, "inputs": p,
        "trace": [
            f"材料：{material['name']}；常量组 {material['slot']}。",
            "pfw/ps 大于 1 时作为 W 输入，否则作为额定功率比例输入。",
            f"执行 {result['steps']:,} 条恢复指令，耗时 {time.perf_counter() - started:.3f} 秒。",
            "所有结果由本次输入重新计算，未使用型号库中的历史结果列。",
        ],
        "provenance": {
            "engine_version": __version__,
            "kernel_sha256": KERNEL_SHA256, "wrapper_sha256": WRAPPER_SHA256,
            "material_sha256": hashlib.sha256(json.dumps(material["const_values"]).encode()).hexdigest(),
            "input_sha256": hashlib.sha256(json.dumps(p, sort_keys=True).encode()).hexdigest(),
            "material": material["name"], "steps": result["steps"],
            "elapsed_seconds": time.perf_counter() - started,
            "precision": result["precision"],
        },
    }
