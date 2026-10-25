"""Durable attempt recording and immutable publication for Simple Parser jobs."""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from src.apps.rule_governance.backend.parser.textfsm_dialect import (
    DEFAULT_TEXTFSM_DIALECT,
    dialect_hash,
    installed_textfsm_version,
)

from .samples import ParserSample, SampleRole, SampleSet
from .service import (
    MAX_PARSER_MODEL_CALLS,
    MAX_PARSER_PROVIDER_RETRIES,
    PARSER_TOOL_PROTOCOL_VERSION,
    GenerationAttempt,
    GenerationOutcome,
    ModelCallBudgetExhaustedError,
    ModelCallCompletion,
    ModelCallReservation,
    TemplateCandidate,
)
from .spec import (
    ParserSpec,
    ParserSpecDocument,
    normalize_parser_spec_payload,
    parser_spec_digest,
)

if TYPE_CHECKING:
    from src.apps.rule_governance.backend.parser.repository import (
        ParserJobRepository,
        ParserTemplateRepository,
    )


@dataclass(frozen=True)
class PersistedGeneration:
    attempt_ids: tuple[str, ...]
    best_attempt_id: str | None
    template_generated: bool
    template_path: str | None
    validation_payload: dict[str, Any] | None


class RepositoryModelCallController:
    """Persist a model-call reservation before the provider request starts."""

    def __init__(
        self,
        *,
        job_id: str,
        repository: ParserJobRepository,
        used: int = 0,
        max_calls: int = MAX_PARSER_MODEL_CALLS,
        heartbeat_seconds: int = 15,
        request_timeout_seconds: int = 480,
    ) -> None:
        self.job_id = str(job_id)
        self.repository = repository
        self._used = max(0, int(used))
        self.max_calls = int(max_calls)
        self.heartbeat_seconds = max(5, int(heartbeat_seconds))
        self.request_timeout_seconds = max(1, int(request_timeout_seconds))
        self._heartbeat_stops: dict[str, threading.Event] = {}
        self._request_deadlines: dict[str, str] = {}

    @property
    def used(self) -> int:
        return self._used

    def reserve(self, generation_run_id: str) -> ModelCallReservation:
        try:
            row = self.repository.reserve_model_call(
                self.job_id,
                generation_run_id=generation_run_id,
                max_calls=self.max_calls,
                protocol_version=PARSER_TOOL_PROTOCOL_VERSION,
            )
        except ValueError as exc:
            if "budget exhausted" in str(exc).casefold():
                raise ModelCallBudgetExhaustedError(str(exc)) from exc
            raise
        self._used = int(row.get("run_call_number") or row["call_number"])
        reservation = ModelCallReservation(
            id=str(row["id"]),
            call_number=self._used,
            generation_run_id=str(row["generation_run_id"]),
        )
        self._request_deadlines[reservation.id] = (
            datetime.now(timezone.utc)
            + timedelta(seconds=self.request_timeout_seconds)
        ).isoformat()
        self._start_heartbeat(reservation)
        return reservation

    def _progress(
        self,
        reservation: ModelCallReservation,
        *,
        heartbeat_only: bool = False,
    ) -> None:
        update = getattr(self.repository, "update_generation_progress", None)
        if not callable(update):
            return
        stage = "model_generating" if reservation.call_number == 1 else "model_revising"
        update(
            self.job_id,
            generation_run_id=reservation.generation_run_id,
            stage=stage,
            attempt_number=reservation.call_number,
            request_deadline_at=self._request_deadlines.get(reservation.id),
            details={
                "model_call_id": reservation.id,
                "model_calls_used": self._used,
                "model_call_limit": self.max_calls,
                "heartbeat_only": heartbeat_only,
            },
        )

    def _start_heartbeat(self, reservation: ModelCallReservation) -> None:
        stop = threading.Event()
        self._heartbeat_stops[reservation.id] = stop
        self._progress(reservation)

        def heartbeat() -> None:
            while not stop.wait(self.heartbeat_seconds):
                try:
                    self._progress(reservation, heartbeat_only=True)
                except Exception:
                    # A heartbeat must never terminate the provider request. The
                    # stale-job reconciler remains the final recovery boundary.
                    continue

        threading.Thread(
            target=heartbeat,
            name=f"parser-model-heartbeat-{reservation.id[:8]}",
            daemon=True,
        ).start()

    def _stop_heartbeat(self, reservation: ModelCallReservation) -> None:
        stop = self._heartbeat_stops.pop(reservation.id, None)
        if stop is not None:
            stop.set()

    def complete(
        self,
        reservation: ModelCallReservation,
        completion: ModelCallCompletion,
    ) -> None:
        self._stop_heartbeat(reservation)
        self.repository.complete_model_call(
            reservation.id,
            status=completion.status,
            model_name=completion.model_name,
            candidate_sha256=completion.candidate_sha256,
            failure_signature=completion.failure_signature,
            result=completion.result,
            token_usage=completion.token_usage,
            latency_ms=completion.latency_ms,
            error=completion.error,
        )
        self._request_deadlines.pop(reservation.id, None)

    def release(self, reservation: ModelCallReservation) -> None:
        self._stop_heartbeat(reservation)
        row = self.repository.release_model_call_budget(reservation.id)
        self._used = int(row["model_calls_used"])
        self._request_deadlines.pop(reservation.id, None)


