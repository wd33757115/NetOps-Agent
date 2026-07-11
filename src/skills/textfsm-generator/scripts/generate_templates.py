# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""TextFSM parser asset generation for direct, database, and directory inputs."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config_loader import (
    DEFAULT_MAPPING,
    CategorySpec,
    list_device_profiles,
    load_command_mapping,
    normalize_command,
)
from db_reader import TemplateCandidate, discover_missing_template_candidates
from directory_reader import DirectoryCandidate, TemplateSample, discover_directory_candidates
from input_parser import extract_direct_input, infer_vendor_model
from llm_generator import call_llm, generate_template_text
from report_generator import GenerationReport, save_report, save_summary
from template_synthesizer import expected_min_records, synthesize_template
from template_validator import ValidationResult, strip_llm_template, validate_template

from src.core.parser.assets import (
    DEFAULT_ASSET_ROOT as SHARED_TEXTFSM_ROOT,
)
from src.core.parser.assets import (
    TemplateAssetStore,
    safe_slug,
)
from src.core.parser.contract_inference import infer_contract
from src.core.parser.repository import ParserRepository
from src.core.patrol.repository import PatrolRepository

SKILL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPORTS = SKILL_ROOT / "reports"
PARSER_VERSION = "1.0.0"


@dataclass(frozen=True)
class Candidate:
    vendor: str
    model: str
    family: str | None
    command: str
    category: str | None
    samples: tuple[TemplateSample, ...]
    confidence: float | None = None
    evidence_command: str | None = None
    evidence_text: str | None = None


def cmd_to_template_filename(command: str) -> str:
    return f"{safe_slug(command.strip().lower())}.textfsm"


def model_to_dirname(model: str) -> str:
    return safe_slug(model)


def resolve_template_path(templates_dir: Path, model: str, command: str) -> Path:
    return templates_dir / safe_slug(model) / cmd_to_template_filename(command)


def _job_candidate(params: dict[str, Any]) -> Candidate | None:
    job_id = str(params.get("job_id") or "").strip()
    if not job_id:
        return None
    job = ParserRepository().get_job(job_id, include_sanitized_payload=True)
    if not job:
        raise ValueError(f"parser template job does not exist: {job_id}")
    samples: list[TemplateSample] = []
    patrol_repository = PatrolRepository()
    source_mode = str(job.get("source_mode") or "parser_validation")
    for item in job.get("samples") or []:
        if item.get("snapshot_source") != "postgresql":
            raise ValueError("parser job contains a legacy SQLite sample; re-import the dataset")
        if source_mode == "production":
            if item.get("sanitization_status") != "sanitized":
                raise ValueError("production parser sample is not sanitized")
            sample_output = str(item.get("sanitized_text") or "")
            if not sample_output:
                raise ValueError("production parser sample has no sanitized payload")
            samples.append(
                TemplateSample(
                    file_path=f"sanitized-snapshot:{item['snapshot_id']}",
                    device_id=str(item["device_id"]),
                    command=str(job["canonical_command"]),
                    output=sample_output,
                )
            )
            continue
        context = patrol_repository.get_snapshot_context(item["snapshot_id"])
        if not context:
            raise ValueError(
                f"parser job sample is unavailable in PostgreSQL: {item['snapshot_id']}"
            )
        if context["mode"] != "parser_validation":
            raise ValueError("parser job sample mode does not match its job")
        snapshot = context["snapshot"]
        if snapshot:
            samples.append(
                TemplateSample(
                    file_path=f"snapshot:{snapshot.snapshot_id}",
                    device_id=snapshot.device_id,
                    command=snapshot.command_canonical,
                    output=snapshot.raw_text,
                )
            )
    if not samples:
        raise ValueError(f"parser template job has no readable samples: {job_id}")
    return Candidate(
        vendor=job["vendor"],
        model=job["model"] or job["profile"],
        family=job.get("family"),
        command=job["canonical_command"],
        category=None,
        samples=tuple(samples),
        confidence=1.0,
        evidence_text=f"parser job {job_id}",
    )


