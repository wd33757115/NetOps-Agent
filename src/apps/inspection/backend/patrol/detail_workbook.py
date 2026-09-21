"""Deterministic, check-item-based Excel export, using the project's Excel writer.

The supplied patrol_tool_opt.py is the formatting reference. No model calls,
field aliases, live catalog reinterpretation or new health decisions occur here.
"""

import json
import math
import re
from collections import defaultdict
from difflib import SequenceMatcher
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from src.core.patrol.report_evidence import build_excerpts
from src.core.patrol.report_findings import date_text, project_findings
from src.core.patrol.report_view import project_report

RED = "C00000"
MAX_ROWS = 1_048_576


def standard_fields(contract):
    """Names/order come from the frozen check item, never from a guessed alias."""
    identities = [f for f in contract.get("identity_dimensions") or [] if f != "device_id"]
    fields = list(identities)
    for observation in contract.get("observations") or []:
        fields.append(observation.get("canonical_field"))
        fields.extend(observation.get("dimensions") or [])
        fields.extend(observation.get("context_fields") or [])
        fields.extend(
            v.get("field_name")
            for v in (observation.get("threshold_references") or {}).values()
            if isinstance(v, dict)
        )
    return identities, list(dict.fromkeys(f for f in fields if f and f != "device_id"))


def record_sets(value):
    if isinstance(value, list):
        return {"default": value}
    if isinstance(value, dict) and isinstance(value.get("record_sets"), dict):
        return value["record_sets"]
    return {}


def _literal(value):
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, int) and abs(value) >= 10**15:
        return str(value)
    if isinstance(value, str):
        # XML cannot represent control characters; retain a visible escape.
        value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", lambda m: f"\\x{ord(m[0]):02x}", value)
        if len(value) > 32767:
            raise ValueError("结构化单元格超过 Excel 长度限制，请核对原始输出")
    return value


def _typed(value, field, contract):
    # Preserve identifiers, large counters and TextFSM strings without precision loss.
    spec = next(
        (s for s in contract.get("observations") or [] if s.get("canonical_field") == field), {}
    )
    numeric = spec.get("value_type") in {"number", "integer"} or spec.get("semantic_type") in {
        "gauge",
        "counter",
    }
    if not numeric or value in (None, ""):
        return value
    if isinstance(value, str) and re.fullmatch(r"-?\d+(?:\.\d+)?", value):
        if len(value.replace("-", "").replace(".", "").lstrip("0")) <= 15:
            return float(value) if "." in value else int(value)
    return value


def _same(a, b):
    return (a is None and b is None) or (a is not None and b is not None and str(a) == str(b))


def canonical_rows(snapshot, definition):
    """Read complete validated records, never reconstruct them from state observations."""
    identities, fields = standard_fields(definition["contract"])
    if "detail_records" in snapshot:
        sets = {"canonical": snapshot["detail_records"].get(definition["version_id"], [])}
    else:
        sets = record_sets(snapshot.get("structured_json"))
    result = []
    for name, records in sets.items():
        for record in records:
            if not isinstance(record, dict):
                continue
            if not all(k in record for k in identities):
                continue
            if not any(k in record for k in fields if k not in identities):
                continue
            result.append(
                {
                    "values": {f: record.get(f) for f in fields},
                    "entity": [record.get(f) for f in identities],
                    "set": name,
                }
            )
    return result


def _problem_index(report, source):
    view = project_findings(report, source, {}, project_report(report, source))
    excerpts = {e["id"]: e for e in build_excerpts(report)}
    result = defaultdict(list)
    for row in view["tables"]["findings"]:
        for ref in row[3].split("、"):
            entry = excerpts.get(ref)
            if entry:
                entity = (
                    tuple(str(v) for v in json.loads(entry["entity"]))
                    if entry["entity"].startswith("[")
                    else (entry["entity"],)
                )
                for side in ("baseline", "current"):
                    sid = entry[side].get("snapshot_id")
                    if sid:
                        result[(sid, entity)].append(row[2])
    return result


