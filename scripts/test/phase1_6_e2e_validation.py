#!/usr/bin/env python
"""Run a repeatable Phase 1-6 validation over two offline patrol datasets.

The script imports two parser-validation patrol directories into PostgreSQL,
normalizes parsed snapshots into StateObservation, detects observation-based
changes, builds events, and renders a downstream patrol report.

It writes only aggregate validation reports. Raw CLI output stays in the source
directories and PostgreSQL snapshot/evidence tables.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.patrol.change_detector import detect_changes_from_params  # noqa: E402
from src.core.patrol.event_builder import build_events_from_params  # noqa: E402
from src.core.patrol.raw_importer import import_raw_path, preview_raw_path  # noqa: E402
from src.core.patrol.reporting import render_run_report  # noqa: E402
from src.core.state import normalize_state_from_params  # noqa: E402
from src.infrastructure.db.models import CommandSnapshot  # noqa: E402
from src.infrastructure.db.postgres import (  # noqa: E402
    get_db_session,
    require_postgres_connection,
    settings,
)

ACTIONABLE_PARSE_STATUSES = {"template_missing", "parse_failed"}


def _failure_payload(error: Exception) -> dict[str, Any]:
    return {
        "success": False,
        "error_type": error.__class__.__name__,
        "error": str(error),
        "postgres": {
            "host": settings.POSTGRES_HOST,
            "port": settings.POSTGRES_PORT,
            "database": settings.POSTGRES_DB,
            "user": settings.POSTGRES_USER,
        },
        "recovery": [
            "Start Docker Desktop or the Docker daemon.",
            "Run: docker compose -f deployment/docker-compose.middleware.yml up -d postgres",
            "Run: venv\\Scripts\\python.exe -m alembic upgrade head",
            "Re-run: venv\\Scripts\\python.exe scripts\\test\\phase1_6_e2e_validation.py",
        ],
    }


def _top(counter: Counter[str], *, limit: int = 20) -> dict[str, int]:
    return dict(counter.most_common(limit))


def _parse_device_ids(values: list[str] | None) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        for item in str(value).split(","):
            device_id = item.strip()
            if not device_id or device_id in seen:
                continue
            seen.add(device_id)
            result.append(device_id)
    return result


def _row_value(row: Any, name: str) -> Any:
    if isinstance(row, dict):
        return row.get(name)
    return getattr(row, name, None)


def _snapshot_summary_from_rows(rows: list[Any]) -> dict[str, Any]:
    status_counter = Counter(
        str(_row_value(row, "parse_status") or "unknown") for row in rows
    )
    command_counter = Counter(
        str(_row_value(row, "canonical_command") or "unknown") for row in rows
    )
    device_counter = Counter(
        str(_row_value(row, "device_id") or "unknown") for row in rows
    )
    vendor_counter = Counter(str(_row_value(row, "vendor") or "unknown") for row in rows)
    model_counter = Counter(str(_row_value(row, "model") or "unknown") for row in rows)
    gap_by_command: dict[str, Counter[str]] = {
        status: Counter() for status in ACTIONABLE_PARSE_STATUSES
    }
    gap_by_model: dict[str, Counter[str]] = {
        status: Counter() for status in ACTIONABLE_PARSE_STATUSES
    }
    gap_by_vendor: dict[str, Counter[str]] = {
        status: Counter() for status in ACTIONABLE_PARSE_STATUSES
    }
    for row in rows:
        status = str(_row_value(row, "parse_status") or "unknown")
        if status not in ACTIONABLE_PARSE_STATUSES:
            continue
        command = str(_row_value(row, "canonical_command") or "unknown")
        vendor = str(_row_value(row, "vendor") or "unknown")
        model = str(_row_value(row, "model") or "unknown")
        gap_by_command[status][command] += 1
        gap_by_model[status][f"{vendor} {model}".strip()] += 1
        gap_by_vendor[status][vendor] += 1
    parsed_count = status_counter.get("parsed", 0) + status_counter.get("parse_empty", 0)
    eligible_count = parsed_count + sum(
        status_counter.get(status, 0) for status in ACTIONABLE_PARSE_STATUSES
    )
    return {
        "snapshot_count": len(rows),
        "parse_status": dict(status_counter),
        "devices": _top(device_counter),
        "vendors": dict(vendor_counter),
        "models": dict(model_counter),
        "commands": _top(command_counter),
        "unique_commands": len(command_counter),
        "eligible_snapshot_count": eligible_count,
        "structured_snapshot_count": parsed_count,
        "coverage_rate": round(parsed_count / eligible_count, 4)
        if eligible_count
        else 1.0,
        "template_missing_by_command": _top(gap_by_command["template_missing"]),
        "template_missing_by_model": _top(gap_by_model["template_missing"]),
        "template_missing_by_vendor": _top(gap_by_vendor["template_missing"]),
        "parse_failed_by_command": _top(gap_by_command["parse_failed"]),
        "parse_failed_by_model": _top(gap_by_model["parse_failed"]),
        "parse_failed_by_vendor": _top(gap_by_vendor["parse_failed"]),
    }


def _snapshot_summary(
    run_id: str,
    *,
    device_ids: list[str] | None = None,
) -> dict[str, Any]:
    with get_db_session() as db:
        query = db.query(
            CommandSnapshot.device_id,
            CommandSnapshot.parse_status,
            CommandSnapshot.vendor,
            CommandSnapshot.model,
            CommandSnapshot.canonical_command,
        ).filter(CommandSnapshot.run_id == run_id)
        if device_ids:
            query = query.filter(CommandSnapshot.device_id.in_(device_ids))
        rows = query.all()
    return _snapshot_summary_from_rows(list(rows))


def _run_device_ids(run_id: str) -> list[str]:
    with get_db_session() as db:
        rows = (
            db.query(CommandSnapshot.device_id)
            .filter(CommandSnapshot.run_id == run_id)
            .distinct()
            .order_by(CommandSnapshot.device_id)
            .all()
        )
    return [str(row[0]) for row in rows]


def _write_markdown(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# Phase 1-6 E2E Validation Report",
        "",
        f"- Generated At: `{summary['generated_at']}`",
        f"- Previous Source: `{summary['previous_source']}`",
        f"- Current Source: `{summary['current_source']}`",
        f"- Previous Run: `{summary['previous_run_id']}`",
        f"- Current Run: `{summary['current_run_id']}`",
        f"- Validation Scope: `{summary['validation_scope']}`",
        f"- Validation Devices: `{', '.join(summary['validation_device_ids']) or 'all imported devices'}`",
        f"- Missing Requested Devices: `{', '.join(summary.get('missing_validation_device_ids') or []) or '-'}`",
        "",
        "## Import Preview",
        "",
        "| Dataset | Files | Devices | Commands | New Snapshots | Duplicate Snapshots | Ready | Missing | Failed | Ignored | Identity Unknown |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for label in ("previous", "current"):
        preview = summary[f"{label}_preview"]
        lines.append(
            "| "
            + " | ".join(
                [
                    label,
                    str(preview.get("file_count", 0)),
                    str(preview.get("device_count", 0)),
                    str(preview.get("command_count", 0)),
                    str(preview.get("new_snapshots", 0)),
                    str(preview.get("duplicate_snapshots", 0)),
                    str(preview.get("ready", 0)),
                    str(preview.get("template_missing", 0)),
                    str(preview.get("parse_failed", 0)),
                    str(preview.get("ignored", 0)),
                    str(preview.get("identity_unknown", 0)),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Snapshot Parse Status",
            "",
            "### Previous",
            "",
            "```json",
            json.dumps(summary["previous_snapshots"], ensure_ascii=False, indent=2, default=str),
            "```",
            "",
            "### Current",
            "",
            "```json",
            json.dumps(summary["current_snapshots"], ensure_ascii=False, indent=2, default=str),
            "```",
            "",
            "## Parser Coverage Gaps",
            "",
            "| Dataset | Coverage | Missing | Failed | Top Missing Commands | Top Failed Commands |",
            "|---|---:|---:|---:|---|---|",
        ]
    )
    for label in ("previous", "current"):
        snapshots = summary[f"{label}_snapshots"]
        missing_commands = ", ".join(
            f"{key}={value}"
            for key, value in list(
                snapshots.get("template_missing_by_command", {}).items()
            )[:5]
        )
        failed_commands = ", ".join(
            f"{key}={value}"
            for key, value in list(snapshots.get("parse_failed_by_command", {}).items())[:5]
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    label,
                    f"{float(snapshots.get('coverage_rate', 0)):.2%}",
                    str(snapshots.get("parse_status", {}).get("template_missing", 0)),
                    str(snapshots.get("parse_status", {}).get("parse_failed", 0)),
                    missing_commands or "-",
                    failed_commands or "-",
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## State / Change / Event",
            "",
            f"- Previous Observations: `{summary['previous_state']['observation_count']}`",
            f"- Current Observations: `{summary['current_state']['observation_count']}`",
            f"- Observation Changes: `{summary['change_result']['change_count']}`",
            f"- Persisted Changes: `{summary['change_result']['persisted_change_count']}`",
            f"- Events: `{summary['event_result']['event_count']}`",
            f"- Persisted Events: `{summary['event_result']['persisted_event_count']}`",
            f"- No-Event Changes: `{summary['event_result'].get('no_event_change_count', 0)}`",
            "",
            "### Change Detector Result",
            "",
            "```json",
            json.dumps(
                {
                    key: value
                    for key, value in summary["change_result"].items()
                    if key != "changes"
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            "```",
            "",
            "### Event Builder Result",
            "",
            "```json",
            json.dumps(
                {
                    key: value
                    for key, value in summary["event_result"].items()
                    if key != "events"
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            "```",
            "",
            "## Generated Report",
            "",
            f"- Markdown Report: `{summary['generated_report']['output_path']}`",
            f"- Report Changes: `{summary['generated_report']['change_count']}`",
            f"- Report Events: `{summary['generated_report']['event_count']}`",
            "",
            "## Current Gaps",
            "",
        ]
    )
    gaps = []
    if summary["previous_state"]["observation_count"] == 0:
        gaps.append("previous dataset produced zero StateObservation; parser/template coverage is still insufficient.")
    if summary["current_state"]["observation_count"] == 0:
        gaps.append("current dataset produced zero StateObservation; parser/template coverage is still insufficient.")
    if summary["change_result"]["change_count"] == 0:
        gaps.append("state-based change detector found zero changes; verify parser coverage and primary-key contracts.")
    if summary["event_result"]["event_count"] == 0:
        gaps.append("event-builder produced zero events; either no event-level changes were detected or rules need more coverage.")
    if not gaps:
        gaps.append("no blocking gap detected by this validation run.")
    lines.extend(f"- {gap}" for gap in gaps)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> dict[str, Any]:
    require_postgres_connection()
    previous_source = Path(args.previous_source)
    current_source = Path(args.current_source)
    if not previous_source.exists():
        raise FileNotFoundError(previous_source)
    if not current_source.exists():
        raise FileNotFoundError(current_source)

    generated_at = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    previous_dataset = (
        args.previous_dataset_id or f"phase1-6-validation-previous-{generated_at}"
    )
    current_dataset = (
        args.current_dataset_id or f"phase1-6-validation-current-{generated_at}"
    )
    previous_run_id = args.previous_run_id or f"phase1-6-validation-previous-{generated_at}"
    current_run_id = args.current_run_id or f"phase1-6-validation-current-{generated_at}"

    previous_preview = preview_raw_path(
        file_path=previous_source,
        mode="parser_validation",
        metadata={"dataset_id": previous_dataset, "dataset_name": previous_dataset},
    )
    current_preview = preview_raw_path(
        file_path=current_source,
        mode="parser_validation",
        metadata={"dataset_id": current_dataset, "dataset_name": current_dataset},
    )
    previous_import = import_raw_path(
        file_path=previous_source,
        mode="parser_validation",
        run_id=previous_run_id,
        metadata={
            "dataset_id": previous_dataset,
            "dataset_name": previous_dataset,
            "validation_scope": "phase1-6",
        },
    )
    current_import = import_raw_path(
        file_path=current_source,
        mode="parser_validation",
        run_id=current_run_id,
        metadata={
            "dataset_id": current_dataset,
            "dataset_name": current_dataset,
            "validation_scope": "phase1-6",
        },
    )
    previous_run_id = previous_import["run_id"]
    current_run_id = current_import["run_id"]
    requested_device_ids = _parse_device_ids(args.device_id)
    if requested_device_ids:
        previous_devices = set(_run_device_ids(previous_run_id))
        current_devices = set(_run_device_ids(current_run_id))
        device_ids = [
            device_id
            for device_id in requested_device_ids
            if device_id in previous_devices and device_id in current_devices
        ]
        missing_device_ids = [
            device_id
            for device_id in requested_device_ids
            if device_id not in previous_devices or device_id not in current_devices
        ]
        if not device_ids:
            raise ValueError(
                "none of the requested validation devices exists in both datasets: "
                + ", ".join(requested_device_ids)
            )
    else:
        device_ids = []
        missing_device_ids = []

    previous_state = normalize_state_from_params(
        {
            "run_id": previous_run_id,
            "update_current_state": False,
            "device_ids": device_ids or None,
        }
    )
    current_state = normalize_state_from_params(
        {
            "run_id": current_run_id,
            "update_current_state": False,
            "device_ids": device_ids or None,
        }
    )
    change_result = detect_changes_from_params(
        {
            "previous_run_id": previous_run_id,
            "current_run_id": current_run_id,
            "use_state_observations": True,
            "device_ids": device_ids or None,
            "persist": True,
        }
    )
    event_result = build_events_from_params(
        {
            "changes": change_result.get("changes") or [],
            "persist": True,
            "publish": False,
        }
    )
    generated_report = render_run_report(
        run_id=current_run_id,
        output_path=str(output_dir / f"phase1_6_patrol_report_{generated_at}.md"),
        output_format="markdown",
    )
    summary = {
        "success": True,
        "generated_at": generated_at,
        "previous_source": str(previous_source),
        "current_source": str(current_source),
        "previous_run_id": previous_run_id,
        "current_run_id": current_run_id,
        "validation_scope": "selected_devices" if device_ids else "all_imported_devices",
        "validation_device_ids": device_ids,
        "missing_validation_device_ids": missing_device_ids,
        "previous_preview": previous_preview,
        "current_preview": current_preview,
        "previous_import": previous_import,
        "current_import": current_import,
        "previous_snapshots": _snapshot_summary(
            previous_run_id,
            device_ids=device_ids or None,
        ),
        "current_snapshots": _snapshot_summary(
            current_run_id,
            device_ids=device_ids or None,
        ),
        "previous_all_snapshots": _snapshot_summary(previous_run_id),
        "current_all_snapshots": _snapshot_summary(current_run_id),
        "previous_state": {
            key: value for key, value in previous_state.items() if key != "results"
        },
        "current_state": {
            key: value for key, value in current_state.items() if key != "results"
        },
        "change_result": change_result,
        "event_result": event_result,
        "generated_report": generated_report,
    }
    json_path = output_dir / f"phase1_6_e2e_summary_{generated_at}.json"
    md_path = output_dir / f"phase1_6_e2e_summary_{generated_at}.md"
    json_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    _write_markdown(md_path, summary)
    summary["summary_json_path"] = str(json_path)
    summary["summary_markdown_path"] = str(md_path)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Phase 1-6 E2E validation")
    parser.add_argument(
        "--previous-source",
        default=str(PROJECT_ROOT / "dev_test1"),
        help="previous-quarter offline patrol directory",
    )
    parser.add_argument(
        "--current-source",
        default=str(PROJECT_ROOT / "dev_test"),
        help="current-quarter offline patrol directory",
    )
    parser.add_argument(
        "--output-dir",
        default=str(PROJECT_ROOT / ".runtime" / "test-reports"),
        help="directory for aggregate validation reports",
    )
    parser.add_argument("--previous-run-id")
    parser.add_argument("--current-run-id")
    parser.add_argument("--previous-dataset-id")
    parser.add_argument("--current-dataset-id")
    parser.add_argument(
        "--device-id",
        action="append",
        help=(
            "limit validation to one or more device IDs; repeat the option or pass "
            "comma-separated values. Import preview still scans the whole directory."
        ),
    )
    args = parser.parse_args()
    try:
        result = run(args)
    except Exception as exc:
        print(json.dumps(_failure_payload(exc), ensure_ascii=False, default=str))
        return 2
    print(
        json.dumps(
            {
                "success": result["success"],
                "previous_run_id": result["previous_run_id"],
                "current_run_id": result["current_run_id"],
                "previous_observations": result["previous_state"]["observation_count"],
                "current_observations": result["current_state"]["observation_count"],
                "change_count": result["change_result"]["change_count"],
                "event_count": result["event_result"]["event_count"],
                "summary_json_path": result["summary_json_path"],
                "summary_markdown_path": result["summary_markdown_path"],
            },
            ensure_ascii=False,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
