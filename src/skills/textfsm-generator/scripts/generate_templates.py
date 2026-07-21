# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""TextFSM parser asset generation for direct, database, and directory inputs."""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, replace
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
from llm_generator import build_prompt_bundle, call_llm, generate_template_text
from report_generator import GenerationReport, save_report, save_summary
from template_providers import (
    ProviderContext,
    TemplateProviderRegistry,
    expected_entity_count_from_evidence,
    expected_primary_key_tuples_from_tables,
)
from template_validator import (
    ValidationResult,
    canonicalize_template_fields,
    parse_with_template,
    repair_record_boundary_candidate,
    strip_llm_template,
    uncovered_structured_lines,
    validate_template,
)

from src.core.parser.assets import (
    DEFAULT_ASSET_ROOT as SHARED_TEXTFSM_ROOT,
)
from src.core.parser.assets import (
    TemplateAssetStore,
    safe_slug,
)
from src.core.parser.contract_inference import (
    govern_contract_candidate,
    infer_contract,
    revise_contract,
)
from src.core.parser.contract_validation import (
    proposal_from_dict,
    validate_contract_candidate,
)
from src.core.parser.generation_agent import (
    AgentEvaluation,
    GeneratedTemplate,
    ParserAgentLimits,
    ParserTemplateAgentGraph,
)
from src.core.parser.generation_diagnostics import ParserDiagnostic
from src.core.parser.generation_samples import (
    partition_parser_samples,
    prompt_line_map_for_sample,
    render_sanitized_training_samples,
)
from src.core.parser.repository import (
    ParserJobRepository,
    ParserMetadataRepository,
    ParserTemplateRepository,
)
from src.core.parser.structure_profiler import profile_cli_structure, profile_sample_set
from src.core.parser.textfsm_dialect import (
    DEFAULT_TEXTFSM_DIALECT,
    dialect_hash,
    installed_textfsm_version,
)
from src.core.patrol.repository import PatrolRepository

