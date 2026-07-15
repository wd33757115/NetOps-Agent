#!/usr/bin/env python
"""Generate governed rule assets and a leadership report from local lixian data.

Raw CLI is used only by the local semantic catalog. The LLM receives sampled,
whitelisted and pseudonymized structured records. Generated assets remain
candidates; this script never activates runtime rules.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.patrol.change_policy import (  # noqa: E402
    compare_with_policy,
    policy_from_dict,
)
from src.core.patrol.event_rules import condition_matches  # noqa: E402
from src.core.patrol.leadership_report import (  # noqa: E402
    build_leadership_report,
    render_leadership_docx,
    render_leadership_markdown,
)
from src.core.patrol.raw_importer import (  # noqa: E402
    preview_raw_path,
)
from src.core.rule_control.dataset_context import (  # noqa: E402
    sanitize_structured_records,
)
from src.core.state.observation_applicability import (  # noqa: E402
    APPLICABILITY_CONTEXT_KEY,
    applicability_context_fields,
)
from src.core.workflows.patrol_batch_service import (  # noqa: E402
    evaluate_current_state,
)

PERIODS = ("Q1", "Q2", "Q3")
ASSET_SPECS = (
    {
        "key": "interface",
        "command": "display interface brief",
        "observation_targets": (
            {
                "canonical_field": "status",
                "observation_type": "interface.oper_status",
            },
        ),
        "policy_intent": (
            "接口运行状态仅比较状态迁移；UP 到 DOWN 形成故障，DOWN 到 UP 形成恢复。"
            "首次采集的 DOWN 不直接判定故障，管理关闭状态不得误报。"
        ),
        "event_intent": (
            "生成 InterfaceDown 故障规则和 InterfaceUp 恢复规则；故障只匹配 up 到 down，"
            "恢复只匹配 down 到 up，规则名称稳定且可审计。"
        ),
        "policy_constraints": {
            "allowed_compare_modes": ["state_transition"],
            "allowed_minimum_durations": [None, "0s"],
            "evidence_only_reason": True,
        },
        "event_contract": {
            "fault": "InterfaceDown",
            "recovery": "InterfaceUp",
            "fault_severity": "major",
            "fault_kind": "transition",
            "recovery_kind": "transition",
            "allow_initial_transition_fault": False,
        },
    },
    {
        "key": "optical",
        "command": "display transceiver diagnosis interface",
        "observation_targets": (
            {
                "canonical_field": "rx_power_dbm",
                "observation_type": "interface.optical.rx_power",
            },
            {
                "canonical_field": "tx_power_dbm",
                "observation_type": "interface.optical.tx_power",
            },
        ),
        "policy_intent": (
            "光功率采用 dBm。低于或等于 -17 dBm 为 warning，低于或等于 -20 dBm 为 critical，"
            "恢复阈值为 -15 dBm；空值、DOM 不支持和模块缺失不按低光功率处理。"
        ),
        "event_intent": (
            "按 observation_type 分别生成 Rx/Tx 低光功率、严重低光功率和恢复规则。"
            "warning 与 critical 必须使用不同阈值，critical 优先且互斥；"
            "Rx/Tx 规则名称必须包含方向以避免资产冲突。"
        ),
        "policy_constraints": {
            "allowed_compare_modes": ["threshold_low_and_deadband"],
            "required_threshold_keys": ["low", "critical_low"],
            "threshold_bounds": {
                "low": {"min": -17, "max": -17},
                "critical_low": {"min": -20, "max": -20},
            },
            "recovery_direction": "above",
            "recovery_reference_threshold": "low",
            "minimum_hysteresis": 2,
            "recovery_bounds": {"min": -15, "max": -15},
            "deadband_bounds": {"min": 2, "max": 2},
            "allowed_minimum_durations": [None, "0s"],
            "evidence_only_reason": True,
        },
    },
)


def _load_skill(script_name: str, module_name: str) -> Any:
    path = PROJECT_ROOT / "src" / "skills" / script_name / "scripts" / "run.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if not spec or not spec.loader:
        raise RuntimeError(f"cannot load Skill module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


OBSERVATION_SKILL = _load_skill(
    "observation-contract-generator", "lixian_observation_contract_skill"
)
POLICY_SKILL = _load_skill("change-policy-generator", "lixian_change_policy_skill")
EVENT_SKILL = _load_skill("event-rule-generator", "lixian_event_rule_skill")


def _slug(command: str) -> str:
    return command.replace(" ", "-")


def _latest_parser_report(
    reports_root: Path, period: str, command: str
) -> dict[str, Any]:
    directory = reports_root / period / _slug(command)
    candidates = [
        path
        for path in directory.glob("*.json")
        if not path.name.startswith("summary_")
    ]
    if not candidates:
        raise FileNotFoundError(f"parser benchmark report is missing: {directory}")
    path = max(candidates, key=lambda item: item.stat().st_mtime_ns)
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("validation_pass_rate") != 1.0:
        raise AssertionError(f"Parser report did not pass all samples: {path}")
    report["_report_path"] = str(path)
    return report


def _period_from_file_path(file_path: str) -> str:
    match = re.search(r"(?:^|[\\/])(Q[123])(?:[\\/]|$)", str(file_path), re.IGNORECASE)
    if not match:
        raise AssertionError(f"Parser sample path has no Q1/Q2/Q3 period: {file_path}")
    return match.group(1).upper()


def _split_snapshot_groups(report: dict[str, Any]) -> list[dict[str, Any]]:
    parsed = list(report.get("parsed_records") or [])
    groups: list[dict[str, Any]] = []
    offset = 0
    period_ordinals: Counter[str] = Counter()
    for index, sample in enumerate(report.get("sample_results") or []):
        count = int(sample.get("record_count") or 0)
        records = parsed[offset : offset + count]
        offset += count
        if len(records) != count:
            raise AssertionError("Parser report record groups do not match sample counts")
        period = _period_from_file_path(str(sample.get("file_path") or ""))
        period_ordinals[period] += 1
        groups.append(
            {
                "period": period,
                "device_id": str(sample.get("device_id") or f"device-{index + 1}"),
                "group_id": f"snapshot-{period.lower()}-{period_ordinals[period]}",
                "records": records,
            }
        )
    if offset != len(parsed):
        raise AssertionError("Parser report contains unassigned parsed records")
    return groups


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        return float(str(value).strip().rstrip("%"))
    except ValueError:
        return None


def _sample_group_records(
    records: list[dict[str, Any]],
    *,
    target_fields: tuple[str, ...],
    limit: int,
) -> list[dict[str, Any]]:
    if len(records) <= limit:
        return records
    selected: list[int] = []

    def add(index: int) -> None:
        if 0 <= index < len(records) and index not in selected and len(selected) < limit:
            selected.append(index)

    add(0)
    add(len(records) - 1)
    for field in target_fields:
        values: dict[str, int] = {}
        numeric: list[tuple[float, int]] = []
        for index, record in enumerate(records):
            value = record.get(field)
            if value not in (None, ""):
                values.setdefault(str(value), index)
            number = _number(value)
            if number is not None:
                numeric.append((number, index))
        for index in list(values.values())[:6]:
            add(index)
        if numeric:
            add(min(numeric)[1])
            add(max(numeric)[1])

    signatures: set[tuple[str, ...]] = set()
    for index, record in enumerate(records):
        signature = tuple(sorted(key for key, value in record.items() if value not in (None, "")))
        if signature not in signatures:
            signatures.add(signature)
            add(index)
    if len(selected) < limit:
        step = max(1, len(records) // max(1, limit - len(selected)))
        for index in range(0, len(records), step):
            add(index)
    return [records[index] for index in sorted(selected[:limit])]


def _sanitized_generation_context(
    groups: list[dict[str, Any]],
    *,
    target_fields: tuple[str, ...],
    max_records: int,
) -> dict[str, Any]:
    approved_fields = {
        str(field)
        for group in groups
        for record in group["records"]
        for field in record
    }
    per_group = max(1, max_records // max(1, len(groups)))
    sanitized_groups: list[dict[str, Any]] = []
    privacy_rows: list[dict[str, Any]] = []
    for group in groups:
        sampled = _sample_group_records(
            group["records"],
            target_fields=target_fields,
            limit=per_group,
        )
        sanitized = sanitize_structured_records(
            sampled,
            approved_fields=approved_fields,
        )
        sanitized_groups.append(
            {
                "group_id": group["group_id"],
                "records": sanitized["records"],
            }
        )
        privacy_rows.append(sanitized["privacy"])
    records = [
        record for group in sanitized_groups for record in group["records"]
    ]
    serialized = json.dumps(
        {"records": records, "record_groups": sanitized_groups},
        ensure_ascii=False,
        sort_keys=True,
    )
    forbidden_values = {
        value
        for group in groups
        for value in (
            group["device_id"],
            group["device_id"].split("-", 1)[0],
            group["device_id"].rsplit("-", 1)[-1],
        )
        if len(value) >= 3
    }
    leaked = sorted(value for value in forbidden_values if value in serialized)
    if leaked:
        raise AssertionError("sanitized LLM context leaked device identity: " + ", ".join(leaked))
    for forbidden_key in ("raw_text", "raw_output", "password", "community", "token"):
        if f'"{forbidden_key}"' in serialized.casefold():
            raise AssertionError(f"sanitized LLM context contains {forbidden_key}")
    return {
        "records": records,
        "record_groups": sanitized_groups,
        "approved_fields": sorted(approved_fields),
        "privacy": {
            "success": True,
            "raw_cli_included": False,
            "snapshot_group_count": len(sanitized_groups),
            "sampled_record_count": len(records),
            "removed_fields": dict(
                sum(
                    (Counter(row.get("removed_fields") or {}) for row in privacy_rows),
                    Counter(),
                )
            ),
            "pseudonymized_fields": dict(
                sum(
                    (
                        Counter(row.get("pseudonymized_fields") or {})
                        for row in privacy_rows
                    ),
                    Counter(),
                )
            ),
        },
    }


def _canonical_parser_contract(report: dict[str, Any]) -> dict[str, Any]:
    loop = dict(report.get("agent_loop") or {})
    contract = dict(loop.get("contract") or {})
    if loop.get("status") != "passed" or not contract:
        raise AssertionError("canonical Parser Agent report has no passed Contract")
    declared = {
        str(field)
        for field in [
            *(contract.get("required_fields") or []),
            *(contract.get("optional_fields") or []),
        ]
    }
    emitted = {
        str(field)
        for record in report.get("parsed_records") or []
        if isinstance(record, dict)
        for field in record
    }
    if not emitted or emitted - declared:
        raise AssertionError(
            "canonical Parser Contract does not own every emitted field: "
            + ", ".join(sorted(emitted - declared))
        )
    return contract


def _sampling_target_fields(
    contract: dict[str, Any], spec: dict[str, Any]
) -> tuple[str, ...]:
    desired = {
        str(item["observation_type"]) for item in spec["observation_targets"]
    }
    semantics = dict(contract.get("state_semantics") or {})
    direct = [
        field
        for field, raw in semantics.items()
        if isinstance(raw, dict) and str(raw.get("observation_type") or "") in desired
    ]
    if direct:
        return tuple(direct)
    comparable = [
        field
        for field, raw in semantics.items()
        if isinstance(raw, dict)
        and str(raw.get("semantic_type") or "").casefold()
        in {"state", "gauge", "counter", "percentage"}
        and raw.get("ignore") is not True
    ]
    return tuple(comparable or contract.get("required_fields") or ())


def _event_constraints(
    spec: dict[str, Any], observation_type: str, field_name: str
) -> dict[str, Any]:
    if spec["key"] == "interface":
        contract = dict(spec["event_contract"])
        return {
            "required_event_types": [contract["fault"], contract["recovery"]],
            "required_event_classes": {
                contract["fault"]: "fault",
                contract["recovery"]: "recovery",
            },
            "required_condition_kinds": {
                contract["fault"]: [contract["fault_kind"]],
                contract["recovery"]: [contract["recovery_kind"]],
            },
            "required_severities": {
                contract["fault"]: contract["fault_severity"],
                contract["recovery"]: "info",
            },
            "required_recovery_pairs": [
                {"fault": contract["fault"], "recovery": contract["recovery"]}
            ],
            "allow_initial_transition_fault": contract[
                "allow_initial_transition_fault"
            ],
            "max_rule_count": 3,
        }
    direction = (
        "Rx"
        if observation_type.endswith(".rx_power") or "rx_power" in field_name.casefold()
        else "Tx"
    )
    low = f"Optical{direction}PowerLow"
    critical = f"Optical{direction}PowerCritical"
    recovered = "OpticalRecovered"
    return {
        "required_event_types": [low, critical, recovered],
        "required_event_classes": {
            low: "fault",
            critical: "fault",
            recovered: "recovery",
        },
        "required_condition_kinds": {
            low: ["threshold_lte"],
            critical: ["threshold_lte"],
            recovered: ["threshold_low_recovery"],
        },
        "required_condition_values": {
            low: {"lte": -17},
            critical: {"lte": -20},
            recovered: {"low": -17, "gt": -15},
        },
        "required_severities": {
            low: "warning",
            critical: "critical",
            recovered: "info",
        },
        "required_recovery_pairs": [
            {"fault": low, "recovery": recovered},
            {"fault": critical, "recovery": recovered},
        ],
        "required_exclusive_event_types": [critical],
        "priority_order": [{"higher": critical, "lower": low}],
        "max_rule_count": 4,
        "observation_type": observation_type,
    }


def _generate_asset_bundle(
    *,
    spec: dict[str, Any],
    reports_root: Path,
    max_records: int,
) -> dict[str, Any]:
    canonical_report = _latest_parser_report(reports_root, "CORPUS", spec["command"])
    seed = _canonical_parser_contract(canonical_report)
    groups = _split_snapshot_groups(canonical_report)
    period_counts = Counter(str(group["period"]) for group in groups)
    expected_period_counts = {period: 2 for period in PERIODS}
    if dict(period_counts) != expected_period_counts:
        raise AssertionError(
            f"canonical Parser corpus has wrong period coverage: {dict(period_counts)}"
        )
    safe = _sanitized_generation_context(
        groups,
        target_fields=_sampling_target_fields(seed, spec),
        max_records=max_records,
    )
    contract_result = OBSERVATION_SKILL.run_contract_generation(
        {
            "mode": "infer_and_validate",
            "command": spec["command"],
            "vendor": "sanitized",
            "model": "sanitized",
            "records": safe["records"],
            "record_groups": safe["record_groups"],
            "proposal": seed,
            "policy_intent": spec["policy_intent"],
            "event_intent": spec["event_intent"],
            "observation_targets": list(spec["observation_targets"]),
            "strict_observation_targets": True,
            "generation_provider": "llm",
            "max_agent_attempts": 5,
        }
    )
    if contract_result.get("success") is not True:
        raise AssertionError(
            f"ObservationContract generation failed for {spec['command']}: "
            + json.dumps(contract_result.get("agent_loop"), ensure_ascii=False, default=str)
        )
    contract = dict(contract_result["proposal"])
    semantics = dict(contract.get("state_semantics") or {})
    targets: list[dict[str, Any]] = []
    constraints_by_observation: dict[str, dict[str, Any]] = {}
    desired_observation_types = {
        str(item["observation_type"]) for item in spec["observation_targets"]
    }
    for field_name, raw_semantics in semantics.items():
        field_semantics = dict(raw_semantics or {})
        observation_type = str(field_semantics.get("observation_type") or "")
        if observation_type not in desired_observation_types:
            continue
        targets.append(
            {
                "field_name": field_name,
                "observation_type": observation_type,
                "field_semantics": field_semantics,
                "policy_constraints": spec["policy_constraints"],
            }
        )
        constraints_by_observation[observation_type] = _event_constraints(
            spec, observation_type, field_name
        )
    observed_targets = {str(target["observation_type"]) for target in targets}
    if observed_targets != desired_observation_types:
        raise AssertionError(
            f"governed observation targets for {spec['command']} are incomplete: "
            f"expected={sorted(desired_observation_types)}, actual={sorted(observed_targets)}"
        )
    policy_result = POLICY_SKILL.run_change_policy_generation(
        {
            "mode": "infer_and_validate",
            "records": safe["records"],
            "policy_targets": targets,
            "policy_intent": spec["policy_intent"],
            "generation_provider": "llm",
            "max_agent_attempts": 5,
        }
    )
    if policy_result.get("success") is not True:
        raise AssertionError(
            f"ChangePolicy generation failed for {spec['command']}: "
            + json.dumps(policy_result.get("agent_loop"), ensure_ascii=False, default=str)
        )
    event_result = EVENT_SKILL.run_event_rule_generation(
        {
            "contract": contract,
            "change_policies": policy_result["policies"],
            "intent": spec["event_intent"],
            "event_constraints_by_observation_type": constraints_by_observation,
            "generation_provider": "llm",
            "max_agent_attempts": 5,
        }
    )
    if event_result.get("success") is not True:
        raise AssertionError(
            f"EventRule generation failed for {spec['command']}: "
            + json.dumps(event_result.get("agent_loop"), ensure_ascii=False, default=str)
        )
    field_by_observation = {
        str(target["observation_type"]): str(target["field_name"])
        for target in targets
    }
    digest = hashlib.sha256(
        json.dumps(
            {
                "contract": contract,
                "policies": policy_result["policies"],
                "rules": event_result["rules"],
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    return {
        "key": spec["key"],
        "command": spec["command"],
        "reports": {"CORPUS": canonical_report},
        "groups": groups,
        "privacy": safe["privacy"],
        "contract": contract,
        "contract_agent_loop": contract_result.get("agent_loop"),
        "policies": list(policy_result["policies"]),
        "policy_agent_loop": policy_result.get("agent_loop"),
        "rules": list(event_result["rules"]),
        "event_agent_loop": event_result.get("agent_loop"),
        "field_by_observation": field_by_observation,
        "bundle_digest": digest,
    }


def _device_name(device_id: str) -> str:
    return device_id.split("-", 1)[0] or device_id


def _entity_key(contract: dict[str, Any], record: dict[str, Any]) -> str:
    return json.dumps(
        [record.get(field) for field in contract.get("primary_keys") or []],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _domain_observations(
    bundle: dict[str, Any], period: str
) -> list[dict[str, Any]]:
    contract = bundle["contract"]
    semantics = dict(contract.get("state_semantics") or {})
    output: list[dict[str, Any]] = []
    ordinal = 0
    for group in bundle["groups"]:
        if group["period"] != period:
            continue
        for record in group["records"]:
            key = _entity_key(contract, record)
            for observation_type, field_name in bundle["field_by_observation"].items():
                value = record.get(field_name)
                if value in (None, ""):
                    continue
                ordinal += 1
                semantic = dict(semantics.get(field_name) or {})
                context_fields = applicability_context_fields(
                    semantic.get("applicability")
                )
                context = (
                    {field: record.get(field) for field in context_fields}
                    if context_fields
                    else None
                )
                output.append(
                    {
                        "id": f"obs-{bundle['key']}-{period.lower()}-{ordinal}",
                        "device_id": group["device_id"],
                        "device_name": _device_name(group["device_id"]),
                        "entity_type": contract["entity_type"],
                        "entity_key": key,
                        "field": field_name,
                        "value": _number(value) if _number(value) is not None else value,
                        "unit": semantic.get("unit"),
                        "observation_type": observation_type,
                        "command": bundle["command"],
                        "observed_at": (
                            f"2026-{ {'Q1': '01', 'Q2': '04', 'Q3': '07'}[period] }"
                            "-01T00:00:00+00:00"
                        ),
                        "contract_version": "llm-candidate",
                        "context": context,
                        "dimensions": (
                            {APPLICABILITY_CONTEXT_KEY: context}
                            if context is not None
                            else {}
                        ),
                    }
                )
    return output


def _changes_for_bundle(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    previous = _domain_observations(bundle, "Q2")
    current = _domain_observations(bundle, "Q3")
    old_by_key = {
        (row["device_id"], row["entity_type"], row["entity_key"], row["field"]): row
        for row in previous
    }
    policy_by_field = {
        bundle["field_by_observation"][str(policy["observation_type"])]: policy
        for policy in bundle["policies"]
    }
    changes: list[dict[str, Any]] = []
    for row in current:
        key = (row["device_id"], row["entity_type"], row["entity_key"], row["field"])
        old = old_by_key.get(key)
        policy_payload = policy_by_field[row["field"]]
        comparison = compare_with_policy(
            policy_from_dict(policy_payload),
            old=old.get("value") if old else None,
            new=row["value"],
            field=row["field"],
        )
        if comparison.get("changed") is not True:
            continue
        changes.append(
            {
                "id": f"change-{bundle['key']}-{len(changes) + 1}",
                "device_id": row["device_id"],
                "device_name": row["device_name"],
                "entity_type": row["entity_type"],
                "entity_key": row["entity_key"],
                "field": row["field"],
                "old": old.get("value") if old else None,
                "new": row["value"],
                "old_value": old.get("value") if old else None,
                "new_value": row["value"],
                "unit": row.get("unit"),
                "observation_type": row["observation_type"],
                "command": row["command"],
                "change_type": "updated" if old else "initial",
                "comparison": comparison,
                "occurred_at": row["observed_at"],
                "domain": bundle["key"],
                "context": row.get("context"),
                "dimensions": dict(row.get("dimensions") or {}),
            }
        )
    return changes


def _events_for_changes(
    bundle: dict[str, Any], changes: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for change in changes:
        for rule in sorted(
            bundle["rules"], key=lambda item: -int(item.get("priority") or 100)
        ):
            condition = dict(rule.get("condition") or {})
            if not condition_matches(condition, change):
                continue
            events.append(
                {
                    "id": f"event-{bundle['key']}-{len(events) + 1}",
                    "event_type": rule.get("event_type"),
                    "severity": rule.get("severity"),
                    "event_class": rule.get("event_class") or "fault",
                    "device_id": change["device_id"],
                    "device_name": change["device_name"],
                    "domain": bundle["key"],
                    "entity_type": change["entity_type"],
                    "entity_key": change["entity_key"],
                    "field": change["field"],
                    "old_value": change["old"],
                    "current_value": change["new"],
                    "unit": change.get("unit"),
                    "rule_id": rule.get("name"),
                    "rule_version": 1,
                    "condition": condition,
                    "occurred_at": change["occurred_at"],
                    "change_id": change["id"],
                }
            )
            if bool(rule.get("exclusive")):
                break
    return events


def _build_report_summary(
    bundles: list[dict[str, Any]], source_root: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    q3_preview = preview_raw_path(
        file_path=source_root / "Q3",
        mode="parser_validation",
        vendor="H3C",
        model="S6850-56HF",
        metadata={"dataset_id": "lixian-q3-report-acceptance"},
    )
    observations = [
        item for bundle in bundles for item in _domain_observations(bundle, "Q3")
    ]
    changes = [item for bundle in bundles for item in _changes_for_bundle(bundle)]
    events = [
        item
        for bundle in bundles
        for item in _events_for_changes(
            bundle,
            [change for change in changes if change["domain"] == bundle["key"]],
        )
    ]
    rules = [
        {
            **rule,
            "rule_id": rule.get("name"),
            "version": 1,
        }
        for bundle in bundles
        for rule in bundle["rules"]
    ]
    bundle_digest = hashlib.sha256(
        "|".join(bundle["bundle_digest"] for bundle in bundles).encode("utf-8")
    ).hexdigest()
    current = evaluate_current_state(
        observations,
        rules,
        rule_source="lixian_llm_candidate_bundle",
        rule_bundle_digest=bundle_digest,
    )
    device_ids = sorted({row["device_id"] for row in observations})
    runs: list[dict[str, Any]] = []
    for device_id in device_ids:
        checks = []
        for bundle in bundles:
            domain_observations = [
                row
                for row in observations
                if row["device_id"] == device_id and row["command"] == bundle["command"]
            ]
            domain_findings = [
                row
                for row in current["findings"]
                if row["device_id"] == device_id and row["domain"] == bundle["key"]
            ]
            domain_changes = [
                row
                for row in changes
                if row["device_id"] == device_id and row["domain"] == bundle["key"]
            ]
            domain_events = [
                row
                for row in events
                if row["device_id"] == device_id and row["domain"] == bundle["key"]
            ]
            abnormal = bool(
                domain_findings
                or [row for row in domain_events if row["event_class"] != "recovery"]
            )
            checks.append(
                {
                    "domain": bundle["key"],
                    "label": "接口运行状态" if bundle["key"] == "interface" else "光模块功率",
                    "status": "异常" if abnormal else "本次覆盖范围内未发现当前异常",
                    "reason": (
                        "命中当前状态或周期变化故障规则"
                        if abnormal
                        else "结构化数据已由候选规则评估，未命中故障条件"
                    ),
                    "observation_count": len(domain_observations),
                    "evaluated_observation_count": len(domain_observations),
                    "current_finding_count": len(domain_findings),
                    "change_count": len(domain_changes),
                    "event_count": len(domain_events),
                }
            )
        runs.append(
            {
                "device_id": device_id,
                "device_name": _device_name(device_id),
                "status": "completed",
                "observations": [
                    row for row in observations if row["device_id"] == device_id
                ],
                "domain_checks": checks,
            }
        )
    eligible = int(q3_preview.get("ready") or 0) + int(
        q3_preview.get("template_missing") or 0
    ) + int(q3_preview.get("parse_failed") or 0)
    ready_rate = float(q3_preview.get("ready") or 0) / eligible if eligible else 0.0
    quality_status = (
        "ready"
        if q3_preview.get("template_missing") == 0
        and q3_preview.get("parse_failed") == 0
        and q3_preview.get("identity_unknown") == 0
        else "degraded"
    )
    summary = {
        "report_purpose": "history_compare",
        "run_count": len(device_ids),
        "device_count": len(device_ids),
        "status_counts": {"completed": len(device_ids)},
        "scope": {
            "target_count": len(device_ids),
            "completed_count": len(device_ids),
            "failed_count": 0,
            "skipped_count": 0,
            "cancelled_count": 0,
            "active_count": 0,
            "target_label": "Q2→Q3 两台 H3C 样本设备",
        },
        "quality": {
            "status": quality_status,
            "command_count": int(q3_preview.get("command_count") or 0),
            "snapshot_count": int(q3_preview.get("command_count") or 0),
            "parser_ready_count": int(q3_preview.get("ready") or 0),
            "template_missing_count": int(q3_preview.get("template_missing") or 0),
            "parse_failed_count": int(q3_preview.get("parse_failed") or 0),
            "ignored_count": int(q3_preview.get("ignored") or 0),
            "identity_unknown_count": int(q3_preview.get("identity_unknown") or 0),
            "parser_ready_rate": ready_rate,
            "observation_count": len(observations),
        },
        "change_count": len(changes),
        "event_count": len(events),
        "changes": changes,
        "events": events,
        "current_findings": current["findings"],
        "current_finding_count": current["finding_count"],
        "current_state_rule_coverage": {
            **{key: value for key, value in current.items() if key != "findings"},
            "bundle_digests": [bundle_digest],
        },
        "runs": runs,
        "deliverables": {
            "raw_cli_url": "local://lixian/Q3",
            "events_url": "events.json",
            "technical_report_url": "lixian-q2-q3-leadership-report.json",
        },
    }
    evidence = {
        "preview": {
            key: q3_preview.get(key)
            for key in (
                "file_count",
                "device_count",
                "command_count",
                "ready",
                "template_missing",
                "parse_failed",
                "ignored",
                "identity_unknown",
            )
        },
        "current_state": current,
        "changes": changes,
        "events": events,
    }
    return summary, evidence


def run_acceptance(
    *,
    source_root: Path,
    parser_reports_root: Path,
    output_dir: Path,
    max_records: int,
) -> dict[str, Any]:
    bundles = [
        _generate_asset_bundle(
            spec=dict(spec),
            reports_root=parser_reports_root,
            max_records=max_records,
        )
        for spec in ASSET_SPECS
    ]
    summary, replay_evidence = _build_report_summary(bundles, source_root)
    report = build_leadership_report(
        "offline-patrol-q2-q3-20260714",
        summary,
        generated_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
        issuer="网络运维中心",
        recipient="项目建设单位领导",
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    docx_path = output_dir / "lixian-q2-q3-leadership-report.docx"
    json_path = output_dir / "lixian-q2-q3-leadership-report.json"
    markdown_path = output_dir / "lixian-q2-q3-leadership-report.md"
    evidence_path = output_dir / "rule-generation-evidence.json"
    docx_path.write_bytes(render_leadership_docx(report))
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_leadership_markdown(report),
        encoding="utf-8",
    )
    public_bundles = [
        {
            key: bundle[key]
            for key in (
                "key",
                "command",
                "privacy",
                "contract",
                "contract_agent_loop",
                "policies",
                "policy_agent_loop",
                "rules",
                "event_agent_loop",
                "bundle_digest",
            )
        }
        for bundle in bundles
    ]
    evidence_path.write_text(
        json.dumps(
            {
                "success": True,
                "raw_cli_sent_to_llm": False,
                "generation_provider": "llm",
                "bundles": public_bundles,
                "replay": replay_evidence,
                "report_assessment": report["assessment"],
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    if not docx_path.read_bytes().startswith(b"PK"):
        raise AssertionError("leadership DOCX is not a valid OOXML package")
    return {
        "success": True,
        "generation_provider": "llm",
        "bundle_count": len(bundles),
        "policy_count": sum(len(bundle["policies"]) for bundle in bundles),
        "rule_count": sum(len(bundle["rules"]) for bundle in bundles),
        "change_count": summary["change_count"],
        "event_count": summary["event_count"],
        "current_finding_count": summary["current_finding_count"],
        "assessment": report["assessment"],
        "outputs": {
            "docx": str(docx_path),
            "json": str(json_path),
            "markdown": str(markdown_path),
            "evidence": str(evidence_path),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run real-LLM rule generation and leadership report acceptance"
    )
    parser.add_argument("--source-root", default=str(PROJECT_ROOT / "lixian"))
    parser.add_argument(
        "--parser-reports-root",
        default=str(PROJECT_ROOT / ".tmp" / "textfsm-agent-lixian-benchmark"),
    )
    parser.add_argument(
        "--output-dir",
        default=str(PROJECT_ROOT / ".tmp" / "lixian-rule-report-e2e"),
    )
    parser.add_argument("--max-records", type=int, default=120)
    args = parser.parse_args()
    try:
        result = run_acceptance(
            source_root=Path(args.source_root).resolve(),
            parser_reports_root=Path(args.parser_reports_root).resolve(),
            output_dir=Path(args.output_dir).resolve(),
            max_records=max(20, min(args.max_records, 240)),
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "success": False,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