def _sheet_name(name, used):
    clean = re.sub(r"[\x00-\x1f\\/*?:\[\]]", "_", name).strip(" '") or "检查项"
    clean = clean[:31]
    candidate, n = clean, 2
    while candidate.casefold() in used:
        suffix = f" ({n})"
        candidate = clean[: 31 - len(suffix)] + suffix
        n += 1
    used.add(candidate.casefold())
    return candidate


def _write(ws, values, red=()):
    if ws.max_row >= MAX_ROWS:
        raise ValueError("详细数据超过 Excel 单页行数限制，请缩小巡检范围")
    row = ws.max_row + 1
    for index, value in enumerate(values, 1):
        cell = ws.cell(row, index, _literal(value))
        if isinstance(cell.value, str):
            cell.data_type = "s"  # literal CLI, including =, +, @; never an Excel formula
        cell.font = Font(name="Microsoft YaHei", size=10, color=RED if index in red else "243247")
        cell.alignment = Alignment(vertical="top", wrap_text=True)
        cell.border = Border(bottom=Side(style="hair", color="DEE5ED"))
        if row % 2 == 0:
            cell.fill = PatternFill("solid", fgColor="F5F8FC")
    return row


def _text_rows(ws, base, now, issue, columns):
    old = (base or {}).get("raw_text")
    current = (now or {}).get("raw_text")
    old_lines = old.splitlines() if old is not None else []
    new_lines = current.splitlines() if current is not None else []
    operations = SequenceMatcher(None, old_lines, new_lines, autojunk=False).get_opcodes()
    for op, a, b, c, d in operations or [("equal", 0, 0, 0, 0)]:
        for index in range(max(b - a, d - c, 1)):
            left = old_lines[a + index] if a + index < b else ""
            right = new_lines[c + index] if c + index < d else ""
            # Split pathological single lines as well as multi-line command blocks.
            for offset in range(0, max(len(left), len(right), 1), 800):
                changed = op != "equal" and old is not None and current is not None
                snapshot = now or base
                _write(
                    ws,
                    [
                        snapshot["device_id"],
                        snapshot["command"],
                        issue,
                    ]
                    + [None] * columns
                    + [left[offset : offset + 800], right[offset : offset + 800]],
                    red=(4 + columns, 5 + columns) if changed else (),
                )


