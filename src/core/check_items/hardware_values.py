"""Source-faithful CPU, memory, fan and power Value contracts.

CheckItem fields and TextFSM Values are identical. Field meaning owns fixed
measurement windows; neither the parser nor the adapter calculates metrics.
"""

from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

from .canonical import freeze_rule_semantics
from .catalog import build_check_item_definition, build_collection_implementation
from .value_contract import same_name_fields

REVISION = "hardware-values/v1"
TARGETS = {
    "system.cpu_usage": ("CPU 使用率", ["slot", "cpu"], "display cpu"),
    "system.memory_usage": ("内存使用率", ["slot"], "display memory"),
    "environment.fan": ("风扇状态", ["slot", "fan"], "display fan"),
    "environment.power": ("电源状态", ["slot", "power_supply"], "display power"),
}
# Extra observations are evidence, not additional health criteria. Units are
# exactly those printed by the command, especially memory KB (not bytes).
EXTRA_FIELDS = {
    "system.cpu_usage": [
        ("cpu_5s_percent", "percent", "明确标注 last 5 seconds 的 CPU 使用率", "5s"),
        ("cpu_1m_percent", "percent", "明确标注 last 1 minute 的 CPU 使用率", "1m"),
    ],
    "system.memory_usage": [
        ("memory_total_kb", "kb", "Mem 行 Total，原文 KB 容量", None),
        ("memory_used_kb", "kb", "Mem 行 Used，原文 KB 容量", None),
        ("memory_free_kb", "kb", "Mem 行 Free，原文 KB 容量", None),
        ("memory_shared_kb", "kb", "Mem 行 Shared，原文 KB 容量", None),
        ("memory_buffers_kb", "kb", "Mem 行 Buffers，原文 KB 容量", None),
        ("memory_cached_kb", "kb", "Mem 行 Cached，原文 KB 容量", None),
        ("memory_free_percent", "percent", "设备直接输出的 FreeRatio；不由容量相除计算", None),
    ],
    "environment.fan": [
        ("fan_airflow_direction", None, "风扇实际气流方向原文", None),
        ("preferred_fan_airflow_direction", None, "设备期望气流方向原文", None),
    ],
    "environment.power": [
        ("mode", None, "电源 AC/DC 模式原文", None),
        ("current_amperes", "amperes", "此电源 Current(A)，-- 表示未提供", None),
        ("voltage_volts", "volts", "此电源 Voltage(V)，-- 表示未提供", None),
        ("output_watts", "watts", "此电源 Power(W)，不等于机框总输入功率", None),
        ("fan_direction", None, "电源风扇方向原文", None),
        (
            "slot_input_watts",
            "watts",
            "槽位 Input Power(W)，在该槽位电源行重复展示，不能按电源求和",
            None,
        ),
    ],
}


def extra_observations(item_id):
    return [
        {
            "canonical_field": field,
            "observation_type": f"{item_id}.{field}",
            "semantic_type": "gauge" if unit else "text",
            "value_type": "number" if unit else "text",
            "unit": unit,
            "description": description,
            "required": False,
            "assessment_roles": ["evidence"],
            "dimensions": [],
            **({"fixed_dimensions": {"window": window}} if window else {}),
        }
        for field, unit, description, window in EXTRA_FIELDS[item_id]
    ]


def revise_definition(item):
    if item.canonical_contract.get("hardware_value_revision") == REVISION:
        return item
    raw = deepcopy(asdict(item))
    name, keys, _ = TARGETS[item.id]
    old = item.canonical_contract
    rows = [
        deepcopy(row)
        for row in old["observations"]
        if row["canonical_field"] not in {"memory_used_bytes", "memory_total_bytes"}
    ]
    for row in rows:
        for key in ("mapping_guidance", "projection_recipes", "allowed_values"):
            row.pop(key, None)
        row.setdefault("required", True)
        row.setdefault("value_type", "number" if row.get("unit") else "text")
        row["dimensions"] = []
        if item.id in {"system.cpu_usage", "system.memory_usage"}:
            row["fixed_dimensions"] = {"window": "5m"}
            if item.id == "system.memory_usage" and row["semantic_type"] == "threshold":
                row.pop("fixed_dimensions")
            row["description"] = (
                "设备明确输出的五分钟平均使用率或对应阈值；不能填入瞬时值或容量推算值"
            )
            if item.id == "system.memory_usage":
                row["required"] = False
    extras = extra_observations(item.id)
    for row in extras:
        if row["canonical_field"] in {"memory_total_kb", "memory_used_kb"}:
            row["required"] = True
    rows = (
        extras + rows if item.id in {"system.cpu_usage", "system.memory_usage"} else rows + extras
    )
    contract = {
        "entity_type": old["entity_type"],
        "identity_dimensions": keys,
        "identity_dimension_rules": {key: {"kind": "identifier"} for key in keys},
        "value_contract": "same-name/v1",
        "hardware_value_revision": REVISION,
        "observations": rows,
    }
    raw.update(
        name=name,
        version=item.version + 1,
        report_projection="auto@1",
        canonical_contract=freeze_rule_semantics(contract),
    )
    return build_check_item_definition(raw)


def template_path(item_id):
    return Path(__file__).parent / "assets" / "hardware-values" / (item_id + ".textfsm")


def revise_adapter(adapter, item, parser_id, fields):
    raw = deepcopy(asdict(adapter))
    raw["commands"] = list(raw["commands"])
    old_outputs = raw["adapter_spec"].get("outputs") or []
    if len(old_outputs) != 1 or old_outputs[0]["check_item_id"] != item.id:
        raise ValueError("Hardware revision requires the existing single CheckItem association")
    raw["version"] += 1
    raw["adapter_spec"] = {
        "schema_version": "command-adapter/v2",
        "parser": {"template_id": parser_id},
        "outputs": [
            {
                "check_item_id": item.id,
                "check_item_version_id": item.version_id,
                "source_record_set": "default",
                "empty_status": "unavailable",
                "projection": {"fields": same_name_fields(item.canonical_contract, fields)},
            }
        ],
    }
    return build_collection_implementation(raw, known_check_item_ids=set(adapter.check_item_ids))
