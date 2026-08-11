"""Human-oriented patrol workbook grouped by check item or unmatched command."""

from __future__ import annotations

import io
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.core.check_items.report_projection import project_report_tables


_INVALID_SHEET_CHARACTERS = re.compile(r"[\\/*?:\[\]]")
_EXCEL_CELL_LIMIT = 32767

_COMMON_COLUMNS = (
    ("device_id", "设备ID"),
    ("device_name", "设备名称"),
    ("management_ip", "管理IP"),
    ("command", "命令"),
    ("change_status", "变化状态"),
    ("check_result", "检查结果"),
)
_TRAILING_COLUMNS = (
    ("current_raw", "内容"),
    ("baseline_raw", "基线输出"),
    ("observed_at", "采集时间"),
    ("parse_status", "解析状态"),
)
_PROJECTION_METADATA_COLUMNS = {
    "device_name",
    "management_ip",
    "entity_type",
    "entity_key",
    "execution_status",
    "assessment_status",
    "completeness_status",
    "comparison_status",
    "threshold_unknown_count",
    "risk_verification_status",
    "blocking_reason_codes",
    "execution_id",
    "snapshot_id",
    "previous_snapshot_id",
    "current_snapshot_id",
    "parser_template_id",
    "runtime_bundle_digest",
    "observed_at",
}

_TEXTFSM_VALUE_ALIASES = {
    "POWERID": "POWER_SUPPLY",
    "POWER_ID": "POWER_SUPPLY",
    "PSU": "POWER_SUPPLY",
    "PSU_ID": "POWER_SUPPLY",
    "FIVE_SEC": "CPU_5S_PCT",
    "FIVE_SECONDS": "CPU_5S_PCT",
    "CPU_USAGE_5S": "CPU_5S_PCT",
    "ONE_MIN": "CPU_1M_PCT",
    "CPU_USAGE_1M": "CPU_1M_PCT",
    "FIVE_MIN": "CPU_5M_PCT",
    "CPU_USAGE_5M": "CPU_5M_PCT",
    "TOTAL_MEMORY": "MEMORY_TOTAL",
    "USED_MEMORY": "MEMORY_USED",
    "USED_RATE": "MEMORY_USED_PCT",
    "MEM_RATE": "MEMORY_USED_PCT",
}

_ASSESSMENT_STATUS_LABELS = {
    "healthy": "正常",
    "passed": "正常",
    "pass": "正常",
    "at_risk": "异常",
    "failed": "异常",
    "fail": "异常",
    "not_applicable": "不适用",
    "unable_to_determine": "未知",
    "unknown": "未知",
}
_PARSE_STATUS_LABELS = {
    "success": "已解析",
    "parsed": "已解析",
    "empty": "空结果",
    "valid_empty": "空结果",
    "template_missing": "缺少模板",
    "parse_failed": "解析失败",
    "failed": "解析失败",
    "ignored": "已忽略",
    "not_applicable": "不适用",
}


@dataclass(slots=True)
class _SheetGroup:
    key: str
    title: str
    kind: str
    columns: list[str] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)
    rows: list[dict[str, Any]] = field(default_factory=list)

    def add_column(self, column_id: str, label: str) -> None:
        if column_id not in self.labels:
            self.columns.append(column_id)
            self.labels[column_id] = label


def _as_rows(value: Any) -> list[dict[str, Any]]:
    return [dict(item) for item in value or [] if isinstance(item, Mapping)]


def _display_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool, datetime)):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _cell_text(value: Any) -> Any:
    value = _display_value(value)
    if not isinstance(value, str) or len(value) <= _EXCEL_CELL_LIMIT:
        return value
    suffix = "\n[内容超过 Excel 单元格限制，已截断]"
    return value[: _EXCEL_CELL_LIMIT - len(suffix)] + suffix


def _set_cell(cell: Any, value: Any) -> None:
    value = _cell_text(value)
    cell.value = value
    if isinstance(value, str):
        cell.data_type = "s"
        cell.number_format = "@"


def _normalize_field_name(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "_", str(value or "")).strip("_").upper()
    return text or "VALUE"


def _canonical_textfsm_field(command: str, value: Any) -> str:
    name = _normalize_field_name(value)
    if name == "STATE":
        normalized_command = command.lower()
        if "power" in normalized_command or "environment" in normalized_command:
            return "POWER_STATUS"
        if "fan" in normalized_command:
            return "FAN_STATUS"
    return _TEXTFSM_VALUE_ALIASES.get(name, name)


