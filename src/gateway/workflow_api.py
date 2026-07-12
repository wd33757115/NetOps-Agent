# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Workflow 查询与管理 API。"""

from __future__ import annotations

import csv
import io
import ipaddress
import json
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import or_

from src.auth.dependencies import get_optional_user, require_role
from src.auth.models import CurrentUser
from src.core.workflows.dsl import GenerateOptions, WorkflowDSL
from src.core.workflows.engine import WorkflowEngine
from src.core.workflows.events import get_workflow_timeline
from src.core.workflows.generator import generate_and_persist, preview_workflow
from src.core.workflows.manager import (
    get_plugin_detail,
    preview_chat_intent,
    save_plugin,
    template_to_summary,
    validate_plugin_files,
    validate_workflow_yaml,
)
from src.core.workflows.mapping import build_mapping_suggestions
from src.core.workflows.registry import (
    WORKFLOWS_ROOT,
    get_template,
    list_templates,
    load_workflows,
    missing_test_context,
)
from src.core.workflows.repository import (
    get_workflow_run,
    list_child_runs,
    list_workflow_runs,
    list_workflow_steps,
    update_run_context,
)
from src.core.workflows.stream import stream_workflow_events
from src.gateway.schemas import (
    WorkflowChildRunSummary,
    WorkflowRunResponse,
    WorkflowStepResponse,
    WorkflowTimelineEvent,
)
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
from src.observability.langfuse import get_trace_url

router = APIRouter(prefix="/api/v1/workflows", tags=["Workflows"])

ACTIVE_PATROL_RUN_STATUSES = {
    "pending",
    "queued",
    "running",
    "in_progress",
    "collecting",
    "dispatching",
}


class ValidateWorkflowRequest(BaseModel):
    workflow_yaml: str = Field(..., description="WORKFLOW.yaml 内容")
    chat_intent_yaml: str | None = None


class SaveWorkflowPluginRequest(BaseModel):
    name: str = Field(..., description="插件目录名")
    category: str = Field(default="itsm", description="workflows 下子目录")
    files: dict[str, str] = Field(..., description="WORKFLOW.yaml / CHAT.intent.yaml 等")


class ChatIntentPreviewRequest(BaseModel):
    query: str = Field(..., description="测试话术")
    workflow_name: str | None = None
    chat_intent_yaml: str | None = None
    context: dict[str, Any] | None = None


class WorkflowTestRunRequest(BaseModel):
    template_name: str
    context: dict[str, Any] = Field(default_factory=dict)
    user_id: str | None = None
    thread_id: str | None = None


class WorkflowRetryRequest(BaseModel):
    user_id: str | None = None
    thread_id: str | None = None


class PatrolBatchGovernanceRequest(BaseModel):
    reason: str | None = Field(default=None, description="归档、恢复或保留策略调整原因")
    keep_until: str | None = Field(default=None, description="可选保留到期时间，ISO8601 字符串")
    force: bool = Field(default=False, description="是否允许归档仍在运行的批次")


class WorkflowPreviewRequest(BaseModel):
    dsl: WorkflowDSL
    options: GenerateOptions = Field(default_factory=GenerateOptions)


