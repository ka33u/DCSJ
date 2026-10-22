"""Input schema and validation using recovered original field names."""
import json
import math
from difflib import get_close_matches
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
from .field_specs import FIELDS
from .kernel_layout import INPUT_MAP, OUTPUT_MAP

DICTIONARY_CASEFOLD = {key.casefold(): value for key, value in FIELDS.items()}
_example_path = ROOT / "data/example.json"
EXAMPLE = json.loads(_example_path.read_text()) if _example_path.is_file() else None


def example_data():
    from .resources import current, asset
    package = current()
    data = json.loads(asset('example.json', package).read_text(encoding='utf-8')) if package else EXAMPLE
    return {**data, 'parameters': input_parameters(data['parameters'])} if data else None
ELEMENT_KEYS = [f"{key}{i}" for i in range(1, 10) for key in ("N", "A", "B", "L")]
INPUT_KEYS = [k for k in INPUT_MAP if k != "_wire_target"] + ELEMENT_KEYS + [
    "SizeX", "Jiefa", "Jueyuan", "CORR", "_wire_target", "_curve", "_load_curve"]
RETIRED_INPUT_KEYS = [key + '_req' for key in ('ATA', 'PF', 'IST', 'TST', 'TM')]
OPTIONAL_KEYS = ['pfw_unit', 'ps_unit', 'pfw_ref_rpm', 'pfw_speed_exponent']
GROUPS = [
    ("rating", "额定参数与设计指标"), ("geometry", "铁心与气隙"),
    ("stator", "定子槽"), ("rotor", "转子槽"),
    ("winding", "绕组与导线"), ("material", "材料与损耗"),
    ("elements", "自定义槽形元素"), ("controls", "计算选项"),
    ("results", "原始计算记录"),
]
GROUP_KEYS = {
    "rating": "SizeX Jiefa Jueyuan kW p f Ufai ATA PF IST TST TM",
    "geometry": "Q1 Q2 D1 Df Dn Xn Di1 Di2 g L dk nk dk2 d kfe",
    "stator": "b01 hs0 zs1 bs1 bs2 hs12 czn hw Si Wi",
    "rotor": "zc bc02 hr0 zs2 br1 br2 br3 br4 hr12 hr3 hr4 Srr Jc srl hrr hrl le lel",
    "winding": "Y1 a ZZ JJ Djj MM Dmm sk",
    "material": "Paihao L01 L02 L02u pfw ps pfw_unit ps_unit pfw_ref_rpm pfw_speed_exponent kt ky Ccu Cfe Cal CORR",
    "controls": "_wire_target _curve _load_curve",
    "elements": " ".join(ELEMENT_KEYS),
}
INTEGER_KEYS = {"p", "Q1", "Q2", "a", "JJ", "MM", "nk", "czn", "zc"}
POSITIVE_KEYS = {"kW", "p", "f", "Ufai", "Q1", "Q2", "D1", "Di1", "g", "L",
                 "a", "ZZ", "kfe", "ATA", "PF", "kt", "ky", "L01", "L02"}
SPECIAL_LABELS = {
    "Paihao": "硅钢片牌号", "SizeX": "设计名称", "Jiefa": "接法",
    "Jueyuan": "绝缘等级", "_wire_target": "目标槽满率（0 为固定线规）",
    "_curve": "计算转矩与电流曲线", "_load_curve": "计算负荷特性",
    "CORR": "冷轧硅钢修正", "Ufai": "相电压", "ATA1": "效率",
    "PF1": "功率因数", "IST1": "启动电流倍数", "TST1": "启动转矩倍数",
    "TM1": "最大转矩倍数（原公式估计）",
    'pfw_unit': '机械损耗输入单位', 'ps_unit': '杂散损耗输入单位',
    'pfw_ref_rpm': '机械损耗参考同步转速', 'pfw_speed_exponent': '机械损耗转速指数',
}
UNIT_OVERRIDES = {
    "Ufai": "V（相）", "U1": "V（相）", "I1": "A（线）", "P1": "kW", "kW": "kW",
    "P0": "W", "Pcu": "W", "Pal": "W", "Pfe": "W", "Pfw1": "W", "Ps1": "W",
    "A11": "A/cm", "n": "r/min", "Tn": "N·m", "TstNm": "N·m",
    "Gcu": "kg", "Gfe": "kg", "Gal": "kg", "R1a": "Ω",
    'pfw_ref_rpm': 'r/min', 'pfw_speed_exponent': '',
    **{key: 'G' for key in ('Bg', 'Bts', 'Btr', 'Bys', 'Byr')},
    **{key: "p.u." for key in ("r1b", "r2", "r2st", "rst", "X1", "X2", "X",
                               "X1s", "X2s", "Xst")},
}


