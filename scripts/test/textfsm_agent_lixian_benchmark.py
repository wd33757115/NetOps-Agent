#!/usr/bin/env python
"""Local acceptance benchmark for the Parser Template Agent.

The full ``lixian`` corpus remains local-only. CI should use the minimized,
sanitized unit fixtures that exercise the same providers and gates.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GENERATOR_SCRIPTS = PROJECT_ROOT / "src" / "skills" / "textfsm-generator" / "scripts"
for import_path in (PROJECT_ROOT, GENERATOR_SCRIPTS):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from generate_templates import generate_templates  # noqa: E402

from src.core.patrol.raw_importer import preview_raw_path  # noqa: E402

PERIOD_BASELINES = {
    "Q1": {"file_count": 2, "device_count": 2, "command_count": 54},
    "Q2": {"file_count": 2, "device_count": 2, "command_count": 44},
    "Q3": {"file_count": 2, "device_count": 2, "command_count": 54},
}
GOLDEN_COMMANDS = (
    "display interface brief",
    "display ip interface brief",
    "display transceiver diagnosis interface",
)
EXPECTED_HARD_GATES = {
    "dialect_compatible",
    "compiled",
    "executed",
    "contract_valid",
    "key_valid",
    "entity_complete",
    "record_evidence_aligned",
    "variant_coverage",
    "empty_classified",
    "holdout_passed",
    "regression_passed",
    "negative_passed",
    "evidence_coverage",
}


def _assert_equal(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def _preview_period(path: Path, period: str) -> dict[str, Any]:
    preview = preview_raw_path(
        file_path=path,
        mode="parser_validation",
        vendor="H3C",
        model="S6850-56HF",
        metadata={"dataset_id": f"lixian-{period.lower()}-benchmark"},
    )
    baseline = PERIOD_BASELINES[period]
    for field, expected in baseline.items():
        _assert_equal(preview.get(field), expected, f"{period}.{field}")
    for item in preview.get("files") or []:
        _assert_equal(item.get("vendor"), "H3C", f"{period}.vendor")
        _assert_equal(item.get("model"), "S6850-56HF", f"{period}.model")
        _assert_equal(item.get("identity_status"), "known", f"{period}.identity")
        if not item.get("device_name") or not item.get("management_ip"):
            raise AssertionError(f"{period}: device name or management IP is missing")
    return {
        field: preview.get(field)
        for field in (
            "file_count",
            "device_count",
            "command_count",
            "ready",
            "template_missing",
            "parse_failed",
            "ignored",
            "identity_unknown",
        )
    }


def _generate_command(
    *,
    source_path: Path,
    reports_dir: Path,
    period: str,
    command: str,
) -> dict[str, Any]:
    result = generate_templates(
        {
            "source_path": str(source_path),
            "vendor": "H3C",
            "model": "S6850-56HF",
            "command": command,
            "publish": False,
            "dry_run": True,
            "force_generate": True,
            "max_retries": 7,
            "minimum_sample_pass_rate": 1.0,
            "reports_dir": str(reports_dir / period / command.replace(" ", "-")),
        }
    )
    _assert_equal(result.get("candidate_groups"), 1, f"{period}.{command}.groups")
    _assert_equal(result.get("success"), True, f"{period}.{command}.success")
    _assert_equal(
        result.get("validation_pass_rate"),
        1.0,
        f"{period}.{command}.pass_rate",
    )
    reports = result.get("reports") or []
    _assert_equal(len(reports), 1, f"{period}.{command}.reports")
    report = reports[0]
    _assert_equal(report.get("accepted"), True, f"{period}.{command}.accepted")
    _assert_equal(report.get("sample_count"), 2, f"{period}.{command}.samples")
    _assert_equal(report.get("passed_samples"), 2, f"{period}.{command}.passed")
    _assert_equal(report.get("retry_count"), 0, f"{period}.{command}.retries")
    _assert_equal(report.get("validation_score"), 100, f"{period}.{command}.validation")
    if int(report.get("record_count") or 0) <= 0:
        raise AssertionError(f"{period}.{command}: no records parsed")
    agent_loop = report.get("agent_loop") or {}
    _assert_equal(agent_loop.get("status"), "passed", f"{period}.{command}.agent")
    _assert_equal(
        agent_loop.get("template_attempts"),
        1,
        f"{period}.{command}.template_attempts",
    )
    provider = str(agent_loop.get("best_candidate_provider") or "")
    if not provider.startswith("generic_"):
        raise AssertionError(
            f"{period}.{command}: automatic path used non-generic provider {provider!r}"
        )
    hard_gates = (
        (agent_loop.get("validation_report") or {}).get("hard_gates") or {}
    )
    _assert_equal(
        set(hard_gates),
        EXPECTED_HARD_GATES,
        f"{period}.{command}.hard_gate_names",
    )
    failed_gates = sorted(name for name, passed in hard_gates.items() if not passed)
    _assert_equal(failed_gates, [], f"{period}.{command}.hard_gates")
    return {
        "sample_count": report.get("sample_count"),
        "passed_samples": report.get("passed_samples"),
        "record_count": report.get("record_count"),
        "retry_count": report.get("retry_count"),
        "validation_score": report.get("validation_score"),
        "provider": provider,
        "hard_gates": len(hard_gates),
    }


def run_benchmark(*, source_root: Path, reports_dir: Path) -> dict[str, Any]:
    periods: dict[str, Any] = {}
    for period in PERIOD_BASELINES:
        source_path = source_root / period
        if not source_path.is_dir():
            raise FileNotFoundError(f"missing benchmark period: {source_path}")
        command_results = {
            command: _generate_command(
                source_path=source_path,
                reports_dir=reports_dir,
                period=period,
                command=command,
            )
            for command in GOLDEN_COMMANDS
        }
        periods[period] = {
            "preview": _preview_period(source_path, period),
            "commands": command_results,
        }
    return {
        "success": True,
        "source_root": str(source_root),
        "periods": periods,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the local Q1/Q2/Q3 Parser Agent acceptance benchmark"
    )
    parser.add_argument(
        "--source-root",
        default=str(PROJECT_ROOT / "lixian"),
        help="Directory containing Q1, Q2 and Q3",
    )
    parser.add_argument(
        "--reports-dir",
        default=str(PROJECT_ROOT / ".tmp" / "textfsm-agent-lixian-benchmark"),
    )
    args = parser.parse_args()
    try:
        result = run_benchmark(
            source_root=Path(args.source_root).resolve(),
            reports_dir=Path(args.reports_dir).resolve(),
        )
    except Exception as exc:
        print(json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