def _run_summary_dict(run) -> dict[str, Any]:
    context = run.context or {}
    return {
        "run_id": run.id,
        "template_name": run.template_name,
        "ticket_id": run.ticket_id,
        "source": run.source,
        "status": run.status,
        "current_step_index": run.current_step_index,
        "error_message": run.error_message,
        "created_at": run.created_at,
        "completed_at": run.completed_at,
        "device_id": context.get("device_id"),
        "device_name": context.get("device_name"),
        "patrol_batch_id": context.get("patrol_batch_id"),
        "retry_of_run_id": context.get("retry_of_run_id"),
        "rerun_of_batch_id": context.get("rerun_of_batch_id"),
        "rerun_of_run_id": context.get("rerun_of_run_id"),
        "patrol_batch_archived": bool(context.get("patrol_batch_archived")),
        "patrol_batch_archived_at": context.get("patrol_batch_archived_at"),
        "patrol_batch_archive_reason": context.get("patrol_batch_archive_reason"),
        "patrol_batch_keep_until": context.get("patrol_batch_keep_until"),
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _run_context(run) -> dict[str, Any]:
    return dict(run.context or {})


def _is_patrol_batch_archived(runs: list[Any]) -> bool:
    return bool(runs) and all(bool(_run_context(run).get("patrol_batch_archived")) for run in runs)


def _assert_patrol_batch_not_archived(runs: list[Any]) -> None:
    if _is_patrol_batch_archived(runs):
        raise HTTPException(
            status_code=409, detail="patrol batch is archived; restore it before retry or rerun"
        )


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


def _format_report_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return str(value)
    return str(value)


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


def _report_domain(entity_type: Any = None, command: Any = None) -> str | None:
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


def _markdown_cell(value: Any) -> str:
    text = _format_report_value(value) or "-"
    return text.replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _display_entity_key(value: Any) -> str:
    if not isinstance(value, str):
        return _format_report_value(value) or "__singleton__"
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return value or "__singleton__"
    if isinstance(decoded, list):
        return "/".join(str(item) for item in decoded) or "__singleton__"
    return _format_report_value(decoded) or "__singleton__"


def _quality_from_run_data(
    command_details: list[dict[str, Any]],
    observations: list[dict[str, Any]],
) -> dict[str, Any]:
    parse_counts = Counter(str(item.get("parse_status") or "unknown") for item in command_details)
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


def _domain_checks(
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
            if _report_domain(item.get("entity_type"), item.get("command")) == domain
        ]
        domain_commands = [
            item
            for item in command_details
            if _report_domain(None, item.get("command")) == domain
        ]
        domain_changes = [
            item
            for item in changes
            if _report_domain(item.get("entity_type"), item.get("command")) == domain
        ]
        domain_events = [
            item
            for item in events
            if _report_domain(item.get("entity_type"), item.get("event_type")) == domain
        ]
        if not any(
            (domain_commands, domain_observations, domain_changes, domain_events)
        ):
            continue
        parse_statuses = {str(item.get("parse_status") or "") for item in domain_commands}
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
            value = _format_report_value(item.get("value"))
            current_values.append(
                f"{_display_entity_key(item.get('entity_key'))} "
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


def _batch_report_conclusion(summary: dict[str, Any]) -> dict[str, str]:
    failed_runs = sum(
        count for status, count in summary["status_counts"].items() if status != "completed"
    )
    quality = summary.get("quality") or {}
    no_event = summary.get("no_event_summary") or {}
    missing_rules = int(no_event.get("missing_rule_count") or 0)
    suspected = int(summary.get("suspected_format_change_count") or 0)
    if failed_runs:
        return {"status": "执行失败", "trust": "低", "reason": "存在未完成的巡检工作流"}
    if summary.get("event_count"):
        return {"status": "发现异常", "trust": "高", "reason": "已生成可追溯网络事件"}
    if quality.get("status") == "degraded" or missing_rules or suspected:
        reasons = []
        if quality.get("status") == "degraded":
            reasons.append("结构化数据覆盖不完整")
        if missing_rules:
            reasons.append(f"{missing_rules} 条变化因无活动事件规则而无法判定")
        if suspected:
            reasons.append(f"{suspected} 条变化疑似格式噪声")
        return {"status": "部分可信，需复核", "trust": "中", "reason": "；".join(reasons)}
    if summary.get("change_count"):
        return {
            "status": "检测到变化，未触发事件",
            "trust": "高",
            "reason": "活动规则已完成评估，变化未达到事件触发条件",
        }
    return {
        "status": "未发现变化（健康未判定）",
        "trust": "高",
        "reason": "数据完整且无变化或事件；当前值是否健康仍由状态策略判定",
    }


def _suspected_format_change(field: Any, old: Any, new: Any) -> bool:
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


def _build_patrol_batch_report(patrol_batch_id: str, runs: list[Any]) -> str:
    summary = _build_patrol_batch_summary(patrol_batch_id, runs)
    conclusion = _batch_report_conclusion(summary)
    quality = summary["quality"]
    lines = [
        "# 网络设备巡检报告",
        "",
        f"- **批次 ID**：`{patrol_batch_id}`",
        f"- **生成时间**：{datetime.now(timezone.utc).isoformat()}",
        "",
        "## 一、巡检结论",
        "",
        f"- **总体结论**：{conclusion['status']}",
        f"- **结论可信度**：{conclusion['trust']}",
        f"- **判断依据**：{conclusion['reason']}",
        f"- **工作流**：{summary['run_count']} 个，状态分布 {summary['status_counts']}",
        f"- **设备**：{summary['device_count']} 台",
        f"- **Observation / Change / Event**：{quality['observation_count']} / "
        f"{summary['change_count']} / {summary['event_count']}",
        "",
        "> Workflow 完成只表示巡检链路执行结束，不等同于设备健康。",
        "",
        "## 二、设备与执行",
        "",
        "| 设备 | Workflow Run | 状态 | Snapshot Run | 命令 | Observation | Change | Event |",
        "|---|---|---|---|---:|---:|---:|---:|",
    ]
    for row in summary["runs"]:
        lines.append(
            "| "
            + " | ".join(
                _markdown_cell(value)
                for value in (
                    row["device_name"] or row["device_id"],
                    row["run_id"],
                    row["status"],
                    row["snapshot_run_id"],
                    row["quality"]["command_count"],
                    row["quality"]["observation_count"],
                    row["change_count"],
                    row["event_count"],
                )
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## 三、业务检查项",
            "",
            "| 设备 | 检查项 | 状态 | Observation | Change | Event | 说明 | 当前值示例 |",
            "|---|---|---|---:|---:|---:|---|---|",
        ]
    )
    for row in summary["runs"]:
        for check in row["domain_checks"]:
            lines.append(
                "| "
                + " | ".join(
                    _markdown_cell(value)
                    for value in (
                        row["device_name"] or row["device_id"],
                        check["label"],
                        check["status"],
                        check["observation_count"],
                        check["change_count"],
                        check["event_count"],
                        check["reason"],
                        "; ".join(check["current_values"]) or "-",
                    )
                )
                + " |"
            )
    lines.extend(
        [
            "",
            "## 四、数据质量",
            "",
            f"- **数据质量状态**：{quality['status']}",
            f"- **命令执行**：{quality['execution_success_count']} / {quality['command_count']}",
            f"- **Snapshot 证据**：{quality['snapshot_count']}，重复命令证据 "
            f"{quality['duplicate_snapshot_count']} 条",
            f"- **Parser ready**：{quality['parser_ready_count']} / "
            f"{quality['parser_eligible_count']} ({quality['parser_ready_rate']:.1%})",
            f"- **Parse 状态**：{quality['parse_status_counts']}",
            f"- **输出状态**：{quality['output_status_counts']}",
            "",
            "### 命令质量明细",
            "",
            "| 设备 | 命令 | 执行 | Parse | 输出分类 | Fact | 失败代码 |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for row in summary["runs"]:
        for command in row["command_details"]:
            lines.append(
                "| "
                + " | ".join(
                    _markdown_cell(value)
                    for value in (
                        row["device_name"] or row["device_id"],
                        command["command"],
                        command["execution_status"],
                        command["parse_status"],
                        command["output_status"],
                        command["fact_status"],
                        command["failure_code"],
                    )
                )
                + " |"
            )
    no_event = summary["no_event_summary"]
    lines.extend(
        [
            "",
            "## 五、事件与规则判定",
            "",
            f"- **事件数**：{summary['event_count']}",
            f"- **未生成事件的 Change**：{no_event['total']}",
            f"- **原因分布**：{no_event['by_reason']}",
        ]
    )
    if summary["event_count"] == 0 and no_event["total"]:
        if no_event["missing_rule_count"]:
            lines.append(
                "- **解释**：0 Event 不代表设备无异常；当前缺少活动 EventRule，"
                "无法完成事件判定。"
            )
        elif no_event["evaluated_no_match_count"]:
            lines.append(
                "- **解释**：活动 EventRule 已评估这些变化，但均未达到触发条件；"
                "这不等同于对当前状态做出健康认定。"
            )
        else:
            lines.append(
                "- **解释**：0 Event 不代表设备无异常；事件判定上下文不完整，需复核。"
            )
    lines.extend(
        [
            "",
            "## 六、完整变化明细",
            "",
            "| 设备 | 命令 | 实体 | 主键 | 字段 | 旧值 | 新值 | 类型 | 事件状态 | 原因 | 备注 |",
            "|---|---|---|---|---|---|---|---|---|---|---|",
        ]
    )
    if summary["changes"]:
        for change in summary["changes"]:
            lines.append(
                "| "
                + " | ".join(
                    _markdown_cell(value)
                    for value in (
                        change["device_name"] or change["device_id"],
                        change["command"],
                        change["entity_type"],
                        _display_entity_key(change["entity_key"]),
                        change["field"],
                        change["old_value"],
                        change["new_value"],
                        change["change_type"],
                        change["event_status"],
                        change["no_event_reason"],
                        change["note"],
                    )
                )
                + " |"
            )
    else:
        lines.append("| - | - | - | - | - | - | - | - | - | - | 无变化 |")
    lines.extend(["", "## 七、事件明细", ""])
    if summary["events"]:
        lines.extend(
            f"- **{_markdown_cell(event['severity'])}** "
            f"{_markdown_cell(event['event_type'])}："
            f"{_markdown_cell(event['device_name'] or event['device_id'])} "
            f"{_markdown_cell(event['entity_type'])}/"
            f"{_markdown_cell(_display_entity_key(event['entity_key']))}"
            for event in summary["events"]
        )
    else:
        lines.append("- 未生成事件；请结合上一节 no_event 原因判断，不能据此直接认定健康。")
    lines.extend(["", "## 八、待处理项", ""])
    if summary["action_items"]:
        lines.extend(f"- {item}" for item in summary["action_items"])
    else:
        lines.append("- 无")
    lines.extend(["", "## 九、证据引用", ""])
    for row in summary["runs"]:
        lines.append(
            f"- {row['device_name'] or row['device_id']}：Workflow "
            f"`{row['run_id']}`，Snapshot Run `{row['snapshot_run_id']}`"
        )
    lines.append("")
    return "\n".join(lines)


def _build_patrol_batch_summary(patrol_batch_id: str, runs: list[Any]) -> dict[str, Any]:
    status_counts = Counter(str(run.status or "unknown") for run in runs)
    device_ids = {
        str(
            (run.context or {}).get("device_id") or (run.context or {}).get("device_name") or run.id
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
        for run in runs:
            context = run.context or {}
            device_id = str(context.get("device_id") or "")
            device_name = str(context.get("device_name") or device_id or "-")
            steps = (
                db.query(WorkflowStepRecord)
                .filter(WorkflowStepRecord.run_id == run.id)
                .order_by(WorkflowStepRecord.step_index.asc())
                .all()
            )
            snapshot_run_id = None
            for step in steps:
                snapshot_run_id = _find_nested_value(step.result or {}, "snapshot_run_id")
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
                    occurrence_event_ids = db.query(NetworkEventOccurrenceRecord.event_id).filter(
                        NetworkEventOccurrenceRecord.run_id == str(snapshot_run_id)
                    )
                    event_query = event_query.filter(
                        or_(
                            NetworkEventRecord.run_id == str(snapshot_run_id),
                            NetworkEventRecord.id.in_(occurrence_event_ids),
                        )
                    )
                except AttributeError:
                    # Lightweight repository test doubles and pre-0027 readers.
                    event_query = event_query.filter(
                        NetworkEventRecord.run_id == str(snapshot_run_id)
                    )
                events = event_query.order_by(NetworkEventRecord.occurred_at.desc()).all()
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
                suspected_format = _suspected_format_change(
                    change.field,
                    old_value,
                    new_value,
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
                        "change_type": getattr(change, "change_type", None) or "modified",
                        "event_status": getattr(change, "event_status", None) or "unknown",
                        "no_event_reason": reason or "unknown",
                        "note": "疑似同一 IP 的前缀格式差异" if suspected_format else "",
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
            run_quality = _quality_from_run_data(command_details, observations)
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
                    "domain_checks": _domain_checks(
                        observations=observations,
                        command_details=command_details,
                        changes=serialized_changes,
                        events=serialized_events,
                    ),
                }
            )
    parser_rate = (
        aggregate_parser_ready / aggregate_parser_eligible
        if aggregate_parser_eligible
        else 1.0
    )
    aggregate_quality = {
        "status": (
            "degraded"
            if parser_rate < 1.0
            or aggregate_parse_statuses.get("template_missing")
            or aggregate_parse_statuses.get("parse_failed")
            or aggregate_output_statuses.get("command_error")
            or aggregate_output_statuses.get("empty_unverified")
            else "ready"
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
        "sample_events": all_events[:20],
        "sample_changes": all_changes[:20],
        "action_items": action_items,
        "deliverables": {
            "csv_url": f"/api/v1/workflows/runs/patrol-batches/{patrol_batch_id}/export.csv",
            "report_url": f"/api/v1/workflows/runs/patrol-batches/{patrol_batch_id}/report.md",
        },
    }


def _retry_existing_run(run, request: WorkflowRetryRequest, user: CurrentUser | None) -> str:
    context = dict(run.context or {})
    context["retry_of_run_id"] = run.id
    context.pop("langfuse_trace_id", None)
    context.pop("langfuse_workflow_root_span_id", None)
    user_id = request.user_id or (user.user_id if user else run.user_id)
    thread_id = request.thread_id or run.thread_id
    return WorkflowEngine.start(
        run.template_name,
        context,
        source=run.source or "retry",
        user_id=user_id,
        thread_id=thread_id,
        parent_run_id=run.id,
    )


def _rerun_existing_run(
    run,
    request: WorkflowRetryRequest,
    user: CurrentUser | None,
    *,
    new_batch_id: str,
    old_batch_id: str,
) -> str:
    context = dict(run.context or {})
    context["patrol_batch_id"] = new_batch_id
    context["rerun_of_batch_id"] = old_batch_id
    context["rerun_of_run_id"] = run.id
    context.pop("retry_of_run_id", None)
    context.pop("langfuse_trace_id", None)
    context.pop("langfuse_workflow_root_span_id", None)
    user_id = request.user_id or (user.user_id if user else run.user_id)
    thread_id = request.thread_id or run.thread_id
    return WorkflowEngine.start(
        run.template_name,
        context,
        source=run.source or "batch-rerun",
        user_id=user_id,
        thread_id=thread_id,
        parent_run_id=run.id,
    )


class WorkflowGenerateRequest(BaseModel):
    dsl: WorkflowDSL
    options: GenerateOptions = Field(default_factory=lambda: GenerateOptions(persist=True))


class InferMappingsRequest(BaseModel):
    dsl: WorkflowDSL


class ImportWorkflowRequest(BaseModel):
    bundle: dict[str, Any]
    overwrite: bool = False


class PublishWorkflowRequest(BaseModel):
    change_summary: str | None = None


class PublishToMarketRequest(BaseModel):
    title: str | None = None


class WorkflowDryRunRequest(BaseModel):
    dsl: WorkflowDSL
    context: dict[str, Any] = Field(default_factory=dict)
    auto_map_inputs: bool = True


class ChatIntentSuggestRequest(BaseModel):
    description: str = Field(..., min_length=2, description="自然语言触发场景描述")
    workflow_name: str = Field(..., description="Workflow 插件名")
    use_llm: bool = True


def _run_to_response(run, *, include_timeline: bool = True) -> WorkflowRunResponse:
    steps = list_workflow_steps(run.id)
    ctx = run.context or {}
    trace_id = ctx.get("langfuse_trace_id")
    timeline = get_workflow_timeline(run.id) if include_timeline else []
    children = list_child_runs(run.id)
    return WorkflowRunResponse(
        run_id=run.id,
        template_name=run.template_name,
        ticket_id=run.ticket_id,
        source=run.source,
        status=run.status,
        current_step_index=run.current_step_index,
        error_message=run.error_message,
        context=ctx,
        steps=[
            WorkflowStepResponse(
                step_index=s.step_index,
                step_name=s.step_name,
                skill_name=s.skill_name,
                status=s.status,
                celery_task_id=s.celery_task_id,
                output_artifacts=s.output_artifacts,
                error_message=s.error_message,
                started_at=s.started_at,
                completed_at=s.completed_at,
            )
            for s in steps
        ],
        timeline=[WorkflowTimelineEvent(**e) for e in timeline],
        child_runs=[
            WorkflowChildRunSummary(
                run_id=c.id,
                template_name=c.template_name,
                status=c.status,
                error_message=c.error_message,
            )
            for c in children
        ],
        langfuse_trace_id=str(trace_id) if trace_id else None,
        langfuse_url=get_trace_url(str(trace_id)) if trace_id else None,
        created_at=run.created_at,
        completed_at=run.completed_at,
    )


@router.get("/templates")
async def list_workflow_templates():
    load_workflows(force=True)
    return [template_to_summary(t) for t in list_templates()]


@router.post("/preview")
async def preview_workflow_from_dsl(request: WorkflowPreviewRequest):
    """DSL → 插件 YAML 预览（不落盘）。"""
    opts = request.options.model_copy(update={"persist": False})
    return preview_workflow(request.dsl, options=opts)


@router.post("/generate")
async def generate_workflow_from_dsl(
    request: WorkflowGenerateRequest,
    user: CurrentUser | None = Depends(get_optional_user),
):
    """DSL → 生成插件文件，可选落盘与热加载。"""
    opts = request.options
    if opts.publish and (not user or not user.is_admin()):
        raise HTTPException(status_code=403, detail="发布 Workflow 需要 admin 权限")

    result = generate_and_persist(
        request.dsl,
        options=opts,
        user_id=user.user_id if user else None,
    )
    if not result.get("success") and result.get("validation", {}).get("errors"):
        raise HTTPException(
            status_code=400,
            detail={
                "message": result.get("message", "生成失败"),
                "validation": result.get("validation"),
            },
        )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "生成失败"))
    return result


@router.post("/infer-mappings")
async def infer_workflow_mappings(request: InferMappingsRequest):
    """返回各步骤的智能 inputs 映射建议。"""
    return {"suggestions": build_mapping_suggestions(request.dsl.steps)}


@router.post("/dry-run")
async def dry_run_workflow_api(request: WorkflowDryRunRequest):
    """模拟执行 Workflow（不启动 Celery）。"""
    from src.core.workflows.dry_run import dry_run_workflow

    return dry_run_workflow(
        request.dsl,
        request.context,
        auto_map_inputs=request.auto_map_inputs,
    )


@router.post("/chat-intent/suggest-nl")
async def suggest_chat_intent_from_nl_api(request: ChatIntentSuggestRequest):
    """自然语言描述 → CHAT.intent 规则草稿。"""
    from src.core.workflows.chat_intent_nl import suggest_chat_intent_from_nl

    return suggest_chat_intent_from_nl(
        request.description,
        request.workflow_name,
        use_llm=request.use_llm,
    )


@router.get("/expression-hints")
async def get_workflow_expression_hints(
    step_name: str | None = None,
    skill: str | None = None,
    workflow_name: str | None = None,
):
    """
    返回指定步骤可用的表达式提示（context / 上游 steps / 建议映射）。

    可通过 query 传 step_name + 临时 DSL 字段，或传 workflow_name 加载已注册插件。
    """
    from src.core.workflows.expression_hints import build_expression_hints

    if workflow_name:
        tpl = get_template(workflow_name)
        if not tpl:
            raise HTTPException(status_code=404, detail=f"Workflow '{workflow_name}' 不存在")
        from src.core.workflows.dsl import WorkflowMetaDSL, WorkflowStepDSL

        dsl = WorkflowDSL(
            meta=WorkflowMetaDSL(name=tpl.name, description=tpl.description, version=tpl.version),
            steps=[
                WorkflowStepDSL(
                    id=s.name,
                    name=s.name,
                    label=s.label or s.name,
                    skill=s.skill_name,
                    when=s.when,
                )
                for s in tpl.steps
            ],
        )
        return build_expression_hints(dsl, step_name=step_name, skill=skill)

    raise HTTPException(
        status_code=400,
        detail="请使用 POST /expression-hints/preview 传入 DSL，或指定 workflow_name",
    )


class ExpressionHintsPreviewRequest(BaseModel):
    dsl: WorkflowDSL
    step_name: str | None = None
    skill: str | None = None


@router.post("/expression-hints/preview")
async def preview_expression_hints(request: ExpressionHintsPreviewRequest):
    """基于 DSL 预览表达式提示。"""
    from src.core.workflows.expression_hints import build_expression_hints

    return build_expression_hints(
        request.dsl,
        step_name=request.step_name,
        skill=request.skill,
    )


@router.get("/categories")
async def list_workflow_categories():
    """列出 workflows/ 下已有分类目录。"""
    load_workflows(force=True)
    categories = sorted(
        {p.name for p in WORKFLOWS_ROOT.iterdir() if p.is_dir() and not p.name.startswith(".")}
    )
    return {"categories": categories or ["itsm", "custom"]}


# ---------------------------------------------------------------------------
# 插件治理（Phase 3）：元数据 / 版本 / 发布 / 导入导出 / 模板市场
# ---------------------------------------------------------------------------


@router.get("/plugins")
async def list_workflow_plugins():
    """列出插件（文件 + DB 元数据）。"""
    from src.core.workflows.versioning import list_plugins_enriched

    return list_plugins_enriched()


@router.get("/plugins/{name}/versions")
async def list_plugin_versions_api(name: str, limit: int = 50):
    from src.core.workflows.metadata_repo import list_plugin_versions, version_to_dict

    tpl = get_template(name)
    if not tpl:
        raise HTTPException(status_code=404, detail=f"Workflow '{name}' 不存在")
    versions = list_plugin_versions(name, limit=limit)
    return [version_to_dict(v) for v in versions]


@router.get("/plugins/{name}/versions/diff")
async def diff_plugin_versions_api(name: str, v1: int, v2: int, file_key: str = "WORKFLOW.yaml"):
    from src.core.workflows.versioning import diff_plugin_versions

    try:
        return diff_plugin_versions(name, v1, v2, file_key=file_key)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/plugins/{name}/export")
async def export_workflow_plugin(name: str, format: str = "json"):
    from src.core.workflows.versioning import export_plugin_bundle, export_plugin_zip_bytes

    try:
        if format == "zip":
            data = export_plugin_zip_bytes(name)
            return Response(
                content=data,
                media_type="application/zip",
                headers={"Content-Disposition": f'attachment; filename="{name}.zip"'},
            )
        return export_plugin_bundle(name)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/import")
async def import_workflow_plugin(
    request: ImportWorkflowRequest,
    user: CurrentUser | None = Depends(get_optional_user),
):
    from src.core.workflows.versioning import import_plugin_bundle

    result = import_plugin_bundle(
        request.bundle,
        overwrite=request.overwrite,
        user_id=user.user_id if user else None,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "导入失败"))
    return result


@router.post("/plugins/{name}/submit-review")
async def submit_plugin_review(
    name: str,
    user: CurrentUser = Depends(require_role(["admin", "operator"])),
):
    from src.core.workflows.metadata_repo import transition_plugin_status, upsert_plugin_metadata

    tpl = get_template(name)
    if not tpl:
        raise HTTPException(status_code=404, detail=f"Workflow '{name}' 不存在")
    try:
        meta = transition_plugin_status(name, "review", user_id=user.user_id)
    except LookupError:
        meta = upsert_plugin_metadata(
            name,
            category=tpl.plugin_dir.parent.name,
            description=tpl.description,
            status="review",
            user_id=user.user_id,
        )
    return {"success": True, "status": meta.status, "message": "已提交审核"}


@router.post("/plugins/{name}/publish")
async def publish_workflow_plugin(
    name: str,
    request: PublishWorkflowRequest = PublishWorkflowRequest(),
    user: CurrentUser = Depends(require_role(["admin"])),
):
    from src.core.workflows.versioning import publish_plugin

    result = publish_plugin(name, user_id=user.user_id, change_summary=request.change_summary)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "发布失败"))
    return result


@router.post("/plugins/{name}/reject")
async def reject_plugin_review(
    name: str,
    user: CurrentUser = Depends(require_role(["admin"])),
):
    from src.core.workflows.metadata_repo import transition_plugin_status

    try:
        meta = transition_plugin_status(name, "draft", user_id=user.user_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"success": True, "status": meta.status, "message": "已退回 draft"}


@router.post("/plugins/{name}/archive")
async def archive_workflow_plugin(
    name: str,
    user: CurrentUser = Depends(require_role(["admin"])),
):
    from src.core.workflows.metadata_repo import transition_plugin_status

    try:
        meta = transition_plugin_status(name, "archived", user_id=user.user_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"success": True, "status": meta.status}


@router.delete("/plugins/{name}")
async def delete_workflow_plugin(
    name: str,
    user: CurrentUser = Depends(require_role(["admin", "operator"])),
):
    from src.core.workflows.versioning import delete_plugin

    result = delete_plugin(name, user_id=user.user_id)
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("message", "删除失败"))
    return result