class InputError(ValueError):
    def __init__(self, issues):
        self.issues = issues
        super().__init__("；".join(f"{x['field']}: {x['message']}" for x in issues))


def field_info(key):
    value = DICTIONARY_CASEFOLD.get(key.casefold(), {})
    label = SPECIAL_LABELS.get(key, value.get("description") or value.get("label") or key)
    group = next((group for group, keys in GROUP_KEYS.items() if key in keys.split()), "results")
    unit = UNIT_OVERRIDES.get(key, value.get("unit") or "")
    return {"key": key, "label": label, "unit": unit, "group": group}


def schema():
    example = example_data()
    fields = []
    for key in INPUT_KEYS + OPTIONAL_KEYS:
        info = field_info(key)
        info.update(type="integer" if key in INTEGER_KEYS else "number", role="input")
        if key in {"SizeX", "Jueyuan", "Paihao"}:
            info["type"] = "text"
        if key == "Jiefa":
            info.update(type="select", options=[{"value": "△", "label": "三角形 △"},
                                                {"value": "Y", "label": "星形 Y"}])
        if key in {"CORR", "_curve", "_load_curve"}:
            info.update(type="select", options=[{"value": 0, "label": "关闭"},
                                                {"value": 1, "label": "开启"}])
        if example is not None:
            raw_default = example['parameters'].get(key)
            if raw_default is not None:
                try:
                    info['default'] = float(raw_default) if info['type'] in {'integer', 'number'} else raw_default
                except (ValueError, TypeError):
                    pass
        if key in POSITIVE_KEYS:
            info["min"] = 1 if key in INTEGER_KEYS else 0.000001
        if key in {"ATA", "PF", "kfe"}:
            info["max"] = 1
        if key in {"pfw", "ps"}:
            info["description"] = "沿用原程序：数值大于 1 时按 W 输入，否则为额定输出功率的比例。"
        if key == "Ufai":
            info["description"] = "绕组相电压；△ 接法等于线电压，Y 接法线电压为相电压 × √3。"
        if key in {'ATA', 'PF', 'IST'}:
            info.update(participates_in_calculation=True, purpose='legacy_iteration_seed',
                        description='沿用原程序，同时用于设计指标比较和计算初值。')
        if key in {'pfw_unit', 'ps_unit'}:
            info.update(type='select', optional=True, default='legacy', options=[
                {'value': 'legacy', 'label': '原版自动判断'}, {'value': 'W', 'label': 'W（额定点）'},
                {'value': 'pu', 'label': '额定输出功率比例'}],
                description='未设置保留原版 >1 为 W、≤1 为比例的规则；显式 W 可输入小于 1 W 的损耗。')
        if key == 'pfw_ref_rpm':
            info.update(optional=True, min=0, description='0 或未设置时不折算。正数启用 (当前同步转速/参考同步转速)^k 工程估算。')
        if key == 'pfw_speed_exponent':
            info.update(optional=True, min=0, max=6, default=2, description='仅启用参考转速时使用，默认 2；请按损耗模型选取。')
        if key == "_wire_target":
            info.update(default=0, min=0, max=0.99,
                        description="0 使用给定并绕根数与线径；正数调用原程序的线规反算分支。")
        fields.append(info)
    for key in OUTPUT_MAP:
        if key not in INPUT_KEYS:
            fields.append({**field_info(key), "type": "number", "role": "output"})
    return {"groups": [{"id": key, "label": label} for key, label in GROUPS], "fields": fields,
            'retired_input_keys': RETIRED_INPUT_KEYS}


def normalize_keys(values):
    """Delphi/Access field names are case insensitive; preserve all raw columns."""
    normalized = dict(values)
    folded = {key.casefold(): value for key, value in values.items()}
    for key in INPUT_KEYS + OPTIONAL_KEYS:
        if key not in normalized and key.casefold() in folded:
            normalized[key] = folded[key.casefold()]
    return normalized


def input_parameters(values):
    """Project trusted legacy catalog rows onto inputs; raw rows stay separate."""
    normalized = normalize_keys(values)
    return {key: normalized[key] for key in INPUT_KEYS + OPTIONAL_KEYS if key in normalized}


def check_keys(values):
    """External API keys are exact; never silently ignore a misspelled override."""
    accepted = set(INPUT_KEYS + OPTIONAL_KEYS)
    issues = []
    folded = {key.casefold(): key for key in accepted}
    for key in sorted(set(values) - accepted):
        match = folded.get(key.casefold()) or next(iter(get_close_matches(key, sorted(accepted), n=1)), None)
        message = '未知输入字段' + (f'；是否指 {match}？字段名区分大小写' if match else '')
        if key in OUTPUT_MAP:
            message = '此字段为历史计算输出，不能作为输入'
        if key in RETIRED_INPUT_KEYS:
            message = '独立设计要求已取消，请使用原参数 ' + key.removesuffix('_req')
        issues.append({'field': key, 'message': message})
    if issues:
        raise InputError(issues)
    return dict(values)