def render_detail_workbook(report, source, data):
    wb = Workbook()
    wb.remove(wb.active)
    definitions = {v: {**d, "version_id": v} for v, d in data["definitions"].items()}
    notes = []
    problems = _problem_index(report, source)
    groups = defaultdict(list)
    for snapshot in data["snapshots"]:
        meta = snapshot.get("metadata_json") or {}
        versions = list(
            dict.fromkeys(
                meta.get("check_item_version_ids")
                or [b.get("check_item_version_id") for b in meta.get("check_item_bindings") or []]
            )
        )
        if not versions:
            notes.append(
                [
                    snapshot["device_id"],
                    snapshot["command"],
                    "未关联检查项",
                    "未建立检查项关联；原文请下载任务原始输出。",
                ]
            )
        for version in versions:
            definition = definitions.get(version)
            if definition:
                groups[(definition["id"], definition["name"])].append((snapshot, definition))
            else:
                notes.append(
                    [
                        snapshot["device_id"],
                        snapshot["command"],
                        "检查项定义缺失",
                        "历史检查项定义已不可用；原文请下载任务原始输出。",
                    ]
                )
    used = set()
    for (_, name), items in groups.items():
        # Union by recorded check-item versions, preserving declared order.
        fields = list(
            dict.fromkeys(
                f
                for _, definition in items
                if definition
                for f in standard_fields(definition["contract"])[1]
            )
        )
        structured = any(d and d["mode"] == "structured" for _, d in items)
        ws = wb.create_sheet(_sheet_name(name, used))
        ws.append(
            ["设备ID", "命令", "问题描述"]
            + (fields if structured else ["基线 CLI 原文", "当前 CLI 原文"])
        )
        pairs = defaultdict(lambda: defaultdict(list))
        for snapshot, definition in items:
            pairs[
                (snapshot["device_id"], snapshot.get("canonical_command") or snapshot["command"])
            ][snapshot["period"]].append((snapshot, definition))
        for periods in pairs.values():
            current, baseline = periods.get("current") or [], periods.get("baseline") or []
            if len(current) > 1 or len(baseline) > 1:
                snapshot = (current or baseline)[0][0]
                notes.append(
                    [
                        snapshot["device_id"],
                        snapshot["command"],
                        name,
                        "同一设备命令存在多个采集结果，未自动配对。",
                    ]
                )
                continue
            now, definition = current[0] if current else (None, None)
            base, old_definition = baseline[0] if baseline else (None, None)
            definition = definition or old_definition
            if not structured:
                matched = [
                    p for (sid, _), ps in problems.items() if sid == (now or base)["id"] for p in ps
                ]
                _text_rows(ws, base, now, "；".join(dict.fromkeys(matched)), 0)
                if not base or not now:
                    snapshot = now or base
                    notes.append(
                        [
                            snapshot["device_id"],
                            snapshot["command"],
                            name,
                            "缺少一期原文，不能判断文本变化。",
                        ]
                    )
                continue
            if not now:
                notes.append(
                    [
                        base["device_id"],
                        base["command"],
                        name,
                        "本期未采集，未填入基线值冒充当前数据。",
                    ]
                )
                continue
            rows = canonical_rows(now, definition)
            old_rows = canonical_rows(base, old_definition or definition) if base else []
            if not rows:
                notes.append(
                    [
                        now["device_id"],
                        now["command"],
                        name,
                        (now.get("detail_errors") or {}).get(
                            definition["version_id"],
                            "本期没有可用标准记录；无法判断健康，需检查采集与解析结果。",
                        ),
                    ]
                )
                continue
            ids, _ = standard_fields(definition["contract"])
            for declaration in definition["contract"].get("observations") or []:
                policy = declaration.get("health_policy") or {}
                metric = declaration.get("canonical_field")
                if policy and all(row["values"].get(metric) in (None, "") for row in rows):
                    window = (declaration.get("fixed_dimensions") or {}).get("window")
                    label = "五分钟使用率" if window == "5m" else "健康判定测量值"
                    notes.append(
                        [
                            now["device_id"],
                            now["command"],
                            name,
                            f"未采集{label}：{metric}；不能用瞬时值或容量推算值替代。",
                        ]
                    )
                threshold_fields = list(
                    dict.fromkeys(
                        f
                        for values in (policy.get("device_thresholds") or {}).values()
                        for f in values
                    )
                )
                if threshold_fields and all(
                    row["values"].get(f) in (None, "") for row in rows for f in threshold_fields
                ):
                    notes.append(
                        [
                            now["device_id"],
                            now["command"],
                            name,
                            "未采集设备阈值：" + "、".join(threshold_fields) + "；空值不代表正常。",
                        ]
                    )
            dimensions = list(
                dict.fromkeys(
                    f
                    for o in definition["contract"].get("observations") or []
                    for f in o.get("dimensions") or []
                )
            )
            key_fields = list(dict.fromkeys(ids + dimensions))

            def row_key(row):
                return tuple(str(row["values"].get(f)) for f in key_fields)

            old_map, new_map = defaultdict(list), defaultdict(list)
            for row in old_rows:
                old_map[row_key(row)].append(row)
            for row in rows:
                new_map[row_key(row)].append(row)
            for key in list(new_map) + [k for k in old_map if k not in new_map]:
                chosen = new_map.get(key) or old_map[key]
                for row in chosen:
                    entity = tuple(str(v) for v in row["entity"])
                    descriptions = problems.get((now["id"], entity), [])
                    issue = "；".join(dict.fromkeys(descriptions))
                    comparison_note = ""
                    old = old_map.get(key, [])
                    ambiguous = (
                        len(old) > 1
                        or len(new_map.get(key, [])) > 1
                        or any(row["values"].get(f) in (None, "") for f in ids)
                    )
                    red = []
                    if ambiguous:
                        comparison_note = "对象标识不唯一，未自动比较。"
                    elif not base or not old_definition or not old_rows:
                        comparison_note = "未提供可比基线。"
                    elif standard_fields(old_definition["contract"]) != standard_fields(
                        definition["contract"]
                    ):
                        comparison_note = "两期检查项字段定义不同，未自动比较。"
                    elif not new_map.get(key):
                        notes.append(
                            [
                                now["device_id"],
                                now["command"],
                                name,
                                "本期解析结果未包含对象 "
                                + " / ".join(entity)
                                + "；需核对覆盖，未填入基线值。",
                            ]
                        )
                        continue
                    elif not old:
                        comparison_note = "本期新增解析对象，需结合覆盖范围核对。"
                        red = list(range(4, len(fields) + 4))
                    else:
                        red = [
                            index + 4
                            for index, f in enumerate(fields)
                            if not _same(row["values"].get(f), old[0]["values"].get(f))
                        ]
                    if comparison_note:
                        notes.append([now["device_id"], now["command"], name, comparison_note])
                    _write(
                        ws,
                        [
                            now["device_id"],
                            now["command"],
                            issue.strip("；"),
                        ]
                        + [_typed(row["values"].get(f), f, definition["contract"]) for f in fields],
                        red=red,
                    )
        _format_sheet(ws, structured, len(fields), source)
    if notes:
        ws = wb.create_sheet(_sheet_name("导出说明", used))
        ws.append(["设备ID", "命令", "检查项", "数据说明"])
        for row in dict.fromkeys(tuple(n) for n in notes):
            _write(ws, row)
        _format_sheet(ws, True, 1, source)
    if not wb.worksheets:
        ws = wb.create_sheet("未取得巡检数据")
        ws.append(["问题描述"])
        ws.append(["本次任务没有可导出的命令快照，不能据此认定健康。"])
    output = BytesIO()
    wb.save(output)
    return output.getvalue()


