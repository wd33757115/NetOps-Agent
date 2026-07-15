#!/usr/bin/env python
"""Export an immutable leadership report bundle for an offline patrol job."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.patrol.leadership_report import (  # noqa: E402
    render_leadership_docx,
    render_leadership_json,
    render_leadership_markdown,
)
from src.core.patrol.offline_jobs import OfflinePatrolJobService  # noqa: E402


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def export_report_bundle(
    *,
    job_id: str,
    output_dir: Path,
    analysis_attempt_id: str | None = None,
) -> dict[str, Any]:
    report = OfflinePatrolJobService().build_leadership_report_data(
        job_id,
        analysis_attempt_id=analysis_attempt_id,
    )
    artifacts = {
        "network-patrol-leadership-report.docx": render_leadership_docx(report),
        "network-patrol-leadership-report.json": render_leadership_json(report),
        "network-patrol-leadership-report.md": render_leadership_markdown(report).encode(
            "utf-8"
        ),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    files: list[dict[str, Any]] = []
    for filename, content in artifacts.items():
        path = output_dir / filename
        path.write_bytes(content)
        files.append(
            {
                "name": filename,
                "bytes": len(content),
                "sha256": _sha256(content),
            }
        )

    evidence = dict(report.get("evidence") or {})
    manifest = {
        "schema_version": "offline-patrol-leadership-delivery/v1",
        "job_id": job_id,
        "report_generated_at": report.get("generated_at"),
        "analysis": evidence.get("analysis"),
        "previous_analysis": evidence.get("previous_analysis"),
        "metrics": report.get("metrics"),
        "risk_summary": report.get("risk_summary"),
        "event_summary": report.get("event_summary"),
        "files": files,
    }
    manifest_content = json.dumps(
        manifest,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_bytes(manifest_content)
    return {
        "success": True,
        "job_id": job_id,
        "analysis_attempt_id": (manifest.get("analysis") or {}).get("attempt_id"),
        "analysis_revision": (manifest.get("analysis") or {}).get("revision"),
        "output_dir": str(output_dir),
        "files": [
            *files,
            {
                "name": manifest_path.name,
                "bytes": len(manifest_content),
                "sha256": _sha256(manifest_content),
            },
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export a traceable leadership report for an offline patrol job"
    )
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--analysis-attempt-id")
    parser.add_argument(
        "--output-dir",
        default=str(PROJECT_ROOT / ".runtime" / "deliverables" / "offline-patrol"),
    )
    args = parser.parse_args()
    try:
        result = export_report_bundle(
            job_id=args.job_id,
            output_dir=Path(args.output_dir).resolve(),
            analysis_attempt_id=args.analysis_attempt_id,
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "success": False,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                ensure_ascii=False,
            )
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