@router.post("/plugins/{name}/publish-to-market")
async def publish_plugin_to_market_api(
    name: str,
    request: PublishToMarketRequest = PublishToMarketRequest(),
    user: CurrentUser = Depends(require_role(["admin", "operator"])),
):
    from src.core.workflows.marketplace import publish_plugin_to_market

    result = publish_plugin_to_market(name, title=request.title, user_id=user.user_id)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "发布失败"))
    return result


@router.get("/market/templates")
async def list_market_templates(category: str | None = None, featured: bool = False):
    from src.core.workflows.marketplace import list_market_templates

    return list_market_templates(category=category, featured_only=featured)


@router.get("/market/templates/{template_id}")
async def get_market_template_detail(template_id: str):
    from src.core.workflows.marketplace import get_market_template, increment_market_use

    detail = get_market_template(template_id)
    if not detail:
        raise HTTPException(status_code=404, detail="模板不存在")
    increment_market_use(template_id)
    return detail


@router.get("/templates/{name}")
async def get_workflow_template(name: str):
    detail = get_plugin_detail(name)
    if not detail:
        raise HTTPException(status_code=404, detail=f"Workflow 模板 '{name}' 不存在")
    return detail


@router.get("/templates/{name}/dsl")
async def get_workflow_template_dsl(name: str):
    """将已有插件 YAML 反解析为 WorkflowDSL，供 Wizard 编辑。"""
    from src.core.workflows.generator import dsl_from_plugin_files
    from src.core.workflows.registry import get_template

    tpl = get_template(name)
    if not tpl:
        raise HTTPException(status_code=404, detail=f"Workflow 模板 '{name}' 不存在")
    detail = get_plugin_detail(name)
    assert detail is not None
    category = tpl.plugin_dir.parent.name if tpl.plugin_dir.parent != WORKFLOWS_ROOT else "itsm"
    dsl = dsl_from_plugin_files(detail["files"], category=category)
    return {
        "success": True,
        "dsl": dsl.model_dump(),
        "chat_intent_yaml": detail["files"].get("CHAT.intent.yaml") or "",
        "webhook_yaml": detail["files"].get("ITSM.webhook.yaml") or "",
    }