def _direct_candidate(params: dict[str, Any], mappings: list[Any]) -> Candidate | None:
    direct = extract_direct_input(params)
    if not direct:
        return None
    vendor, model = infer_vendor_model(
        params,
        direct,
        known_devices=list_device_profiles(mappings),
    )
    command = normalize_command(direct.command)
    return Candidate(
        vendor=vendor,
        model=model,
        family=None,
        command=command,
        category=str(params.get("category") or "").strip() or None,
        samples=(
            TemplateSample(
                file_path="direct_input",
                device_id=direct.device_prompt or "chat-input",
                command=command,
                output=direct.raw_output,
            ),
        ),
        confidence=1.0 if params.get("vendor") and params.get("model") else None,
    )


def _database_candidates(items: list[TemplateCandidate]) -> list[Candidate]:
    return [
        Candidate(
            vendor=item.vendor,
            model=item.model,
            family=None,
            command=normalize_command(item.command),
            category=None,
            samples=(
                TemplateSample(
                    file_path=item.source_table,
                    device_id=item.device_id,
                    command=normalize_command(item.command),
                    output=item.sample_output,
                ),
            ),
        )
        for item in items
    ]


def _directory_candidates(items: tuple[DirectoryCandidate, ...]) -> list[Candidate]:
    return [
        Candidate(
            vendor=item.vendor,
            model=item.model,
            family=item.family,
            command=item.command,
            category=item.category,
            samples=item.samples,
            confidence=item.confidence,
            evidence_command=item.evidence_command,
            evidence_text=item.evidence_text,
        )
        for item in items
    ]


def _prompt_samples(samples: tuple[TemplateSample, ...], limit: int) -> str:
    selected = samples[: max(1, limit)]
    parts = [
        f"===== sample {index + 1}: {sample.device_id} / {sample.file_path} =====\n{sample.output}"
        for index, sample in enumerate(selected)
    ]
    return "\n\n".join(parts)


def _deterministic_template(candidate: Candidate, spec: CategorySpec) -> str | None:
    synthesized = synthesize_template(
        command=candidate.command,
        samples=candidate.samples,
        spec=spec,
    )
    return synthesized.template_text if synthesized else None


def _validate_samples(
    template_text: str,
    samples: tuple[TemplateSample, ...],
    spec: CategorySpec,
) -> tuple[list[ValidationResult], list[dict[str, Any]]]:
    validations: list[ValidationResult] = []
    details: list[dict[str, Any]] = []
    for sample in samples:
        result = validate_template(
            template_text,
            sample.output,
            spec.required_fields,
            spec.validators,
            allow_empty=spec.allow_empty,
            empty_patterns=spec.empty_patterns,
            primary_keys=spec.primary_keys,
            expected_min_records=expected_min_records(sample.output, spec),
        )
        validations.append(result)
        details.append(
            {
                "file_path": sample.file_path,
                "device_id": sample.device_id,
                "valid": result.is_valid,
                "record_count": result.record_count,
                "empty_accepted": result.empty_accepted,
                "field_coverage": result.field_coverage,
                "validation_score": result.validation_score,
                "errors": result.errors,
                "key_errors": result.key_errors,
                "completeness_errors": result.completeness_errors,
            }
        )
    return validations, details


def _is_non_template_sample(raw_text: str) -> bool:
    text = (raw_text or "").lower()
    return len(text.strip()) < 20 or any(
        marker in text
        for marker in (
            "connection timed out",
            "connection reset",
            "command execution failed",
            "ssh connection failed",
            "output truncated",
            "invalid command at",
            "unrecognized command found",
            "incomplete command found",
        )
    )


