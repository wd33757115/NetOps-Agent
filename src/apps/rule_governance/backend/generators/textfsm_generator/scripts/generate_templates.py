# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Simple v2 TextFSM generation for one durable Parser control-plane job."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.apps.rule_governance.backend.generators.textfsm_generator.scripts.parser_model import TextFSMGenerationAgent
from src.apps.rule_governance.backend.generators.textfsm_generator.scripts.report_generator import GenerationReport, save_report, save_summary
from src.apps.rule_governance.backend.generators.textfsm_generator.scripts.result_reviewer import IndependentParserResultReviewer

from src.common.config import get_settings
from src.apps.rule_governance.backend.parser.repository import ParserJobRepository, ParserTemplateRepository
from src.apps.rule_governance.backend.parser_generation import (
    MAX_PARSER_MODEL_CALLS,
    KeyValueCompiler,
    ParserSample,
    ParserSpec,
    ParserSpecDocument,
    RepeatedBlockCompiler,
    RepositoryGenerationObserver,
    RepositoryModelCallController,
    SampleExpectation,
    SampleRole,
    SampleSet,
    SimpleParserGenerationService,
    TableCompiler,
    TemplateCandidate,
    TextFSMValidationBackend,
    latest_reusable_candidate,
    outcome_records,
    persist_generation,
    validate_template_runtime_compatibility,
)
from src.core.patrol.repository import PatrolRepository
from src.observability.deprecations import observe_legacy_path

from src.apps.rule_governance.paths import ROOT

SKILL_ROOT = ROOT / "src" / "skills" / "textfsm-generator"
DEFAULT_REPORTS = SKILL_ROOT / "reports"


def _load_sample_set(job: dict[str, Any]) -> SampleSet:
    patrol = PatrolRepository()
    samples: list[ParserSample] = []
    for item in job.get("samples") or []:
        snapshot_id = str(item.get("snapshot_id") or "")
        context = patrol.get_snapshot_context(snapshot_id)
        if context is None or context.get("snapshot") is None:
            raise ValueError(f"Parser sample is unavailable: {snapshot_id}")
        if item.get("sanitization_status") != "sanitized" or not str(
            item.get("sanitized_text") or ""
        ):
            raise ValueError(f"Parser sample is not sanitized: {item.get('id')}")
        try:
            role = SampleRole(str(item.get("sample_role") or "unassigned"))
        except ValueError as exc:
            raise ValueError(f"invalid Parser sample role: {item.get('sample_role')}") from exc
        expectation_payload = item.get("expectation_json")
        expectation = None
        if expectation_payload is not None:
            expectation = SampleExpectation(
                record_count=expectation_payload.get("record_count"),
                primary_keys=frozenset(
                    tuple(str(value) for value in key)
                    for key in expectation_payload.get("primary_keys") or []
                ),
            )
        snapshot = context["snapshot"]
        samples.append(
            ParserSample(
                sample_id=str(item["id"]),
                raw_text=str(snapshot.raw_text or ""),
                role=role,
                # Every role is validated against the same frozen sanitized evidence
                # used by ParserSpec. SampleRole still controls what the model may see.
                prompt_text=(
                    str(item.get("sanitized_text") or "")
                    if role is SampleRole.GENERATION
                    else None
                ),
                expectation=expectation,
                validation_text_override=str(item.get("sanitized_text") or ""),
            )
        )
    return SampleSet(tuple(samples))


