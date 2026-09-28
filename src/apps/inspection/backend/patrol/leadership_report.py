# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Leadership-facing patrol report built only from traceable patrol facts."""

from __future__ import annotations

import io
import json
import os
import re
import zipfile
from collections import Counter
from datetime import datetime, timezone
from difflib import SequenceMatcher
from html import escape as html_escape
from typing import Any
from zoneinfo import ZoneInfo

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from src.core.patrol.observation_report import REPORT_VIEWS, build_observation_report_tables
from src.core.patrol.unstructured_diff import summarize_unstructured_changes
from src.skills.official_document.docx_format import (
    add_styled_paragraph,
    apply_fixed_line_spacing,
    setup_a4_margins,
)

SEVERITY_RANK = {
    "critical": 5,
    "major": 4,
    "warning": 3,
    "minor": 2,
    "info": 1,
    "unknown": 0,
}
SEVERITY_LABEL = {
    "critical": "严重",
    "major": "重大",
    "warning": "告警",
    "minor": "提示",
    "info": "信息",
    "unknown": "未知",
}
EVENT_TYPE_LABEL = {
    "NumericHealthAbnormal": "数值健康异常",
    "InterfaceDown": "接口运行状态中断",
    "InterfaceUp": "接口运行状态恢复",
    "interface_link_down": "接口链路中断",
    "interface_link_up": "接口链路恢复",
    "interface_oper_down": "接口协议状态中断",
    "interface_oper_up": "接口协议状态恢复",
    "OpticalRxPowerLow": "接收光功率偏低",
    "OpticalRxPowerCritical": "接收光功率严重偏低",
    "OpticalTxPowerLow": "发送光功率偏低",
    "OpticalTxPowerCritical": "发送光功率严重偏低",
    "OpticalRecovered": "光功率恢复",
    "rx_power_low": "接收光功率偏低",
    "rx_power_low_recovery": "接收光功率恢复",
    "tx_power_low": "发送光功率偏低",
    "tx_power_low_recovery": "发送光功率恢复",
    "transceiver_rx_power_low": "接收光功率低于设备告警阈值",
    "transceiver_rx_power_low_recovery": "接收光功率恢复",
    "transceiver_rx_power_high": "接收光功率高于设备告警阈值",
    "transceiver_rx_power_high_recovery": "接收光功率恢复",
    "transceiver_tx_power_low": "发送光功率低于设备告警阈值",
    "transceiver_tx_power_low_recovery": "发送光功率恢复",
    "transceiver_tx_power_high": "发送光功率高于设备告警阈值",
    "transceiver_tx_power_high_recovery": "发送光功率恢复",
    "TransceiverAbsent": "光模块缺失",
}
DOMAIN_STATUS_RANK = {
    "异常": 7,
    "数据不完整": 6,
    "规则缺失": 5,
    "无法判断": 4,
    "需复核": 3,
    "有变化，未触发事件": 2,
    "本次覆盖范围内未发现当前异常": 1,
    "本次覆盖范围内未发现异常": 1,
    "未发现变化（健康未判定）": 0,
    "不适用": -1,
}
REPORT_TIMEZONE = os.getenv("NETOPS_REPORT_TIMEZONE", "Asia/Shanghai")


