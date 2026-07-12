"""Human-facing patrol diff and report helpers."""

from __future__ import annotations

from collections import Counter
from html import escape
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from src.core.patrol.handlers import DetectChangesHandler
from src.core.patrol.change_event_repository import ChangeEventRepository
from src.core.patrol.repository import PatrolRepository


def build_run_quality_summary(run_id: str) -> dict[str, Any]:
    repository = PatrolRepository()
    run = repository.get_run(run_id) or {}
    results = repository.list_collection_results(run_id)
    eligible = [
        item
        for item in results
        if item.get("parse_status") not in {"ignored", "not_applicable"}
    ]
    parser_eligible = [item for item in eligible if item.get("parse_status") != "raw_hashed"]
    parser_ready = [
        item
        for item in parser_eligible
        if item.get("parse_status") in {"parsed", "parse_empty"}
    ]
    fact_ready = [item for item in eligible if item.get("fact_status") in {"ready", "empty"}]
    normalized = [
        item for item in eligible if item.get("normalization_status") in {"normalized", "empty"}
    ]
    denominator = len(eligible)
    parser_denominator = len(parser_eligible)
    return {
        "collection_status": run.get("collection_status") or "unknown",
        "parser_readiness_status": run.get("parser_readiness_status") or "unknown",
        "fact_readiness_status": run.get("fact_readiness_status") or "unknown",
        "analysis_status": run.get("analysis_status") or "unknown",
        "command_count": len(results),
        "execution_success_count": sum(
            item.get("execution_status") == "complete" for item in results
        ),
        "parser_ready_rate": (
            len(parser_ready) / parser_denominator if parser_denominator else 1.0
        ),
        "fact_ready_rate": len(fact_ready) / denominator if denominator else 1.0,
        "normalization_ready_rate": len(normalized) / denominator if denominator else 1.0,
        "template_missing_count": sum(
            item.get("parse_status") == "template_missing" for item in results
        ),
        "parser_backlog_count": len(
            {item.get("parser_candidate_id") for item in results if item.get("parser_candidate_id")}
        ),
        "blocked_by_contract_count": sum(
            item.get("fact_status") == "blocked_by_contract" for item in results
        ),
        "blocked_by_parser_count": sum(
            item.get("fact_status") == "blocked_by_parser" for item in results
        ),
        "valid_empty_count": sum(
            item.get("output_status") == "valid_empty" for item in results
        ),
        "empty_unverified_count": sum(
            item.get("output_status") == "empty_unverified" for item in results
        ),
        "unsupported_command_count": sum(
            item.get("output_status") == "unsupported_command" for item in results
        ),
        "command_error_count": sum(
            item.get("output_status") == "command_error" for item in results
        ),
        "raw_hash_diff_count": sum(
            item.get("parse_status") == "raw_hashed" for item in results
        ),
    }


def build_human_diff(params: dict[str, Any]) -> dict[str, Any]:
    effective = dict(params)
    effective.setdefault("persist", False)
    result = DetectChangesHandler().handle(effective)
    return {
        **result,
        "report_type": "patrol_diff",
        "persisted_as_network_changes": bool(effective.get("persist")),
    }


def render_run_report(
    *,
    run_id: str,
    output_path: str | None = None,
    device_id: str | None = None,
    output_format: str = "markdown",
) -> dict[str, Any]:
    repository = ChangeEventRepository()
    changes = repository.list_changes(run_id=run_id, device_id=device_id)
    events = repository.list_events(run_id=run_id, device_id=device_id)
    event_counter = Counter(event["event_type"] for event in events)
    severity_counter = Counter(event["severity"] for event in events)
    quality = build_run_quality_summary(run_id)
    lines = [
        f"# Patrol Report: {run_id}",
        "",
        "## Summary",
        "",
        f"- Changes: {len(changes)}",
        f"- Events: {len(events)}",
        f"- Collection status: {quality['collection_status']}",
        f"- Parser ready: {quality['parser_ready_rate']:.1%}",
        f"- Fact ready: {quality['fact_ready_rate']:.1%}",
        f"- Normalization ready: {quality['normalization_ready_rate']:.1%}",
        f"- Missing templates: {quality['template_missing_count']}",
        f"- Parser backlog: {quality['parser_backlog_count']}",
        f"- Valid empty outputs: {quality['valid_empty_count']}",
        f"- Unverified empty outputs: {quality['empty_unverified_count']}",
        f"- Unsupported commands: {quality['unsupported_command_count']}",
        f"- Raw hash comparisons: {quality['raw_hash_diff_count']}",
    ]
    if event_counter:
        lines.extend(["", "## Events By Type", ""])
        lines.extend(
            f"- {event_type}: {count}" for event_type, count in sorted(event_counter.items())
        )
    if severity_counter:
        lines.extend(["", "## Events By Severity", ""])
        lines.extend(
            f"- {severity}: {count}" for severity, count in sorted(severity_counter.items())
        )
    if events:
        lines.extend(["", "## Event Details", ""])
        for event in events:
            lines.append(
                "- "
                f"{event['occurred_at']} "
                f"{event['severity']} "
                f"{event['event_type']} "
                f"{event.get('device_id') or '-'} "
                f"{event.get('entity_key') or ''}"
            )
    markdown = "\n".join(lines) + "\n"
    output_format = str(output_format or "markdown").lower()
    rendered = markdown
    if output_format == "html":
        rendered = _render_html(
            run_id,
            changes,
            events,
            event_counter,
            severity_counter,
            quality,
        )
    written_path = None
    if output_path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if output_format == "xlsx":
            _write_xlsx(path, run_id, changes, events, quality)
            rendered = ""
        elif output_format == "html":
            path.write_text(rendered, encoding="utf-8")
        else:
            path.write_text(markdown, encoding="utf-8")
        written_path = str(path)
    return {
        "success": True,
        "run_id": run_id,
        "output_format": output_format,
        "change_count": len(changes),
        "event_count": len(events),
        "event_types": dict(event_counter),
        "severities": dict(severity_counter),
        "quality": quality,
        "markdown": markdown,
        "content": rendered,
        "output_path": written_path,
    }