def _regression_samples(candidate: Candidate) -> tuple[TemplateSample, ...]:
    existing_hashes = {
        hashlib.sha256(sample.output.encode("utf-8")).hexdigest() for sample in candidate.samples
    }
    output: list[TemplateSample] = []
    for snapshot in PatrolRepository().list_dataset_snapshots(mode="parser_validation"):
        if (
            (snapshot.vendor or "").lower() != candidate.vendor.lower()
            or (snapshot.model or "").lower() != candidate.model.lower()
            or snapshot.command_canonical != candidate.command
        ):
            continue
        digest = hashlib.sha256(snapshot.raw_text.encode("utf-8")).hexdigest()
        if digest in existing_hashes:
            continue
        if _is_non_template_sample(snapshot.raw_text):
            continue
        existing_hashes.add(digest)
        output.append(
            TemplateSample(
                file_path=f"regression:{snapshot.snapshot_id}",
                device_id=snapshot.device_id,
                command=snapshot.command_canonical,
                output=snapshot.raw_text,
            )
        )
    return tuple(output)


def _report_failure(
    candidate: Candidate,
    *,
    mode: str,
    reason: str,
    reports_dir: Path,
) -> GenerationReport:
    report = GenerationReport(
        vendor=candidate.vendor,
        model=candidate.model,
        family=candidate.family,
        command=candidate.command,
        category=candidate.category or "",
        template_generated=False,
        compile_success=False,
        record_count=0,
        field_coverage=0,
        validation_score=0,
        retry_count=0,
        skipped_reason=reason,
        mode=mode,
        sample_count=len(candidate.samples),
        confidence=candidate.confidence,
        evidence_command=candidate.evidence_command,
        evidence_text=candidate.evidence_text,
    )
    save_report(report, reports_dir)
    return report