@router.post("/reload")
async def reload_workflows():
    from src.core.workflows.reload_bus import broadcast_workflow_reload

    stats = broadcast_workflow_reload(source="api")
    return {
        "success": True,
        "count": stats.get("templates", 0),
        "intents": stats.get("intents", 0),
        "message": "Workflow 插件已重载（含多 Worker 广播）",
    }


@router.post("/validate")
async def validate_workflow(request: ValidateWorkflowRequest):
    if request.chat_intent_yaml:
        return validate_plugin_files(
            {"WORKFLOW.yaml": request.workflow_yaml, "CHAT.intent.yaml": request.chat_intent_yaml}
        )
    return validate_workflow_yaml(request.workflow_yaml)


@router.post("/templates", status_code=201)
async def create_workflow_plugin(request: SaveWorkflowPluginRequest):
    result = save_plugin(request.name, category=request.category, files=request.files)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "保存失败"))
    return result


@router.put("/templates/{name}")
async def update_workflow_plugin(name: str, request: SaveWorkflowPluginRequest):
    tpl = get_template(name)
    if not tpl:
        raise HTTPException(status_code=404, detail=f"Workflow 模板 '{name}' 不存在")
    category = request.category or tpl.plugin_dir.parent.name
    result = save_plugin(name, category=category, files=request.files)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "保存失败"))
    return result