def _structured_records(structured: Any) -> list[tuple[str, dict[str, Any]]]:
    if isinstance(structured, Mapping) and isinstance(
        structured.get("record_sets"), Mapping
    ):
        sources = list(dict(structured["record_sets"]).items())
    elif isinstance(structured, Mapping):
        candidate_sets = [
            (str(name), rows)
            for name, rows in structured.items()
            if isinstance(rows, list)
        ]
        sources = candidate_sets or [("default", [dict(structured)])]
    elif isinstance(structured, list):
        sources = [("default", structured)]
    else:
        sources = []

    records: list[tuple[str, dict[str, Any]]] = []
    for record_type, rows in sources:
        if isinstance(rows, Mapping):
            rows = [rows]
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, Mapping):
                records.append((str(record_type or "default"), dict(row)))
            else:
                records.append((str(record_type or "default"), {"VALUE": row}))
    return records


def _normalize_record(command: str, record: Mapping[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    collisions: dict[str, Any] = {}
    for raw_name, value in record.items():
        name = _canonical_textfsm_field(command, raw_name)
        if name in normalized and normalized[name] != value:
            collisions[str(raw_name)] = value
            continue
        normalized[name] = value
    if collisions:
        normalized["UNMAPPED_FIELDS"] = collisions
    return normalized


def _check_item_identity(
    report: Mapping[str, Any],
) -> tuple[dict[str, str], dict[str, str]]:
    aliases: dict[str, str] = {}
    labels: dict[str, str] = {}
    for version_id, raw in dict(report.get("check_items") or {}).items():
        item = dict(raw) if isinstance(raw, Mapping) else {}
        check_item_id = str(item.get("check_item_id") or version_id)
        aliases[str(version_id)] = check_item_id
        aliases[check_item_id] = check_item_id
        labels[check_item_id] = str(
            item.get("name")
            or item.get("display_name")
            or item.get("label")
            or check_item_id
        )
    return aliases, labels


def _resolved_check_item_ids(
    evidence: Mapping[str, Any], aliases: Mapping[str, str]
) -> tuple[str, ...]:
    values = evidence.get("check_item_ids") or []
    return tuple(
        dict.fromkeys(
            aliases.get(str(value), str(value))
            for value in values
            if str(value).strip()
        )
    )


def _change_status(matrix: Mapping[str, Any]) -> str:
    comparison = str(matrix.get("comparison_status") or "").lower()
    if comparison in {"baseline_missing", "missing_baseline", "not_compared"}:
        return "新增"
    if int(matrix.get("change_count") or 0) > 0:
        return "变更"
    if comparison in {"comparable", "compared", "ready", "unchanged"}:
        return "不变"
    if comparison:
        return comparison
    return "未知"


def _check_result(matrix: Mapping[str, Any]) -> str:
    status = str(matrix.get("assessment_status") or "unknown").lower()
    return _ASSESSMENT_STATUS_LABELS.get(status, status or "未知")


def _parse_status(evidence: Mapping[str, Any]) -> str:
    status = str(evidence.get("parse_status") or "unknown").lower()
    return _PARSE_STATUS_LABELS.get(status, status or "未知")


def _is_current_evidence(
    evidence: Mapping[str, Any], baseline_snapshot_ids: set[str]
) -> bool:
    role = str(evidence.get("evidence_role") or "").lower()
    if role:
        return role == "current"
    return str(evidence.get("snapshot_id") or "") not in baseline_snapshot_ids


def _selected_command(matrix: Mapping[str, Any]) -> str:
    for selected in matrix.get("selected_commands") or []:
        if isinstance(selected, Mapping):
            value = selected.get("command") or selected.get("canonical_command")
        else:
            value = selected
        if str(value or "").strip():
            return str(value)
    return ""


def _evidence_for_matrix(
    matrix: Mapping[str, Any],
    evidence_by_snapshot: Mapping[str, Mapping[str, Any]],
    baseline_snapshot_ids: set[str],
    preferred_snapshot_id: Any = None,
) -> Mapping[str, Any]:
    preferred = evidence_by_snapshot.get(str(preferred_snapshot_id or ""))
    if preferred and _is_current_evidence(preferred, baseline_snapshot_ids):
        return preferred
    for reference in matrix.get("evidence_refs") or []:
        evidence = evidence_by_snapshot.get(str(reference))
        if evidence and _is_current_evidence(evidence, baseline_snapshot_ids):
            return evidence
    return {}


def _baseline_output(
    matrix: Mapping[str, Any],
    evidence_by_snapshot: Mapping[str, Mapping[str, Any]],
) -> str:
    outputs = [
        str(evidence_by_snapshot[str(snapshot_id)].get("raw_text") or "")
        for snapshot_id in matrix.get("baseline_snapshot_ids") or []
        if str(snapshot_id) in evidence_by_snapshot
        and str(evidence_by_snapshot[str(snapshot_id)].get("raw_text") or "")
    ]
    return "\n\n---\n\n".join(outputs)


def _base_row(
    *,
    matrix: Mapping[str, Any],
    evidence: Mapping[str, Any],
    evidence_by_snapshot: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "device_id": matrix.get("device_id") or evidence.get("device_id"),
        "device_name": matrix.get("device_name") or evidence.get("device_name"),
        "management_ip": matrix.get("management_ip") or evidence.get("management_ip"),
        "command": (
            evidence.get("command")
            or evidence.get("canonical_command")
            or _selected_command(matrix)
        ),
        "change_status": _change_status(matrix),
        "check_result": _check_result(matrix),
        "current_raw": evidence.get("raw_text") or "",
        "baseline_raw": _baseline_output(matrix, evidence_by_snapshot),
        "observed_at": evidence.get("observed_at"),
        "parse_status": _parse_status(evidence),
    }


def _append_evidence_rows(
    group: _SheetGroup,
    *,
    matrix: Mapping[str, Any],
    evidence: Mapping[str, Any],
    evidence_by_snapshot: Mapping[str, Mapping[str, Any]],
) -> None:
    records = _structured_records(evidence.get("structured_json")) or [("", {})]
    for record_type, raw_record in records:
        row = _base_row(
            matrix=matrix,
            evidence=evidence,
            evidence_by_snapshot=evidence_by_snapshot,
        )
        if record_type:
            group.add_column("record_type", "记录类型")
            row["record_type"] = record_type
        for name, value in _normalize_record(str(row.get("command") or ""), raw_record).items():
            column_id = f"value:{name}"
            group.add_column(column_id, name)
            row[column_id] = value
        group.rows.append(row)


def _sheet_groups(report: Mapping[str, Any]) -> list[_SheetGroup]:
    aliases, check_item_labels = _check_item_identity(report)
    matrix_rows = _as_rows(report.get("matrix"))
    matrix_by_execution = {
        str(item.get("execution_id") or ""): item
        for item in matrix_rows
        if str(item.get("execution_id") or "")
    }
    matrix_by_device_check: dict[tuple[str, str], dict[str, Any]] = {}
    for item in matrix_rows:
        check_item_id = aliases.get(
            str(item.get("check_item_version_id") or ""),
            str(item.get("check_item_id") or ""),
        )
        if check_item_id:
            matrix_by_device_check[(str(item.get("device_id") or ""), check_item_id)] = item

    evidence_rows = _as_rows(report.get("evidence_index"))
    evidence_by_snapshot = {
        str(item.get("snapshot_id") or ""): item
        for item in evidence_rows
        if str(item.get("snapshot_id") or "")
    }
    baseline_snapshot_ids = {
        str(snapshot_id)
        for item in matrix_rows
        for snapshot_id in item.get("baseline_snapshot_ids") or []
        if str(snapshot_id)
    }
    current_evidence = [
        item
        for item in evidence_rows
        if _is_current_evidence(item, baseline_snapshot_ids)
    ]

    groups: dict[str, _SheetGroup] = {}
    represented_snapshots: dict[str, set[str]] = {}
    represented_executions: dict[str, set[str]] = {}

    for table in project_report_tables(report):
        check_item_id = aliases.get(table.check_item_id, table.check_item_id)
        group_key = f"check:{check_item_id}"
        group = groups.setdefault(
            group_key,
            _SheetGroup(
                key=group_key,
                title=check_item_labels.get(check_item_id) or table.sheet_name or check_item_id,
                kind="检查项",
            ),
        )
        group.add_column("record_type", "记录类型")
        group.add_column("record_key", "记录标识")
        headers_by_id = dict(zip(table.column_ids, table.headers, strict=True))
        semantic_columns = [
            column_id
            for column_id in table.column_ids
            if column_id not in _PROJECTION_METADATA_COLUMNS
        ]
        for column_id in semantic_columns:
            group.add_column(
                f"projection:{column_id}",
                headers_by_id.get(column_id, column_id),
            )
        for values in table.rows:
            projected = dict(zip(table.column_ids, values, strict=True))
            execution_id = str(projected.get("execution_id") or "")
            matrix = matrix_by_execution.get(execution_id, {})
            evidence = _evidence_for_matrix(
                matrix,
                evidence_by_snapshot,
                baseline_snapshot_ids,
                projected.get("snapshot_id") or projected.get("current_snapshot_id"),
            )
            row = _base_row(
                matrix=matrix,
                evidence=evidence,
                evidence_by_snapshot=evidence_by_snapshot,
            )
            row["record_type"] = projected.get("entity_type")
            row["record_key"] = projected.get("entity_key")
            for column_id in semantic_columns:
                row[f"projection:{column_id}"] = projected.get(column_id)
            if projected.get("observed_at") and not row.get("observed_at"):
                row["observed_at"] = projected.get("observed_at")
            group.rows.append(row)
            if execution_id:
                represented_executions.setdefault(check_item_id, set()).add(execution_id)
            snapshot_id = str(evidence.get("snapshot_id") or "")
            if snapshot_id:
                represented_snapshots.setdefault(check_item_id, set()).add(snapshot_id)

    for matrix in matrix_rows:
        check_item_id = aliases.get(
            str(matrix.get("check_item_version_id") or ""),
            str(matrix.get("check_item_id") or ""),
        )
        if not check_item_id:
            continue
        group_key = f"check:{check_item_id}"
        group = groups.setdefault(
            group_key,
            _SheetGroup(
                key=group_key,
                title=check_item_labels.get(check_item_id) or check_item_id,
                kind="检查项",
            ),
        )
        execution_id = str(matrix.get("execution_id") or "")
        if execution_id in represented_executions.get(check_item_id, set()):
            continue
        evidence = _evidence_for_matrix(
            matrix,
            evidence_by_snapshot,
            baseline_snapshot_ids,
        )
        if evidence:
            _append_evidence_rows(
                group,
                matrix=matrix,
                evidence=evidence,
                evidence_by_snapshot=evidence_by_snapshot,
            )
            snapshot_id = str(evidence.get("snapshot_id") or "")
            if snapshot_id:
                represented_snapshots.setdefault(check_item_id, set()).add(snapshot_id)
        else:
            group.rows.append(
                _base_row(
                    matrix=matrix,
                    evidence={},
                    evidence_by_snapshot=evidence_by_snapshot,
                )
            )
        if execution_id:
            represented_executions.setdefault(check_item_id, set()).add(execution_id)

    for evidence in current_evidence:
        check_item_ids = _resolved_check_item_ids(evidence, aliases)
        if check_item_ids:
            for check_item_id in check_item_ids:
                snapshot_id = str(evidence.get("snapshot_id") or "")
                if snapshot_id in represented_snapshots.get(check_item_id, set()):
                    continue
                group_key = f"check:{check_item_id}"
                group = groups.setdefault(
                    group_key,
                    _SheetGroup(
                        key=group_key,
                        title=check_item_labels.get(check_item_id) or check_item_id,
                        kind="检查项",
                    ),
                )
                matrix = matrix_by_device_check.get(
                    (str(evidence.get("device_id") or ""), check_item_id),
                    {},
                )
                _append_evidence_rows(
                    group,
                    matrix=matrix,
                    evidence=evidence,
                    evidence_by_snapshot=evidence_by_snapshot,
                )
                if snapshot_id:
                    represented_snapshots.setdefault(check_item_id, set()).add(snapshot_id)
            continue

        command = str(
            evidence.get("canonical_command")
            or evidence.get("command")
            or "未识别命令"
        )
        group_key = f"command:{command.casefold()}"
        group = groups.setdefault(
            group_key,
            _SheetGroup(
                key=group_key,
                title=command,
                kind="未关联命令",
            ),
        )
        _append_evidence_rows(
            group,
            matrix={},
            evidence=evidence,
            evidence_by_snapshot=evidence_by_snapshot,
        )

    for group in groups.values():
        group.rows.sort(
            key=lambda row: (
                str(row.get("device_name") or ""),
                str(row.get("management_ip") or ""),
                str(row.get("command") or ""),
                str(row.get("record_type") or ""),
                str(row.get("record_key") or ""),
            )
        )
    return sorted(groups.values(), key=lambda item: (item.kind != "检查项", item.title))


def _safe_sheet_name(name: str, used: set[str]) -> str:
    base = _INVALID_SHEET_CHARACTERS.sub("_", str(name or "未命名")).strip(" '")
    base = base or "未命名"
    candidate = base[:31]
    index = 2
    while candidate.casefold() in used:
        suffix = f"~{index}"
        candidate = f"{base[: 31 - len(suffix)]}{suffix}"
        index += 1
    used.add(candidate.casefold())
    return candidate


def _excel_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        except ValueError:
            parsed = datetime.now(timezone.utc)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _write_data_sheet(workbook: Any, group: _SheetGroup, name: str) -> None:
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    sheet = workbook.create_sheet(name)
    headers = [
        *[label for _, label in _COMMON_COLUMNS],
        *[group.labels[column_id] for column_id in group.columns],
        *[label for _, label in _TRAILING_COLUMNS],
    ]
    column_ids = [
        *[column_id for column_id, _ in _COMMON_COLUMNS],
        *group.columns,
        *[column_id for column_id, _ in _TRAILING_COLUMNS],
    ]
    sheet.append(headers)
    for row_index, row in enumerate(group.rows, start=2):
        for column_index, column_id in enumerate(column_ids, start=1):
            _set_cell(sheet.cell(row_index, column_index), row.get(column_id))
        sheet.row_dimensions[row_index].height = 42

    header_fill = PatternFill("solid", fgColor="344E41")
    for cell in sheet[1]:
        cell.fill = header_fill
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    sheet.row_dimensions[1].height = 30
    sheet.sheet_view.showGridLines = False
    sheet.sheet_view.zoomScale = 85
    sheet.freeze_panes = "G2"
    if group.rows:
        sheet.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{sheet.max_row}"
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)

    raw_headers = {"内容", "基线输出"}
    for index, header in enumerate(headers, start=1):
        if header in raw_headers:
            width = 60
        else:
            values = [header] + [
            str(sheet.cell(row, index).value or "").partition("\n")[0]
                for row in range(2, min(sheet.max_row, 120) + 1)
            ]
            width = min(max(max(len(value) for value in values) + 2, 12), 36)
        sheet.column_dimensions[get_column_letter(index)].width = width
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.print_title_rows = "1:1"


def render_inspection_workbook(report: Mapping[str, Any]) -> bytes:
    """Render one sheet per check item and one sheet per unmatched command."""

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    groups = _sheet_groups(report)
    workbook = Workbook()
    workbook.properties.creator = "NetOps Agent"
    generated_at = _excel_datetime(report.get("generated_at"))
    workbook.properties.created = generated_at
    workbook.properties.modified = generated_at

    summary = workbook.active
    summary.title = "摘要"
    summary.sheet_view.showGridLines = False
    summary.sheet_view.zoomScale = 90
    summary.merge_cells("A1:D1")
    summary["A1"] = "网络设备巡检明细"
    summary["A1"].font = Font(color="FFFFFF", bold=True, size=16)
    summary["A1"].fill = PatternFill("solid", fgColor="263238")
    summary["A1"].alignment = Alignment(horizontal="left", vertical="center")
    summary.row_dimensions[1].height = 30

    check_groups = [group for group in groups if group.kind == "检查项"]
    unmatched_groups = [group for group in groups if group.kind == "未关联命令"]
    metrics = (
        ("巡检批次", report.get("patrol_batch_id")),
        ("生成时间", report.get("generated_at")),
        ("检查项 Sheet", len(check_groups)),
        ("未关联命令 Sheet", len(unmatched_groups)),
        ("明细记录", sum(len(group.rows) for group in groups)),
    )
    for row_index, (label, value) in enumerate(metrics, start=3):
        summary.cell(row_index, 1, label).font = Font(bold=True)
        _set_cell(summary.cell(row_index, 2), value)

    index_row = 10
    index_headers = ("Sheet", "类型", "来源", "记录数")
    for column_index, header in enumerate(index_headers, start=1):
        cell = summary.cell(index_row, column_index, header)
        cell.fill = PatternFill("solid", fgColor="344E41")
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(horizontal="center")

    used_names = {"摘要".casefold()}
    for offset, group in enumerate(groups, start=1):
        sheet_name = _safe_sheet_name(group.title, used_names)
        _write_data_sheet(workbook, group, sheet_name)
        row_index = index_row + offset
        summary.cell(row_index, 1, sheet_name)
        summary.cell(row_index, 1).hyperlink = f"#'{sheet_name}'!A1"
        summary.cell(row_index, 1).style = "Hyperlink"
        summary.cell(row_index, 2, group.kind)
        summary.cell(row_index, 3, group.title)
        summary.cell(row_index, 4, len(group.rows))

    summary.column_dimensions["A"].width = 30
    summary.column_dimensions["B"].width = 18
    summary.column_dimensions["C"].width = 42
    summary.column_dimensions["D"].width = 14
    summary.freeze_panes = "A11"

    stream = io.BytesIO()
    workbook.save(stream)
    return stream.getvalue()


__all__ = ["render_inspection_workbook"]
