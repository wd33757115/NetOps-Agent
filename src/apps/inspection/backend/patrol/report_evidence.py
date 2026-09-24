"""Deterministic, traceable CLI excerpts. No generated text is treated as raw CLI."""

import json
import re
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher


def _parts(key):
    try:
        value = json.loads(key)
        return [str(v) for v in value] if isinstance(value, list) else [str(value)]
    except (ValueError, TypeError):
        return [str(key or "")]


def _token(value):
    value = value.lower().replace("-", "")
    for short, long in [
        ("xge", "tengigabitethernet"),
        ("ge", "gigabitethernet"),
        ("twentyfivegige", "twentyfivegigabitethernet"),
        ("hundredgige", "hundredgigabitethernet"),
    ]:
        if re.match(short + r"\d", value):
            return long + value[len(short) :]
    return value


PORT = re.compile(r"[A-Za-z][A-Za-z-]*\d+(?:/\d+)+(?:\.\d+)?")


def locate_object(text, key):
    """Only slice when an exact, unique object boundary can be proved."""
    lines = str(text or "").splitlines()
    identities = _parts(key)
    port = next((p for p in identities if PORT.fullmatch(p)), None)
    if port:
        boundaries = []
        for index, line in enumerate(lines):
            matches = PORT.findall(line)
            if matches and (
                line.lstrip().startswith(matches[0]) or re.match(r"\s*interface\s+", line, re.I)
            ):
                boundaries.append((index, _token(matches[0])))
        matches = [(i, row) for i, row in enumerate(boundaries) if row[1] == _token(port)]
        if len(matches) == 1:
            position, (start, _) = matches[0]
            end = boundaries[position + 1][0] if position + 1 < len(boundaries) else len(lines)
            selected = list(range(start, end))
            brief_headers = [
                i
                for i in range(start)
                if re.match(r"\s*Interface\s+(?:Link|PHY|Physical|Status)\b", lines[i], re.I)
            ]
            if (
                brief_headers
                and not re.search(r"diagnostic|current\s+state|transceiver", lines[start], re.I)
                and re.match(r"\S+\s+(?:UP|DOWN|ADM|\*down)\b", lines[start].strip(), re.I)
            ):
                # Brief output is a row table. The next port block can be far
                # away (VLAN/aggregate rows have no slash-delimited port name).
                selected = [brief_headers[-1], start]
            # A channel table is cropped only with explicit channel identity and header.
            channel = identities[1] if len(identities) > 1 else None
            headers = [i for i in selected if re.search(r"\[?channel\]?", lines[i], re.I)]
            if channel and len(headers) == 1:
                header = headers[0]
                numeric_rows = [
                    i for i in selected if i > header and re.match(r"\s*\d+\s", lines[i])
                ]
                rows = [i for i in numeric_rows if lines[i].split()[0] == channel]
                if len(rows) == 1:
                    selected = [i for i in selected if i not in numeric_rows or i == rows[0]]
                else:
                    return [], "通道无法唯一定位，请核对完整命令"
            return selected, "已定位对象；未展示其他对象"
    # Generic single-row records: every identity part must be a complete token.
    if identities and all(identities):
        candidates = [
            i
            for i, line in enumerate(lines)
            if all(re.search(r"(?<![\w/])" + re.escape(p) + r"(?![\w/])", line) for p in identities)
        ]
        if len(candidates) == 1:
            i = candidates[0]
            return list(
                range(max(0, i - 2), min(len(lines), i + 3))
            ), "已定位记录及上下文；字段位置需核对"
    return [], "对象无法唯一定位；请查看完整命令证据"


def text_hunks(baseline, current, context=3):
    old, new = str(baseline or "").splitlines(), str(current or "").splitlines()
    matcher = SequenceMatcher(
        None, [s.rstrip() for s in old], [s.rstrip() for s in new], autojunk=False
    )
    result = []
    for group in matcher.get_grouped_opcodes(context):
        rows = []
        for op, a, b, c, d in group:
            for i in range(max(b - a, d - c)):
                rows.append(
                    {
                        "baseline": {"line": a + i + 1, "text": old[a + i]} if a + i < b else None,
                        "current": {"line": c + i + 1, "text": new[c + i]} if c + i < d else None,
                        "change": op,
                    }
                )
        result.append(rows)
    return result


def object_rows(item):
    sides = {}
    notes = []
    for side in ["baseline", "current"]:
        evidence = item.get(side) or {}
        text = evidence.get("raw_text")
        if text is None:
            sides[side] = []
            notes.append(f"{side} 原文未提供")
            continue
        indices, note = locate_object(text, item.get("entity_key"))
        lines = text.splitlines()
        sides[side] = [{"line": i + 1, "text": lines[i]} for i in indices]
        notes.append(note)
    old, new = sides["baseline"], sides["current"]
    # Alignment is performed inside the verified object, never across other ports.
    matcher = SequenceMatcher(
        None, [r["text"].rstrip() for r in old], [r["text"].rstrip() for r in new], autojunk=False
    )
    rows = []
    for op, a, b, c, d in matcher.get_opcodes():
        for i in range(max(b - a, d - c)):
            rows.append(
                {
                    "baseline": dict(old[a + i]) if a + i < b else None,
                    "current": dict(new[c + i]) if c + i < d else None,
                    "change": op,
                }
            )
    return rows, "；".join(dict.fromkeys(notes))


