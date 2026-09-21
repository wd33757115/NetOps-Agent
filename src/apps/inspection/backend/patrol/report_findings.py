"""Problem-focused presentation of frozen patrol facts; never reassess device health."""

from datetime import datetime

from src.core.patrol.report_evidence import build_excerpts
from src.core.patrol.report_view import display, identity, observation_groups

LABELS = {
    "rx_power_dbm": "接收光功率",
    "tx_power_dbm": "发射光功率",
    "cpu_usage_percent": "CPU 使用率",
    "memory_usage_percent": "内存使用率",
    "temperature_celsius": "温度",
    "fan_status": "风扇状态",
    "power_supply_status": "电源状态",
    "oper_status": "接口运行状态",
    "line_status": "接口物理状态",
    "protocol_status": "接口协议状态",
    "input_errors": "接收错误计数",
    "output_errors": "发送错误计数",
    "temperature_high_alarm_celsius": "温度告警上限",
    "temperature_high_warning_celsius": "温度预警上限",
}


def date_text(value):
    if not value:
        return "未提供"
    try:
        date = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return date.strftime("%Y-%m-%d %H:%M:%S") + (date.strftime(" %z") if date.tzinfo else "")
    except ValueError:
        return str(value)


def field_label(field):
    return LABELS.get(field, field or "检查结果")


def readable_reason(value):
    text = str(value or "缺少判定依据")
    for field, label in LABELS.items():
        text = text.replace(field, label)
    return text


