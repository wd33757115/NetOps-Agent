#!/usr/bin/env python
"""Local acceptance benchmark for the bounded Simple Parser pipeline.

The full ``lixian`` corpus remains local-only. This harness deliberately calls
the pure generation service instead of bypassing the production ``job_id``
entry. CI uses minimized fixtures for the same compiler and validation gates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GENERATOR_SCRIPTS = PROJECT_ROOT / "src" / "skills" / "textfsm-generator" / "scripts"
for import_path in (PROJECT_ROOT, GENERATOR_SCRIPTS):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from config_loader import load_command_mapping  # noqa: E402
from directory_reader import TemplateSample, discover_directory_candidates  # noqa: E402

from src.core.parser.assets import TemplateAssetStore  # noqa: E402
from src.core.parser.policy import SHARED_PARSER_CONFIG_ROOT  # noqa: E402
from src.core.parser.repository import ParserTemplateRepository  # noqa: E402
from src.core.parser_generation import (  # noqa: E402
    FieldSpec,
    KeyValueCompiler,
    ParserModelTurn,
    ParserSample,
    ParserSpec,
    RepeatedBlockCompiler,
    SampleExpectation,
    SampleRole,
    SampleSet,
    SimpleParserGenerationService,
    TableCompiler,
    TemplateCandidate,
    TextFSMValidationBackend,
)
from src.core.patrol.raw_importer import preview_raw_path  # noqa: E402

PERIOD_BASELINES = {
    "Q1": {"file_count": 2, "device_count": 2, "command_count": 54},
    "Q2": {"file_count": 6, "device_count": 6, "command_count": 152},
    "Q3": {"file_count": 6, "device_count": 6, "command_count": 162},
}
GOLDEN_COMMANDS = (
    "display cpu",
    "display interface brief",
    "display ip interface brief",
    "display transceiver diagnosis interface",
)


class _WeakModelSentinel:
    """An intentionally useless model; golden layouts must compile without it."""

    def __init__(self) -> None:
        self.calls = 0

    def open_session(self, _spec, _samples):
        sentinel = self

        class _Session:
            def next(self, _validation_feedback=None) -> ParserModelTurn:
                sentinel.calls += 1
                return ParserModelTurn(
                    candidate=TemplateCandidate(
                        "Start\n  ^never$$\n",
                        "weak-model:native_tool_call",
                    ),
                    submission_mode="native_tool_call",
                )

        return _Session()


def _assert_equal(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def _interface_spec(command: str) -> ParserSpec:
    status_labels = ("Physical",) if command == "display ip interface brief" else ("Link",)
    command_fields = (
        (
            FieldSpec(
                "protocol",
                False,
                "regex",
                pattern=r"(?:up|down|--)(?:\(s\))?",
                source_labels=("Protocol",),
            ),
            FieldSpec(
                "ip_address_mask",
                False,
                "regex",
                pattern=r"(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?|--",
                source_labels=("IP address/Mask", "IP address"),
            ),
            FieldSpec(
                "vpn_instance",
                False,
                "regex",
                pattern=r"\S+",
                source_labels=("VPN instance",),
            ),
            FieldSpec("description", False, source_labels=("Description",)),
        )
        if command == "display ip interface brief"
        else (
            FieldSpec(
                "protocol",
                False,
                "regex",
                pattern=r"(?:UP|DOWN|--)(?:\(s\))?",
                source_labels=("Protocol",),
            ),
            FieldSpec(
                "ip_address",
                False,
                "regex",
                pattern=r"(?:\d{1,3}\.){3}\d{1,3}|--",
                source_labels=("Primary IP",),
            ),
            FieldSpec(
                "speed",
                False,
                "regex",
                pattern=r"(?:\d+[GM](?:\(a\))?|auto)",
                source_labels=("Speed",),
            ),
            FieldSpec(
                "duplex",
                False,
                "regex",
                pattern=r"(?:(?:F|H|A)(?:\(a\))?|auto)",
                source_labels=("Duplex",),
            ),
            FieldSpec(
                "type",
                False,
                "enum",
                values=("A", "T", "H"),
                source_labels=("Type",),
            ),
            FieldSpec("pvid", False, "number", source_labels=("PVID",)),
            FieldSpec("description", False, source_labels=("Description",)),
        )
    )
    return ParserSpec(
        entity="interface",
        primary_keys=("interface",),
        fields=(
            FieldSpec("interface", True, "interface_name", source_labels=("Interface",)),
            FieldSpec(
                "status",
                True,
                "enum",
                values=("UP", "DOWN", "ADM"),
                source_labels=status_labels,
            ),
            *command_fields,
        ),
    )


def _optical_spec() -> ParserSpec:
    return ParserSpec(
        entity="interface_optical",
        primary_keys=("interface",),
        fields=(
            FieldSpec("interface", True, "interface_name"),
            FieldSpec(
                "transceiver_present",
                False,
                "enum",
                values=("present", "absent"),
                source_labels=("The transceiver is/absent",),
            ),
            FieldSpec(
                "current_temp_c",
                False,
                "number",
                source_labels=("Current diagnostic parameters/Temp.(C)",),
            ),
            FieldSpec(
                "current_voltage_v",
                False,
                "number",
                source_labels=("Current diagnostic parameters/Voltage(V)",),
            ),
            FieldSpec(
                "current_bias_ma",
                False,
                "number",
                source_labels=("Current diagnostic parameters/Bias(mA)",),
            ),
            FieldSpec(
                "rx_power_dbm",
                False,
                "number",
                source_labels=("Current diagnostic parameters/RX power(dBm)",),
            ),
            FieldSpec(
                "tx_power_dbm",
                False,
                "number",
                source_labels=("Current diagnostic parameters/TX power(dBm)",),
            ),
            *tuple(
                FieldSpec(
                    f"{level}_{boundary}_{metric}",
                    False,
                    "number",
                    source_labels=(
                        f"{level.title()} thresholds/{boundary.title()}/{source_label}",
                    ),
                )
                for level in ("alarm", "warning")
                for boundary in ("low", "high")
                for metric, source_label in (
                    ("temp_c", "Temp.(C)"),
                    ("voltage_v", "Voltage(V)"),
                    ("bias_ma", "Bias(mA)"),
                )
            ),
            *tuple(
                FieldSpec(
                    f"{direction}_{boundary}_{level}_dbm",
                    False,
                    "number",
                    source_labels=(
                        f"{level.title()} thresholds/{boundary.title()}/"
                        f"{direction.upper()} power(dBm)",
                    ),
                )
                for level in ("alarm", "warning")
                for boundary in ("low", "high")
                for direction in ("rx", "tx")
            ),
        ),
    )


def _cpu_spec() -> ParserSpec:
    return ParserSpec(
        entity="processor",
        primary_keys=("slot", "cpu"),
        fields=(
            FieldSpec("slot", True, "number", source_labels=("Slot",)),
            FieldSpec("cpu", True, "number", source_labels=("CPU",)),
            FieldSpec(
                "utilization_5s",
                True,
                "percent",
                source_labels=("in last 5 seconds",),
            ),
            FieldSpec(
                "utilization_1m",
                True,
                "percent",
                source_labels=("in last 1 minute",),
            ),
            FieldSpec(
                "utilization_5m",
                True,
                "percent",
                source_labels=("in last 5 minutes",),
            ),
        ),
    )


def _spec_for(command: str) -> ParserSpec:
    if command == "display cpu":
        return _cpu_spec()
    if command == "display transceiver diagnosis interface":
        return _optical_spec()
    return _interface_spec(command)


def _expected_keys(command: str, raw_text: str) -> tuple[tuple[str, ...], ...]:
    if command == "display cpu":
        keys = [
            (match.group(1), match.group(2))
            for line in raw_text.splitlines()
            if (
                match := re.match(
                    r"^\s*Slot\s+(\d+)\s+CPU\s+(\d+)\s+CPU usage:\s*$",
                    line,
                    re.IGNORECASE,
                )
            )
        ]
        if not keys:
            raise AssertionError(
                f"{command}: independent entity extractor found no keys"
            )
        return tuple(keys)
    if command == "display transceiver diagnosis interface":
        keys = [
            match.group(1)
            for line in raw_text.splitlines()
            if (
                match := re.match(
                    r"^\s*(\S+)\s+transceiver diagnostic information:\s*$",
                    line,
                    re.IGNORECASE,
                )
            )
        ]
    else:
        keys = []
        in_table = False
        for line in raw_text.splitlines():
            stripped = line.strip()
            if stripped.startswith("Interface") and (
                " Link " in f" {stripped} " or " Physical " in f" {stripped} "
            ):
                in_table = True
                continue
            if not stripped:
                in_table = False
                continue
            if in_table and not stripped.endswith(":"):
                key = stripped.split()[0]
                if key not in keys:
                    keys.append(key)
    if not keys:
        raise AssertionError(f"{command}: independent entity extractor found no keys")
    return tuple((key,) for key in keys)


def _preview_period(path: Path, period: str) -> dict[str, Any]:
    preview = preview_raw_path(
        file_path=path,
        mode="parser_validation",
        metadata={"dataset_id": f"lixian-{period.lower()}-simple-parser-benchmark"},
    )
    for field, expected in PERIOD_BASELINES[period].items():
        _assert_equal(preview.get(field), expected, f"{period}.{field}")
    if int(preview.get("identity_unknown") or 0) != 0:
        raise AssertionError(f"{period}: device identity contains unknown records")
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


def _discover_corpus(source_root: Path) -> dict[tuple[str, str, str], list[TemplateSample]]:
    mappings = load_command_mapping(SHARED_PARSER_CONFIG_ROOT / "command_policy.yaml")
    groups: dict[tuple[str, str, str], list[TemplateSample]] = {}
    seen: dict[tuple[str, str, str], set[tuple[str, str]]] = {}
    for period in PERIOD_BASELINES:
        discovery = discover_directory_candidates(source_root / period, mappings=mappings)
        if discovery.unresolved_files:
            raise AssertionError(f"{period}: unresolved files: {discovery.unresolved_files}")
        for candidate in discovery.candidates:
            if candidate.command not in GOLDEN_COMMANDS:
                continue
            key = (candidate.vendor, candidate.model, candidate.command)
            groups.setdefault(key, [])
            seen.setdefault(key, set())
            for sample in candidate.samples:
                digest = hashlib.sha256(sample.output.encode("utf-8")).hexdigest()
                evidence_id = (str(sample.file_path), digest)
                if evidence_id not in seen[key]:
                    groups[key].append(sample)
                    seen[key].add(evidence_id)
    return groups


def _run_asset(
    identity: tuple[str, str, str],
    samples: list[TemplateSample],
    *,
    active_template: dict[str, Any] | None,
) -> dict[str, Any]:
    vendor, model, command = identity
    if len(samples) < 2:
        raise AssertionError(f"{vendor}.{model}.{command}: fewer than two unique samples")
    parser_samples: list[ParserSample] = []
    for index, sample in enumerate(samples):
        role = SampleRole.GENERATION if index == 0 else SampleRole.ACCEPTANCE
        keys = _expected_keys(command, sample.output)
        parser_samples.append(
            ParserSample(
                sample_id=f"sample-{index + 1}",
                raw_text=sample.output,
                prompt_text=sample.prompt_output if role is SampleRole.GENERATION else None,
                role=role,
                expectation=SampleExpectation(
                    record_count=len(keys),
                    primary_keys=frozenset(keys),
                ),
            )
        )
    model_sentinel = _WeakModelSentinel()
    backend = TextFSMValidationBackend()
    spec = _spec_for(command)
    outcome = SimpleParserGenerationService(
        compilers=(TableCompiler(), KeyValueCompiler(), RepeatedBlockCompiler()),
        model=model_sentinel,
        backend=backend,
    ).generate(
        job_id=f"benchmark:{vendor}:{model}:{command}",
        spec=spec,
        sample_set=SampleSet(tuple(parser_samples)),
    )
    if not outcome.passed:
        diagnostics = [
            diagnostic
            for attempt in outcome.attempts
            for report in (attempt.generation_validation, attempt.acceptance_validation)
            if report is not None
            for diagnostic in report.diagnostics
        ]
        raise AssertionError(
            f"{vendor}.{model}.{command}: {outcome.stop_reason}; diagnostics={diagnostics[:10]}"
        )
    if outcome.model_calls != 0 or model_sentinel.calls != 0:
        raise AssertionError(f"{vendor}.{model}.{command}: golden asset unexpectedly used LLM")
    final_attempt = next(
        attempt for attempt in reversed(outcome.attempts) if attempt.candidate == outcome.candidate
    )
    record_count = sum(
        sample.record_count
        for report in (
            final_attempt.generation_validation,
            final_attempt.acceptance_validation,
        )
        if report is not None
        for sample in report.samples
        if sample.role is not SampleRole.NEGATIVE
    )
    new_fields = sorted(
        {
            field
            for report in (
                final_attempt.generation_validation,
                final_attempt.acceptance_validation,
            )
            if report is not None
            for sample in report.samples
            for record in sample.records
            for field in record
        }
    )
    new_records_by_sample = [
        [dict(record) for record in sample.records]
        for report in (
            final_attempt.generation_validation,
            final_attempt.acceptance_validation,
        )
        if report is not None
        for sample in report.samples
        if sample.role is not SampleRole.NEGATIVE
    ]
    active_comparison = _compare_active_template(
        active_template=active_template,
        samples=samples,
        command=command,
        backend=backend,
        new_fields=new_fields,
        new_records_by_sample=new_records_by_sample,
    )
    return {
        "vendor": vendor,
        "model": model,
        "command": command,
        "sample_count": len(parser_samples),
        "record_count": record_count,
        "provider": outcome.candidate.provider if outcome.candidate else None,
        "model_calls": outcome.model_calls,
        "hard_gates": final_attempt.generation_validation.hard_gates,
        "active_comparison": active_comparison,
    }


def _compare_active_template(
    *,
    active_template: dict[str, Any] | None,
    samples: list[TemplateSample],
    command: str,
    backend: TextFSMValidationBackend,
    new_fields: list[str],
    new_records_by_sample: list[list[dict[str, str]]],
) -> dict[str, Any]:
    if active_template is None:
        return {"status": "not_available", "sample_count": 0, "passed_samples": 0}
    try:
        template_text = TemplateAssetStore().read(
            str(active_template.get("asset_path") or ""),
            str(active_template.get("sha256") or ""),
            active_template.get("template_text"),
        )
    except ValueError:
        return {
            "status": "asset_missing",
            "template_id": active_template.get("id"),
            "sample_count": len(samples),
            "passed_samples": 0,
        }
    compile_errors = backend.compile(template_text)
    if compile_errors:
        return {
            "status": "compile_failed",
            "template_id": active_template.get("id"),
            "sample_count": len(samples),
            "passed_samples": 0,
            "errors": list(compile_errors),
        }
    rows: list[dict[str, Any]] = []
    active_fields: set[str] = set()
    for sample_index, sample in enumerate(samples):
        expected_keys = {key[0] for key in _expected_keys(command, sample.output)}
        try:
            records = backend.parse(template_text, sample.output)
            actual_keys = {
                str(record.get("interface") or "").strip()
                for record in records
                if str(record.get("interface") or "").strip()
            }
            active_fields.update(field for record in records for field in record)
            new_records = (
                new_records_by_sample[sample_index]
                if sample_index < len(new_records_by_sample)
                else []
            )
            old_by_key = {
                str(record.get("interface") or "").strip(): record for record in records
            }
            new_by_key = {
                str(record.get("interface") or "").strip(): record for record in new_records
            }
            shared_fields = sorted(
                (set().union(*(record.keys() for record in records)) if records else set())
                & (
                    set().union(*(record.keys() for record in new_records))
                    if new_records
                    else set()
                )
                - {"interface"}
            )
            value_mismatches = [
                {
                    "primary_key": key,
                    "field": field,
                    "active": str(old_by_key[key].get(field) or ""),
                    "simple_v2": str(new_by_key[key].get(field) or ""),
                }
                for key in sorted(set(old_by_key).intersection(new_by_key))
                for field in shared_fields
                if str(old_by_key[key].get(field) or "")
                != str(new_by_key[key].get(field) or "")
            ]
            passed = (
                actual_keys == expected_keys
                and len(records) == len(expected_keys)
                and not value_mismatches
            )
            errors = []
            if actual_keys != expected_keys or len(records) != len(expected_keys):
                errors.append("record count or primary key set differs from corpus")
            if value_mismatches:
                errors.append("shared field values differ from the active Parser")
        except Exception as exc:
            records = []
            passed = False
            errors = [str(exc)]
            value_mismatches = []
        rows.append(
            {
                "source": str(sample.file_path),
                "passed": passed,
                "expected_records": len(expected_keys),
                "actual_records": len(records),
                "errors": errors,
                "value_mismatch_count": len(value_mismatches),
                "value_mismatches": value_mismatches[:20],
            }
        )
    passed_samples = sum(1 for row in rows if row["passed"])
    return {
        "status": "passed" if passed_samples == len(rows) else "failed",
        "template_id": active_template.get("id"),
        "sample_count": len(rows),
        "passed_samples": passed_samples,
        "active_fields": sorted(active_fields),
        "new_fields": new_fields,
        "added_fields": sorted(set(new_fields) - active_fields),
        "removed_fields": sorted(active_fields - set(new_fields)),
        "schema_compatible": active_fields.issubset(set(new_fields)),
        "samples": rows,
    }


def run_benchmark(*, source_root: Path, reports_dir: Path) -> dict[str, Any]:
    periods = {
        period: _preview_period(source_root / period, period) for period in PERIOD_BASELINES
    }
    groups = _discover_corpus(source_root)
    expected_asset_count = len(GOLDEN_COMMANDS) * 4
    _assert_equal(len(groups), expected_asset_count, "corpus.asset_count")
    template_repository = ParserTemplateRepository()
    active_templates = {
        (
            str(item.get("vendor") or ""),
            str(item.get("model") or item.get("profile") or ""),
            str(item.get("canonical_command") or ""),
        ): {
            **item,
            "template_text": getattr(
                template_repository.get_template(str(item.get("id") or "")),
                "template_text",
                None,
            ),
        }
        for item in template_repository.list_templates(status="active")
    }
    assets = [
        _run_asset(
            identity,
            samples,
            active_template=active_templates.get(identity),
        )
        for identity, samples in sorted(groups.items())
    ]
    comparable_assets = [
        item for item in assets if item["active_comparison"]["status"] != "not_available"
    ]
    active_regressions = [
        item
        for item in comparable_assets
        if item["active_comparison"]["status"] != "passed"
    ]
    active_schema_regressions = [
        item
        for item in comparable_assets
        if item["active_comparison"].get("schema_compatible") is not True
    ]
    result = {
        "success": True,
        "pipeline_version": "simple_v2",
        "source_root": str(source_root),
        "periods": periods,
        "asset_count": len(assets),
        "sample_count": sum(int(item["sample_count"]) for item in assets),
        "record_count": sum(int(item["record_count"]) for item in assets),
        "llm_call_count": sum(int(item["model_calls"]) for item in assets),
        "migration": {
            "active_comparable_assets": len(comparable_assets),
            "active_baseline_passed": len(comparable_assets) - len(active_regressions),
            "active_baseline_failed": len(active_regressions),
            "active_schema_regressions": len(active_schema_regressions),
            "legacy_new_job_intake_enabled": False,
            "legacy_code_removal_ready": not active_regressions
            and not active_schema_regressions,
        },
        "assets": assets,
    }
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "simple-parser-lixian-benchmark.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the local Q1/Q2/Q3 Simple Parser v2 acceptance benchmark"
    )
    parser.add_argument("--source-root", default=str(PROJECT_ROOT / "lixian"))
    parser.add_argument(
        "--reports-dir",
        default=str(PROJECT_ROOT / ".tmp" / "simple-parser-lixian-benchmark"),
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