def locate_field_value(rows, field, value):
    """Locate a field in verified current-side rows without judging its health."""
    if value is None:
        return []
    pattern = re.compile(r"(?<![\w.+-])" + re.escape(str(value)) + r"(?![\w.])", re.I)
    numeric_value = None
    try:
        numeric_value = Decimal(str(value))
        if numeric_value.is_finite():
            pattern = re.compile(r"(?<![\w.+-])[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?(?![\w.])")
        else:
            numeric_value = None
    except InvalidOperation:
        pass
    candidates = []
    column = None
    module_header = False
    labels = {
        "interface_oper_status": r"(?:current|line protocol)\s+state",
        "oper_status": r"(?:current|line protocol)\s+state",
        "rx_power_dbm": r"RX\s+power",
        "tx_power_dbm": r"TX\s+power",
    }
    for pair in rows:
        row = pair.get("current")
        if not row:
            continue
        text = row["text"]
        if re.match(r"\s*Interface\s+(?:Link|PHY|Physical|Status)\b", text, re.I):
            module_header = False
            column = 1 if field in {"oper_status", "interface_oper_status"} else None
            continue
        if re.search(r"channel.*Bias.*RX.*TX", text, re.I):
            module_header = False
            column = {"rx_power_dbm": 2, "tx_power_dbm": 3}.get(field)
            continue
        if re.search(r"module.*Temp.*Voltage", text, re.I):
            module_header = True
            column = {"temperature_celsius": 0, "voltage_v": 1}.get(field)
            continue
        if re.match(r"\s*Temp.*Voltage.*Bias.*RX.*TX", text, re.I):
            module_header = False
            column = {
                "temperature_celsius": 0,
                "voltage_v": 1,
                "bias_ma": 2,
                "rx_power_dbm": 3,
                "tx_power_dbm": 4,
            }.get(field)
            continue
        matches = list(pattern.finditer(text))
        if numeric_value is not None:
            matches = [match for match in matches if Decimal(match.group()) == numeric_value]
        if column is not None and (
            re.match(r"\s*-?\d+(?:\.\d+)?\s", text)
            or (field in {"oper_status", "interface_oper_status"} and PORT.match(text.lstrip()))
        ):
            tokens = list(re.finditer(r"\S+", text))
            data_column = column + (1 if module_header and len(tokens) == 3 else 0)
            if len(tokens) > data_column:
                candidates.extend(
                    (row, m.span())
                    for m in matches
                    if tokens[data_column].start() <= m.start()
                    and m.end() <= tokens[data_column].end()
                )
        elif field in labels and re.search(labels[field] + r"\s*[:=]", text, re.I):
            candidates.extend((row, m.span()) for m in matches)
    return candidates


def mark_anomaly(rows, item):
    event = item.get("event") or {}
    if event.get("event_class") == "change" and not item.get("current_finding"):
        return "字段变化事件：仅标示差异，不作为异常标红"
    event_type = str(event.get("event_type") or "")
    recovery = bool(re.search(r"recover|restore|(?:^|_)up$", event_type, re.I))
    # Recovery does not itself prove that the old value was independently abnormal.
    if recovery:
        return "恢复事件：未重新判定基线，不自动标红"
    if not (event.get("event_id") or item.get("current_finding")):
        return "未提供异常判定，不自动标红"
    value = (item.get("current") or {}).get("value")
    if value is None:
        return "异常字段缺少可定位原始值"
    candidates = locate_field_value(rows, str(item.get("field") or ""), value)
    if len(candidates) == 1:
        row, span = candidates[0]
        row["anomaly_span"] = list(span)
        return "红色为当前异常值；其他颜色仅表示文本变化"
    return "异常值出现多处或原文格式不同，未精确标红；请按字段核对"