def project_findings(report, source, metadata, view):
    """Add the latest template's tables while retaining imported template bindings."""
    source, metadata = source or {}, metadata or {}
    excerpts = build_excerpts(report)
    refs = {ref: e["id"] for e in excerpts for ref in e["match_references"]}

    def evidence(*values):
        matches = list(dict.fromkeys(refs[v] for v in values if v and v in refs))
        return "、".join(matches) or "原文证据不足，需补采"

    findings, actions, represented = [], [], set()
    # Use the report's authoritative risk list, enriched by the frozen finding assessment.
    details = {f.get("observation_id"): f for f in source.get("current_findings") or []}
    for item in (report.get("structured_event_evidence") or {}).get("items") or []:
        if item.get("current_finding"):
            details.setdefault(
                (item.get("current") or {}).get("observation_id"),
                item["current_finding"] if isinstance(item["current_finding"], dict) else item,
            )
    risk_rows = [(risk, False) for risk in report.get("risks") or []]
    risk_rows += [(risk, True) for risk in report.get("correlated_evidence") or []]
    for risk, correlated in risk_rows:
        oid = (risk.get("evidence_chain") or {}).get("current_observation_id") or risk.get(
            "evidence_id"
        )
        if oid:
            represented.add(oid)
        detail = details.get(oid) or {}
        health = detail.get("health_assessment") or {}
        field = field_label(risk.get("field"))
        unit = "dBm" if str(risk.get("unit")).lower() == "dbm" else (risk.get("unit") or "")
        obj = identity(risk.get("entity_key"))
        value = display(risk.get("current_value"))
        description = f"{obj}：{field}为 {value} {unit}。"
        limits = health.get("thresholds") or {}
        for side, word in [("low", "下限"), ("high", "上限")]:
            threshold = (limits.get(side) or {}).get("value")
            if threshold is not None:
                description += f"设备{word} {threshold} {unit}。"
        trend = (health.get("trend") or {}).get("status")
        if trend == "persistent_abnormal":
            description += "基线与本期均异常，属于持续异常。"
        else:
            description += "按本次冻结判定列为风险；仅凭此结果不认定本期新增。"
        device = risk.get("device_name") or risk.get("device_id") or "未提供设备名称"
        findings.append(
            [
                "关联证据"
                if correlated
                else ("当前异常" if risk.get("source") == "current_state" else "未关闭风险"),
                device,
                description,
                evidence(oid, risk.get("event_id"), risk.get("evidence_id")),
            ]
        )
        actions.append(
            [
                device,
                f"{obj} / {field}",
                "核对附录原文与设备阈值，确认业务影响后安排处理；勿仅凭数值推断故障原因。",
                "复采同一对象，按原检查项判定并确认异常消除。",
            ]
        )

    for item in (report.get("numeric_trends") or {}).get("items") or []:
        if item.get("status") not in {"recovered", "notice"}:
            continue
        oid = item.get("observation_id")
        if oid in represented:
            continue
        if oid:
            represented.add(oid)
        findings.append(
            [
                "已恢复" if item["status"] == "recovered" else "趋势提示",
                item.get("device_id"),
                f"{identity(item.get('entity_key'))}："
                f"{item.get('label') or field_label(item.get('field'))} "
                f"{display(item.get('baseline_value'))} → {display(item.get('current_value'))} "
                f"{item.get('unit') or ''}。"
                f"{item.get('explanation') or ''}",
                evidence(oid),
            ]
        )
    # Keep other real events, but do not duplicate a current finding or recovery.
    for item in (report.get("structured_event_evidence") or {}).get("items") or []:
        event = item.get("event") or {}
        current, baseline = item.get("current") or {}, item.get("baseline") or {}
        oid = current.get("observation_id")
        if not event.get("event_id") or oid in represented or item.get("current_finding"):
            continue
        if oid:
            represented.add(oid)
        kind = "已恢复" if event.get("event_class") == "recovery" else "变化事项"
        findings.append(
            [
                kind,
                item.get("device_name") or item.get("device_id"),
                f"{identity(item.get('entity_key'))}：{field_label(item.get('field'))}由 "
                f"{display(baseline.get('value'))} 变为 {display(current.get('value'))}。"
                + (
                    "已恢复至判定范围。" if kind == "已恢复" else "已记录变化事件，需结合业务复核。"
                ),
                evidence(event.get("event_id"), item.get("change_id"), oid),
            ]
        )
    for item in (report.get("unstructured_comparison") or {}).get("items") or []:
        device = item.get("device_name") or item.get("device_id")
        findings.append(
            [
                "文本变化",
                device,
                f"{item.get('command') or '命令输出'}："
                f"新增 {display(item.get('added_line_count'))} 行，"
                f"删除 {display(item.get('removed_line_count'))} 行；请核对具体变化及变更授权。",
                evidence(item.get("change_id")),
            ]
        )
        actions.append(
            [
                device,
                item.get("command"),
                "对照原文确认配置变更或日志变化是否符合预期。",
                "核对变更记录并复采相关命令。",
            ]
        )

    gaps = []
    for domain in report.get("domains") or []:
        if domain.get("status") in {"数据不完整", "规则缺失", "无法判断", "需复核"}:
            gaps.append(
                [
                    "补充核验",
                    domain.get("label"),
                    readable_reason(domain.get("reason")),
                    "该范围不能据此确认健康",
                    "补充对应命令、设备阈值或适用规则后重新分析。",
                ]
            )
    for gap in gaps:
        findings.append(["数据缺口", "本次检查范围", f"{gap[1]}：{gap[2]}", "见 4.1"])
    if gaps:
        actions.append(
            [
                "本次检查范围",
                "数据与判定缺口",
                "按 4.1 补充材料及判定依据。",
                "重新分析后核对覆盖状态。",
            ]
        )

    inventory = []
    # Never substitute a fan/module serial for the chassis serial.
    chassis = {}
    for row in observation_groups(source.get("observations") or []):
        if row["type"] == "hardware_component" and any(
            word in row["entity"].lower() for word in ("chassis", "slot 1 cpu", "机框")
        ):
            serial = row["values"].get("serial_number")
            if serial and str(serial).upper() not in {"NONE", "N/A", "NA"}:
                chassis.setdefault(row["device"], set()).add(str(serial))
    for row in report.get("device_inventory_rows") or []:
        serials = chassis.get(row[1], set())
        inventory.append(
            [
                row[1],
                row[2],
                row[3],
                next(iter(serials)) if len(serials) == 1 else "未确认机框序列号",
            ]
        )

    scalar = view["scalars"]
    scalar["meta.report_date"] = date_text(report.get("generated_at"))
    scalar["meta.project"] = metadata.get("project") or "网络设备巡检"
    scalar["meta.period_label"] = metadata.get("period_label") or "巡检问题与变化报告"
    current_count = sum(row[0] in {"当前异常", "未关闭风险"} for row in findings)
    recovered_count = sum(row[0] == "已恢复" for row in findings)
    changes_count = sum(row[0] in {"变化事项", "文本变化", "趋势提示"} for row in findings)
    scalar["narrative.summary"] = (
        f"本次分析列出 {current_count} 项风险、{recovered_count} 项恢复和 "
        f"{changes_count} 项其他变化或趋势提示。"
        "恢复与一般变化单独列示，不计入当前故障。"
    )
    scalar["scope.overview"] = (
        "本报告基于本次提供的巡检材料和已完成的分析结果，"
        f"覆盖 {(report.get('metrics') or {}).get('target_device_count', len(inventory))} 台设备。"
        "正文仅列需要关注的问题、变化及数据缺口，正常指标明细不展开。结论仅反映采集时点及实际覆盖范围。"
    )
    scalar["scope.periods"] = (
        "基线采集："
        + date_text(scalar.get("scope.baseline_capture_time"))
        + "\n本期采集："
        + date_text(scalar.get("scope.current_capture_time"))
    )
    scalar["scope.contact"] = (
        "编制人员：" + scalar["meta.author"] + "；复核人员：" + scalar["meta.reviewer"]
    )
    scalar["scope.method"] = (
        "本报告由已保存的巡检分析结果生成。离线材料分析不登录设备、不修改配置；不据此声称已完成配置备份或在线复检。"
    )
    if not findings:
        findings = [
            [
                "未列出问题",
                "本次检查范围",
                "当前分析未记录可列示问题或变化；不等同于所有设备健康，须结合结论和覆盖限制阅读。",
                "见 3.1 / 4.1",
            ]
        ]
    view["tables"].update(
        inventory=inventory,
        findings=findings,
        quality_gaps=gaps
        or [["—", "覆盖说明", "未记录检查域缺口", "仍以本次实际覆盖范围为限", "参见本节覆盖限制"]],
        remediation=actions
        or [["本次检查范围", "后续核验", "按计划复采并核对覆盖范围。", "依据适用检查项复检。"]],
    )
    return view
