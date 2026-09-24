"""Finite Parser candidate generation with a bounded model/tool loop."""

from __future__ import annotations

import hashlib
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from .samples import ParserSample, SampleSet
from .spec import ParserSpec, ParserSpecDocument
from .validation import ValidationBackend, ValidationReport, validate_template_candidate

MAX_PARSER_MODEL_CALLS = 3
MAX_PARSER_PROVIDER_RETRIES = 1
# Persisted Parser jobs already reference this protocol label.  Provider
# selection is now generic inside the implementation, so keep the durable
# value stable for historical attempts and approvals.
PARSER_TOOL_PROTOCOL_VERSION = "deepseek-thinking-tools-v1"


@dataclass(frozen=True)
class TemplateCandidate:
    template_text: str
    provider: str
    model_name: str | None = None
    token_usage: dict[str, int] | None = None
    latency_ms: int | None = None
    protocol_version: str | None = None
    provenance: dict[str, Any] | None = None

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.template_text.encode("utf-8")).hexdigest()


class DeterministicCompiler(Protocol):
    name: str

    def compile(
        self,
        spec: ParserSpec,
        generation_samples: tuple[ParserSample, ...],
    ) -> TemplateCandidate | None: ...


@dataclass(frozen=True)
class ParserModelTurn:
    candidate: TemplateCandidate | None
    submission_mode: str
    review_reason: str | None = None
    protocol_error: str | None = None
    model_name: str | None = None
    token_usage: dict[str, int] | None = None
    latency_ms: int | None = None
    protocol_version: str | None = None
    retryable: bool = False


class ParserModelSession(Protocol):
    def next(
        self,
        validation_feedback: dict[str, object] | None = None,
    ) -> ParserModelTurn: ...


class ParserModel(Protocol):
    def open_session(
        self,
        spec: ParserSpec | ParserSpecDocument,
        samples: tuple[str, ...],
    ) -> ParserModelSession: ...


@dataclass(frozen=True)
class ParserReviewReport:
    passed: bool
    summary: str
    diagnostics: tuple[dict[str, str], ...] = ()
    primary_key_assessment: dict[str, Any] = field(default_factory=dict)
    completeness_assessment: dict[str, Any] = field(default_factory=dict)
    record_set_assessments: tuple[dict[str, Any], ...] = ()
    model_name: str | None = None
    token_usage: dict[str, int] | None = None
    latency_ms: int | None = None
    protocol_version: str = "independent-semantic-review-v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "summary": self.summary,
            "diagnostics": [dict(item) for item in self.diagnostics],
            "primary_key_assessment": dict(self.primary_key_assessment),
            "completeness_assessment": dict(self.completeness_assessment),
            "record_set_assessments": [
                dict(item) for item in self.record_set_assessments
            ],
            "model_name": self.model_name,
            "token_usage": dict(self.token_usage or {}),
            "latency_ms": self.latency_ms,
            "protocol_version": self.protocol_version,
        }

    def repair_feedback(self) -> dict[str, object]:
        return {
            "failure_type": "independent_semantic_review_failed",
            "primary_diagnostic": (
                dict(self.diagnostics[0])
                if self.diagnostics
                else {
                    "code": "semantic_review_failed",
                    "message": self.summary,
                    "target": "template",
                }
            ),
            "all_diagnostics": [dict(item) for item in self.diagnostics],
            "primary_key_assessment": dict(self.primary_key_assessment),
            "completeness_assessment": dict(self.completeness_assessment),
            "record_set_assessments": [
                dict(item) for item in self.record_set_assessments
            ],
        }


def _review_is_unavailable(report: ParserReviewReport | None) -> bool:
    return bool(
        report
        and not report.passed
        and any(
            str(item.get("code") or "").strip().casefold()
            == "review_provider_or_protocol_error"
            for item in report.diagnostics
        )
    )