class RepositoryGenerationObserver:
    """Persist every completed round before another model call can start."""

    def __init__(
        self,
        *,
        job: Mapping[str, Any],
        spec: ParserSpec | ParserSpecDocument,
        sample_set: SampleSet,
        repository: ParserJobRepository,
    ) -> None:
        self.job = dict(job)
        self.spec = spec
        self.sample_set = sample_set
        self.repository = repository
        existing = repository.list_generation_attempts(str(job["id"]))
        self._last_attempt_id = str(existing[-1]["id"]) if existing else None

    def progress(
        self,
        *,
        generation_run_id: str,
        stage: str,
        attempt_number: int,
        deadline_at: str | None,
        details: dict[str, object] | None = None,
    ) -> None:
        self.repository.update_generation_progress(
            str(self.job["id"]),
            generation_run_id=generation_run_id,
            stage=stage,
            attempt_number=attempt_number,
            deadline_at=deadline_at,
            details=details,
        )

    def record_attempt(
        self,
        *,
        generation_run_id: str,
        attempt: GenerationAttempt,
        protocol_version: str | None,
    ) -> str:
        del generation_run_id
        attempt_id = _persist_attempt(
            jobs=self.repository,
            job=self.job,
            sample_set=self.sample_set,
            attempt=attempt,
            parent_attempt_id=self._last_attempt_id,
            protocol_version=protocol_version,
            selected_for_review=_objective_passed(attempt),
        )
        self._last_attempt_id = attempt_id
        return attempt_id


def sample_set_digest(samples: tuple[ParserSample, ...]) -> str:
    payload = "|".join(sorted(sample.sha256 for sample in samples))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def latest_reusable_candidate(
    *,
    job: dict[str, Any],
    sample_set: SampleSet,
    repository: ParserJobRepository,
) -> TemplateCandidate | None:
    """Return a previously valid candidate only when its frozen inputs still match."""

    contract_hash = str(job.get("parser_spec_sha256") or "")
    generation_hash = sample_set_digest(sample_set.generation)
    acceptance_hash = sample_set_digest(sample_set.acceptance)
    rejected_hash_loader = getattr(repository, "list_rejected_candidate_hashes", None)
    rejected_hashes = (
        set(rejected_hash_loader(str(job["id"])))
        if callable(rejected_hash_loader)
        else set()
    )
    for attempt in reversed(
        repository.list_generation_attempts(str(job["id"]), include_template=True)
    ):
        template_text = str(attempt.get("template_text") or "")
        validation = dict(attempt.get("validation_report_json") or {})
        # Automatic NTC selection is retired. Keep historical provenance, but
        # never silently seed a new run from an old automatically selected NTC asset.
        provenance = dict(validation.get("candidate_provenance") or {})
        if (provenance.get("provider") == "ntc_templates"
                or attempt.get("model_provider") == "ntc_templates"
                or attempt.get("action") == "seed:ntc_templates"):
            continue
        generation = dict(validation.get("generation") or {})
        acceptance = validation.get("acceptance")
        if not template_text or generation.get("passed") is not True:
            continue
        if hashlib.sha256(template_text.encode("utf-8")).hexdigest() in rejected_hashes:
            continue
        if isinstance(acceptance, dict) and acceptance.get("passed") is not True:
            continue
        if str(attempt.get("contract_hash") or "") != contract_hash:
            continue
        if str(attempt.get("train_sample_set_hash") or "") != generation_hash:
            continue
        if str(attempt.get("holdout_sample_set_hash") or "") != acceptance_hash:
            continue
        return TemplateCandidate(
            template_text=template_text,
            provider="llm:persisted_revalidation",
            model_name=str(attempt.get("model_name") or "") or None,
            protocol_version=(str(validation.get("generator_protocol_version") or "") or None),
            provenance=(
                dict(validation.get("candidate_provenance") or {})
                if validation.get("candidate_provenance")
                else None
            ),
        )
    return None