@router.post("/chat-intent/preview")
async def preview_chat_intent_match(request: ChatIntentPreviewRequest):
    return preview_chat_intent(
        request.query,
        workflow_name=request.workflow_name,
        chat_intent_yaml=request.chat_intent_yaml,
        context=request.context,
    )


@router.get("/runs")
async def list_runs(
    limit: int = 50,
    template_name: str | None = None,
    ticket_id: str | None = None,
    source: str | None = None,
    status: str | None = None,
    device_id: str | None = None,
    patrol_batch_id: str | None = None,
):
    runs = list_workflow_runs(
        limit=limit,
        template_name=template_name,
        ticket_id=ticket_id,
        source=source,
        status=status,
        device_id=device_id,
        patrol_batch_id=patrol_batch_id,
    )
    return [_run_summary_dict(r) for r in runs]


@router.get("/runs/patrol-batches")
async def list_patrol_batches(limit: int = 20, include_archived: bool = False):
    runs = list_workflow_runs(
        limit=1000,
        template_name="patrol-change-event",
        source="device-management",
    )
    batches: dict[str, dict[str, Any]] = {}
    for run in runs:
        context = run.context or {}
        batch_id = context.get("patrol_batch_id")
        if not batch_id:
            continue
        item = batches.setdefault(
            str(batch_id),
            {
                "patrol_batch_id": str(batch_id),
                "workflow_name": run.template_name,
                "source": run.source,
                "ticket_id": run.ticket_id,
                "total": 0,
                "status_counts": {},
                "failed_count": 0,
                "running_count": 0,
                "completed_count": 0,
                "pending_count": 0,
                "cancelled_count": 0,
                "created_at": run.created_at,
                "completed_at": None,
                "sample_devices": [],
                "archived": True,
                "archived_count": 0,
                "archived_at": None,
                "archive_reason": None,
                "archived_by": None,
                "keep_until": None,
            },
        )
        item["total"] += 1
        if context.get("patrol_batch_archived"):
            item["archived_count"] += 1
            archived_at = context.get("patrol_batch_archived_at")
            if archived_at and (
                not item["archived_at"] or str(archived_at) > str(item["archived_at"])
            ):
                item["archived_at"] = archived_at
            item["archive_reason"] = item["archive_reason"] or context.get(
                "patrol_batch_archive_reason"
            )
            item["archived_by"] = item["archived_by"] or context.get("patrol_batch_archived_by")
        else:
            item["archived"] = False
        item["keep_until"] = item["keep_until"] or context.get("patrol_batch_keep_until")
        item["status_counts"][run.status] = item["status_counts"].get(run.status, 0) + 1
        if run.status in {"failed", "running", "completed", "pending", "cancelled"}:
            item[f"{run.status}_count"] += 1
        if run.created_at and (not item["created_at"] or run.created_at < item["created_at"]):
            item["created_at"] = run.created_at
        if run.completed_at and (
            not item["completed_at"] or run.completed_at > item["completed_at"]
        ):
            item["completed_at"] = run.completed_at
        device_name = context.get("device_name") or context.get("device_id")
        if (
            device_name
            and device_name not in item["sample_devices"]
            and len(item["sample_devices"]) < 5
        ):
            item["sample_devices"].append(device_name)
    visible_batches = [
        item for item in batches.values() if include_archived or not item.get("archived")
    ]
    ordered = sorted(
        visible_batches,
        key=lambda item: str(item.get("created_at") or ""),
        reverse=True,
    )
    return {"items": ordered[: max(1, min(limit, 100))]}