def _process_simple_job(
    job: dict[str, Any],
    *,
    reports_dir: Path,
    publish: bool,
    dry_run: bool,
) -> GenerationReport:
    if job.get("pipeline_version") != "simple_v2":
        raise ValueError(
            "legacy Parser jobs are read-only; confirm ParserSpec and sample roles first"
        )
    readiness = dict(job.get("generation_readiness") or {})
    if not readiness.get("ready"):
        blockers = ", ".join(str(item) for item in readiness.get("blockers") or [])
        raise ValueError(f"simple Parser job is not ready: {blockers or 'unknown'}")

    spec_document = ParserSpecDocument.from_dict(
        dict(job.get("parser_spec_json") or {})
    )
    spec: ParserSpec | ParserSpecDocument = (
        spec_document.single_record_set
        if len(spec_document.record_sets) == 1
        else spec_document
    )
    sample_set = _load_sample_set(job)
    job_repository = ParserJobRepository()
    compilers = (
        (TableCompiler(), KeyValueCompiler(), RepeatedBlockCompiler())
        if job.get("generation_strategy") == "deterministic_fallback"
        else ()
    )
    seed_candidates: list[TemplateCandidate] = []
    seed_candidate = latest_reusable_candidate(
        job=job,
        sample_set=sample_set,
        repository=job_repository,
    )
    if seed_candidate is not None and all(
        candidate.sha256 != seed_candidate.sha256 for candidate in seed_candidates
    ):
        seed_candidates.append(seed_candidate)
    settings = get_settings()
    generation_observer = RepositoryGenerationObserver(
        job=job,
        spec=spec,
        sample_set=sample_set,
        repository=job_repository,
    )
    outcome = SimpleParserGenerationService(
        # Production generation is LLM-led. Deterministic compilers remain available
        # as explicit fallback/test providers, but cannot define the acceptance truth.
        compilers=compilers,
        model=TextFSMGenerationAgent(
            vendor=str(job["vendor"]),
            model=str(job.get("model") or job["profile"]),
            command=str(job["canonical_command"]),
            validation_samples=sample_set.samples,
        ),
        backend=TextFSMValidationBackend(),
        reviewer=IndependentParserResultReviewer(),
        model_call_controller=RepositoryModelCallController(
            job_id=str(job["id"]),
            repository=job_repository,
            used=int(job.get("model_calls_used") or 0),
            heartbeat_seconds=settings.PARSER_MODEL_CALL_HEARTBEAT_SECONDS,
            request_timeout_seconds=settings.PARSER_MODEL_REQUEST_HARD_TIMEOUT_SECONDS,
        ),
        observer=generation_observer,
        seed_candidates=tuple(seed_candidates),
        total_timeout_seconds=settings.PARSER_GENERATION_TOTAL_TIMEOUT_SECONDS,
        # Enforce the same executable contract as production (including unique keys).
        # Advisory counts are ignored; independent review still owns semantics.
        validator=validate_template_runtime_compatibility,
    ).generate(job_id=str(job["id"]), spec=spec, sample_set=sample_set)

    persisted = persist_generation(
        job=job,
        spec=spec,
        sample_set=sample_set,
        outcome=outcome,
        publish=publish,
        dry_run=dry_run,
        job_repository=job_repository,
        template_repository=ParserTemplateRepository(),
    )
    records, sample_results = outcome_records(outcome)
    final_attempt = outcome.attempts[-1] if outcome.attempts else None
    final_validation = (
        final_attempt.generation_validation if final_attempt is not None else None
    )
    final_review = final_attempt.review_report if final_attempt is not None else None
    review_errors = (
        [
            str(item.get("message") or item.get("code") or "semantic review failed")
            for item in final_review.diagnostics
        ]
        if final_review is not None and not final_review.passed
        else []
    )
    if (
        final_review is not None
        and not final_review.passed
        and not review_errors
        and final_review.summary
    ):
        review_errors = [final_review.summary]
    if final_validation is not None and not sample_results:
        sample_results = [
            {
                "sample_id": sample.sample_id,
                "role": sample.role.value,
                "passed": sample.passed,
                "record_count": sample.record_count,
                "errors": list(sample.errors),
            }
            for sample in final_validation.samples
        ]
    passed_samples = sum(bool(item.get("passed")) for item in sample_results)
    validation_sample_count = len(sample_results)
    report = GenerationReport(
        vendor=str(job["vendor"]),
        model=str(job.get("model") or job["profile"]),
        family=job.get("family"),
        command=str(job["canonical_command"]),
        category=(spec.entity if isinstance(spec, ParserSpec) else "multi_record"),
        entity_type=(spec.entity if isinstance(spec, ParserSpec) else "multi_record"),
        primary_keys=(
            list(spec.primary_keys)
            if isinstance(spec, ParserSpec)
            else []
        ),
        template_generated=persisted.template_generated,
        accepted=outcome.passed,
        compile_success=bool(final_validation and final_validation.hard_gates.get("compiled")),
        record_count=len(records),
        field_coverage=100 if outcome.passed else 0,
        validation_score=100 if outcome.passed else 0,
        retry_count=outcome.model_calls,
        template_path=persisted.template_path,
        skipped_reason=(
            None
            if persisted.template_generated
            else "dry_run"
            if outcome.passed and (dry_run or not publish)
            else outcome.stop_reason
        ),
        errors=(
            None
            if outcome.passed
            else review_errors
            if review_errors
            else list(final_validation.diagnostics)
            if final_validation and final_validation.diagnostics
            else [outcome.stop_reason]
        ),
        parsed_records=records or None,
        mode="job",
        sample_count=len(sample_set.samples),
        source_sample_count=len(sample_set.samples),
        unique_sample_count=len(sample_set.samples),
        duplicate_sample_count=0,
        passed_samples=passed_samples,
        validation_pass_rate=(
            passed_samples / validation_sample_count if validation_sample_count else 0.0
        ),
        sample_results=sample_results,
        confidence=1.0,
        evidence_text=f"simple Parser job {job['id']}",
        agent_loop={
            "pipeline_version": "simple_v2",
            "generator_protocol_version": outcome.protocol_version,
            "status": outcome.status,
            "stop_reason": outcome.stop_reason,
            "model_calls": outcome.model_calls,
            "model_call_limit": MAX_PARSER_MODEL_CALLS,
            "attempt_ids": list(persisted.attempt_ids),
            "best_attempt_id": persisted.best_attempt_id,
            "best_candidate_template": (
                outcome.candidate.template_text if outcome.candidate else ""
            ),
            "best_candidate_provider": (outcome.candidate.provider if outcome.candidate else None),
            "best_candidate_review": (
                final_review.to_dict() if final_review is not None else None
            ),
        },
    )
    save_report(report, reports_dir)
    return report