def build_excerpts(report, *, include_unmatched_changes=False):
    result = []
    structured = (report.get("structured_event_evidence") or {}).get("items") or []
    sources = [
        ("event", i)
        for i in structured
        if (i.get("event") or {}).get("event_id")
        or i.get("current_finding")
        or i.get("numeric_trend")
        or include_unmatched_changes
    ]
    sources += [
        ("text", i) for i in (report.get("unstructured_comparison") or {}).get("items") or []
    ]
    merged = {}
    for kind, original in sources:
        item = dict(original)
        baseline = item.get("baseline") or item.get("baseline_evidence") or {}
        current = item.get("current") or item.get("current_evidence") or {}
        item.update(baseline=baseline, current=current)
        source_kind = (
            "change_event"
            if (item.get("event") or {}).get("event_id")
            else "current_finding"
            if item.get("current_finding")
            else "numeric_trend"
            if item.get("numeric_trend")
            else "structured_change" if kind == "event" else "text_change"
        )
        key = (
            kind,
            source_kind,
            item.get("device_id"),
            baseline.get("snapshot_id"),
            current.get("snapshot_id"),
            item.get("command"),
            item.get("entity_key") if kind == "event" else None,
        )
        event = item.get("event") or {}
        reference = (
            event.get("event_id") or item.get("change_id") or item.get("finding_id") or "未提供编号"
        )
        match_references = [
            str(value)
            for value in (
                reference,
                item.get("change_id"),
                event.get("event_id"),
                item.get("finding_id"),
                current.get("observation_id"),
            )
            if value
        ]
        if kind == "event":
            rows, note = object_rows(item)
            if not any(r.get("baseline") for r in rows) or not any(r.get("current") for r in rows):
                comparison = "unavailable"
                comparison_label = "对象摘录不完整，无法判断两期是否一致"
            elif all(r["change"] == "equal" for r in rows):
                comparison = "unchanged"
                comparison_label = "两期对象摘录一致（忽略行尾空白）"
            else:
                comparison = "changed"
                comparison_label = "两期对象摘录存在差异，具体字段以关联判定为准"
            source_label = (
                "当前状态异常：由当前值命中规则列入，不代表本期新增变化事件"
                if source_kind == "current_finding"
                else "数值趋势：" + str(item.get("trend_explanation") or "仅作趋势说明，不代表故障")
                if source_kind == "numeric_trend"
                else "原始观测对比：展示采集证据，不代表故障或事件判定"
                if source_kind == "structured_change"
                else "变化事件：由关联事件列入"
            )
            note = f"{source_label}；{comparison_label}。{note}"
            health = item.get("health_assessment") or {}
            if health:
                checks = []
                for side, limit in (health.get("thresholds") or {}).items():
                    checks.append(f"设备{'上限' if side == 'high' else '下限'} {limit['value']}")
                if health.get("relative_deviation") is not None:
                    checks.append(
                        f"基线值 {health['baseline_value']}，"
                        f"相对偏差 {health['relative_deviation']:.2%}，"
                        f"允许 {health['max_relative_deviation']:.0%}"
                    )
                if health.get("baseline_mode") == "trend_only":
                    checks.append("当前健康按设备阈值判定，基线变化仅用于趋势说明")
                if health.get("missing"):
                    checks.append("部分判定依据缺失，不能据此断言全部正常")
                note += "；" + "；".join(checks)
            note += "；" + mark_anomaly(rows, item)
            groups = [rows]
        else:
            comparison = "changed"
            groups = text_hunks(baseline.get("raw_text"), current.get("raw_text"))
            note = "仅原文差异摘录；不作为新增事件判定。保留原始行号，未展示无变化区段"
            if baseline.get("raw_text") is None or current.get("raw_text") is None:
                comparison = "unavailable"
                groups = [[]]
                note = "一侧原文未提供，不能判断为新增、删除或无变化"
        if key in merged:
            entry = merged[key]
            entry["references"].append(str(reference))
            entry["match_references"] = list(
                dict.fromkeys(
                    [
                        *entry["match_references"],
                        *match_references,
                    ]
                )
            )
            entry["fields"].append(str(item.get("field") or ""))
            # Union highlights for additional events without discarding the first event.
            marks = (
                {
                    r["current"]["line"]: r["current"]["anomaly_span"]
                    for r in groups[0]
                    if (r.get("current") or {}).get("anomaly_span")
                }
                if groups
                else {}
            )
            for row in entry["rows"]:
                if row.get("current") and row["current"]["line"] in marks:
                    row["current"].setdefault("anomaly_spans", []).append(
                        marks[row["current"]["line"]]
                    )
            continue
        for group in groups:
            entry = {
                "id": f"CLI-{len(result) + 1:03d}",
                "kind": kind,
                "source_kind": source_kind,
                "excerpt_comparison": comparison,
                "references": [str(reference)],
                "match_references": list(dict.fromkeys(match_references)),
                "fields": [str(item.get("field") or "")],
                "device": item.get("device_name") or item.get("device_id") or "未提供",
                "command": item.get("command") or "未提供",
                "entity": item.get("entity_key") or "",
                "baseline": {k: baseline.get(k) for k in ["snapshot_id", "observed_at"]},
                "current": {k: current.get(k) for k in ["snapshot_id", "observed_at"]},
                "rows": group,
                "note": note,
                "full_source": {
                    "baseline": baseline.get("raw_text"),
                    "current": current.get("raw_text"),
                },
            }
            result.append(entry)
            merged[key] = entry
    return result