def sample_manifest(sample_set: SampleSet) -> list[dict[str, Any]]:
    return [
        {
            "sample_id": sample.sample_id,
            "role": sample.role.value,
            "sha256": sample.sha256,
            "validation_sha256": sample.validation_sha256,
            "expectation": (
                {
                    "record_count": sample.expectation.record_count,
                    "primary_keys": sorted([list(key) for key in sample.expectation.primary_keys]),
                }
                if sample.expectation
                else None
            ),
        }
        for sample in sample_set.samples
    ]


def _passed(attempt: GenerationAttempt) -> bool:
    return bool(
        attempt.generation_validation.passed
        and (attempt.acceptance_validation is None or attempt.acceptance_validation.passed)
        and (attempt.review_report is None or attempt.review_report.passed)
    )


def _objective_passed(attempt: GenerationAttempt) -> bool:
    return bool(
        attempt.generation_validation.passed
        and (
            attempt.acceptance_validation is None
            or attempt.acceptance_validation.passed
        )
    )


def _manifest_digest(manifest: list[dict[str, Any]]) -> str:
    encoded = json.dumps(
        sorted(manifest, key=lambda item: item["sample_id"]),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _validation_payload(
    *,
    job: dict[str, Any],
    spec: ParserSpec | ParserSpecDocument,
    sample_set: SampleSet,
    outcome: GenerationOutcome,
    best_attempt_id: str,
) -> dict[str, Any]:
    candidate = outcome.candidate
    if candidate is None:
        raise RuntimeError("candidate_ready outcome has no candidate")
    accepted_attempt = next(
        attempt
        for attempt in reversed(outcome.attempts)
        if attempt.candidate == candidate and _objective_passed(attempt)
    )
    evidence: dict[str, dict[str, Any]] = {}
    for report in (
        accepted_attempt.generation_validation,
        accepted_attempt.acceptance_validation,
    ):
        if report is None:
            continue
        for sample in report.samples:
            evidence[sample.sample_id] = {
                "sample_id": sample.sample_id,
                "role": sample.role.value,
                "passed": sample.passed,
                "record_count": sample.record_count,
                "errors": list(sample.errors),
            }
    manifest = sample_manifest(sample_set)
    hard_gates = {
        **accepted_attempt.generation_validation.hard_gates,
        **{
            f"acceptance_{key}": value
            for key, value in (
                accepted_attempt.acceptance_validation.hard_gates
                if accepted_attempt.acceptance_validation
                else {}
            ).items()
        },
        "independent_semantic_review": bool(
            accepted_attempt.review_report and accepted_attempt.review_report.passed
        ),
    }
    validation_mode = (
        "independent_acceptance"
        if accepted_attempt.acceptance_validation is not None
        else "generation_sample_only"
    )
    compiled_parser_spec = normalize_parser_spec_payload(spec.to_dict())
    compiled_parser_spec_sha256 = parser_spec_digest(compiled_parser_spec)
    raw_source_parser_spec = job.get("parser_spec_json")
    if not isinstance(raw_source_parser_spec, Mapping):
        raise RuntimeError("Parser job is missing its frozen source ParserSpec")
    source_parser_spec = normalize_parser_spec_payload(dict(raw_source_parser_spec))
    source_parser_spec_sha256 = parser_spec_digest(source_parser_spec)
    if source_parser_spec_sha256 != str(job.get("parser_spec_sha256") or ""):
        raise RuntimeError("Parser job source ParserSpec digest does not match its frozen payload")
    if isinstance(spec, ParserSpecDocument):
        record_sets = [
            {
                "name": item.record_set_name,
                "entity_type": item.entity,
                "cardinality": item.cardinality,
                "presence": item.presence,
                "primary_keys": list(item.primary_keys),
                "primary_key_defaults": item.primary_key_defaults,
                "required_fields": list(item.required_fields),
                "optional_fields": list(item.optional_fields),
                "validators": item.validators,
                "allow_empty": item.allow_empty,
                "empty_patterns": list(item.empty_patterns),
                "ignore_fields": [],
            }
            for item in spec.record_sets
        ]
        entity_type = "multi_record"
        primary_keys: list[str] = []
        primary_key_defaults: dict[str, str] = {}
        required_fields: list[str] = []
        optional_fields: list[str] = []
        validators: dict[str, Any] = {}
        allow_empty = False
        empty_patterns: list[str] = []
    else:
        record_sets = []
        entity_type = spec.entity
        primary_keys = list(spec.primary_keys)
        primary_key_defaults = spec.primary_key_defaults
        required_fields = list(spec.required_fields)
        optional_fields = list(spec.optional_fields)
        validators = spec.validators
        allow_empty = spec.allow_empty
        empty_patterns = list(spec.empty_patterns)
    ai_review = accepted_attempt.review_report
    human_direct_review_required = not bool(
        ai_review is not None and ai_review.passed
    )
    return {
        "key_contract_version": 2,
        "sample_count": len(sample_set.samples),
        "source_sample_count": len(sample_set.samples),
        "unique_sample_count": len(sample_set.samples),
        "passed_samples": len(sample_set.samples),
        "validation_pass_rate": 1.0,
        "samples": list(evidence.values()),
        "sample_manifest": manifest,
        "sample_manifest_sha256": _manifest_digest(manifest),
        "candidate_sample_evidence_version": 1,
        "entity_type": entity_type,
        "primary_keys": primary_keys,
        "primary_key_defaults": primary_key_defaults,
        "required_fields": required_fields,
        "optional_fields": optional_fields,
        "validators": validators,
        "allow_empty": allow_empty,
        "empty_patterns": empty_patterns,
        "ignore_fields": [],
        "record_sets": record_sets,
        "parser_spec_evidence_version": 2,
        "parser_spec": compiled_parser_spec,
        "parser_spec_sha256": compiled_parser_spec_sha256,
        "source_parser_spec": source_parser_spec,
        "source_parser_spec_sha256": source_parser_spec_sha256,
        "contract_source": (
            "candidate_result_ai_reviewed"
            if not human_direct_review_required
            else "candidate_result_human_review_pending"
        ),
        "contract_confidence": (
            0.95
            if ai_review is not None and ai_review.passed
            else 0.8
            if ai_review is not None
            else 0.7
        ),
        "semantic_plan": dict(
            ((job.get("agent_state_json") or {}).get("parser_spec_suggestion") or {}).get("result")
            or {}
        ),
        "ai_review": (
            ai_review.to_dict()
            if ai_review is not None
            else None
        ),
        "human_direct_review_required": human_direct_review_required,
        "human_direct_review_reason": (
            ai_review.summary
            if ai_review is not None and not ai_review.passed
            else "independent AI semantic review was unavailable"
            if ai_review is None
            else None
        ),
        "template_synthesis_strategy": candidate.provider,
        "candidate_provenance": dict(candidate.provenance or {}),
        "textfsm_dialect": {
            "runtime_version": installed_textfsm_version(),
            "dialect_version": DEFAULT_TEXTFSM_DIALECT.dialect_version,
            "dialect_hash": dialect_hash(DEFAULT_TEXTFSM_DIALECT),
            "prompt_version": DEFAULT_TEXTFSM_DIALECT.prompt_version,
        },
        "contract_approval_status": "pending",
        "simple_parser": {
            "pipeline_version": "simple_v2",
            "generator_protocol_version": outcome.protocol_version,
            "model_call_limit": MAX_PARSER_MODEL_CALLS,
            "provider_retry_limit": MAX_PARSER_PROVIDER_RETRIES,
            "provider_retries": outcome.provider_retries,
            "validation_mode": validation_mode,
            "independent_acceptance": bool(sample_set.acceptance),
            "risk_notice": (
                None
                if sample_set.acceptance
                else "candidate validated with generation samples only"
            ),
            "status": outcome.status,
            "stop_reason": outcome.stop_reason,
            "model_calls": outcome.model_calls,
            "best_attempt_id": best_attempt_id,
            "hard_gates": hard_gates,
        },
        "regression_validation": {
            "passed": True,
            "sample_count": len(sample_set.samples),
            "passed_samples": len(sample_set.samples),
            "source_sample_count": len(sample_set.samples),
            "unique_sample_count": len(sample_set.samples),
            "historical_sample_count": 0,
            "minimum_pass_rate": 1.0,
        },
        "compatibility": {
            "manifest_version": 2,
            "vendor": job["vendor"],
            "profile": job["profile"],
            "scope": job.get("compatibility_scope") or "model",
            "model": job.get("model"),
            "family": job.get("family"),
            "canonical_command": job["canonical_command"],
            "structure_variant_count": int(job.get("structure_variant_count") or 0),
            "structure_signatures": list(job.get("structure_signatures") or []),
            "all_variants_required": True,
        },
    }


def _persist_attempt(
    *,
    jobs: ParserJobRepository,
    job: Mapping[str, Any],
    sample_set: SampleSet,
    attempt: GenerationAttempt,
    parent_attempt_id: str | None,
    protocol_version: str | None,
    selected_for_review: bool,
) -> str:
    objective_passed = _objective_passed(attempt)
    fully_passed = _passed(attempt)
    row = jobs.create_generation_attempt(
        job_id=str(job["id"]),
        parent_attempt_id=parent_attempt_id,
        stage="template",
        action=attempt.stage,
        status=(
            "passed"
            if fully_passed
            else "needs_review"
            if selected_for_review and objective_passed
            else "failed"
        ),
        textfsm_runtime_version=installed_textfsm_version(),
        dialect_version=DEFAULT_TEXTFSM_DIALECT.dialect_version,
        dialect_hash=dialect_hash(DEFAULT_TEXTFSM_DIALECT),
        prompt_version=DEFAULT_TEXTFSM_DIALECT.prompt_version,
        validation_report={
            "pipeline_version": "simple_v2",
            "generator_protocol_version": protocol_version,
            "candidate_provenance": dict(attempt.candidate.provenance or {}),
            "generation": attempt.generation_validation.to_dict(),
            "acceptance": (
                attempt.acceptance_validation.to_dict()
                if attempt.acceptance_validation
                else None
            ),
            "ai_review": (
                attempt.review_report.to_dict() if attempt.review_report else None
            ),
        },
        model_provider=(
            attempt.candidate.provider
            if attempt.stage.startswith("llm_")
            else "platform"
        ),
        model_name=attempt.candidate.model_name,
        token_usage=attempt.candidate.token_usage,
        latency_ms=attempt.candidate.latency_ms,
        contract_hash=str(job.get("parser_spec_sha256") or ""),
        train_sample_set_hash=sample_set_digest(sample_set.generation),
        holdout_sample_set_hash=sample_set_digest(sample_set.acceptance),
        template_text=attempt.candidate.template_text,
        failure_signature=(
            None
            if fully_passed
            else str(
                (
                    attempt.review_report.diagnostics[0]
                    if attempt.review_report and attempt.review_report.diagnostics
                    else {}
                ).get("code")
                or attempt.generation_validation.failure_signature
                or "human_review_required"
            )
        ),
        candidate_rank=(
            1.0
            if fully_passed
            else 0.9
            if selected_for_review and objective_passed
            else 0.5
            if attempt.generation_validation.passed
            else 0.0
        ),
    )
    return str(row["id"])


def persist_generation(
    *,
    job: dict[str, Any],
    spec: ParserSpec | ParserSpecDocument,
    sample_set: SampleSet,
    outcome: GenerationOutcome,
    publish: bool,
    dry_run: bool,
    job_repository: ParserJobRepository | None = None,
    template_repository: ParserTemplateRepository | None = None,
) -> PersistedGeneration:
    if job_repository is None or template_repository is None:
        from src.apps.rule_governance.backend.parser.repository import (
            ParserJobRepository,
            ParserTemplateRepository,
        )

    jobs = job_repository or ParserJobRepository()
    load_current_job = getattr(jobs, "get_job", None)
    if callable(load_current_job):
        current_job = load_current_job(str(job["id"]))
        if current_job is None:
            raise RuntimeError("Parser job disappeared before generation was persisted")
        if isinstance(current_job, Mapping):
            from src.apps.rule_governance.backend.access import authorize
            identity = dict((current_job.get("agent_state_json") or {}).get("execution_identity") or {})
            authorize(identity.get("user_id"))
            if current_job.get("celery_task_id") != job.get("celery_task_id"):
                raise RuntimeError("Parser task was replaced or paused; discarding the stale generation result")
            readiness = dict(current_job.get("generation_readiness") or {})
            if "generation_readiness" in current_job and not readiness.get("ready"):
                raise RuntimeError(
                    "Parser source evidence changed while TextFSM generation was running: "
                    + ", ".join(
                        str(item)
                        for item in readiness.get("blockers") or ["readiness_unknown"]
                    )
                )
            if str(current_job.get("parser_spec_sha256") or "") != str(
                job.get("parser_spec_sha256") or ""
            ):
                raise RuntimeError(
                    "ParserSpec changed while TextFSM generation was running; "
                    "discarding the stale result"
                )
            if str(current_job.get("status") or "") not in {"running", "validating"}:
                raise RuntimeError(
                    "Parser job is no longer active; discarding the stale generation result"
                )
    attempts: list[str] = []
    best_attempt_id: str | None = None
    for attempt in outcome.attempts:
        objective_passed = _objective_passed(attempt)
        selected_for_review = bool(
            objective_passed and outcome.candidate == attempt.candidate
        )
        attempt_id = attempt.persisted_id or _persist_attempt(
            jobs=jobs,
            job=job,
            sample_set=sample_set,
            attempt=attempt,
            parent_attempt_id=attempts[-1] if attempts else None,
            protocol_version=outcome.protocol_version,
            selected_for_review=selected_for_review,
        )
        attempts.append(attempt_id)
        if selected_for_review:
            best_attempt_id = attempt_id

    if not (outcome.passed and outcome.candidate and publish and not dry_run):
        return PersistedGeneration(tuple(attempts), best_attempt_id, False, None, None)
    if not best_attempt_id:
        raise RuntimeError("Simple Parser passed without a durable best attempt")

    version = f"simple-{best_attempt_id}"
    digest = outcome.candidate.sha256
    asset_uri = f"postgresql://parser-template/{digest}"
    validation = _validation_payload(
        job=job,
        spec=spec,
        sample_set=sample_set,
        outcome=outcome,
        best_attempt_id=best_attempt_id,
    )
    category = spec.entity if isinstance(spec, ParserSpec) else "multi_record"
    (template_repository or ParserTemplateRepository()).register_template(
        vendor=str(job["vendor"]),
        model=job.get("model"),
        family=job.get("family"),
        profile=str(job["profile"]),
        command=str(job["canonical_command"]),
        category=category,
        version=version,
        asset_path=asset_uri,
        sha256=digest,
        template_text=outcome.candidate.template_text,
        validation=validation,
        source_job_id=str(job["id"]),
        status="pending_approval",
        candidate_sample_manifest=list(validation["sample_manifest"]),
        **({"expected_celery_task_id": job["celery_task_id"]} if job.get("celery_task_id") else {}),
    )
    return PersistedGeneration(tuple(attempts), best_attempt_id, True, asset_uri, validation)


def outcome_records(
    outcome: GenerationOutcome,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    samples: list[dict[str, Any]] = []
    if not (outcome.passed and outcome.candidate):
        return records, samples
    accepted_attempt = next(
        attempt
        for attempt in reversed(outcome.attempts)
        if attempt.candidate == outcome.candidate
        and _objective_passed(attempt)
    )
    for report in (
        accepted_attempt.generation_validation,
        accepted_attempt.acceptance_validation,
    ):
        if report is None:
            continue
        for sample in report.samples:
            if sample.role is not SampleRole.NEGATIVE:
                records.extend(sample.records)
            samples.append(
                {
                    "sample_id": sample.sample_id,
                    "role": sample.role.value,
                    "passed": sample.passed,
                    "record_count": sample.record_count,
                    "errors": list(sample.errors),
                }
            )
    return records, samples