@router.post("/runs/patrol-batches/{patrol_batch_id}/archive")
async def archive_patrol_batch(
    patrol_batch_id: str,
    request: PatrolBatchGovernanceRequest = PatrolBatchGovernanceRequest(),
    user: CurrentUser | None = Depends(get_optional_user),
):
    runs = list_workflow_runs(
        limit=1000,
        template_name="patrol-change-event",
        source="device-management",
        patrol_batch_id=patrol_batch_id,
    )
    if not runs:
        raise HTTPException(status_code=404, detail="patrol batch not found")
    active = [run for run in runs if str(run.status or "").lower() in ACTIVE_PATROL_RUN_STATUSES]
    if active and not request.force:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "patrol batch has active runs; use force=true if you really want to archive it",
                "active_run_count": len(active),
            },
        )
    archived_at = _now_iso()
    actor = user.user_id if user else "system"
    for run in runs:
        context = _run_context(run)
        context["patrol_batch_archived"] = True
        context["patrol_batch_archived_at"] = archived_at
        context["patrol_batch_archived_by"] = actor
        context["patrol_batch_archive_reason"] = request.reason or "manual archive"
        if request.keep_until:
            context["patrol_batch_keep_until"] = request.keep_until
        update_run_context(run.id, context)
    return {
        "success": True,
        "patrol_batch_id": patrol_batch_id,
        "archived": True,
        "affected_runs": len(runs),
        "active_run_count": len(active),
        "archived_at": archived_at,
        "archived_by": actor,
        "reason": request.reason or "manual archive",
        "keep_until": request.keep_until,
    }