class ParserCandidateReviewer(Protocol):
    def review(
        self,
        *,
        spec: ParserSpec | ParserSpecDocument,
        samples: tuple[ParserSample, ...],
        candidate: TemplateCandidate,
        validation: ValidationReport,
    ) -> ParserReviewReport: ...


class ModelCallBudgetExhaustedError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelCallReservation:
    id: str
    call_number: int
    generation_run_id: str


@dataclass(frozen=True)
class ModelCallCompletion:
    status: str
    model_name: str | None = None
    candidate_sha256: str | None = None
    failure_signature: str | None = None
    result: dict[str, object] | None = None
    token_usage: dict[str, int] | None = None
    latency_ms: int | None = None
    error: str | None = None


class ModelCallController(Protocol):
    @property
    def used(self) -> int: ...

    def reserve(self, generation_run_id: str) -> ModelCallReservation: ...

    def complete(
        self,
        reservation: ModelCallReservation,
        completion: ModelCallCompletion,
    ) -> None: ...

    def release(self, reservation: ModelCallReservation) -> None: ...


class GenerationObserver(Protocol):
    """Durably observes progress and completed candidate attempts."""

    def progress(
        self,
        *,
        generation_run_id: str,
        stage: str,
        attempt_number: int,
        deadline_at: str | None,
        details: dict[str, object] | None = None,
    ) -> None: ...

    def record_attempt(
        self,
        *,
        generation_run_id: str,
        attempt: GenerationAttempt,
        protocol_version: str | None,
    ) -> str | None: ...


@dataclass
class InMemoryModelCallController:
    max_calls: int = MAX_PARSER_MODEL_CALLS
    used: int = 0
    completions: list[tuple[ModelCallReservation, ModelCallCompletion]] = field(
        default_factory=list
    )
    released: set[str] = field(default_factory=set)
    request_sequence: int = 0

    def reserve(self, generation_run_id: str) -> ModelCallReservation:
        if self.used >= self.max_calls:
            raise ModelCallBudgetExhaustedError(
                f"Parser model-call budget exhausted ({self.used}/{self.max_calls})"
            )
        self.used += 1
        self.request_sequence += 1
        return ModelCallReservation(
            id=f"in-memory-call-{self.request_sequence}",
            call_number=self.used,
            generation_run_id=generation_run_id,
        )

    def complete(
        self,
        reservation: ModelCallReservation,
        completion: ModelCallCompletion,
    ) -> None:
        self.completions.append((reservation, completion))

    def release(self, reservation: ModelCallReservation) -> None:
        if reservation.id in self.released:
            return
        self.released.add(reservation.id)
        self.used = max(0, self.used - 1)


@dataclass(frozen=True)
class GenerationAttempt:
    stage: str
    candidate: TemplateCandidate
    generation_validation: ValidationReport
    acceptance_validation: ValidationReport | None = None
    review_report: ParserReviewReport | None = None
    persisted_id: str | None = None


@dataclass(frozen=True)
class GenerationOutcome:
    status: str
    stop_reason: str
    candidate: TemplateCandidate | None
    attempts: tuple[GenerationAttempt, ...]
    model_calls: int
    provider_retries: int
    protocol_version: str | None = None
    generation_run_id: str | None = None

    @property
    def passed(self) -> bool:
        return self.status == "candidate_ready"


