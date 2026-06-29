"""Human-facing patrol diff and report helpers."""

from __future__ import annotations

from collections import Counter
from html import escape
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from src.core.patrol.change_detector import detect_changes_from_params
from src.core.patrol.change_event_repository import ChangeEventRepository


def build_human_diff(params: dict[str, Any]) -> dict[str, Any]:
    effective = dict(params)
    effective.setdefault("persist", False)
    result = detect_changes_from_params(effective)
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
    lines = [
        f"# Patrol Report: {run_id}",
        "",
        "## Summary",
        "",
        f"- Changes: {len(changes)}",
        f"- Events: {len(events)}",
    ]
    if event_counter:
        lines.extend(["", "## Events By Type", ""])
        lines.extend(
            f"- {event_type}: {count}"
            for event_type, count in sorted(event_counter.items())
        )
    if severity_counter:
        lines.extend(["", "## Events By Severity", ""])
        lines.extend(
            f"- {severity}: {count}"
            for severity, count in sorted(severity_counter.items())
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
        rendered = _render_html(run_id, changes, events, event_counter, severity_counter)
    written_path = None
    if output_path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if output_format == "xlsx":
            _write_xlsx(path, run_id, changes, events)
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
  <p>Changes: {len(changes)}<br>Events: {len(events)}</p>
  <h2>Events By Type</h2>
  <ul>{''.join(f'<li>{escape(k)}: {v}</li>' for k, v in sorted(event_counter.items()))}</ul>
  <h2>Events By Severity</h2>
  <ul>{''.join(f'<li>{escape(k)}: {v}</li>' for k, v in sorted(severity_counter.items()))}</ul>
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
) -> None:
    rows = [
        ["Run ID", run_id],
        ["Changes", str(len(changes))],
        ["Events", str(len(events))],
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
            cells.append(
                f'<c r="{ref}" t="inlineStr"><is><t>{escape(value)}</t></is></c>'
            )
        sheet_rows.append(f'<row r="{index}">{"".join(cells)}</row>')
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(sheet_rows)}</sheetData></worksheet>'
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
