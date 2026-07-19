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

from src.core.parser.semantic_catalog import canonical_field_for_name  # noqa: E402
from src.core.patrol.change_policy import (  # noqa: E402
    compare_with_policy,
    policy_from_dict,
)
from src.core.patrol.command_splitter import (  # noqa: E402
    infer_device_from_filename,
    split_cli_capture,
)
from src.core.patrol.event_rules import (  # noqa: E402
    condition_matches,
    resolve_threshold_reference,
)
from src.core.patrol.leadership_report import (  # noqa: E402
    build_leadership_report,
    render_leadership_docx,
    render_leadership_markdown,
)
from src.core.patrol.raw_importer import (  # noqa: E402
    _read_text_best_effort,
    preview_raw_path,
)
from src.core.patrol.unstructured_diff import (  # noqa: E402
    build_unstructured_diff,
    resolve_unstructured_diff_profile,
    summarize_unstructured_changes,
)
from src.core.rule_control.dataset_context import (  # noqa: E402
    sanitize_structured_records,
)
from src.core.state.observation_applicability import (  # noqa: E402
    APPLICABILITY_CONTEXT_KEY,
    applicability_context_fields,
)
from src.core.workflows.patrol_batch_service import (  # noqa: E402
    PatrolBatchService,
    evaluate_current_state,
)

PERIODS = ("Q1", "Q2", "Q3")
PERIOD_PAIRS = (("Q1", "Q2"), ("Q2", "Q3"))
ASSET_SPECS = (
    {
        "key": "interface",
        "entity_type": "interface",
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
        "entity_type": "interface_optical",
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
            "光功率采用 dBm。必须优先使用每条结构化记录中的设备 Warning/Alarm 阈值；"
            "Warning 形成 warning，Alarm 形成 critical，恢复边界使用相应阈值加回差。"
            "只有 Alarm 时只生成 critical 与 recovery；空值、DOM 不支持和模块缺失"
            "不按低光功率处理。"
        ),
        "event_intent": (
            "按 observation_type 分别生成 Rx/Tx 低光功率、严重低光功率和恢复规则。"
            "warning 与 critical 必须使用不同阈值，critical 优先且互斥；"
            "Rx/Tx 规则名称必须包含方向以避免资产冲突。"
        ),
        "policy_constraints": {
            "allowed_compare_modes": ["threshold_low_and_deadband"],
            "require_structured_threshold_references": True,
            "required_threshold_reference_any": ["low", "critical_low"],
            "recovery_direction": "above",
            "minimum_hysteresis": 2,
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


def _latest_parser_reports(
    reports_root: Path, period: str, command: str
) -> list[dict[str, Any]]:
    directory = reports_root / period / _slug(command)
    candidates = [
        path
        for path in directory.glob("*.json")
        if not path.name.startswith("summary_")
    ]
    if not candidates:
        raise FileNotFoundError(f"parser benchmark report is missing: {directory}")
    latest: dict[tuple[str, str, str], tuple[Path, dict[str, Any]]] = {}
    for path in candidates:
        report = json.loads(path.read_text(encoding="utf-8"))
        identity = (
            str(report.get("vendor") or ""),
            str(report.get("model") or ""),
            str(report.get("command") or command),
        )
        current = latest.get(identity)
        if current is None or path.stat().st_mtime_ns > current[0].stat().st_mtime_ns:
            latest[identity] = (path, report)
    reports: list[dict[str, Any]] = []
    for identity in sorted(latest):
        path, report = latest[identity]
        if report.get("validation_pass_rate") != 1.0:
            raise AssertionError(f"Parser report did not pass all samples: {path}")
        report["_report_path"] = str(path)
        reports.append(report)
    return reports


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
    asset_token = _slug(
        f"{report.get('vendor') or 'unknown'}-{report.get('model') or 'unknown'}"
    )
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
                "group_id": (
                    f"snapshot-{asset_token}-{period.lower()}-"
                    f"{period_ordinals[period]}"
                ),
                "vendor": report.get("vendor"),
                "model": report.get("model"),
                "records": records,
            }
        )
    if offset != len(parsed):
        raise AssertionError("Parser report contains unassigned parsed records")
    return groups