class SimpleParserGenerationService:
    def __init__(
        self,
        *,
        compilers: tuple[DeterministicCompiler, ...],
        model: ParserModel,
        backend: ValidationBackend,
        reviewer: ParserCandidateReviewer | None = None,
        max_model_calls: int = MAX_PARSER_MODEL_CALLS,
        max_provider_retries: int = MAX_PARSER_PROVIDER_RETRIES,
        model_call_controller: ModelCallController | None = None,
        observer: GenerationObserver | None = None,
        seed_candidates: tuple[TemplateCandidate, ...] = (),
        total_timeout_seconds: int | None = None,
        progress_heartbeat_seconds: int = 15,
        validator: Callable[
            [
                str,
                ParserSpec | ParserSpecDocument,
                tuple[ParserSample, ...],
                ValidationBackend,
            ],
            ValidationReport,
        ] = validate_template_candidate,
    ) -> None:
        if len(compilers) > 3:
            raise ValueError(
                "Simple Parser pipeline supports at most three deterministic compilers"
            )
        self.compilers = compilers
        self.model = model
        self.backend = backend
        self.reviewer = reviewer
        if not 1 <= max_model_calls <= MAX_PARSER_MODEL_CALLS:
            raise ValueError(
                f"Parser model-call budget must be between 1 and {MAX_PARSER_MODEL_CALLS}"
            )
        self.max_model_calls = max_model_calls
        if not 0 <= max_provider_retries <= MAX_PARSER_PROVIDER_RETRIES:
            raise ValueError(
                "Parser provider retries must be between 0 and "
                f"{MAX_PARSER_PROVIDER_RETRIES}"
            )
        self.max_provider_retries = max_provider_retries
        self.model_call_controller = model_call_controller
        self.observer = observer
        self.seed_candidates = seed_candidates
        if total_timeout_seconds is not None and int(total_timeout_seconds) <= 0:
            raise ValueError("Parser generation timeout must be positive")
        self.total_timeout_seconds = (
            int(total_timeout_seconds) if total_timeout_seconds is not None else None
        )
        self.progress_heartbeat_seconds = max(5, int(progress_heartbeat_seconds))
        self.validator = validator

    def generate(
        self,
        *,
        job_id: str,
        spec: ParserSpec | ParserSpecDocument,
        sample_set: SampleSet,
    ) -> GenerationOutcome:
        if not str(job_id).strip():
            raise ValueError("job_id is required")
        attempts: list[GenerationAttempt] = []
        generation_run_id = uuid.uuid4().hex
        started_monotonic = time.monotonic()
        deadline_monotonic = (
            started_monotonic + self.total_timeout_seconds
            if self.total_timeout_seconds is not None
            else None
        )
        deadline_at = (
            (
                datetime.now(timezone.utc)
                + timedelta(seconds=self.total_timeout_seconds)
            ).isoformat()
            if self.total_timeout_seconds is not None
            else None
        )
        provider_retries = 0
        model_calls = self.model_call_controller or InMemoryModelCallController(
            max_calls=self.max_model_calls
        )

        def emit_progress(
            stage: str,
            *,
            attempt_number: int | None = None,
            details: dict[str, object] | None = None,
        ) -> None:
            if self.observer is None:
                return
            self.observer.progress(
                generation_run_id=generation_run_id,
                stage=stage,
                attempt_number=(
                    max(1, int(attempt_number))
                    if attempt_number is not None
                    else len(attempts) + 1
                ),
                deadline_at=deadline_at,
                details=details,
            )

        def deadline_exceeded() -> bool:
            return bool(
                deadline_monotonic is not None
                and time.monotonic() >= deadline_monotonic
            )

        def append_attempt(
            attempt: GenerationAttempt,
            *,
            protocol_version: str | None = None,
        ) -> GenerationAttempt:
            if self.observer is not None:
                persisted_id = self.observer.record_attempt(
                    generation_run_id=generation_run_id,
                    attempt=attempt,
                    protocol_version=protocol_version,
                )
                if persisted_id:
                    attempt = replace(attempt, persisted_id=str(persisted_id))
            attempts.append(attempt)
            diagnostic = (
                attempt.generation_validation.failure_signature
                if not attempt.generation_validation.passed
                else (
                    attempt.acceptance_validation.failure_signature
                    if attempt.acceptance_validation is not None
                    and not attempt.acceptance_validation.passed
                    else None
                )
            )
            emit_progress(
                "attempt_persisted",
                attempt_number=len(attempts),
                details={
                    "attempt_id": attempt.persisted_id,
                    "candidate_sha256": attempt.candidate.sha256,
                    "diagnostic_code": diagnostic,
                },
            )
            return attempt

        def finish(
            status: str,
            stop_reason: str,
            candidate: TemplateCandidate | None,
            protocol_version: str | None = None,
        ) -> GenerationOutcome:
            emit_progress(
                "candidate_freezing" if status == "candidate_ready" else "generation_stopped",
                attempt_number=max(1, len(attempts)),
                details={"status": status, "stop_reason": stop_reason},
            )
            return GenerationOutcome(
                status=status,
                stop_reason=stop_reason,
                candidate=candidate,
                attempts=tuple(attempts),
                model_calls=model_calls.used,
                provider_retries=provider_retries,
                protocol_version=protocol_version,
                generation_run_id=generation_run_id,
            )

        def objective_candidate_ready(
            candidate: TemplateCandidate | None,
        ) -> bool:
            return bool(
                candidate
                and any(
                    attempt.candidate == candidate
                    and attempt.generation_validation.passed
                    and (
                        attempt.acceptance_validation is None
                        or attempt.acceptance_validation.passed
                    )
                    for attempt in attempts
                )
            )

        def finish_blocked_or_human_review(
            stop_reason: str,
            candidate: TemplateCandidate | None,
            protocol_version: str | None = None,
        ) -> GenerationOutcome:
            review_candidate = (
                candidate
                if objective_candidate_ready(candidate)
                else next(
                    (
                        attempt.candidate
                        for attempt in reversed(attempts)
                        if attempt.generation_validation.passed
                        and (
                            attempt.acceptance_validation is None
                            or attempt.acceptance_validation.passed
                        )
                    ),
                    None,
                )
            )
            if review_candidate is not None:
                return finish(
                    "candidate_ready",
                    f"human_review_required:{stop_reason}",
                    review_candidate,
                    protocol_version,
                )
            return finish(
                "blocked",
                stop_reason,
                candidate,
                protocol_version,
            )

        def record_model_protocol_attempt(
            *,
            call_number: int,
            turn: ParserModelTurn,
            message: str,
        ) -> None:
            append_attempt(
                GenerationAttempt(
                    f"llm_tool_round_{call_number}",
                    TemplateCandidate(
                        template_text="",
                        provider=f"llm:{turn.submission_mode}",
                        model_name=turn.model_name,
                        token_usage=turn.token_usage,
                        latency_ms=turn.latency_ms,
                        protocol_version=(turn.protocol_version or PARSER_TOOL_PROTOCOL_VERSION),
                    ),
                    ValidationReport(
                        passed=False,
                        hard_gates={"model_protocol_valid": False},
                        samples=(),
                        diagnostics=(message,),
                    ),
                ),
                protocol_version=(turn.protocol_version or PARSER_TOOL_PROTOCOL_VERSION),
            )

        def validate_generation(candidate: TemplateCandidate) -> ValidationReport:
            emit_progress(
                "sample_replaying",
                details={
                    "candidate_sha256": candidate.sha256,
                    "sample_count": len(sample_set.generation) + len(sample_set.negative),
                },
            )
            return self.validator(
                candidate.template_text,
                spec,
                (*sample_set.generation, *sample_set.negative),
                self.backend,
            )

        def review_evidence(
            generation_report: ValidationReport,
            acceptance_report: ValidationReport | None,
        ) -> ValidationReport:
            """Combine every frozen sample result for one stateless semantic review."""

            if acceptance_report is None:
                return generation_report
            samples_by_id = {
                sample.sample_id: sample
                for report in (generation_report, acceptance_report)
                for sample in report.samples
            }
            return ValidationReport(
                passed=generation_report.passed and acceptance_report.passed,
                hard_gates={
                    **generation_report.hard_gates,
                    **{
                        f"acceptance_{name}": passed
                        for name, passed in acceptance_report.hard_gates.items()
                    },
                },
                samples=tuple(samples_by_id.values()),
                diagnostics=(
                    *generation_report.diagnostics,
                    *(
                        f"acceptance: {diagnostic}"
                        for diagnostic in acceptance_report.diagnostics
                    ),
                ),
            )

        def review_candidate(
            candidate: TemplateCandidate,
            execution_evidence: ValidationReport,
        ) -> ParserReviewReport | None:
            if self.reviewer is None:
                return None
            emit_progress(
                "semantic_reviewing",
                details={"candidate_sha256": candidate.sha256},
            )
            stop_heartbeat = threading.Event()

            def heartbeat() -> None:
                while not stop_heartbeat.wait(self.progress_heartbeat_seconds):
                    emit_progress(
                        "semantic_reviewing",
                        details={
                            "candidate_sha256": candidate.sha256,
                            "heartbeat_only": True,
                        },
                    )

            heartbeat_thread = threading.Thread(
                target=heartbeat,
                name=f"parser-review-heartbeat-{generation_run_id[:8]}",
                daemon=True,
            )
            heartbeat_thread.start()
            try:
                return self.reviewer.review(
                    spec=spec,
                    samples=sample_set.samples,
                    candidate=candidate,
                    validation=execution_evidence,
                )
            finally:
                stop_heartbeat.set()
                heartbeat_thread.join(timeout=1)

        emit_progress(
            "context_prepared",
            details={
                "generation_sample_count": len(sample_set.generation),
                "acceptance_sample_count": len(sample_set.acceptance),
                "negative_sample_count": len(sample_set.negative),
                "model_call_limit": self.max_model_calls,
            },
        )
        initial_feedback: dict[str, object] | None = None
        last_seed_candidate: TemplateCandidate | None = None
        for candidate in self.seed_candidates:
            generation_report = validate_generation(candidate)
            acceptance_report = (
                self.validator(
                    candidate.template_text,
                    spec,
                    (*sample_set.acceptance, *sample_set.negative),
                    self.backend,
                )
                if generation_report.passed and sample_set.acceptance
                else None
            )
            semantic_evidence = review_evidence(
                generation_report,
                acceptance_report,
            )
            review_report = (
                review_candidate(candidate, semantic_evidence)
                if generation_report.passed
                and (acceptance_report is None or acceptance_report.passed)
                else None
            )
            append_attempt(
                GenerationAttempt(
                    "llm_revalidate_persisted",
                    candidate,
                    generation_report,
                    acceptance_report,
                    review_report,
                ),
                protocol_version=candidate.protocol_version,
            )
            if (
                generation_report.passed
                and (acceptance_report is None or acceptance_report.passed)
                and (review_report is None or review_report.passed)
            ):
                return finish(
                    "candidate_ready",
                    "persisted_candidate_revalidated",
                    candidate,
                    candidate.protocol_version,
                )
            last_seed_candidate = candidate
            if not generation_report.passed:
                initial_feedback = {
                    **generation_report.repair_diagnostic(),
                    "current_template": candidate.template_text,
                    "candidate_provider": candidate.provider,
                    "candidate_sha256": candidate.sha256,
                }
            elif acceptance_report is not None and not acceptance_report.passed:
                initial_feedback = {
                    **acceptance_report.repair_diagnostic(),
                    "current_template": candidate.template_text,
                    "candidate_provider": candidate.provider,
                    "candidate_sha256": candidate.sha256,
                }
            elif review_report is not None and not review_report.passed:
                if _review_is_unavailable(review_report):
                    return finish(
                        "candidate_ready",
                        "human_review_required:semantic_review_unavailable",
                        candidate,
                        candidate.protocol_version,
                    )
                initial_feedback = {
                    **review_report.repair_feedback(),
                    "parsed_results": semantic_evidence.parsed_result_preview(),
                    "local_execution": semantic_evidence.to_dict(),
                    "current_template": candidate.template_text,
                    "candidate_provider": candidate.provider,
                    "candidate_sha256": candidate.sha256,
                }

        for compiler in (
            self.compilers if isinstance(spec, ParserSpec) else ()
        ):
            candidate = compiler.compile(spec, sample_set.generation)
            if candidate is None:
                continue
            last_seed_candidate = candidate
            generation_report = validate_generation(candidate)
            if not generation_report.passed:
                append_attempt(
                    GenerationAttempt(
                        f"deterministic:{compiler.name}",
                        candidate,
                        generation_report,
                    ),
                )
                initial_feedback = {
                    **generation_report.repair_diagnostic(),
                    "current_template": candidate.template_text,
                    "candidate_provider": candidate.provider,
                    "candidate_sha256": candidate.sha256,
                }
                continue
            acceptance_report = (
                self.validator(
                    candidate.template_text,
                    spec,
                    (*sample_set.acceptance, *sample_set.negative),
                    self.backend,
                )
                if sample_set.acceptance
                else None
            )
            semantic_evidence = review_evidence(
                generation_report,
                acceptance_report,
            )
            review_report = (
                review_candidate(candidate, semantic_evidence)
                if acceptance_report is None or acceptance_report.passed
                else None
            )
            append_attempt(
                GenerationAttempt(
                    f"deterministic:{compiler.name}",
                    candidate,
                    generation_report,
                    acceptance_report,
                    review_report,
                ),
            )
            if acceptance_report is not None and not acceptance_report.passed:
                initial_feedback = {
                    **acceptance_report.repair_diagnostic(),
                    "current_template": candidate.template_text,
                    "candidate_provider": candidate.provider,
                    "candidate_sha256": candidate.sha256,
                }
                continue
            if review_report is not None and not review_report.passed:
                if _review_is_unavailable(review_report):
                    return finish(
                        "candidate_ready",
                        "human_review_required:semantic_review_unavailable",
                        candidate,
                    )
                initial_feedback = {
                    **review_report.repair_feedback(),
                    "parsed_results": semantic_evidence.parsed_result_preview(),
                    "local_execution": semantic_evidence.to_dict(),
                    "current_template": candidate.template_text,
                    "candidate_provider": candidate.provider,
                    "candidate_sha256": candidate.sha256,
                }
                continue
            return finish(
                "candidate_ready",
                "deterministic_candidate_passed",
                candidate,
            )

        session = self.model.open_session(spec, sample_set.model_samples)
        feedback: dict[str, object] | None = initial_feedback
        last_candidate: TemplateCandidate | None = last_seed_candidate
        repeated_failure_count = 0
        last_failure_signature: str | None = None

        while True:
            if deadline_exceeded():
                return finish_blocked_or_human_review(
                    "generation_deadline_exceeded",
                    last_candidate,
                    PARSER_TOOL_PROTOCOL_VERSION,
                )
            try:
                reservation = model_calls.reserve(generation_run_id)
            except ModelCallBudgetExhaustedError:
                return finish_blocked_or_human_review(
                    "model_call_budget_exhausted",
                    last_candidate,
                    PARSER_TOOL_PROTOCOL_VERSION,
                )
            call_number = reservation.call_number
            emit_progress(
                "model_generating" if call_number == 1 else "model_revising",
                details={
                    "call_number": call_number,
                    "model_calls_used": model_calls.used,
                    "model_call_limit": self.max_model_calls,
                },
            )
            try:
                turn = session.next(feedback)
            except Exception as exc:
                turn = ParserModelTurn(
                    candidate=None,
                    submission_mode="provider_error",
                    protocol_error=f"provider_request_failed:{type(exc).__name__}: {exc}",
                    protocol_version=PARSER_TOOL_PROTOCOL_VERSION,
                    retryable=False,
                )
            feedback = None
            if turn.review_reason:
                record_model_protocol_attempt(
                    call_number=call_number,
                    turn=turn,
                    message=f"model requested engineer review: {turn.review_reason}",
                )
                model_calls.complete(
                    reservation,
                    ModelCallCompletion(
                        status="review_requested",
                        model_name=turn.model_name,
                        result={"submission_mode": turn.submission_mode},
                        token_usage=turn.token_usage,
                        latency_ms=turn.latency_ms,
                        error=turn.review_reason,
                    ),
                )
                return finish_blocked_or_human_review(
                    f"model_requested_review:{turn.review_reason}",
                    last_candidate,
                    PARSER_TOOL_PROTOCOL_VERSION,
                )
            if turn.candidate is None:
                protocol_message = turn.protocol_error or "model returned no candidate"
                provider_error = turn.submission_mode == "provider_error"
                model_calls.complete(
                    reservation,
                    ModelCallCompletion(
                        status="provider_failed" if provider_error else "protocol_failed",
                        model_name=turn.model_name,
                        result={
                            "submission_mode": turn.submission_mode,
                            "retryable": turn.retryable,
                        },
                        token_usage=turn.token_usage,
                        latency_ms=turn.latency_ms,
                        error=protocol_message,
                    ),
                )
                if provider_error:
                    if "model_timeout" in protocol_message.casefold():
                        record_model_protocol_attempt(
                            call_number=call_number,
                            turn=turn,
                            message=protocol_message,
                        )
                        return finish_blocked_or_human_review(
                            "model_timeout",
                            last_candidate,
                            PARSER_TOOL_PROTOCOL_VERSION,
                        )
                    if not turn.retryable:
                        record_model_protocol_attempt(
                            call_number=call_number,
                            turn=turn,
                            message=protocol_message,
                        )
                        return finish_blocked_or_human_review(
                            "provider_request_failed",
                            last_candidate,
                            PARSER_TOOL_PROTOCOL_VERSION,
                        )
                    if provider_retries < self.max_provider_retries:
                        model_calls.release(reservation)
                        provider_retries += 1
                        continue
                    record_model_protocol_attempt(
                        call_number=call_number,
                        turn=turn,
                        message=protocol_message,
                    )
                    return finish_blocked_or_human_review(
                        "provider_retry_budget_exhausted",
                        last_candidate,
                        PARSER_TOOL_PROTOCOL_VERSION,
                    )
                record_model_protocol_attempt(
                    call_number=call_number,
                    turn=turn,
                    message=protocol_message,
                )
                feedback = {
                    "failure_type": "model_protocol_error",
                    "primary_diagnostic": {
                        "code": "model_protocol_error",
                        "message": protocol_message,
                        "allowed_action": "submit_complete_template",
                    },
                    "calls_remaining": self.max_model_calls - model_calls.used,
                }
                if model_calls.used < self.max_model_calls:
                    continue
                return finish_blocked_or_human_review(
                    "model_protocol_failed",
                    last_candidate,
                    PARSER_TOOL_PROTOCOL_VERSION,
                )

            candidate = turn.candidate
            last_candidate = candidate
            generation_report = validate_generation(candidate)
            acceptance_report = None
            if generation_report.passed and sample_set.acceptance:
                acceptance_report = self.validator(
                    candidate.template_text,
                    spec,
                    (*sample_set.acceptance, *sample_set.negative),
                    self.backend,
                )
            semantic_evidence = review_evidence(
                generation_report,
                acceptance_report,
            )
            review_report = (
                review_candidate(candidate, semantic_evidence)
                if generation_report.passed
                and (acceptance_report is None or acceptance_report.passed)
                else None
            )
            append_attempt(
                GenerationAttempt(
                    f"llm_tool_round_{call_number}",
                    candidate,
                    generation_report,
                    acceptance_report,
                    review_report,
                ),
                protocol_version=PARSER_TOOL_PROTOCOL_VERSION,
            )
            call_status = (
                "validation_failed"
                if not generation_report.passed
                else (
                    "acceptance_failed"
                    if acceptance_report is not None and not acceptance_report.passed
                    else (
                        "semantic_review_failed"
                        if review_report is not None and not review_report.passed
                        else "candidate_ready"
                    )
                )
            )
            failure_signature = (
                generation_report.failure_signature
                if not generation_report.passed
                else (
                    acceptance_report.failure_signature
                    if acceptance_report is not None and not acceptance_report.passed
                    else (
                        str(
                            (review_report.diagnostics[0] if review_report.diagnostics else {}).get(
                                "code",
                                "semantic_review_failed",
                            )
                        )
                        if review_report is not None and not review_report.passed
                        else None
                    )
                )
            )
            model_calls.complete(
                reservation,
                ModelCallCompletion(
                    status=call_status,
                    model_name=turn.model_name,
                    candidate_sha256=candidate.sha256,
                    failure_signature=failure_signature,
                    result={
                        "submission_mode": turn.submission_mode,
                        "generation_passed": generation_report.passed,
                        "generation_hard_gates": generation_report.hard_gates,
                        "acceptance_passed": (
                            acceptance_report.passed
                            if acceptance_report is not None
                            else None
                        ),
                        "semantic_review": (
                            review_report.to_dict() if review_report is not None else None
                        ),
                    },
                    token_usage=turn.token_usage,
                    latency_ms=turn.latency_ms,
                ),
            )
            if generation_report.passed:
                if acceptance_report is not None and not acceptance_report.passed:
                    feedback = {
                        **acceptance_report.repair_diagnostic(),
                        "current_template": candidate.template_text,
                        "candidate_sha256": candidate.sha256,
                        "round": call_number,
                        "calls_remaining": self.max_model_calls - model_calls.used,
                    }
                    if model_calls.used < self.max_model_calls:
                        continue
                    return finish_blocked_or_human_review(
                        "acceptance_execution_failed",
                        candidate,
                        PARSER_TOOL_PROTOCOL_VERSION,
                    )
                if review_report is not None and not review_report.passed:
                    if _review_is_unavailable(review_report):
                        return finish(
                            "candidate_ready",
                            "human_review_required:semantic_review_unavailable",
                            candidate,
                            PARSER_TOOL_PROTOCOL_VERSION,
                        )
                    feedback = {
                        **review_report.repair_feedback(),
                        "parsed_results": semantic_evidence.parsed_result_preview(),
                        "local_execution": semantic_evidence.to_dict(),
                        "current_template": candidate.template_text,
                        "candidate_sha256": candidate.sha256,
                        "round": call_number,
                        "calls_remaining": self.max_model_calls - model_calls.used,
                    }
                    if model_calls.used < self.max_model_calls:
                        continue
                    return finish_blocked_or_human_review(
                        "independent_semantic_review_failed",
                        candidate,
                        PARSER_TOOL_PROTOCOL_VERSION,
                    )
                return finish(
                    "candidate_ready",
                    "llm_tool_candidate_passed",
                    candidate,
                    PARSER_TOOL_PROTOCOL_VERSION,
                )

            failure_signature = generation_report.failure_signature
            repeated_failure_count = (
                repeated_failure_count + 1 if failure_signature == last_failure_signature else 1
            )
            last_failure_signature = failure_signature
            feedback = {
                **generation_report.repair_diagnostic(),
                "candidate_sha256": candidate.sha256,
                "round": call_number,
                "calls_remaining": self.max_model_calls - model_calls.used,
                "repeated_failure_count": repeated_failure_count,
            }
            if deadline_exceeded():
                return finish_blocked_or_human_review(
                    "generation_deadline_exceeded",
                    last_candidate,
                    PARSER_TOOL_PROTOCOL_VERSION,
                )
            if model_calls.used >= self.max_model_calls:
                return finish_blocked_or_human_review(
                    "generation_validation_failed",
                    last_candidate,
                    PARSER_TOOL_PROTOCOL_VERSION,
                )
