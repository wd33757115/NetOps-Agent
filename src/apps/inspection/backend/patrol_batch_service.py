# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Patrol batch summary policy independent from HTTP transport."""

from __future__ import annotations

import ipaddress
import json
import math
from collections import Counter
from collections.abc import Iterator
from datetime import datetime
from typing import Any

from src.core.check_items.assessment_policy import (
    build_assessment_basis,
    resolve_assessment_basis,
)
from src.core.check_items.numeric_health import policy_rule
from src.core.patrol.event_rules import (
    EventRuleRepository,
    current_state_condition_matches,
    govern_evaluation_scope,
    norm,
    norm_field,
)
from src.core.patrol.leadership_report import build_leadership_report
from src.core.patrol.unstructured_diff import (
    resolve_unstructured_diff_profile,
    summarize_unstructured_changes,
)
from src.core.state.observation_applicability import APPLICABILITY_CONTEXT_KEY
from src.infrastructure.db.models import (
    CommandSnapshot,
    NetworkChangeRecord,
    NetworkEventOccurrenceRecord,
    NetworkEventRecord,
    PatrolAnalysisAttemptRecord,
    PatrolBatchDeviceRecord,
    PatrolBatchRecord,
    PatrolCollectionResult,
    PatrolRun,
    StateObservationRecord,
    WorkflowStepRecord,
)
from src.infrastructure.db.postgres import get_db_session

REPORT_DOMAINS = (
    ("interface", "接口状态"),
    ("optical", "光模块与光功率"),
    ("cpu", "CPU"),
    ("memory", "内存"),
    ("power", "电源"),
    ("fan", "风扇"),
    ("environment", "环境"),
    ("device", "设备与版本"),
    ("raw", "非结构化输出"),
)


def _format_value(value: Any) -> str:
    return "" if value is None else str(value)


def display_entity_key(value: Any) -> str:
    if not isinstance(value, str):
        return _format_value(value) or "__singleton__"
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return value or "__singleton__"
    if isinstance(decoded, list):
        return "/".join(str(item) for item in decoded) or "__singleton__"
    return _format_value(decoded) or "__singleton__"


def report_domain(entity_type: Any = None, command: Any = None) -> str | None:
    entity = str(entity_type or "").lower()
    text = str(command or "").lower()
    if (
        "optical" in entity
        or "transceiver" in entity
        or "transceiver" in text
        or any(token in text for token in ("rx_power", "tx_power", "optical_power"))
    ):
        return "optical"
    if "interface" in entity or "interface brief" in text:
        return "interface"
    if entity == "cpu" or " cpu" in f" {text}":
        return "cpu"
    if entity == "memory" or "memory" in text:
        return "memory"
    if entity == "power" or "power" in text:
        return "power"
    if entity == "fan" or " fan" in f" {text}":
        return "fan"
    if entity in {"environment", "temperature"} or "environment" in text:
        return "environment"
    if entity == "raw_command_output":
        return "raw"
    if text and resolve_unstructured_diff_profile(text) is not None:
        return "raw"
    if entity == "device" or any(token in text for token in ("version", "device manuinfo")):
        return "device"
    return None


def _missing_reference_text(coverage: dict[str, Any]) -> str:
    """The missing reference fields, with those the device is known not to print marked as such."""

    absent = set(coverage.get("device_absent_reference_fields") or [])
    return ", ".join(
        f"{item}（设备不输出）" if str(item) in absent else str(item)
        for item in coverage.get("missing_threshold_reference_fields") or []
    )