def _process_candidate(
    candidate: Candidate,
    *,
    templates_dir: Path,
    reports_dir: Path,
    max_retries: int,
    dry_run: bool,
    publish: bool,
    force_overwrite: bool,
    minimum_pass_rate: float,
    max_samples_per_prompt: int,
    mode: str,
    source_job_id: str | None = None,
) -> GenerationReport:
    trigger_sample_count = len(candidate.samples)
    regression_samples = _regression_samples(candidate) if source_job_id else ()
    validation_candidate = Candidate(
        vendor=candidate.vendor,
        model=candidate.model,
        family=candidate.family,
        command=candidate.command,
        category=candidate.category,
        samples=tuple([*candidate.samples, *regression_samples]),
        confidence=candidate.confidence,
        evidence_command=candidate.evidence_command,
        evidence_text=candidate.evidence_text,
    )
    proposal = infer_contract(
        command=candidate.command,
        samples_text=_prompt_samples(candidate.samples, max_samples_per_prompt),
        llm_call=call_llm,
    )
    category = proposal.category
    core_spec = proposal.to_category_spec()
    spec = CategorySpec(
        name=core_spec.name,
        required_fields=list(core_spec.required_fields),
        optional_fields=list(core_spec.optional_fields),
        entity_type=core_spec.entity_type,
        primary_keys=list(core_spec.primary_keys),
        validators=core_spec.validators,
        allow_empty=core_spec.allow_empty,
        empty_patterns=list(core_spec.empty_patterns),
        ignore_fields=list(core_spec.ignore_fields),
    )

    target = resolve_template_path(templates_dir, candidate.model, candidate.command)
    template_text = ""
    retry_count = 0
    validations: list[ValidationResult] = []
    sample_results: list[dict[str, Any]] = []
    skipped_reason = None
    previous_errors: list[str] = []
    missing_fields: list[str] = []
    value_errors: list[str] = []
    synthesized = synthesize_template(
        command=candidate.command,
        samples=candidate.samples,
        spec=spec,
    )
    deterministic = synthesized.template_text if synthesized else None
    attempts = [deterministic] if deterministic else []
    attempts.extend([None] * (max_retries + 1))
    for attempt, preset_template in enumerate(attempts):
        retry_count = attempt
        if preset_template:
            template_text = preset_template
        else:
            raw = generate_template_text(
                vendor=candidate.vendor,
                model=candidate.model,
                command=candidate.command,
                cli_output=_prompt_samples(candidate.samples, max_samples_per_prompt),
                required_fields=spec.required_fields,
                optional_fields=spec.optional_fields,
                primary_keys=spec.primary_keys,
                previous_template=template_text or None,
                validation_errors=previous_errors,
                missing_fields=missing_fields,
                value_errors=value_errors,
            )
            template_text = strip_llm_template(raw)
        validations, sample_results = _validate_samples(
            template_text, validation_candidate.samples, spec
        )
        passed = sum(result.is_valid for result in validations)
        pass_rate = passed / len(validations) if validations else 0.0
        if pass_rate >= minimum_pass_rate:
            break
        failed = [result for result in validations if not result.is_valid]
        previous_errors = [error for result in failed for error in result.errors][:30]
        missing_fields = sorted({field for result in failed for field in result.missing_fields})
        value_errors = [error for result in failed for error in result.value_errors][:30]

    passed_samples = sum(result.is_valid for result in validations)
    pass_rate = passed_samples / len(validations) if validations else 0.0
    accepted = bool(validations) and pass_rate >= minimum_pass_rate
    generated = False
    if accepted and publish and not dry_run:
        version = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        target, digest = TemplateAssetStore(templates_dir).write_immutable(
            vendor=candidate.vendor,
            profile=candidate.model,
            command=candidate.command,
            version=version,
            content=template_text,
        )
        validation_payload = {
            "key_contract_version": 1,
            "sample_count": len(validation_candidate.samples),
            "trigger_sample_count": trigger_sample_count,
            "regression_sample_count": len(regression_samples),
            "passed_samples": passed_samples,
            "validation_pass_rate": pass_rate,
            "samples": sample_results,
            "entity_type": spec.entity_type,
            "primary_keys": spec.primary_keys,
            "required_fields": spec.required_fields,
            "optional_fields": spec.optional_fields,
            "validators": spec.validators,
            "allow_empty": spec.allow_empty,
            "empty_patterns": spec.empty_patterns or [],
            "ignore_fields": spec.ignore_fields or [],
            "contract_source": proposal.source,
            "contract_confidence": proposal.confidence,
            "template_synthesis_strategy": synthesized.strategy if synthesized else "llm",
            "contract_approval_status": "pending",
            "regression_validation": {
                "passed": pass_rate >= minimum_pass_rate,
                "sample_count": len(validation_candidate.samples),
                "passed_samples": passed_samples,
                "historical_sample_count": len(regression_samples),
                "minimum_pass_rate": minimum_pass_rate,
            },
            "identity_evidence": {
                "command": candidate.evidence_command,
                "text": candidate.evidence_text,
                "confidence": candidate.confidence,
            },
        }
        repository = ParserRepository()
        repository.register_template(
            vendor=candidate.vendor,
            model=candidate.model,
            family=candidate.family,
            profile=candidate.model,
            command=candidate.command,
            category=category,
            version=version,
            asset_path=str(target),
            sha256=digest,
            validation=validation_payload,
            source_job_id=source_job_id,
            status="pending_approval",
        )
        generated = True

    all_records = [record for result in validations if result.is_valid for record in result.records]
    errors = [error for result in validations if not result.is_valid for error in result.errors]
    report = GenerationReport(
        vendor=candidate.vendor,
        model=candidate.model,
        family=candidate.family,
        command=candidate.command,
        category=category,
        entity_type=spec.entity_type,
        primary_keys=spec.primary_keys,
        template_generated=generated,
        compile_success=all(result.compile_success for result in validations),
        record_count=sum(result.record_count for result in validations),
        field_coverage=min((result.field_coverage for result in validations), default=0),
        validation_score=min((result.validation_score for result in validations), default=0),
        retry_count=retry_count,
        template_path=str(target) if accepted else None,
        skipped_reason=(
            skipped_reason
            if skipped_reason
            else "dry_run"
            if accepted and (dry_run or not publish)
            else "sample_pass_rate_below_threshold"
            if not accepted
            else None
        ),
        errors=errors or None,
        parsed_records=all_records or None,
        mode=mode,
        sample_count=len(validation_candidate.samples),
        passed_samples=passed_samples,
        validation_pass_rate=pass_rate,
        sample_results=sample_results,
        confidence=candidate.confidence,
        evidence_command=candidate.evidence_command,
        evidence_text=candidate.evidence_text,
    )
    save_report(report, reports_dir)
    return report