def _render_html(
    run_id: str,
    changes: list[dict[str, Any]],
    events: list[dict[str, Any]],
    event_counter: Counter,
    severity_counter: Counter,
    quality: dict[str, Any],
) -> str:
    rows = "\n".join(
        "<tr>"
        f"<td>{escape(str(event.get('occurred_at') or ''))}</td>"
        f"<td>{escape(str(event.get('severity') or ''))}</td>"
        f"<td>{escape(str(event.get('event_type') or ''))}</td>"
        f"<td>{escape(str(event.get('device_id') or ''))}</td>"
        f"<td>{escape(str(event.get('entity_key') or ''))}</td>"
        "</tr>"
        for event in events
    )
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Patrol Report {escape(run_id)}</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 24px; color: #1f2937; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #d1d5db; padding: 6px 8px; text-align: left; }}
    th {{ background: #f3f4f6; }}
  </style>
</head>
<body>
  <h1>Patrol Report: {escape(run_id)}</h1>
  <h2>Summary</h2>
  <p>Changes: {len(changes)}<br>Events: {len(events)}<br>
  Collection: {escape(str(quality["collection_status"]))}<br>
  Parser ready: {quality["parser_ready_rate"]:.1%}<br>
  Fact ready: {quality["fact_ready_rate"]:.1%}<br>
  Normalization ready: {quality["normalization_ready_rate"]:.1%}<br>
  Missing templates: {quality["template_missing_count"]}<br>
  Parser backlog: {quality["parser_backlog_count"]}</p>
  <p>Valid empty outputs: {quality["valid_empty_count"]}<br>
  Unverified empty outputs: {quality["empty_unverified_count"]}<br>
  Unsupported commands: {quality["unsupported_command_count"]}<br>
  Raw hash comparisons: {quality["raw_hash_diff_count"]}</p>
  <h2>Events By Type</h2>
  <ul>{"".join(f"<li>{escape(k)}: {v}</li>" for k, v in sorted(event_counter.items()))}</ul>
  <h2>Events By Severity</h2>
  <ul>{"".join(f"<li>{escape(k)}: {v}</li>" for k, v in sorted(severity_counter.items()))}</ul>
  <h2>Event Details</h2>
  <table>
    <thead><tr><th>Time</th><th>Severity</th><th>Type</th><th>Device</th><th>Entity</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</body>
</html>
"""


def _write_xlsx(
    path: Path,
    run_id: str,
    changes: list[dict[str, Any]],
    events: list[dict[str, Any]],
    quality: dict[str, Any],
) -> None:
    rows = [
        ["Run ID", run_id],
        ["Changes", str(len(changes))],
        ["Events", str(len(events))],
        ["Collection status", str(quality["collection_status"])],
        ["Parser ready", f"{quality['parser_ready_rate']:.1%}"],
        ["Fact ready", f"{quality['fact_ready_rate']:.1%}"],
        ["Normalization ready", f"{quality['normalization_ready_rate']:.1%}"],
        ["Missing templates", str(quality["template_missing_count"])],
        ["Parser backlog", str(quality["parser_backlog_count"])],
        ["Valid empty outputs", str(quality["valid_empty_count"])],
        ["Unverified empty outputs", str(quality["empty_unverified_count"])],
        ["Unsupported commands", str(quality["unsupported_command_count"])],
        ["Raw hash comparisons", str(quality["raw_hash_diff_count"])],
        [],
        ["Time", "Severity", "Event Type", "Device", "Entity"],
    ]
    rows.extend(
        [
            str(event.get("occurred_at") or ""),
            str(event.get("severity") or ""),
            str(event.get("event_type") or ""),
            str(event.get("device_id") or ""),
            str(event.get("entity_key") or ""),
        ]
        for event in events
    )
    sheet_rows = []
    for index, row in enumerate(rows, start=1):
        cells = []
        for col_index, value in enumerate(row, start=1):
            ref = f"{_xlsx_col(col_index)}{index}"
            cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{escape(value)}</t></is></c>')
        sheet_rows.append(f'<row r="{index}">{"".join(cells)}</row>')
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{''.join(sheet_rows)}</sheetData></worksheet>"
    )
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            "</Types>",
        )
        archive.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
        )
        archive.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Patrol Report" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            "</Relationships>",
        )
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)


def _xlsx_col(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result