def _iso(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _report_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is not None:
        try:
            return parsed.astimezone(ZoneInfo(REPORT_TIMEZONE))
        except (KeyError, ValueError):
            return parsed
    return parsed


def _format_cn_datetime(value: Any) -> str:
    parsed = _report_datetime(value)
    if parsed is None:
        return str(value or "-")
    return f"{parsed.year}年{parsed.month}月{parsed.day}日 {parsed:%H:%M}"


def _format_cn_date(value: Any) -> str:
    parsed = _report_datetime(value)
    if parsed is None:
        return str(value or "-")[:10]
    return f"{parsed.year}年{parsed.month}月{parsed.day}日"


def _display_entity_key(value: Any) -> str:
    def compact(item: Any) -> str:
        text = str(item)
        replacements = (
            ("Twenty-FiveGigE", "25GE"),
            ("TwentyFiveGigE", "25GE"),
            ("HundredGigE", "100GE"),
            ("FortyGigE", "40GE"),
            ("Ten-GigabitEthernet", "10GE"),
            ("TenGigabitEthernet", "10GE"),
            ("GigabitEthernet", "GE"),
        )
        for source, target in replacements:
            if text.startswith(source):
                return target + text[len(source) :]
        return text

    if not isinstance(value, str):
        return compact(value or "-")
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return compact(value or "-")
    if isinstance(decoded, list):
        return "/".join(compact(item) for item in decoded) or "-"
    return compact(decoded or "-")


def _stable_risk_dimensions(item: dict[str, Any]) -> dict[str, Any]:
    return {
        str(key): value
        for key, value in dict(item.get("dimensions") or {}).items()
        if not str(key).startswith("_")
    }


def _display_risk_entity(item: dict[str, Any]) -> str:
    label = _display_entity_key(item.get("entity_key"))
    dimensions = _stable_risk_dimensions(item)
    lane = dimensions.get("value_index") or dimensions.get("channel")
    return f"{label} / 通道 {lane}" if lane not in (None, "") else label


def _severity(value: Any) -> str:
    normalized = str(value or "unknown").lower()
    return normalized if normalized in SEVERITY_RANK else "unknown"


def _event_type_label(value: Any) -> str:
    event_type = str(value or "未知风险")
    return EVENT_TYPE_LABEL.get(event_type, event_type)


def _canonical_interface(value: Any) -> str:
    text = _display_entity_key(value).strip().casefold().replace(" ", "")
    aliases = (
        ("25ge", "wge"),
        ("100ge", "hge"),
        ("40ge", "fge"),
        ("10ge", "xge"),
        ("twenty-five-gigabitethernet", "wge"),
        ("twentyfivegigabitethernet", "wge"),
        ("twenty-fivegige", "wge"),
        ("twentyfivegige", "wge"),
        ("hundredgigabitethernet", "hge"),
        ("hundredgige", "hge"),
        ("fortygigabitethernet", "fge"),
        ("fortygige", "fge"),
        ("tengigabitethernet", "xge"),
        ("tengige", "xge"),
        ("gigabitethernet", "ge"),
    )
    for source, target in aliases:
        if text.startswith(source):
            return target + text[len(source) :]
    return re.sub(r"[^a-z0-9/.-]+", "", text)


def _partition_correlated_risks(
    summary: dict[str, Any], risks: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_episode = {
        str(risk.get("risk_episode_id") or ""): risk
        for risk in risks
        if risk.get("risk_episode_id")
    }
    candidates: dict[str, list[dict[str, Any]]] = {}
    for association in summary.get("risk_episode_associations") or []:
        if str(association.get("status") or "") != "approved":
            continue
        if association.get("stale") is True or not association.get("reviewed_by"):
            continue
        if str(association.get("source_type") or "manual") == "approved_policy":
            policy = dict(association.get("policy_ref_json") or {})
            if not {
                "policy_id",
                "policy_version",
                "policy_digest",
                "approval_digest",
            }.issubset({key for key, value in policy.items() if value}):
                continue
        primary_episode = str(association.get("primary_risk_episode_id") or "")
        related_episode = str(association.get("related_risk_episode_id") or "")
        if (
            not primary_episode
            or not related_episode
            or primary_episode == related_episode
            or primary_episode not in by_episode
            or related_episode not in by_episode
        ):
            continue
        candidates.setdefault(related_episode, []).append(association)

    primary_episodes = {
        str(items[0].get("primary_risk_episode_id") or "")
        for items in candidates.values()
        if len(items) == 1
    }
    governed = {
        related_episode: items[0]
        for related_episode, items in candidates.items()
        if (
            len(items) == 1
            and related_episode not in primary_episodes
            and str(items[0].get("primary_risk_episode_id") or "") not in candidates
        )
    }
    primary: list[dict[str, Any]] = []
    correlated: list[dict[str, Any]] = []
    for risk in risks:
        association = governed.get(str(risk.get("risk_episode_id") or ""))
        if association is not None:
            correlated.append(
                {
                    **risk,
                    "correlation": {
                        "status": "approved_risk_episode_association",
                        "association_id": association.get("id"),
                        "primary_risk_episode_id": association.get("primary_risk_episode_id"),
                        "relation_type": association.get("relation_type"),
                        "reviewed_by": association.get("reviewed_by"),
                        "evidence_refs": list(association.get("evidence_refs_json") or []),
                        "counted_as_independent_risk": False,
                    },
                }
            )
        else:
            primary.append(risk)
    return primary, correlated


def _compact_entities(values: list[str]) -> str:
    unique = sorted({value for value in values if value})
    if not unique:
        return "-"
    if len(unique) == 1:
        return unique[0]
    prefixes = [value.rsplit("/", 1) for value in unique]
    if all(len(item) == 2 for item in prefixes) and len({item[0] for item in prefixes}) == 1:
        return prefixes[0][0] + "/" + "、".join(item[1] for item in prefixes)
    shown = unique[:5]
    suffix = f"等 {len(unique)} 个对象" if len(unique) > len(shown) else ""
    return "、".join(shown) + suffix


def _value_summary(items: list[dict[str, Any]]) -> str:
    values = [
        item.get("current_value") for item in items if item.get("current_value") not in (None, "")
    ]
    unit = str(next((item.get("unit") for item in items if item.get("unit")), ""))
    unit = "dBm" if unit.casefold() == "dbm" else unit
    numbers: list[float] = []
    for value in values:
        try:
            numbers.append(float(value))
        except (TypeError, ValueError):
            numbers = []
            break
    if numbers:
        if min(numbers) == max(numbers):
            return f"{min(numbers):g}{unit}"
        return f"{min(numbers):g} 至 {max(numbers):g}{unit}"
    return "、".join(dict.fromkeys(str(value) for value in values)) or "详见事件证据"


def _criterion_summary(item: dict[str, Any]) -> str:
    condition = dict(item.get("condition") or {})
    kind = str(condition.get("kind") or "")
    unit = "dBm" if str(item.get("unit") or "").casefold() == "dbm" else str(item.get("unit") or "")
    if kind == "threshold_lte":
        return f"当前值 ≤ {condition.get('lte')}{unit}"
    if kind == "threshold":
        return f"当前值 ≥ {condition.get('gte')}{unit}"
    if kind in {"threshold_field_lte", "threshold_field_gte"}:
        operator = "≤" if kind == "threshold_field_lte" else "≥"
        reference_value = item.get("reference_value")
        if reference_value not in (None, ""):
            return f"当前值 {operator} 设备告警阈值 {reference_value}{unit}"
        reference_field = condition.get("reference_field") or item.get("reference_field")
        return f"当前值 {operator} 设备阈值字段 {reference_field or '-'}"
    if kind in {"threshold_field_low_recovery", "threshold_field_high_recovery"}:
        direction = "低" if kind == "threshold_field_low_recovery" else "高"
        reference_value = item.get("reference_value")
        hysteresis = condition.get("hysteresis")
        if reference_value not in (None, ""):
            return f"恢复至设备{direction}告警阈值 {reference_value}{unit}之外" + (
                f"（回差 {hysteresis}）" if hysteresis not in (None, "") else ""
            )
        return f"恢复至设备{direction}告警阈值之外"
    if kind == "transition":
        previous = str(condition.get("from_group") or "-").upper()
        current = str(condition.get("to_group") or "-").upper()
        return f"{previous} → {current}"
    return "活动规则命中"


def _criteria_summary(items: list[dict[str, Any]]) -> str:
    if items and all(
        str((item.get("condition") or {}).get("kind") or "")
        in {"threshold_field_lte", "threshold_field_gte"}
        for item in items
    ):
        operators = {
            "≤" if str((item.get("condition") or {}).get("kind")) == "threshold_field_lte" else "≥"
            for item in items
        }
        references = []
        for item in items:
            try:
                references.append(float(item.get("reference_value")))
            except (TypeError, ValueError):
                references = []
                break
        if len(operators) == 1 and references:
            unit = str(next((item.get("unit") for item in items if item.get("unit")), ""))
            unit = "dBm" if unit.casefold() == "dbm" else unit
            reference_summary = (
                f"{min(references):g}{unit}"
                if min(references) == max(references)
                else f"{min(references):g} 至 {max(references):g}{unit}"
            )
            return f"当前值 {next(iter(operators))} 设备告警阈值 {reference_summary}"
    criteria = list(dict.fromkeys(_criterion_summary(item) for item in items))
    if len(criteria) <= 2:
        return "；".join(criteria)
    return "；".join(criteria[:2]) + f"等 {len(criteria)} 项判据"


def _aggregate_risk_groups(risks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for risk in risks:
        key = (
            _severity(risk.get("severity")),
            str(risk.get("event_type") or "unknown"),
            str(risk.get("device_name") or risk.get("device_id") or "-"),
        )
        grouped.setdefault(key, []).append(risk)
    rows = []
    for (severity, event_type, device_name), items in grouped.items():
        rows.append(
            {
                "severity": severity,
                "event_type": event_type,
                "risk_name": _event_type_label(event_type),
                "device_name": device_name,
                "count": len(items),
                "objects": _compact_entities([_display_risk_entity(item) for item in items]),
                "current_values": _value_summary(items),
                "criterion": _criteria_summary(items),
            }
        )
    return sorted(
        rows,
        key=lambda item: (
            -SEVERITY_RANK[item["severity"]],
            item["risk_name"],
            item["device_name"],
        ),
    )


def _risk_key(item: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        str(item.get("event_type") or "unknown"),
        str(item.get("device_id") or ""),
        str(item.get("entity_type") or ""),
        str(item.get("entity_key") or ""),
        json.dumps(
            _stable_risk_dimensions(item),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ),
    )


def _build_risks(summary: dict[str, Any]) -> list[dict[str, Any]]:
    risks: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for finding in summary.get("current_findings") or []:
        row = {
            "source": "current_state",
            "event_type": finding.get("event_type") or "CurrentStateAbnormal",
            "severity": _severity(finding.get("severity")),
            "device_id": finding.get("device_id"),
            "device_name": finding.get("device_name") or finding.get("device_id"),
            "domain": finding.get("domain"),
            "entity_type": finding.get("entity_type"),
            "entity_key": finding.get("entity_key"),
            "field": finding.get("field"),
            "current_value": finding.get("value"),
            "unit": finding.get("unit"),
            "rule_id": finding.get("rule_id"),
            "rule_version": finding.get("rule_version"),
            "observed_at": finding.get("observed_at"),
            "evidence_id": finding.get("observation_id"),
            "condition": dict(finding.get("condition") or {}),
            "dimensions": dict(finding.get("dimensions") or {}),
            "reference_field": finding.get("reference_field"),
            "reference_value": finding.get("reference_value"),
            "risk_case_id": finding.get("risk_case_id"),
            "risk_episode_id": finding.get("risk_episode_id") or finding.get("episode_id"),
            "evidence_chain": {
                "analysis_attempt_id": (summary.get("analysis") or {}).get("attempt_id"),
                "analysis_revision": (summary.get("analysis") or {}).get("revision"),
                "workflow_run_id": summary.get("workflow_run_id"),
                "patrol_run_id": finding.get("run_id"),
                "current_snapshot_id": finding.get("snapshot_id"),
                "raw_evidence_id": finding.get("evidence_id"),
                "current_observation_id": finding.get("observation_id"),
                "parser_template_id": finding.get("parser_template_id"),
                "parser_version": finding.get("parser_version"),
                "observation_contract_id": finding.get("observation_contract_id"),
                "observation_contract_version": finding.get("contract_version"),
                "observation_contract_digest": finding.get("observation_contract_digest"),
                "event_rule_id": finding.get("rule_id"),
                "event_rule_version": finding.get("rule_version"),
                "rule_bundle_digest": finding.get("rule_bundle_digest"),
            },
        }
        risks[_risk_key(row)] = row
    for event in summary.get("events") or []:
        if str(event.get("event_class") or "fault") != "fault":
            continue
        if str(event.get("status_after_occurrence") or "").casefold() in {
            "resolved",
            "closed",
            "cancelled",
        }:
            continue
        row = {
            "source": "change_event",
            "event_type": event.get("event_type") or "NetworkEvent",
            "severity": _severity(event.get("severity")),
            "device_id": event.get("device_id"),
            "device_name": event.get("device_name") or event.get("device_id"),
            "domain": event.get("domain"),
            "entity_type": event.get("entity_type"),
            "entity_key": event.get("entity_key"),
            "field": event.get("field"),
            "current_value": event.get("current_value"),
            "unit": event.get("unit"),
            "rule_id": event.get("rule_id"),
            "rule_version": event.get("rule_version"),
            "observed_at": event.get("occurred_at"),
            "evidence_id": event.get("id"),
            "condition": dict(event.get("condition") or {}),
            "dimensions": dict(event.get("dimensions") or {}),
            "reference_field": event.get("reference_field"),
            "reference_value": event.get("reference_value"),
            "risk_case_id": event.get("risk_case_id"),
            "risk_episode_id": event.get("risk_episode_id") or event.get("episode_id"),
            "evidence_chain": dict(event.get("evidence_chain") or {}),
        }
        key = _risk_key(row)
        current = risks.get(key)
        if current is None or SEVERITY_RANK[row["severity"]] > SEVERITY_RANK[current["severity"]]:
            risks[key] = row
        elif current is not None:
            current["evidence_chain"] = {
                **dict(row.get("evidence_chain") or {}),
                **dict(current.get("evidence_chain") or {}),
            }
    return sorted(
        risks.values(),
        key=lambda item: (
            -SEVERITY_RANK[_severity(item.get("severity"))],
            str(item.get("device_name") or ""),
            str(item.get("event_type") or ""),
        ),
    )


def _build_longitudinal_trend(
    summary: dict[str, Any],
    risks: list[dict[str, Any]],
    correlated_evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    previous = dict(summary.get("previous_period") or {})
    if not previous:
        return {"available": False, "period_count": len(summary.get("periods") or [])}
    previous_evidence = [
        *list(previous.get("risks") or []),
        *list(previous.get("correlated_evidence") or []),
    ]
    historical_fault_events = [
        {**event, "status_after_occurrence": "open"}
        for event in previous.get("events") or []
        if str(event.get("event_class") or "fault") == "fault"
    ]
    historical_fault_risks = _build_risks({"events": historical_fault_events})
    previous_evidence = list(
        {_risk_key(item): item for item in [*previous_evidence, *historical_fault_risks]}.values()
    )
    current_evidence = [*risks, *correlated_evidence]
    previous_by_key = {_risk_key(item): item for item in previous_evidence}
    current_by_key = {_risk_key(item): item for item in current_evidence}
    persistent = [
        current_by_key[key] for key in sorted(current_by_key.keys() & previous_by_key.keys())
    ]
    new = [current_by_key[key] for key in sorted(current_by_key.keys() - previous_by_key.keys())]
    resolved = [
        previous_by_key[key] for key in sorted(previous_by_key.keys() - current_by_key.keys())
    ]
    recovery_by_entity = {}
    for event in summary.get("events") or []:
        if str(event.get("event_class") or "fault") != "recovery":
            continue
        recovery_by_entity[
            (
                str(event.get("device_id") or ""),
                _canonical_interface(event.get("entity_key")),
                str(event.get("field") or "").casefold(),
            )
        ] = event
    resolved_with_recovery = []
    for item in resolved:
        recovery = recovery_by_entity.get(
            (
                str(item.get("device_id") or ""),
                _canonical_interface(item.get("entity_key")),
                str(item.get("field") or "").casefold(),
            )
        )
        if recovery is None:
            resolved_with_recovery.append(item)
            continue
        resolved_with_recovery.append(
            {
                **item,
                "source": "recovery_event",
                "event_type": recovery.get("event_type"),
                "severity": recovery.get("severity") or "info",
                "current_value": recovery.get("current_value"),
                "unit": recovery.get("unit") or item.get("unit"),
                "condition": dict(recovery.get("condition") or {}),
                "reference_field": recovery.get("reference_field"),
                "reference_value": recovery.get("reference_value"),
                "evidence_chain": dict(recovery.get("evidence_chain") or {}),
            }
        )
    periods_by_time: dict[str, dict[str, Any]] = {}
    for item in [*(previous.get("periods") or []), *(summary.get("periods") or [])]:
        observed_at = str(item.get("observed_at") or "")
        if observed_at:
            periods_by_time[observed_at] = dict(item)
    periods = [periods_by_time[key] for key in sorted(periods_by_time)]

    def comparison(period_rows: list[dict[str, Any]], source: dict[str, Any]) -> dict[str, Any]:
        by_role = {str(item.get("role") or ""): item for item in period_rows}
        return {
            "from": (by_role.get("baseline") or {}).get("observed_at"),
            "to": (by_role.get("current") or {}).get("observed_at"),
            "change_count": int(source.get("change_count") or 0),
            "event_count": int(source.get("event_count") or 0),
            "command_count": int((source.get("metrics") or {}).get("command_count") or 0),
            "parser_ready_rate": float((source.get("metrics") or {}).get("parser_ready_rate") or 0),
            "event_summary": dict(source.get("event_summary") or {}),
            "analysis": dict(source.get("analysis") or {}),
        }

    return {
        "available": True,
        "continuity_evidence": previous.get("continuity_evidence"),
        "period_count": len(periods),
        "periods": periods,
        "comparisons": [
            comparison(list(previous.get("periods") or []), previous),
            comparison(
                list(summary.get("periods") or []),
                {
                    "change_count": summary.get("change_count"),
                    "event_count": summary.get("event_count"),
                    "event_summary": {
                        "fact_count": len(summary.get("events") or []),
                    },
                    "analysis": summary.get("analysis"),
                    "metrics": {
                        "command_count": (summary.get("quality") or {}).get("command_count"),
                        "parser_ready_rate": (summary.get("quality") or {}).get(
                            "parser_ready_rate"
                        ),
                    },
                },
            ),
        ],
        "new_evidence_count": len(new),
        "persistent_evidence_count": len(persistent),
        "resolved_evidence_count": len(resolved_with_recovery),
        "new_evidence_groups": _aggregate_risk_groups(new),
        "persistent_evidence_groups": _aggregate_risk_groups(persistent),
        "resolved_evidence_groups": _aggregate_risk_groups(resolved_with_recovery),
    }


def _aggregate_domains(summary: dict[str, Any]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for run in summary.get("runs") or []:
        for check in run.get("domain_checks") or []:
            domain = str(check.get("domain") or "other")
            row = grouped.setdefault(
                domain,
                {
                    "domain": domain,
                    "label": check.get("label") or domain,
                    "device_count": 0,
                    "observation_count": 0,
                    "evaluated_observation_count": 0,
                    "threshold_unknown_observation_count": 0,
                    "partial_threshold_observation_count": 0,
                    "current_finding_count": 0,
                    "unresolved_fault_event_count": 0,
                    "current_risk_count": 0,
                    "change_count": 0,
                    "event_count": 0,
                    "statuses": Counter(),
                    "reasons": Counter(),
                },
            )
            row["device_count"] += 1
            row["observation_count"] += int(check.get("observation_count") or 0)
            row["evaluated_observation_count"] += int(check.get("evaluated_observation_count") or 0)
            row["threshold_unknown_observation_count"] += int(
                check.get("threshold_unknown_observation_count") or 0
            )
            row["partial_threshold_observation_count"] += int(
                check.get("partial_threshold_observation_count") or 0
            )
            row["current_finding_count"] += int(check.get("current_finding_count") or 0)
            row["unresolved_fault_event_count"] += int(
                check.get("unresolved_fault_event_count") or 0
            )
            row["current_risk_count"] += int(
                check.get("current_risk_count")
                if check.get("current_risk_count") is not None
                else check.get("current_finding_count") or 0
            )
            row["change_count"] += int(check.get("change_count") or 0)
            row["event_count"] += int(check.get("event_count") or 0)
            row["statuses"].update([str(check.get("status") or "无法判断")])
            row["reasons"].update([str(check.get("reason") or "")])
    result = []
    for row in grouped.values():
        statuses: Counter[str] = row.pop("statuses")
        reasons: Counter[str] = row.pop("reasons")
        status = max(
            statuses,
            key=lambda item: (DOMAIN_STATUS_RANK.get(item, 0), statuses[item]),
        )
        result.append(
            {
                **row,
                "status": status,
                "status_counts": dict(statuses),
                "reason": reasons.most_common(1)[0][0] if reasons else "",
            }
        )
    return sorted(result, key=lambda item: item["label"])


def _assessment(
    summary: dict[str, Any],
    risks: list[dict[str, Any]],
    domains: list[dict[str, Any]],
    correlated_evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    scope = summary.get("scope") or {}
    quality = summary.get("quality") or {}
    status_counts = summary.get("status_counts") or {}
    target_count = int(scope.get("target_count") or summary.get("device_count") or 0)
    completed_count = int(scope.get("completed_count") or status_counts.get("completed") or 0)
    active_count = int(scope.get("active_count") or 0)
    failed_count = int(scope.get("failed_count") or status_counts.get("failed") or 0)
    skipped_count = int(scope.get("skipped_count") or 0)
    cancelled_count = int(scope.get("cancelled_count") or status_counts.get("cancelled") or 0)
    rule_coverage = summary.get("current_state_rule_coverage") or {}
    incomplete_domains = [
        row for row in domains if row["status"] in {"数据不完整", "规则缺失", "无法判断", "需复核"}
    ]
    full_execution = bool(target_count) and completed_count == target_count
    facts_complete = quality.get("status") == "ready"
    rules_available = bool(rule_coverage.get("evaluated_observation_count"))
    if summary.get("report_purpose") == "parser_validation":
        return {
            "code": "parser_validation_only",
            "status": "已完成离线样本解析验证，不形成网络健康结论",
            "trust": "不适用",
            "basis": "该任务用于发现和修复结构化解析模板及数据契约缺口，未执行双周期状态比较",
            "conclusion_allowed": False,
        }
    if summary.get("report_purpose") == "rule_asset_replay":
        return {
            "code": "rule_replay_only",
            "status": "已完成双周期规则回放，不形成网络健康结论",
            "trust": "不适用",
            "basis": "该任务用于验证候选变化与事件规则，不是正式设备巡检批次",
            "conclusion_allowed": False,
        }
    if active_count:
        return {
            "code": "in_progress",
            "status": "巡检进行中，尚未形成最终结论",
            "trust": "不足",
            "basis": f"仍有 {active_count} 台设备处于运行或等待状态",
            "conclusion_allowed": False,
        }
    if not target_count or not summary.get("run_count"):
        return {
            "code": "insufficient_data",
            "status": "未形成可用巡检结论",
            "trust": "不足",
            "basis": "未发现已完成的设备巡检运行",
            "conclusion_allowed": False,
        }
    if risks:
        top = _severity(risks[0].get("severity"))
        partial = not full_execution or not facts_complete or bool(incomplete_domains)
        return {
            "code": "abnormal_partial" if partial else "abnormal",
            "status": "发现重大网络风险" if top in {"critical", "major"} else "发现网络异常",
            "trust": "中" if partial else "高",
            "basis": (
                f"识别 {len(risks)} 项当前风险（由当前状态规则命中或尚未恢复的"
                "周期变化事件形成）；"
                f"最高等级为{SEVERITY_LABEL[top]}"
                + (
                    f"；另有 {len(correlated_evidence)} 项症状已通过独立复核归入主问题，不重复计数"
                    if correlated_evidence
                    else ""
                )
                + ("，但本次覆盖或数据质量不完整" if partial else "")
            ),
            "conclusion_allowed": True,
        }
    if correlated_evidence:
        return {
            "code": "correlated_evidence_review",
            "status": "发现需复核的关联异常证据",
            "trust": "中",
            "basis": (
                f"发现 {len(correlated_evidence)} 项症状已通过独立复核归入主问题，"
                "技术事实仍完整保留；当前缺少可独立展示的主问题，请复核冻结关联证据"
            ),
            "conclusion_allowed": False,
        }
    gaps = []
    if not full_execution:
        gaps.append(
            f"目标 {target_count} 台，仅 {completed_count} 台完成，"
            f"失败/跳过/取消 {failed_count + skipped_count + cancelled_count} 台"
        )
    if not facts_complete:
        gaps.append("结构化解析或命令输出质量未达到完整门禁")
    if not rules_available:
        gaps.append("当前状态缺少可适用的活动事件规则")
    if incomplete_domains:
        gaps.append(f"{len(incomplete_domains)} 个检查域无法完整判定")
    if gaps:
        return {
            "code": "partial",
            "status": "本次未发现已确认异常，但结论不完整",
            "trust": "中",
            "basis": "；".join(gaps),
            "conclusion_allowed": False,
        }
    if summary.get("change_count"):
        return {
            "code": "normal_with_changes",
            "status": "本次覆盖范围内未发现当前异常，检测到状态变化",
            "trust": "高",
            "basis": (
                f"当前状态规则评估未命中故障条件；检测到 "
                f"{summary.get('change_count')} 条变化，需按变更流程留痕复核"
            ),
            "conclusion_allowed": True,
        }
    return {
        "code": "normal_in_scope",
        "status": "本次覆盖范围内未发现异常",
        "trust": "高",
        "basis": "目标设备执行完整，结构化数据与当前状态规则判定均通过",
        "conclusion_allowed": True,
    }


def _recommendations(
    assessment: dict[str, Any],
    summary: dict[str, Any],
    risks: list[dict[str, Any]],
    correlated_evidence: list[dict[str, Any]],
) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    critical = sum(_severity(item.get("severity")) == "critical" for item in risks)
    major = sum(_severity(item.get("severity")) == "major" for item in risks)
    if critical:
        result.append(
            {
                "priority": "P0",
                "action": f"立即核查并处置 {critical} 项严重风险，确认业务影响和冗余状态",
                "owner": "网络运维负责人",
                "deadline": "立即",
            }
        )
    if major:
        result.append(
            {
                "priority": "P1",
                "action": f"在一个工作日内完成 {major} 项重大风险定位、处置和复测",
                "owner": "网络运维组",
                "deadline": "1 个工作日",
            }
        )
    scope = summary.get("scope") or {}
    incomplete = int(scope.get("target_count") or 0) - int(scope.get("completed_count") or 0)
    if incomplete > 0:
        result.append(
            {
                "priority": "P1",
                "action": f"补巡 {incomplete} 台未完成设备并补齐登录或预检失败原因",
                "owner": "巡检执行人",
                "deadline": "1 个工作日",
            }
        )
    quality = summary.get("quality") or {}
    if quality.get("status") != "ready":
        result.append(
            {
                "priority": "P1",
                "action": "修复缺失或失败的结构化解析模板和数据契约，并对本批次重新分析",
                "owner": "平台与网络运维组",
                "deadline": "2 个工作日",
            }
        )
    coverage = summary.get("current_state_rule_coverage") or {}
    if not coverage.get("evaluated_observation_count"):
        result.append(
            {
                "priority": "P1",
                "action": "通过规则资产控制面补齐并审批当前状态事件规则后重新生成报告",
                "owner": "规则资产审批人",
                "deadline": "2 个工作日",
            }
        )
    if summary.get("change_count"):
        result.append(
            {
                "priority": "P2",
                "action": f"复核 {summary.get('change_count')} 条状态变化与变更记录的一致性",
                "owner": "网络运维组",
                "deadline": "3 个工作日",
            }
        )
    unstructured = dict(summary.get("unstructured_comparison") or {})
    unstructured_changed = int(unstructured.get("changed_output_count") or 0)
    if unstructured_changed:
        categories = "、".join(
            str(item) for item in list((unstructured.get("by_category") or {}).keys())[:4]
        )
        result.append(
            {
                "priority": "P1"
                if int((unstructured.get("by_profile") or {}).get("配置输出") or 0)
                else "P2",
                "action": (
                    f"复核 {unstructured_changed} 项非结构化输出变化"
                    + (f"（主要涉及{categories}）" if categories else "")
                    + "，核对变更单、配置备份或日志上下文"
                ),
                "owner": "网络运维组",
                "deadline": "1 个工作日",
            }
        )
    if correlated_evidence:
        result.append(
            {
                "priority": "P2",
                "action": (
                    f"持续核验 {len(correlated_evidence)} 项已关联症状与主问题的一致性，"
                    "若根因、实体或时间关系变化，应撤销关联并生成新报告版本"
                ),
                "owner": "网络运维组",
                "deadline": "3 个工作日",
            }
        )
    if not result:
        result.append(
            {
                "priority": "P2",
                "action": "按既定周期持续巡检，并保留本批次报告与原始输出作为基线",
                "owner": "网络运维组",
                "deadline": "持续",
            }
        )
    return result


def _display_evidence_value(value: Any, *, limit: int = 180) -> str:
    if value is None:
        return "无"
    if isinstance(value, str):
        rendered = value.strip() or "空字符串"
    elif isinstance(value, (dict, list)):
        rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    else:
        rendered = str(value)
    rendered = re.sub(r"\s+", " ", rendered).strip()
    return rendered if len(rendered) <= limit else f"{rendered[: limit - 3]}..."


def _build_structured_event_evidence(
    summary: dict[str, Any], events: list[dict[str, Any]]
) -> dict[str, Any]:
    """Join structured Change facts to their Event decision for report evidence."""

    events_by_change: dict[str, dict[str, Any]] = {}
    for event in events:
        chain = dict(event.get("evidence_chain") or {})
        change_id = str(event.get("source_change_id") or chain.get("change_id") or "")
        if change_id:
            events_by_change.setdefault(change_id, event)

    items: list[dict[str, Any]] = []
    for change in summary.get("changes") or []:
        dimensions = dict(change.get("dimensions") or {})
        if (
            str(change.get("field") or "") == "raw_text_hash"
            or str(change.get("observation_type") or "") == "raw.command.hash"
            or "unstructured_diff" in dimensions
        ):
            continue
        change_id = str(change.get("id") or change.get("change_id") or "")
        event = events_by_change.get(change_id) or {}
        comparison_evidence = dict(change.get("comparison_evidence") or {})
        baseline_raw = dict(comparison_evidence.get("baseline") or {})
        current_raw = dict(comparison_evidence.get("current") or {})
        baseline_value = change.get("old_value") if "old_value" in change else change.get("old")
        current_value = change.get("new_value") if "new_value" in change else change.get("new")
        event_type = event.get("event_type")
        event_status = (
            event.get("status_after_occurrence")
            or event.get("status")
            or change.get("event_status")
            or "unknown"
        )
        items.append(
            {
                "change_id": change_id or None,
                "device_id": change.get("device_id"),
                "device_name": change.get("device_name") or change.get("device_id"),
                "command": change.get("command"),
                "entity_type": change.get("entity_type"),
                "entity_key": change.get("entity_key"),
                "field": change.get("field"),
                "change_type": change.get("change_type"),
                "baseline": {
                    "snapshot_id": (
                        baseline_raw.get("snapshot_id") or change.get("previous_snapshot_id")
                    ),
                    "observation_id": change.get("previous_observation_id"),
                    "value": baseline_value,
                    "observed_at": baseline_raw.get("observed_at"),
                    "parse_status": baseline_raw.get("parse_status"),
                    "raw_text": baseline_raw.get("raw_text"),
                },
                "current": {
                    "snapshot_id": (
                        current_raw.get("snapshot_id") or change.get("current_snapshot_id")
                    ),
                    "observation_id": change.get("current_observation_id"),
                    "value": current_value,
                    "observed_at": current_raw.get("observed_at"),
                    "parse_status": current_raw.get("parse_status"),
                    "raw_text": current_raw.get("raw_text"),
                },
                "event": {
                    "event_id": event.get("id") or event.get("event_id"),
                    "event_type": event_type,
                    "event_class": event.get("event_class"),
                    "event_label": _event_type_label(event_type) if event_type else "未触发 Event",
                    "severity": event.get("severity"),
                    "status": event_status,
                    "reason": (
                        event.get("decision_reason")
                        or change.get("no_event_reason")
                        or change.get("decision_reason")
                    ),
                },
            }
        )
    items.sort(
        key=lambda item: (
            str(item.get("device_name") or ""),
            str(item.get("command") or ""),
            str(item.get("entity_key") or ""),
            str(item.get("field") or ""),
        )
    )
    event_fact_count = sum(bool((item.get("event") or {}).get("event_id")) for item in items)
    return {
        "schema_version": "structured-event-evidence/v1",
        "structured_change_count": len(items),
        "event_fact_count": event_fact_count,
        "no_event_count": len(items) - event_fact_count,
        "items": items,
    }


def _unstructured_evidence_text(item: dict[str, Any], side: str, *, limit: int = 3) -> str:
    evidence = dict(item.get(f"{side}_evidence") or {})
    snapshot_id = str(evidence.get("snapshot_id") or "")
    rows = list(evidence.get("excerpt") or [])[:limit]
    text = "；".join(_display_evidence_value(row.get("text"), limit=120) for row in rows)
    if evidence.get("excerpt_truncated"):
        text = f"{text}；..." if text else "..."
    if not text:
        text = "无可展示的变化行"
    return f"快照 {snapshot_id or '-'}；{text}"


def _cli_evidence_snapshot_identity(evidence: dict[str, Any]) -> str:
    """Return a stable in-memory identity without exposing a digest in the report."""
    for key in ("snapshot_id", "observation_id"):
        value = evidence.get(key)
        if value not in (None, ""):
            return str(value)
    raw_text = evidence.get("raw_text")
    return str(raw_text) if raw_text is not None else ""


def _cli_evidence_fact_label(item: dict[str, Any], group_label: str) -> str:
    if group_label == "结构化变化":
        event = dict(item.get("event") or {})
        entity_key = str(item.get("entity_key") or "-")
        field = str(item.get("field") or "-")
        fact = f"{entity_key} / {field}"
        if event.get("event_id"):
            label = str(event.get("event_label") or event.get("event_type") or "Event")
            return f"Event {event.get('event_id')}：{label} / {fact}"
        return f"结构化变化：{fact}"

    categories = "、".join(
        str(row.get("label") or row.get("name") or "其他变化")
        for row in item.get("categories") or []
    )
    return f"文本对比：{categories or item.get('profile_label') or '未分类'}"


def _cli_evidence_related_facts_text(evidence: dict[str, Any], *, limit: int = 5) -> str:
    facts = [str(item) for item in evidence.get("related_facts") or []]
    if not facts:
        return ""
    visible = "；".join(facts[:limit])
    if len(facts) > limit:
        return f"{visible}；其余 {len(facts) - limit} 项"
    return visible


def _full_cli_evidence_items(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Group raw CLI evidence by device, command, and baseline/current snapshot pair."""
    result: list[dict[str, Any]] = []
    evidence_by_key: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    groups = (
        (
            "结构化变化",
            list(dict(report.get("structured_event_evidence") or {}).get("items") or []),
        ),
        (
            "非结构化文本对比",
            list(dict(report.get("unstructured_comparison") or {}).get("items") or []),
        ),
    )
    for group_label, items in groups:
        for item in items:
            baseline = dict(item.get("baseline") or item.get("baseline_evidence") or {})
            current = dict(item.get("current") or item.get("current_evidence") or {})
            if baseline.get("raw_text") is None and current.get("raw_text") is None:
                continue
            device_id = str(
                item.get("device_id") or baseline.get("device_id") or current.get("device_id") or ""
            )
            device_name = str(
                item.get("device_name")
                or baseline.get("device_name")
                or current.get("device_name")
                or ""
            )
            command = str(
                item.get("command") or baseline.get("command") or current.get("command") or ""
            ).strip()
            key = (
                device_id or device_name,
                " ".join(command.split()),
                _cli_evidence_snapshot_identity(baseline),
                _cli_evidence_snapshot_identity(current),
            )
            evidence = evidence_by_key.get(key)
            if evidence is None:
                evidence = {
                    "evidence_id": f"CLI-{len(result) + 1:03d}",
                    "group_labels": [group_label],
                    "device_id": device_id,
                    "device_name": device_name or device_id or "-",
                    "command": command or "-",
                    "baseline": baseline,
                    "current": current,
                    "related_facts": [],
                    "parser_failure_text_comparison": bool(
                        item.get("parser_failure_text_comparison")
                    ),
                }
                evidence_by_key[key] = evidence
                result.append(evidence)
            else:
                if group_label not in evidence["group_labels"]:
                    evidence["group_labels"].append(group_label)
                evidence["parser_failure_text_comparison"] = bool(
                    evidence["parser_failure_text_comparison"]
                    or item.get("parser_failure_text_comparison")
                )

            item["cli_evidence_id"] = evidence["evidence_id"]
            fact_label = _cli_evidence_fact_label(item, group_label)
            if fact_label not in evidence["related_facts"]:
                evidence["related_facts"].append(fact_label)
    return result


def _cli_inline_diff_runs(
    baseline_line: str, current_line: str
) -> tuple[list[tuple[str, bool]], list[tuple[str, bool]]]:
    baseline_runs: list[tuple[str, bool]] = []
    current_runs: list[tuple[str, bool]] = []
    matcher = SequenceMatcher(None, baseline_line, current_line, autojunk=False)
    for tag, baseline_start, baseline_end, current_start, current_end in matcher.get_opcodes():
        baseline_text = baseline_line[baseline_start:baseline_end]
        current_text = current_line[current_start:current_end]
        changed = tag != "equal"
        if baseline_text:
            baseline_runs.append((baseline_text, changed))
        if current_text:
            current_runs.append((current_text, changed))
    return baseline_runs, current_runs


def _cli_diff_rows(
    baseline_text: Any, current_text: Any
) -> list[tuple[list[tuple[str, bool]], list[tuple[str, bool]]]]:
    baseline_lines = str(baseline_text or "").splitlines()
    current_lines = str(current_text or "").splitlines()
    baseline_compare = [line.rstrip() for line in baseline_lines]
    current_compare = [line.rstrip() for line in current_lines]
    matcher = SequenceMatcher(None, baseline_compare, current_compare, autojunk=False)
    rows: list[tuple[list[tuple[str, bool]], list[tuple[str, bool]]]] = []

    for tag, baseline_start, baseline_end, current_start, current_end in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(baseline_end - baseline_start):
                rows.append(
                    (
                        [(baseline_lines[baseline_start + offset], False)],
                        [(current_lines[current_start + offset], False)],
                    )
                )
            continue
        if tag == "delete":
            rows.extend(
                ([(line, True)], []) for line in baseline_lines[baseline_start:baseline_end]
            )
            continue
        if tag == "insert":
            rows.extend(([], [(line, True)]) for line in current_lines[current_start:current_end])
            continue

        baseline_block = baseline_lines[baseline_start:baseline_end]
        current_block = current_lines[current_start:current_end]
        for offset in range(max(len(baseline_block), len(current_block))):
            if offset >= len(baseline_block):
                rows.append(([], [(current_block[offset], True)]))
            elif offset >= len(current_block):
                rows.append(([(baseline_block[offset], True)], []))
            else:
                rows.append(_cli_inline_diff_runs(baseline_block[offset], current_block[offset]))
    return rows or [([], [])]


def _cli_runs_as_html(runs: list[tuple[str, bool]]) -> str:
    if not runs:
        return "&nbsp;"
    rendered: list[str] = []
    for text, changed in runs:
        escaped = html_escape(text)
        if changed:
            rendered.append(f'<span style="color:#c00000">{escaped}</span>')
        else:
            rendered.append(escaped)
    return "".join(rendered) or "&nbsp;"


def _append_cli_comparison_markdown(lines: list[str], evidence: dict[str, Any]) -> None:
    baseline = dict(evidence.get("baseline") or {})
    current = dict(evidence.get("current") or {})
    lines.extend(
        [
            "<table>",
            "<thead><tr>"
            f"<th>基线 CLI 原文<br>快照 {html_escape(str(baseline.get('snapshot_id') or '-'))}</th>"
            f"<th>当前 CLI 原文<br>快照 {html_escape(str(current.get('snapshot_id') or '-'))}</th>"
            "</tr></thead>",
            "<tbody>",
        ]
    )
    for baseline_runs, current_runs in _cli_diff_rows(
        baseline.get("raw_text"), current.get("raw_text")
    ):
        lines.append(
            "<tr>"
            '<td><pre style="margin:0;white-space:pre-wrap">'
            f"{_cli_runs_as_html(baseline_runs)}</pre></td>"
            '<td><pre style="margin:0;white-space:pre-wrap">'
            f"{_cli_runs_as_html(current_runs)}</pre></td>"
            "</tr>"
        )
    lines.extend(["</tbody>", "</table>"])


_BASIC_OBSERVATION_TABLES = (
    ("device_version", "设备启动信息与版本状态", None),
    ("power", "设备电源状态", "电源"),
    ("fan", "设备风扇状态", "风扇"),
    ("temperature", "设备环境温度状态", "温度传感器"),
    ("cpu", "设备 CPU 利用率状态", None),
    ("memory", "设备内存利用率状态", None),
)


def _build_device_inventory_rows(summary: dict[str, Any]) -> list[list[str]]:
    """Build the report's device list from the same immutable observations."""

    devices: dict[str, dict[str, str]] = {}
    for observation in list(summary.get("observations") or []):
        device_id = str(observation.get("device_id") or "").strip()
        device_name = str(observation.get("device_name") or device_id).strip()
        if not device_id and not device_name:
            continue
        key = device_id or device_name
        row = devices.setdefault(
            key,
            {
                "device_name": device_name or key,
                "vendor": "",
                "model": "",
            },
        )
        row["device_name"] = device_name or row["device_name"]
        row["vendor"] = str(observation.get("vendor") or row["vendor"])
        row["model"] = str(observation.get("model") or row["model"])
    return [
        [str(index), item["device_name"], item["model"] or "-", item["vendor"] or "-"]
        for index, item in enumerate(
            sorted(devices.values(), key=lambda row: row["device_name"]),
            start=1,
        )
    ]


def _build_basic_observation_tables(summary: dict[str, Any]) -> list[dict[str, Any]]:
    """Project customer-readable core B facts without technical timestamps or IDs."""

    observations = list(summary.get("observations") or [])
    if not observations:
        return []
    device_names = {
        str(item.get("device_id") or ""): str(item.get("device_name") or "")
        for item in observations
        if item.get("device_id") and item.get("device_name")
    }
    views = {view.key: view for view in REPORT_VIEWS}
    source_tables = {table.key: table for table in build_observation_report_tables(observations)}
    result: list[dict[str, Any]] = []
    for key, title, component_label in _BASIC_OBSERVATION_TABLES:
        source = source_tables.get(key)
        view = views.get(key)
        if source is None or view is None or not source.rows:
            continue
        data_headers = [f"{column.label}（{column.key}）" for column in view.columns]
        if key == "device_version":
            headers = ["设备", "厂商", *data_headers]
            rows = [
                [
                    device_names.get(str(row[0]) or "") or row[0],
                    row[1],
                    *row[5:-1],
                ]
                for row in source.rows
            ]
        else:
            headers = ["设备", "厂商", "型号"]
            if component_label:
                headers.append(component_label)
            headers.extend(data_headers)
            rows = []
            for row in source.rows:
                values = [
                    device_names.get(str(row[0]) or "") or row[0],
                    row[1],
                    row[2],
                ]
                if component_label:
                    values.append(_display_entity_key(row[4]))
                values.extend(row[5:-1])
                rows.append(values)
        result.append({"key": key, "title": title, "headers": headers, "rows": rows})
    return result


def _basic_table_widths(column_count: int) -> list[float]:
    """Fit appendix tables into an A4 portrait content width without fixed heights."""

    usable_width_cm = 16.7
    if column_count <= 0:
        return []
    return [round(usable_width_cm / column_count, 2)] * column_count


def build_leadership_report(
    patrol_batch_id: str,
    summary: dict[str, Any],
    *,
    generated_at: datetime | None = None,
    issuer: str = "网络运维中心",
    recipient: str = "项目建设单位",
) -> dict[str, Any]:
    summary = dict(summary)
    if generated_at is None:
        generated_at = _report_datetime(
            (summary.get("analysis") or {}).get("completed_at")
        ) or datetime.now(timezone.utc)
    all_risks = _build_risks(summary)
    risks, correlated_evidence = _partition_correlated_risks(summary, all_risks)
    domains = _aggregate_domains(summary)
    independent_domains = {str(item.get("domain") or "") for item in risks}
    correlated_domains = {str(item.get("domain") or "") for item in correlated_evidence}
    for domain in domains:
        domain_name = str(domain.get("domain") or "")
        domain["independent_risk_count"] = sum(
            str(item.get("domain") or "") == domain_name for item in risks
        )
        domain["correlated_evidence_count"] = sum(
            str(item.get("domain") or "") == domain_name for item in correlated_evidence
        )
        if (
            domain_name in correlated_domains
            and domain_name not in independent_domains
            and domain.get("status") == "异常"
        ):
            domain["status"] = "需复核"
            domain["reason"] = "异常症状已通过独立复核归入主问题，技术证据仍完整保留"
    assessment = _assessment(summary, risks, domains, correlated_evidence)
    trend = _build_longitudinal_trend(summary, risks, correlated_evidence)
    scope = dict(summary.get("scope") or {})
    quality = dict(summary.get("quality") or {})
    coverage = dict(summary.get("current_state_rule_coverage") or {})
    parser_ready_count = int(quality.get("parser_ready_count") or 0)
    parser_eligible_count = int(quality.get("parser_eligible_count") or 0)
    parse_status_counts = dict(quality.get("parse_status_counts") or {})
    unstructured = dict(summary.get("unstructured_comparison") or {})
    if not unstructured:
        unstructured = summarize_unstructured_changes(
            list(summary.get("changes") or []),
            collected_output_count=int(parse_status_counts.get("raw_hashed") or 0),
        )
    template_missing_count = int(
        quality.get("template_missing_count") or parse_status_counts.get("template_missing") or 0
    )
    parse_failed_count = int(
        quality.get("parse_failed_count") or parse_status_counts.get("parse_failed") or 0
    )
    if not parser_eligible_count:
        parser_eligible_count = parser_ready_count + template_missing_count + parse_failed_count
    events = list(summary.get("events") or [])
    structured_event_evidence = _build_structured_event_evidence(summary, events)
    recovery_event_count = sum(
        str(item.get("event_class") or "fault") == "recovery" for item in events
    )
    resolved_prior_fault_count = sum(
        str(item.get("event_class") or "fault") == "recovery"
        and str(item.get("occurrence_kind") or "") == "recovery"
        for item in events
    )
    state_improvement_count = recovery_event_count - resolved_prior_fault_count
    fault_event_count = sum(str(item.get("event_class") or "fault") == "fault" for item in events)
    unresolved_fault_event_count = sum(
        str(item.get("event_class") or "fault") == "fault"
        and str(item.get("status_after_occurrence") or "open").casefold()
        not in {"resolved", "closed", "cancelled"}
        for item in events
    )
    recovery_by_type = Counter(
        str(item.get("event_type") or "unknown")
        for item in events
        if str(item.get("event_class") or "fault") == "recovery"
    )
    fault_by_type = Counter(
        str(item.get("event_type") or "unknown")
        for item in events
        if str(item.get("event_class") or "fault") == "fault"
    )
    limitations = ["结论仅适用于本批次实际完成并形成结构化观测的设备及检查项。"]
    health_missing_labels = {
        "device_high_threshold_missing": "设备上限未提供",
        "device_low_threshold_missing": "设备下限未提供",
        "measurement_window_missing": "缺少要求的5分钟统计",
        "baseline_reference_missing": "缺少匹配的基线数据",
        "zero_baseline_relative_deviation_undefined": "基线为0且当前非0，相对偏差无法计算",
        "baseline_unit_mismatch": "两期单位不一致",
    }
    health_labels = {
        "device.cpu.utilization": "CPU",
        "device.memory.utilization": "内存",
        "interface.optical.rx_power": "接收光功率",
        "interface.optical.tx_power": "发射光功率",
    }
    missing_health = Counter(
        (basis.get("observation_type"), code)
        for basis in (coverage.get("assessment_basis") or {}).get("entries", [])
        for code in (basis.get("health_assessment") or {}).get("missing", [])
    )
    for (observation_type, code), count in sorted(missing_health.items()):
        limitations.append(
            f"{health_labels.get(observation_type, observation_type)}：{count}项"
            f"{health_missing_labels.get(code, '判定依据不完整')}，不能标为健康。"
        )
    numeric_trends = {
        "notice_count": 0,
        "recovered_count": 0,
        "unknown_count": 0,
        "unverified_identity_count": 0,
        "items": [],
    }
    for basis in (coverage.get("assessment_basis") or {}).get("entries", []):
        health = basis.get("health_assessment") or {}
        numeric_trend = health.get("trend") or {}
        state = numeric_trend.get("status")
        if numeric_trend.get("unverified_identity_fields"):
            numeric_trends["unverified_identity_count"] += 1
        if state == "unknown":
            numeric_trends["unknown_count"] += 1
        if state not in {"notice", "recovered"}:
            continue
        numeric_trends["notice_count" if state == "notice" else "recovered_count"] += 1
        identity = basis.get("entity_identity") or {}
        label = health_labels.get(basis.get("observation_type"), basis.get("observation_type"))
        if state == "recovered":
            explanation = "基线超出当时设备阈值，当前已回到设备正常范围。"
        elif numeric_trend.get("kind") == "linear_power_drop":
            explanation = (
                f"实际光功率下降 {numeric_trend['ratio']:.2%}，"
                f"超过趋势门槛 {numeric_trend['threshold_ratio']:.0%}；当前仍在设备阈值内。"
            )
        else:
            explanation = (
                f"使用率增加 {numeric_trend['increase_points']:g} 个百分点，"
                f"相对增长 {numeric_trend['ratio']:.2%}；当前仍在设备阈值内。"
            )
        if numeric_trend.get("unverified_identity_fields"):
            explanation += "按同一设备、接口和通道比较，模块身份未核实。"
        numeric_trends["items"].append(
            {
                "observation_id": basis.get("observation_id"),
                "device_id": identity.get("device_id"),
                "entity_key": identity.get("entity_key"),
                "label": label,
                "status": state,
                "unit": basis.get("unit"),
                "baseline_value": health.get("baseline_value"),
                "current_value": health.get("value"),
                "explanation": explanation,
            }
        )
    numeric_trends["summary"] = (
        f"数值趋势提醒 {numeric_trends['notice_count']} 项，"
        f"阈值恢复 {numeric_trends['recovered_count']} 项；"
        "趋势提醒不计入当前故障数。"
        f"另有 {numeric_trends['unknown_count']} 项缺少可比依据，未作趋势或恢复判断。"
    )
    if numeric_trends["unverified_identity_count"]:
        numeric_trends["summary"] += (
            f"此外，{numeric_trends['unverified_identity_count']} 项光功率观测仅按接口和通道比较，"
            "模块身份未核实。"
        )
    if int(scope.get("completed_count") or 0) < int(scope.get("target_count") or 0):
        limitations.append("存在未完成、跳过或失败设备，不能据此评价未覆盖设备的健康状态。")
    if quality.get("status") != "ready":
        gap_parts = []
        if template_missing_count:
            gap_parts.append(f"{template_missing_count} 条命令缺少 Parser 模板")
        if parse_failed_count:
            gap_parts.append(f"{parse_failed_count} 条命令解析失败")
        limitations.append(
            "部分命令输出或结构化解析未通过质量门禁"
            + (f"（{'、'.join(gap_parts)}）" if gap_parts else "")
            + "，相关检查域结论可能不完整。"
        )
    if not coverage.get("evaluated_observation_count"):
        limitations.append("当前状态未被活动故障规则有效覆盖，未产生事件不等同于设备健康。")
    if correlated_evidence:
        limitations.append(
            "已批准的风险关联只调整巡检主问题计数，不删除技术风险、事件或证据；关联失效后必须在新报告版本中恢复独立计数。"
        )
    incomplete_domain_labels = [
        str(item.get("label") or item.get("domain") or "")
        for item in domains
        if item.get("status") in {"数据不完整", "规则缺失", "无法判断", "需复核"}
    ]
    if incomplete_domain_labels:
        limitations.append(
            "以下检查域尚不能形成完整健康判断：" + "、".join(incomplete_domain_labels) + "。"
        )
    if unstructured.get("changed_output_count"):
        limitations.append(
            "非结构化输出变化仅表示文本事实发生改变；未经活动事件规则判定的项目"
            "不自动等同于故障，报告只展示脱敏摘要，完整原文以受控原始输出为准。"
        )
    evidence_rows = []
    for evidence_kind, items in (
        ("independent_risk", risks),
        ("correlated_evidence", correlated_evidence),
    ):
        for item in items:
            evidence_rows.append(
                {
                    "kind": evidence_kind,
                    "event_type": item.get("event_type"),
                    "event_label": _event_type_label(item.get("event_type")),
                    "device_id": item.get("device_id"),
                    "device_name": item.get("device_name"),
                    "entity_type": item.get("entity_type"),
                    "entity_key": item.get("entity_key"),
                    "dimensions": dict(item.get("dimensions") or {}),
                    "chain": dict(item.get("evidence_chain") or {}),
                }
            )
    is_asset_evidence_report = summary.get("report_purpose") in {
        "parser_validation",
        "rule_asset_replay",
    }
    return {
        "schema_version": (
            "rule-asset-evidence-report/v1"
            if is_asset_evidence_report
            else "leadership-patrol-report/v1"
        ),
        "document_type": ("规则资产验证报告" if is_asset_evidence_report else "网络设备巡检报告"),
        "title": ("规则资产样本验证与回放报告" if is_asset_evidence_report else "网络设备巡检报告"),
        "issuer": issuer,
        "recipient": recipient,
        "patrol_batch_id": patrol_batch_id,
        "generated_at": _iso(generated_at),
        "assessment": assessment,
        "scope": scope,
        "metrics": {
            "target_device_count": int(scope.get("target_count") or 0),
            "completed_device_count": int(scope.get("completed_count") or 0),
            "failed_device_count": int(scope.get("failed_count") or 0),
            "skipped_device_count": int(scope.get("skipped_count") or 0),
            "command_count": int(quality.get("command_count") or 0),
            "parser_eligible_count": parser_eligible_count,
            "parser_ready_count": parser_ready_count,
            "parser_ready_rate": float(quality.get("parser_ready_rate") or 0),
            "template_missing_count": template_missing_count,
            "parse_failed_count": parse_failed_count,
            "observation_count": int(quality.get("observation_count") or 0),
            "evaluated_current_state_count": int(coverage.get("evaluated_observation_count") or 0),
            "change_count": int(summary.get("change_count") or 0),
            "effective_change_count": sum(bool(change.get('change_policy_version_id')) for change in summary.get('changes') or []),
            "trend_notice_count": numeric_trends['notice_count'],
            "change_policy_missing_count": int(summary.get('change_policy_missing_count') or 0),
            "event_count": int(summary.get("event_count") or 0),
            "fault_event_count": fault_event_count,
            "recovery_event_count": recovery_event_count,
            "current_state_finding_count": len(summary.get("current_findings") or []),
            "unresolved_fault_event_count": unresolved_fault_event_count,
            "current_risk_count": len(risks),
            "current_finding_count": len(risks),
            "unstructured_collected_count": int(unstructured.get("collected_output_count") or 0),
            "unstructured_compared_count": int(unstructured.get("compared_output_count") or 0),
            "unstructured_changed_count": int(unstructured.get("changed_output_count") or 0),
            "unstructured_added_line_count": int(unstructured.get("added_line_count") or 0),
            "unstructured_removed_line_count": int(unstructured.get("removed_line_count") or 0),
            "structured_change_evidence_count": int(
                structured_event_evidence.get("structured_change_count") or 0
            ),
            "structured_event_evidence_count": int(
                structured_event_evidence.get("event_fact_count") or 0
            ),
        },
        "risk_summary": {
            "total": len(risks),
            "by_severity": dict(Counter(item["severity"] for item in risks)),
            "by_type": dict(Counter(str(item["event_type"]) for item in risks)),
            "correlated_evidence_count": len(correlated_evidence),
        },
        "event_summary": {
            "fact_count": len(events),
            "fault_fact_count": fault_event_count,
            "recovery_fact_count": recovery_event_count,
            "resolved_prior_fault_count": resolved_prior_fault_count,
            "state_improvement_count": state_improvement_count,
            "fault_by_type": dict(fault_by_type),
            "recovery_by_type": dict(recovery_by_type),
        },
        "risks": risks,
        "risk_groups": _aggregate_risk_groups(risks),
        "correlated_evidence": correlated_evidence,
        "correlated_evidence_groups": _aggregate_risk_groups(correlated_evidence),
        "trend": trend,
        "numeric_trends": numeric_trends,
        "device_inventory_rows": _build_device_inventory_rows(summary),
        "domains": domains,
        "basic_observation_tables": _build_basic_observation_tables(summary),
        "data_quality": quality,
        "rule_coverage": coverage,
        "structured_event_evidence": structured_event_evidence,
        "unstructured_comparison": unstructured,
        "recommendations": _recommendations(
            assessment,
            {**summary, "unstructured_comparison": unstructured},
            risks,
            correlated_evidence,
        ),
        "limitations": limitations,
        "evidence": {
            "workflow_run_id": summary.get("workflow_run_id"),
            "analysis": dict(summary.get("analysis") or {}),
            "previous_analysis": dict((summary.get("previous_period") or {}).get("analysis") or {}),
            "patrol_run_ids": [
                str(item.get("run_id")) for item in summary.get("runs") or [] if item.get("run_id")
            ],
            "workflow_run_count": int(summary.get("run_count") or 0),
            "snapshot_count": int(quality.get("snapshot_count") or 0),
            "rule_bundle_digests": list(coverage.get("bundle_digests") or []),
            "risk_chains": evidence_rows,
            "raw_output_url": (summary.get("deliverables") or {}).get("raw_cli_url"),
            "event_export_url": (summary.get("deliverables") or {}).get("events_url"),
            "technical_report_url": (summary.get("deliverables") or {}).get("technical_report_url"),
        },
    }


def _domain_threshold_coverage_text(row: dict[str, Any]) -> str | None:
    unknown = int(row.get("threshold_unknown_observation_count") or 0)
    partial = int(row.get("partial_threshold_observation_count") or 0)
    if not unknown and not partial:
        return None
    total = int(row.get("observation_count") or 0)
    evaluated = int(row.get("evaluated_observation_count") or 0)
    details = []
    if partial:
        details.append(f"{partial} 条仅具备部分阈值层级")
    if unknown:
        details.append(f"{unknown} 条无可用设备阈值")
    return (
        f"判定完整性：{row.get('label') or row.get('domain')}共 {total} 条结构化观测，"
        f"{evaluated} 条可按设备阈值评估；{'，'.join(details)}，未据此扩大健康结论。"
    )


def _unstructured_preview_text(item: dict[str, Any], *, limit: int = 2) -> str:
    values = []
    for row in list(item.get("preview") or [])[:limit]:
        operation = "+" if row.get("operation") == "added" else "-"
        values.append(f"{operation} {row.get('text') or '-'}")
    return "；".join(values) or "详见变化证据"


def render_leadership_markdown(report: dict[str, Any]) -> str:
    assessment = report["assessment"]
    metrics = report["metrics"]
    scope_label = (report.get("scope") or {}).get("target_label") or "本批次目标设备"
    device_inventory_rows = list(report.get("device_inventory_rows") or [])
    basic_observation_tables = list(report.get("basic_observation_tables") or [])
    lines = [
        f"# {report['title']}",
        "",
        f"主送：{report['recipient']}",
        f"批次：{report['patrol_batch_id']}",
        f"统计时间：{_format_cn_datetime(report['generated_at'])}",
        "",
        "## 第一章 概述",
        "",
        "### 1.1 巡检概述",
        "",
        f"本报告面向{report['recipient']}，对“{scope_label}”开展网络设备巡检，"
        "依据本批次已采集的设备事实和已启用的巡检规则形成。",
        "",
        "### 1.2 巡检说明",
        "",
        "本次巡检仅采集和核验设备运行信息，不对设备配置作变更。健康结论以标准观测、"
        "设备或模块上报阈值、有效基线和已发布规则为依据；数据不足时明确标记为无法判断。",
        "",
        "### 1.3 巡检范围",
        "",
        f"- 目标设备：{metrics['target_device_count']} 台",
        f"- 完成设备：{metrics['completed_device_count']} 台",
        f"- 失败/跳过：{metrics['failed_device_count'] + metrics['skipped_device_count']} 台",
        f"- 命令/Observation：{metrics['command_count']} / {metrics['observation_count']}",
        (
            f"- Parser ready：{metrics['parser_ready_count']} / "
            f"{metrics['parser_eligible_count']}（{metrics['parser_ready_rate']:.1%}）"
        ),
        (
            f"- 缺失模板/解析失败：{metrics['template_missing_count']} / "
            f"{metrics['parse_failed_count']}"
        ),
        (
            f"- 事件事实：{metrics['event_count']} 条，其中恢复类 "
            f"{metrics['recovery_event_count']} 条"
        ),
        (
            f"- 风险归并：当前状态规则命中 {metrics['current_state_finding_count']} 条，"
            f"尚未恢复故障事件 {metrics['unresolved_fault_event_count']} 条；按对象去重并"
            "按已批准的 RiskEpisode 关联投影后，"
            f"形成巡检主问题 {metrics['current_risk_count']} 项、"
            f"关联症状 {report['risk_summary']['correlated_evidence_count']} 项"
        ),
        "",
        "#### 1.3.1 设备清单",
        "",
        "## 第二章 巡检内容",
        "",
        "### 2.1 网络设备巡检项",
        "",
        "本次巡检按已启用检查项和实际采集命令执行，具体健康判断以检查项绑定的"
        "标准语义 B 和规则资产为准。",
        "",
        "### 2.2 巡检设备配置备份",
        "",
        "由于设备数量较多，配置备份文件较大，不适宜粘贴到 Word 文档内，"
        "因此将以电子版的形式单独提交，特此说明。",
        "",
        "## 第三章 巡检结果",
        "",
        "### 3.1 网络设备",
        "",
        f"**{assessment['status']}**（可信度：{assessment['trust']}）。{assessment['basis']}",
        "",
        "#### 3.1.1 设备基本运行状况",
        "",
    ]
    if device_inventory_rows:
        lines.extend(
            f"- {row[0]}. {row[1]}（型号：{row[2]}；厂商：{row[3]}）"
            for row in device_inventory_rows
        )
    else:
        lines.append("本批次未形成可列示的标准观测设备清单，设备范围以巡检任务清单为准。")
    if basic_observation_tables:
        lines.append("以下展示本次已采集并映射为标准字段的基础运行数据；表中不展示采集时间。")
        for index, table in enumerate(basic_observation_tables, start=1):
            lines.extend(["", f"##### 3.1.1.{index} {table['title']}", ""])
            lines.append("字段：" + "、".join(str(item) for item in table["headers"]))
            for row in table["rows"]:
                lines.append(
                    "- " + "；".join(str(item if item not in (None, "") else "-") for item in row)
                )
    else:
        lines.append("本批次未形成可用于报告的设备版本、CPU、内存、电源、风扇或温度标准观测数据。")
    lines.extend(["", "#### 3.1.2 主要风险与趋势", "",
                  f"当前异常 {metrics.get('current_state_finding_count', 0)} 项；"
                  f"趋势提醒 {metrics.get('trend_notice_count', 0)} 项；"
                  f"有效变化 {metrics.get('effective_change_count', 0)} 项。"])
    if metrics.get('change_policy_missing_count'):
        lines.append(f"{metrics['change_policy_missing_count']} 项观测缺少变化策略，未执行普通变化比较；不能据此判定为没有变化。")
    if report["risk_groups"]:
        for index, risk in enumerate(report["risk_groups"], start=1):
            lines.append(
                f"{index}. [{SEVERITY_LABEL[risk['severity']]}] {risk['risk_name']}："
                f"{risk['device_name']} / {risk['objects']}，{risk['count']} 项，"
                f"当前值 {risk['current_values']}，判据 {risk['criterion']}。"
            )
    else:
        lines.append("本次未形成已确认风险项。")
    if report.get("correlated_evidence"):
        lines.append(
            f"另有 {len(report['correlated_evidence'])} 项症状已通过独立复核归入主问题，"
            "技术风险与事件证据完整保留，不重复计入巡检主问题数。"
        )
        for item in report.get("correlated_evidence_groups") or []:
            lines.append(
                f"- 关联症状：{item['device_name']} / {item['objects']}，"
                f"当前值 {item['current_values']}，判据 {item['criterion']}。"
            )
    if metrics.get("recovery_event_count"):
        event_summary = report.get("event_summary") or {}
        lines.append(
            f"本周期另形成 {metrics['recovery_event_count']} 条恢复方向事实："
            f"其中 {event_summary.get('resolved_prior_fault_count') or 0} 条关闭平台已跟踪故障，"
            f"{event_summary.get('state_improvement_count') or 0} 条仅表明对比期状态改善；"
            "状态改善不等同于关闭同等数量故障，以上均不计入当前风险。"
        )
    trend = report.get("trend") or {}
    if trend.get("available"):
        period_labels = "、".join(
            _format_cn_date(item.get("observed_at")) for item in trend.get("periods") or []
        )
        lines.extend(
            [
                "",
                "##### 3.1.2.1 三期趋势",
                "",
                f"相邻周期已通过设备身份与采集清单连续性校验，串联 {trend['period_count']} 个"
                f"采集周期（{period_labels}）。当前新增异常证据 {trend['new_evidence_count']} 项、"
                f"持续异常证据 {trend['persistent_evidence_count']} 项、已恢复异常证据 "
                f"{trend['resolved_evidence_count']} 项。",
            ]
        )
        for label, key in (
            ("新增", "new_evidence_groups"),
            ("持续", "persistent_evidence_groups"),
            ("已恢复", "resolved_evidence_groups"),
        ):
            for item in trend.get(key) or []:
                lines.append(
                    f"- {label}：{item['risk_name']}，{item['device_name']} / "
                    f"{item['objects']}，{item['current_values']}。"
                )
        comparisons = list(trend.get("comparisons") or [])
        if len(comparisons) >= 2 and (
            comparisons[-1].get("command_count") != comparisons[-2].get("command_count")
        ):
            lines.append(
                f"- 覆盖变化：最近两个周期命令数由 {comparisons[-2].get('command_count')} 条"
                f"增加到 {comparisons[-1].get('command_count')} 条，Change 数量不可直接作为"
                "网络劣化幅度横向比较。"
            )
    lines.extend(["", "#### 3.1.3 分域检查结果", ""])
    for row in report["domains"]:
        lines.append(
            f"- {row['label']}：{row['status']}，Observation {row['observation_count']}，"
            f"巡检主问题 {row['independent_risk_count']}，关联症状 "
            f"{row['correlated_evidence_count']}，Change {row['change_count']}，"
            f"Event {row['event_count']}。"
        )
    for row in report["domains"]:
        coverage_text = _domain_threshold_coverage_text(row)
        if coverage_text:
            lines.append(f"- {coverage_text}")
    full_cli_evidence = _full_cli_evidence_items(report)
    structured = dict(report.get("structured_event_evidence") or {})
    lines.extend(["", "#### 3.1.4 结构化变化与 Event 证据", ""])
    if not structured.get("items"):
        lines.append("本批次没有形成可比较的结构化状态变化。")
    else:
        lines.append(
            f"共形成 {structured.get('structured_change_count')} 条结构化变化，"
            f"其中 {structured.get('event_fact_count')} 条形成 Event。"
        )
        for item in list(structured.get("items") or [])[:20]:
            event = dict(item.get("event") or {})
            baseline = dict(item.get("baseline") or {})
            current = dict(item.get("current") or {})
            outcome = event.get("event_label") or "未触发 Event"
            if event.get("severity"):
                severity = event.get("severity")
                severity_label = SEVERITY_LABEL.get(
                    _severity(severity),
                    severity,
                )
                outcome = f"{outcome}（{severity_label}）"
            lines.append(
                f"- [{outcome}] {item.get('device_name') or '-'} / "
                f"`{item.get('command') or '-'}` / {item.get('entity_key') or '-'} / "
                f"{item.get('field') or '-'}"
            )
            lines.append(
                f"  - 基线数据：{_display_evidence_value(baseline.get('value'))}"
                f"（快照 {baseline.get('snapshot_id') or '-'}）"
            )
            lines.append(
                f"  - 当前数据：{_display_evidence_value(current.get('value'))}"
                f"（快照 {current.get('snapshot_id') or '-'}）"
            )
            if item.get("cli_evidence_id"):
                lines.append(f"  - CLI 原文证据：{item.get('cli_evidence_id')}（详见附录）")
            if event.get("reason") and not event.get("event_id"):
                lines.append(f"  - 未形成 Event 原因：{event.get('reason')}")
        if len(structured.get("items") or []) > 20:
            lines.append("- 其余结构化变化详见技术报告与事件数据附件。")
    unstructured = dict(report.get("unstructured_comparison") or {})
    lines.extend(["", "#### 3.1.5 非结构化文本对比证据", ""])
    if not unstructured.get("collected_output_count"):
        lines.append("本批次未采集配置、日志等已启用非结构化对比的命令输出。")
    elif not unstructured.get("changed_output_count"):
        lines.append(
            f"本批次采集 {unstructured.get('collected_output_count')} 项非结构化输出，"
            "未检测到相对基线的有效文本变化。"
        )
    else:
        lines.append(
            f"共采集 {unstructured.get('collected_output_count')} 项、比较 "
            f"{unstructured.get('compared_output_count')} 项，检测到 "
            f"{unstructured.get('changed_output_count')} 项有效变化；新增 "
            f"{unstructured.get('added_line_count')} 行、删除 "
            f"{unstructured.get('removed_line_count')} 行。"
            + ("完整 CLI 原文详见本报告附录。" if full_cli_evidence else "以下仅展示脱敏摘要。")
        )
        for item in list(unstructured.get("items") or [])[:10]:
            categories = "、".join(
                str(row.get("label") or row.get("name") or "其他变化")
                for row in item.get("categories") or []
            )
            lines.append(
                f"- [{item.get('profile_label')}] {item.get('device_name')} / "
                f"`{item.get('command')}`：+{item.get('added_line_count')} / "
                f"-{item.get('removed_line_count')} 行"
                + (f"，{categories}" if categories else "")
                + f"；{_unstructured_preview_text(item)}"
            )
            lines.append(f"  - 基线证据：{_unstructured_evidence_text(item, 'baseline')}")
            lines.append(f"  - 当前证据：{_unstructured_evidence_text(item, 'current')}")
            if item.get("cli_evidence_id"):
                lines.append(f"  - CLI 原文证据：{item.get('cli_evidence_id')}（详见附录）")
        if len(unstructured.get("items") or []) > 10:
            lines.append("- 其余变化详见技术报告和原始输出证据包。")
    appendix_lines: list[str] = []
    if full_cli_evidence:
        appendix_lines.extend(["", "## 附录 A：完整 CLI 原文证据", ""])
        appendix_lines.append(
            "同一设备、命令和基线/当前快照对仅保留一组原文；红色文本为差异，"
            "空白单元格表示该行只存在于另一侧。"
        )
        for evidence in full_cli_evidence:
            group_label = "、".join(evidence.get("group_labels") or [])
            appendix_lines.append(
                f"### {evidence.get('evidence_id')} / {group_label} / "
                f"{evidence.get('device_name') or evidence.get('device_id') or '-'} / "
                f"{evidence.get('command') or '-'}"
            )
            related_facts = _cli_evidence_related_facts_text(evidence)
            if related_facts:
                appendix_lines.append(f"- 关联事实：{related_facts}")
            if evidence.get("parser_failure_text_comparison"):
                appendix_lines.append(
                    "Parser 未成功，本项仅提供原文文本差异证据，不能替代结构化 Event 判定。"
                )
            _append_cli_comparison_markdown(appendix_lines, evidence)

    lines.extend(["", "## 第四章 巡检总结", "", "### 4.1 数据可信度与结论边界", ""])
    lines.append(
        f"**{assessment['status']}**（可信度：{assessment['trust']}）。"
        "本章结论仅适用于本批次实际完成并形成有效证据的巡检范围。"
    )
    lines.extend(f"- {item}" for item in report["limitations"])

    lines.extend(["", "## 第五章 后续工作计划", "", "### 5.1 后续工作计划", ""])
    for item in report["recommendations"]:
        lines.append(
            f"- {item['priority']}：{item['action']}"
            f"（责任：{item['owner']}；时限：{item['deadline']}）"
        )

    lines.extend(appendix_lines)
    evidence = report["evidence"]
    analysis = evidence.get("analysis") or {}
    lines.extend(
        [
            "",
            "## 附录 B：证据与附件",
            "",
            f"- Workflow Run：{evidence.get('workflow_run_id') or '-'}",
            f"- 分析尝试：{analysis.get('attempt_id') or '-'}",
            f"- 分析版本：{analysis.get('revision') or '-'}",
            "- 规则包：本次分析已固化",
            f"- 权威 Change：{analysis.get('source_change_count') or 0} 条",
            "",
            f"{report['issuer']}",
            _format_cn_date(report["generated_at"]),
            "",
        ]
    )
    return "\n".join(lines)


def _shade_cell(cell: Any, fill: str) -> None:
    properties = cell._tc.get_or_add_tcPr()
    shading = OxmlElement("w:shd")
    shading.set(qn("w:fill"), fill)
    properties.append(shading)


def _set_cell_text(cell: Any, value: Any, *, bold: bool = False) -> None:
    cell.text = ""
    paragraph = cell.paragraphs[0]
    paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
    paragraph.paragraph_format.space_before = Pt(0)
    paragraph.paragraph_format.space_after = Pt(0)
    run = paragraph.add_run(str(value if value not in (None, "") else "-"))
    run.font.name = "仿宋_GB2312"
    run.font.size = Pt(10.5)
    run.font.bold = bold
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "仿宋_GB2312")
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER


def _add_table(
    doc: Any,
    headers: list[str],
    rows: list[list[Any]],
    widths: list[float],
    *,
    keep_rows_together: bool = False,
) -> Any:
    table = doc.add_table(rows=1, cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.style = "Table Grid"
    table.autofit = False
    for index, header in enumerate(headers):
        _set_cell_text(table.rows[0].cells[index], header, bold=True)
        _shade_cell(table.rows[0].cells[index], "D9E2F3")
        table.rows[0].cells[index].width = Cm(widths[index])
    header_properties = table.rows[0]._tr.get_or_add_trPr()
    header_repeat = OxmlElement("w:tblHeader")
    header_repeat.set(qn("w:val"), "true")
    header_properties.append(header_repeat)
    for values in rows:
        row = table.add_row()
        row_properties = row._tr.get_or_add_trPr()
        row_properties.append(OxmlElement("w:cantSplit"))
        cells = row.cells
        for index, value in enumerate(values):
            _set_cell_text(cells[index], value)
            cells[index].width = Cm(widths[index])
    if keep_rows_together:
        for row in table.rows[:-1]:
            for cell in row.cells:
                for paragraph in cell.paragraphs:
                    paragraph.paragraph_format.keep_with_next = True
    doc.add_paragraph().paragraph_format.space_after = Pt(0)
    return table


def _add_section(doc: Any, title: str, *, level: int = 1) -> None:
    """Add a readable chapter heading without relying on Word theme styles."""

    paragraph = doc.add_paragraph()
    apply_fixed_line_spacing(paragraph)
    paragraph.paragraph_format.keep_with_next = True
    paragraph.paragraph_format.space_before = Pt({1: 16, 2: 12, 3: 8}.get(level, 5))
    paragraph.paragraph_format.space_after = Pt(4)
    run = paragraph.add_run(title)
    run.font.name = "黑体"
    run.font.size = Pt({1: 16, 2: 15, 3: 14}.get(level, 12))
    run.font.bold = True
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "黑体")


def _add_compact_paragraph(doc: Any, text: str, *, bold: bool = False) -> Any:
    paragraph = doc.add_paragraph()
    paragraph.paragraph_format.line_spacing_rule = WD_LINE_SPACING.EXACTLY
    paragraph.paragraph_format.line_spacing = Pt(11)
    paragraph.paragraph_format.space_before = Pt(0)
    paragraph.paragraph_format.space_after = Pt(1)
    run = paragraph.add_run(text)
    run.font.name = "仿宋_GB2312"
    run.font.size = Pt(8)
    run.font.bold = bold
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "仿宋_GB2312")
    return paragraph


def _evidence_value(chain: dict[str, Any], key: str) -> str:
    value = chain.get(key)
    return str(value) if value not in (None, "") else "-"


_DOCX_ARCHIVE_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def _normalize_docx_archive(content: bytes) -> bytes:
    """Make identical report facts produce byte-identical DOCX artifacts."""
    source_stream = io.BytesIO(content)
    output_stream = io.BytesIO()
    with (
        zipfile.ZipFile(source_stream, "r") as source,
        zipfile.ZipFile(
            output_stream,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as target,
    ):
        for source_info in source.infolist():
            target_info = zipfile.ZipInfo(
                filename=source_info.filename,
                date_time=_DOCX_ARCHIVE_TIMESTAMP,
            )
            target_info.compress_type = zipfile.ZIP_DEFLATED
            target_info.create_system = source_info.create_system
            target_info.external_attr = source_info.external_attr
            target_info.internal_attr = source_info.internal_attr
            target_info.comment = source_info.comment
            target.writestr(target_info, source.read(source_info.filename))
    return output_stream.getvalue()


def _add_cli_diff_cell(cell: Any, runs: list[tuple[str, bool]]) -> None:
    cell.text = ""
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
    paragraph = cell.paragraphs[0]
    paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
    paragraph.paragraph_format.line_spacing_rule = WD_LINE_SPACING.EXACTLY
    paragraph.paragraph_format.line_spacing = Pt(9)
    paragraph.paragraph_format.space_before = Pt(0)
    paragraph.paragraph_format.space_after = Pt(0)
    if not runs:
        paragraph.add_run(" ")
        return
    for text, changed in runs:
        run = paragraph.add_run(text)
        run.font.name = "Consolas"
        run.font.size = Pt(7.5)
        if changed:
            run.font.color.rgb = RGBColor(192, 0, 0)


def _add_cli_comparison_docx(doc: Any, evidence: dict[str, Any]) -> None:
    baseline = dict(evidence.get("baseline") or {})
    current = dict(evidence.get("current") or {})
    table = doc.add_table(rows=1, cols=2)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.style = "Table Grid"
    table.autofit = False
    headers = [
        f"基线 CLI 原文\n快照 {baseline.get('snapshot_id') or '-'}",
        f"当前 CLI 原文\n快照 {current.get('snapshot_id') or '-'}",
    ]
    for index, header in enumerate(headers):
        cell = table.rows[0].cells[index]
        _set_cell_text(cell, header, bold=True)
        _shade_cell(cell, "D9E2F3")
        cell.width = Cm(8.35)
    header_properties = table.rows[0]._tr.get_or_add_trPr()
    header_repeat = OxmlElement("w:tblHeader")
    header_repeat.set(qn("w:val"), "true")
    header_properties.append(header_repeat)

    for baseline_runs, current_runs in _cli_diff_rows(
        baseline.get("raw_text"), current.get("raw_text")
    ):
        row = table.add_row()
        row._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))
        for index, runs in enumerate((baseline_runs, current_runs)):
            cell = row.cells[index]
            cell.width = Cm(8.35)
            _add_cli_diff_cell(cell, runs)
    doc.add_paragraph().paragraph_format.space_after = Pt(0)


def render_leadership_docx(report: dict[str, Any]) -> bytes:
    doc = Document()
    setup_a4_margins(doc)
    section = doc.sections[0]
    section.header_distance = Cm(1.5)
    section.footer_distance = Cm(1.75)

    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title.paragraph_format.space_before = Pt(0)
    title.paragraph_format.space_after = Pt(18)
    title_run = title.add_run(report["title"])
    title_run.font.name = "方正小标宋简体"
    title_run.font.size = Pt(22)
    title_run._element.rPr.rFonts.set(qn("w:eastAsia"), "方正小标宋简体")

    add_styled_paragraph(
        doc,
        f"主送：{report['recipient']}",
        first_line_indent=0,
    )

    meta = _add_table(
        doc,
        ["巡检范围", "统计时间", "完成情况", "结论可信度"],
        [
            [
                (report.get("scope") or {}).get("target_label") or "本批次目标设备",
                _format_cn_datetime(report["generated_at"]),
                (
                    f"{report['metrics']['completed_device_count']} / "
                    f"{report['metrics']['target_device_count']} 台"
                ),
                report["assessment"]["trust"],
            ]
        ],
        [5.6, 4.0, 3.2, 2.5],
    )
    meta.rows[0].height = Cm(0.8)

    metrics = report["metrics"]
    scope_label = (report.get("scope") or {}).get("target_label") or "本批次目标设备"
    device_inventory_rows = list(report.get("device_inventory_rows") or [])
    basic_observation_tables = list(report.get("basic_observation_tables") or [])

    add_styled_paragraph(doc,
        f"当前异常 {metrics.get('current_state_finding_count', 0)} 项；"
        f"趋势提醒 {metrics.get('trend_notice_count', 0)} 项；"
        f"有效变化 {metrics.get('effective_change_count', 0)} 项。")
    if metrics.get('change_policy_missing_count'):
        add_styled_paragraph(doc, f"{metrics['change_policy_missing_count']} 项观测缺少变化策略，未执行普通变化比较；不能据此判定为没有变化。")

    _add_section(doc, "第一章 概述", level=1)
    _add_section(doc, "1.1 巡检概述", level=2)
    add_styled_paragraph(
        doc,
        f"本报告面向{report['recipient']}，对“{scope_label}”开展网络设备巡检，"
        f"报告依据本批次已采集的设备事实和已启用的巡检规则形成。",
        first_line_indent=0.74,
    )
    _add_section(doc, "1.2 巡检说明", level=2)
    add_styled_paragraph(
        doc,
        "本次巡检仅采集和核验设备运行信息，不对设备配置作变更。健康结论以标准观测、"
        "设备或模块上报阈值、有效基线和已发布规则为依据；数据不足时明确标记为无法判断。",
        first_line_indent=0.74,
    )
    _add_section(doc, "1.3 巡检范围", level=2)
    _add_table(
        doc,
        ["目标设备", "完成设备", "失败", "跳过", "命令", "结构化率", "结构化观测"],
        [
            [
                metrics["target_device_count"],
                metrics["completed_device_count"],
                metrics["failed_device_count"],
                metrics["skipped_device_count"],
                metrics["command_count"],
                f"{metrics['parser_ready_rate']:.1%}",
                metrics["observation_count"],
            ]
        ],
        [2.1, 2.1, 1.6, 1.6, 1.8, 2.7, 2.8],
    )
    _add_section(doc, "1.3.1 设备清单", level=3)
    if device_inventory_rows:
        _add_table(
            doc,
            ["序号", "设备名称", "设备型号", "厂商"],
            device_inventory_rows,
            [1.2, 5.6, 5.6, 4.3],
            keep_rows_together=True,
        )
    else:
        add_styled_paragraph(
            doc,
            "本批次未形成可列示的标准观测设备清单，设备范围以巡检任务清单为准。",
            first_line_indent=0.74,
        )

    _add_section(doc, "第二章 巡检内容", level=1)
    _add_section(doc, "2.1 网络设备巡检项", level=2)
    domain_labels = "、".join(
        str(item.get("label") or item.get("domain") or "")
        for item in report.get("domains") or []
        if item.get("label") or item.get("domain")
    )
    add_styled_paragraph(
        doc,
        "本次巡检按已启用检查项和实际采集命令执行，涵盖"
        f"{domain_labels or '设备基础信息、硬件环境、接口状态、配置与日志'}等内容。"
        "具体健康判断以检查项绑定的标准语义 B 和规则资产为准。",
        first_line_indent=0.74,
    )
    add_styled_paragraph(
        doc,
        (
            f"结构化门禁：可解析命令 {metrics['parser_ready_count']} / "
            f"{metrics['parser_eligible_count']}（{metrics['parser_ready_rate']:.1%}）；"
            f"缺少模板 {metrics['template_missing_count']} 条，解析失败 "
            f"{metrics['parse_failed_count']} 条。事件事实 {metrics['event_count']} 条，"
            f"其中恢复类 {metrics['recovery_event_count']} 条。当前状态规则命中 "
            f"{metrics['current_state_finding_count']} 条，尚未恢复故障事件 "
            f"{metrics['unresolved_fault_event_count']} 条；按已批准的 RiskEpisode 关联投影后，"
            f"形成巡检主问题 {metrics['current_risk_count']} 项、关联症状 "
            f"{report['risk_summary']['correlated_evidence_count']} 条。"
        ),
        first_line_indent=0.74,
    )
    _add_section(doc, "2.2 巡检设备配置备份", level=2)
    add_styled_paragraph(
        doc,
        "由于设备数量较多，配置备份文件较大，不适宜粘贴到 Word 文档内，"
        "因此将以电子版的形式单独提交，特此说明。",
        first_line_indent=0.74,
    )

    doc.add_page_break()
    _add_section(doc, "第三章 巡检结果", level=1)
    _add_section(doc, "3.1 网络设备", level=2)
    add_styled_paragraph(
        doc,
        f"{report['assessment']['status']}。{report['assessment']['basis']}。",
        first_line_indent=0.74,
        bold=True,
    )
    add_styled_paragraph(
        doc,
        "说明：即使巡检流程执行完成，或未产生状态变化与事件，也不能单独证明设备健康；"
        "本报告结论同时受执行覆盖、结构化质量和当前状态规则覆盖约束。",
        first_line_indent=0.74,
    )
    _add_section(doc, "3.1.1 设备基本运行状况", level=3)
    if not basic_observation_tables:
        add_styled_paragraph(
            doc,
            "本批次未形成可用于报告的设备版本、CPU、内存、电源、风扇或温度标准观测数据。",
            first_line_indent=0.74,
        )
    else:
        add_styled_paragraph(
            doc,
            "以下表格展示本次已采集并映射为标准字段的设备基础运行数据；"
            "表中不展示采集时间，缺失字段保留为空。",
            first_line_indent=0.74,
        )
        for index, table in enumerate(basic_observation_tables, start=1):
            _add_section(doc, f"3.1.1.{index} {table['title']}", level=4)
            _add_table(
                doc,
                list(table["headers"]),
                list(table["rows"]),
                _basic_table_widths(len(table["headers"])),
                keep_rows_together=True,
            )

    _add_section(doc, "3.1.2 主要风险与趋势", level=3)
    risk_groups = report["risk_groups"]
    if risk_groups:
        rows = [
            [
                SEVERITY_LABEL[_severity(risk.get("severity"))],
                risk.get("risk_name"),
                risk.get("device_name"),
                risk.get("objects"),
                risk.get("current_values"),
                risk.get("criterion"),
                risk.get("count"),
            ]
            for risk in risk_groups
        ]
        _add_table(
            doc,
            ["等级", "风险事项", "设备", "影响对象", "当前值", "判定依据", "数量"],
            rows,
            [1.2, 2.2, 2.8, 2.6, 1.6, 3.5, 0.9],
        )
    else:
        add_styled_paragraph(doc, "本次未形成已确认风险项。", first_line_indent=0.74)
    if report.get("correlated_evidence"):
        devices = sorted(
            {
                str(item.get("device_name") or item.get("device_id") or "-")
                for item in report["correlated_evidence"]
            }
        )
        add_styled_paragraph(
            doc,
            f"另有 {len(report['correlated_evidence'])} 项症状已通过独立复核归入主问题，"
            f"涉及设备 {'、'.join(devices)}。技术风险与事件证据完整保留，"
            "不重复计入巡检主问题数，具体对象和值如下。",
            first_line_indent=0.74,
        )
        correlated_rows = [
            [
                item.get("risk_name"),
                item.get("device_name"),
                item.get("objects"),
                item.get("current_values"),
                item.get("criterion"),
                item.get("count"),
            ]
            for item in report.get("correlated_evidence_groups") or []
        ]
        if correlated_rows:
            _add_table(
                doc,
                ["关联症状", "设备", "对象", "当前值", "判定依据", "数量"],
                correlated_rows,
                [2.4, 1.3, 4.8, 2.0, 3.5, 0.8],
            )
    if metrics.get("recovery_event_count"):
        recovery_items = []
        for event_type, count in sorted(
            (report.get("event_summary") or {}).get("recovery_by_type", {}).items()
        ):
            recovery_items.append(f"{_event_type_label(event_type)} {count} 条")
        add_styled_paragraph(
            doc,
            f"本周期另形成 {metrics['recovery_event_count']} 条恢复类事件事实"
            + (f"（{'、'.join(recovery_items)}）" if recovery_items else "")
            + f"。其中 {report['event_summary']['resolved_prior_fault_count']} 条关闭平台"
            f"已跟踪故障，{report['event_summary']['state_improvement_count']} 条仅表示"
            "对比期状态改善；状态改善不等同于关闭同等数量故障，以上均不计入当前风险。",
            first_line_indent=0.74,
        )

    trend = report.get("trend") or {}
    if trend.get("available"):
        period_labels = "、".join(
            _format_cn_date(item.get("observed_at")) for item in trend.get("periods") or []
        )
        add_styled_paragraph(
            doc,
            f"三期趋势：相邻周期已通过设备身份与采集清单连续性校验，已串联 "
            f"{trend['period_count']} 个采集周期（{period_labels}）。当前新增异常证据 "
            f"{trend['new_evidence_count']} 项、持续异常证据 "
            f"{trend['persistent_evidence_count']} 项、已恢复异常证据 "
            f"{trend['resolved_evidence_count']} 项。",
            first_line_indent=0.74,
            bold=True,
        )
        trend_rows = []
        for label, key in (
            ("新增", "new_evidence_groups"),
            ("持续", "persistent_evidence_groups"),
            ("已恢复", "resolved_evidence_groups"),
        ):
            for item in trend.get(key) or []:
                trend_rows.append(
                    [
                        label,
                        item.get("risk_name"),
                        item.get("device_name"),
                        item.get("objects"),
                        item.get("current_values"),
                    ]
                )
        if trend_rows:
            _add_table(
                doc,
                ["趋势", "异常信号", "设备", "对象", "观测值"],
                trend_rows,
                [1.2, 3.2, 3.0, 4.1, 2.5],
            )
        comparisons = list(trend.get("comparisons") or [])
        if len(comparisons) >= 2 and (
            comparisons[-1].get("command_count") != comparisons[-2].get("command_count")
        ):
            add_styled_paragraph(
                doc,
                f"覆盖变化说明：最近两个周期命令数由 "
                f"{comparisons[-2].get('command_count')} 条增加到 "
                f"{comparisons[-1].get('command_count')} 条。因检查范围扩大，Change 数量"
                "不可直接作为网络劣化幅度横向比较。",
                first_line_indent=0.74,
            )

    _add_section(doc, "3.1.3 分域检查结果", level=3)
    domain_rows = [
        [
            row["label"],
            row["status"],
            row["device_count"],
            row["observation_count"],
            f"{row['independent_risk_count']} / {row['correlated_evidence_count']}",
            row["change_count"],
            row["event_count"],
        ]
        for row in report["domains"]
    ]
    if domain_rows:
        _add_table(
            doc,
            ["检查域", "结论", "设备", "结构化观测", "风险/证据", "状态变化", "事件"],
            domain_rows,
            [2.5, 4.1, 1.4, 2.1, 1.8, 1.7, 1.7],
            keep_rows_together=True,
        )
        for row in report["domains"]:
            coverage_text = _domain_threshold_coverage_text(row)
            if coverage_text:
                add_styled_paragraph(
                    doc,
                    coverage_text,
                    first_line_indent=0.74,
                )
    else:
        add_styled_paragraph(doc, "未形成可用的分域检查结果。", first_line_indent=0.74)

    full_cli_evidence = _full_cli_evidence_items(report)
    structured = dict(report.get("structured_event_evidence") or {})
    _add_section(doc, "3.1.4 结构化变化与 Event 证据", level=3)
    if not structured.get("items"):
        add_styled_paragraph(doc, "本批次没有形成可比较的结构化状态变化。", first_line_indent=0.74)
    else:
        add_styled_paragraph(
            doc,
            f"共形成 {structured.get('structured_change_count')} 条结构化变化，"
            f"其中 {structured.get('event_fact_count')} 条形成 Event；下表列出基线、当前数据、"
            "Event 判定及 CLI 原文证据编号。",
            first_line_indent=0.74,
        )
        structured_rows = []
        for item in list(structured.get("items") or [])[:20]:
            event = dict(item.get("event") or {})
            baseline = dict(item.get("baseline") or {})
            current = dict(item.get("current") or {})
            outcome = event.get("event_label") or "未触发 Event"
            if event.get("severity"):
                severity = event.get("severity")
                severity_label = SEVERITY_LABEL.get(
                    _severity(severity),
                    severity,
                )
                outcome = f"{outcome} / {severity_label}"
            if not event.get("event_id") and event.get("reason"):
                outcome = f"{outcome}\n{event.get('reason')}"
            structured_rows.append(
                [
                    outcome,
                    item.get("device_name"),
                    f"{item.get('command') or '-'}\n{item.get('entity_key') or '-'}",
                    item.get("field"),
                    _display_evidence_value(baseline.get("value"), limit=140),
                    _display_evidence_value(current.get("value"), limit=140),
                    item.get("cli_evidence_id"),
                ]
            )
        _add_table(
            doc,
            [
                "Event 判定",
                "设备",
                "命令 / 对象",
                "字段",
                "基线数据",
                "当前数据",
                "CLI 证据",
            ],
            structured_rows,
            [2.0, 1.8, 2.4, 1.4, 3.2, 3.2, 1.5],
            keep_rows_together=True,
        )
        if len(structured.get("items") or []) > 20:
            add_styled_paragraph(
                doc,
                (
                    f"其余 {len(structured.get('items') or []) - 20} 条结构化变化"
                    "详见技术报告与事件数据附件。"
                ),
                first_line_indent=0.74,
            )

    _add_section(doc, "3.1.5 非结构化文本对比证据", level=3)
    unstructured = dict(report.get("unstructured_comparison") or {})
    if not unstructured.get("collected_output_count"):
        add_styled_paragraph(
            doc,
            "本批次未采集配置、日志等已启用非结构化对比的命令输出。",
            first_line_indent=0.74,
        )
    elif not unstructured.get("changed_output_count"):
        add_styled_paragraph(
            doc,
            f"本批次采集 {unstructured.get('collected_output_count')} 项非结构化输出，"
            "未检测到相对基线的有效文本变化。",
            first_line_indent=0.74,
        )
    else:
        add_styled_paragraph(
            doc,
            (
                f"共采集 {unstructured.get('collected_output_count')} 项、比较 "
                f"{unstructured.get('compared_output_count')} 项，检测到 "
                f"{unstructured.get('changed_output_count')} 项有效变化；新增 "
                f"{unstructured.get('added_line_count')} 行、删除 "
                f"{unstructured.get('removed_line_count')} 行。下表按变化规模列示重点输出，"
                + (
                    "完整 CLI 原文详见本报告附录。"
                    if full_cli_evidence
                    else "完整脱敏差异见技术报告，原文以受控原始输出为准。"
                )
            ),
            first_line_indent=0.74,
        )
        unstructured_rows = []
        for item in list(unstructured.get("items") or [])[:8]:
            categories = "、".join(
                str(row.get("label") or row.get("name") or "其他变化")
                for row in item.get("categories") or []
            )
            unstructured_rows.append(
                [
                    item.get("profile_label"),
                    item.get("device_name"),
                    item.get("command"),
                    f"+{item.get('added_line_count')} / -{item.get('removed_line_count')}",
                    categories or "其他变化",
                    item.get("cli_evidence_id"),
                ]
            )
        _add_table(
            doc,
            ["类型", "设备", "命令", "增/删行", "变化分类", "CLI 证据"],
            unstructured_rows,
            [2.0, 2.2, 3.6, 1.7, 3.8, 1.7],
        )
        for item in list(unstructured.get("items") or [])[:8]:
            add_styled_paragraph(
                doc,
                f"{item.get('device_name') or '-'} / {item.get('command') or '-'}："
                "以下为脱敏的基线与当前文本证据。",
                first_line_indent=0.74,
            )
            _add_table(
                doc,
                ["基线证据", "当前证据"],
                [
                    [
                        _unstructured_evidence_text(item, "baseline", limit=4),
                        _unstructured_evidence_text(item, "current", limit=4),
                    ]
                ],
                [8.4, 8.4],
                keep_rows_together=True,
            )
        if len(unstructured.get("items") or []) > 8:
            add_styled_paragraph(
                doc,
                f"其余 {len(unstructured.get('items') or []) - 8} 项变化详见技术报告；"
                "巡检报告不展开长日志正文，避免将滚动日志记录误读为独立故障。",
                first_line_indent=0.74,
            )

    _add_section(doc, "第四章 巡检总结", level=1)
    add_styled_paragraph(
        doc,
        f"{report['assessment']['status']}。{report['assessment']['basis']}。"
        "本章结论仅适用于本批次实际完成并形成有效证据的巡检范围。",
        first_line_indent=0.74,
        bold=True,
    )
    _add_section(doc, "4.1 数据可信度与结论边界", level=2)
    coverage = report["rule_coverage"]
    digests = [str(item) for item in coverage.get("bundle_digests") or []]
    digest_summary = "、".join(f"{item[:12]}…" for item in digests) or "未形成"
    add_styled_paragraph(
        doc,
        f"本批次活动故障规则 {coverage.get('active_fault_rule_count') or 0} 条，"
        f"对 {coverage.get('evaluated_observation_count') or 0} 条当前状态完成判定；"
        f"规则资产快照 {len(digests)} 组（摘要 {digest_summary}），完整摘要在技术附件中留存。",
        first_line_indent=0.74,
    )
    add_styled_paragraph(
        doc,
        (
            f"本批次共执行 {metrics['command_count']} 条命令，形成 "
            f"{metrics['observation_count']} 条结构化观测。Parser 就绪率为 "
            f"{metrics['parser_ready_rate']:.1%}，缺少模板 "
            f"{metrics['template_missing_count']} 条、解析失败 "
            f"{metrics['parse_failed_count']} 条；因此报告可信度定为"
            f"“{report['assessment']['trust']}”，不得外推为全网健康结论。"
        ),
        first_line_indent=0.74,
    )
    chinese_ordinals = "一二三四五六七八九十"
    for index, item in enumerate(report["limitations"]):
        ordinal = chinese_ordinals[index] if index < len(chinese_ordinals) else str(index + 1)
        add_styled_paragraph(doc, f"（{ordinal}）{item}", first_line_indent=0.74)

    _add_section(doc, "第五章 后续工作计划", level=1)
    _add_section(doc, "5.1 后续工作计划", level=2)
    for index, item in enumerate(report["recommendations"], start=1):
        add_styled_paragraph(
            doc,
            f"{index}. [{item['priority']}] {item['action']}。责任单位：{item['owner']}；"
            f"完成时限：{item['deadline']}。",
            first_line_indent=0.74,
        )

    if full_cli_evidence:
        _add_section(doc, "附录 A：完整 CLI 原文证据", level=1)
        add_styled_paragraph(
            doc,
            "同一设备、命令和基线/当前快照对仅保留一组原文；红色文本为差异，"
            "空白单元格表示该行只存在于另一侧。",
            first_line_indent=0.74,
        )
        for evidence in full_cli_evidence:
            group_label = "、".join(evidence.get("group_labels") or [])
            add_styled_paragraph(
                doc,
                f"{evidence.get('evidence_id')} / {group_label} / "
                f"{evidence.get('device_name') or evidence.get('device_id') or '-'} / "
                f"{evidence.get('command') or '-'}",
                first_line_indent=0.74,
            )
            related_facts = _cli_evidence_related_facts_text(evidence)
            if related_facts:
                _add_compact_paragraph(doc, f"关联事实：{related_facts}")
            if evidence.get("parser_failure_text_comparison"):
                add_styled_paragraph(
                    doc,
                    "Parser 未成功，本项仅提供原文文本差异证据，不能替代结构化 Event 判定。",
                    first_line_indent=0.74,
                )
            _add_cli_comparison_docx(doc, evidence)

    _add_section(doc, "附录 B：证据与附件", level=1)
    evidence = report["evidence"]
    analysis = dict(evidence.get("analysis") or {})
    attempt_id = str(analysis.get("attempt_id") or "-")
    attempt_summary = f"{attempt_id[:14]}…" if len(attempt_id) > 14 else attempt_id
    add_styled_paragraph(
        doc,
        f"本报告关联 Workflow {evidence.get('workflow_run_id') or '-'}"
        f"（设备执行 {evidence['workflow_run_count']} 份、命令快照 "
        f"{evidence['snapshot_count']} 条），交付物均按批次号提供。事实锁定 Analysis "
        f"{attempt_summary} / {analysis.get('revision') or '-'}（权威 Change "
        f"{analysis.get('source_change_count') or 0} 条），后续分析不改写。",
        first_line_indent=0.74,
    )
    add_styled_paragraph(
        doc,
        f"{report['issuer']}\n{_format_cn_date(report['generated_at'])}",
        right=True,
    )

    risk_chains = list(evidence.get("risk_chains") or [])
    if risk_chains:
        doc.add_page_break()
        _add_section(doc, "附录 C：风险证据索引")
        _add_compact_paragraph(
            doc,
            "以下标识均为平台持久化事实的完整 ID，可在离线巡检详情、变化清单和事件清单中交叉复核。",
        )
        independent_chains = [
            item for item in risk_chains if item.get("kind") == "independent_risk"
        ]
        correlated_chains = [item for item in risk_chains if item.get("kind") != "independent_risk"]
        ordered_chains: list[dict[str, Any]] = []
        if independent_chains and correlated_chains:
            correlated_per_risk = (len(correlated_chains) + len(independent_chains) - 1) // len(
                independent_chains
            )
            correlated_index = 0
            for independent_chain in independent_chains:
                ordered_chains.append(independent_chain)
                next_index = min(
                    correlated_index + correlated_per_risk,
                    len(correlated_chains),
                )
                ordered_chains.extend(correlated_chains[correlated_index:next_index])
                correlated_index = next_index
            ordered_chains.extend(correlated_chains[correlated_index:])
        else:
            ordered_chains = risk_chains

        for index, item in enumerate(ordered_chains, start=1):
            chain = dict(item.get("chain") or {})
            kind = "巡检主问题" if item.get("kind") == "independent_risk" else "关联症状"
            item_heading = _add_compact_paragraph(
                doc,
                (
                    f"{index}. [{kind}] {item.get('event_label') or item.get('event_type')} | "
                    f"{item.get('device_name') or item.get('device_id')} | "
                    f"{_display_risk_entity(item)}"
                ),
                bold=True,
            )
            item_heading.paragraph_format.keep_together = True
            item_heading.paragraph_format.keep_with_next = True
            collection_evidence = _add_compact_paragraph(
                doc,
                "采集与结构化："
                f"Analysis={_evidence_value(chain, 'analysis_attempt_id')} / "
                f"{_evidence_value(chain, 'analysis_revision')}；"
                f"PatrolRun={_evidence_value(chain, 'patrol_run_id')}；"
                f"PreviousSnapshot={_evidence_value(chain, 'previous_snapshot_id')}；"
                f"CurrentSnapshot={_evidence_value(chain, 'current_snapshot_id')}；"
                f"RawEvidence={_evidence_value(chain, 'raw_evidence_id')}；"
                f"Observation={_evidence_value(chain, 'current_observation_id')}；"
                f"Parser={_evidence_value(chain, 'parser_template_id')} / "
                f"{_evidence_value(chain, 'parser_version')}；"
                f"Contract={_evidence_value(chain, 'observation_contract_id')} / "
                f"{_evidence_value(chain, 'observation_contract_version')}。",
            )
            collection_evidence.paragraph_format.keep_together = True
            collection_evidence.paragraph_format.keep_with_next = True
            change_event_evidence = _add_compact_paragraph(
                doc,
                "变化与事件："
                f"Change={_evidence_value(chain, 'change_id')}；"
                f"ChangePolicy={_evidence_value(chain, 'change_policy_id')} / "
                f"{_evidence_value(chain, 'change_policy_version_id')}；"
                f"EventRule={_evidence_value(chain, 'event_rule_id')} / "
                f"{_evidence_value(chain, 'event_rule_version_id')}；"
                f"Event={_evidence_value(chain, 'event_id')}；"
                f"Occurrence={_evidence_value(chain, 'event_occurrence_id')}。",
            )
            change_event_evidence.paragraph_format.keep_together = True
            change_event_evidence.paragraph_format.space_after = Pt(3)

    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer_run = footer.add_run(f"批次 {report['patrol_batch_id']} | 系统依据可追溯事实自动生成")
    footer_run.font.name = "仿宋_GB2312"
    footer_run.font.size = Pt(9)
    footer_run._element.rPr.rFonts.set(qn("w:eastAsia"), "仿宋_GB2312")

    stream = io.BytesIO()
    doc.save(stream)
    return _normalize_docx_archive(stream.getvalue())


def render_leadership_json(report: dict[str, Any]) -> bytes:
    return json.dumps(
        report,
        ensure_ascii=False,
        indent=2,
        default=str,
        allow_nan=False,
    ).encode("utf-8")