def generate_templates(params: dict[str, Any]) -> dict[str, Any]:
    templates_dir = Path(params.get("templates_dir") or SHARED_TEXTFSM_ROOT)
    reports_dir = Path(params.get("reports_dir") or DEFAULT_REPORTS)
    mappings = load_command_mapping(Path(params.get("mapping_config") or DEFAULT_MAPPING))
    reports: list[GenerationReport] = []
    discovery_metadata: dict[str, Any] = {}

    job_candidate = _job_candidate(params)
    direct = _direct_candidate(params, mappings)
    if job_candidate:
        candidates = [job_candidate]
        mode = "job"
    elif params.get("source_path"):
        discovery = discover_directory_candidates(
            params["source_path"],
            mappings=mappings,
            recursive=bool(params.get("recursive", True)),
            vendor=params.get("vendor"),
            model=params.get("model"),
            command_filter=params.get("command"),
        )
        candidates = _directory_candidates(discovery.candidates)
        mode = "directory"
        discovery_metadata = {
            "files_scanned": discovery.files_scanned,
            "devices_detected": discovery.devices_detected,
            "device_profiles": list(discovery.device_profiles),
            "skipped_commands": list(discovery.skipped_commands),
            "unresolved_files": list(discovery.unresolved_files),
        }
    elif direct:
        candidates = [direct]
        mode = "direct"
    else:
        database_items = discover_missing_template_candidates(
            None,
            None,
            mappings,
            vendor_filter=params.get("vendor"),
            model_filter=params.get("model"),
            command_filter=params.get("command"),
        )
        candidates = _database_candidates(database_items)
        mode = "database"

    publish = bool(params.get("publish", True))
    if mode in {"direct", "directory"} and publish and candidates:
        raise ValueError(
            "direct and directory modes are discovery-only; publish from a "
            "parser-validation database job"
        )

    for candidate in candidates:
        reports.append(
            _process_candidate(
                candidate,
                templates_dir=templates_dir,
                reports_dir=reports_dir,
                max_retries=int(params.get("max_retries", 3)),
                dry_run=bool(params.get("dry_run", False)),
                publish=publish,
                force_overwrite=bool(params.get("force_generate", mode == "direct")),
                minimum_pass_rate=float(params.get("minimum_sample_pass_rate", 1.0)),
                max_samples_per_prompt=int(params.get("max_samples_per_prompt", 3)),
                mode=mode,
                source_job_id=str(params.get("job_id") or "").strip() or None,
            )
        )

    summary_path = save_summary(
        reports,
        reports_dir,
        metadata={"mode": mode, **discovery_metadata},
    )
    generated = [report for report in reports if report.template_generated]
    passed = sum(report.passed_samples for report in reports)
    samples = sum(report.sample_count for report in reports)
    return {
        "success": (
            not discovery_metadata.get("unresolved_files")
            and all(
                report.validation_pass_rate >= float(params.get("minimum_sample_pass_rate", 1.0))
                for report in reports
            )
        ),
        "message": (
            f"{mode} mode processed {len(reports)} candidate groups; "
            f"published {len(generated)} templates"
            + (f"; commands: {', '.join(report.command for report in reports)}" if reports else "")
        ),
        "mode": mode,
        "total_candidates": len(candidates),
        "candidate_groups": len(candidates),
        "success_count": len(generated),
        "generated_templates": [
            report.template_path for report in generated if report.template_path
        ],
        "validation_pass_rate": passed / samples if samples else 0.0,
        "parsed_records": [
            record for report in reports for record in (report.parsed_records or [])
        ],
        "reports": [report.to_dict() for report in reports],
        "summary_path": str(summary_path),
        "shared_templates_dir": str(templates_dir),
        **discovery_metadata,
    }