def _format_sheet(ws, structured, field_count, source):
    ws.freeze_panes = "D2"
    ws.auto_filter.ref = ws.dimensions
    ws.sheet_view.showGridLines = False
    ws.sheet_view.zoomScale = 85
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A3
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
    ws.print_title_rows = "1:1"
    ws.print_options.horizontalCentered = True
    ws.oddHeader.center.text = ws.title
    periods = {p.get("role"): date_text(p.get("observed_at")) for p in source.get("periods") or []}
    ws.oddFooter.left.text = (
        f"基线 {periods.get('baseline', '未提供')} / 本期 {periods.get('current', '未提供')}"
    )
    ws.oddFooter.right.text = "第 &P 页 / 共 &N 页"
    for cell in ws[1]:
        cell.fill = PatternFill("solid", fgColor="D9E7F3")
        cell.font = Font(name="Microsoft YaHei", bold=True, size=10, color="193B59")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = Border(bottom=Side(style="thin", color="90AEC6"))
    ws.row_dimensions[1].height = 42
    for col in range(1, ws.max_column + 1):
        width = {1: 32, 2: 34, 3: 58}.get(col)
        if ws.cell(1, col).value == "数据说明":
            width = 85
        if width is None:
            if col > 3 + (field_count if structured else 0):
                width = 85
            else:
                width = min(38, max(18, len(str(ws.cell(1, col).value)) + 2))
        ws.column_dimensions[get_column_letter(col)].width = width
    for row in ws.iter_rows(min_row=2):
        lines = 1
        for cell in row:
            if isinstance(cell.value, float):
                cell.number_format = "0" if cell.value.is_integer() else "0.###"
            width = ws.column_dimensions[cell.column_letter].width
            value = str(cell.value or "")
            visual_length = sum(2 if ord(c) > 127 else 1 for c in value)
            lines = max(lines, math.ceil(visual_length / max(width - 2, 1)), value.count("\n") + 1)
            if cell.column > 3 + (field_count if structured else 0):
                cell.font = Font(name="Consolas", size=10, color=cell.font.color.rgb)
        ws.row_dimensions[row[0].row].height = min(409, max(24, lines * 15 + 6))
