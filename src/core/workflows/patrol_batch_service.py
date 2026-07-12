# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Patrol batch summary policy independent from HTTP transport."""

from __future__ import annotations

import ipaddress
import json
import math
from collections import Counter
from datetime import datetime
from typing import Any

from sqlalchemy import or_

from src.infrastructure.db.models import (
    CommandSnapshot,
    NetworkChangeRecord,
    NetworkEventOccurrenceRecord,
    NetworkEventRecord,
    PatrolCollectionResult,
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
    ("raw", "原始配置指纹"),
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
    if "optical" in entity or "transceiver" in text:
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
    if entity == "device" or any(token in text for token in ("version", "device manuinfo")):
        return "device"
    return None


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
            str(item.get("output_status") or "not_classified")
            for item in command_details
        )
        execution_success_count = sum(
            item.get("execution_status") == "complete" for item in command_details
        )
        eligible = [
            item
            for item in command_details
            if item.get("execution_status") == "complete"
            and item.get("parse_status")
            not in {"ignored", "not_applicable", "raw_hashed"}
        ]
        ready = [
            item
            for item in eligible
            if item.get("parse_status") in {"parsed", "parse_empty"}
        ]
        parser_rate = len(ready) / len(eligible) if eligible else 1.0
        degraded = (
            parser_rate < 1.0
            or bool(parse_counts.get("template_missing"))
            or bool(parse_counts.get("parse_failed"))
            or bool(output_counts.get("command_error"))
            or bool(output_counts.get("empty_unverified"))
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
    ) -> list[dict[str, Any]]:
        checks: list[dict[str, Any]] = []
        latest_observations: dict[tuple[str, str, str], dict[str, Any]] = {}
        for item in observations:
            key = (
                str(item.get("entity_type") or ""),
                str(item.get("entity_key") or ""),
                str(item.get("field") or ""),
            )
            latest_observations[key] = item
        observations = list(latest_observations.values())
        for domain, label in REPORT_DOMAINS:
            domain_observations = [
                item
                for item in observations
                if report_domain(item.get("entity_type"), item.get("command")) == domain
            ]
            domain_commands = [
                item
                for item in command_details
                if report_domain(None, item.get("command")) == domain
            ]
            domain_changes = [
                item
                for item in changes
                if report_domain(item.get("entity_type"), item.get("command")) == domain
            ]
            domain_events = [
                item
                for item in events
                if report_domain(item.get("entity_type"), item.get("event_type")) == domain
            ]
            if not any((domain_commands, domain_observations, domain_changes, domain_events)):
                continue
            parse_statuses = {
                str(item.get("parse_status") or "") for item in domain_commands
            }
            if domain_events:
                status = "异常"
                reason = f"产生 {len(domain_events)} 个事件"
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
            elif domain_changes:
                reason_counts = Counter(
                    str(item.get("no_event_reason") or "unknown")
                    for item in domain_changes
                )
                missing_rules = int(reason_counts.get("no_active_event_rules") or 0)
                evaluated = int(
                    reason_counts.get("active_rules_evaluated_no_match") or 0
                )
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
                "key": f'{definition["step_index"]}:{definition["step_name"]}',
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

    completed_stages = [
        stage for stage in stages if stage["duration"]["p95_seconds"] is not None
    ]
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
    status_counts = Counter(str(run.status or "unknown") for run in runs)
    device_ids = {
        str(
            (run.context or {}).get("device_id")
            or (run.context or {}).get("device_name")
            or run.id
        )
        for run in runs
    }
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
        for run in runs:
            context = run.context or {}
            device_id = str(context.get("device_id") or "")
            device_name = str(context.get("device_name") or device_id or "-")
            steps = steps_by_run.get(str(run.id), [])
            snapshot_run_id = None
            for step in steps:
                snapshot_run_id = _find_nested_value(
                    step.result or {}, "snapshot_run_id"
                )
                if snapshot_run_id:
                    break
            changes: list[NetworkChangeRecord] = []
            events: list[NetworkEventRecord] = []
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
                event_query = db.query(NetworkEventRecord)
                try:
                    occurrence_event_ids = db.query(
                        NetworkEventOccurrenceRecord.event_id
                    ).filter(
                        NetworkEventOccurrenceRecord.run_id == str(snapshot_run_id)
                    )
                    event_query = event_query.filter(
                        or_(
                            NetworkEventRecord.run_id == str(snapshot_run_id),
                            NetworkEventRecord.id.in_(occurrence_event_ids),
                        )
                    )
                except AttributeError:
                    event_query = event_query.filter(
                        NetworkEventRecord.run_id == str(snapshot_run_id)
                    )
                events = event_query.order_by(
                    NetworkEventRecord.occurred_at.desc()
                ).all()
            no_event_summary = {}
            for step in steps:
                candidate = _find_nested_value(step.result or {}, "no_event_summary")
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
                    "command": row.canonical_command,
                    "entity_type": row.entity_type,
                    "entity_key": row.entity_key,
                    "field": row.field_name,
                    "observation_type": row.observation_type,
                    "value": row.value_json,
                    "unit": row.unit,
                    "observed_at": _iso(row.observed_at),
                }
                for row in observation_rows
            ]
            observation_by_id = {str(item["id"]): item for item in observations}
            serialized_changes: list[dict[str, Any]] = []
            for change in changes:
                change_id = str(getattr(change, "id", "") or "")
                old_value = change.old_value_json
                new_value = change.new_value_json
                suspected_format = suspected_format_change(
                    change.field, old_value, new_value
                )
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
                        "field": change.field,
                        "old_value": old_value,
                        "new_value": new_value,
                        "unit": (current_observation or {}).get("unit"),
                        "change_type": getattr(change, "change_type", None)
                        or "modified",
                        "event_status": getattr(change, "event_status", None)
                        or "unknown",
                        "no_event_reason": reason or "unknown",
                        "note": "疑似同一 IP 的前缀格式差异"
                        if suspected_format
                        else "",
                        "detected_at": _iso(getattr(change, "detected_at", None)),
                    }
                )
            serialized_events = [
                {
                    "id": getattr(event, "id", None),
                    "device_id": event.device_id,
                    "device_name": device_name,
                    "event_type": event.event_type,
                    "severity": event.severity,
                    "entity_type": event.entity_type,
                    "entity_key": event.entity_key,
                    "occurred_at": _iso(getattr(event, "occurred_at", None)),
                }
                for event in events
            ]
            for event in events:
                event_types[str(event.event_type or "unknown")] += 1
                severities[str(event.severity or "unknown")] += 1
            total_change_count += len(changes)
            total_event_count += len(events)
            all_changes.extend(serialized_changes)
            all_events.extend(serialized_events)
            run_quality = quality_from_run_data(command_details, observations)
            snapshot_command_counts = Counter(
                row.canonical_command for row in snapshots
            )
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
            aggregate_no_event_reasons.update(
                no_event_summary.get("by_reason") or {}
            )
            run_rows.append(
                {
                    "run_id": run.id,
                    "device_id": device_id,
                    "device_name": device_name,
                    "status": run.status or "",
                    "snapshot_run_id": snapshot_run_id or "",
                    "change_count": len(changes),
                    "event_count": len(events),
                    "error_message": run.error_message or "",
                    "quality": run_quality,
                    "command_details": command_details,
                    "observations": observations,
                    "changes": serialized_changes,
                    "events": serialized_events,
                    "no_event_summary": no_event_summary,
                    "domain_checks": domain_checks(
                        observations=observations,
                        command_details=command_details,
                        changes=serialized_changes,
                        events=serialized_events,
                    ),
                }
            )
        pipeline_summary = build_patrol_pipeline_summary(runs, steps_by_run)
    parser_rate = (
        aggregate_parser_ready / aggregate_parser_eligible
        if aggregate_parser_eligible
        else 1.0
    )
    aggregate_quality = {
        "status": (
            "not_started"
            if not runs
            else (
                "degraded"
                if parser_rate < 1.0
                or aggregate_parse_statuses.get("template_missing")
                or aggregate_parse_statuses.get("parse_failed")
                or aggregate_output_statuses.get("command_error")
                or aggregate_output_statuses.get("empty_unverified")
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
    no_event_total = sum(aggregate_no_event_reasons.values())
    if not no_event_total:
        no_event_total = sum(
            change.get("event_status") == "no_event" for change in all_changes
        )
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
    legacy_unmatched = int(
        aggregate_no_event_reasons.get("no_active_rule_matched") or 0
    )
    missing_rule_count = int(
        aggregate_no_event_reasons.get("no_active_event_rules") or 0
    ) + legacy_unmatched
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
    return {
        "patrol_batch_id": patrol_batch_id,
        "run_count": len(runs),
        "device_count": len(device_ids),
        "change_count": total_change_count,
        "event_count": total_event_count,
        "status_counts": dict(status_counts),
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
            "unmatched_rule_count": missing_rule_count
            + evaluated_no_match_count,
        },
        "suspected_format_change_count": suspected_format_change_count,
        "events": all_events,
        "changes": all_changes,
        "sample_events": all_events[:20],
        "sample_changes": all_changes[:20],
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
            "report_url": f"/api/v1/workflows/runs/patrol-batches/{patrol_batch_id}/report.md",
            "csv_url": f"/api/v1/workflows/runs/patrol-batches/{patrol_batch_id}/export.csv",
        },
    }