@router.post("/runs/patrol-batches/{patrol_batch_id}/restore")
async def restore_patrol_batch(
    patrol_batch_id: str,
    request: PatrolBatchGovernanceRequest = PatrolBatchGovernanceRequest(),
    user: CurrentUser | None = Depends(get_optional_user),
):
    runs = list_workflow_runs(
        limit=1000,
        template_name="patrol-change-event",
        source="device-management",
        patrol_batch_id=patrol_batch_id,
    )
    if not runs:
        raise HTTPException(status_code=404, detail="patrol batch not found")
    restored_at = _now_iso()
    actor = user.user_id if user else "system"
    for run in runs:
        context = _run_context(run)
        context["patrol_batch_archived"] = False
        context["patrol_batch_restored_at"] = restored_at
        context["patrol_batch_restored_by"] = actor
        context["patrol_batch_restore_reason"] = request.reason or "manual restore"
        if request.keep_until:
            context["patrol_batch_keep_until"] = request.keep_until
        update_run_context(run.id, context)
    return {
        "success": True,
        "patrol_batch_id": patrol_batch_id,
        "archived": False,
        "affected_runs": len(runs),
        "restored_at": restored_at,
        "restored_by": actor,
        "reason": request.reason or "manual restore",
        "keep_until": request.keep_until,
    }


@router.post("/runs/patrol-batches/{patrol_batch_id}/retry")
async def retry_patrol_batch(
    patrol_batch_id: str,
    request: WorkflowRetryRequest = WorkflowRetryRequest(),
    user: CurrentUser | None = Depends(get_optional_user),
):
    runs = list_workflow_runs(
        limit=1000,
        template_name="patrol-change-event",
        source="device-management",
        patrol_batch_id=patrol_batch_id,
    )
    _assert_patrol_batch_not_archived(runs)
    retryable = [run for run in runs if run.status in {"failed", "cancelled"}]
    retried: list[dict[str, Any]] = []
    for run in retryable:
        try:
            new_run_id = _retry_existing_run(run, request, user)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        retried.append(
            {
                "retry_of_run_id": run.id,
                "run_id": new_run_id,
                "device_id": (run.context or {}).get("device_id"),
                "device_name": (run.context or {}).get("device_name"),
            }
        )
    return {
        "success": True,
        "patrol_batch_id": patrol_batch_id,
        "retryable_count": len(retryable),
        "retried_count": len(retried),
        "runs": retried,
    }