def validate(values, *, strict=False):
    """No substitution of missing essential input with hidden example values."""
    # Legacy Access rows contain case-insensitive columns and historical results.
    # HTTP inputs must call check_keys (or strict=True) before this adapter.
    values = check_keys(values) if strict else input_parameters(values)
    issues, clean = [], {}
    for key in INPUT_KEYS:
        raw = values.get(key)
        if key in {"SizeX", "Jiefa", "Jueyuan", "Paihao"}:
            if raw is None or str(raw).strip() == "":
                issues.append({"field": key, "message": "缺少必填参数"})
                continue
            clean[key] = str(raw).strip()
            if len(clean[key]) > 200:
                issues.append({"field": key, "message": "文本过长"})
            continue
        if raw is None or raw == "":
            if key in ELEMENT_KEYS or key in {"CORR", "_wire_target", "_curve", "_load_curve"}:
                raw = 0
            else:
                issues.append({"field": key, "message": "缺少必填数值"})
                continue
        try:
            if isinstance(raw, bool):
                raise ValueError
            number = float(raw)
            if not math.isfinite(number) or abs(number) > 1e9:
                raise ValueError
        except (ValueError, TypeError):
            issues.append({"field": key, "message": "必须是有限数值，绝对值不超过 10⁹"})
            continue
        clean[key] = number
        if key in POSITIVE_KEYS and number <= 0:
            issues.append({"field": key, "message": "必须大于 0"})
        if key in INTEGER_KEYS and not number.is_integer():
            issues.append({"field": key, "message": "必须为整数"})
        if key in {"ATA", "PF", "kfe"} and not 0 < number <= 1:
            issues.append({"field": key, "message": "必须在 (0, 1] 范围"})
        if key in {"CORR", "_curve", "_load_curve"} and number not in {0, 1}:
            issues.append({"field": key, "message": "必须为 0 或 1"})
        if key == "_wire_target" and not 0 <= number < 1:
            issues.append({"field": key, "message": "必须在 [0, 1) 范围"})
    if all(key in clean for key in ("D1", "Di1", "Di2", "g")):
        if not 0 <= clean["Di2"] < clean["Di1"] - 2 * clean["g"] < clean["Di1"] < clean["D1"]:
            issues.append({"field": "Di1", "message": "须满足轴孔直径 < 转子外径 < 定子内径 < 定子外径"})
    if clean.get("p", 2) % 2:
        issues.append({"field": "p", "message": "极数必须为偶数"})
    if clean.get("Q1", 3) % 3:
        issues.append({"field": "Q1", "message": "三相绕组的定子槽数须为 3 的倍数"})
    if clean.get("czn") not in {1, 2}:
        issues.append({"field": "czn", "message": "绕组层数必须为 1 或 2"})
    if min(clean.get("JJ", 0), clean.get("MM", 0)) < 0 or clean.get("JJ", 0) + clean.get("MM", 0) <= 0:
        issues.append({"field": "JJ", "message": "并绕根数不能为负数，至少一组须大于 0"})
    if clean.get("JJ", 0) > 0 and clean.get("Djj", 0) <= 0:
        issues.append({"field": "Djj", "message": "第一组有导线时，线径须大于 0"})
    if clean.get("MM", 0) > 0 and clean.get("Dmm", 0) <= 0:
        issues.append({"field": "Dmm", "message": "第二组有导线时，线径须大于 0"})
    if clean.get("Jiefa") not in {"△", "Δ", "D", "Y", "y"}:
        issues.append({"field": "Jiefa", "message": "接法须为 △ 或 Y"})
    for key in OPTIONAL_KEYS:
        raw = values.get(key)
        if raw is None or raw == '':
            continue
        if key.endswith('_unit'):
            if raw not in {'legacy', 'W', 'pu'}:
                issues.append({'field': key, 'message': '单位须为 legacy、W 或 pu'})
            else:
                clean[key] = raw
            continue
        maximum = 6 if key == 'pfw_speed_exponent' else 1e9
        try:
            number = float(raw)
            if isinstance(raw, bool) or not math.isfinite(number) or not 0 <= number <= maximum:
                raise ValueError
            clean[key] = number
        except (ValueError, TypeError):
            issues.append({'field': key, 'message': f'必须是 0 至 {maximum:g} 的有限数值'})
    for key in ('pfw', 'ps'):
        if clean.get(key, 0) < 0:
            issues.append({'field': key, 'message': '损耗不能为负数'})
    if issues:
        raise InputError(issues)
    clean["Jiefa"] = "Y" if clean["Jiefa"].upper() == "Y" else "△"
    return clean
