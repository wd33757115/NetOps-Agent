"""Presentation-only projections; never create health conclusions or missing facts."""

import json
from collections import defaultdict

MISSING = "未提供"


def display(value):
    if value is None or value == "":
        return MISSING
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def identity(value):
    try:
        parsed = json.loads(value)
        return " / ".join(map(str, parsed)) if isinstance(parsed, list) else str(parsed)
    except (TypeError, ValueError):
        return display(value)


def observation_groups(observations):
    grouped = {}
    for obs in observations:
        key = (
            obs.get("device_id"),
            obs.get("snapshot_id"),
            obs.get("schema_id"),
            obs.get("entity_type"),
            obs.get("entity_key"),
        )
        row = grouped.setdefault(
            key,
            {
                "device": obs.get("device_name") or obs.get("device_id"),
                "entity": identity(obs.get("entity_key")),
                "type": obs.get("entity_type"),
                "values": {},
                "units": {},
                "conflicts": [],
            },
        )
        field = str(obs.get("field") or obs.get("observation_type") or "")
        window = (obs.get("dimensions") or {}).get("window")
        if obs.get("observation_type") == "device.cpu.utilization":
            field = {"5s": "FIVE_SEC", "1m": "ONE_MIN", "5m": "FIVE_MIN"}.get(window, field)
        if field in row["values"] and row["values"][field] != obs.get("value"):
            row["conflicts"].append(field)
            row["values"][field] = "来源冲突，需核对"
        else:
            row["values"][field] = obs.get("value")
        row["units"][field] = obs.get("unit")
    return list(grouped.values())