@router.post("/runs/patrol-batches/{patrol_batch_id}/rerun")
async def rerun_patrol_batch(
    patrol_batch_id: str,
    request: WorkflowRetryRequest = WorkflowRetryRequest(),
    user: CurrentUser | None = Depends(get_optional_user),
):
    runs = list_workflow_runs(
        limit=1000,
        template_name="patrol-change-event",
        source="device-management",
        patrol_batch_id=patrol_batch_id,
    )
    if not runs:
        raise HTTPException(status_code=404, detail="patrol batch not found")
    _assert_patrol_batch_not_archived(runs)
    selected_runs: list[Any] = []
    seen_devices: set[str] = set()
    for run in sorted(runs, key=lambda item: str(item.created_at or ""), reverse=True):
        context = run.context or {}
        device_key = str(context.get("device_id") or context.get("device_name") or run.id)
        if device_key in seen_devices:
            continue
        seen_devices.add(device_key)
        selected_runs.append(run)
    new_batch_id = f"patrol-batch-{uuid.uuid4()}"
    rerun_items: list[dict[str, Any]] = []
    for run in selected_runs:
        try:
            new_run_id = _rerun_existing_run(
                run,
                request,
                user,
                new_batch_id=new_batch_id,
                old_batch_id=patrol_batch_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        rerun_items.append(
            {
                "rerun_of_run_id": run.id,
                "run_id": new_run_id,
                "device_id": (run.context or {}).get("device_id"),
                "device_name": (run.context or {}).get("device_name"),
            }
        )
    return {
        "success": True,
        "patrol_batch_id": patrol_batch_id,
        "new_patrol_batch_id": new_batch_id,
        "source_run_count": len(runs),
        "deduplicated_device_count": len(selected_runs),
        "rerun_count": len(rerun_items),
        "runs": rerun_items,
    }


@router.get("/runs/patrol-batches/{patrol_batch_id}/summary")
async def get_patrol_batch_summary(patrol_batch_id: str):
    runs = list_workflow_runs(
        limit=1000,
        template_name="patrol-change-event",
        source="device-management",
        patrol_batch_id=patrol_batch_id,
    )
    if not runs:
        raise HTTPException(status_code=404, detail="patrol batch not found")
    return _build_patrol_batch_summary(patrol_batch_id, runs)


@router.get("/runs/patrol-batches/{patrol_batch_id}/export.csv")
async def export_patrol_batch_csv(patrol_batch_id: str):
    runs = list_workflow_runs(
        limit=1000,
        template_name="patrol-change-event",
        source="device-management",
        patrol_batch_id=patrol_batch_id,
    )
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "patrol_batch_id",
            "run_id",
            "retry_of_run_id",
            "device_id",
            "device_name",
            "status",
            "current_step_index",
            "ticket_id",
            "created_at",
            "completed_at",
            "error_message",
        ],
    )
    writer.writeheader()
    for run in runs:
        item = _run_summary_dict(run)
        writer.writerow(
            {
                "patrol_batch_id": item.get("patrol_batch_id") or "",
                "run_id": item.get("run_id") or "",
                "retry_of_run_id": item.get("retry_of_run_id") or "",
                "device_id": item.get("device_id") or "",
                "device_name": item.get("device_name") or "",
                "status": item.get("status") or "",
                "current_step_index": item.get("current_step_index") or 0,
                "ticket_id": item.get("ticket_id") or "",
                "created_at": item.get("created_at") or "",
                "completed_at": item.get("completed_at") or "",
                "error_message": item.get("error_message") or "",
            }
        )
    return Response(
        content=output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{patrol_batch_id}.csv"'},
    )


@router.get("/runs/patrol-batches/{patrol_batch_id}/report.md")
async def export_patrol_batch_report(patrol_batch_id: str):
    runs = list_workflow_runs(
        limit=1000,
        template_name="patrol-change-event",
        source="device-management",
        patrol_batch_id=patrol_batch_id,
    )
    if not runs:
        raise HTTPException(status_code=404, detail="patrol batch not found")
    markdown = _build_patrol_batch_report(patrol_batch_id, runs)
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{patrol_batch_id}-report.md"'},
    )


@router.post("/runs/test")
async def test_run_workflow(
    request: WorkflowTestRunRequest,
    user: CurrentUser | None = Depends(get_optional_user),
):
    tpl = get_template(request.template_name)
    if not tpl:
        raise HTTPException(status_code=404, detail=f"未知 Workflow: {request.template_name}")
    ctx = dict(request.context)
    missing = missing_test_context(tpl, ctx)
    if missing:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Workflow 试跑上下文缺少必填字段",
                "missing_context": missing,
            },
        )
    user_id = request.user_id or (user.user_id if user else None)
    try:
        run_id = WorkflowEngine.start(
            request.template_name,
            ctx,
            source="test",
            user_id=user_id,
            thread_id=request.thread_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    run = get_workflow_run(run_id)
    return {
        "success": True,
        "run_id": run_id,
        "message": f"已启动试跑 Workflow `{request.template_name}`",
        "query_endpoint": f"/api/v1/workflows/{run_id}",
        "run": _run_to_response(run) if run else None,
    }


@router.post("/{run_id}/retry")
async def retry_workflow_run(
    run_id: str,
    request: WorkflowRetryRequest = WorkflowRetryRequest(),
    user: CurrentUser | None = Depends(get_optional_user),
):
    run = get_workflow_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Workflow 不存在")
    if run.status not in {"failed", "cancelled"}:
        raise HTTPException(
            status_code=400, detail="only failed or cancelled workflow runs can be retried"
        )
    try:
        new_run_id = _retry_existing_run(run, request, user)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    new_run = get_workflow_run(new_run_id)
    return {
        "success": True,
        "run_id": new_run_id,
        "retry_of_run_id": run.id,
        "message": f"已重新启动 Workflow `{run.template_name}`",
        "query_endpoint": f"/api/v1/workflows/{new_run_id}",
        "run": _run_to_response(new_run) if new_run else None,
    }


@router.get("/{run_id}/timeline")
async def get_workflow_timeline_api(run_id: str):
    run = get_workflow_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Workflow 不存在")
    events = get_workflow_timeline(run_id)
    ctx = run.context or {}
    trace_id = ctx.get("langfuse_trace_id")
    return {
        "run_id": run_id,
        "status": run.status,
        "events": events,
        "langfuse_trace_id": trace_id,
        "langfuse_url": get_trace_url(str(trace_id)) if trace_id else None,
    }


@router.get("/{run_id}/events/stream")
async def stream_workflow_run_events(run_id: str):
    run = get_workflow_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Workflow 不存在")

    async def event_generator():
        async for chunk in stream_workflow_events(run_id):
            yield chunk

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{run_id}", response_model=WorkflowRunResponse)
async def get_workflow(run_id: str):
    run = get_workflow_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Workflow 不存在")
    return _run_to_response(run)
