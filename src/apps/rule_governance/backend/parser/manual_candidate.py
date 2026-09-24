"""Engineer-submitted Parser candidate validation and publication."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from src.apps.rule_governance.backend.parser.repository import (
    ParserJobRepository,
    ParserJobStateError,
    ParserTemplateRepository,
)
from src.apps.rule_governance.backend.parser_generation.publishing import persist_generation
from src.apps.rule_governance.backend.parser_generation.samples import (
    ParserSample,
    SampleExpectation,
    SampleRole,
    SampleSet,
)
from src.apps.rule_governance.backend.parser_generation.service import (
    GenerationAttempt,
    GenerationOutcome,
    TemplateCandidate,
)
from src.apps.rule_governance.backend.parser_generation.spec import ParserSpec, ParserSpecDocument
from src.apps.rule_governance.backend.parser_generation.validation import (
    TextFSMValidationBackend,
    ValidationReport,
    validate_template_execution,
    validate_template_runtime_compatibility,
)
from src.core.patrol.repository import PatrolRepository


@dataclass(frozen=True)
class ManualParserCandidateResult:
    job_id: str
    template_id: str
    attempt_id: str
    candidate_sha256: str
    execution: ValidationReport
    runtime_compatibility: ValidationReport

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": True,
            "job_id": self.job_id,
            "template_id": self.template_id,
            "attempt_id": self.attempt_id,
            "candidate_sha256": self.candidate_sha256,
            "status": "pending_approval",
            "human_semantic_review_required": True,
            "execution": self.execution.to_dict(),
            "runtime_compatibility": self.runtime_compatibility.to_dict(),
        }


class ManualParserCandidateValidationError(ValueError):
    """Raised when an engineer revision is not executable on frozen evidence."""

    def __init__(
        self,
        message: str,
        *,
        execution: ValidationReport,
        runtime_compatibility: ValidationReport | None = None,
    ) -> None:
        super().__init__(message)
        self.execution = execution
        self.runtime_compatibility = runtime_compatibility

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": False,
            "error": str(self),
            "execution": self.execution.to_dict(),
            "runtime_compatibility": (
                self.runtime_compatibility.to_dict()
                if self.runtime_compatibility is not None
                else None
            ),
        }


def _load_sample_set(job: dict[str, Any], patrol: PatrolRepository) -> SampleSet:
    samples: list[ParserSample] = []
    for item in job.get("samples") or []:
        snapshot_id = str(item.get("snapshot_id") or "").strip()
        context = patrol.get_snapshot_context(snapshot_id)
        if context is None or context.get("snapshot") is None:
            raise ParserJobStateError(f"Parser sample is unavailable: {snapshot_id}")
        sanitized_text = str(item.get("sanitized_text") or "")
        if item.get("sanitization_status") != "sanitized" or not sanitized_text:
            raise ParserJobStateError(
                f"Parser sample is not sanitized: {item.get('id')}"
            )
        try:
            role = SampleRole(str(item.get("sample_role") or "unassigned"))
        except ValueError as exc:
            raise ParserJobStateError(
                f"invalid Parser sample role: {item.get('sample_role')}"
            ) from exc
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
                prompt_text=(
                    sanitized_text if role is SampleRole.GENERATION else None
                ),
                expectation=expectation,
                validation_text_override=sanitized_text,
            )
        )
    return SampleSet(tuple(samples))


class ManualParserCandidateSubmissionService:
    """Validate an engineer revision and freeze it as a normal approval candidate."""

    def __init__(
        self,
        *,
        job_repository: ParserJobRepository | None = None,
        template_repository: ParserTemplateRepository | None = None,
        patrol_repository: PatrolRepository | None = None,
        backend: TextFSMValidationBackend | None = None,
    ) -> None:
        self.jobs = job_repository or ParserJobRepository()
        self.templates = template_repository or ParserTemplateRepository()
        self.patrol = patrol_repository or PatrolRepository()
        self.backend = backend or TextFSMValidationBackend()

    def submit(
        self,
        *,
        job_id: str,
        template_text: str,
        actor: str,
        actor_user_id: str | None = None,
        reason: str | None = None,
        based_on_template_id: str | None = None,
    ) -> ManualParserCandidateResult:
        normalized_actor = str(actor or "").strip()
        if not normalized_actor:
            raise ValueError("engineer candidate submission requires an actor")
        candidate_text = str(template_text or "").rstrip()
        if not candidate_text:
            raise ValueError("engineer candidate submission requires TextFSM content")
        candidate_text += "\n"

        job = self.jobs.get_job(job_id, include_sanitized_payload=True)
        if job is None:
            raise ParserJobStateError(f"parser job {job_id} not found")
        if job.get("pipeline_version") != "simple_v2":
            raise ParserJobStateError("engineer revisions require a simple_v2 Parser job")
        readiness = dict(job.get("generation_readiness") or {})
        if not readiness.get("ready"):
            raise ParserJobStateError(
                "Parser job is not ready for engineer revision: "
                + ", ".join(
                    str(item)
                    for item in readiness.get("blockers") or ["readiness_unknown"]
                )
            )

        parent_template = None
        if based_on_template_id:
            parent_template = self.templates.get_template(based_on_template_id)
            if parent_template is None:
                raise ParserJobStateError("based-on Parser candidate does not exist")
            if str(parent_template.source_job_id or "") != str(job_id):
                raise ParserJobStateError(
                    "based-on Parser candidate belongs to another Parser job"
                )

        spec_document = ParserSpecDocument.from_dict(
            dict(job.get("parser_spec_json") or {})
        )
        spec: ParserSpec | ParserSpecDocument = (
            spec_document.single_record_set
            if len(spec_document.record_sets) == 1
            else spec_document
        )
        sample_set = _load_sample_set(job, self.patrol)
        execution = validate_template_execution(
            candidate_text,
            spec,
            sample_set.samples,
            self.backend,
        )
        if not execution.passed:
            raise ManualParserCandidateValidationError(
                "engineer candidate could not execute on every frozen sample",
                execution=execution,
            )
        runtime_compatibility = validate_template_runtime_compatibility(
            candidate_text,
            spec,
            sample_set.samples,
            self.backend,
        )
        if not runtime_compatibility.passed:
            raise ManualParserCandidateValidationError(
                "engineer candidate is not compatible with the production Parser runtime",
                execution=execution,
                runtime_compatibility=runtime_compatibility,
            )

        candidate = TemplateCandidate(
            template_text=candidate_text,
            provider="engineer:manual_revision",
            protocol_version="engineer-manual-v1",
            provenance={
                "source": "engineer",
                "submitted_by": normalized_actor,
                "reason": str(reason or "").strip() or None,
                "based_on_template_id": based_on_template_id,
                "based_on_sha256": (
                    str(parent_template.sha256) if parent_template is not None else None
                ),
            },
        )
        attempt = GenerationAttempt(
            stage="engineer_manual_revision",
            candidate=candidate,
            generation_validation=execution,
            acceptance_validation=runtime_compatibility,
            review_report=None,
        )
        outcome = GenerationOutcome(
            status="candidate_ready",
            stop_reason="human_review_required:engineer_submitted",
            candidate=candidate,
            attempts=(attempt,),
            model_calls=0,
            provider_retries=0,
            protocol_version="engineer-manual-v1",
            generation_run_id=uuid.uuid4().hex,
        )

        current_status = str(job.get("status") or "")
        if current_status in {"failed", "cancelled", "succeeded"}:
            self.jobs.retry_job(
                job_id,
                actor=normalized_actor,
                actor_user_id=actor_user_id,
                reason=str(reason or "").strip() or "engineer submitted a Parser revision",
            )
            current_status = "pending"
        if current_status not in {"collecting", "pending"}:
            raise ParserJobStateError(
                f"parser job {job_id} cannot accept an engineer revision from {current_status}"
            )
        # Claim a durable execution identity for this synchronous submission too.
        # The existing task-id fence prevents an older worker from saving over it;
        # this does not enqueue a Celery task.
        execution_id = "manual:" + uuid.uuid4().hex
        self.jobs.mark_job_dispatched(job_id, execution_id,
            actor=normalized_actor, actor_user_id=actor_user_id)
        self.jobs.transition_job_status(
            job_id,
            "running",
            expected_status="pending",
            expected_celery_task_id=execution_id,
        )
        try:
            current_job = self.jobs.get_job(job_id, include_sanitized_payload=True)
            if current_job is None:
                raise ParserJobStateError(f"parser job {job_id} disappeared")
            persisted = persist_generation(
                job=current_job,
                spec=spec,
                sample_set=sample_set,
                outcome=outcome,
                publish=True,
                dry_run=False,
                job_repository=self.jobs,
                template_repository=self.templates,
            )
            if not persisted.template_generated or not persisted.best_attempt_id:
                raise RuntimeError("engineer Parser candidate was not frozen")
            self.jobs.transition_job_status(
                job_id,
                "succeeded",
                expected_status="running",
            )
        except Exception as exc:
            current = self.jobs.get_job(job_id) or {}
            if current.get("status") in {"running", "validating"}:
                self.jobs.transition_job_status(
                    job_id,
                    "failed",
                    str(exc),
                    failure_code="engineer_candidate_persist_failed",
                )
            raise

        template_id = ""
        for item in self.templates.list_templates():
            if (
                str(item.get("source_job_id") or "") == str(job_id)
                and str(item.get("version") or "")
                == f"simple-{persisted.best_attempt_id}"
            ):
                template_id = str(item["id"])
                break
        if not template_id:
            raise RuntimeError("engineer Parser candidate was frozen but cannot be reloaded")
        return ManualParserCandidateResult(
            job_id=job_id,
            template_id=template_id,
            attempt_id=persisted.best_attempt_id,
            candidate_sha256=candidate.sha256,
            execution=execution,
            runtime_compatibility=runtime_compatibility,
        )