def _generation_succeeded(reports: list[GenerationReport]) -> bool:
    return bool(reports) and all(report.accepted for report in reports)


def generate_templates(params: dict[str, Any]) -> dict[str, Any]:
    job_id = str(params.get("job_id") or "").strip()
    if not job_id:
        observe_legacy_path(
            "legacy-parser-job-dispatch",
            "generation_without_job_id",
            outcome="rejected",
        )
        raise ValueError(
            "job_id is required for Parser generation; import evidence or promote a "
            "Parser gap in the control plane before starting generation"
        )
    if params.get("templates_dir"):
        raise ValueError(
            "templates_dir is no longer supported; Parser candidates use immutable "
            "PostgreSQL content"
        )
    reports_dir = Path(params.get("reports_dir") or DEFAULT_REPORTS)
    job = ParserJobRepository().get_job(job_id, include_sanitized_payload=True)
    if job is None:
        raise ValueError(f"parser template job does not exist: {job_id}")
    if job.get("pipeline_version") != "simple_v2":
        observe_legacy_path(
            "legacy-parser-job-dispatch",
            "legacy_job_dispatch",
            outcome="rejected",
        )
        raise ValueError(
            "legacy Parser jobs are read-only; confirm ParserSpec and sample roles to "
            "promote this job to simple_v2"
        )

    report = _process_simple_job(
        job,
        reports_dir=reports_dir,
        publish=bool(params.get("publish", False)),
        dry_run=bool(params.get("dry_run", False)),
    )
    summary_path = save_summary([report], reports_dir, metadata={"mode": "job", "job_id": job_id})
    return {
        "success": _generation_succeeded([report]),
        "message": (
            "job mode processed 1 candidate group; "
            f"published {1 if report.template_generated else 0} templates; "
            f"commands: {report.command}"
        ),
        "mode": "job",
        "total_candidates": 1,
        "candidate_groups": 1,
        "success_count": 1 if report.accepted else 0,
        "published_count": 1 if report.template_generated else 0,
        "generated_templates": [report.template_path] if report.template_path else [],
        "validation_pass_rate": report.validation_pass_rate,
        "parsed_records": report.parsed_records or [],
        "reports": [report.to_dict()],
        "summary_path": str(summary_path),
        "asset_store": "postgresql",
        "job_id": job_id,
    }