def _expected_period_sample_counts(
    source_root: Path, command: str
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for period in PERIODS:
        count = 0
        for path in sorted((source_root / period).glob("*.txt")):
            device_name, _management_ip = infer_device_from_filename(path)
            blocks = split_cli_capture(
                _read_text_best_effort(path),
                device_name=device_name,
            )
            if any(block.command_canonical == command for block in blocks):
                count += 1
        counts[period] = count
    return counts


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


def _merge_rule_generation_seed(
    reports: list[dict[str, Any]],
    contracts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Merge model-specific Parser contracts without erasing their boundaries."""

    if not contracts:
        raise AssertionError("rule generation requires at least one Parser contract")
    entity_types = {str(contract.get("entity_type") or "") for contract in contracts}
    primary_keys = {
        tuple(str(item) for item in contract.get("primary_keys") or [])
        for contract in contracts
    }
    if len(entity_types) != 1 or len(primary_keys) != 1:
        identities = [
            f"{report.get('vendor')}/{report.get('model')}"
            for report in reports
        ]
        raise AssertionError(
            "model-specific Parser contracts disagree on entity identity; "
            "create explicit normalization mappings before shared rule generation: "
            + ", ".join(identities)
        )

    declared_by_contract = [
        {
            str(item)
            for item in [
                *(contract.get("required_fields") or []),
                *(contract.get("optional_fields") or []),
            ]
        }
        for contract in contracts
    ]
    declared = set().union(*declared_by_contract)
    required = set.intersection(
        *(
            {str(item) for item in contract.get("required_fields") or []}
            for contract in contracts
        )
    )
    required.update(next(iter(primary_keys)))

    validators: dict[str, dict[str, Any]] = {}
    semantics: dict[str, dict[str, Any]] = {}
    for report, contract in zip(reports, contracts, strict=True):
        identity = f"{report.get('vendor')}/{report.get('model')}"
        for field, raw in dict(contract.get("validators") or {}).items():
            candidate = dict(raw or {})
            if field in validators and validators[field] != candidate:
                raise AssertionError(
                    f"validator conflict for {field} in compatibility profile {identity}"
                )
            validators[str(field)] = candidate
        for field, raw in dict(contract.get("state_semantics") or {}).items():
            candidate = dict(raw or {})
            current = semantics.setdefault(str(field), {})
            for key, value in candidate.items():
                if key in current and current[key] != value:
                    raise AssertionError(
                        f"semantic conflict for {field}.{key} in compatibility "
                        f"profile {identity}"
                    )
                current[key] = value

    richest = max(contracts, key=lambda item: len(_declared_contract_fields(item)))
    ordered_fields = list(_declared_contract_fields(richest))
    ordered_fields.extend(sorted(declared - set(ordered_fields)))
    required_fields = [field for field in ordered_fields if field in required]
    optional_fields = [field for field in ordered_fields if field not in required]
    return {
        **{
            key: value
            for key, value in richest.items()
            if key
            not in {
                "required_fields",
                "optional_fields",
                "validators",
                "state_semantics",
                "evidence",
            }
        },
        "required_fields": required_fields,
        "optional_fields": optional_fields,
        "validators": validators,
        "state_semantics": semantics,
        "source": "model_contract_compatibility_merge",
        "evidence": {
            "compatibility_profiles": [
                {
                    "vendor": report.get("vendor"),
                    "model": report.get("model"),
                    "report_path": report.get("_report_path"),
                }
                for report in reports
            ]
        },
    }


def _declared_contract_fields(contract: dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            str(item)
            for item in [
                *(contract.get("required_fields") or []),
                *(contract.get("optional_fields") or []),
            ]
        )
    )


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


def _observation_target_fields(
    contract: dict[str, Any],
    target: dict[str, Any],
) -> list[str]:
    """Resolve one governed observation target within one Parser schema."""

    expected = str(target.get("canonical_field") or "").strip().casefold()
    observation_type = str(target.get("observation_type") or "").strip()
    semantics = dict(contract.get("state_semantics") or {})
    fields = _declared_contract_fields(contract)
    semantic_matches = [
        field
        for field in fields
        if str((semantics.get(field) or {}).get("observation_type") or "")
        == observation_type
    ]
    if len(semantic_matches) == 1:
        return semantic_matches
    expected_is_reference = bool(
        re.search(r"(?:^|_)(?:alarm|warning|threshold|high|low)(?:_|$)", expected)
    )
    matches: list[str] = []
    for field in fields:
        normalized = str(field).strip().casefold()
        if not expected_is_reference and re.search(
            r"(?:^|_)(?:alarm|warning|threshold|high|low)(?:_|$)",
            normalized,
        ):
            continue
        canonical = canonical_field_for_name(field) or normalized
        if canonical == expected or normalized == expected:
            matches.append(field)
    preferred = [
        field
        for field in matches
        if str((semantics.get(field) or {}).get("observation_type") or "")
        == observation_type
    ]
    return preferred if len(preferred) == 1 else matches


def _flatten_values(value: Any) -> list[Any]:
    if value in (None, "", [], {}):
        return []
    if isinstance(value, list):
        return [item for nested in value for item in _flatten_values(nested)]
    if isinstance(value, dict):
        return []
    return [value]


def _merge_profile_agent_loops(profiles: list[dict[str, Any]]) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for profile in profiles:
        loop = dict(profile.get("contract_agent_loop") or {})
        for attempt in loop.get("attempts") or []:
            if isinstance(attempt, dict):
                attempts.append({**attempt, "attempt": len(attempts) + 1})
        summaries.append(
            {
                "vendor": profile["vendor"],
                "model": profile["model"],
                "status": profile.get("contract_status"),
                "target_count": len(profile.get("targets") or []),
            }
        )
    return {
        "ready_for_approval": all(
            item["status"] in {"passed", "not_applicable"} for item in summaries
        ),
        "attempt_count": len(attempts),
        "attempts": attempts,
        "compatibility_profiles": summaries,
    }


def _event_constraints(
    spec: dict[str, Any],
    observation_type: str,
    field_name: str,
    policy: dict[str, Any] | None = None,
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
    recovered = f"Optical{direction}PowerRecovered"
    references = dict((policy or {}).get("threshold_references") or {})
    fault_specs = [
        ("low", low, "warning"),
        ("critical_low", critical, "critical"),
    ]
    enabled_faults = [item for item in fault_specs if references.get(item[0])]
    if not enabled_faults:
        raise AssertionError(
            f"{observation_type} has no approved structured threshold reference"
        )
    primary_role, _primary_event, _primary_severity = enabled_faults[0]
    required_types = [event_type for _role, event_type, _severity in enabled_faults]
    required_types.append(recovered)
    classes = {
        event_type: "fault" for _role, event_type, _severity in enabled_faults
    }
    classes[recovered] = "recovery"
    kinds = {
        event_type: ["threshold_field_lte"]
        for _role, event_type, _severity in enabled_faults
    }
    kinds[recovered] = ["threshold_field_low_recovery"]
    values = {
        event_type: {
            "reference_field": references[role]["field_name"],
        }
        for role, event_type, _severity in enabled_faults
    }
    values[recovered] = {
        "reference_field": references[primary_role]["field_name"],
    }
    severities = {
        event_type: severity
        for _role, event_type, severity in enabled_faults
    }
    severities[recovered] = "info"
    constraints = {
        "required_event_types": required_types,
        "required_event_classes": classes,
        "required_condition_kinds": kinds,
        "required_condition_values": values,
        "required_severities": severities,
        "required_recovery_pairs": [
            {"fault": event_type, "recovery": recovered}
            for _role, event_type, _severity in enabled_faults
        ],
        "max_rule_count": len(required_types) + 1,
        "observation_type": observation_type,
    }
    if references.get("low") and references.get("critical_low"):
        constraints["required_exclusive_event_types"] = [critical]
        constraints["priority_order"] = [{"higher": critical, "lower": low}]
    return constraints


def _generate_asset_bundle(
    *,
    spec: dict[str, Any],
    reports_root: Path,
    source_root: Path,
    max_records: int,
) -> dict[str, Any]:
    canonical_reports = _latest_parser_reports(
        reports_root,
        "CORPUS",
        spec["command"],
    )
    contracts = [_canonical_parser_contract(report) for report in canonical_reports]
    report_groups = [
        _split_snapshot_groups(report) for report in canonical_reports
    ]
    groups = [group for items in report_groups for group in items]
    period_counts = Counter(str(group["period"]) for group in groups)
    expected_period_counts = _expected_period_sample_counts(
        source_root,
        spec["command"],
    )
    if dict(period_counts) != expected_period_counts:
        raise AssertionError(
            f"canonical Parser corpus has wrong period coverage: {dict(period_counts)}"
        )
    per_profile_limit = max(20, max_records // max(1, len(canonical_reports)))
    profiles: list[dict[str, Any]] = []
    desired_observation_types = {
        str(item["observation_type"]) for item in spec["observation_targets"]
    }
    for report, parser_contract, profile_groups in zip(
        canonical_reports,
        contracts,
        report_groups,
        strict=True,
    ):
        active_targets = [
            target
            for target in spec["observation_targets"]
            if len(_observation_target_fields(parser_contract, dict(target))) == 1
        ]
        profile: dict[str, Any] = {
            "vendor": str(report.get("vendor") or ""),
            "model": str(report.get("model") or ""),
            "groups": profile_groups,
            "parser_contract": parser_contract,
            "targets": [],
        }
        if not active_targets:
            profile.update(
                contract=parser_contract,
                contract_status="not_applicable",
                contract_agent_loop={
                    "ready_for_approval": True,
                    "next_action": "no_observed_measurement_target",
                    "attempts": [],
                },
                privacy={
                    "success": True,
                    "raw_cli_included": False,
                    "snapshot_group_count": 0,
                    "sampled_record_count": 0,
                    "removed_fields": {},
                    "pseudonymized_fields": {},
                },
            )
            profiles.append(profile)
            continue
        target_fields = tuple(
            field
            for target in active_targets
            for field in _observation_target_fields(parser_contract, dict(target))
        )
        safe = _sanitized_generation_context(
            profile_groups,
            target_fields=target_fields,
            max_records=per_profile_limit,
        )
        contract_result = OBSERVATION_SKILL.run_contract_generation(
            {
                "mode": "infer_and_validate",
                "command": spec["command"],
                "vendor": "sanitized",
                "model": "sanitized",
                "records": safe["records"],
                "record_groups": safe["record_groups"],
                "proposal": parser_contract,
                "policy_intent": spec["policy_intent"],
                "event_intent": spec["event_intent"],
                "observation_targets": [dict(item) for item in active_targets],
                "strict_observation_targets": True,
                "generation_provider": "llm",
                "max_agent_attempts": 5,
            }
        )
        if contract_result.get("success") is not True:
            identity = f"{profile['vendor']}/{profile['model']}"
            raise AssertionError(
                f"ObservationContract generation failed for {identity} "
                f"{spec['command']}: "
                + json.dumps(
                    contract_result.get("agent_loop"),
                    ensure_ascii=False,
                    default=str,
                )
            )
        contract = dict(contract_result["proposal"])
        semantics = dict(contract.get("state_semantics") or {})
        targets: list[dict[str, Any]] = []
        for field_name, raw_semantics in semantics.items():
            field_semantics = dict(raw_semantics or {})
            observation_type = str(field_semantics.get("observation_type") or "")
            if observation_type not in {
                str(item["observation_type"]) for item in active_targets
            }:
                continue
            targets.append(
                {
                    "field_name": field_name,
                    "observation_type": observation_type,
                    "field_semantics": field_semantics,
                    "threshold_references": dict(
                        field_semantics.get("threshold_references") or {}
                    ),
                }
            )
        profile.update(
            contract=contract,
            contract_status="passed",
            contract_agent_loop=contract_result.get("agent_loop"),
            privacy=safe["privacy"],
            targets=targets,
        )
        profiles.append(profile)

    observed_targets = {
        str(target["observation_type"])
        for profile in profiles
        for target in profile["targets"]
    }
    if observed_targets != desired_observation_types:
        raise AssertionError(
            f"governed observation targets for {spec['command']} are incomplete: "
            f"expected={sorted(desired_observation_types)}, actual={sorted(observed_targets)}"
        )

    policy_targets: list[dict[str, Any]] = []
    historical_values_by_field: dict[str, list[Any]] = {}
    field_by_observation = {
        str(item["observation_type"]): str(item["canonical_field"])
        for item in spec["observation_targets"]
    }
    canonical_semantics: dict[str, dict[str, Any]] = {}
    for observation_type, canonical_field in field_by_observation.items():
        profile_targets = [
            (profile, target)
            for profile in profiles
            for target in profile["targets"]
            if str(target["observation_type"]) == observation_type
        ]
        if not profile_targets:
            raise AssertionError(f"no compatibility profile exposes {observation_type}")
        semantic = dict(profile_targets[0][1]["field_semantics"])
        for _profile, target in profile_targets[1:]:
            candidate = dict(target["field_semantics"])
            for key in ("semantic_type", "unit"):
                if semantic.get(key) and candidate.get(key) and semantic[key] != candidate[key]:
                    raise AssertionError(
                        f"semantic conflict for {observation_type}.{key}"
                    )
        runtime_references: dict[str, dict[str, Any]] = {}
        values: list[Any] = []
        for profile, target in profile_targets:
            source_field = str(target["field_name"])
            values.extend(
                item
                for group in profile["groups"]
                for record in group["records"]
                for item in _flatten_values(record.get(source_field))
            )
            for role, raw_reference in dict(
                target.get("threshold_references") or {}
            ).items():
                if not isinstance(raw_reference, dict):
                    continue
                context_key = str(
                    raw_reference.get("context_key") or f"threshold.{role}"
                )
                candidate_reference = {
                    "field_name": context_key,
                    "context_key": context_key,
                    "level": str(raw_reference.get("level") or "reference"),
                    "source": "structured_reference",
                }
                current = runtime_references.get(str(role))
                if current and current != candidate_reference:
                    raise AssertionError(
                        f"threshold reference conflict for {observation_type}.{role}"
                    )
                runtime_references[str(role)] = candidate_reference
        semantic.update(
            observation_type=observation_type,
            comparison_role="observation",
            monitor=True,
        )
        if runtime_references:
            semantic["threshold_references"] = runtime_references
            semantic["context_fields"] = sorted(
                reference["field_name"]
                for reference in runtime_references.values()
            )
        canonical_semantics[canonical_field] = semantic
        historical_values_by_field[canonical_field] = values
        policy_targets.append(
            {
                "field_name": canonical_field,
                "observation_type": observation_type,
                "field_semantics": semantic,
                "threshold_references": runtime_references,
                "policy_constraints": spec["policy_constraints"],
            }
        )

    contract = {
        "category": spec["key"],
        "entity_type": spec["entity_type"],
        "primary_keys": ["interface"],
        "required_fields": ["interface"],
        "optional_fields": list(field_by_observation.values()),
        "validators": {},
        "state_semantics": {
            "interface": {"semantic_type": "identity", "monitor": False},
            **canonical_semantics,
        },
        "source": "compatibility_profile_observation_projection",
        "evidence": {
            "compatibility_profiles": [
                {
                    "vendor": profile["vendor"],
                    "model": profile["model"],
                    "contract_status": profile["contract_status"],
                    "target_fields": {
                        str(target["observation_type"]): str(target["field_name"])
                        for target in profile["targets"]
                    },
                }
                for profile in profiles
            ]
        },
    }
    policy_result = POLICY_SKILL.run_change_policy_generation(
        {
            "mode": "infer_and_validate",
            "records": [],
            "historical_values_by_field": historical_values_by_field,
            "policy_targets": policy_targets,
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
    policies = list(policy_result["policies"])
    policies_by_observation = {
        str(policy.get("observation_type") or ""): policy
        for policy in policies
    }
    constraints_by_observation = {
        str(target["observation_type"]): _event_constraints(
            spec,
            str(target["observation_type"]),
            str(target["field_name"]),
            policies_by_observation.get(str(target["observation_type"])),
        )
        for target in policy_targets
    }
    event_result = EVENT_SKILL.run_event_rule_generation(
        {
            "contract": contract,
            "change_policies": policies,
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
    digest = hashlib.sha256(
        json.dumps(
            {
                "contract": contract,
                "profile_contracts": [profile["contract"] for profile in profiles],
                "policies": policy_result["policies"],
                "rules": event_result["rules"],
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    privacy_rows = [dict(profile.get("privacy") or {}) for profile in profiles]
    privacy = {
        "success": all(row.get("success") is True for row in privacy_rows),
        "raw_cli_included": False,
        "snapshot_group_count": sum(
            int(row.get("snapshot_group_count") or 0) for row in privacy_rows
        ),
        "sampled_record_count": sum(
            int(row.get("sampled_record_count") or 0) for row in privacy_rows
        ),
        "removed_fields": dict(
            sum((Counter(row.get("removed_fields") or {}) for row in privacy_rows), Counter())
        ),
        "pseudonymized_fields": dict(
            sum(
                (Counter(row.get("pseudonymized_fields") or {}) for row in privacy_rows),
                Counter(),
            )
        ),
    }
    return {
        "key": spec["key"],
        "command": spec["command"],
        "reports": {"CORPUS": canonical_reports},
        "groups": groups,
        "profiles": profiles,
        "privacy": privacy,
        "contract": contract,
        "contracts": [profile["contract"] for profile in profiles],
        "contract_agent_loop": _merge_profile_agent_loops(profiles),
        "policies": policies,
        "policy_agent_loop": policy_result.get("agent_loop"),
        "rules": list(event_result["rules"]),
        "event_agent_loop": event_result.get("agent_loop"),
        "field_by_observation": field_by_observation,
        "bundle_digest": digest,
    }


def _device_name(device_id: str) -> str:
    return device_id.split("-", 1)[0] or device_id


def _period_unstructured_outputs(
    source_root: Path,
    period: str,
) -> dict[tuple[str, str], dict[str, Any]]:
    outputs: dict[tuple[str, str], dict[str, Any]] = {}
    for path in sorted((source_root / period).glob("*.txt")):
        device_name, management_ip = infer_device_from_filename(path)
        text = _read_text_best_effort(path)
        blocks = split_cli_capture(text, device_name=device_name)
        device_id = (
            f"{device_name}-{management_ip}" if management_ip else str(device_name or path.stem)
        )
        for block in blocks:
            profile = resolve_unstructured_diff_profile(block.command_canonical)
            if profile is None:
                continue
            outputs[(device_id, block.command_canonical)] = {
                "device_id": device_id,
                "device_name": str(device_name or device_id),
                "command": block.command_canonical,
                "raw_text": block.raw_output,
                "profile": profile,
                "source_path": str(path),
            }
    return outputs


def _unstructured_changes(
    source_root: Path,
    *,
    previous_period: str,
    current_period: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    previous = _period_unstructured_outputs(source_root, previous_period)
    current = _period_unstructured_outputs(source_root, current_period)
    common_keys = sorted(set(previous).intersection(current))
    changes: list[dict[str, Any]] = []
    for device_id, command in common_keys:
        old = previous[(device_id, command)]
        new = current[(device_id, command)]
        diff = build_unstructured_diff(
            old["raw_text"],
            new["raw_text"],
            command=command,
            profile=new["profile"],
        )
        if not diff.get("changed"):
            continue
        token = hashlib.sha256(
            f"{previous_period}|{current_period}|{device_id}|{command}".encode()
        ).hexdigest()[:16]
        changes.append(
            {
                "id": f"change-raw-{token}",
                "run_id": f"raw-{current_period.lower()}-{token}",
                "device_id": device_id,
                "device_name": new["device_name"],
                "domain": "raw",
                "command": command,
                "entity_type": "raw_command_output",
                "entity_key": command,
                "field": "raw_text_hash",
                "observation_type": "raw.command.hash",
                "old_value": hashlib.sha256(old["raw_text"].encode()).hexdigest(),
                "new_value": hashlib.sha256(new["raw_text"].encode()).hexdigest(),
                "change_type": "modified",
                "event_status": "no_event",
                "no_event_reason": "active_rules_evaluated_no_match",
                "comparison_type": f"unstructured_{diff['comparison_mode']}",
                "dimensions": {
                    "command": command,
                    "unstructured_diff": diff,
                },
                "previous_snapshot_id": f"snapshot-raw-{previous_period.lower()}-{token}",
                "current_snapshot_id": f"snapshot-raw-{current_period.lower()}-{token}",
                "detected_at": "2026-07-01T00:00:00+00:00",
            }
        )
    return changes, summarize_unstructured_changes(
        changes,
        collected_output_count=len(current),
        compared_output_count=len(common_keys),
    )


def _entity_key(contract: dict[str, Any], record: dict[str, Any]) -> str:
    return json.dumps(
        [record.get(field) for field in contract.get("primary_keys") or []],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _domain_observations(
    bundle: dict[str, Any], period: str
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    ordinal = 0
    canonical_entity_type = str(bundle["contract"]["entity_type"])
    for profile in bundle["profiles"]:
        contract = dict(profile["contract"])
        semantics = dict(contract.get("state_semantics") or {})
        profile_targets = {
            str(target["observation_type"]): str(target["field_name"])
            for target in profile["targets"]
        }
        contract_digest = hashlib.sha256(
            json.dumps(
                contract,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        profile_token = hashlib.sha256(
            f"{profile['vendor']}|{profile['model']}".encode()
        ).hexdigest()[:10]
        for group in profile["groups"]:
            if group["period"] != period:
                continue
            for record in group["records"]:
                key = _entity_key(contract, record)
                device_token = hashlib.sha256(
                    str(group["device_id"]).encode("utf-8")
                ).hexdigest()[:10]
                parser_candidate_id = (
                    f"parser-candidate-{bundle['key']}-{profile_token}"
                )
                contract_candidate_id = (
                    f"contract-candidate-{bundle['key']}-{profile_token}"
                )
                for observation_type, source_field in profile_targets.items():
                    canonical_field = bundle["field_by_observation"][observation_type]
                    raw_value = record.get(source_field)
                    indexed_values = (
                        list(enumerate(raw_value))
                        if isinstance(raw_value, list)
                        else [(None, raw_value)]
                    )
                    for value_index, value in indexed_values:
                        if value in (None, ""):
                            continue
                        ordinal += 1
                        semantic = dict(semantics.get(source_field) or {})
                        context_fields = set(
                            applicability_context_fields(
                                semantic.get("applicability")
                            )
                        )
                        context_fields.update(
                            str(item)
                            for item in semantic.get("context_fields") or []
                            if str(item).strip()
                        )
                        context = None
                        if context_fields:
                            context = {}
                            for context_field in context_fields:
                                context_value = record.get(context_field)
                                if value_index is not None and isinstance(
                                    context_value,
                                    list,
                                ):
                                    context_value = (
                                        context_value[value_index]
                                        if value_index < len(context_value)
                                        else None
                                    )
                                context[context_field] = (
                                    _number(context_value)
                                    if _number(context_value) is not None
                                    else context_value
                                )
                            runtime_threshold_fields: list[str] = []
                            local_threshold_fields: list[str] = []
                            for role, raw_reference in dict(
                                semantic.get("threshold_references") or {}
                            ).items():
                                if not isinstance(raw_reference, dict):
                                    continue
                                local_field = str(
                                    raw_reference.get("field_name") or ""
                                ).strip()
                                context_key = str(
                                    raw_reference.get("context_key")
                                    or f"threshold.{role}"
                                ).strip()
                                if not local_field or not context_key:
                                    continue
                                local_value = context.get(local_field)
                                if local_value in (None, ""):
                                    continue
                                context[context_key] = local_value
                                local_threshold_fields.append(local_field)
                                runtime_threshold_fields.append(context_key)
                            populated_thresholds = sorted(
                                set(
                                    runtime_threshold_fields
                                    or local_threshold_fields
                                )
                            )
                            if populated_thresholds:
                                context["threshold_source"] = "device_output"
                                context["threshold_confidence"] = "authoritative"
                                context["threshold_fields"] = populated_thresholds
                        dimensions = {
                            str(canonical_field_for_name(primary_key) or primary_key): record.get(
                                primary_key
                            )
                            for primary_key in contract.get("primary_keys") or []
                        }
                        if value_index is not None:
                            dimensions["value_index"] = value_index + 1
                        if context is not None:
                            dimensions[APPLICABILITY_CONTEXT_KEY] = context
                        output.append(
                            {
                                "id": f"obs-{bundle['key']}-{period.lower()}-{ordinal}",
                                "run_id": f"patrol-{period.lower()}-{device_token}",
                                "snapshot_id": (
                                    f"snapshot-{bundle['key']}-{period.lower()}-"
                                    f"{device_token}"
                                ),
                                "evidence_id": (
                                    f"raw-{bundle['key']}-{period.lower()}-{device_token}"
                                ),
                                "device_id": group["device_id"],
                                "device_name": _device_name(group["device_id"]),
                                "entity_type": canonical_entity_type,
                                "entity_key": key,
                                "field": canonical_field,
                                "source_field": source_field,
                                "value": (
                                    _number(value)
                                    if _number(value) is not None
                                    else value
                                ),
                                "unit": semantic.get("unit"),
                                "observation_type": observation_type,
                                "command": bundle["command"],
                                "observed_at": (
                                    f"2026-{ {'Q1': '01', 'Q2': '04', 'Q3': '07'}[period] }"
                                    "-01T00:00:00+00:00"
                                ),
                                "contract_version": "llm-candidate",
                                "parser_template_id": parser_candidate_id,
                                "parser_version": 1,
                                "observation_contract_id": contract_candidate_id,
                                "observation_contract_digest": contract_digest,
                                "context": context,
                                "dimensions": dimensions,
                            }
                        )
    return output


def _observation_identity(row: dict[str, Any]) -> tuple[str, str, str, str, str]:
    dimensions = dict(row.get("dimensions") or {})
    dimensions.pop(APPLICABILITY_CONTEXT_KEY, None)
    return (
        str(row.get("device_id") or ""),
        str(row.get("entity_type") or ""),
        str(row.get("entity_key") or ""),
        str(row.get("observation_type") or row.get("field") or ""),
        json.dumps(
            dimensions,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ),
    )


def _changes_for_bundle(
    bundle: dict[str, Any],
    *,
    previous_period: str,
    current_period: str,
) -> list[dict[str, Any]]:
    previous = _domain_observations(bundle, previous_period)
    current = _domain_observations(bundle, current_period)
    old_by_key = {_observation_identity(row): row for row in previous}
    policy_by_observation = {
        str(policy["observation_type"]): policy for policy in bundle["policies"]
    }
    changes: list[dict[str, Any]] = []
    for row in current:
        key = _observation_identity(row)
        old = old_by_key.get(key)
        policy_payload = policy_by_observation[row["observation_type"]]
        comparison = compare_with_policy(
            policy_from_dict(policy_payload),
            old=old.get("value") if old else None,
            new=row["value"],
            field=row["field"],
            context=dict(row.get("context") or {}),
        )
        if comparison.get("changed") is not True:
            continue
        changes.append(
            {
                "id": (
                    f"change-{bundle['key']}-{previous_period.lower()}-"
                    f"{current_period.lower()}-{len(changes) + 1}"
                ),
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
                "run_id": row.get("run_id"),
                "previous_snapshot_id": old.get("snapshot_id") if old else None,
                "current_snapshot_id": row.get("snapshot_id"),
                "previous_observation_id": old.get("id") if old else None,
                "current_observation_id": row.get("id"),
                "raw_evidence_id": row.get("evidence_id"),
                "parser_template_id": row.get("parser_template_id"),
                "parser_version": row.get("parser_version"),
                "observation_contract_id": row.get("observation_contract_id"),
                "observation_contract_version": row.get("contract_version"),
                "change_policy_id": (
                    f"policy-candidate-{row['observation_type']}"
                ),
                "change_policy_version_id": 1,
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
            reference_field, reference_value = resolve_threshold_reference(
                condition,
                dict(change.get("context") or {}),
            )
            events.append(
                {
                    "id": f"event-{bundle['key']}-{len(events) + 1}",
                    "event_type": rule.get("event_type"),
                    "severity": rule.get("severity"),
                    "event_class": rule.get("event_class") or "fault",
                    "status_after_occurrence": (
                        "resolved"
                        if str(rule.get("event_class") or "fault") == "recovery"
                        else "open"
                    ),
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
                    "reference_field": reference_field,
                    "reference_value": reference_value,
                    "dimensions": dict(change.get("dimensions") or {}),
                    "occurred_at": change["occurred_at"],
                    "change_id": change["id"],
                    "event_rule_id": rule.get("name"),
                    "event_rule_version_id": 1,
                    "event_id": f"event-{bundle['key']}-{len(events) + 1}",
                    "event_occurrence_id": (
                        f"occurrence-{bundle['key']}-{len(events) + 1}"
                    ),
                }
            )
            if bool(rule.get("exclusive")):
                break
    return events


def _build_report_summary(
    bundles: list[dict[str, Any]],
    source_root: Path,
    *,
    previous_period: str,
    current_period: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    current_preview = preview_raw_path(
        file_path=source_root / current_period,
        mode="parser_validation",
        metadata={
            "dataset_id": (
                f"lixian-{current_period.lower()}-report-acceptance"
            )
        },
    )
    observations = [
        item
        for bundle in bundles
        for item in _domain_observations(bundle, current_period)
    ]
    changes = [
        item
        for bundle in bundles
        for item in _changes_for_bundle(
            bundle,
            previous_period=previous_period,
            current_period=current_period,
        )
    ]
    raw_changes, unstructured_comparison = _unstructured_changes(
        source_root,
        previous_period=previous_period,
        current_period=current_period,
    )
    changes.extend(raw_changes)
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
    analysis = {
        "attempt_id": (
            f"analysis-{previous_period.lower()}-{current_period.lower()}-candidate"
        ),
        "revision": "lixian-production-pilot-v1",
        "completed_at": (
            f"2026-{ {'Q1': '01', 'Q2': '04', 'Q3': '07'}[current_period] }"
            "-01T00:05:00+00:00"
        ),
        "rule_bundle_digest": bundle_digest,
        "source_change_count": len(changes),
    }
    workflow_run_id = (
        f"workflow-{previous_period.lower()}-{current_period.lower()}"
    )
    for event in events:
        change = next(
            item for item in changes if item.get("id") == event.get("change_id")
        )
        event["evidence_chain"] = {
            "analysis_attempt_id": analysis["attempt_id"],
            "analysis_revision": analysis["revision"],
            "workflow_run_id": workflow_run_id,
            "change_id": event.get("change_id"),
            "patrol_run_id": change.get("run_id"),
            "previous_snapshot_id": change.get("previous_snapshot_id"),
            "current_snapshot_id": change.get("current_snapshot_id"),
            "raw_evidence_id": change.get("raw_evidence_id"),
            "current_observation_id": change.get("current_observation_id"),
            "parser_template_id": change.get("parser_template_id"),
            "parser_version": change.get("parser_version"),
            "observation_contract_id": change.get("observation_contract_id"),
            "observation_contract_version": change.get(
                "observation_contract_version"
            ),
            "change_policy_id": change.get("change_policy_id"),
            "change_policy_version_id": change.get("change_policy_version_id"),
            "event_rule_id": event.get("event_rule_id"),
            "event_rule_version_id": event.get("event_rule_version_id"),
            "event_id": event.get("event_id"),
            "event_occurrence_id": event.get("event_occurrence_id"),
            "rule_bundle_digest": bundle_digest,
        }
    current = evaluate_current_state(
        observations,
        rules,
        rule_source="lixian_llm_candidate_bundle",
        rule_bundle_digest=bundle_digest,
    )
    device_ids = sorted({row["device_id"] for row in observations})
    runs: list[dict[str, Any]] = []
    for device_id in device_ids:
        device_observations = [
            row for row in observations if row["device_id"] == device_id
        ]
        device_changes = [row for row in changes if row["device_id"] == device_id]
        device_events = [row for row in events if row["device_id"] == device_id]
        device_current = evaluate_current_state(
            device_observations,
            rules,
            rule_source="lixian_llm_candidate_bundle",
            rule_bundle_digest=bundle_digest,
        )
        checks = PatrolBatchService.domain_checks(
            observations=device_observations,
            command_details=[],
            changes=device_changes,
            events=device_events,
            current_findings=device_current["findings"],
            rule_coverage=device_current,
        )
        runs.append(
            {
                "run_id": (
                    f"run-{previous_period.lower()}-{current_period.lower()}-"
                    f"{device_id.casefold()}"
                ),
                "device_id": device_id,
                "device_name": _device_name(device_id),
                "status": "completed",
                "observations": device_observations,
                "domain_checks": checks,
            }
        )
    eligible = int(current_preview.get("ready") or 0) + int(
        current_preview.get("template_missing") or 0
    ) + int(current_preview.get("parse_failed") or 0)
    ready_rate = (
        float(current_preview.get("ready") or 0) / eligible if eligible else 0.0
    )
    quality_status = (
        "ready"
        if current_preview.get("template_missing") == 0
        and current_preview.get("parse_failed") == 0
        and current_preview.get("identity_unknown") == 0
        else "degraded"
    )
    summary = {
        "report_purpose": "history_compare",
        "workflow_run_id": workflow_run_id,
        "analysis": analysis,
        "periods": [
            {
                "role": "baseline",
                "period": previous_period,
                "observed_at": (
                    f"2026-{ {'Q1': '01', 'Q2': '04', 'Q3': '07'}[previous_period] }"
                    "-01T00:00:00+00:00"
                ),
            },
            {
                "role": "current",
                "period": current_period,
                "observed_at": (
                    f"2026-{ {'Q1': '01', 'Q2': '04', 'Q3': '07'}[current_period] }"
                    "-01T00:00:00+00:00"
                ),
            },
        ],
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
            "target_label": (
                f"{previous_period}→{current_period} {len(device_ids)} 台 H3C 样本设备"
            ),
        },
        "quality": {
            "status": quality_status,
            "command_count": int(current_preview.get("command_count") or 0),
            "snapshot_count": int(current_preview.get("command_count") or 0),
            "parser_ready_count": int(current_preview.get("ready") or 0),
            "parser_eligible_count": eligible,
            "template_missing_count": int(
                current_preview.get("template_missing") or 0
            ),
            "parse_failed_count": int(current_preview.get("parse_failed") or 0),
            "ignored_count": int(current_preview.get("ignored") or 0),
            "raw_compare_count": int(current_preview.get("raw_compare") or 0),
            "identity_unknown_count": int(
                current_preview.get("identity_unknown") or 0
            ),
            "parser_ready_rate": ready_rate,
            "observation_count": len(observations),
        },
        "change_count": len(changes),
        "event_count": len(events),
        "changes": changes,
        "unstructured_comparison": unstructured_comparison,
        "events": events,
        "current_findings": current["findings"],
        "current_finding_count": current["finding_count"],
        "current_state_rule_coverage": {
            **{key: value for key, value in current.items() if key != "findings"},
            "bundle_digests": [bundle_digest],
        },
        "runs": runs,
        "deliverables": {
            "raw_cli_url": f"local://lixian/{current_period}",
            "events_url": "events.json",
            "technical_report_url": (
                f"lixian-{previous_period.lower()}-{current_period.lower()}-"
                "leadership-report.json"
            ),
        },
    }
    evidence = {
        "preview": {
            key: current_preview.get(key)
            for key in (
                "file_count",
                "device_count",
                "command_count",
                "ready",
                "template_missing",
                "parse_failed",
                "ignored",
                "raw_compare",
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
            source_root=source_root,
            max_records=max_records,
        )
        for spec in ASSET_SPECS
    ]
    output_dir.mkdir(parents=True, exist_ok=True)
    evidence_path = output_dir / "rule-generation-evidence.json"
    pair_results: dict[str, dict[str, Any]] = {}
    replay_evidence: dict[str, Any] = {}
    previous_summary: dict[str, Any] | None = None
    previous_report: dict[str, Any] | None = None
    for previous_period, current_period in PERIOD_PAIRS:
        pair_key = f"{previous_period}->{current_period}"
        pair_slug = f"{previous_period.lower()}-{current_period.lower()}"
        summary, pair_evidence = _build_report_summary(
            bundles,
            source_root,
            previous_period=previous_period,
            current_period=current_period,
        )
        if previous_summary is not None and previous_report is not None:
            summary["previous_period"] = {
                "risks": list(previous_report.get("risks") or []),
                "correlated_evidence": list(
                    previous_report.get("correlated_evidence") or []
                ),
                "events": list(previous_summary.get("events") or []),
                "periods": list(previous_summary.get("periods") or []),
                "change_count": previous_summary.get("change_count"),
                "event_count": previous_summary.get("event_count"),
                "metrics": dict(previous_report.get("metrics") or {}),
                "event_summary": dict(previous_report.get("event_summary") or {}),
                "analysis": dict(previous_summary.get("analysis") or {}),
                "continuity_evidence": {
                    "device_identity_match": True,
                    "device_count": len(
                        {
                            str(item.get("device_id") or "")
                            for item in summary.get("runs") or []
                        }
                    ),
                },
            }
        report = build_leadership_report(
            f"offline-patrol-{pair_slug}-20260714",
            summary,
            generated_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
            issuer="网络运维中心",
            recipient="项目建设单位领导",
        )
        docx_path = output_dir / f"lixian-{pair_slug}-leadership-report.docx"
        json_path = output_dir / f"lixian-{pair_slug}-leadership-report.json"
        markdown_path = output_dir / f"lixian-{pair_slug}-leadership-report.md"
        docx_path.write_bytes(render_leadership_docx(report))
        json_path.write_text(
            json.dumps(
                report,
                ensure_ascii=False,
                indent=2,
                default=str,
                allow_nan=False,
            ),
            encoding="utf-8",
        )
        markdown_path.write_text(
            render_leadership_markdown(report),
            encoding="utf-8",
        )
        if not docx_path.read_bytes().startswith(b"PK"):
            raise AssertionError(
                f"leadership DOCX is not a valid OOXML package: {docx_path}"
            )
        unresolved_fault_event_count = sum(
            str(item.get("event_class") or "fault") != "recovery"
            and str(item.get("status_after_occurrence") or "open")
            not in {"resolved", "closed", "cancelled"}
            for item in summary["events"]
        )
        pair_results[pair_key] = {
            "change_count": summary["change_count"],
            "event_count": summary["event_count"],
            "current_state_finding_count": summary["current_finding_count"],
            "unresolved_fault_event_count": unresolved_fault_event_count,
            "current_risk_count": report["risk_summary"]["total"],
            "unstructured_changed_count": report["metrics"][
                "unstructured_changed_count"
            ],
            "assessment": report["assessment"],
            "outputs": {
                "docx": str(docx_path),
                "json": str(json_path),
                "markdown": str(markdown_path),
            },
        }
        replay_evidence[pair_key] = pair_evidence
        previous_summary = summary
        previous_report = report
    public_bundles = [
        {
            key: bundle[key]
            for key in (
                "key",
                "command",
                "privacy",
                "contract",
                "contracts",
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
                "report_assessments": {
                    key: value["assessment"] for key, value in pair_results.items()
                },
            },
            ensure_ascii=False,
            indent=2,
            default=str,
            allow_nan=False,
        ),
        encoding="utf-8",
    )
    return {
        "success": True,
        "generation_provider": "llm",
        "bundle_count": len(bundles),
        "policy_count": sum(len(bundle["policies"]) for bundle in bundles),
        "rule_count": sum(len(bundle["rules"]) for bundle in bundles),
        "period_pair_count": len(pair_results),
        "period_pairs": pair_results,
        "evidence": str(evidence_path),
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
