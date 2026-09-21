# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Write per-template and batch TextFSM generation reports."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

BEIJING_TZ = timezone(timedelta(hours=8))


@dataclass
class GenerationReport:
    vendor: str
    model: str
    command: str
    category: str
    template_generated: bool
    accepted: bool
    compile_success: bool
    record_count: int
    field_coverage: int
    validation_score: int
    retry_count: int
    template_path: str | None = None
    skipped_reason: str | None = None
    errors: list[str] | None = None
    parsed_records: list[dict[str, Any]] | None = None
    mode: str = "database"
    family: str | None = None
    entity_type: str | None = None
    primary_keys: list[str] | None = None
    sample_count: int = 1
    source_sample_count: int | None = None
    unique_sample_count: int | None = None
    duplicate_sample_count: int | None = None
    passed_samples: int = 0
    validation_pass_rate: float = 0.0
    sample_results: list[dict[str, Any]] | None = None
    confidence: float | None = None
    evidence_command: str | None = None
    evidence_text: str | None = None
    agent_loop: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in asdict(self).items()
            if value is not None and value != []
        }


def _safe_name(value: str) -> str:
    return "".join(char if char.isalnum() or char in "._-" else "_" for char in value)


def save_report(report: GenerationReport, reports_dir: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(BEIJING_TZ).strftime("%Y%m%d_%H%M%S_%f")
    filename = "_".join(
        [
            _safe_name(report.vendor),
            _safe_name(report.model),
            _safe_name(report.command)[:50],
            timestamp,
        ]
    )
    path = reports_dir / f"{filename}.json"
    path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def save_summary(
    reports: list[GenerationReport],
    reports_dir: Path,
    *,
    metadata: dict[str, Any] | None = None,
) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(BEIJING_TZ).strftime("%Y%m%d_%H%M%S_%f")
    path = reports_dir / f"summary_{timestamp}.json"
    payload = {
        "generated_at": datetime.now(BEIJING_TZ).isoformat(),
        "total": len(reports),
        "success": sum(1 for report in reports if report.accepted),
        "published": sum(1 for report in reports if report.template_generated),
        "metadata": metadata or {},
        "reports": [report.to_dict() for report in reports],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
