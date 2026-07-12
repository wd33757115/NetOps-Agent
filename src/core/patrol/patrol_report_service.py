# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Deterministic patrol batch report analysis and rendering."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


def _format_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _markdown_cell(value: Any) -> str:
    text = _format_value(value) or "-"
    return text.replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _display_entity_key(value: Any) -> str:
    if not isinstance(value, str):
        return _format_value(value) or "__singleton__"
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return value or "__singleton__"
    if isinstance(decoded, list):
        return "/".join(str(item) for item in decoded) or "__singleton__"
    return _format_value(decoded) or "__singleton__"


class PatrolReportService:
    """Build report conclusions from an already materialized batch summary."""

    @staticmethod
    def conclusion(summary: dict[str, Any]) -> dict[str, str]:
        failed_runs = sum(
            count
            for status, count in summary["status_counts"].items()
            if status != "completed"
        )
        quality = summary.get("quality") or {}
        no_event = summary.get("no_event_summary") or {}
        missing_rules = int(no_event.get("missing_rule_count") or 0)
        suspected = int(summary.get("suspected_format_change_count") or 0)
        if failed_runs:
            return {
                "status": "执行失败",
                "trust": "低",
                "reason": "存在未完成的巡检工作流",
            }
        if summary.get("event_count"):
            return {
                "status": "发现异常",
                "trust": "高",
                "reason": "已生成可追溯网络事件",
            }
        if quality.get("status") == "degraded" or missing_rules or suspected:
            reasons = []
            if quality.get("status") == "degraded":
                reasons.append("结构化数据覆盖不完整")
            if missing_rules:
                reasons.append(f"{missing_rules} 条变化因无活动事件规则而无法判定")
            if suspected:
                reasons.append(f"{suspected} 条变化疑似格式噪声")
            return {
                "status": "部分可信，需复核",
                "trust": "中",
                "reason": "；".join(reasons),
            }
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

    @classmethod
    def render_markdown(
        cls,
        patrol_batch_id: str,
        summary: dict[str, Any],
        *,
        generated_at: datetime | None = None,
    ) -> str:
        conclusion = cls.conclusion(summary)
        quality = summary["quality"]
        generated_at = generated_at or datetime.now(timezone.utc)
        lines = [
            "# 网络设备巡检报告",
            "",
            f"- **批次 ID**：`{patrol_batch_id}`",
            f"- **生成时间**：{generated_at.isoformat()}",
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


patrol_report_conclusion = PatrolReportService.conclusion