SKILL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPORTS = SKILL_ROOT / "reports"
PARSER_VERSION = "1.0.0"
_CLI_SAMPLE_BLOCK_RE = re.compile(
    r'<CLI_SAMPLE\s+id="(?P<sample_id>[^"]+)"[^>]*>\s*\n?'
    r"(?P<body>[\s\S]*?)\n?\s*</CLI_SAMPLE>"
)


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
    job = ParserJobRepository().get_job(job_id, include_sanitized_payload=True)
    if not job:
        raise ValueError(f"parser template job does not exist: {job_id}")
    samples: list[TemplateSample] = []
    patrol_repository = PatrolRepository()
    for item in job.get("samples") or []:
        if item.get("snapshot_source") != "postgresql":
            raise ValueError("parser job contains a legacy SQLite sample; re-import the dataset")
        context = patrol_repository.get_snapshot_context(item["snapshot_id"])
        if not context:
            raise ValueError(
                f"parser job sample is unavailable in PostgreSQL: {item['snapshot_id']}"
            )
        sample_mode = str(context.get("mode") or "")
        if sample_mode not in {"parser_validation", "production"}:
            raise ValueError(f"unsupported parser job sample mode: {sample_mode or 'unknown'}")
        if item.get("sanitization_status") != "sanitized":
            raise ValueError("parser sample is not sanitized")
        if not str(item.get("sanitized_text") or ""):
            raise ValueError("parser sample has no sanitized payload")
        snapshot = context["snapshot"]
        if snapshot:
            samples.append(
                TemplateSample(
                    file_path=f"snapshot:{snapshot.snapshot_id}",
                    device_id=snapshot.device_id,
                    command=snapshot.command_canonical,
                    output=snapshot.raw_text,
                    prompt_output=str(item.get("sanitized_text") or ""),
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
    partition = partition_parser_samples(samples, max_train_samples=max(1, limit))
    prompt_refs = (*partition.train, *partition.negative[:1])
    return render_sanitized_training_samples(samples, prompt_refs)


def _deterministic_template(candidate: Candidate, spec: CategorySpec) -> str | None:
    profile = profile_sample_set(
        [(f"sample-{index}", sample.output) for index, sample in enumerate(candidate.samples)]
    )
    synthesized = TemplateProviderRegistry().propose(
        ProviderContext(
            command=candidate.command,
            samples=candidate.samples,
            spec=spec,
            structure_profile=profile,
            field_bindings=None,
        )
    )
    return synthesized.template_text if synthesized else None


def _validate_samples(
    template_text: str,
    samples: tuple[TemplateSample, ...],
    spec: CategorySpec,
    *,
    field_bindings: dict[str, Any] | None = None,
) -> tuple[list[ValidationResult], list[dict[str, Any]]]:
    validations: list[ValidationResult] = []
    details: list[dict[str, Any]] = []
    for sample in samples:
        sample_hash = hashlib.sha256(sample.output.encode("utf-8")).hexdigest()
        sample_id = f"sample-{sample_hash[:12]}"
        structure = profile_cli_structure(sample.output, sample_id=sample.device_id)
        expected_keys = expected_primary_key_tuples_from_tables(
            sample.output,
            spec,
            field_bindings=field_bindings,
        )
        evidenced_entity_count = expected_entity_count_from_evidence(
            sample.output,
            spec,
            field_bindings=field_bindings,
        )
        expected_entities = (
            len(expected_keys)
            if expected_keys
            else evidenced_entity_count
            if evidenced_entity_count is not None
            else structure.estimated_entities
            if structure.confidence >= 0.8
            and structure.layout_kind
            in {
                "table",
                "multi_table",
                "sectioned_table",
                "repeated_block",
                "key_value_singleton",
            }
            else None
        )
        result = validate_template(
            template_text,
            sample.output,
            spec.required_fields,
            spec.validators,
            allow_empty=spec.allow_empty,
            empty_patterns=spec.empty_patterns,
            primary_keys=spec.primary_keys,
            expected_min_records=expected_entities,
            expected_record_count=expected_entities,
            expected_primary_keys=expected_keys,
            field_bindings=field_bindings,
            sample_id=sample_id,
            evidence_line_map=prompt_line_map_for_sample(sample),
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
                "layout_kind": structure.layout_kind,
                "expected_entity_count": expected_entities,
                "errors": result.errors,
                "key_errors": result.key_errors,
                "completeness_errors": result.completeness_errors,
                "evidence_errors": result.evidence_errors,
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


def _regression_samples(
    candidate: Candidate,
    *,
    target_profile: str | None = None,
) -> tuple[TemplateSample, ...]:
    existing_hashes = {
        hashlib.sha256(sample.output.encode("utf-8")).hexdigest() for sample in candidate.samples
    }
    output: list[TemplateSample] = []
    allowed_models = {candidate.model.casefold()}
    if (
        target_profile
        and candidate.family
        and target_profile.casefold() == candidate.family.casefold()
    ):
        allowed_models.update(
            str(item.get("model") or "").casefold()
            for item in ParserMetadataRepository().list_device_profiles()
            if str(item.get("vendor") or "").casefold() == candidate.vendor.casefold()
            and str(item.get("family") or "").casefold() == candidate.family.casefold()
        )
    for snapshot in PatrolRepository().list_dataset_snapshots(mode="parser_validation"):
        if (
            (snapshot.vendor or "").lower() != candidate.vendor.lower()
            or (snapshot.model or "").casefold() not in allowed_models
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
        accepted=False,
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


def _category_spec_from_contract(payload: dict[str, Any]) -> CategorySpec:
    bindings = dict((payload.get("evidence") or {}).get("field_bindings") or {})
    derived_only = {
        str(field_name)
        for field_name, raw in bindings.items()
        if (raw if isinstance(raw, list) else [raw])
        and all(
            str(item.get("capture_kind") or "") == "derived"
            for item in (raw if isinstance(raw, list) else [raw])
            if isinstance(item, dict)
        )
    }
    required_fields = [
        str(field)
        for field in payload.get("required_fields") or []
        if str(field) not in derived_only
    ]
    optional_fields = [
        str(field)
        for field in payload.get("optional_fields") or []
        if str(field) not in derived_only
    ]
    parser_fields = {*required_fields, *optional_fields}
    return CategorySpec(
        name=str(payload.get("category") or "record"),
        required_fields=required_fields,
        optional_fields=optional_fields,
        entity_type=str(payload.get("entity_type") or payload.get("category") or "record"),
        primary_keys=list(payload.get("primary_keys") or []),
        validators={
            str(field): dict(rules or {})
            for field, rules in dict(payload.get("validators") or {}).items()
            if str(field) in parser_fields
        },
        allow_empty=bool(payload.get("allow_empty", False)),
        empty_patterns=list(payload.get("empty_patterns") or []),
        ignore_fields=list(payload.get("ignore_fields") or []),
    )


def _prompt_sample_bodies(samples_text: str) -> dict[str, str]:
    return {
        match.group("sample_id"): match.group("body")
        for match in _CLI_SAMPLE_BLOCK_RE.finditer(samples_text or "")
    }


def _evaluate_agent_candidate(
    *,
    template_text: str,
    contract: dict[str, Any],
    positive_samples: tuple[TemplateSample, ...],
    sample_roles: dict[str, str],
    negative_samples: tuple[TemplateSample, ...],
) -> AgentEvaluation:
    spec = _category_spec_from_contract(contract)
    validations, details = _validate_samples(
        template_text,
        positive_samples,
        spec,
        field_bindings=dict((contract.get("evidence") or {}).get("field_bindings") or {}),
    )
    diagnostics: list[ParserDiagnostic] = []
    for sample, result, detail in zip(positive_samples, validations, details, strict=True):
        sample_hash = hashlib.sha256(sample.output.encode("utf-8")).hexdigest()
        role = sample_roles.get(sample_hash, "regression")
        detail["role"] = role
        detail["sample_id"] = f"sample-{sample_hash[:12]}"
        for item in result.diagnostics:
            diagnostics.append(replace(item, sample_id=detail["sample_id"]))

    negative_results: list[dict[str, Any]] = []
    for sample in negative_samples:
        records, errors = parse_with_template(template_text, sample.output)
        passed = not errors and not records
        sample_hash = hashlib.sha256(sample.output.encode("utf-8")).hexdigest()
        negative_results.append(
            {
                "sample_id": f"sample-{sample_hash[:12]}",
                "role": "negative",
                "passed": passed,
                "record_count": len(records),
                "errors": errors,
            }
        )
        if not passed:
            diagnostics.append(
                ParserDiagnostic(
                    code="extra_entities",
                    message="negative sample produced parser records",
                    sample_id=f"sample-{sample_hash[:12]}",
                    expected=0,
                    actual=len(records),
                )
            )

    role_results: dict[str, list[ValidationResult]] = {
        "train": [],
        "holdout": [],
        "regression": [],
    }
    for sample, result in zip(positive_samples, validations, strict=True):
        digest = hashlib.sha256(sample.output.encode("utf-8")).hexdigest()
        role_results.setdefault(sample_roles.get(digest, "regression"), []).append(result)

    def all_or_empty(rows: list[ValidationResult], predicate) -> bool:
        return not rows or all(predicate(item) for item in rows)

    compile_ok = bool(validations) and all(item.compile_success for item in validations)
    executed = compile_ok and all(item.parse_success for item in validations)
    contract_ok = executed and all(
        not item.missing_fields and not item.value_errors for item in validations
    )
    keys_ok = contract_ok and all(not item.key_errors for item in validations)
    entities_ok = keys_ok and all(not item.completeness_errors for item in validations)
    evidence_alignment_ok = entities_ok and all(not item.evidence_errors for item in validations)
    empty_ok = evidence_alignment_ok and all(
        item.record_count > 0 or item.empty_accepted for item in validations
    )
    holdout_ok = empty_ok and all_or_empty(
        role_results.get("holdout", []), lambda item: item.is_valid
    )
    regression_ok = holdout_ok and all_or_empty(
        role_results.get("regression", []), lambda item: item.is_valid
    )
    negative_ok = all(item["passed"] for item in negative_results)
    declared_optional = set(spec.optional_fields)
    bound_optional = declared_optional.intersection(
        dict((contract.get("evidence") or {}).get("field_bindings") or {})
    )
    parsed_records = [record for result in validations for record in result.records]
    uncovered_variants: list[dict[str, Any]] = []
    capture_fields = set(spec.fields) - set(spec.primary_keys)
    for sample, result, detail in zip(positive_samples, validations, details, strict=True):
        if detail.get("layout_kind") != "repeated_block" or not result.records:
            continue
        gaps = uncovered_structured_lines(
            template_text,
            sample.output,
            primary_keys=spec.primary_keys,
            records=result.records,
            captured_fields=capture_fields,
        )
        if not gaps:
            continue
        sample_hash = hashlib.sha256(sample.output.encode("utf-8")).hexdigest()
        sample_id = f"sample-{sample_hash[:12]}"
        uncovered_variants.extend({"sample_id": sample_id, **gap} for gap in gaps)
        detail["uncovered_structured_lines"] = gaps[:20]
        diagnostics.append(
            ParserDiagnostic(
                code="uncovered_structured_line",
                message=(
                    f"{len(gaps)} structured entity lines are not covered by any "
                    "field-capturing rule"
                ),
                sample_id=sample_id,
                expected="every structured entity variant has a capture rule",
                actual=len(gaps),
                evidence={"uncovered": gaps[:10]},
            )
        )
    variant_coverage_ok = not uncovered_variants
    missing_evidenced_fields = sorted(
        field_name
        for field_name in bound_optional
        if not any(str(record.get(field_name, "")).strip() for record in parsed_records)
    )
    evidence_ok = not missing_evidenced_fields
    if missing_evidenced_fields:
        record_transition_fields: set[str] = set()
        for line in template_text.splitlines():
            if not re.search(r"->\s*(?:(?:Continue|Next)\.)?Record\b", line):
                continue
            record_transition_fields.update(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", line))
        primary_keys_populated = bool(parsed_records) and all(
            all(str(record.get(key, "")).strip() for key in spec.primary_keys)
            for record in parsed_records
        )
        likely_early_record = (
            primary_keys_populated
            and bool(set(spec.primary_keys).intersection(record_transition_fields))
            and not set(missing_evidenced_fields).intersection(record_transition_fields)
        )
        if likely_early_record:
            diagnostics.append(
                ParserDiagnostic(
                    code="record_boundary_error",
                    message=(
                        "records are emitted when the entity key is seen, before "
                        "evidenced fields are populated"
                    ),
                    expected={"captured_before_record": missing_evidenced_fields},
                    actual={
                        "record_transition_fields": sorted(record_transition_fields),
                    },
                    evidence={
                        "parsed_records_preview": parsed_records[:3],
                    },
                )
            )
        diagnostics.append(
            ParserDiagnostic(
                code="missing_evidenced_fields",
                message=(
                    "candidate did not capture evidenced fields: "
                    + ", ".join(missing_evidenced_fields)
                ),
                expected=sorted(bound_optional),
                actual=sorted(bound_optional - set(missing_evidenced_fields)),
            )
        )
    gates = {
        "dialect_compatible": compile_ok,
        "compiled": compile_ok,
        "executed": executed,
        "contract_valid": contract_ok,
        "key_valid": keys_ok,
        "entity_complete": entities_ok,
        "record_evidence_aligned": evidence_alignment_ok,
        "variant_coverage": variant_coverage_ok,
        "empty_classified": empty_ok,
        "holdout_passed": holdout_ok,
        "regression_passed": regression_ok,
        "negative_passed": negative_ok,
        "evidence_coverage": evidence_ok,
    }
    ordered = list(gates.values())
    level = 0
    for passed in ordered:
        if not passed:
            break
        level += 1
    rank = float(level * 1000 + min((item.validation_score for item in validations), default=0))
    success = (
        bool(validations)
        and all(item.is_valid for item in validations)
        and negative_ok
        and evidence_ok
        and variant_coverage_ok
    )
    if not success and role_results.get("holdout") and not holdout_ok:
        diagnostics.append(
            ParserDiagnostic(
                code="cross_sample_regression",
                message="candidate failed one or more hidden holdout samples",
                evidence={
                    "failed_count": sum(not item.is_valid for item in role_results["holdout"])
                },
            )
        )
    return AgentEvaluation(
        success=success,
        rank=rank,
        report={
            "success": success,
            "validation_level": level,
            "hard_gates": gates,
            "sample_results": details,
            "negative_results": negative_results,
            "missing_evidenced_fields": missing_evidenced_fields,
            "uncovered_structured_lines": uncovered_variants[:50],
            "passed_samples": sum(item.is_valid for item in validations),
            "sample_count": len(validations),
        },
        diagnostics=tuple(diagnostics),
    )


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
    agent_token_budget: int,
    agent_time_budget_seconds: int,
    mode: str,
    source_job_id: str | None = None,
) -> GenerationReport:
    target_profile = candidate.model
    if source_job_id:
        source_job = ParserJobRepository().get_job(source_job_id)
        if source_job:
            target_profile = str(source_job.get("profile") or target_profile)
    trigger_sample_count = len(candidate.samples)
    regression_samples = (
        _regression_samples(candidate, target_profile=target_profile) if source_job_id else ()
    )
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
    sample_partition = partition_parser_samples(
        candidate.samples,
        max_train_samples=max_samples_per_prompt,
    )
    prompt_refs = (*sample_partition.train, *sample_partition.negative[:1])
    sanitized_prompt_samples = render_sanitized_training_samples(
        candidate.samples,
        prompt_refs,
    )
    parse_plan = profile_sample_set(
        [(ref.sample_id, candidate.samples[ref.index].output) for ref in sample_partition.train]
    )
    target = resolve_template_path(templates_dir, candidate.model, candidate.command)
    skipped_reason = None
    positive_refs = (*sample_partition.train, *sample_partition.holdout)
    positive_trigger_samples = tuple(candidate.samples[ref.index] for ref in positive_refs)
    negative_samples = tuple(candidate.samples[ref.index] for ref in sample_partition.negative)
    positive_samples = tuple([*positive_trigger_samples, *regression_samples])
    negative_hashes = {ref.content_sha256 for ref in sample_partition.negative}
    replay_positive_samples = tuple(
        [
            *(
                sample
                for sample in candidate.samples
                if hashlib.sha256(sample.output.encode("utf-8")).hexdigest() not in negative_hashes
            ),
            *regression_samples,
        ]
    )
    sample_roles = {
        **{ref.content_sha256: "train" for ref in sample_partition.train},
        **{ref.content_sha256: "holdout" for ref in sample_partition.holdout},
        **{
            hashlib.sha256(sample.output.encode("utf-8")).hexdigest(): "regression"
            for sample in regression_samples
        },
    }

    synthesis_strategy: dict[str, str] = {"value": "llm"}

    def contract_builder(state):
        if int(state.get("contract_revision") or 0) == 0:
            proposal = infer_contract(
                command=candidate.command,
                samples_text=sanitized_prompt_samples,
                structure_profile=parse_plan,
                llm_call=call_llm,
            )
        else:
            proposal = revise_contract(
                previous=proposal_from_dict(dict(state.get("contract") or {})),
                command=candidate.command,
                samples_text=sanitized_prompt_samples,
                structure_profile=parse_plan,
                diagnostic=dict(state.get("primary_diagnostic") or {}),
                llm_call=call_llm,
            )
        valid, diagnostics = validate_contract_candidate(
            proposal,
            structure_profile=parse_plan,
            samples_text=sanitized_prompt_samples,
        )
        return proposal.to_dict(), valid, diagnostics

    def candidate_builder(state) -> list[dict[str, Any]]:
        spec = _category_spec_from_contract(dict(state["contract"]))
        candidates = TemplateProviderRegistry().propose_all(
            ProviderContext(
                command=candidate.command,
                samples=candidate.samples,
                spec=spec,
                structure_profile=parse_plan,
                field_bindings=dict(
                    ((state.get("contract") or {}).get("evidence") or {}).get("field_bindings")
                    or {}
                ),
                evidence_samples=_prompt_sample_bodies(sanitized_prompt_samples),
                entity_scope=str(
                    (
                        ((state.get("contract") or {}).get("evidence") or {}).get(
                            "entity_scope_governance"
                        )
                        or {}
                    ).get("scope")
                    or ""
                ),
            ),
            limit=2,
        )
        return [
            {
                "template_text": item.template_text,
                "provider": f"{item.provider}:{item.strategy}",
            }
            for item in candidates
        ]

    def template_builder(state) -> GeneratedTemplate:
        spec = _category_spec_from_contract(dict(state["contract"]))
        regenerate = state.get("template_mode") == "regenerate"
        previous = (
            ""
            if regenerate
            else str(state.get("best_template") or state.get("current_template") or "")
        )
        diagnostic = dict(state.get("primary_diagnostic") or {})
        repaired = (
            repair_record_boundary_candidate(
                previous,
                diagnostic,
                primary_keys=spec.primary_keys,
            )
            if previous
            else None
        )
        if repaired:
            synthesis_strategy["value"] = "deterministic_record_boundary_repair"
            return GeneratedTemplate(
                template_text=repaired,
                provider="platform-repair",
            )
        if regenerate:
            diagnostic = {
                **diagnostic,
                "allowed_action": "regenerate_template",
                "strategy": "discard_previous_candidate_and_rebuild",
            }
        bundle = build_prompt_bundle(
            vendor=candidate.vendor,
            model=candidate.model,
            command=candidate.command,
            cli_output=sanitized_prompt_samples,
            required_fields=spec.required_fields,
            optional_fields=spec.optional_fields,
            primary_keys=spec.primary_keys,
            validators=spec.validators,
            parse_plan=parse_plan,
            field_bindings=dict(
                ((state.get("contract") or {}).get("evidence") or {}).get("field_bindings") or {}
            ),
            allow_empty=spec.allow_empty,
            empty_patterns=spec.empty_patterns,
            repair_diagnostic=diagnostic or None,
            current_template=previous or None,
        )
        started = time.perf_counter()
        raw = generate_template_text(
            vendor=candidate.vendor,
            model=candidate.model,
            command=candidate.command,
            cli_output=sanitized_prompt_samples,
            required_fields=spec.required_fields,
            optional_fields=spec.optional_fields,
            primary_keys=spec.primary_keys,
            validators=spec.validators,
            parse_plan=parse_plan,
            field_bindings=dict(
                ((state.get("contract") or {}).get("evidence") or {}).get("field_bindings") or {}
            ),
            allow_empty=spec.allow_empty,
            empty_patterns=spec.empty_patterns,
            previous_template=previous or None,
            repair_diagnostic=diagnostic or None,
        )
        synthesis_strategy["value"] = "llm"
        return GeneratedTemplate(
            template_text=canonicalize_template_fields(
                strip_llm_template(raw),
                [*spec.required_fields, *spec.optional_fields],
            ),
            provider="llm",
            model_name=getattr(raw, "model_name", None),
            prompt_version=bundle.prompt_version,
            prompt_hash=hashlib.sha256(bundle.render().encode("utf-8")).hexdigest(),
            token_usage=dict(getattr(raw, "token_usage", None) or {}),
            latency_ms=round((time.perf_counter() - started) * 1000),
        )

    def validator(template_text: str, contract: dict[str, Any]) -> AgentEvaluation:
        return _evaluate_agent_candidate(
            template_text=template_text,
            contract=contract,
            positive_samples=positive_samples,
            sample_roles=sample_roles,
            negative_samples=negative_samples,
        )

    agent = ParserTemplateAgentGraph(
        contract_builder=contract_builder,
        candidate_builder=candidate_builder,
        template_builder=template_builder,
        validator=validator,
        repository=ParserJobRepository() if source_job_id else None,
        limits=ParserAgentLimits(
            max_template_attempts=min(8, max(1, int(max_retries) + 1)),
            max_contract_revisions=3,
            max_stagnant_rounds=8,
            max_repeated_failure=3,
            token_budget=max(1, int(agent_token_budget)),
            time_budget_seconds=max(1, int(agent_time_budget_seconds)),
        ),
        train_sample_set_hash=sample_partition.train_set_hash,
        holdout_sample_set_hash=sample_partition.holdout_set_hash,
    )
    initial_agent_state: dict[str, Any] = {
        "job_id": source_job_id or "",
        "initial_candidates": [],
        "train_sample_set_hash": sample_partition.train_set_hash,
        "holdout_sample_set_hash": sample_partition.holdout_set_hash,
    }
    if source_job_id:
        job_repository = ParserJobRepository()
        resume_state = job_repository.get_generation_resume_state(source_job_id)
        same_sample_sets = (
            resume_state.get("train_sample_set_hash") == sample_partition.train_set_hash
            and resume_state.get("holdout_sample_set_hash") == sample_partition.holdout_set_hash
        )
        if same_sample_sets:
            initial_agent_state.update(resume_state)
            initial_agent_state["job_id"] = source_job_id
            if resume_state.get("contract"):
                governed = govern_contract_candidate(
                    proposal_from_dict(dict(resume_state["contract"]))
                )
                contract_valid, _contract_diagnostics = validate_contract_candidate(
                    governed,
                    structure_profile=parse_plan,
                    samples_text=sanitized_prompt_samples,
                )
                if contract_valid:
                    initial_agent_state["contract"] = governed.to_dict()
                    initial_agent_state["contract_valid"] = True
        elif not resume_state:
            reusable = job_repository.get_latest_valid_contract_attempt(
                source_job_id,
                train_sample_set_hash=sample_partition.train_set_hash,
                holdout_sample_set_hash=sample_partition.holdout_set_hash,
            )
            if reusable:
                governed = govern_contract_candidate(proposal_from_dict(dict(reusable["contract"])))
                contract_valid, _contract_diagnostics = validate_contract_candidate(
                    governed,
                    structure_profile=parse_plan,
                    samples_text=sanitized_prompt_samples,
                )
                if contract_valid:
                    initial_agent_state.update(
                        {
                            "contract": governed.to_dict(),
                            "contract_valid": True,
                            "contract_revision": 0,
                            "contract_reuse": {
                                "attempt_id": reusable["attempt_id"],
                                "attempt_number": reusable["attempt_number"],
                                "train_sample_set_hash": sample_partition.train_set_hash,
                                "holdout_sample_set_hash": sample_partition.holdout_set_hash,
                                "governance_reapplied": True,
                            },
                            "parent_attempt_id": reusable["attempt_id"],
                            "status": "contract_reused",
                        }
                    )
    agent_result = agent.invoke(initial_agent_state)
    synthesis_strategy["value"] = str(
        (agent_result.get("best_generated_meta") or agent_result.get("generated_meta") or {}).get(
            "provider"
        )
        or "llm"
    )
    proposal = proposal_from_dict(dict(agent_result.get("contract") or {}))
    category = proposal.category
    spec = _category_spec_from_contract(proposal.to_dict())
    template_text = str(
        agent_result.get("best_template") or agent_result.get("current_template") or ""
    )
    retry_count = max(0, int(agent_result.get("template_attempt") or 0) - 1)
    validations, sample_results = (
        _validate_samples(
            template_text,
            replay_positive_samples,
            spec,
            field_bindings=dict((proposal.evidence or {}).get("field_bindings") or {}),
        )
        if template_text
        else ([], [])
    )
    positive_passed = sum(result.is_valid for result in validations)
    negative_results = list(
        (agent_result.get("validation_report") or {}).get("negative_results") or []
    )
    negative_passed = sum(bool(item.get("passed")) for item in negative_results)
    passed_samples = positive_passed + negative_passed
    total_validation_samples = len(validations) + len(negative_results)
    pass_rate = passed_samples / total_validation_samples if total_validation_samples else 0.0
    accepted = agent_result.get("status") == "passed"
    generated = False
    if accepted and publish and not dry_run:
        best_attempt_id = str(agent_result.get("best_attempt_id") or "").strip()
        if source_job_id and not best_attempt_id:
            raise RuntimeError(
                "Parser Agent passed without a durable best attempt; refusing to publish"
            )
        version = (
            f"agent-{best_attempt_id}"
            if best_attempt_id
            else datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        )
        target, digest = TemplateAssetStore(templates_dir).write_immutable(
            vendor=candidate.vendor,
            profile=target_profile,
            command=candidate.command,
            version=version,
            content=template_text,
        )
        validation_payload = {
            "key_contract_version": 1,
            # Activation is gated by the corpus actually validated. Raw snapshot
            # counts remain available for evidence-volume auditing below.
            "sample_count": total_validation_samples,
            "source_sample_count": len(validation_candidate.samples),
            "unique_sample_count": len(positive_samples) + len(negative_results),
            "sample_partition": sample_partition.to_dict(),
            "compatibility": {
                "manifest_version": 1,
                "vendor": candidate.vendor,
                "profile": target_profile,
                "scope": (
                    "family"
                    if candidate.family and target_profile.casefold() == candidate.family.casefold()
                    else "model"
                ),
                "model": candidate.model,
                "family": candidate.family,
                "canonical_command": candidate.command,
                "structure_variant_count": len(
                    {
                        ref.layout_signature
                        for ref in (*sample_partition.train, *sample_partition.holdout)
                    }
                ),
                "structure_signatures": sorted(
                    {
                        ref.layout_signature
                        for ref in (*sample_partition.train, *sample_partition.holdout)
                    }
                ),
                "all_variants_required": True,
            },
            "structure_profile": parse_plan,
            "trigger_sample_count": trigger_sample_count,
            "unique_trigger_sample_count": sample_partition.unique_sample_count,
            "duplicate_trigger_sample_count": sample_partition.duplicate_sample_count,
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
            "state_semantics": proposal.to_dict()["state_semantics"],
            "contract_source": proposal.source,
            "contract_confidence": proposal.confidence,
            "template_synthesis_strategy": synthesis_strategy["value"],
            "textfsm_dialect": {
                "runtime_version": installed_textfsm_version(),
                "dialect_version": DEFAULT_TEXTFSM_DIALECT.dialect_version,
                "dialect_hash": dialect_hash(DEFAULT_TEXTFSM_DIALECT),
                "prompt_version": DEFAULT_TEXTFSM_DIALECT.prompt_version,
            },
            "contract_approval_status": "pending",
            "parser_agent": {
                "thread_id": agent_result.get("thread_id"),
                "status": agent_result.get("status"),
                "stop_reason": agent_result.get("stop_reason"),
                "contract_revisions": agent_result.get("contract_revision"),
                "template_attempts": agent_result.get("template_attempt"),
                "best_attempt_id": agent_result.get("best_attempt_id"),
                "contract_reuse": agent_result.get("contract_reuse"),
                "hard_gates": (agent_result.get("validation_report") or {}).get("hard_gates"),
            },
            "regression_validation": {
                "passed": accepted,
                "sample_count": total_validation_samples,
                "passed_samples": passed_samples,
                "source_sample_count": len(validation_candidate.samples),
                "unique_sample_count": total_validation_samples,
                "historical_sample_count": len(regression_samples),
                "minimum_pass_rate": 1.0,
                "requested_minimum_pass_rate": minimum_pass_rate,
            },
            "identity_evidence": {
                "command": candidate.evidence_command,
                "text": candidate.evidence_text,
                "confidence": candidate.confidence,
            },
        }
        repository = ParserTemplateRepository()
        repository.register_template(
            vendor=candidate.vendor,
            model=candidate.model,
            family=candidate.family,
            profile=target_profile,
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
    agent_diagnostics = [
        str(item.get("message") or item.get("code") or "generation diagnostic")
        for item in agent_result.get("diagnostics") or []
    ]
    report = GenerationReport(
        vendor=candidate.vendor,
        model=candidate.model,
        family=candidate.family,
        command=candidate.command,
        category=category,
        entity_type=spec.entity_type,
        primary_keys=spec.primary_keys,
        template_generated=generated,
        accepted=accepted,
        compile_success=bool(validations) and all(result.compile_success for result in validations),
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
            else str(agent_result.get("stop_reason") or "parser_agent_blocked")
            if not accepted
            else None
        ),
        errors=(errors or agent_diagnostics or [str(agent_result.get("stop_reason"))])
        if not accepted
        else None,
        parsed_records=all_records or None,
        mode=mode,
        sample_count=total_validation_samples,
        source_sample_count=len(validation_candidate.samples),
        unique_sample_count=len(positive_samples) + len(negative_results),
        duplicate_sample_count=sample_partition.duplicate_sample_count,
        passed_samples=passed_samples,
        validation_pass_rate=pass_rate,
        sample_results=sample_results,
        confidence=candidate.confidence,
        evidence_command=candidate.evidence_command,
        evidence_text=candidate.evidence_text,
        agent_loop={
            "status": agent_result.get("status"),
            "stop_reason": agent_result.get("stop_reason"),
            "contract_revisions": agent_result.get("contract_revision"),
            "template_attempts": agent_result.get("template_attempt"),
            "template_mode": agent_result.get("template_mode"),
            "diagnostics": agent_result.get("diagnostics") or [],
            "validation_report": agent_result.get("validation_report") or {},
            "contract": agent_result.get("contract") or {},
            "best_candidate_template": str(
                agent_result.get("best_template") or agent_result.get("current_template") or ""
            ),
            "best_candidate_provider": synthesis_strategy["value"],
            "best_attempt_id": agent_result.get("best_attempt_id"),
            "contract_reuse": agent_result.get("contract_reuse"),
        },
    )
    save_report(report, reports_dir)
    return report


def _generation_succeeded(
    reports: list[GenerationReport],
    *,
    unresolved_files: list[str],
    minimum_pass_rate: float,
) -> bool:
    return (
        bool(reports)
        and not unresolved_files
        and all(
            report.accepted and report.validation_pass_rate >= minimum_pass_rate
            for report in reports
        )
    )


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
                max_retries=int(params.get("max_retries", 7)),
                dry_run=bool(params.get("dry_run", False)),
                publish=publish,
                force_overwrite=bool(params.get("force_generate", mode == "direct")),
                minimum_pass_rate=float(params.get("minimum_sample_pass_rate", 1.0)),
                max_samples_per_prompt=int(params.get("max_samples_per_prompt", 3)),
                agent_token_budget=int(params.get("agent_token_budget", 96000)),
                agent_time_budget_seconds=int(params.get("agent_time_budget_seconds", 600)),
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
    accepted_reports = [report for report in reports if report.accepted]
    passed = sum(report.passed_samples for report in reports)
    samples = sum(report.sample_count for report in reports)
    return {
        "success": _generation_succeeded(
            reports,
            unresolved_files=discovery_metadata.get("unresolved_files") or [],
            minimum_pass_rate=float(params.get("minimum_sample_pass_rate", 1.0)),
        ),
        "message": (
            f"{mode} mode processed {len(reports)} candidate groups; "
            f"published {len(generated)} templates"
            + (f"; commands: {', '.join(report.command for report in reports)}" if reports else "")
        ),
        "mode": mode,
        "total_candidates": len(candidates),
        "candidate_groups": len(candidates),
        "success_count": len(accepted_reports),
        "published_count": len(generated),
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