def project_report(report, source=None, metadata=None):
    source = source or {}
    metadata = metadata or {}
    metrics = report.get("metrics") or {}
    assessment = report.get("assessment") or {}
    periods = {p.get("role"): p for p in source.get("periods") or []}
    limitations = (
        "；".join(report.get("limitations") or []) or "仅适用于本次提供材料及已完成判定的范围"
    )
    scalar = {
        "conclusion.text": assessment.get("status"),
        "conclusion.basis": assessment.get("basis"),
        "narrative.summary": (
            f"{assessment.get('status', '无法判断')}。{assessment.get('basis', '')}"
        ),
        "narrative.limitations": limitations,
        "meta.report_date": report.get("generated_at"),
        "meta.template_version": "v1.2",
        "trace.template_version": "v1.2",
        "trace.run_id": report.get("patrol_batch_id"),
        "trace.result_version": (report.get("evidence", {}).get("analysis") or {}).get("revision"),
        "trace.standards_version": (report.get("evidence", {}).get("analysis") or {}).get(
            "rule_bundle_digest"
        ),
        "scope.current_capture_time": periods.get("current", {}).get("observed_at"),
        "scope.baseline_capture_time": periods.get("baseline", {}).get("observed_at"),
        "scope.analysis_time": (report.get("evidence", {}).get("analysis") or {}).get(
            "completed_at"
        ),
        "scope.source_purpose": "离线材料分析；不代表生成报告时的设备状态",
        "scope.disclosure": limitations,
        "scope.method": "用户提供离线命令输出，按冻结分析结果生成",
        "scope.difference": (
            f"目标 {metrics.get('target_device_count', 0)} 台，"
            f"执行完成 {metrics.get('completed_device_count', 0)} 台；完成不等于健康。"
        ),
        "backup.disclosure": "未核实备份完整性；采集配置文本不等于备份验证完成。",
        "comparison.scope": "按本次选定的基线及本期材料比较；没有基线的项目不能表述为无变化。",
        "issues.review_notes": "风险事项按系统判定展示，业务影响与处置方案需人工复核。",
        "resources.gaps": limitations,
    }
    for key in [
        "project",
        "period_label",
        "recipient",
        "issuer",
        "author",
        "reviewer",
        "report_number",
        "revision",
    ]:
        scalar["meta." + key] = metadata.get(key) or "未填写"
    raw = report.get("unstructured_comparison") or {}
    scalar["changes.summary"] = (
        f"采集 {raw.get('collected_output_count', 0)} 项文本输出，尚未完成文本比较。"
        if not raw.get("compared_output_count")
        else (
            f"已比较 {raw['compared_output_count']} 项文本输出，"
            f"其中 {raw.get('changed_output_count', 0)} 项有变化。文本变化不自动等同于故障。"
        )
    )
    if raw.get("comparison_coverage_known") is False:
        scalar["changes.summary"] = (
            f"已采集 {raw.get('collected_output_count', 0)} 项文本输出，"
            f"现有分析记录 {raw.get('changed_output_count', 0)} 项变化。"
            "该历史分析未保存完整比较覆盖统计，不能据此确认全部已比较或没有变化。"
        )
    tables = defaultdict(list)
    numeric_trends = report.get("numeric_trends") or {}
    if numeric_trends:
        scalar["changes.summary"] += "\n" + numeric_trends["summary"]
        for item in numeric_trends.get("items", []):
            scalar["changes.summary"] += (
                f"\n{item['device_id']} / {identity(item['entity_key'])} / {item['label']}："
                f"{item['baseline_value']} → {item['current_value']} {item['unit']}。"
                f"{item['explanation']}"
            )
    all_risks = (report.get("risks") or []) + (report.get("correlated_evidence") or [])
    abnormal_devices = {r.get("device_id") for r in all_risks if r.get("device_id")}
    pending_devices = {
        r.get("device_id")
        for r in source.get("runs") or []
        if r.get("device_id")
        and (
            r.get("status") != "completed"
            or any(
                d.get("status") in {"数据不完整", "规则缺失", "无法判断", "需复核"}
                for d in r.get("domain_checks") or []
            )
        )
    }
    tables["summary"] = [
        [
            metrics.get("target_device_count"),
            metrics.get("completed_device_count"),
            len(abnormal_devices)
            if "current_risk_count" in metrics and all(r.get("device_id") for r in all_risks)
            else "未计算",
            len(pending_devices) if source.get("runs") else "未计算",
        ]
    ]
    tables["devices"] = [
        [r[1], f"{r[2]} / {r[3]}", MISSING, MISSING, MISSING]
        for r in report.get("device_inventory_rows") or []
    ]
    observations = source.get("observations") or []
    for row in observation_groups(observations):
        v, dev, obj = row["values"], row["device"], row["entity"]

        def get(*keys):
            return next((v[k] for k in keys if k in v and v[k] is not None), None)

        entity = row["type"] or ""
        if entity == "cpu_scope":
            tables["cpu"].append(
                [
                    dev,
                    obj,
                    get("FIVE_SEC", "cpu_5s"),
                    get("ONE_MIN", "cpu_1m"),
                    get("FIVE_MIN", "cpu_5m"),
                ]
            )
        elif entity == "memory_scope":
            total, used, rate = (
                get("memory_total_bytes"),
                get("memory_used_bytes"),
                get("memory_usage_percent"),
            )
            tables["memory"].append(
                [
                    dev,
                    obj,
                    f"{total} B" if total is not None else None,
                    f"{used} B" if used is not None else None,
                    f"{rate:.2f}%"
                    if isinstance(rate, (int, float))
                    else (f"{rate}%" if rate is not None else None),
                ]
            )
        elif entity == "environment_fan":
            parts = obj.split(" / ")
            tables["fan"].append(
                [dev, parts[0], " / ".join(parts[1:]) or obj, get("fan_status", "fan_state")]
            )
        elif entity == "environment_power":
            parts = obj.split(" / ")
            tables["power"].append(
                [
                    dev,
                    parts[0],
                    " / ".join(parts[1:]) or obj,
                    get("power_supply_status", "power_state"),
                    get("mode", "power_mode"),
                    get("current", "current_a"),
                    get("voltage", "voltage_v"),
                    get("power", "power_w"),
                ]
            )
        elif entity == "temperature_sensor":
            tables["temperature"].append(
                [
                    f"{dev} / {obj}",
                    get("temperature_celsius"),
                    get("temperature_high_warning_celsius"),
                    get("temperature_high_alarm_celsius"),
                    "见判定结果及缺口",
                ]
            )
        elif "optical" in entity:
            tables["interfaces"].append(
                [
                    f"{dev} / {obj}",
                    obj.split(" / ")[1] if "channel" in entity and " / " in obj else "接口级",
                    get("temperature_celsius"),
                    get("rx_power_dbm"),
                    get("tx_power_dbm"),
                ]
            )
        elif entity == "interface" and any(
            k in v for k in ["input_errors", "output_errors", "total_pkts", "TOTAL_PKTS"]
        ):
            tables["interface_counters"].append(
                [
                    dev,
                    obj,
                    get("total_pkts", "TOTAL_PKTS"),
                    get("broadcast_pkts", "BROADCAST_PKTS"),
                    get("multicast_pkts", "MULTICAST_PKTS"),
                    get("err_pkts", "ERR_PKTS"),
                ]
            )
        elif entity == "hardware_component":
            tables["hardware"].append(
                [dev, obj, get("model"), get("serial_number"), get("version", "state")]
            )
        elif entity.startswith(("stack", "vrrp", "mlag")):
            tables["redundancy"].append(
                [
                    f"{dev} / {obj}",
                    entity,
                    get("member_status", "state", "role"),
                    MISSING,
                    "见分析结果",
                ]
            )
    for domain in report.get("domains") or []:
        label = domain.get("label")
        tables["standards"].append(
            [
                label,
                f"{domain.get('observation_count', 0)} 条字段观测",
                domain.get("reason"),
                scalar["trace.standards_version"],
            ]
        )
        if domain.get("status") in ["数据不完整", "规则缺失", "无法判断", "需复核"]:
            tables["gaps"].append(
                [label, domain.get("status"), domain.get("reason"), "需补充数据或判定依据"]
            )
    for index, risk in enumerate(report.get("risk_groups") or [], 1):
        tables["issues"].append(
            [
                f"R{index:03d} / {risk.get('severity')}",
                f"{risk.get('risk_name')}；{risk.get('device_name')}；{risk.get('objects')}",
                risk.get("criterion"),
                "核对原始证据后确定处置",
            ]
        )
        tables["issue_evidence"].append(
            [
                f"R{index:03d}",
                f"{risk.get('device_name')} / {risk.get('objects')}",
                f"{risk.get('current_values')}；{risk.get('criterion')}",
                MISSING,
                "见 CLI 证据附录",
            ]
        )
    # The user's amended v1 priorities table has three columns.
    tables["priorities"] = [[r[1], "需人工核实业务影响", r[3]] for r in tables["issues"]]
    for i in raw.get("items") or []:
        tables["changes"].append(
            [
                i.get("device_name") or i.get("device_id"),
                i.get("command"),
                f"新增 {display(i.get('added_line_count'))} 行；"
                f"删除 {display(i.get('removed_line_count'))} 行（采用本任务比较规则）",
                f"文本变化，需复核；变更 {display(i.get('change_id'))}，见附录 F",
            ]
        )
    for i in report.get("recommendations") or []:
        tables["actions"].append(
            ["系统建议", i.get("action"), i.get("priority"), "待确认", "待确认", "依据原检查项复检"]
        )
    evidence_groups = {}
    for obs in observations:
        key = (obs.get("device_id"), obs.get("snapshot_id"), obs.get("command"))
        entry = evidence_groups.setdefault(key, {"sample": obs, "fields": set()})
        entry["fields"].add(str(obs.get("field") or "未提供字段"))
    for index, entry in enumerate(evidence_groups.values(), 1):
        obs = entry["sample"]
        tables["evidence"].append(
            [
                f"EV-{index:05d}",
                "、".join(sorted(entry["fields"])),
                f"{display(obs.get('device_name') or obs.get('device_id'))} / "
                f"{display(obs.get('command'))} / {display(obs.get('snapshot_id'))}",
                obs.get("observed_at"),
            ]
        )
    for key, domain in [
        ("assets", "device"),
        ("resources", "cpu"),
        ("temperature", "environment"),
        ("components", "fan"),
        ("interfaces", "optical"),
        ("redundancy", "redundancy"),
    ]:
        domains = {
            "resources": {"cpu", "memory"},
            "components": {"fan", "power"},
            "interfaces": {"interface", "optical"},
        }.get(key, {domain})
        matches = [d for d in report.get("domains") or [] if d.get("domain") in domains]
        scalar[key + ".summary"] = (
            "；".join(
                f"{d.get('label')}：{d.get('status')}。{d.get('reason', '')}" for d in matches
            )
            or "本次未形成对应判定，不能据此认定正常。"
        )
    return {"scalars": scalar, "tables": dict(tables), "observations": observations}