class PatrolBatchService:
    """Apply deterministic quality and domain policies to batch facts."""

    @staticmethod
    def quality_from_run_data(
        command_details: list[dict[str, Any]],
        observations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        parse_counts = Counter(
            str(item.get("parse_status") or "unknown") for item in command_details
        )
        output_counts = Counter(
            str(item.get("output_status") or "not_classified") for item in command_details
        )
        execution_success_count = sum(
            item.get("execution_status") == "complete" for item in command_details
        )
        eligible = [
            item
            for item in command_details
            if item.get("execution_status") == "complete"
            and item.get("parse_status") not in {"ignored", "not_applicable", "raw_hashed"}
        ]
        ready = [item for item in eligible if item.get("parse_status") in {"parsed", "parse_empty"}]
        parser_rate = len(ready) / len(eligible) if eligible else 1.0
        degraded = (
            parser_rate < 1.0
            or bool(parse_counts.get("template_missing"))
            or bool(parse_counts.get("parse_failed"))
            or bool(output_counts.get("command_error"))
            or bool(output_counts.get("empty_unverified"))
            or bool(output_counts.get("truncated"))
            or bool(output_counts.get("paged"))
            or execution_success_count < len(command_details)
        )
        return {
            "status": "degraded" if degraded else "ready",
            "command_count": len(command_details),
            "execution_success_count": execution_success_count,
            "parse_status_counts": dict(parse_counts),
            "output_status_counts": dict(output_counts),
            "parser_eligible_count": len(eligible),
            "parser_ready_count": len(ready),
            "parser_ready_rate": parser_rate,
            "observation_count": len(observations),
        }

    @staticmethod
    def domain_checks(
        *,
        observations: list[dict[str, Any]],
        command_details: list[dict[str, Any]],
        changes: list[dict[str, Any]],
        events: list[dict[str, Any]],
        current_findings: list[dict[str, Any]] | None = None,
        rule_coverage: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        checks: list[dict[str, Any]] = []
        latest_observations: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        for item in observations:
            dimensions = dict(item.get("dimensions") or {})
            dimensions.pop(APPLICABILITY_CONTEXT_KEY, None)
            key = (
                str(item.get("entity_type") or ""),
                str(item.get("entity_key") or ""),
                str(item.get("field") or ""),
                json.dumps(
                    dimensions,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ),
            )
            latest_observations[key] = item
        observations = list(latest_observations.values())
        # A device may have thousands of fields from the same command. Resolve
        # its domain once per distinct pair, not once per field per domain.
        domain_cache: dict[tuple[str, str], str | None] = {}

        def domain_for(entity_type=None, command=None):
            key = (str(entity_type or ""), str(command or ""))
            if key not in domain_cache:
                domain_cache[key] = report_domain(*key)
            return domain_cache[key]

        def group_by_domain(items, *, command_field="command", command_only=False):
            grouped = {}
            for item in items:
                domain = domain_for(None if command_only else item.get("entity_type"), item.get(command_field))
                grouped.setdefault(domain, []).append(item)
            return grouped

        observations_by_domain = group_by_domain(observations)
        commands_by_domain = group_by_domain(command_details, command_only=True)
        changes_by_domain = group_by_domain(changes)
        events_by_domain = group_by_domain(events, command_field="event_type")
        findings_by_domain = {}
        for item in current_findings or []:
            findings_by_domain.setdefault(str(item.get("domain") or ""), []).append(item)
        coverage_by_domain = dict((rule_coverage or {}).get("by_domain") or {})

        for domain, label in REPORT_DOMAINS:
            domain_observations = observations_by_domain.get(domain, [])
            domain_commands = commands_by_domain.get(domain, [])
            domain_changes = changes_by_domain.get(domain, [])
            domain_events = events_by_domain.get(domain, [])
            unresolved_fault_events = [
                item
                for item in domain_events
                if str(item.get("event_class") or "fault") == "fault"
                and str(item.get("status_after_occurrence") or "open").casefold()
                not in {"resolved", "closed", "cancelled"}
            ]
            recovery_events = [
                item
                for item in domain_events
                if str(item.get("event_class") or "fault") == "recovery"
            ]
            domain_findings = findings_by_domain.get(domain, [])
            if not any((domain_commands, domain_observations, domain_changes, domain_events)):
                continue
            parse_statuses = {str(item.get("parse_status") or "") for item in domain_commands}
            domain_rule_coverage = dict(coverage_by_domain.get(domain) or {})
            evaluated_current = int(domain_rule_coverage.get("evaluated_observation_count") or 0)
            threshold_unknown_current = int(
                domain_rule_coverage.get("threshold_unknown_observation_count") or 0
            )
            partial_threshold_current = int(
                domain_rule_coverage.get("partial_threshold_observation_count") or 0
            )
            current_risk_keys = {
                (
                    str(item.get("event_type") or ""),
                    str(item.get("entity_type") or ""),
                    str(item.get("entity_key") or ""),
                    str(item.get("field") or ""),
                )
                for item in [*domain_findings, *unresolved_fault_events]
            }
            if current_risk_keys:
                status = "异常"
                reason_parts = []
                if domain_findings:
                    reason_parts.append(f"{len(domain_findings)} 个当前状态命中活动故障规则")
                if unresolved_fault_events:
                    reason_parts.append(f"{len(unresolved_fault_events)} 个尚未恢复的故障事件")
                if recovery_events:
                    reason_parts.append(f"{len(recovery_events)} 个恢复事件")
                reason = "；".join(reason_parts)
            elif domain == "raw":
                if domain_changes:
                    status = "有变化，未触发事件"
                    reason = (
                        f"检测到 {len(domain_changes)} 项非结构化输出发生有效变化；"
                        "已生成脱敏摘要，需结合变更记录或日志上下文复核"
                    )
                elif domain_observations:
                    status = "未发现变化（健康未判定）"
                    reason = "非结构化输出与对比基线一致；一致不等同于设备健康"
                else:
                    status = "无法判断"
                    reason = "未形成可用于非结构化对比的输出指纹"
            elif threshold_unknown_current:
                status = "数据不完整"
                missing_fields = _missing_reference_text(domain_rule_coverage)
                reason = (
                    f"{threshold_unknown_current} 条当前观测缺少适用的设备阈值，"
                    "不能据此判定健康" + (f"；缺失字段：{missing_fields}" if missing_fields else "")
                )
            elif partial_threshold_current:
                status = "需复核"
                missing_fields = _missing_reference_text(domain_rule_coverage)
                reason = (
                    f"{partial_threshold_current} 条当前观测已按可用设备阈值完成部分判定，"
                    "但预警/告警层级不完整"
                    + (f"；缺失字段：{missing_fields}" if missing_fields else "")
                )
            elif recovery_events:
                status = "本次覆盖范围内未发现当前异常"
                reason = f"产生 {len(recovery_events)} 个恢复事件；当前状态未命中活动故障规则"
            elif not domain_observations:
                if parse_statuses and parse_statuses <= {"not_applicable"}:
                    status = "不适用"
                    reason = "设备输出表明该检查项不适用"
                elif parse_statuses & {"template_missing", "parse_failed", "empty_unverified"}:
                    status = "数据不完整"
                    reason = "缺少可用 Parser 或结构化结果"
                else:
                    status = "无法判断"
                    reason = "没有形成结构化观测"
            elif current_findings is not None and rule_coverage is not None:
                if not bool(rule_coverage.get("available", True)):
                    status = "无法判断"
                    reason = str(rule_coverage.get("error") or "活动规则上下文不可用")
                elif not evaluated_current:
                    status = "规则缺失"
                    reason = "已形成结构化观测，但没有适用于当前状态的活动故障规则"
                elif domain_changes:
                    status = "本次覆盖范围内未发现当前异常"
                    reason = (
                        f"活动规则已评估 {evaluated_current} 条当前状态；"
                        f"另检测到 {len(domain_changes)} 条变化，均未形成当前故障结论"
                    )
                else:
                    status = "本次覆盖范围内未发现异常"
                    reason = f"活动规则已评估 {evaluated_current} 条当前状态，均未命中故障条件"
            elif domain_changes:
                reason_counts = Counter(
                    str(item.get("no_event_reason") or "unknown") for item in domain_changes
                )
                missing_rules = int(reason_counts.get("no_active_event_rules") or 0)
                evaluated = int(reason_counts.get("active_rules_evaluated_no_match") or 0)
                if missing_rules:
                    status = "规则缺失"
                    reason = (
                        f"{len(domain_changes)} 条变化，其中 {missing_rules} 条因无活动规则"
                        "无法完成事件判定"
                    )
                elif evaluated == len(domain_changes):
                    status = "有变化，未触发事件"
                    reason = f"{evaluated} 条变化已由活动规则评估，均未达到触发条件"
                else:
                    status = "需复核"
                    reason = f"检测到 {len(domain_changes)} 条变化，事件判定原因不完整"
            else:
                status = "未发现变化（健康未判定）"
                reason = "已形成结构化观测；无变化或事件不代表当前值处于健康范围"
            current_values = []
            for item in domain_observations[:8]:
                unit = str(item.get("unit") or "")
                value = _format_value(item.get("value"))
                current_values.append(
                    f"{display_entity_key(item.get('entity_key'))} "
                    f"{item.get('field')}={value}{unit}"
                )
            checks.append(
                {
                    "domain": domain,
                    "label": label,
                    "status": status,
                    "observation_count": len(domain_observations),
                    "change_count": len(domain_changes),
                    "event_count": len(domain_events),
                    "current_finding_count": len(domain_findings),
                    "unresolved_fault_event_count": len(unresolved_fault_events),
                    "current_risk_count": len(current_risk_keys),
                    "evaluated_observation_count": evaluated_current,
                    "threshold_unknown_observation_count": threshold_unknown_current,
                    "partial_threshold_observation_count": partial_threshold_current,
                    "reason": reason,
                    "current_values": current_values,
                }
            )
        return checks

    @staticmethod
    def suspected_format_change(field: Any, old: Any, new: Any) -> bool:
        if str(field or "").lower() not in {"ip", "ip_address", "address"}:
            return False
        old_text = str(old or "").strip()
        new_text = str(new or "").strip()
        if not old_text or not new_text or ("/" in old_text) == ("/" in new_text):
            return False
        try:
            return ipaddress.ip_interface(old_text).ip == ipaddress.ip_interface(new_text).ip
        except ValueError:
            return False


quality_from_run_data = PatrolBatchService.quality_from_run_data
domain_checks = PatrolBatchService.domain_checks
suspected_format_change = PatrolBatchService.suspected_format_change


def _condition_applies_to_observation(
    condition: dict[str, Any], observation: dict[str, Any]
) -> bool:
    kind = str(condition.get("kind") or "").strip()
    if kind in {"", "raw_config_hash", "threshold_recovery", "threshold_low_recovery"}:
        return False
    fields = {norm_field(item) for item in condition.get("fields") or []}
    if fields and norm_field(observation.get("field")) not in fields:
        return False
    observation_types = {
        str(item).strip() for item in condition.get("observation_types") or [] if str(item).strip()
    }
    if (
        observation_types
        and str(observation.get("observation_type") or "").strip() not in observation_types
    ):
        return False
    entity_types = {norm(item) for item in condition.get("entity_types") or []}
    if entity_types and norm(observation.get("entity_type")) not in entity_types:
        return False
    if kind == "bgp_transition":
        return "bgp" in norm(observation.get("entity_type")) or "bgp" in norm(
            observation.get("command")
        )
    return kind in {
        "threshold",
        "threshold_lte",
        "threshold_field_gte",
        "threshold_field_lte",
        "linear_power_baseline_loss",
        "numeric_health",
        "transition",
        "bgp_transition",
        "entity_status_down",
    }


def evaluate_current_state(
    observations: list[dict[str, Any]],
    rules: list[dict[str, Any]],
    *,
    rule_source: str,
    rule_bundle_digest: str | None = None,
    runtime_bundle_version_id: str | None = None,
    assessment_policy_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate latest state against fault rules without inventing a historical change."""

    latest: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for item in observations:
        dimensions = dict(item.get("dimensions") or {})
        dimensions.pop(APPLICABILITY_CONTEXT_KEY, None)
        key = (
            str(item.get("device_id") or ""),
            str(item.get("entity_type") or ""),
            str(item.get("entity_key") or ""),
            str(item.get("field") or ""),
            json.dumps(
                dimensions,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ),
        )
        current = latest.get(key)
        if current is None or str(item.get("observed_at") or "") >= str(
            current.get("observed_at") or ""
        ):
            latest[key] = item

    findings: list[dict[str, Any]] = []
    evaluated_keys: set[tuple[str, str, str, str, str]] = set()
    threshold_unknown_keys: set[tuple[str, str, str, str, str]] = set()
    partial_threshold_keys: set[tuple[str, str, str, str, str]] = set()
    policy_missing_keys: set[tuple[str, str, str, str, str]] = set()
    policy_conflict_keys: set[tuple[str, str, str, str, str]] = set()
    expected_state_unknown_keys: set[tuple[str, str, str, str, str]] = set()
    policy_blocker_codes: set[str] = set()
    # Per domain and field: the check item and adapter of each observation that lacked a reference field.
    missing_reference_sources: dict[str, dict[str, set[tuple[str, str]]]] = {}
    assessment_basis_entries: list[dict[str, Any]] = []
    by_domain: dict[str, dict[str, Any]] = {}
    fault_rules = [
        row
        for row in rules
        if str(row.get("event_class") or "fault") == "fault"
        and govern_evaluation_scope(
            row.get("evaluation_scope"),
            event_class=str(row.get("event_class") or "fault"),
        )
        in {"current_state", "both"}
    ]
    for observation_key, observation in latest.items():
        applicable = []
        health_rule = policy_rule(observation)
        for row in [health_rule] if health_rule else fault_rules:
            condition = dict(row.get("effective_condition") or row.get("condition") or {})
            if _condition_applies_to_observation(condition, observation):
                applicable.append((row, condition))
        if not applicable:
            continue
        domain = report_domain(observation.get("entity_type"), observation.get("command"))
        domain_key = domain or "other"
        domain_row = by_domain.setdefault(
            domain_key,
            {
                "evaluated_observation_count": 0,
                "finding_count": 0,
                "threshold_unknown_observation_count": 0,
                "partial_threshold_observation_count": 0,
                "assessment_policy_missing_observation_count": 0,
                "assessment_policy_conflict_observation_count": 0,
                "expected_state_unknown_observation_count": 0,
                "missing_threshold_reference_fields": [],
            },
        )
        evaluable: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
        missing_reference_fields: set[str] = set()
        threshold_binding_resolved = False
        threshold_binding_blocked = False
        observation_resolved = False
        observation_policy_missing = False
        observation_policy_conflict = False
        observation_expected_state_missing = False
        for row, condition in applicable:
            resolution = resolve_assessment_basis(
                rule=row,
                observation=observation,
                policy_snapshot=assessment_policy_snapshot,
                runtime_bundle_version_id=runtime_bundle_version_id,
                runtime_bundle_digest=rule_bundle_digest,
            )
            basis = dict(resolution.basis)
            assessment_basis_entries.append(basis)
            threshold_binding = dict(basis.get("threshold_binding") or {})
            baseline_binding = dict(basis.get("baseline_binding") or {})
            if threshold_binding.get("status") == "resolved":
                threshold_binding_resolved = True
            elif threshold_binding.get("status") in {"missing", "conflict"}:
                threshold_binding_blocked = True
                missing_reference_fields.update(
                    str(field)
                    for field in [
                        condition.get("reference_field"),
                        *(condition.get("fallback_reference_fields") or []),
                    ]
                    if str(field or "").strip()
                )
            if baseline_binding.get("status") == "resolved":
                threshold_binding_resolved = True
            elif baseline_binding.get("status") in {"missing", "conflict"}:
                threshold_binding_blocked = True
                missing_reference_fields.update(
                    str(field)
                    for field in condition.get("identity_fields") or []
                    if str(field or "").strip()
                )
            if resolution.status != "resolved":
                blocker = str(resolution.blocker_code or "assessment_policy_missing")
                policy_blocker_codes.add(blocker)
                if blocker == "expected_state_missing":
                    observation_expected_state_missing = True
                elif blocker in {
                    "assessment_policy_conflict",
                    "assessment_policy_source_invalid",
                    "assessment_policy_snapshot_invalid",
                    "assessment_policy_unit_mismatch",
                }:
                    observation_policy_conflict = True
                else:
                    observation_policy_missing = True
                continue
            observation_resolved = True
            if resolution.should_evaluate:
                evaluable.append((row, dict(resolution.effective_condition), basis))
        if observation_policy_missing:
            policy_missing_keys.add(observation_key)
            domain_row["assessment_policy_missing_observation_count"] += 1
        if observation_policy_conflict:
            policy_conflict_keys.add(observation_key)
            domain_row["assessment_policy_conflict_observation_count"] += 1
        if observation_expected_state_missing:
            expected_state_unknown_keys.add(observation_key)
            domain_row["expected_state_unknown_observation_count"] += 1
        if threshold_binding_blocked:
            if threshold_binding_resolved or observation_resolved:
                partial_threshold_keys.add(observation_key)
                domain_row["partial_threshold_observation_count"] += 1
            else:
                threshold_unknown_keys.add(observation_key)
                domain_row["threshold_unknown_observation_count"] += 1
        if missing_reference_fields:
            domain_row["missing_threshold_reference_fields"] = sorted(
                {
                    *domain_row["missing_threshold_reference_fields"],
                    *missing_reference_fields,
                }
            )
            metadata = dict(observation.get("metadata") or {})
            source = (
                str(observation.get("check_item_id") or metadata.get("check_item_id") or ""),
                str(metadata.get("command_adapter_id") or ""),
            )
            for field in missing_reference_fields:
                missing_reference_sources.setdefault(domain_key, {}).setdefault(field, set()).add(source)
        if observation_resolved:
            evaluated_keys.add(observation_key)
            domain_row["evaluated_observation_count"] += 1
        if not evaluable:
            continue
        for row, condition, basis in sorted(
            evaluable,
            key=lambda item: -int(item[0].get("priority") or 100),
        ):
            try:
                scope = govern_evaluation_scope(
                    row.get("evaluation_scope"),
                    event_class=str(row.get("event_class") or "fault"),
                )
                matched = current_state_condition_matches(
                    condition,
                    observation,
                    explicit_current_scope=scope in {"current_state", "both"},
                )
            except (TypeError, ValueError, KeyError):
                matched = False
            if not matched:
                continue
            threshold_binding = dict(basis.get("threshold_binding") or {})
            expected_state_binding = dict(basis.get("expected_state_binding") or {})
            reference_binding = (
                threshold_binding
                if threshold_binding.get("status") == "resolved"
                else dict(basis.get("baseline_binding") or {})
                if dict(basis.get("baseline_binding") or {}).get("status") == "resolved"
                else expected_state_binding
                if expected_state_binding.get("status") == "resolved"
                else {}
            )
            reference_field = reference_binding.get("reference_field")
            reference_value = (
                reference_binding.get("value")
                if "value" in reference_binding
                else reference_binding.get("expected_states")
            )
            finding = {
                "finding_id": (
                    f"{row.get('rule_id') or row.get('rule_name')}|"
                    f"{observation.get('device_id')}|{observation.get('entity_type')}|"
                    f"{observation.get('entity_key')}|{observation.get('field')}|"
                    f"{observation_key[4]}"
                ),
                "device_id": observation.get("device_id"),
                "device_name": observation.get("device_name"),
                "run_id": observation.get("run_id"),
                "domain": domain_key,
                "entity_type": observation.get("entity_type"),
                "entity_key": observation.get("entity_key"),
                "field": observation.get("field"),
                "value": observation.get("value"),
                "unit": observation.get("unit"),
                "event_type": row.get("event_type"),
                "severity": row.get("severity") or "warning",
                "rule_id": row.get("rule_id") or row.get("rule_name"),
                "rule_version_id": row.get("version_id"),
                "rule_version": row.get("version"),
                "rule_digest": row.get("digest"),
                "evaluation_scope": scope,
                "condition": condition,
                "dimensions": dict(observation.get("dimensions") or {}),
                "health_assessment": basis.get("health_assessment"),
                "assessment_source": row.get("source") or rule_source,
                "reference_field": reference_field,
                "reference_value": reference_value,
                "reference_source": reference_binding.get("source_kind"),
                "command": observation.get("command"),
                "observation_id": observation.get("id"),
                "snapshot_id": observation.get("snapshot_id"),
                "evidence_id": observation.get("evidence_id"),
                "observation_type": observation.get("observation_type"),
                "observed_at": observation.get("observed_at"),
                "parser_template_id": observation.get("parser_template_id"),
                "parser_version": observation.get("parser_version"),
                "observation_contract_id": observation.get("observation_contract_id"),
                "contract_version": observation.get("contract_version"),
                "observation_contract_digest": observation.get("observation_contract_digest"),
                "rule_source": rule_source,
                "rule_bundle_digest": rule_bundle_digest,
                "assessment_basis": basis,
            }
            findings.append(finding)
            domain_row["finding_count"] += 1
            if bool(row.get("exclusive")):
                break
    severity_counts = Counter(str(item.get("severity") or "unknown") for item in findings)
    assessment_basis = build_assessment_basis(
        entries=assessment_basis_entries,
        policy_snapshot_digest=(
            str(dict(assessment_policy_snapshot or {}).get("digest") or "") or None
        ),
        runtime_bundle_version_id=runtime_bundle_version_id,
        runtime_bundle_digest=rule_bundle_digest,
    )
    return {
        "available": True,
        "rule_source": rule_source,
        "rule_bundle_digest": rule_bundle_digest,
        "active_fault_rule_count": len(fault_rules),
        "latest_observation_count": len(latest),
        "evaluated_observation_count": len(evaluated_keys),
        "evaluated_entity_count": len(
            {
                (device_id, entity_type, entity_key)
                for device_id, entity_type, entity_key, _field, _dimensions in evaluated_keys
            }
        ),
        "threshold_unknown_observation_count": len(threshold_unknown_keys),
        "partial_threshold_observation_count": len(partial_threshold_keys),
        "assessment_policy_missing_observation_count": len(policy_missing_keys),
        "assessment_policy_conflict_observation_count": len(policy_conflict_keys),
        "expected_state_unknown_observation_count": len(expected_state_unknown_keys),
        "assessment_policy_blocking_reason_codes": sorted(policy_blocker_codes),
        "assessment_basis": assessment_basis,
        "finding_count": len(findings),
        "severity_counts": dict(severity_counts),
        "by_domain": by_domain,
        "findings": findings,
        "missing_threshold_reference_sources": {
            domain_key: {field: sorted(sources) for field, sources in fields.items()}
            for domain_key, fields in missing_reference_sources.items()
        },
    }


def mark_device_absent_reference_fields(db: Any, assessment: dict[str, Any]) -> None:
    """Note, per domain, the missing reference fields every adapter involved is known not to print.

    Adding fields to an adapter records the fields its outputs never contain. Where
    a threshold is missing because the device does not print it, the report says
    so; the judgement it reached does not change.
    """

    from src.core.check_items.field_absence import device_absent

    sources = dict(assessment.get("missing_threshold_reference_sources") or {})
    for domain_key, fields in sources.items():
        row = dict(assessment.get("by_domain") or {}).get(domain_key)
        if row is None:
            continue
        try:
            row["device_absent_reference_fields"] = device_absent(
                db, {field: [tuple(pair) for pair in pairs] for field, pairs in fields.items()}
            )
        except Exception:  # noqa: BLE001 - a note in the report must never stop the report
            row["device_absent_reference_fields"] = []


def _iso(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _find_nested_value(payload: Any, key: str) -> Any:
    if isinstance(payload, dict):
        if key in payload:
            return payload[key]
        for value in payload.values():
            found = _find_nested_value(value, key)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _find_nested_value(item, key)
            if found is not None:
                return found
    return None


def _batch_scope(
    db: Any,
    patrol_batch_id: str,
    runs: list[Any],
    status_counts: Counter[str],
) -> dict[str, Any]:
    active_statuses = {
        "pending",
        "queued",
        "running",
        "in_progress",
        "collecting",
        "dispatching",
    }
    completed_count = int(status_counts.get("completed") or 0)
    failed_count = int(status_counts.get("failed") or 0)
    cancelled_count = int(status_counts.get("cancelled") or 0)
    active_count = sum(int(status_counts.get(item) or 0) for item in active_statuses)
    fallback = {
        "manifest_available": False,
        "target_type": "legacy",
        "target_label": "历史巡检批次",
        "selector": {},
        "target_count": len(runs),
        "launched_count": len(runs),
        "skipped_count": 0,
        "preflight_failed_count": 0,
        "completed_count": completed_count,
        "failed_count": failed_count,
        "cancelled_count": cancelled_count,
        "active_count": active_count,
        "execution_coverage_rate": completed_count / len(runs) if runs else 0.0,
    }
    try:
        manifest = db.get(PatrolBatchRecord, patrol_batch_id)
    except (AttributeError, TypeError):
        return fallback
    if manifest is None:
        return fallback
    try:
        targets = (
            db.query(PatrolBatchDeviceRecord)
            .filter(PatrolBatchDeviceRecord.patrol_batch_id == patrol_batch_id)
            .all()
        )
    except (AttributeError, TypeError):
        targets = []
    target_count = int(manifest.target_count or len(targets) or len(runs))
    launched_count = int(manifest.launched_count or len(runs))
    skipped_count = int(manifest.skipped_count or 0)
    preflight_failed = sum(
        str(getattr(item, "preflight_status", "") or "") == "failed" for item in targets
    )
    return {
        "manifest_available": True,
        "target_type": manifest.target_type,
        "target_label": manifest.target_label,
        "selector": dict(manifest.selector_json or {}),
        "target_count": target_count,
        "launched_count": launched_count,
        "skipped_count": skipped_count,
        "preflight_failed_count": preflight_failed,
        "completed_count": completed_count,
        "failed_count": failed_count,
        "cancelled_count": cancelled_count,
        "active_count": active_count,
        "execution_coverage_rate": completed_count / target_count if target_count else 0.0,
        "created_at": _iso(manifest.created_at),
    }


def _frozen_event_rule_bundles(db: Any, snapshot_run_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not snapshot_run_ids:
        return {}
    try:
        rows = (
            db.query(PatrolAnalysisAttemptRecord)
            .filter(
                PatrolAnalysisAttemptRecord.run_id.in_(snapshot_run_ids),
                PatrolAnalysisAttemptRecord.stage == "event_building",
                PatrolAnalysisAttemptRecord.status == "completed",
            )
            .order_by(PatrolAnalysisAttemptRecord.completed_at.desc())
            .all()
        )
    except (AttributeError, TypeError):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        run_id = str(row.run_id)
        if run_id in result:
            continue
        bundle = dict(row.rule_bundle_json or {})
        result[run_id] = {
            "rules": list(bundle.get("event_rules") or []),
            "digest": row.rule_bundle_digest,
            "source": "frozen_analysis_attempt",
        }
    return result


def _runtime_event_rule_bundle(db: Any) -> dict[str, Any]:
    # Fake sessions used by unit tests deliberately have no engine binding.
    if not hasattr(db, "bind"):
        return {"rules": [], "digest": None, "source": "unavailable", "error": None}
    try:
        rules = EventRuleRepository().snapshot_active_rules()
    except Exception as exc:
        return {
            "rules": [],
            "digest": None,
            "source": "unavailable",
            "error": f"活动 EventRule 加载失败：{exc}",
        }
    digests = sorted(
        str(item.get("digest") or item.get("version_id") or item.get("rule_id") or "")
        for item in rules
    )
    import hashlib

    digest = hashlib.sha256("|".join(digests).encode("utf-8")).hexdigest() if rules else None
    return {
        "rules": rules,
        "digest": digest,
        "source": "active_runtime",
        "error": None if rules else "没有活动 EventRule",
    }


def list_patrol_snapshot_run_ids(workflow_run_ids: list[str]) -> list[str]:
    """Resolve collector run IDs without exposing persistence to HTTP handlers."""

    if not workflow_run_ids:
        return []
    snapshot_ids: list[str] = []
    seen: set[str] = set()
    with get_db_session() as db:
        steps = (
            db.query(WorkflowStepRecord)
            .filter(WorkflowStepRecord.run_id.in_(workflow_run_ids))
            .order_by(
                WorkflowStepRecord.run_id.asc(),
                WorkflowStepRecord.step_index.asc(),
            )
            .all()
        )
        for step in steps:
            run_id = str(step.run_id)
            if run_id in seen:
                continue
            snapshot_run_id = _find_nested_value(step.result or {}, "snapshot_run_id")
            if snapshot_run_id:
                snapshot_ids.append(str(snapshot_run_id))
                seen.add(run_id)
    return snapshot_ids


def iter_patrol_raw_snapshot_rows(
    snapshot_run_ids: list[str],
) -> Iterator[dict[str, Any]]:
    """Stream transport-neutral raw snapshot dictionaries for an export."""

    if not snapshot_run_ids:
        return
    with get_db_session() as db:
        query = (
            db.query(CommandSnapshot)
            .filter(CommandSnapshot.run_id.in_(snapshot_run_ids))
            .order_by(
                CommandSnapshot.device_name.asc(),
                CommandSnapshot.observed_at.asc(),
                CommandSnapshot.canonical_command.asc(),
            )
            .yield_per(100)
        )
        for row in query:
            yield {
                "snapshot_run_id": row.run_id,
                "snapshot_id": row.id,
                "device_id": row.device_id,
                "device_name": row.device_name,
                "management_ip": row.management_ip,
                "vendor": row.vendor,
                "model": row.model,
                "command": row.canonical_command,
                "observed_at": _iso(row.observed_at) or None,
                "parse_status": row.parse_status,
                "output_status": row.output_status,
                "raw_text_hash": row.raw_text_hash,
                "raw_text": row.raw_text,
            }


def list_patrol_raw_output_artifacts(
    workflow_run_ids: list[str],
) -> dict[str, dict[str, Any]]:
    """Return each device Workflow Run's immutable raw-output artifact."""

    if not workflow_run_ids:
        return {}
    artifacts: dict[str, dict[str, Any]] = {}
    with get_db_session() as db:
        steps = (
            db.query(WorkflowStepRecord)
            .filter(
                WorkflowStepRecord.run_id.in_(workflow_run_ids),
                WorkflowStepRecord.skill_name == "device-patrol",
            )
            .all()
        )
        for step in steps:
            artifact = dict((step.output_artifacts or {}).get("raw_outputs_archive") or {})
            if artifact:
                artifacts[str(step.run_id)] = artifact
    return artifacts


def list_patrol_run_raw_output_artifacts(
    snapshot_run_ids: list[str],
) -> dict[str, dict[str, Any]]:
    """Return immutable collector archives keyed by PatrolRun identity."""

    normalized_ids = sorted({str(item) for item in snapshot_run_ids if str(item)})
    if not normalized_ids:
        return {}
    artifacts: dict[str, dict[str, Any]] = {}
    with get_db_session() as db:
        rows = (
            db.query(PatrolRun)
            .filter(PatrolRun.id.in_(normalized_ids))
            .order_by(PatrolRun.id.asc())
            .all()
        )
        for row in rows:
            metadata = dict(row.metadata_json or {})
            value = metadata.get("raw_outputs_archive")
            if isinstance(value, dict):
                artifact = dict(value)
            elif value:
                artifact = {"download_url": str(value)}
            else:
                continue
            if artifact.get("download_url"):
                artifacts[str(row.id)] = artifact
    return artifacts


def iter_patrol_event_export_rows(
    snapshot_run_ids: list[str],
    device_names: dict[str, str],
) -> Iterator[dict[str, Any]]:
    """Stream immutable event occurrences, with a legacy event fallback."""

    if not snapshot_run_ids:
        return
    with get_db_session() as db:
        occurrence_rows = (
            db.query(NetworkEventOccurrenceRecord, NetworkEventRecord)
            .join(
                NetworkEventRecord,
                NetworkEventRecord.id == NetworkEventOccurrenceRecord.event_id,
            )
            .filter(NetworkEventOccurrenceRecord.run_id.in_(snapshot_run_ids))
            .order_by(
                NetworkEventOccurrenceRecord.observed_at.asc(),
                NetworkEventOccurrenceRecord.id.asc(),
            )
            .yield_per(100)
        )
        occurrence_event_ids: set[str] = set()
        for occurrence, event in occurrence_rows:
            occurrence_event_ids.add(str(event.id))
            fact = _serialize_event_occurrence(occurrence, event)
            device_id = str(fact["device_id"] or "")
            yield {
                "occurrence_id": fact["occurrence_id"],
                "occurrence_kind": fact["occurrence_kind"],
                "event_id": fact["id"],
                "device_id": device_id,
                "device_name": device_names.get(device_id, device_id),
                "event_type": fact["event_type"],
                "severity": fact["severity"],
                "status": fact["status"],
                "entity_type": fact["entity_type"],
                "entity_key": fact["entity_key"],
                "occurred_at": fact["occurred_at"],
            }

        legacy_query = db.query(NetworkEventRecord).filter(
            NetworkEventRecord.run_id.in_(snapshot_run_ids)
        )
        if occurrence_event_ids:
            legacy_query = legacy_query.filter(
                ~NetworkEventRecord.id.in_(sorted(occurrence_event_ids))
            )
        for event in legacy_query.order_by(NetworkEventRecord.occurred_at.asc()).yield_per(100):
            fact = _serialize_legacy_event(event)
            device_id = str(fact["device_id"] or "")
            yield {
                "occurrence_id": None,
                "occurrence_kind": "legacy",
                "event_id": fact["id"],
                "device_id": device_id,
                "device_name": device_names.get(device_id, device_id),
                "event_type": fact["event_type"],
                "severity": fact["severity"],
                "status": fact["status"],
                "entity_type": fact["entity_type"],
                "entity_key": fact["entity_key"],
                "occurred_at": fact["occurred_at"],
            }


def _occurrence_status(occurrence: Any) -> str:
    payload = dict(getattr(occurrence, "payload_json", None) or {})
    persisted = payload.get("aggregate_status_after_occurrence")
    if persisted:
        return str(persisted)
    kind = str(getattr(occurrence, "occurrence_kind", None) or "observed")
    if kind == "recovery":
        return "resolved"
    if kind in {"opening", "recurrence"}:
        return "open"
    return "observed"


def _serialize_event_occurrence(occurrence: Any, event: Any) -> dict[str, Any]:
    payload = dict(getattr(occurrence, "payload_json", None) or {})
    rule_payload = dict(payload.get("rule") or {})
    event_type = str(payload.get("event_type") or event.event_type)
    entity_type = str(payload.get("entity_type") or event.entity_type)
    return {
        "id": str(event.id),
        "occurrence_id": str(occurrence.id),
        "occurrence_kind": str(occurrence.occurrence_kind or "observed"),
        "source_change_id": occurrence.source_change_id,
        "device_id": payload.get("device_id") or event.device_id,
        "event_type": event_type,
        "severity": str(payload.get("severity") or occurrence.severity),
        "event_class": rule_payload.get("event_class") or payload.get("event_class") or "fault",
        "status": _occurrence_status(occurrence),
        "entity_type": entity_type,
        "entity_key": payload.get("entity_key") or event.entity_key,
        "field": payload.get("field"),
        "current_value": payload.get("new"),
        "unit": payload.get("unit"),
        "rule_id": rule_payload.get("rule_id") or rule_payload.get("name"),
        "rule_version": rule_payload.get("version"),
        "condition": dict(rule_payload.get("condition") or {}),
        "domain": report_domain(entity_type, event_type),
        "occurred_at": _iso(occurrence.observed_at),
    }


def _serialize_legacy_event(event: Any) -> dict[str, Any]:
    payload = dict(getattr(event, "payload_json", None) or {})
    rule_payload = dict(payload.get("rule") or {})
    return {
        "id": str(event.id),
        "occurrence_id": None,
        "occurrence_kind": "legacy",
        "source_change_id": event.source_change_id,
        "device_id": event.device_id,
        "event_type": event.event_type,
        "severity": event.severity,
        "event_class": rule_payload.get("event_class") or payload.get("event_class") or "fault",
        "status": event.status,
        "entity_type": event.entity_type,
        "entity_key": event.entity_key,
        "field": payload.get("field"),
        "current_value": payload.get("new"),
        "unit": payload.get("unit"),
        "rule_id": rule_payload.get("rule_id") or rule_payload.get("name"),
        "rule_version": rule_payload.get("version"),
        "condition": dict(rule_payload.get("condition") or {}),
        "domain": report_domain(event.entity_type, event.event_type),
        "occurred_at": _iso(event.occurred_at),
    }


PATROL_PIPELINE_STAGE_META = {
    "device-patrol": ("命令采集与解析", "登录设备、执行巡检命令并保存原始输出"),
    "state-normalizer": ("状态归一化", "将解析结果转换为统一的设备状态"),
    "change-detector": ("变化检测", "与历史状态比较并识别有效变化"),
    "event-builder": ("事件判定", "使用活动规则判断是否生成巡检事件"),
}


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


def _duration_seconds(started_at: Any, completed_at: Any) -> float | None:
    if not started_at or not completed_at:
        return None
    return max(0.0, (completed_at - started_at).total_seconds())


def build_patrol_pipeline_summary(
    runs: list[Any],
    steps_by_run: dict[str, list[Any]],
) -> dict[str, Any]:
    definitions: dict[tuple[int, str, str], dict[str, Any]] = {}
    for steps in steps_by_run.values():
        for step in steps:
            key = (int(step.step_index or 0), str(step.step_name), str(step.skill_name))
            definitions.setdefault(
                key,
                {"step_index": key[0], "step_name": key[1], "skill_name": key[2]},
            )

    stages: list[dict[str, Any]] = []
    total_runs = len(runs)
    for definition in sorted(definitions.values(), key=lambda item: item["step_index"]):
        statuses: Counter[str] = Counter()
        durations: list[float] = []
        started_values: list[Any] = []
        completed_values: list[Any] = []
        errors: list[dict[str, str]] = []
        for run in runs:
            matching = next(
                (
                    step
                    for step in steps_by_run.get(str(run.id), [])
                    if int(step.step_index or 0) == definition["step_index"]
                    and str(step.step_name) == definition["step_name"]
                    and str(step.skill_name) == definition["skill_name"]
                ),
                None,
            )
            if matching is None:
                statuses["not_started"] += 1
                continue
            status = str(matching.status or "pending")
            statuses[status] += 1
            if matching.started_at:
                started_values.append(matching.started_at)
            if matching.completed_at:
                completed_values.append(matching.completed_at)
            duration = _duration_seconds(matching.started_at, matching.completed_at)
            if duration is not None:
                durations.append(duration)
            if matching.error_message and len(errors) < 5:
                context = dict(run.context or {})
                errors.append(
                    {
                        "device_id": str(context.get("device_id") or ""),
                        "device_name": str(
                            context.get("device_name") or context.get("device_id") or run.id
                        ),
                        "message": str(matching.error_message),
                    }
                )

        completed = int(statuses.get("completed") or 0)
        failed = int(statuses.get("failed") or 0)
        active = sum(
            int(statuses.get(status) or 0)
            for status in ("running", "queued", "in_progress", "collecting", "dispatching")
        )
        if active:
            aggregate_status = "running"
        elif failed:
            aggregate_status = "degraded" if completed else "failed"
        elif total_runs and completed == total_runs:
            aggregate_status = "completed"
        else:
            aggregate_status = "pending"
        label, description = PATROL_PIPELINE_STAGE_META.get(
            definition["skill_name"],
            (definition["step_name"], definition["skill_name"]),
        )
        p95 = _percentile(durations, 0.95)
        stages.append(
            {
                **definition,
                "key": (
                    f"{definition['step_index']}:{definition['step_name']}:"
                    f"{definition['skill_name']}"
                ),
                "label": label,
                "description": description,
                "status": aggregate_status,
                "total": total_runs,
                "status_counts": dict(statuses),
                "completed_count": completed,
                "failed_count": failed,
                "active_count": active,
                "pending_count": max(0, total_runs - completed - failed - active),
                "completion_rate": completed / total_runs if total_runs else 0.0,
                "started_at": _iso(min(started_values)) if started_values else None,
                "completed_at": _iso(max(completed_values)) if completed_values else None,
                "duration": {
                    "sample_count": len(durations),
                    "average_seconds": round(sum(durations) / len(durations), 3)
                    if durations
                    else None,
                    "p95_seconds": round(p95, 3) if p95 is not None else None,
                    "max_seconds": round(max(durations), 3) if durations else None,
                },
                "error_examples": errors,
            }
        )

    completed_stages = [stage for stage in stages if stage["duration"]["p95_seconds"] is not None]
    bottleneck = max(
        completed_stages,
        key=lambda stage: float(stage["duration"]["p95_seconds"] or 0),
        default=None,
    )
    return {
        "total_devices": total_runs,
        "stage_count": len(stages),
        "stages": stages,
        "bottleneck_stage_key": bottleneck["key"] if bottleneck else None,
        "bottleneck_stage_label": bottleneck["label"] if bottleneck else None,
    }


def build_patrol_batch_summary(
    patrol_batch_id: str,
    runs: list[Any],
) -> dict[str, Any]:
    run_rows: list[dict[str, Any]] = []
    event_types: Counter[str] = Counter()
    severities: Counter[str] = Counter()
    total_change_count = 0
    total_event_count = 0
    all_events: list[dict[str, Any]] = []
    all_changes: list[dict[str, Any]] = []
    aggregate_parse_statuses: Counter[str] = Counter()
    aggregate_output_statuses: Counter[str] = Counter()
    aggregate_command_count = 0
    aggregate_execution_success = 0
    aggregate_parser_eligible = 0
    aggregate_parser_ready = 0
    aggregate_observations = 0
    aggregate_snapshot_count = 0
    aggregate_duplicate_snapshots = 0
    aggregate_no_event_reasons: Counter[str] = Counter()
    suspected_format_change_count = 0
    all_current_findings: list[dict[str, Any]] = []
    aggregate_rule_domains: dict[str, dict[str, Any]] = {}
    aggregate_evaluated_observations = 0
    aggregate_threshold_unknown_observations = 0
    aggregate_partial_threshold_observations = 0
    aggregate_latest_observations = 0
    aggregate_fault_rule_count = 0
    rule_sources: Counter[str] = Counter()
    rule_bundle_digests: set[str] = set()
    rule_errors: set[str] = set()

    with get_db_session() as db:
        run_ids = [str(run.id) for run in runs]
        if run_ids:
            step_query = db.query(WorkflowStepRecord)
            run_filter = (
                WorkflowStepRecord.run_id == run_ids[0]
                if len(run_ids) == 1
                else WorkflowStepRecord.run_id.in_(run_ids)
            )
            all_steps = (
                step_query.filter(run_filter)
                .order_by(
                    WorkflowStepRecord.run_id.asc(),
                    WorkflowStepRecord.step_index.asc(),
                )
                .all()
            )
        else:
            all_steps = []
        steps_by_run: dict[str, list[Any]] = {}
        for step in all_steps:
            steps_by_run.setdefault(str(step.run_id), []).append(step)
        snapshot_run_by_workflow_run: dict[str, str] = {}
        for workflow_run_id, steps in steps_by_run.items():
            for step in steps:
                snapshot_run_id = _find_nested_value(step.result or {}, "snapshot_run_id")
                if snapshot_run_id:
                    snapshot_run_by_workflow_run[workflow_run_id] = str(snapshot_run_id)
                    break
        from src.apps.inspection.backend.online_reads import (
            batch_execution_rows,
            pipeline_summary as inspection_pipeline_summary,
        )

        direct_rows = (
            batch_execution_rows(db, patrol_batch_id) if not runs and hasattr(db, "get") else None
        )
        facts = (
            direct_rows
            if direct_rows is not None
            else [
                {
                    "run_id": run.id,
                    "execution_kind": "workflow",
                    "device_id": str((run.context or {}).get("device_id") or ""),
                    "device_name": str(
                        (run.context or {}).get("device_name")
                        or (run.context or {}).get("device_id")
                        or "-"
                    ),
                    "status": run.status or "unknown",
                    "error": run.error_message or "",
                    "snapshot_run_id": snapshot_run_by_workflow_run.get(str(run.id)),
                    "steps": {
                        str(index): step.result or {}
                        for index, step in enumerate(steps_by_run.get(str(run.id), []))
                    },
                }
                for run in runs
            ]
        )
        status_counts = Counter(str(row["status"]) for row in facts)
        device_ids = {
            row["device_id"] or (row["device_name"] if row["device_name"] != "-" else row["run_id"])
            for row in facts
        }
        frozen_rule_bundles = _frozen_event_rule_bundles(
            db,
            [row["snapshot_run_id"] for row in facts if row["snapshot_run_id"]],
        )
        runtime_rule_bundle: dict[str, Any] | None = None
        for fact in facts:
            device_id = fact["device_id"]
            device_name = fact["device_name"]
            snapshot_run_id = fact["snapshot_run_id"]
            changes: list[NetworkChangeRecord] = []
            events: list[dict[str, Any]] = []
            snapshots: list[CommandSnapshot] = []
            collection_results: list[PatrolCollectionResult] = []
            observation_rows: list[StateObservationRecord] = []
            if snapshot_run_id:
                snapshots = (
                    db.query(CommandSnapshot)
                    .filter(CommandSnapshot.run_id == str(snapshot_run_id))
                    .order_by(CommandSnapshot.observed_at.asc())
                    .all()
                )
                collection_results = (
                    db.query(PatrolCollectionResult)
                    .filter(PatrolCollectionResult.run_id == str(snapshot_run_id))
                    .order_by(PatrolCollectionResult.observed_at.asc())
                    .all()
                )
                observation_rows = (
                    db.query(StateObservationRecord)
                    .filter(StateObservationRecord.run_id == str(snapshot_run_id))
                    .order_by(StateObservationRecord.observed_at.asc())
                    .all()
                )
                changes = (
                    db.query(NetworkChangeRecord)
                    .filter(NetworkChangeRecord.run_id == str(snapshot_run_id))
                    .order_by(NetworkChangeRecord.detected_at.desc())
                    .all()
                )
                occurrence_rows = (
                    db.query(NetworkEventOccurrenceRecord, NetworkEventRecord)
                    .join(
                        NetworkEventRecord,
                        NetworkEventRecord.id == NetworkEventOccurrenceRecord.event_id,
                    )
                    .filter(NetworkEventOccurrenceRecord.run_id == str(snapshot_run_id))
                    .order_by(
                        NetworkEventOccurrenceRecord.observed_at.desc(),
                        NetworkEventOccurrenceRecord.id.desc(),
                    )
                    .all()
                )
                occurrence_event_ids = {str(event.id) for _occurrence, event in occurrence_rows}
                events = [
                    _serialize_event_occurrence(occurrence, event)
                    for occurrence, event in occurrence_rows
                ]
                legacy_query = db.query(NetworkEventRecord).filter(
                    NetworkEventRecord.run_id == str(snapshot_run_id)
                )
                if occurrence_event_ids:
                    legacy_query = legacy_query.filter(
                        ~NetworkEventRecord.id.in_(sorted(occurrence_event_ids))
                    )
                events.extend(
                    _serialize_legacy_event(event)
                    for event in legacy_query.order_by(NetworkEventRecord.occurred_at.desc()).all()
                )
            no_event_summary = {}
            for result in fact["steps"].values():
                candidate = _find_nested_value(result, "no_event_summary")
                if isinstance(candidate, dict):
                    no_event_summary = candidate
                    break
            no_event_by_change = {
                str(item.get("change_id")): str(item.get("reason") or "unknown")
                for item in no_event_summary.get("examples") or []
                if isinstance(item, dict) and item.get("change_id")
            }
            command_details = [
                {
                    "command": row.canonical_command,
                    "execution_status": row.execution_status,
                    "parse_status": row.parse_status,
                    "output_status": row.output_status,
                    "fact_status": row.fact_status,
                    "failure_code": row.failure_code or "",
                }
                for row in collection_results
            ]
            if not command_details:
                command_details = [
                    {
                        "command": row.canonical_command,
                        "execution_status": "complete",
                        "parse_status": row.parse_status,
                        "output_status": row.output_status,
                        "fact_status": "unknown",
                        "failure_code": "",
                    }
                    for row in snapshots
                ]
            observations = [
                {
                    "id": row.id,
                    "device_id": row.device_id,
                    "device_name": device_name,
                    "command": row.canonical_command,
                    "entity_type": row.entity_type,
                    "entity_key": row.entity_key,
                    "field": row.field_name,
                    "observation_type": row.observation_type,
                    "semantic_type": row.semantic_type,
                    "value": row.value_json,
                    "unit": row.unit,
                    "observed_at": _iso(row.observed_at),
                    "source_type": row.source_type,
                    "schema_id": row.schema_id,
                    "parser_template_id": row.parser_template_id,
                    "parser_version": row.parser_version,
                    "contract_version": row.contract_version,
                    "context": ((row.metadata_json or {}).get(APPLICABILITY_CONTEXT_KEY)),
                    "metadata": dict(row.metadata_json or {}),
                    "dimensions": dict((row.metadata_json or {}).get("dimensions") or {}),
                }
                for row in observation_rows
            ]
            effective_rule_bundle = frozen_rule_bundles.get(str(snapshot_run_id or ""))
            if effective_rule_bundle is None:
                if runtime_rule_bundle is None:
                    runtime_rule_bundle = _runtime_event_rule_bundle(db)
                effective_rule_bundle = runtime_rule_bundle
            rules = list(effective_rule_bundle.get("rules") or [])
            if rules or any(map(policy_rule, observations)):
                current_assessment = evaluate_current_state(
                    observations,
                    rules,
                    rule_source=str(effective_rule_bundle.get("source") or "unknown"),
                    rule_bundle_digest=effective_rule_bundle.get("digest"),
                )
                mark_device_absent_reference_fields(db, current_assessment)
            else:
                current_assessment = {
                    "available": False,
                    "rule_source": str(effective_rule_bundle.get("source") or "unavailable"),
                    "rule_bundle_digest": effective_rule_bundle.get("digest"),
                    "active_fault_rule_count": 0,
                    "latest_observation_count": len(observations),
                    "evaluated_observation_count": 0,
                    "threshold_unknown_observation_count": 0,
                    "partial_threshold_observation_count": 0,
                    "finding_count": 0,
                    "severity_counts": {},
                    "by_domain": {},
                    "findings": [],
                    "error": effective_rule_bundle.get("error")
                    or "没有可用于当前状态判定的活动 EventRule",
                }
            all_current_findings.extend(current_assessment["findings"])
            aggregate_evaluated_observations += int(
                current_assessment.get("evaluated_observation_count") or 0
            )
            aggregate_threshold_unknown_observations += int(
                current_assessment.get("threshold_unknown_observation_count") or 0
            )
            aggregate_partial_threshold_observations += int(
                current_assessment.get("partial_threshold_observation_count") or 0
            )
            aggregate_latest_observations += int(
                current_assessment.get("latest_observation_count") or 0
            )
            aggregate_fault_rule_count = max(
                aggregate_fault_rule_count,
                int(current_assessment.get("active_fault_rule_count") or 0),
            )
            rule_sources.update([str(current_assessment.get("rule_source") or "unknown")])
            if current_assessment.get("rule_bundle_digest"):
                rule_bundle_digests.add(str(current_assessment["rule_bundle_digest"]))
            if current_assessment.get("error"):
                rule_errors.add(str(current_assessment["error"]))
            for domain, values in dict(current_assessment.get("by_domain") or {}).items():
                target = aggregate_rule_domains.setdefault(
                    str(domain),
                    {
                        "evaluated_observation_count": 0,
                        "threshold_unknown_observation_count": 0,
                        "partial_threshold_observation_count": 0,
                        "missing_threshold_reference_fields": [],
                        "finding_count": 0,
                    },
                )
                target["evaluated_observation_count"] += int(
                    values.get("evaluated_observation_count") or 0
                )
                target["threshold_unknown_observation_count"] += int(
                    values.get("threshold_unknown_observation_count") or 0
                )
                target["partial_threshold_observation_count"] += int(
                    values.get("partial_threshold_observation_count") or 0
                )
                target["missing_threshold_reference_fields"] = sorted(
                    {
                        *target["missing_threshold_reference_fields"],
                        *(values.get("missing_threshold_reference_fields") or []),
                    }
                )
                target["finding_count"] += int(values.get("finding_count") or 0)
            observation_by_id = {str(item["id"]): item for item in observations}
            serialized_changes: list[dict[str, Any]] = []
            for change in changes:
                change_id = str(getattr(change, "id", "") or "")
                old_value = change.old_value_json
                new_value = change.new_value_json
                suspected_format = suspected_format_change(change.field, old_value, new_value)
                if suspected_format:
                    suspected_format_change_count += 1
                current_observation = observation_by_id.get(
                    str(getattr(change, "current_observation_id", "") or "")
                )
                reason = no_event_by_change.get(change_id) or str(
                    getattr(change, "decision_reason", "") or ""
                )
                serialized_changes.append(
                    {
                        "id": change_id,
                        "device_id": change.device_id,
                        "device_name": device_name,
                        "command": getattr(change, "canonical_command", None) or "",
                        "entity_type": change.entity_type,
                        "entity_key": change.entity_key,
                        "dimensions": dict(getattr(change, "dimensions_json", None) or {}),
                        "field": change.field,
                        "observation_type": getattr(change, "observation_type", None),
                        "old_value": old_value,
                        "new_value": new_value,
                        "unit": (current_observation or {}).get("unit"),
                        "change_type": getattr(change, "change_type", None) or "modified",
                        "event_status": getattr(change, "event_status", None) or "unknown",
                        "no_event_reason": reason or "unknown",
                        "comparison_type": getattr(change, "comparison_type", None),
                        "previous_snapshot_id": getattr(change, "previous_snapshot_id", None),
                        "current_snapshot_id": getattr(change, "current_snapshot_id", None),
                        "note": "疑似同一 IP 的前缀格式差异" if suspected_format else "",
                        "detected_at": _iso(getattr(change, "detected_at", None)),
                    }
                )
            serialized_events = [{**event, "device_name": device_name} for event in events]
            for event in events:
                event_types[str(event.get("event_type") or "unknown")] += 1
                severities[str(event.get("severity") or "unknown")] += 1
            total_change_count += len(changes)
            total_event_count += len(events)
            all_changes.extend(serialized_changes)
            all_events.extend(serialized_events)
            run_quality = quality_from_run_data(command_details, observations)
            snapshot_command_counts = Counter(row.canonical_command for row in snapshots)
            duplicate_snapshot_count = sum(
                count - 1 for count in snapshot_command_counts.values() if count > 1
            )
            run_quality["snapshot_count"] = len(snapshots)
            run_quality["duplicate_snapshot_count"] = duplicate_snapshot_count
            aggregate_parse_statuses.update(run_quality["parse_status_counts"])
            aggregate_output_statuses.update(run_quality["output_status_counts"])
            aggregate_command_count += run_quality["command_count"]
            aggregate_execution_success += run_quality["execution_success_count"]
            aggregate_parser_eligible += run_quality["parser_eligible_count"]
            aggregate_parser_ready += run_quality["parser_ready_count"]
            aggregate_observations += run_quality["observation_count"]
            aggregate_snapshot_count += len(snapshots)
            aggregate_duplicate_snapshots += duplicate_snapshot_count
            aggregate_no_event_reasons.update(no_event_summary.get("by_reason") or {})
            run_rows.append(
                {
                    "run_id": fact["run_id"],
                    "task_id": fact.get("task_id"),
                    "execution_kind": fact["execution_kind"],
                    "execution_phase": fact.get("phase"),
                    "device_id": device_id,
                    "device_name": device_name,
                    "status": fact["status"],
                    "snapshot_run_id": snapshot_run_id or "",
                    "change_count": len(changes),
                    "event_count": len(events),
                    "error_message": fact.get("error") or "",
                    "quality": run_quality,
                    "command_details": command_details,
                    "observations": observations,
                    "changes": serialized_changes,
                    "events": serialized_events,
                    "current_state_assessment": current_assessment,
                    "current_findings": current_assessment["findings"],
                    "no_event_summary": no_event_summary,
                    "domain_checks": domain_checks(
                        observations=observations,
                        command_details=command_details,
                        changes=serialized_changes,
                        events=serialized_events,
                        current_findings=current_assessment["findings"],
                        rule_coverage=current_assessment,
                    ),
                }
            )
        pipeline_summary = (
            inspection_pipeline_summary(facts)
            if direct_rows is not None
            else build_patrol_pipeline_summary(runs, steps_by_run)
        )
        scope = _batch_scope(db, patrol_batch_id, facts, status_counts)
    parser_rate = (
        aggregate_parser_ready / aggregate_parser_eligible
        if aggregate_parser_eligible
        else (1.0 if facts else 0.0)
    )
    aggregate_quality = {
        "status": (
            "not_started"
            if not facts or (direct_rows is not None and not aggregate_command_count)
            else (
                "degraded"
                if parser_rate < 1.0
                or aggregate_parse_statuses.get("template_missing")
                or aggregate_parse_statuses.get("parse_failed")
                or aggregate_output_statuses.get("command_error")
                or aggregate_output_statuses.get("empty_unverified")
                or aggregate_output_statuses.get("truncated")
                or aggregate_output_statuses.get("paged")
                else "ready"
            )
        ),
        "command_count": aggregate_command_count,
        "execution_success_count": aggregate_execution_success,
        "parse_status_counts": dict(aggregate_parse_statuses),
        "output_status_counts": dict(aggregate_output_statuses),
        "parser_eligible_count": aggregate_parser_eligible,
        "parser_ready_count": aggregate_parser_ready,
        "parser_ready_rate": parser_rate,
        "observation_count": aggregate_observations,
        "snapshot_count": aggregate_snapshot_count,
        "duplicate_snapshot_count": aggregate_duplicate_snapshots,
    }
    unstructured_comparison = summarize_unstructured_changes(
        all_changes,
        collected_output_count=int(aggregate_parse_statuses.get("raw_hashed") or 0),
    )
    no_event_total = sum(aggregate_no_event_reasons.values())
    if not no_event_total:
        no_event_total = sum(change.get("event_status") == "no_event" for change in all_changes)
        aggregate_no_event_reasons.update(
            change.get("no_event_reason") or "unknown"
            for change in all_changes
            if change.get("event_status") == "no_event"
        )
    action_items: list[str] = []
    missing_commands = sorted(
        {
            command["command"]
            for row in run_rows
            for command in row["command_details"]
            if command["parse_status"] == "template_missing"
        }
    )
    if missing_commands:
        action_items.append("补齐 Parser 模板：" + "、".join(missing_commands))
    legacy_unmatched = int(aggregate_no_event_reasons.get("no_active_rule_matched") or 0)
    missing_rule_count = (
        int(aggregate_no_event_reasons.get("no_active_event_rules") or 0) + legacy_unmatched
    )
    evaluated_no_match_count = int(
        aggregate_no_event_reasons.get("active_rules_evaluated_no_match") or 0
    )
    if missing_rule_count:
        action_items.append(
            f"生成并激活 EventRule：{missing_rule_count} 条 Change 缺少活动规则判定"
        )
    if suspected_format_change_count:
        action_items.append(
            f"复核 IP 语义归一化：{suspected_format_change_count} 条变化仅存在前缀格式差异"
        )
    if aggregate_duplicate_snapshots:
        action_items.append(
            f"清理 Command Profile 重复项：检测到 {aggregate_duplicate_snapshots} 条重复命令证据"
        )
    command_errors = int(aggregate_output_statuses.get("command_error") or 0)
    if command_errors:
        action_items.append(f"处理命令执行错误：{command_errors} 条")
    truncated_outputs = int(aggregate_output_statuses.get("truncated") or 0)
    if truncated_outputs:
        action_items.append(f"重新采集被截断的命令输出：{truncated_outputs} 条")
    paged_outputs = int(aggregate_output_statuses.get("paged") or 0)
    if paged_outputs:
        action_items.append(f"关闭分页并重新采集命令输出：{paged_outputs} 条")
    if aggregate_latest_observations and not aggregate_evaluated_observations:
        action_items.append("补齐当前状态判定规则：结构化观测尚无可适用的活动 EventRule")
    if aggregate_threshold_unknown_observations:
        action_items.append(
            "补齐设备或光模块阈值："
            f"{aggregate_threshold_unknown_observations} 条当前观测无法完整判定"
        )
    if aggregate_partial_threshold_observations:
        action_items.append(
            "补全设备阈值层级："
            f"{aggregate_partial_threshold_observations} 条当前观测仅完成部分阈值判定"
        )
    if unstructured_comparison["changed_output_count"]:
        action_items.append(
            "复核非结构化输出变化："
            f"{unstructured_comparison['changed_output_count']} 项输出发生有效变化，"
            "请核对变更单、配置备份或日志上下文"
        )
    current_severities = Counter(
        str(item.get("severity") or "unknown") for item in all_current_findings
    )
    current_event_types = Counter(
        str(item.get("event_type") or "unknown") for item in all_current_findings
    )
    rule_coverage = {
        "available": not bool(rule_errors) and bool(aggregate_fault_rule_count),
        "active_fault_rule_count": aggregate_fault_rule_count,
        "latest_observation_count": aggregate_latest_observations,
        "evaluated_observation_count": aggregate_evaluated_observations,
        "threshold_unknown_observation_count": (aggregate_threshold_unknown_observations),
        "partial_threshold_observation_count": (aggregate_partial_threshold_observations),
        "evaluation_rate": (
            aggregate_evaluated_observations / aggregate_latest_observations
            if aggregate_latest_observations
            else 0.0
        ),
        "finding_count": len(all_current_findings),
        "severity_counts": dict(current_severities),
        "event_types": dict(current_event_types),
        "by_domain": aggregate_rule_domains,
        "sources": dict(rule_sources),
        "bundle_digests": sorted(rule_bundle_digests),
        "errors": sorted(rule_errors),
        "error": "；".join(sorted(rule_errors)),
    }
    summary = {
        "patrol_batch_id": patrol_batch_id,
        "run_count": len(facts),
        "device_count": len(device_ids),
        "change_count": total_change_count,
        "event_count": total_event_count,
        "status_counts": dict(status_counts),
        "scope": scope,
        "pipeline": pipeline_summary,
        "event_types": dict(event_types),
        "severities": dict(severities),
        "runs": run_rows,
        "quality": aggregate_quality,
        "no_event_summary": {
            "total": no_event_total,
            "by_reason": dict(aggregate_no_event_reasons),
            "missing_rule_count": missing_rule_count,
            "evaluated_no_match_count": evaluated_no_match_count,
            "unmatched_rule_count": missing_rule_count + evaluated_no_match_count,
        },
        "suspected_format_change_count": suspected_format_change_count,
        "events": all_events,
        "changes": all_changes,
        "unstructured_comparison": unstructured_comparison,
        "current_findings": all_current_findings,
        "current_finding_count": len(all_current_findings),
        "current_state_rule_coverage": rule_coverage,
        "sample_events": all_events[:20],
        "sample_changes": all_changes[:20],
        "sample_current_findings": all_current_findings[:20],
        "action_items": action_items,
        "deliverables": {
            "execution_ledger_url": (
                f"/api/v1/workflows/runs/patrol-batches/{patrol_batch_id}/export.csv"
            ),
            "raw_cli_url": f"/api/v1/workflows/runs/patrol-batches/{patrol_batch_id}/raw-cli.zip",
            "raw_data_url": (
                f"/api/v1/workflows/runs/patrol-batches/{patrol_batch_id}/raw-data.jsonl"
            ),
            "events_url": f"/api/v1/workflows/runs/patrol-batches/{patrol_batch_id}/events.csv",
            "report_url": f"/api/v1/workflows/runs/patrol-batches/{patrol_batch_id}/report.docx",
            "report_json_url": (
                f"/api/v1/workflows/runs/patrol-batches/{patrol_batch_id}/report.json"
            ),
            "technical_report_url": (
                f"/api/v1/workflows/runs/patrol-batches/{patrol_batch_id}/report.md"
            ),
            "technical_workbook_url": (
                f"/api/v1/workflows/runs/patrol-batches/{patrol_batch_id}/report.xlsx"
            ),
            "csv_url": f"/api/v1/workflows/runs/patrol-batches/{patrol_batch_id}/export.csv",
        },
    }
    leadership = build_leadership_report(patrol_batch_id, summary)
    summary["assessment"] = leadership["assessment"]
    summary["risk_summary"] = leadership["risk_summary"]
    return summary
