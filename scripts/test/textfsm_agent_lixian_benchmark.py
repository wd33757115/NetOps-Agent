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
    "Q2": {"file_count": 6, "device_count": 6, "command_count": 152},
    "Q3": {"file_count": 6, "device_count": 6, "command_count": 162},
}
PERIOD_PROFILE_SAMPLE_COUNTS = {
    "Q1": {("H3C", "S6850-56HF"): 2},
    "Q2": {
        ("H3C", "S6850-56HF"): 2,
        ("H3C", "S5130S-52S-HI"): 1,
        ("H3C", "S5130S-54S-HI-G"): 1,
        ("H3C", "MSR5620"): 2,
    },
    "Q3": {
        ("H3C", "S6850-56HF"): 2,
        ("H3C", "S5130S-52S-HI"): 1,
        ("H3C", "S5130S-54S-HI-G"): 1,
        ("H3C", "MSR5620"): 2,
    },
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
        metadata={"dataset_id": f"lixian-{period.lower()}-benchmark"},
    )
    baseline = PERIOD_BASELINES[period]
    for field, expected in baseline.items():
        _assert_equal(preview.get(field), expected, f"{period}.{field}")
    observed_profiles: dict[tuple[str, str], int] = {}
    for item in preview.get("files") or []:
        _assert_equal(item.get("vendor"), "H3C", f"{period}.vendor")
        _assert_equal(item.get("identity_status"), "known", f"{period}.identity")
        if not item.get("device_name") or not item.get("management_ip"):
            raise AssertionError(f"{period}: device name or management IP is missing")
        profile = (str(item.get("vendor") or ""), str(item.get("model") or ""))
        observed_profiles[profile] = observed_profiles.get(profile, 0) + 1
    _assert_equal(
        observed_profiles,
        PERIOD_PROFILE_SAMPLE_COUNTS[period],
        f"{period}.profiles",
    )
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
    expected_profiles: dict[tuple[str, str], int],
    max_expected_retries: int = 2,
    require_generic_provider: bool = False,
) -> dict[str, Any]:
    result = generate_templates(
        {
            "source_path": str(source_path),
            "command": command,
            "publish": False,
            "dry_run": True,
            "force_generate": True,
            "max_retries": 7,
            "minimum_sample_pass_rate": 1.0,
            "reports_dir": str(reports_dir / period / command.replace(" ", "-")),
        }
    )
    _assert_equal(
        result.get("candidate_groups"),
        len(expected_profiles),
        f"{period}.{command}.groups",
    )
    _assert_equal(result.get("success"), True, f"{period}.{command}.success")
    _assert_equal(
        result.get("validation_pass_rate"),
        1.0,
        f"{period}.{command}.pass_rate",
    )
    reports = result.get("reports") or []
    _assert_equal(len(reports), len(expected_profiles), f"{period}.{command}.reports")
    observed_profiles: dict[tuple[str, str], int] = {}
    assets: list[dict[str, Any]] = []
    for report in reports:
        profile = (str(report.get("vendor") or ""), str(report.get("model") or ""))
        label = f"{period}.{profile[0]}.{profile[1]}.{command}"
        observed_profiles[profile] = int(report.get("source_sample_count") or 0)
        _assert_equal(report.get("accepted"), True, f"{label}.accepted")
        _assert_equal(report.get("validation_pass_rate"), 1.0, f"{label}.pass_rate")
        _assert_equal(
            report.get("passed_samples"),
            report.get("sample_count"),
            f"{label}.passed",
        )
        retry_count = int(report.get("retry_count") or 0)
        if retry_count > max_expected_retries:
            raise AssertionError(
                f"{label}.retries: expected <= {max_expected_retries}, got {retry_count}"
            )
        _assert_equal(report.get("validation_score"), 100, f"{label}.validation")
        if int(report.get("record_count") or 0) <= 0:
            raise AssertionError(f"{label}: no records parsed")
        agent_loop = report.get("agent_loop") or {}
        _assert_equal(agent_loop.get("status"), "passed", f"{label}.agent")
        provider = str(agent_loop.get("best_candidate_provider") or "")
        if require_generic_provider and not provider.startswith("generic_"):
            raise AssertionError(
                f"{label}: automatic path used non-generic provider {provider!r}"
            )
        if not require_generic_provider and not (
            provider.startswith("generic_") or provider == "llm"
        ):
            raise AssertionError(
                f"{label}: automatic path used unsupported provider {provider!r}"
            )
        hard_gates = (agent_loop.get("validation_report") or {}).get("hard_gates") or {}
        _assert_equal(set(hard_gates), EXPECTED_HARD_GATES, f"{label}.hard_gate_names")
        failed_gates = sorted(name for name, passed in hard_gates.items() if not passed)
        _assert_equal(failed_gates, [], f"{label}.hard_gates")
        assets.append(
            {
                "vendor": profile[0],
                "model": profile[1],
                "source_sample_count": observed_profiles[profile],
                "sample_count": report.get("sample_count"),
                "record_count": report.get("record_count"),
                "retry_count": retry_count,
                "provider": provider,
                "hard_gates": len(hard_gates),
            }
        )
    _assert_equal(observed_profiles, expected_profiles, f"{period}.{command}.profiles")
    return {
        "asset_count": len(assets),
        "source_sample_count": sum(expected_profiles.values()),
        "record_count": sum(int(asset["record_count"] or 0) for asset in assets),
        "assets": assets,
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
                expected_profiles=PERIOD_PROFILE_SAMPLE_COUNTS[period],
            )
            for command in GOLDEN_COMMANDS
        }
        periods[period] = {
            "preview": _preview_period(source_path, period),
            "commands": command_results,
        }
    corpus_profiles: dict[tuple[str, str], int] = {}
    for period_profiles in PERIOD_PROFILE_SAMPLE_COUNTS.values():
        for profile, count in period_profiles.items():
            corpus_profiles[profile] = corpus_profiles.get(profile, 0) + count
    corpus_results = {
        command: _generate_command(
            source_path=source_root,
            reports_dir=reports_dir,
            period="CORPUS",
            command=command,
            expected_profiles=corpus_profiles,
            max_expected_retries=2,
            require_generic_provider=False,
        )
        for command in GOLDEN_COMMANDS
    }
    return {
        "success": True,
        "source_root": str(source_root),
        "periods": periods,
        "canonical_corpus": {
            "sample_count": sum(corpus_profiles.values()),
            "asset_count": len(corpus_profiles),
            "commands": corpus_results,
        },
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
