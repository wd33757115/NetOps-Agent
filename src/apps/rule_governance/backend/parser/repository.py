"""PostgreSQL repository for parser control-plane state."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from sqlalchemy import and_, func, or_, text

from src.apps.rule_governance.backend.identity import actor_user_id as normalize_actor_user_id

from src.apps.rule_governance.backend.parser.outbox_repository import add_outbox_row
from src.apps.rule_governance.backend.parser.check_item_binding import (
    CheckItemBindingError,
    require_parser_check_item,
)
from src.apps.rule_governance.backend.parser.output_classifier import (
    classify_parser_generation_sample,
)
from src.apps.rule_governance.backend.parser.sanitization import (
    SANITIZATION_VERSION,
    sanitize_parser_sample,
)
from src.apps.rule_governance.backend.parser.textfsm_dialect import (
    DEFAULT_TEXTFSM_DIALECT,
    installed_textfsm_version,
)
from src.apps.rule_governance.backend.parser.textfsm_dialect import (
    dialect_hash as current_dialect_hash,
)
from src.apps.rule_governance.backend.parser.time_utils import database_clocks, elapsed_seconds
from src.apps.rule_governance.backend.parser_generation import (
    ParserSpecDocument,
    normalize_parser_spec_payload,
    parser_spec_digest,
)
from src.apps.rule_governance.backend.rule_control.source_scope import (
    RuleAssetSourceScopeError,
    require_immutable_rule_asset_source_datasets,
)
from src.common.config import get_settings
from src.core.patrol.repository import PatrolRepository
from src.infrastructure.db.models import (
    AuditLogRecord,
    CommandSnapshot,
    ParserBackfillJobRecord,
    ParserCandidateSampleEvidence,
    ParserCommandAlias,
    ParserControlSetting,
    ParserDeviceProfile,
    ParserGenerationAttempt,
    ParserIdentitySignature,
    ParserJobSample,
    ParserModelCallLedger,
    ParserOutboxEvent,
    ParserSpecRevisionRecord,
    ParserTemplate,
    ParserTemplateJob,
    PatrolCollectionResult,
    PatrolRun,
    RuleGenerationJobRecord,
    SnapshotParseResult,
)
from src.infrastructure.db.postgres import get_db_session

OPEN_JOB_STATUSES = {"pending", "collecting", "running", "validating"}
TERMINAL_JOB_STATUSES = {"succeeded", "failed", "cancelled", "superseded"}
GENERATION_PROGRESS_STAGES = {
    "queued",
    "context_prepared",
    "model_generating",
    "local_compiling",
    "sample_replaying",
    "model_revising",
    "semantic_reviewing",
    "attempt_persisted",
    "candidate_freezing",
    "generation_stopped",
    "succeeded",
    "failed",
    "cancelled",
}
JOB_TRANSITIONS = {
    "collecting": {"pending", "running", "cancelled", "failed"},
    "pending": {"running", "cancelled", "failed"},
    "running": {"validating", "succeeded", "failed", "cancelled"},
    "validating": {"succeeded", "failed", "cancelled"},
    "failed": {"pending", "superseded"},
    "cancelled": {"pending", "superseded"},
    "succeeded": {"superseded"},
    "superseded": set(),
}


class ParserJobStateError(ValueError):
    pass


class ParserTemplateStateError(ValueError):
    """The requested template lifecycle transition conflicts with current state."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _abort_reserved_model_calls(
    db: Any,
    *,
    job_id: str,
    reason: str,
    completed_at: datetime,
) -> int:
    rows = (
        db.query(ParserModelCallLedger)
        .filter(
            ParserModelCallLedger.job_id == job_id,
            ParserModelCallLedger.status == "reserved",
        )
        .with_for_update()
        .all()
    )
    for row in rows:
        result = dict(row.result_json or {})
        result.update(
            {
                "aborted_by_job_terminal": True,
                "terminal_reason": str(reason or "job_terminal"),
            }
        )
        row.status = "provider_failed"
        row.failure_signature = str(reason or "job_terminal")[:64]
        row.result_json = result
        row.error = f"model call closed because Parser Job became terminal: {reason}"[:2000]
        row.completed_at = completed_at
    return len(rows)


def output_signature(raw_text: str) -> str:
    normalized = re.sub(r"\b\d+(?:\.\d+){1,3}\b", "<IP>", raw_text or "")
    normalized = re.sub(r"\b\d+\b", "<N>", normalized)
    normalized = "\n".join(line.strip() for line in normalized.splitlines()[:40])
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]


def parser_job_asset_key(
    *,
    vendor: str,
    profile: str,
    command: str,
    evidence_scope: str | None = None,
) -> str:
    """Return the stable identity of one target TextFSM template asset.

    ``evidence_scope`` is used only while an offline rule-asset import is
    being prepared.  A generation task must replay exactly the samples the
    engineer imported, rather than silently accumulating samples from another
    import that happens to use the same vendor/model/command tuple.
    """

    parts = [
        "parser-template-asset-v2",
        " ".join(str(vendor or "").strip().casefold().split()),
        " ".join(str(profile or "").strip().casefold().split()),
        " ".join(str(command or "").strip().casefold().split()),
    ]
    normalized_scope = " ".join(
        str(evidence_scope or "").strip().casefold().split()
    )
    if normalized_scope:
        parts.append(normalized_scope)
    normalized = "|".join(parts)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def parser_job_asset_identity(*, vendor: str, profile: str, command: str) -> tuple[str, str, str]:
    """Return the normalized fields that identify one governed Parser asset."""

    return tuple(
        " ".join(str(value or "").strip().casefold().split())
        for value in (vendor, profile, command)
    )


def _parser_spec_suggestion_is_stale(state: dict[str, Any]) -> bool:
    """Return True when a queued/running suggestion exceeded the stale timeout.

    Worker crashes can leave the suggestion state stuck in queued/running with
    nothing to write it back; allow engineers to re-queue after the timeout.
    """
    stale_after = timedelta(minutes=max(1, int(get_settings().PARSER_JOB_RUNNING_STALE_MINUTES)))
    reference_raw = state.get("updated_at") or state.get("requested_at")
    try:
        reference = datetime.fromisoformat(str(reference_raw))
    except (TypeError, ValueError):
        return True
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return utcnow() - reference >= stale_after


def _parser_spec_digest(payload: dict[str, Any]) -> str:
    return parser_spec_digest(payload)


def _normalize_parser_spec_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return normalize_parser_spec_payload(payload)


def _parser_spec_primary_key_count(
    normalized_spec: dict[str, Any],
) -> int | None:
    """Return the expectation key width when one record set defines it."""

    record_sets = normalized_spec.get("record_sets")
    if isinstance(record_sets, list):
        if len(record_sets) != 1 or not isinstance(record_sets[0], dict):
            return None
        return len(record_sets[0].get("primary_keys") or ())
    return len(normalized_spec.get("primary_keys") or ())


def _parser_spec_accepts_valid_empty(
    normalized_spec: dict[str, Any],
    sample_text: str,
) -> bool:
    """Return whether every required record set explicitly accepts this empty sample."""

    try:
        document = ParserSpecDocument.from_dict(normalized_spec)
    except ValueError:
        return False
    matched_any = False
    for record_set in document.record_sets:
        patterns = tuple(pattern for pattern in record_set.empty_patterns if pattern)
        try:
            matched = record_set.allow_empty and any(
                re.search(pattern, sample_text) is not None for pattern in patterns
            )
        except re.error:
            return False
        if matched:
            matched_any = True
            continue
        if record_set.presence != "optional":
            return False
    return matched_any


def _safe_source_file_name(metadata: dict[str, Any]) -> str | None:
    source_path = str(metadata.get("source_path") or metadata.get("relative_path") or "").strip()
    if not source_path:
        return None
    windows_name = PureWindowsPath(source_path).name
    posix_name = PurePosixPath(source_path).name
    return windows_name if len(windows_name) <= len(posix_name) else posix_name


def _validated_sample_expectation(
    *,
    role: str,
    expectation: dict[str, Any] | None,
    primary_key_count: int | None,
) -> dict[str, Any] | None:
    if role == "negative":
        if expectation:
            raise ParserJobStateError("negative samples cannot define positive expectations")
        return None
    if role == "unassigned":
        if expectation:
            raise ParserJobStateError("unassigned samples cannot define expectations")
        return None
    if expectation is None:
        return None
    if not isinstance(expectation, dict):
        raise ParserJobStateError("sample expectation must be an object when provided")
    record_count = expectation.get("record_count")
    if isinstance(record_count, bool) or not isinstance(record_count, int) or record_count < 0:
        raise ParserJobStateError("sample expectation record_count must be a non-negative integer")
    raw_keys = expectation.get("primary_keys")
    if not isinstance(raw_keys, list):
        raise ParserJobStateError("sample expectation primary_keys must be a list")
    keys: list[list[str]] = []
    for raw_key in raw_keys:
        if not isinstance(raw_key, list) or not raw_key:
            raise ParserJobStateError("each expected primary key must be a non-empty list")
        key = [str(value).strip() for value in raw_key]
        if any(not value for value in key):
            raise ParserJobStateError("expected primary key values cannot be empty")
        if primary_key_count is not None and len(key) != primary_key_count:
            raise ParserJobStateError(
                "expected primary key width must match ParserSpec primary_keys"
            )
        if key in keys:
            raise ParserJobStateError("expected primary keys must be unique")
        keys.append(key)
    # A record-count baseline is useful on its own.  Explicit primary keys are
    # optional and, when supplied, add a stricter exact-key-set gate.
    if keys and len(keys) != record_count:
        raise ParserJobStateError("sample expectation primary key count must equal record_count")
    return {"record_count": record_count, "primary_keys": keys}


def _simple_parser_readiness(
    job: ParserTemplateJob,
    samples: list[ParserJobSample],
) -> dict[str, Any]:
    blockers: list[str] = []
    normalized_spec = None
    if not job.parser_spec_json:
        blockers.append("parser_spec_missing")
    else:
        try:
            normalized_spec = _normalize_parser_spec_payload(dict(job.parser_spec_json))
        except ValueError as exc:
            blockers.append(f"parser_spec_invalid:{exc}")
    if job.parser_spec_status != "confirmed":
        blockers.append("parser_spec_not_confirmed")
    if normalized_spec and job.parser_spec_sha256 != _parser_spec_digest(normalized_spec):
        blockers.append("parser_spec_digest_mismatch")
    if not samples:
        blockers.append("samples_missing")
    if any(
        sample.sanitization_status != "sanitized" or not sample.sanitized_text for sample in samples
    ):
        blockers.append("samples_not_sanitized")
    roles = {str(sample.sample_role or "unassigned") for sample in samples}
    if "unassigned" in roles:
        blockers.append("sample_roles_unassigned")
    if "generation" not in roles:
        blockers.append("generation_sample_missing")
    generation_samples = [
        sample
        for sample in samples
        if str(sample.sample_role or "unassigned") == "generation"
    ]
    generation_classifications = [
        classify_parser_generation_sample(str(sample.sanitized_text or ""))
        for sample in generation_samples
    ]
    governed_valid_empty = bool(normalized_spec and generation_samples) and all(
        _parser_spec_accepts_valid_empty(
            normalized_spec,
            str(sample.sanitized_text or ""),
        )
        for sample in generation_samples
    )
    if generation_classifications and not any(
        item["status"] == "data" for item in generation_classifications
    ) and not governed_valid_empty:
        blockers.append("generation_samples_without_data")
    if (
        any(
            item["status"] == "header_only_unverified"
            for item in generation_classifications
        )
        and not governed_valid_empty
    ):
        blockers.append("header_only_samples_unverified")
    has_acceptance = "acceptance" in roles
    if normalized_spec:
        primary_key_count = _parser_spec_primary_key_count(normalized_spec)
        try:
            for sample in samples:
                _validated_sample_expectation(
                    role=str(sample.sample_role or "unassigned"),
                    expectation=sample.expectation_json,
                    primary_key_count=primary_key_count,
                )
        except ParserJobStateError as exc:
            blockers.append(f"sample_expectation_invalid:{exc}")
    return {
        "ready": not blockers,
        "blockers": list(dict.fromkeys(blockers)),
        "warnings": [
            *([] if has_acceptance else ["independent_acceptance_missing"]),
            *(["generation_samples_are_governed_valid_empty"] if governed_valid_empty else []),
        ],
        "validation_mode": (
            "independent_acceptance" if has_acceptance else "generation_sample_only"
        ),
        "role_counts": {
            role: sum(1 for sample in samples if sample.sample_role == role)
            for role in ("generation", "acceptance", "negative", "unassigned")
        },
    }


def _default_sample_role_from_content(text: str | None) -> str:
    """Choose a conservative role for already-sanitized evidence.

    Definite data and definite negative output can enter generation without an
    extra click. Blank or header-only evidence remains unassigned for an
    engineer because the platform cannot infer applicability safely.
    """

    role = str(
        classify_parser_generation_sample(str(text or "")).get("recommended_role")
        or "unassigned"
    ).strip().lower()
    return role if role in {"generation", "negative"} else "unassigned"


def _parser_generation_readiness(
    db: Any,
    job: ParserTemplateJob,
    samples: list[ParserJobSample],
) -> dict[str, Any]:
    """Combine ParserSpec/sample readiness with immutable source provenance."""

    readiness = _simple_parser_readiness(job, samples)
    blockers = list(readiness["blockers"])
    try:
        binding = require_parser_check_item(db, job.id)
        readiness["check_item_binding"] = binding
        if (job.agent_state_json or {}).get("parser_spec_check_item") != binding:
            blockers.append("检查项语义与当前 ParserSpec 不一致，请重新生成或确认字段方案")
    except CheckItemBindingError as exc:
        blockers.append(str(exc))
    if samples:
        try:
            source_scope = require_immutable_rule_asset_source_datasets(
                db,
                [str(sample.dataset_id or "") for sample in samples],
                snapshot_ids=[str(sample.snapshot_id) for sample in samples],
            )
        except RuleAssetSourceScopeError as exc:
            blockers.append(f"rule_asset_source_{exc.code}")
            source_scope = None
        else:
            readiness["source_scope_digest"] = source_scope["scope_digest"]
            readiness["source_job_ids"] = list(source_scope["job_ids"])
            readiness["source_dataset_ids"] = list(source_scope["dataset_ids"])
    readiness["blockers"] = list(dict.fromkeys(blockers))
    readiness["ready"] = not readiness["blockers"]
    return readiness


def _parser_job_retry_projections(db, job_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not job_ids:
        return {}
    statuses: dict[str, list[str]] = {}
    for job_id, status in (
        db.query(ParserTemplate.source_job_id, ParserTemplate.status)
        .filter(ParserTemplate.source_job_id.in_(job_ids))
        .all()
    ):
        statuses.setdefault(str(job_id), []).append(str(status))
    projections: dict[str, dict[str, Any]] = {}
    for job_id in job_ids:
        candidate_statuses = statuses.get(job_id, [])
        blocking_count = sum(
            status not in {"deprecated", "rejected"} for status in candidate_statuses
        )
        projections[job_id] = {
            "generated_candidate_count": len(candidate_statuses),
            "blocking_candidate_count": blocking_count,
        }
    return projections


def _with_parser_job_retryability(
    payload: dict[str, Any],
    projection: dict[str, Any] | None,
) -> dict[str, Any]:
    candidate_count = int((projection or {}).get("generated_candidate_count") or 0)
    blocking_count = int((projection or {}).get("blocking_candidate_count") or 0)
    status = str(payload.get("status") or "")
    return {
        **payload,
        "generated_candidate_count": candidate_count,
        "blocking_candidate_count": blocking_count,
        "can_retry": status in {"failed", "cancelled"}
        or (status == "succeeded" and candidate_count > 0 and blocking_count == 0),
    }


def contract_digest(validation: dict[str, Any]) -> str:
    contract = {
        key: validation.get(key)
        for key in (
            "entity_type",
            "primary_keys",
            "required_fields",
            "optional_fields",
            "validators",
            "allow_empty",
            "empty_patterns",
            "ignore_fields",
            "state_semantics",
            "record_sets",
        )
    }
    encoded = json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


PARSER_AGENT_HARD_GATES = {
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
SIMPLE_PARSER_BASE_GATES = {
    "compiled",
    "executed",
    "source_executed",
    "source_shape_compatible",
}
SIMPLE_PARSER_HARD_GATES = SIMPLE_PARSER_BASE_GATES | {
    f"acceptance_{key}" for key in SIMPLE_PARSER_BASE_GATES
}
CANDIDATE_SAMPLE_EVIDENCE_VERSION = 1


def parser_human_direct_review_required(validation: dict[str, Any]) -> bool:
    """Return whether release needs an explicit human override of AI review."""

    simple = validation.get("simple_parser")
    return bool(
        isinstance(simple, dict)
        and validation.get("human_direct_review_required") is True
    )


def _is_generated_parser_candidate(validation: dict[str, Any]) -> bool:
    return isinstance(validation.get("simple_parser"), dict) or isinstance(
        validation.get("parser_agent"),
        dict,
    )


def _stable_json_digest(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _normalized_manifest_expectation(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("Parser candidate sample expectation must be an object")
    record_count = value.get("record_count")
    primary_keys = value.get("primary_keys") or []
    return {
        "record_count": record_count,
        "primary_keys": sorted(
            [list(map(str, item)) for item in primary_keys],
            key=lambda item: tuple(item),
        ),
    }


def _normalized_candidate_manifest_item(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Parser candidate sample manifest item must be an object")
    sample_id = str(value.get("sample_id") or "").strip()
    role = str(value.get("role") or "").strip().lower()
    raw_sha256 = str(value.get("sha256") or "").strip().lower()
    validation_sha256 = str(value.get("validation_sha256") or "").strip().lower()
    if not sample_id:
        raise ValueError("Parser candidate sample manifest item has no sample_id")
    if role not in {"generation", "acceptance", "negative"}:
        raise ValueError(f"Parser candidate sample {sample_id} has an invalid role")
    for label, digest in (
        ("sha256", raw_sha256),
        ("validation_sha256", validation_sha256),
    ):
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(
                f"Parser candidate sample {sample_id} has an invalid {label}"
            )
    return {
        "sample_id": sample_id,
        "role": role,
        "sha256": raw_sha256,
        "validation_sha256": validation_sha256,
        "expectation": _normalized_manifest_expectation(value.get("expectation")),
    }


def _candidate_sample_evidence_errors(
    db: Any,
    template: ParserTemplate,
    validation: dict[str, Any] | None = None,
) -> list[str]:
    """Verify that a generated candidate still owns its exact source evidence."""

    current = dict(validation if validation is not None else template.validation_json or {})
    if not _is_generated_parser_candidate(current) or not template.source_job_id:
        return []
    errors: list[str] = []
    if db.get(ParserTemplateJob, template.source_job_id) is None:
        errors.append("Parser source Job is missing")
    if current.get("candidate_sample_evidence_version") != CANDIDATE_SAMPLE_EVIDENCE_VERSION:
        errors.append("immutable candidate sample evidence version is missing or invalid")
    raw_manifest = current.get("sample_manifest")
    if not isinstance(raw_manifest, list) or not raw_manifest:
        return [*errors, "immutable candidate sample manifest is missing"]
    try:
        manifest = [_normalized_candidate_manifest_item(item) for item in raw_manifest]
    except ValueError as exc:
        return [*errors, str(exc)]
    expected_by_id = {item["sample_id"]: item for item in manifest}
    if len(expected_by_id) != len(manifest):
        errors.append("immutable candidate sample manifest contains duplicate sample IDs")

    links = (
        db.query(ParserCandidateSampleEvidence)
        .filter(ParserCandidateSampleEvidence.template_id == template.id)
        .order_by(ParserCandidateSampleEvidence.sample_id)
        .all()
    )
    links_by_id = {str(link.sample_id): link for link in links}
    missing_ids = sorted(set(expected_by_id) - set(links_by_id))
    extra_ids = sorted(set(links_by_id) - set(expected_by_id))
    if missing_ids:
        errors.append("candidate source evidence links are missing: " + ", ".join(missing_ids))
    if extra_ids:
        errors.append("candidate source evidence links are unexpected: " + ", ".join(extra_ids))

    current_sample_ids = {
        str(sample_id)
        for (sample_id,) in db.query(ParserJobSample.id)
        .filter(
            ParserJobSample.job_id == template.source_job_id,
            ParserJobSample.lifecycle_status == "current",
        )
        .all()
    }
    expected_ids = set(expected_by_id)
    if current_sample_ids != expected_ids:
        errors.append("Parser source sample set changed after candidate generation")

    for sample_id in sorted(expected_ids.intersection(links_by_id)):
        expected = expected_by_id[sample_id]
        link = links_by_id[sample_id]
        if _stable_json_digest(link.manifest_item_json) != str(
            link.manifest_item_sha256 or ""
        ):
            errors.append(f"candidate evidence manifest digest changed: {sample_id}")
            continue
        try:
            linked_manifest = _normalized_candidate_manifest_item(
                link.manifest_item_json
            )
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if linked_manifest != expected:
            errors.append(f"candidate evidence manifest does not match template: {sample_id}")
        sample = db.get(ParserJobSample, sample_id)
        if sample is None:
            errors.append(f"candidate source sample is missing: {sample_id}")
            continue
        if str(sample.job_id) != str(template.source_job_id):
            errors.append(f"candidate source sample belongs to another Job: {sample_id}")
        if sample.sanitization_status != "sanitized" or not sample.sanitized_text:
            errors.append(f"candidate source sample is not sanitized: {sample_id}")
        else:
            sanitized_sha256 = hashlib.sha256(
                str(sample.sanitized_text).encode("utf-8")
            ).hexdigest()
            if sanitized_sha256 != expected["validation_sha256"] or str(
                sample.sanitized_hash or ""
            ).lower() != expected["validation_sha256"]:
                errors.append(f"candidate sanitized sample changed: {sample_id}")
        if str(sample.sample_role or "").lower() != expected["role"]:
            errors.append(f"candidate sample role changed: {sample_id}")
        try:
            current_expectation = _normalized_manifest_expectation(
                sample.expectation_json
            )
        except ValueError as exc:
            errors.append(str(exc))
        else:
            if current_expectation != expected["expectation"]:
                errors.append(f"candidate sample expectation changed: {sample_id}")
        snapshot = db.get(CommandSnapshot, sample.snapshot_id)
        if snapshot is None:
            errors.append(f"candidate source Snapshot is missing: {sample_id}")
        else:
            raw_sha256 = str(snapshot.raw_text_hash or "").lower() or hashlib.sha256(
                str(snapshot.raw_text or "").encode("utf-8")
            ).hexdigest()
            if raw_sha256 != expected["sha256"]:
                errors.append(f"candidate source Snapshot changed: {sample_id}")
    return list(dict.fromkeys(errors))


def parser_semantic_review_evidence(
    template: ParserTemplate,
    validation: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str]:
    """Freeze the objective Parser result that an engineer reviewed."""

    current = dict(validation if validation is not None else template.validation_json or {})
    regression = dict(current.get("regression_validation") or {})
    preview = dict(current.get("approval_result_preview") or {})
    samples = preview.get("samples")
    result_samples = samples if isinstance(samples, list) else []
    evidence = {
        "schema_version": "parser-semantic-review-evidence/v1",
        "template_id": str(template.id),
        "template_sha256": str(template.sha256 or ""),
        "contract_sha256": str(template.contract_sha256 or ""),
        "parser_spec_sha256": str(
            current.get("source_parser_spec_sha256")
            or current.get("parser_spec_sha256")
            or ""
        ),
        "sample_manifest_sha256": str(current.get("sample_manifest_sha256") or ""),
        "regression": {
            "passed": regression.get("passed") is True,
            "sample_count": int(regression.get("sample_count") or 0),
            "passed_samples": int(regression.get("passed_samples") or 0),
        },
        "result_sample_count": len(result_samples),
        "result_digest": _stable_json_digest(result_samples),
    }
    return evidence, _stable_json_digest(evidence)


def parser_semantic_review_activation_errors(
    template: ParserTemplate,
    validation: dict[str, Any] | None = None,
) -> list[str]:
    """Return release blockers for the separately persisted human review."""

    current = dict(validation if validation is not None else template.validation_json or {})
    if not _is_generated_parser_candidate(current):
        return []
    review = current.get("human_semantic_review")
    if not isinstance(review, dict) or review.get("status") != "accepted":
        return ["human semantic review has not been accepted"]
    errors: list[str] = []
    if review.get("result_confirmed") is not True:
        errors.append("human semantic review did not confirm parsed results")
    if review.get("primary_key_confirmed") is not True:
        errors.append("human semantic review did not confirm primary keys")
    if not str(review.get("reviewed_by") or "").strip():
        errors.append("human semantic review actor is missing")
    _evidence, expected_digest = parser_semantic_review_evidence(template, current)
    if str(review.get("evidence_digest") or "") != expected_digest:
        errors.append("human semantic review evidence is stale")
    return errors


def generated_template_activation_errors(
    validation: dict[str, Any],
    *,
    source_job_id: str | None,
) -> list[str]:
    """Return hard-gate failures for an Agent-generated Parser candidate."""

    agent = validation.get("parser_agent")
    simple = validation.get("simple_parser")
    if not isinstance(agent, dict) and not isinstance(simple, dict):
        return []
    errors: list[str] = []
    if isinstance(simple, dict):
        if simple.get("pipeline_version") != "simple_v2":
            errors.append("Simple Parser pipeline version is invalid")
        if simple.get("status") != "candidate_ready":
            errors.append("Simple Parser did not finish with status=candidate_ready")
        model_calls = simple.get("model_calls")
        if (
            isinstance(model_calls, bool)
            or not isinstance(model_calls, int)
            or not 0 <= model_calls <= 3
        ):
            errors.append("Simple Parser model-call budget evidence is invalid")
        gates = simple.get("hard_gates")
        if not isinstance(gates, dict):
            errors.append("Simple Parser hard gates are missing")
        else:
            validation_mode = simple.get("validation_mode")
            if validation_mode is None:
                # Candidates produced before validation modes were introduced used
                # the strict independent-acceptance gate set.
                required_gates = SIMPLE_PARSER_HARD_GATES
            elif validation_mode == "independent_acceptance":
                required_gates = SIMPLE_PARSER_HARD_GATES
            elif validation_mode == "generation_sample_only":
                required_gates = SIMPLE_PARSER_BASE_GATES
            else:
                required_gates = SIMPLE_PARSER_HARD_GATES
                errors.append("Simple Parser validation mode is invalid")
            if validation.get("record_sets"):
                required_gates = set(required_gates)
                required_gates.add("bundle_complete")
                if validation_mode == "independent_acceptance":
                    required_gates.add("acceptance_bundle_complete")
            missing = sorted(required_gates - set(gates))
            failed = sorted(
                key for key in required_gates.intersection(gates) if gates.get(key) is not True
            )
            if missing:
                errors.append(f"Simple Parser hard gates are incomplete: {', '.join(missing)}")
            if failed:
                errors.append(f"Simple Parser hard gates failed: {', '.join(failed)}")
        if source_job_id and not simple.get("best_attempt_id"):
            errors.append("Simple Parser best attempt is missing")
        contract_source = validation.get("contract_source")
        if contract_source in {
            "llm_generated_result_reviewed",
            "candidate_result_ai_reviewed",
        }:
            review = validation.get("ai_review")
            if not isinstance(review, dict) or review.get("passed") is not True:
                errors.append("independent semantic review did not pass")
        elif contract_source == "candidate_result_human_review_pending":
            if validation.get("human_direct_review_required") is not True:
                errors.append("human direct-review governance evidence is invalid")
        parser_spec = validation.get("parser_spec")
        parser_spec_sha256 = str(validation.get("parser_spec_sha256") or "")
        if not isinstance(parser_spec, dict) or not parser_spec_sha256:
            errors.append("Simple Parser immutable ParserSpec evidence is missing")
        else:
            try:
                normalized_spec = _normalize_parser_spec_payload(parser_spec)
            except ValueError as exc:
                errors.append(f"Simple Parser stored ParserSpec is invalid: {exc}")
            else:
                if _parser_spec_digest(normalized_spec) != parser_spec_sha256:
                    errors.append("Simple Parser ParserSpec digest does not match")
        evidence_version = validation.get("parser_spec_evidence_version")
        if evidence_version is not None:
            if evidence_version != 2:
                errors.append("Simple Parser ParserSpec evidence version is invalid")
            source_parser_spec = validation.get("source_parser_spec")
            source_parser_spec_sha256 = str(validation.get("source_parser_spec_sha256") or "")
            if not isinstance(source_parser_spec, dict) or not source_parser_spec_sha256:
                errors.append("Simple Parser source ParserSpec evidence is missing")
            else:
                try:
                    normalized_source_spec = _normalize_parser_spec_payload(source_parser_spec)
                except ValueError as exc:
                    errors.append(f"Simple Parser stored source ParserSpec is invalid: {exc}")
                else:
                    if _parser_spec_digest(normalized_source_spec) != source_parser_spec_sha256:
                        errors.append("Simple Parser source ParserSpec digest does not match")
        manifest = validation.get("sample_manifest")
        manifest_sha256 = str(validation.get("sample_manifest_sha256") or "")
        if (
            source_job_id
            and validation.get("candidate_sample_evidence_version")
            != CANDIDATE_SAMPLE_EVIDENCE_VERSION
        ):
            errors.append("immutable candidate sample evidence is missing")
        if not isinstance(manifest, list) or not manifest or not manifest_sha256:
            errors.append("Simple Parser immutable sample manifest is missing")
        else:
            encoded_manifest = json.dumps(
                sorted(manifest, key=lambda item: str(item.get("sample_id") or "")),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if hashlib.sha256(encoded_manifest.encode("utf-8")).hexdigest() != manifest_sha256:
                errors.append("Simple Parser sample manifest digest does not match")
    else:
        if agent.get("status") != "passed":
            errors.append("parser agent did not finish with status=passed")
        gates = agent.get("hard_gates")
        if not isinstance(gates, dict):
            errors.append("parser agent hard gates are missing")
        else:
            missing = sorted(PARSER_AGENT_HARD_GATES - set(gates))
            failed = sorted(
                key
                for key in PARSER_AGENT_HARD_GATES.intersection(gates)
                if gates.get(key) is not True
            )
            if missing:
                errors.append(f"parser agent hard gates are incomplete: {', '.join(missing)}")
            if failed:
                errors.append(f"parser agent hard gates failed: {', '.join(failed)}")
        if source_job_id and not agent.get("best_attempt_id"):
            errors.append("parser agent best attempt is missing")

    dialect = validation.get("textfsm_dialect")
    if not isinstance(dialect, dict):
        errors.append("TextFSM dialect evidence is missing")
        return errors
    expected = DEFAULT_TEXTFSM_DIALECT
    actual_runtime = installed_textfsm_version()
    expected_hash = current_dialect_hash(expected)
    if dialect.get("runtime_version") != actual_runtime:
        errors.append("TextFSM runtime version changed after candidate validation")
    if dialect.get("dialect_version") != expected.dialect_version:
        errors.append("TextFSM dialect version changed after candidate validation")
    if dialect.get("dialect_hash") != expected_hash:
        errors.append("TextFSM dialect hash changed after candidate validation")
    if dialect.get("prompt_version") != expected.prompt_version:
        errors.append("TextFSM prompt version changed after candidate validation")
    return errors




def _invalidate_parser_candidates_for_sample_revision(
    db: Any,
    job: ParserTemplateJob,
    *,
    actor: str,
    reason: str,
    now: datetime,
) -> list[str]:
    """Reject unpublished candidates whose frozen source sample set changed."""

    invalidated_template_ids: list[str] = []
    rows = (
        db.query(ParserTemplate)
        .filter(
            ParserTemplate.source_job_id == job.id,
            ParserTemplate.status.in_(("draft", "validating", "pending_approval")),
        )
        .with_for_update()
        .all()
    )
    for template in rows:
        validation = dict(template.validation_json or {})
        if not _is_generated_parser_candidate(validation):
            continue
        validation["candidate_sample_evidence"] = {
            "status": "invalidated",
            "code": "source_sample_set_revised",
            "reason": reason,
            "invalidated_at": now.isoformat(),
            "invalidated_by": actor,
        }
        validation["status_audit"] = [
            *list(validation.get("status_audit") or []),
            {
                "actor": actor,
                "reason": reason,
                "from_status": template.status,
                "to_status": "rejected",
                "at": now.isoformat(),
            },
        ]
        template.status = "rejected"
        template.validation_json = validation
        template.updated_at = now
        invalidated_template_ids.append(str(template.id))
    return invalidated_template_ids


def _invalidate_parser_job_for_spec_revision(
    db: Any,
    job: ParserTemplateJob,
    *,
    parser_spec_sha256: str,
    actor: str,
    reason: str,
    now: datetime,
    preserve_suggestion_task_id: str | None = None,
) -> tuple[str, list[str]]:
    """Invalidate mutable generation output while preserving active runtime assets."""

    previous_status = str(job.status)
    previous_digest = job.parser_spec_sha256
    invalidated_template_ids: list[str] = []
    stale_candidates = (
        db.query(ParserTemplate)
        .filter(
            ParserTemplate.source_job_id == job.id,
            ParserTemplate.status.in_(("draft", "validating", "pending_approval")),
        )
        .with_for_update()
        .all()
    )
    for template in stale_candidates:
        validation = dict(template.validation_json or {})
        validation["status_audit"] = [
            *list(validation.get("status_audit") or []),
            {
                "actor": actor,
                "reason": (
                    "ParserSpec revised; candidate no longer matches the current generation inputs"
                ),
                "from_status": template.status,
                "to_status": "rejected",
                "at": now.isoformat(),
            },
        ]
        template.status = "rejected"
        template.validation_json = validation
        template.updated_at = now
        invalidated_template_ids.append(str(template.id))

    if previous_status in {
        "pending",
        "running",
        "validating",
        "succeeded",
        "superseded",
    }:
        job.status = "failed"
        job.completed_at = now
    if previous_status == "superseded":
        job.superseded_by_job_id = None
        job.superseded_at = None
    job.failure_code = "parser_spec_revised"
    job.last_error = "ParserSpec was revised; regenerate TextFSM with the new specification"
    job.celery_task_id = None
    job.dispatched_at = None
    job.started_at = None
    job.best_attempt_id = None
    job.agent_budget_json = None
    job.model_calls_used = 0
    job.contract_revision_count = 0
    job.template_attempt_count = 0
    current_state = dict(job.agent_state_json or {})
    current_suggestion = dict(current_state.get("parser_spec_suggestion") or {})
    if current_suggestion.get("status") in {"queued", "running"} and str(
        current_suggestion.get("task_id") or ""
    ) != str(preserve_suggestion_task_id or ""):
        current_state["parser_spec_suggestion"] = {
            **current_suggestion,
            "status": "superseded",
            "task_id": f"spec-revision:{uuid.uuid4()}",
            "updated_at": now.isoformat(),
        }
    current_state["parser_spec_revision"] = {
        "actor": actor,
        "reason": reason,
        "previous_sha256": previous_digest,
        "parser_spec_sha256": parser_spec_sha256,
        "previous_job_status": previous_status,
        "invalidated_template_ids": invalidated_template_ids,
        "revised_at": now.isoformat(),
    }
    current_state.pop("stop_reason", None)
    job.agent_state_json = current_state
    return previous_status, invalidated_template_ids


def _append_parser_spec_revision(
    db: Any,
    job: ParserTemplateJob,
    *,
    parser_spec: dict[str, Any],
    parser_spec_sha256: str,
    parser_spec_status: str,
    previous_parser_spec_sha256: str | None,
    source: str,
    actor: str,
    reason: str,
    invalidated_template_ids: list[str],
    now: datetime,
) -> ParserSpecRevisionRecord:
    """Persist one immutable ParserSpec revision while the owning job is locked."""

    revision_number = int(job.parser_spec_revision or 0) + 1
    revision = ParserSpecRevisionRecord(
        id=str(uuid.uuid4()),
        job_id=str(job.id),
        revision_number=revision_number,
        parser_spec_status=parser_spec_status,
        parser_spec_json=parser_spec,
        parser_spec_sha256=parser_spec_sha256,
        previous_parser_spec_sha256=previous_parser_spec_sha256,
        source=source,
        actor=actor,
        reason=reason,
        invalidated_template_ids_json=list(invalidated_template_ids),
        created_at=now,
    )
    db.add(revision)
    job.parser_spec_revision = revision_number
    return revision


class ParserRepository:
    def backfill_contract_metadata(self) -> int:
        count = 0
        with get_db_session() as db:
            rows = db.query(ParserTemplate).filter(ParserTemplate.contract_sha256.is_(None)).all()
            for row in rows:
                validation = dict(row.validation_json or {})
                row.contract_version = int(validation.get("contract_version") or 1)
                row.contract_sha256 = contract_digest(validation)
                validation["contract_version"] = row.contract_version
                validation["contract_sha256"] = row.contract_sha256
                row.validation_json = validation
                row.updated_at = utcnow()
                count += 1
        return count

    def get_auto_dispatch_enabled(self) -> bool:
        with get_db_session() as db:
            row = (
                db.query(ParserControlSetting)
                .filter(ParserControlSetting.key == "auto_dispatch_enabled")
                .first()
            )
            return bool((row.value_json or {}).get("enabled")) if row else False

    def set_auto_dispatch_enabled(
        self,
        enabled: bool,
        *,
        actor: str,
        reason: str,
    ) -> bool:
        if not str(actor or "").strip():
            raise ValueError("actor is required")
        if not str(reason or "").strip():
            raise ValueError("reason is required")
        with get_db_session() as db:
            row = (
                db.query(ParserControlSetting)
                .filter(ParserControlSetting.key == "auto_dispatch_enabled")
                .first()
            )
            previous_enabled = bool((row.value_json or {}).get("enabled")) if row else False
            if row is None:
                row = ParserControlSetting(
                    key="auto_dispatch_enabled",
                    value_json={"enabled": bool(enabled)},
                )
                db.add(row)
            else:
                row.value_json = {"enabled": bool(enabled)}
                row.updated_at = utcnow()
            db.add(
                AuditLogRecord(
                    id=str(uuid.uuid4()),
                    user_id=actor,
                    username=actor,
                    action="parser_auto_dispatch_changed",
                    resource_type="parser_control_setting",
                    resource_id="auto_dispatch_enabled",
                    detail={
                        "reason": str(reason).strip(),
                        "from_enabled": previous_enabled,
                        "to_enabled": bool(enabled),
                    },
                    status="success",
                    created_at=utcnow(),
                )
            )
            return bool(enabled)

    def list_command_aliases(self, *, status: str = "confirmed") -> list[dict[str, Any]]:
        with get_db_session() as db:
            return [
                row.to_dict()
                for row in db.query(ParserCommandAlias)
                .filter(ParserCommandAlias.status == status)
                .order_by(ParserCommandAlias.alias)
                .all()
            ]

    def list_command_aliases_page(
        self,
        *,
        status: str | None = "confirmed",
        search: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        page_size = max(1, min(int(limit), 200))
        page_offset = max(0, int(offset))
        with get_db_session() as db:
            query = db.query(ParserCommandAlias)
            if status:
                query = query.filter(ParserCommandAlias.status == status)
            pattern = f"%{str(search).strip()}%" if str(search or "").strip() else None
            if pattern:
                query = query.filter(
                    or_(
                        ParserCommandAlias.alias.ilike(pattern),
                        ParserCommandAlias.canonical_command.ilike(pattern),
                    )
                )
            total = query.order_by(None).count()
            rows = (
                query.order_by(ParserCommandAlias.alias, ParserCommandAlias.id)
                .offset(page_offset)
                .limit(page_size)
                .all()
            )
            return {
                "items": [row.to_dict() for row in rows],
                "total": total,
                "limit": page_size,
                "offset": page_offset,
            }

    def upsert_command_alias(
        self,
        *,
        alias: str,
        canonical_command: str,
        confidence: float = 1.0,
        evidence: dict[str, Any] | None = None,
        status: str = "confirmed",
    ) -> str:
        with get_db_session() as db:
            row = db.query(ParserCommandAlias).filter(ParserCommandAlias.alias == alias).first()
            if row is None:
                row = ParserCommandAlias(id=str(uuid.uuid4()), alias=alias)
                db.add(row)
            row.canonical_command = canonical_command
            row.confidence = confidence
            row.evidence_json = evidence or {}
            row.status = status
            row.updated_at = utcnow()
            return row.id

    def list_device_profiles(self, *, status: str = "confirmed") -> list[dict[str, Any]]:
        with get_db_session() as db:
            return [
                row.to_dict()
                for row in db.query(ParserDeviceProfile)
                .filter(ParserDeviceProfile.status == status)
                .order_by(ParserDeviceProfile.vendor, ParserDeviceProfile.model)
                .all()
            ]

    def list_device_profiles_page(
        self,
        *,
        status: str | None = "confirmed",
        search: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        page_size = max(1, min(int(limit), 200))
        page_offset = max(0, int(offset))
        with get_db_session() as db:
            query = db.query(ParserDeviceProfile)
            if status:
                query = query.filter(ParserDeviceProfile.status == status)
            pattern = f"%{str(search).strip()}%" if str(search or "").strip() else None
            if pattern:
                query = query.filter(
                    or_(
                        ParserDeviceProfile.vendor.ilike(pattern),
                        ParserDeviceProfile.model.ilike(pattern),
                        ParserDeviceProfile.family.ilike(pattern),
                        ParserDeviceProfile.normalized_model.ilike(pattern),
                    )
                )
            total = query.order_by(None).count()
            rows = (
                query.order_by(
                    ParserDeviceProfile.vendor,
                    ParserDeviceProfile.model,
                    ParserDeviceProfile.id,
                )
                .offset(page_offset)
                .limit(page_size)
                .all()
            )
            return {
                "items": [row.to_dict() for row in rows],
                "total": total,
                "limit": page_size,
                "offset": page_offset,
            }

    def find_device_profile(self, vendor: str, model: str) -> dict[str, Any] | None:
        with get_db_session() as db:
            row = (
                db.query(ParserDeviceProfile)
                .filter(
                    func.lower(ParserDeviceProfile.vendor) == vendor.lower(),
                    func.lower(ParserDeviceProfile.model) == model.lower(),
                    ParserDeviceProfile.status == "confirmed",
                )
                .first()
            )
            return row.to_dict() if row else None

    def upsert_device_profile(
        self,
        *,
        vendor: str,
        model: str,
        family: str | None,
        confidence: float,
        evidence: dict[str, Any] | None = None,
        status: str = "confirmed",
    ) -> str:
        with get_db_session() as db:
            row = (
                db.query(ParserDeviceProfile)
                .filter(
                    ParserDeviceProfile.vendor == vendor,
                    ParserDeviceProfile.model == model,
                )
                .first()
            )
            if row is None:
                row = ParserDeviceProfile(
                    id=str(uuid.uuid4()),
                    vendor=vendor,
                    model=model,
                )
                db.add(row)
            row.normalized_model = model
            row.family = family
            row.confidence = confidence
            row.evidence_json = evidence or {}
            row.status = status
            row.updated_at = utcnow()
            return row.id

    def list_identity_signatures(self, *, status: str = "confirmed") -> list[dict[str, Any]]:
        with get_db_session() as db:
            return [
                row.to_dict()
                for row in db.query(ParserIdentitySignature)
                .filter(ParserIdentitySignature.status == status)
                .order_by(ParserIdentitySignature.vendor, ParserIdentitySignature.model)
                .all()
            ]

    def list_identity_signatures_page(
        self,
        *,
        status: str | None = "confirmed",
        search: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        page_size = max(1, min(int(limit), 200))
        page_offset = max(0, int(offset))
        with get_db_session() as db:
            query = db.query(ParserIdentitySignature)
            if status:
                query = query.filter(ParserIdentitySignature.status == status)
            pattern = f"%{str(search).strip()}%" if str(search or "").strip() else None
            if pattern:
                query = query.filter(
                    or_(
                        ParserIdentitySignature.vendor.ilike(pattern),
                        ParserIdentitySignature.model.ilike(pattern),
                        ParserIdentitySignature.family.ilike(pattern),
                        ParserIdentitySignature.evidence_command.ilike(pattern),
                        ParserIdentitySignature.pattern.ilike(pattern),
                    )
                )
            total = query.order_by(None).count()
            rows = (
                query.order_by(
                    ParserIdentitySignature.vendor,
                    ParserIdentitySignature.model,
                    ParserIdentitySignature.id,
                )
                .offset(page_offset)
                .limit(page_size)
                .all()
            )
            return {
                "items": [row.to_dict() for row in rows],
                "total": total,
                "limit": page_size,
                "offset": page_offset,
            }

    def register_identity_signature(
        self,
        *,
        vendor: str,
        model: str,
        family: str | None,
        evidence_command: str,
        pattern: str,
        confidence: float,
        source: str = "discovered",
        status: str = "confirmed",
    ) -> str:
        with get_db_session() as db:
            row = (
                db.query(ParserIdentitySignature)
                .filter(
                    ParserIdentitySignature.vendor == vendor,
                    ParserIdentitySignature.model == model,
                    ParserIdentitySignature.evidence_command == evidence_command,
                    ParserIdentitySignature.pattern == pattern,
                )
                .first()
            )
            if row is None:
                row = ParserIdentitySignature(
                    id=str(uuid.uuid4()),
                    vendor=vendor,
                    model=model,
                    evidence_command=evidence_command,
                    pattern=pattern,
                )
                db.add(row)
            row.family = family
            row.confidence = confidence
            row.source = source
            row.status = status
            row.updated_at = utcnow()
            return row.id

    def resolve_active(
        self, *, vendor: str, model: str, family: str | None, command: str
    ) -> ParserTemplate | None:
        with get_db_session() as db:
            profiles = [model] + ([family] if family and family != model else [])
            rows = (
                db.query(ParserTemplate)
                .filter(
                    ParserTemplate.vendor == vendor,
                    ParserTemplate.profile.in_(profiles),
                    ParserTemplate.canonical_command == command,
                    ParserTemplate.status == "active",
                )
                .all()
            )
            rows.sort(key=lambda row: profiles.index(row.profile))
            if not rows:
                return None
            template = rows[0]
            db.expunge(template)
            return template

    def create_candidate(
        self,
        *,
        job_type: str,
        vendor: str,
        model: str,
        family: str | None,
        command: str,
        snapshot_id: str,
        device_id: str,
        raw_text: str | None = None,
        profile: str | None = None,
        failure_code: str | None = None,
        dataset_id: str | None = None,
        evidence_scope: str | None = None,
    ) -> str:
        context = PatrolRepository().get_snapshot_context(snapshot_id)
        if context is None:
            raise ValueError(f"parser candidate snapshot does not exist: {snapshot_id}")
        source_mode = str(context["mode"])
        if source_mode not in {"parser_validation", "production"}:
            raise ValueError(
                "parser template candidates require parser_validation or production snapshots"
            )
        snapshot = context["snapshot"]
        if snapshot.device_id != device_id:
            raise ValueError("parser candidate device does not match the snapshot")
        raw_text = snapshot.raw_text
        trusted_sample = (
            sanitize_parser_sample(raw_text) if source_mode == "parser_validation" else None
        )
        dataset_id = context["dataset_id"] or dataset_id
        signature = output_signature(raw_text)
        profile = str(profile or model or family or "Generic")
        allowed_profiles = {str(item) for item in (model, family, "Generic") if item}
        if profile not in allowed_profiles:
            raise ValueError("parser candidate profile must match the device model or family")
        key = parser_job_asset_key(
            vendor=vendor,
            profile=profile,
            command=command,
            evidence_scope=evidence_scope,
        )
        with get_db_session() as db:
            job_created = False
            job_reopened = False
            db.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": key},
            )
            job = (
                db.query(ParserTemplateJob)
                .filter(ParserTemplateJob.idempotency_key == key)
                .order_by(ParserTemplateJob.updated_at.desc(), ParserTemplateJob.id.desc())
                .first()
            )
            if job is not None:
                invalid_samples = [
                    row
                    for row in db.query(ParserJobSample)
                    .filter(
                        ParserJobSample.job_id == job.id,
                        ParserJobSample.lifecycle_status == "current",
                    )
                    .all()
                    if row.snapshot_source != "postgresql"
                    or db.get(CommandSnapshot, row.snapshot_id) is None
                ]
                if invalid_samples:
                    self._delete_job_graph(db, job)
                    db.flush()
                    job = None
            existing_sample = None
            if job is not None:
                existing_sample = (
                    db.query(ParserJobSample)
                    .filter(
                        ParserJobSample.job_id == job.id,
                        ParserJobSample.snapshot_id == snapshot_id,
                        ParserJobSample.lifecycle_status == "current",
                    )
                    .first()
                )
            if job is not None:
                # A job represents the target template asset, not one output shape or
                # failure code or generation cycle. New evidence reopens the same
                # durable aggregate instead of creating another historical task.
                if source_mode == "production" and job.source_mode != "production":
                    job.source_mode = "production"
                if (
                    job_type == "REPAIR_TEMPLATE"
                    and job.job_type != "REPAIR_TEMPLATE"
                    and (job.status in {"collecting", "pending"} or existing_sample is None)
                ):
                    job.job_type = "REPAIR_TEMPLATE"
                if failure_code:
                    job.failure_code = failure_code
                if existing_sample is None and job.status in TERMINAL_JOB_STATUSES:
                    job_reopened = True
                    job.status = "collecting"
                    job.celery_task_id = None
                    job.dispatched_at = None
                    job.started_at = None
                    job.completed_at = None
                    job.superseded_by_job_id = None
                    job.superseded_at = None
                    job.last_error = None
                    job.best_attempt_id = None
                    job.agent_state_json = None
                    job.agent_budget_json = None
                    job.contract_revision_count = 0
                    job.template_attempt_count = 0
                    job.output_signature = signature
                job.updated_at = utcnow()
            if job is None:
                job_created = True
                job = ParserTemplateJob(
                    id=str(uuid.uuid4()),
                    job_type=job_type,
                    source_mode=source_mode,
                    vendor=vendor,
                    model=model,
                    family=family,
                    profile=profile,
                    canonical_command=command,
                    output_signature=signature,
                    idempotency_key=key,
                    status="collecting",
                    pipeline_version="simple_v2",
                    failure_code=failure_code,
                    sample_count=0,
                    created_at=utcnow(),
                    updated_at=utcnow(),
                )
                db.add(job)
                db.flush()
            sample_added = existing_sample is None
            invalidated_template_ids: list[str] = []
            if sample_added and not job_created:
                invalidated_template_ids = _invalidate_parser_candidates_for_sample_revision(
                    db,
                    job,
                    actor="parser-candidate-discovery",
                    reason=(
                        "Parser source sample set changed; regenerate the candidate "
                        "against the complete evidence set"
                    ),
                    now=utcnow(),
                )
            if sample_added:
                default_sample_role = "unassigned"
                if job.parser_spec_status == "confirmed" and trusted_sample is not None:
                    default_sample_role = _default_sample_role_from_content(
                        trusted_sample.text
                    )
                db.add(
                    ParserJobSample(
                        id=str(uuid.uuid4()),
                        job_id=job.id,
                        snapshot_id=snapshot_id,
                        snapshot_source="postgresql",
                        device_id=device_id,
                        output_signature=signature,
                        dataset_id=dataset_id,
                        sample_role=default_sample_role,
                        sanitization_status=(
                            "sanitized"
                            if source_mode == "parser_validation"
                            else "pending_sanitization"
                        ),
                        sanitization_version=(trusted_sample.version if trusted_sample else None),
                        sanitized_text=(trusted_sample.text if trusted_sample else None),
                        sanitized_hash=(trusted_sample.sha256 if trusted_sample else None),
                        redaction_summary_json=(
                            trusted_sample.redaction_summary if trusted_sample else None
                        ),
                        sanitized_at=(utcnow() if source_mode == "parser_validation" else None),
                        created_at=utcnow(),
                    )
                )
                job.sample_count = int(job.sample_count or 0) + 1
                job.updated_at = utcnow()
            if invalidated_template_ids:
                self._add_outbox(
                    db,
                    event_type="parser.candidate.source_changed",
                    aggregate_id=job.id,
                    payload={
                        "job_id": job.id,
                        "snapshot_id": snapshot_id,
                        "invalidated_template_ids": invalidated_template_ids,
                    },
                )
            if job_created or job_reopened:
                self._add_outbox(
                    db,
                    event_type="parser.candidate.discovered",
                    aggregate_id=job.id,
                    payload={
                        "job_id": job.id,
                        "job_type": job.job_type,
                        "snapshot_id": snapshot_id,
                        "reopened": job_reopened,
                    },
                )
            return job.id

    def sanitize_pending_samples(
        self,
        *,
        job_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        with get_db_session() as db:
            query = (
                db.query(ParserJobSample)
                .join(ParserTemplateJob, ParserTemplateJob.id == ParserJobSample.job_id)
                .filter(
                    ParserJobSample.lifecycle_status == "current",
                    or_(
                        ParserJobSample.sanitization_status == "pending_sanitization",
                        ParserJobSample.sanitized_text.is_(None),
                        ParserJobSample.sanitization_version != SANITIZATION_VERSION,
                    )
                )
            )
            if job_id:
                query = query.filter(ParserJobSample.job_id == job_id)
            samples = (
                query.order_by(ParserJobSample.created_at)
                .limit(max(1, int(limit)))
                .with_for_update(skip_locked=True)
                .all()
            )
            for sample in samples:
                snapshot = db.get(CommandSnapshot, sample.snapshot_id)
                if snapshot is None:
                    sample.sanitization_status = "failed"
                    results.append(
                        {
                            "sample_id": sample.id,
                            "success": False,
                            "failure_code": "snapshot_missing",
                        }
                    )
                    continue
                sanitized = sanitize_parser_sample(snapshot.raw_text)
                previous_sanitized_hash = str(sample.sanitized_hash or "")
                if (
                    previous_sanitized_hash
                    and previous_sanitized_hash != sanitized.sha256
                ):
                    job = db.get(ParserTemplateJob, sample.job_id)
                    if job is not None:
                        _invalidate_parser_candidates_for_sample_revision(
                            db,
                            job,
                            actor="parser-sanitizer",
                            reason=(
                                "Parser sanitized source changed; regenerate the candidate "
                                "against the revised evidence"
                            ),
                            now=utcnow(),
                        )
                sample.sanitized_text = sanitized.text
                sample.sanitized_hash = sanitized.sha256
                sample.sanitization_version = sanitized.version
                sample.redaction_summary_json = sanitized.redaction_summary
                sample.sanitization_status = "sanitized"
                sample.sanitized_at = utcnow()
                results.append(
                    {
                        "sample_id": sample.id,
                        "success": True,
                        "sanitized_hash": sanitized.sha256,
                        "redaction_summary": sanitized.redaction_summary,
                    }
                )
        return results

    def retire_non_authoritative_samples(
        self,
        *,
        dataset_id: str,
        authoritative_run_id: str,
        actor: str,
        reason: str,
    ) -> dict[str, Any]:
        """Retain superseded import samples for audit but exclude them from generation."""

        normalized_dataset_id = str(dataset_id or "").strip()
        normalized_run_id = str(authoritative_run_id or "").strip()
        normalized_actor = str(actor or "").strip()
        normalized_reason = str(reason or "").strip()
        if not all(
            (
                normalized_dataset_id,
                normalized_run_id,
                normalized_actor,
                normalized_reason,
            )
        ):
            raise ParserJobStateError(
                "Parser sample retirement requires dataset, authoritative Run, actor and reason"
            )
        now = utcnow()
        with get_db_session() as db:
            samples = (
                db.query(ParserJobSample)
                .join(CommandSnapshot, CommandSnapshot.id == ParserJobSample.snapshot_id)
                .filter(
                    ParserJobSample.dataset_id == normalized_dataset_id,
                    ParserJobSample.lifecycle_status == "current",
                    CommandSnapshot.run_id != normalized_run_id,
                )
                .order_by(ParserJobSample.job_id, ParserJobSample.id)
                .with_for_update()
                .all()
            )
            job_ids = sorted({str(sample.job_id) for sample in samples})
            jobs = (
                db.query(ParserTemplateJob)
                .filter(ParserTemplateJob.id.in_(job_ids))
                .order_by(ParserTemplateJob.id)
                .with_for_update()
                .all()
                if job_ids
                else []
            )
            invalidated_by_job: dict[str, list[str]] = {}
            for job in jobs:
                invalidated_by_job[str(job.id)] = (
                    _invalidate_parser_candidates_for_sample_revision(
                        db,
                        job,
                        actor=normalized_actor,
                        reason=normalized_reason,
                        now=now,
                    )
                )
            for sample in samples:
                sample.lifecycle_status = "retired"
                sample.retired_at = now
                sample.retirement_reason = normalized_reason
            db.flush()
            for job in jobs:
                invalidated = invalidated_by_job.get(str(job.id), [])
                if invalidated and job.status in TERMINAL_JOB_STATUSES:
                    job.status = "collecting"
                    job.completed_at = None
                    job.failure_code = None
                    job.last_error = None
                    job.celery_task_id = None
                    job.dispatched_at = None
                    job.started_at = None
                    job.best_attempt_id = None
                    job.agent_budget_json = None
                    job.model_calls_used = 0
                    job.contract_revision_count = 0
                    job.template_attempt_count = 0
                job.updated_at = now
                db.add(
                    AuditLogRecord(
                        id=str(uuid.uuid4()),
                        user_id=normalized_actor,
                        username=normalized_actor,
                        action="parser_samples_retired",
                        resource_type="parser_template_job",
                        resource_id=str(job.id),
                        detail={
                            "reason": normalized_reason,
                            "dataset_id": normalized_dataset_id,
                            "authoritative_run_id": normalized_run_id,
                            "retired_sample_ids": [
                                str(sample.id)
                                for sample in samples
                                if str(sample.job_id) == str(job.id)
                            ],
                            "invalidated_template_ids": invalidated,
                        },
                        status="success",
                        created_at=now,
                    )
                )
            return {
                "dataset_id": normalized_dataset_id,
                "authoritative_run_id": normalized_run_id,
                "retired_sample_count": len(samples),
                "affected_job_ids": job_ids,
                "invalidated_template_ids": sorted(
                    {
                        template_id
                        for template_ids in invalidated_by_job.values()
                        for template_id in template_ids
                    }
                ),
            }

    def register_template(
        self,
        *,
        vendor: str,
        model: str | None,
        family: str | None,
        profile: str,
        command: str,
        category: str,
        version: str,
        asset_path: str,
        sha256: str,
        template_text: str | None = None,
        validation: dict[str, Any],
        source_job_id: str | None = None,
        status: str = "draft",
        contract_version: int | None = None,
        candidate_sample_manifest: list[dict[str, Any]] | None = None,
        expected_celery_task_id: str | None = None,
    ) -> str:
        validation = dict(validation or {})
        normalized_candidate_manifest: list[dict[str, Any]] = []
        if source_job_id and _is_generated_parser_candidate(validation):
            if (
                validation.get("candidate_sample_evidence_version")
                != CANDIDATE_SAMPLE_EVIDENCE_VERSION
            ):
                raise ValueError(
                    "generated Parser candidate requires immutable sample evidence v1"
                )
            if not candidate_sample_manifest:
                raise ValueError(
                    "generated Parser candidate requires its exact source sample manifest"
                )
            raw_validation_manifest = validation.get("sample_manifest")
            if not isinstance(raw_validation_manifest, list):
                raise ValueError("generated Parser candidate sample manifest is missing")
            normalized_candidate_manifest = [
                _normalized_candidate_manifest_item(item)
                for item in candidate_sample_manifest
            ]
            normalized_validation_manifest = [
                _normalized_candidate_manifest_item(item)
                for item in raw_validation_manifest
            ]
            manifest_key = lambda item: str(item["sample_id"])
            if sorted(normalized_candidate_manifest, key=manifest_key) != sorted(
                normalized_validation_manifest,
                key=manifest_key,
            ):
                raise ValueError(
                    "candidate source sample manifest does not match validation evidence"
                )
            if len({item["sample_id"] for item in normalized_candidate_manifest}) != len(
                normalized_candidate_manifest
            ):
                raise ValueError("candidate source sample manifest contains duplicate IDs")
        if template_text is not None:
            content_sha256 = hashlib.sha256(template_text.encode("utf-8")).hexdigest()
            if content_sha256 != sha256:
                raise ValueError("parser template content does not match its SHA-256")
        digest = contract_digest(validation)
        with get_db_session() as db:
            if expected_celery_task_id is not None:
                job = db.query(ParserTemplateJob).filter_by(id=source_job_id).with_for_update().first()
                if (not job or job.status not in {"running", "validating"}
                        or job.celery_task_id != expected_celery_task_id):
                    raise ParserJobStateError("Parser task was replaced or paused; stale candidate discarded")
            registration_key = "|".join(
                (
                    str(vendor).strip().casefold(),
                    str(profile).strip().casefold(),
                    str(command).strip().casefold(),
                    str(version).strip(),
                )
            )
            db.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": registration_key},
            )
            existing = (
                db.query(ParserTemplate)
                .filter(
                    ParserTemplate.vendor == vendor,
                    ParserTemplate.profile == profile,
                    ParserTemplate.canonical_command == command,
                    ParserTemplate.version == version,
                )
                .first()
            )
            if existing is not None:
                same_registration = (
                    existing.sha256 == sha256
                    and existing.asset_path == asset_path
                    and existing.template_text == template_text
                    and (existing.source_job_id or None) == (source_job_id or None)
                )
                if same_registration:
                    evidence_errors = _candidate_sample_evidence_errors(
                        db,
                        existing,
                        dict(existing.validation_json or {}),
                    )
                    if evidence_errors:
                        raise ValueError(
                            "existing Parser candidate evidence is invalid: "
                            + "; ".join(evidence_errors)
                        )
                    return existing.id
                raise ValueError(
                    "parser template version already belongs to a different immutable asset"
                )

            if contract_version is None:
                previous = (
                    db.query(ParserTemplate)
                    .filter(
                        ParserTemplate.vendor == vendor,
                        ParserTemplate.profile == profile,
                        ParserTemplate.canonical_command == command,
                    )
                    .order_by(
                        ParserTemplate.contract_version.desc(),
                        ParserTemplate.created_at.desc(),
                    )
                    .first()
                )
                if previous is None:
                    contract_version = 1
                elif previous.contract_sha256 == digest:
                    contract_version = int(previous.contract_version or 1)
                else:
                    contract_version = int(previous.contract_version or 1) + 1
            validation["contract_version"] = int(contract_version)
            validation["contract_sha256"] = digest
            template_id = str(uuid.uuid4())
            template = ParserTemplate(
                id=template_id,
                vendor=vendor,
                model=model,
                family=family,
                profile=profile,
                canonical_command=command,
                category=category,
                version=version,
                asset_path=asset_path,
                template_text=template_text,
                sha256=sha256,
                status=status,
                validation_json=validation,
                contract_version=int(contract_version),
                contract_sha256=digest,
                source_job_id=source_job_id,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            db.add(template)
            db.flush()
            for manifest_item in normalized_candidate_manifest:
                sample_id = str(manifest_item["sample_id"])
                sample = db.get(ParserJobSample, sample_id)
                if sample is None:
                    raise ValueError(f"candidate source sample does not exist: {sample_id}")
                if str(sample.job_id) != str(source_job_id):
                    raise ValueError(
                        f"candidate source sample belongs to another Parser Job: {sample_id}"
                    )
                db.add(
                    ParserCandidateSampleEvidence(
                        id=str(uuid.uuid4()),
                        template_id=template_id,
                        sample_id=sample_id,
                        manifest_item_json=manifest_item,
                        manifest_item_sha256=_stable_json_digest(manifest_item),
                        created_at=utcnow(),
                    )
                )
            if normalized_candidate_manifest:
                db.flush()
                evidence_errors = _candidate_sample_evidence_errors(
                    db,
                    template,
                    validation,
                )
                if evidence_errors:
                    raise ValueError(
                        "generated Parser candidate source evidence is invalid: "
                        + "; ".join(evidence_errors)
                    )
        return template_id

    def activate_template(
        self,
        template_id: str,
        *,
        approved_by: str = "manual-api",
        reason: str | None = None,
        force: bool = False,
        approval_kind: str = "approval",
        allow_same_reviewer: bool = False,
        automation_authorization: tuple[str, str] | None = None,
    ) -> ParserTemplate:
        normalized_actor = str(approved_by or "").strip()
        normalized_reason = str(reason or "").strip()
        if not normalized_actor:
            raise ValueError("template activation requires an actor")
        with get_db_session() as db:
            template = (
                db.query(ParserTemplate)
                .filter(ParserTemplate.id == template_id)
                .with_for_update()
                .one()
            )
            if template.status == "active":
                db.expunge(template)
                return template
            validation = dict(template.validation_json or {})
            automated = None
            if automation_authorization:
                from src.apps.rule_governance.backend.rule_control.autopilot_authorization import (
                    require_autopilot_release,
                )
                automated = require_autopilot_release(db, automation_authorization, kind="parser", asset_id=template_id)
                if validation.get("human_direct_review_required") and not (validation.get("ai_review") or {}).get("passed"):
                    raise ParserTemplateStateError("自动发布需要通过 AI 语义复核；当前候选仍需人工复核")
            regression = dict(validation.get("regression_validation") or {})
            sample_count = int(regression.get("sample_count") or 0)
            passed_samples = int(regression.get("passed_samples") or 0)
            if template.status not in {"draft", "pending_approval", "deprecated"}:
                raise ParserTemplateStateError(
                    f"template cannot be activated from status {template.status}"
                )
            if not force and (
                not regression.get("passed") or sample_count <= 0 or passed_samples != sample_count
            ):
                raise ParserTemplateStateError(
                    "template activation requires a non-empty, fully passing regression set"
                )
            agent_gate_errors = generated_template_activation_errors(
                validation,
                source_job_id=template.source_job_id,
            )
            if not force and agent_gate_errors:
                raise ParserTemplateStateError(
                    "template activation blocked by Parser Agent governance: "
                    + "; ".join(agent_gate_errors)
                )
            sample_evidence_errors = _candidate_sample_evidence_errors(
                db,
                template,
                validation,
            )
            if not force and sample_evidence_errors:
                raise ParserTemplateStateError(
                    "template activation blocked by immutable source evidence: "
                    + "; ".join(sample_evidence_errors)
                )
            semantic_review_errors = parser_semantic_review_activation_errors(
                template,
                validation,
            )
            if not force and semantic_review_errors and not automated:
                raise ParserTemplateStateError(
                    "template activation blocked by human semantic review: "
                    + "; ".join(semantic_review_errors)
                )
            semantic_review = dict(validation.get("human_semantic_review") or {})
            semantic_reviewer = str(semantic_review.get("reviewed_by") or "").strip()
            same_review_and_release_actor = bool(
                semantic_reviewer
                and semantic_reviewer.casefold()
                == str(approved_by).strip().casefold()
            )
            if (
                not force
                and same_review_and_release_actor
                and not allow_same_reviewer
            ):
                raise ParserTemplateStateError(
                    "template activation requires a different release approver "
                    "from the semantic reviewer"
                )
            if template.source_job_id:
                source_job = db.get(ParserTemplateJob, template.source_job_id)
                evidence_version = validation.get("parser_spec_evidence_version")
                candidate_spec_sha256 = str(
                    (
                        validation.get("source_parser_spec_sha256")
                        if evidence_version == 2
                        else validation.get("parser_spec_sha256")
                    )
                    or ""
                )
                current_spec_sha256 = str(getattr(source_job, "parser_spec_sha256", None) or "")
                if (
                    source_job is not None
                    and candidate_spec_sha256
                    and candidate_spec_sha256 != current_spec_sha256
                ):
                    raise ParserTemplateStateError(
                        "template activation blocked because ParserSpec was revised "
                        "after this candidate was generated"
                    )
            old_active_templates = (
                db.query(ParserTemplate)
                .filter(
                    ParserTemplate.vendor == template.vendor,
                    ParserTemplate.profile == template.profile,
                    ParserTemplate.canonical_command == template.canonical_command,
                    ParserTemplate.status == "active",
                    ParserTemplate.id != template.id,
                )
                .order_by(ParserTemplate.id)
                .with_for_update()
                .all()
            )
            for old_template in old_active_templates:
                old_template.status = "deprecated"
                old_template.deprecated_at = utcnow()
                old_template.updated_at = utcnow()
            db.flush()
            template.status = "active"
            validation["contract_approval_status"] = "approved"
            validation["contract_approved_at"] = utcnow().isoformat()
            validation["contract_approved_by"] = normalized_actor
            validation["contract_approval_reason"] = normalized_reason
            validation["contract_approval_kind"] = approval_kind
            if automated:
                validation["automated_approval"] = automated
            validation["contract_force_activated"] = bool(force)
            validation["separation_of_duties_override"] = bool(
                not force and same_review_and_release_actor and allow_same_reviewer
            )
            validation["human_direct_review_confirmed"] = bool(
                semantic_review.get("review_mode") == "human_direct"
            )
            result_preview = validation.get("approval_result_preview")
            if isinstance(result_preview, dict):
                validation["approved_result_baseline"] = {
                    **result_preview,
                    "approved_at": utcnow().isoformat(),
                    "approved_by": str(approved_by).strip(),
                    "template_sha256": template.sha256,
                    "parser_spec_sha256": validation.get("parser_spec_sha256"),
                }
            validation["approval_audit"] = [
                *list(validation.get("approval_audit") or []),
                {
                    "action": approval_kind,
                    "actor": normalized_actor,
                    "reason": normalized_reason,
                    "semantic_review_actor": semantic_reviewer or None,
                    "separation_of_duties_override": bool(
                        not force
                        and same_review_and_release_actor
                        and allow_same_reviewer
                    ),
                    "semantic_review_evidence_digest": semantic_review.get(
                        "evidence_digest"
                    ),
                    "occurred_at": utcnow().isoformat(),
                },
            ]
            template.validation_json = validation
            template.activated_at = utcnow()
            template.updated_at = utcnow()
            profile = (
                db.query(ParserDeviceProfile)
                .filter(
                    ParserDeviceProfile.vendor == template.vendor,
                    ParserDeviceProfile.model == template.model,
                )
                .first()
            )
            if profile is None and template.model:
                profile = ParserDeviceProfile(
                    id=str(uuid.uuid4()),
                    vendor=template.vendor,
                    model=template.model,
                    normalized_model=template.model,
                )
                db.add(profile)
            if profile is not None:
                profile.family = template.family
                profile.confidence = 1.0
                profile.status = "confirmed"
                profile.evidence_json = {
                    "source": "approved_template",
                    "template_id": template.id,
                }
                profile.updated_at = utcnow()

            evidence = dict(validation.get("identity_evidence") or {})
            evidence_command = str(evidence.get("command") or "").strip()
            if template.model and evidence_command:
                pattern = rf"(?i)\b{re.escape(template.model)}\b"
                signature = (
                    db.query(ParserIdentitySignature)
                    .filter(
                        ParserIdentitySignature.vendor == template.vendor,
                        ParserIdentitySignature.model == template.model,
                        ParserIdentitySignature.evidence_command == evidence_command,
                        ParserIdentitySignature.pattern == pattern,
                    )
                    .first()
                )
                if signature is None:
                    db.add(
                        ParserIdentitySignature(
                            id=str(uuid.uuid4()),
                            vendor=template.vendor,
                            model=template.model,
                            family=template.family,
                            evidence_command=evidence_command,
                            pattern=pattern,
                            confidence=1.0,
                            status="confirmed",
                            source="approved_template",
                            created_at=utcnow(),
                            updated_at=utcnow(),
                        )
                    )
            audit_action = {
                "approval": "parser_template_activated",
                "force_activation": "parser_template_force_activated",
                "rollback": "parser_template_rolled_back",
            }.get(approval_kind, "parser_template_activated")
            db.add(
                AuditLogRecord(
                    id=str(uuid.uuid4()),
                    user_id=normalized_actor,
                    username=normalized_actor,
                    action=audit_action,
                    resource_type="parser_template",
                    resource_id=str(template.id),
                    detail={
                        "reason": normalized_reason,
                        "approval_kind": approval_kind,
                        "force": bool(force),
                        "template_sha256": template.sha256,
                        "contract_sha256": template.contract_sha256,
                        "semantic_review_actor": semantic_reviewer or None,
                        "separation_of_duties_override": bool(
                            not force
                            and same_review_and_release_actor
                            and allow_same_reviewer
                        ),
                        "semantic_review_evidence_digest": semantic_review.get(
                            "evidence_digest"
                        ),
                    },
                    status="success",
                    created_at=utcnow(),
                )
            )
            self._add_outbox(
                db,
                event_type="parser.template.activated",
                aggregate_id=template.id,
                payload={"template_id": template.id},
            )
            db.flush()
            db.expunge(template)
            return template

    def record_semantic_review(
        self,
        template_id: str,
        *,
        actor: str,
        reason: str | None = None,
        result_confirmed: bool,
        primary_key_confirmed: bool,
    ) -> dict[str, Any]:
        """Persist an engineer's semantic decision without activating the Parser."""

        normalized_actor = str(actor or "").strip()
        normalized_reason = str(reason or "").strip()
        if not normalized_actor:
            raise ValueError("semantic review requires an actor")
        if not result_confirmed or not primary_key_confirmed:
            raise ValueError(
                "semantic review requires parsed-result and primary-key confirmation"
            )
        with get_db_session() as db:
            template = (
                db.query(ParserTemplate)
                .filter(ParserTemplate.id == template_id)
                .with_for_update()
                .one()
            )
            if template.status != "pending_approval":
                raise ValueError(
                    "semantic review requires template status=pending_approval"
                )
            validation = dict(template.validation_json or {})
            gate_errors = generated_template_activation_errors(
                validation,
                source_job_id=template.source_job_id,
            )
            if gate_errors:
                raise ValueError(
                    "semantic review blocked by Parser objective evidence: "
                    + "; ".join(gate_errors)
                )
            sample_evidence_errors = _candidate_sample_evidence_errors(
                db,
                template,
                validation,
            )
            if sample_evidence_errors:
                raise ValueError(
                    "semantic review blocked by immutable source evidence: "
                    + "; ".join(sample_evidence_errors)
                )
            regression = dict(validation.get("regression_validation") or {})
            sample_count = int(regression.get("sample_count") or 0)
            passed_samples = int(regression.get("passed_samples") or 0)
            if (
                regression.get("passed") is not True
                or sample_count <= 0
                or passed_samples != sample_count
            ):
                raise ValueError(
                    "semantic review requires a non-empty, fully passing regression set"
                )
            preview = dict(validation.get("approval_result_preview") or {})
            if not isinstance(preview.get("samples"), list) or not preview["samples"]:
                raise ValueError("semantic review requires frozen parsed-result evidence")
            evidence, evidence_digest = parser_semantic_review_evidence(
                template,
                validation,
            )
            direct_review_required = parser_human_direct_review_required(validation)
            reviewed_at = utcnow()
            review = {
                "schema_version": "parser-human-semantic-review/v1",
                "status": "accepted",
                "review_mode": (
                    "human_direct" if direct_review_required else "human_after_ai"
                ),
                "reviewed_by": normalized_actor,
                "reviewed_at": reviewed_at.isoformat(),
                "reason": normalized_reason,
                "result_confirmed": True,
                "primary_key_confirmed": True,
                "evidence": evidence,
                "evidence_digest": evidence_digest,
            }
            validation["human_semantic_review"] = review
            validation["semantic_review_audit"] = [
                *list(validation.get("semantic_review_audit") or []),
                review,
            ]
            template.validation_json = validation
            template.updated_at = reviewed_at
            db.add(
                AuditLogRecord(
                    id=str(uuid.uuid4()),
                    user_id=normalized_actor,
                    username=normalized_actor,
                    action="parser_template_semantic_reviewed",
                    resource_type="parser_template",
                    resource_id=str(template.id),
                    detail={
                        "reason": normalized_reason,
                        "review_mode": review["review_mode"],
                        "result_confirmed": True,
                        "primary_key_confirmed": True,
                        "evidence_digest": evidence_digest,
                        "template_sha256": template.sha256,
                        "contract_sha256": template.contract_sha256,
                    },
                    status="success",
                    created_at=reviewed_at,
                )
            )
            return dict(review)

    def rollback_template(
        self,
        template_id: str,
        *,
        actor: str,
        reason: str,
    ) -> ParserTemplate:
        return self.activate_template(
            template_id,
            approved_by=actor,
            reason=reason,
            force=True,
            approval_kind="rollback",
        )

    def supersede_legacy_jobs(self) -> int:
        """Hard-delete jobs whose samples cannot be resolved from PostgreSQL.

        The method name remains for API compatibility. Invalid historical tasks
        are deliberately not migrated into a replacement asset aggregate.
        """

        count = 0
        with get_db_session() as db:
            jobs = db.query(ParserTemplateJob).all()
            for job in jobs:
                samples = (
                    db.query(ParserJobSample)
                    .filter(
                        ParserJobSample.job_id == job.id,
                        ParserJobSample.lifecycle_status == "current",
                    )
                    .all()
                )
                if not samples or not any(
                    sample.snapshot_source != "postgresql"
                    or db.get(CommandSnapshot, sample.snapshot_id) is None
                    for sample in samples
                ):
                    continue
                self._delete_job_graph(db, job)
                count += 1
        return count

    def list_templates(self, *, status: str | None = None) -> list[dict[str, Any]]:
        with get_db_session() as db:
            query = db.query(ParserTemplate)
            if status:
                query = query.filter(ParserTemplate.status == status)
            return [row.to_dict() for row in query.order_by(ParserTemplate.created_at.desc()).all()]

    def list_templates_page(
        self,
        *,
        status: str | None = None,
        search: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        page_size = max(1, min(int(limit), 200))
        page_offset = max(0, int(offset))
        with get_db_session() as db:
            query = db.query(ParserTemplate)
            if status:
                query = query.filter(ParserTemplate.status == status)
            pattern = f"%{str(search).strip()}%" if str(search or "").strip() else None
            if pattern:
                query = query.filter(
                    or_(
                        ParserTemplate.vendor.ilike(pattern),
                        ParserTemplate.profile.ilike(pattern),
                        ParserTemplate.canonical_command.ilike(pattern),
                        ParserTemplate.category.ilike(pattern),
                        ParserTemplate.model.ilike(pattern),
                        ParserTemplate.family.ilike(pattern),
                    )
                )
            total = query.order_by(None).count()
            rows = (
                query.order_by(ParserTemplate.created_at.desc(), ParserTemplate.id.desc())
                .offset(page_offset)
                .limit(page_size)
                .all()
            )
            return {
                "items": [row.to_dict() for row in rows],
                "total": total,
                "limit": page_size,
                "offset": page_offset,
            }

    def template_status_counts(self) -> dict[str, int]:
        with get_db_session() as db:
            return {
                str(status): int(count)
                for status, count in db.query(
                    ParserTemplate.status,
                    func.count(ParserTemplate.id),
                )
                .group_by(ParserTemplate.status)
                .all()
            }

    def get_template(self, template_id: str) -> ParserTemplate | None:
        with get_db_session() as db:
            row = db.query(ParserTemplate).filter(ParserTemplate.id == template_id).first()
            if row:
                db.expunge(row)
            return row

    def get_candidate_sample_evidence(
        self,
        template_id: str,
        *,
        include_sanitized_payload: bool = False,
    ) -> dict[str, Any]:
        """Return the exact sanitized samples frozen for candidate approval."""

        with get_db_session() as db:
            template = db.get(ParserTemplate, template_id)
            if template is None:
                raise ValueError(f"Parser template {template_id} not found")
            errors = _candidate_sample_evidence_errors(
                db,
                template,
                dict(template.validation_json or {}),
            )
            links = (
                db.query(ParserCandidateSampleEvidence)
                .filter(ParserCandidateSampleEvidence.template_id == template_id)
                .order_by(
                    ParserCandidateSampleEvidence.created_at,
                    ParserCandidateSampleEvidence.sample_id,
                )
                .all()
            )
            items: list[dict[str, Any]] = []
            for link in links:
                sample = db.get(ParserJobSample, link.sample_id)
                if sample is None:
                    continue
                snapshot = db.get(CommandSnapshot, sample.snapshot_id)
                item = {
                    "sample_id": str(sample.id),
                    "snapshot_id": str(sample.snapshot_id),
                    "job_id": str(sample.job_id),
                    "dataset_id": str(sample.dataset_id or ""),
                    "device_id": str(sample.device_id or ""),
                    "sample_role": str(sample.sample_role or ""),
                    "expectation": dict(sample.expectation_json or {}),
                    "manifest": dict(link.manifest_item_json or {}),
                    "manifest_item_sha256": str(link.manifest_item_sha256 or ""),
                    "sanitization_status": str(sample.sanitization_status or ""),
                    "sanitization_version": str(sample.sanitization_version or ""),
                    "sanitized_hash": str(sample.sanitized_hash or ""),
                    "redaction_summary": dict(sample.redaction_summary_json or {}),
                    "raw_text_redacted": True,
                    "evidence_kind": "sanitized_candidate_sample",
                }
                if snapshot is not None:
                    item["source_context"] = {
                        "device_name": str(snapshot.device_name or ""),
                        "management_ip": str(snapshot.management_ip or ""),
                        "vendor": str(snapshot.vendor or ""),
                        "model": str(snapshot.model or ""),
                        "command": str(snapshot.command or ""),
                        "canonical_command": str(snapshot.canonical_command or ""),
                        "observed_at": snapshot.observed_at,
                    }
                if include_sanitized_payload:
                    item["sanitized_text"] = str(sample.sanitized_text or "")
                items.append(item)
            return {
                "template_id": str(template.id),
                "source_job_id": str(template.source_job_id or ""),
                "evidence_version": (template.validation_json or {}).get(
                    "candidate_sample_evidence_version"
                ),
                "errors": errors,
                "items": items,
            }

    def find_template_version(
        self, *, vendor: str, profile: str, command: str, version: str
    ) -> ParserTemplate | None:
        with get_db_session() as db:
            row = (
                db.query(ParserTemplate)
                .filter(
                    ParserTemplate.vendor == vendor,
                    ParserTemplate.profile == profile,
                    ParserTemplate.canonical_command == command,
                    ParserTemplate.version == version,
                )
                .first()
            )
            if row:
                db.expunge(row)
            return row

    def has_trusted_family(self, *, vendor: str, family: str) -> bool:
        with get_db_session() as db:
            return (
                db.query(ParserTemplate)
                .filter(
                    ParserTemplate.vendor == vendor,
                    ParserTemplate.family == family,
                    ParserTemplate.status == "active",
                )
                .first()
                is not None
            )

    def set_template_status(
        self,
        template_id: str,
        status: str,
        *,
        actor: str | None = None,
        reason: str | None = None,
    ) -> None:
        with get_db_session() as db:
            row = db.query(ParserTemplate).filter(ParserTemplate.id == template_id).one()
            if actor is not None and (not actor.strip() or not str(reason or "").strip()):
                raise ValueError("template status transition requires actor and reason")
            if row.status == "deleted":
                raise ParserTemplateStateError("模板已删除，不能变更状态")
            previous_status = row.status
            row.status = status
            if status == "deprecated":
                row.deprecated_at = utcnow()
            validation = dict(row.validation_json or {})
            if actor is not None:
                validation["status_audit"] = [
                    *list(validation.get("status_audit") or []),
                    {
                        "actor": actor.strip(),
                        "reason": str(reason).strip(),
                        "from_status": previous_status,
                        "to_status": status,
                        "at": utcnow().isoformat(),
                    },
                ]
                row.validation_json = validation
            row.updated_at = utcnow()

    def governance_alert_summary(self) -> dict[str, Any]:
        settings = get_settings()
        now = utcnow()
        stale_before = now - timedelta(minutes=max(1, settings.PARSER_CANDIDATE_STALE_MINUTES))
        recent_before = now - timedelta(hours=24)
        alerts: list[dict[str, Any]] = []
        with get_db_session() as db:
            production_open = (
                db.query(func.count(ParserTemplateJob.id))
                .filter(
                    ParserTemplateJob.source_mode == "production",
                    ParserTemplateJob.status.in_(OPEN_JOB_STATUSES),
                )
                .scalar()
                or 0
            )
            stale_candidates = (
                db.query(func.count(ParserTemplateJob.id))
                .filter(
                    ParserTemplateJob.source_mode == "production",
                    ParserTemplateJob.status.in_(OPEN_JOB_STATUSES),
                    ParserTemplateJob.updated_at < stale_before,
                )
                .scalar()
                or 0
            )
            failed_generation = (
                db.query(func.count(ParserTemplateJob.id))
                .filter(
                    ParserTemplateJob.source_mode == "production",
                    ParserTemplateJob.status == "failed",
                    ParserTemplateJob.updated_at >= recent_before,
                )
                .scalar()
                or 0
            )
            activation_publish_failures = (
                db.query(func.count(ParserOutboxEvent.id))
                .filter(
                    ParserOutboxEvent.event_type == "parser.template.activated",
                    ParserOutboxEvent.status == "pending",
                    ParserOutboxEvent.attempts > 0,
                )
                .scalar()
                or 0
            )
            stale_approvals = (
                db.query(func.count(ParserTemplate.id))
                .filter(
                    ParserTemplate.status == "pending_approval",
                    ParserTemplate.updated_at < stale_before,
                )
                .scalar()
                or 0
            )
            recent_results = (
                db.query(func.count(PatrolCollectionResult.id))
                .filter(
                    PatrolCollectionResult.updated_at >= recent_before,
                )
                .scalar()
                or 0
            )
            blocked_results = (
                db.query(func.count(PatrolCollectionResult.id))
                .filter(
                    PatrolCollectionResult.updated_at >= recent_before,
                    PatrolCollectionResult.fact_status == "blocked_by_parser",
                )
                .scalar()
                or 0
            )
            rule_jobs = (
                db.query(RuleGenerationJobRecord)
                .filter(
                    RuleGenerationJobRecord.updated_at >= recent_before,
                )
                .all()
            )
            # The session commits on context exit and expires ORM attributes.
            # Project the aggregate while rows are still attached so the
            # governance endpoint never dereferences detached instances.
            rule_generation_failures = sum(
                item.status in {"failed", "validation_failed", "rejected"} for item in rule_jobs
            )
            replay_collisions = sum(
                int(
                    ((item.validation_report_json or {}).get("replay") or {}).get("collision_count")
                    or 0
                )
                for item in rule_jobs
            )

        blocked_ratio = float(blocked_results) / float(recent_results) if recent_results else 0.0

        def add(code: str, count: int | float, severity: str, message: str) -> None:
            if count:
                alerts.append(
                    {"code": code, "count": count, "severity": severity, "message": message}
                )

        if production_open >= max(1, settings.PARSER_BACKLOG_ALERT_THRESHOLD):
            add(
                "parser_backlog_high",
                production_open,
                "warning",
                "Production Parser backlog exceeded its configured threshold.",
            )
        add(
            "parser_candidate_stale",
            stale_candidates,
            "warning",
            "Production Parser candidates exceeded the generation SLA.",
        )
        add(
            "parser_approval_stale",
            stale_approvals,
            "warning",
            "Generated Parser templates are waiting too long for approval.",
        )
        add(
            "parser_generation_failed",
            failed_generation,
            "error",
            "Production Parser generation failed in the last 24 hours.",
        )
        add(
            "template_activation_publish_failed",
            activation_publish_failures,
            "error",
            "Template activation outbox delivery is retrying.",
        )
        if blocked_ratio >= settings.PARSER_BLOCKED_RATIO_ALERT_THRESHOLD and recent_results:
            add(
                "blocked_by_parser_ratio_high",
                round(blocked_ratio, 4),
                "warning",
                "The 24-hour blocked-by-parser ratio exceeded its configured threshold.",
            )
        add(
            "rule_generation_failed",
            rule_generation_failures,
            "error",
            "Rule asset generation or validation failed in the last 24 hours.",
        )
        add(
            "rule_replay_collision",
            replay_collisions,
            "error",
            "Rule replay detected collisions; approval must remain blocked.",
        )
        return {
            "alerts": alerts,
            "alert_count": len(alerts),
            "blocked_by_parser_ratio_24h": round(blocked_ratio, 4),
        }

    def record_regression_validation(
        self,
        template_id: str,
        *,
        passed: bool,
        sample_count: int,
        passed_samples: int,
        result_preview: list[dict[str, Any]] | None = None,
    ) -> None:
        with get_db_session() as db:
            row = db.query(ParserTemplate).filter(ParserTemplate.id == template_id).one()
            validation = dict(row.validation_json or {})
            validation["regression_validation"] = {
                "passed": bool(passed),
                "sample_count": int(sample_count),
                "passed_samples": int(passed_samples),
                "validated_at": utcnow().isoformat(),
            }
            if result_preview is not None:
                validation["approval_result_preview"] = {
                    "captured_at": utcnow().isoformat(),
                    "samples": result_preview,
                }
            row.validation_json = validation
            row.updated_at = utcnow()

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        current = self.get_job(job_id)
        if not current:
            raise ParserJobStateError(f"parser job {job_id} not found")
        if current["status"] not in OPEN_JOB_STATUSES:
            raise ParserJobStateError(
                f"parser job {job_id} cannot be cancelled from {current['status']}"
            )
        return self.transition_job_status(job_id, "cancelled")

    def retry_job(
        self,
        job_id: str,
        *,
        actor: str | None = None,
        reason: str | None = None,
        actor_user_id: str | None = None,
    ) -> dict[str, Any]:
        actor_user_id = normalize_actor_user_id(actor_user_id)
        with get_db_session() as db:
            job = (
                db.query(ParserTemplateJob)
                .filter(ParserTemplateJob.id == job_id)
                .with_for_update()
                .one_or_none()
            )
            if job is None:
                raise ParserJobStateError(f"parser job {job_id} not found")
            if job.pipeline_version != "simple_v2":
                raise ParserJobStateError(
                    "legacy Parser jobs are read-only; confirm ParserSpec and sample "
                    "roles before retrying"
                )
            retryable_status = job.status in {"failed", "cancelled"}
            if job.status == "succeeded":
                generated_templates = (
                    db.query(ParserTemplate)
                    .filter(ParserTemplate.source_job_id == job_id)
                    .with_for_update()
                    .all()
                )
                retryable_status = bool(generated_templates) and all(
                    template.status in {"active", "deprecated", "rejected"}
                    for template in generated_templates
                )
                if not retryable_status:
                    raise ParserJobStateError(
                        "a succeeded parser job can be retried only when every "
                        "generated candidate is active, deprecated or rejected"
                    )
            if not retryable_status:
                raise ParserJobStateError(
                    f"parser job {job_id} cannot be retried from {job.status}"
                )
            _abort_reserved_model_calls(
                db,
                job_id=job_id,
                reason="job_retry",
                completed_at=utcnow(),
            )
            job.status = "pending"
            job.last_error = None
            job.failure_code = None
            job.celery_task_id = None
            job.dispatched_at = None
            job.started_at = None
            job.completed_at = None
            # A retry starts a fresh bounded run. Immutable attempts remain
            # queryable as history and the next run continues their sequence.
            # Keep the adopted semantic plan: retrying template generation must
            # not silently discard the engineer-visible LLM design context.
            current_state = dict(job.agent_state_json or {})
            parser_spec_suggestion = current_state.get("parser_spec_suggestion")
            next_state = (
                {"parser_spec_suggestion": parser_spec_suggestion}
                if isinstance(parser_spec_suggestion, dict)
                else {}
            )
            # The confirmed spec is still bound to this CheckItem version.
            # Keep the binding with the plan; readiness revalidates it against
            # the current association, so a retry cannot bless a stale spec.
            binding = current_state.get("parser_spec_check_item")
            if isinstance(binding, dict):
                next_state["parser_spec_check_item"] = binding
            next_state["execution_identity"] = {
                "user_id": actor_user_id, "display": actor, "task_id": None,
            }
            job.agent_state_json = next_state
            job.agent_budget_json = None
            job.best_attempt_id = None
            job.model_calls_used = 0
            job.contract_revision_count = 0
            job.template_attempt_count = 0
            job.updated_at = utcnow()
            if actor and reason:
                db.add(
                    AuditLogRecord(
                        id=str(uuid.uuid4()),
                        user_id=actor_user_id,
                        username=str(actor),
                        action="parser_job_retry",
                        resource_type="parser_template_job",
                        resource_id=job_id,
                        detail={
                            "reason": str(reason),
                            "parser_spec_revision": int(job.parser_spec_revision or 0),
                            "parser_spec_sha256": job.parser_spec_sha256,
                        },
                        status="success",
                        created_at=utcnow(),
                    )
                )
            db.flush()
            return job.to_dict()

    @staticmethod
    def _delete_job_graph(db: Any, job: ParserTemplateJob) -> dict[str, int]:
        """Hard-delete one Parser task and its non-runtime historical artifacts."""

        job_id = str(job.id)
        template_rows = (
            db.query(ParserTemplate).filter(ParserTemplate.source_job_id == job_id).all()
        )
        active_templates = [row for row in template_rows if row.status == "active"]
        historical_templates = [row for row in template_rows if row.status != "active"]
        historical_template_ids = [str(row.id) for row in historical_templates]
        active_template_ids = [str(row.id) for row in active_templates]
        if active_template_ids:
            retained_evidence_count = int(
                db.query(func.count(ParserCandidateSampleEvidence.id))
                .filter(
                    ParserCandidateSampleEvidence.template_id.in_(active_template_ids)
                )
                .scalar()
                or 0
            )
            if retained_evidence_count:
                raise ParserJobStateError(
                    "Parser Job cannot be deleted while an active generated template "
                    "retains its source evidence"
                )

        for template in active_templates:
            template.source_job_id = None
            template.updated_at = utcnow()
        deleted_rule_jobs = 0
        deleted_backfills = 0
        deleted_parse_results = 0
        deleted_candidate_evidence = 0
        if historical_template_ids:
            deleted_candidate_evidence = int(
                db.query(ParserCandidateSampleEvidence)
                .filter(
                    ParserCandidateSampleEvidence.template_id.in_(
                        historical_template_ids
                    )
                )
                .delete(synchronize_session=False)
                or 0
            )
            source_refs = [
                f"parser_template:{template_id}" for template_id in historical_template_ids
            ]
            linked_rule_jobs = (
                db.query(RuleGenerationJobRecord)
                .filter(
                    or_(
                        RuleGenerationJobRecord.asset_id.in_(historical_template_ids),
                        RuleGenerationJobRecord.source_ref.in_(source_refs),
                    )
                )
                .all()
            )
            for rule_job in linked_rule_jobs:
                if rule_job.status == "active":
                    rule_job.source_ref = None
                    rule_job.asset_ref = None
                    rule_job.asset_id = None
                    rule_job.updated_at = utcnow()
                else:
                    db.delete(rule_job)
                    deleted_rule_jobs += 1
            deleted_backfills = int(
                db.query(ParserBackfillJobRecord)
                .filter(ParserBackfillJobRecord.template_id.in_(historical_template_ids))
                .delete(synchronize_session=False)
                or 0
            )
            deleted_parse_results = int(
                db.query(SnapshotParseResult)
                .filter(SnapshotParseResult.template_id.in_(historical_template_ids))
                .delete(synchronize_session=False)
                or 0
            )
            for template in historical_templates:
                db.delete(template)

        job.best_attempt_id = None
        deleted_samples = int(
            db.query(ParserJobSample)
            .filter(ParserJobSample.job_id == job_id)
            .delete(synchronize_session=False)
            or 0
        )
        deleted_attempts = int(
            db.query(ParserGenerationAttempt)
            .filter(ParserGenerationAttempt.job_id == job_id)
            .delete(synchronize_session=False)
            or 0
        )
        deleted_outbox = int(
            db.query(ParserOutboxEvent)
            .filter(ParserOutboxEvent.aggregate_id == job_id)
            .delete(synchronize_session=False)
            or 0
        )
        db.delete(job)
        return {
            "deleted_samples": deleted_samples,
            "deleted_attempts": deleted_attempts,
            "deleted_outbox_events": deleted_outbox,
            "deleted_templates": len(historical_templates),
            "detached_active_templates": len(active_templates),
            "deleted_rule_jobs": deleted_rule_jobs,
            "deleted_backfills": deleted_backfills,
            "deleted_parse_results": deleted_parse_results,
            "deleted_candidate_evidence": deleted_candidate_evidence,
        }

    def purge_duplicate_asset_jobs(
        self,
        *,
        dry_run: bool = True,
        only_identity: tuple[str, str, str] | None = None,
    ) -> dict[str, Any]:
        """Delete duplicate task rows without merging their samples or attempts."""

        status_priority = {
            "running": 80,
            "validating": 70,
            "pending": 60,
            "collecting": 50,
            "succeeded": 40,
            "failed": 30,
            "cancelled": 20,
            "superseded": 10,
        }

        def recency(row: ParserTemplateJob) -> float:
            value = row.updated_at or row.created_at
            if value is None:
                return 0.0
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.timestamp()

        with get_db_session() as db:
            if not dry_run:
                db.execute(text("LOCK TABLE parser_template_jobs IN ACCESS EXCLUSIVE MODE"))
            rows = (
                db.query(ParserTemplateJob)
                .order_by(ParserTemplateJob.updated_at.desc(), ParserTemplateJob.id.desc())
                .all()
            )
            normalized_filter = (
                parser_job_asset_identity(
                    vendor=only_identity[0],
                    profile=only_identity[1],
                    command=only_identity[2],
                )
                if only_identity
                else None
            )
            if normalized_filter is not None:
                rows = [
                    row
                    for row in rows
                    if parser_job_asset_identity(
                        vendor=row.vendor,
                        profile=row.profile,
                        command=row.canonical_command,
                    )
                    == normalized_filter
                ]
            active_source_job_ids = {
                str(source_job_id)
                for (source_job_id,) in db.query(ParserTemplate.source_job_id)
                .filter(
                    ParserTemplate.status == "active",
                    ParserTemplate.source_job_id.is_not(None),
                )
                .all()
            }
            groups: dict[tuple[str, str, str], list[ParserTemplateJob]] = {}
            for row in rows:
                identity = parser_job_asset_identity(
                    vendor=row.vendor,
                    profile=row.profile,
                    command=row.canonical_command,
                )
                groups.setdefault(identity, []).append(row)

            duplicate_groups: list[dict[str, Any]] = []
            duplicate_ids: set[str] = set()
            for identity, group in groups.items():
                if len(group) < 2:
                    continue
                keeper = max(
                    group,
                    key=lambda row: (
                        row.status in OPEN_JOB_STATUSES,
                        status_priority.get(str(row.status), 0),
                        str(row.id) in active_source_job_ids,
                        recency(row),
                        str(row.id),
                    ),
                )
                removed = [str(row.id) for row in group if row.id != keeper.id]
                duplicate_ids.update(removed)
                duplicate_groups.append(
                    {
                        "vendor": identity[0],
                        "profile": identity[1],
                        "canonical_command": identity[2],
                        "keeper_job_id": str(keeper.id),
                        "deleted_job_ids": removed,
                    }
                )

            deletion_totals: dict[str, int] = {}
            if not dry_run:
                for row in rows:
                    if str(row.id) not in duplicate_ids:
                        continue
                    result = self._delete_job_graph(db, row)
                    for key, value in result.items():
                        deletion_totals[key] = deletion_totals.get(key, 0) + int(value)
                db.flush()
                for row in rows:
                    if str(row.id) in duplicate_ids:
                        continue
                    row.idempotency_key = parser_job_asset_key(
                        vendor=row.vendor,
                        profile=row.profile,
                        command=row.canonical_command,
                    )
                    row.updated_at = utcnow()

            return {
                "dry_run": bool(dry_run),
                "job_count_before": len(rows),
                "duplicate_group_count": len(duplicate_groups),
                "deleted_job_count": len(duplicate_ids) if not dry_run else 0,
                "would_delete_job_count": len(duplicate_ids),
                "migrated_sample_count": 0,
                "deletion_totals": deletion_totals,
                "groups": duplicate_groups,
            }

    def delete_job(
        self,
        job_id: str,
        *,
        actor: str = "system",
        reason: str = "system parser job deletion",
    ) -> dict[str, Any]:
        normalized_actor = str(actor or "").strip()
        normalized_reason = str(reason or "").strip()
        if not normalized_actor or not normalized_reason:
            raise ParserJobStateError("parser job deletion requires actor and reason")
        with get_db_session() as db:
            job = db.query(ParserTemplateJob).filter(ParserTemplateJob.id == job_id).first()
            if job is None:
                raise ParserJobStateError(f"parser job {job_id} not found")
            if job.status in OPEN_JOB_STATUSES:
                raise ParserJobStateError(f"parser job {job_id} must be cancelled before deletion")
            item = job.to_dict()
            db.add(
                AuditLogRecord(
                    id=str(uuid.uuid4()),
                    user_id=normalized_actor,
                    username=normalized_actor,
                    action="parser_job_delete",
                    resource_type="parser_template_job",
                    resource_id=job_id,
                    detail={
                        "reason": normalized_reason,
                        "job_type": job.job_type,
                        "status": job.status,
                        "asset_key": job.idempotency_key,
                    },
                    status="success",
                    created_at=utcnow(),
                )
            )
            return {"job": item, **self._delete_job_graph(db, job)}

    def delete_jobs(
        self,
        job_ids: list[str],
        *,
        actor: str = "system",
        reason: str = "system parser job bulk deletion",
    ) -> dict[str, Any]:
        deleted: list[dict[str, Any]] = []
        failed: list[dict[str, str]] = []
        for job_id in list(dict.fromkeys(job_ids)):
            try:
                deleted.append(
                    {
                        "job_id": job_id,
                        **self.delete_job(job_id, actor=actor, reason=reason),
                    }
                )
            except ParserJobStateError as exc:
                failed.append({"job_id": job_id, "error": str(exc)})
        return {
            "deleted_count": len(deleted),
            "failed_count": len(failed),
            "deleted": deleted,
            "failed": failed,
        }

    def set_parser_spec(
        self,
        job_id: str,
        *,
        parser_spec: dict[str, Any],
        confirmed: bool,
        actor: str,
        reason: str,
    ) -> dict[str, Any]:
        normalized_actor = str(actor or "").strip()
        normalized_reason = str(reason or "").strip()
        if not normalized_actor or not normalized_reason:
            raise ParserJobStateError("ParserSpec change requires actor and reason")
        try:
            normalized_spec = _normalize_parser_spec_payload(parser_spec)
        except ValueError as exc:
            raise ParserJobStateError(str(exc)) from exc
        digest = _parser_spec_digest(normalized_spec)
        with get_db_session() as db:
            job = (
                db.query(ParserTemplateJob)
                .filter(ParserTemplateJob.id == job_id)
                .with_for_update()
                .first()
            )
            if job is None:
                raise ParserJobStateError(f"parser job {job_id} not found")
            previous_digest = job.parser_spec_sha256
            try:
                binding = require_parser_check_item(db, job.id)
            except CheckItemBindingError as exc:
                raise ParserJobStateError(str(exc)) from exc
            job.agent_state_json = {**dict(job.agent_state_json or {}), "parser_spec_check_item": binding}
            previous_status = str(job.status)
            previous_spec_status = str(job.parser_spec_status or "missing")
            spec_changed = previous_digest != digest
            next_spec_status = "confirmed" if confirmed else "draft"
            now = utcnow()
            invalidated_template_ids: list[str] = []
            if spec_changed and previous_digest:
                previous_status, invalidated_template_ids = (
                    _invalidate_parser_job_for_spec_revision(
                        db,
                        job,
                        parser_spec_sha256=digest,
                        actor=normalized_actor,
                        reason=normalized_reason,
                        now=now,
                    )
                )
            job.pipeline_version = "simple_v2"
            job.parser_spec_json = normalized_spec
            job.parser_spec_sha256 = digest
            job.parser_spec_status = next_spec_status
            revision = None
            if spec_changed or previous_spec_status != next_spec_status:
                revision = _append_parser_spec_revision(
                    db,
                    job,
                    parser_spec=normalized_spec,
                    parser_spec_sha256=digest,
                    parser_spec_status=next_spec_status,
                    previous_parser_spec_sha256=previous_digest,
                    source="engineer",
                    actor=normalized_actor,
                    reason=normalized_reason,
                    invalidated_template_ids=invalidated_template_ids,
                    now=now,
                )
                state = dict(job.agent_state_json or {})
                state["parser_spec_revision"] = {
                    **dict(state.get("parser_spec_revision") or {}),
                    "revision_number": revision.revision_number,
                    "parser_spec_sha256": digest,
                    "source": "engineer",
                    "revised_at": now.isoformat(),
                }
                job.agent_state_json = state
            job.updated_at = now
            db.add(
                AuditLogRecord(
                    id=str(uuid.uuid4()),
                    user_id=normalized_actor,
                    username=normalized_actor,
                    action="parser_spec_confirm" if confirmed else "parser_spec_save_draft",
                    resource_type="parser_template_job",
                    resource_id=job_id,
                    detail={
                        "reason": normalized_reason,
                        "previous_sha256": previous_digest,
                        "parser_spec_sha256": digest,
                        "previous_job_status": previous_status,
                        "job_status": job.status,
                        "parser_spec_revision": int(job.parser_spec_revision or 0),
                        "invalidated_template_ids": invalidated_template_ids,
                    },
                    status="success",
                    created_at=now,
                )
            )
            db.flush()
            return job.to_dict()

    def queue_parser_spec_suggestion(
        self,
        job_id: str,
        *,
        task_id: str,
        actor: str,
        reason: str,
        revision_instruction: str = "",
        check_item_id: str = "",
        actor_user_id: str | None = None,
    ) -> dict[str, Any]:
        actor_user_id = normalize_actor_user_id(actor_user_id)
        normalized_actor = str(actor or "").strip()
        normalized_reason = str(reason or "").strip()
        normalized_instruction = str(revision_instruction or "").strip()[:4000]
        normalized_check_item_id = str(check_item_id or "").strip()[:128]
        if not normalized_actor or not normalized_reason:
            raise ParserJobStateError("ParserSpec suggestion requires actor and reason")
        with get_db_session() as db:
            job = (
                db.query(ParserTemplateJob)
                .filter(ParserTemplateJob.id == job_id)
                .with_for_update()
                .first()
            )
            if job is None:
                raise ParserJobStateError(f"parser job {job_id} not found")
            samples = (
                db.query(ParserJobSample)
                .filter(
                    ParserJobSample.job_id == job_id,
                    ParserJobSample.lifecycle_status == "current",
                )
                .all()
            )
            if not samples:
                raise ParserJobStateError("ParserSpec suggestion requires at least one sample")
            try:
                binding = require_parser_check_item(db, job.id, requested_id=normalized_check_item_id)
            except CheckItemBindingError as exc:
                raise ParserJobStateError(str(exc)) from exc
            normalized_check_item_id = binding["check_item_id"]
            if any(
                sample.sanitization_status != "sanitized" or sample.sanitized_text is None
                for sample in samples
            ):
                raise ParserJobStateError("ParserSpec suggestion requires sanitized samples")
            current = dict((job.agent_state_json or {}).get("parser_spec_suggestion") or {})
            if current.get("status") in {"queued", "running"} and not (
                _parser_spec_suggestion_is_stale(current)
            ):
                raise ParserJobStateError("ParserSpec suggestion is already running")
            now = utcnow()
            suggestion_timeout_seconds = max(
                1,
                int(get_settings().PARSER_MODEL_REQUEST_HARD_TIMEOUT_SECONDS),
            )
            state = dict(job.agent_state_json or {})
            state["parser_spec_suggestion"] = {
                "status": "queued",
                "stage": "semantic_queued",
                "task_id": task_id,
                "requested_by": normalized_actor,
                "requested_by_user_id": actor_user_id,
                "revision_instruction": normalized_instruction,
                "check_item_id": normalized_check_item_id or None,
                "check_item_binding": binding,
                "requested_at": now.isoformat(),
                "heartbeat_at": now.isoformat(),
                "deadline_at": (
                    now + timedelta(seconds=suggestion_timeout_seconds)
                ).isoformat(),
                "elapsed_ms": 0,
                "cancellable": True,
            }
            job.agent_state_json = state
            job.updated_at = now
            db.add(
                AuditLogRecord(
                    id=str(uuid.uuid4()),
                    user_id=actor_user_id,
                    username=normalized_actor,
                    action="parser_spec_suggestion_queued",
                    resource_type="parser_template_job",
                    resource_id=job_id,
                    detail={
                        "reason": normalized_reason,
                        "revision_instruction": normalized_instruction,
                        "check_item_id": normalized_check_item_id or None,
                        "task_id": task_id,
                    },
                    status="success",
                    created_at=now,
                )
            )
            db.flush()
            return job.to_dict()

    def claim_parser_spec_suggestion(self, job_id: str, *, task_id: str) -> dict[str, Any]:
        """Only one delivery may start the queued suggestion with this identity."""
        with get_db_session() as db:
            job = db.query(ParserTemplateJob).filter_by(id=job_id).with_for_update().first()
            if job is None:
                raise ParserJobStateError(f"parser job {job_id} not found")
            state = dict(job.agent_state_json or {})
            current = dict(state.get("parser_spec_suggestion") or {})
            if not task_id or current.get("task_id") != task_id or current.get("status") != "queued":
                raise ParserJobStateError("stale or already claimed ParserSpec suggestion task")
            now = utcnow()
            state["parser_spec_suggestion"] = {
                **current, "status": "running", "worker_claimed_at": now.isoformat(),
                "heartbeat_at": now.isoformat(), "updated_at": now.isoformat(),
            }
            job.agent_state_json = state
            job.updated_at = now
            db.flush()
            return job.to_dict()

    def record_parser_spec_suggestion(
        self,
        job_id: str,
        *,
        task_id: str,
        status: str,
        suggestion: dict[str, Any] | None = None,
        error: str | None = None,
        auto_adopt: bool = False,
    ) -> dict[str, Any]:
        normalized_status = str(status or "").strip().lower()
        if normalized_status not in {"running", "ready", "failed"}:
            raise ParserJobStateError(
                f"unsupported ParserSpec suggestion status: {normalized_status}"
            )
        normalized_suggestion = dict(suggestion or {})
        normalized_spec: dict[str, Any] | None = None
        normalized_spec_digest: str | None = None
        if normalized_status == "ready" and normalized_suggestion.get("parser_spec"):
            try:
                normalized_spec = _normalize_parser_spec_payload(
                    dict(normalized_suggestion["parser_spec"])
                )
            except ValueError as exc:
                raise ParserJobStateError(str(exc)) from exc
            normalized_suggestion["parser_spec"] = normalized_spec
            normalized_spec_digest = _parser_spec_digest(normalized_spec)
        with get_db_session() as db:
            job = (
                db.query(ParserTemplateJob)
                .filter(ParserTemplateJob.id == job_id)
                .with_for_update()
                .first()
            )
            if job is None:
                raise ParserJobStateError(f"parser job {job_id} not found")
            state = dict(job.agent_state_json or {})
            current = dict(state.get("parser_spec_suggestion") or {})
            if str(current.get("task_id") or "") != str(task_id or ""):
                raise ParserJobStateError("stale ParserSpec suggestion task")
            if current.get("status") not in {"queued", "running"}:
                raise ParserJobStateError("ParserSpec suggestion is already terminal")
            if normalized_status in {"running", "ready"}:
                try:
                    binding = require_parser_check_item(db, job.id)
                except CheckItemBindingError as exc:
                    raise ParserJobStateError(str(exc)) from exc
                if current.get("check_item_binding") != binding:
                    raise ParserJobStateError("检查项语义已变化，请重新生成字段方案")
            if normalized_status == "ready":
                from src.apps.rule_governance.backend.access import authorize
                authorize(current.get("requested_by_user_id"))
            now = utcnow()
            requested_at_raw = current.get("requested_at")
            requested_at: datetime | None = None
            try:
                requested_at = datetime.fromisoformat(str(requested_at_raw))
                if requested_at.tzinfo is None:
                    requested_at = requested_at.replace(tzinfo=timezone.utc)
                elapsed_ms = max(0, round((now - requested_at).total_seconds() * 1000))
            except (TypeError, ValueError):
                elapsed_ms = int(current.get("elapsed_ms") or 0)
            deadline_at = str(current.get("deadline_at") or "").strip() or None
            if deadline_at is None and requested_at is not None:
                suggestion_timeout_seconds = max(
                    1,
                    int(get_settings().PARSER_MODEL_REQUEST_HARD_TIMEOUT_SECONDS),
                )
                deadline_at = (
                    requested_at + timedelta(seconds=suggestion_timeout_seconds)
                ).isoformat()
            previous_spec_sha256 = job.parser_spec_sha256
            invalidated_template_ids: list[str] = []
            if (
                normalized_spec_digest is not None
                and previous_spec_sha256 is not None
                and normalized_spec_digest != previous_spec_sha256
            ):
                _, invalidated_template_ids = _invalidate_parser_job_for_spec_revision(
                    db,
                    job,
                    parser_spec_sha256=normalized_spec_digest,
                    actor="textfsm-generator",
                    reason="LLM ParserSpec re-analysis produced a revised specification",
                    now=now,
                    preserve_suggestion_task_id=task_id,
                )
                state = dict(job.agent_state_json or {})
                current = dict(state.get("parser_spec_suggestion") or {})
                if str(current.get("task_id") or "") != str(task_id or ""):
                    raise ParserJobStateError("stale ParserSpec suggestion task")
            state["parser_spec_suggestion"] = {
                **current,
                "status": normalized_status,
                "stage": {
                    "running": "semantic_model_analyzing",
                    "ready": "semantic_plan_ready",
                    "failed": "semantic_analysis_failed",
                }[normalized_status],
                "updated_at": now.isoformat(),
                "heartbeat_at": now.isoformat(),
                # A heartbeat reports liveness; it must never extend the original deadline.
                "deadline_at": deadline_at,
                "elapsed_ms": elapsed_ms,
                "cancellable": normalized_status == "running",
                **({"result": normalized_suggestion} if normalized_status == "ready" else {}),
                **(
                    {"error": str(error or "suggestion failed")}
                    if normalized_status == "failed"
                    else {}
                ),
            }
            job.agent_state_json = state
            if normalized_spec is not None:
                job.agent_state_json = {**state, "parser_spec_check_item": current["check_item_binding"]}
                job.pipeline_version = "simple_v2"
                job.parser_spec_json = normalized_spec
                job.parser_spec_sha256 = normalized_spec_digest
                job.parser_spec_status = "confirmed" if auto_adopt else "draft"
                if normalized_spec_digest != previous_spec_sha256:
                    revision = _append_parser_spec_revision(
                        db,
                        job,
                        parser_spec=normalized_spec,
                        parser_spec_sha256=str(normalized_spec_digest),
                        parser_spec_status=str(job.parser_spec_status),
                        previous_parser_spec_sha256=previous_spec_sha256,
                        source="llm_suggestion",
                        actor="textfsm-generator",
                        reason="LLM ParserSpec analysis produced a new semantic plan",
                        invalidated_template_ids=invalidated_template_ids,
                        now=now,
                    )
                    state = dict(job.agent_state_json or {})
                    state["parser_spec_revision"] = {
                        **dict(state.get("parser_spec_revision") or {}),
                        "revision_number": revision.revision_number,
                        "parser_spec_sha256": normalized_spec_digest,
                        "source": "llm_suggestion",
                        "revised_at": now.isoformat(),
                    }
                    job.agent_state_json = state
                if auto_adopt:
                    sample_suggestions = {
                        str(item.get("sample_id") or ""): item
                        for item in normalized_suggestion.get("sample_suggestions") or []
                        if isinstance(item, dict) and item.get("sample_id")
                    }
                    samples = (
                        db.query(ParserJobSample)
                        .filter(
                            ParserJobSample.job_id == job_id,
                            ParserJobSample.lifecycle_status == "current",
                        )
                        .with_for_update()
                        .all()
                    )
                    for sample in samples:
                        sample_suggestion = sample_suggestions.get(str(sample.id), {})
                        suggested_role = sample_suggestion.get("recommended_role")
                        role = (
                            _default_sample_role_from_content(sample.sanitized_text)
                            if suggested_role is None
                            else str(suggested_role).strip().lower()
                        )
                        if role not in {
                            "generation",
                            "acceptance",
                            "negative",
                            "unassigned",
                        }:
                            role = "unassigned"
                        sample.sample_role = role
                        # Pre-generation record counts and key sets are advisory only.
                        # The approved parse result becomes the durable baseline.
                        sample.expectation_json = None
                        sample.updated_at = now
            job.updated_at = now
            if normalized_status in {"ready", "failed"}:
                db.add(
                    AuditLogRecord(
                        id=str(uuid.uuid4()),
                        user_id="textfsm-generator",
                        username="textfsm-generator",
                        action=f"parser_spec_suggestion_{normalized_status}",
                        resource_type="parser_template_job",
                        resource_id=job_id,
                        detail={
                            "task_id": task_id,
                            "classification": normalized_suggestion.get("classification"),
                            "parser_spec_sha256": job.parser_spec_sha256,
                            "parser_spec_revision": int(job.parser_spec_revision or 0),
                            "auto_adopted": bool(auto_adopt and normalized_spec is not None),
                            "error": str(error or "") or None,
                        },
                        status="success" if normalized_status == "ready" else "failed",
                        created_at=utcnow(),
                    )
                )
            db.flush()
            return job.to_dict()

    def set_sample_role(
        self,
        job_id: str,
        sample_id: str,
        *,
        role: str,
        expectation: dict[str, Any] | None,
        actor: str,
        reason: str,
    ) -> dict[str, Any]:
        normalized_actor = str(actor or "").strip()
        normalized_reason = str(reason or "").strip()
        normalized_role = str(role or "").strip().lower()
        if not normalized_actor or not normalized_reason:
            raise ParserJobStateError("sample role change requires actor and reason")
        if normalized_role not in {"unassigned", "generation", "acceptance", "negative"}:
            raise ParserJobStateError(f"unsupported parser sample role: {normalized_role}")
        with get_db_session() as db:
            job = (
                db.query(ParserTemplateJob)
                .filter(ParserTemplateJob.id == job_id)
                .with_for_update()
                .first()
            )
            if job is None:
                raise ParserJobStateError(f"parser job {job_id} not found")
            if job.status in {"pending", "running", "validating", "succeeded", "superseded"}:
                raise ParserJobStateError(
                    f"sample roles cannot be changed while parser job is {job.status}"
                )
            sample = (
                db.query(ParserJobSample)
                .filter(
                    ParserJobSample.id == sample_id,
                    ParserJobSample.job_id == job_id,
                    ParserJobSample.lifecycle_status == "current",
                )
                .with_for_update()
                .first()
            )
            if sample is None:
                raise ParserJobStateError(f"parser sample {sample_id} not found in job {job_id}")
            primary_key_count = None
            if job.parser_spec_json:
                try:
                    document = ParserSpecDocument.from_dict(dict(job.parser_spec_json))
                    if not document.is_multi_record:
                        primary_key_count = len(document.single_record_set.primary_keys)
                except ValueError as exc:
                    raise ParserJobStateError(f"stored ParserSpec is invalid: {exc}") from exc
            normalized_expectation = _validated_sample_expectation(
                role=normalized_role,
                expectation=expectation,
                primary_key_count=primary_key_count,
            )
            previous_role = sample.sample_role
            previous_expectation = sample.expectation_json
            invalidated_template_ids: list[str] = []
            if (
                str(previous_role or "").lower() != normalized_role
                or _stable_json_digest(previous_expectation)
                != _stable_json_digest(normalized_expectation)
            ):
                invalidated_template_ids = _invalidate_parser_candidates_for_sample_revision(
                    db,
                    job,
                    actor=normalized_actor,
                    reason=(
                        "Parser sample role or expectation changed; regenerate the "
                        "candidate against the revised evidence contract"
                    ),
                    now=utcnow(),
                )
            sample.sample_role = normalized_role
            sample.expectation_json = normalized_expectation
            job.pipeline_version = "simple_v2"
            job.updated_at = utcnow()
            db.add(
                AuditLogRecord(
                    id=str(uuid.uuid4()),
                    user_id=normalized_actor,
                    username=normalized_actor,
                    action="parser_sample_role_change",
                    resource_type="parser_job_sample",
                    resource_id=sample_id,
                    detail={
                        "reason": normalized_reason,
                        "job_id": job_id,
                        "previous_role": previous_role,
                        "sample_role": normalized_role,
                        "expectation": normalized_expectation,
                        "invalidated_template_ids": invalidated_template_ids,
                    },
                    status="success",
                    created_at=utcnow(),
                )
            )
            db.flush()
            return sample.to_dict()

    def list_dispatchable_jobs(
        self, *, limit: int | None = None, job_id: str | None = None
    ) -> list[dict[str, Any]]:
        with get_db_session() as db:
            query = (
                db.query(ParserTemplateJob)
                .filter(ParserTemplateJob.status.in_(["pending", "collecting"]))
                .filter(ParserTemplateJob.celery_task_id.is_(None))
                .filter(ParserTemplateJob.pipeline_version == "simple_v2")
            )
            if job_id:
                query = query.filter(ParserTemplateJob.id == job_id)
            jobs = query.order_by(ParserTemplateJob.created_at).all()
            output: list[dict[str, Any]] = []
            for job in jobs:
                samples = (
                    db.query(ParserJobSample)
                    .filter(
                        ParserJobSample.job_id == job.id,
                        ParserJobSample.lifecycle_status == "current",
                    )
                    .all()
                )
                if not samples or any(
                    sample.sanitization_status != "sanitized" or not sample.sanitized_text
                    for sample in samples
                ):
                    continue
                if not _parser_generation_readiness(db, job, samples)["ready"]:
                    continue
                item = job.to_dict()
                item["samples"] = [sample.to_dict() for sample in samples]
                output.append(item)
                if limit and len(output) >= limit:
                    break
            return output

    def claim_pending_outbox(
        self, limit: int = 100, *, lease_seconds: int = 120
    ) -> list[ParserOutboxEvent]:
        now = utcnow()
        with get_db_session() as db:
            rows = (
                db.query(ParserOutboxEvent)
                .filter(
                    or_(
                        ParserOutboxEvent.status == "pending",
                        and_(
                            ParserOutboxEvent.status == "publishing",
                            ParserOutboxEvent.lease_until < now,
                        ),
                    )
                )
                .order_by(ParserOutboxEvent.created_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
                .all()
            )
            for row in rows:
                row.status = "publishing"
                row.claim_token = uuid.uuid4().hex
                row.lease_until = now + timedelta(seconds=max(30, int(lease_seconds)))
                row.attempts = int(row.attempts or 0) + 1
                row.last_error = None
            db.flush()
            for row in rows:
                db.expunge(row)
            return rows

    def mark_outbox_published(self, event_id: str, *, claim_token: str) -> None:
        with get_db_session() as db:
            updated = (
                db.query(ParserOutboxEvent)
                .filter(
                    ParserOutboxEvent.id == event_id,
                    ParserOutboxEvent.status == "publishing",
                    ParserOutboxEvent.claim_token == claim_token,
                )
                .update(
                    {
                        ParserOutboxEvent.status: "published",
                        ParserOutboxEvent.published_at: utcnow(),
                        ParserOutboxEvent.claim_token: None,
                        ParserOutboxEvent.lease_until: None,
                    },
                    synchronize_session=False,
                )
            )
            if updated != 1:
                raise ParserJobStateError(f"parser outbox claim was fenced: {event_id}")

    def release_outbox_claim(self, event_id: str, *, claim_token: str, error: str) -> None:
        with get_db_session() as db:
            db.query(ParserOutboxEvent).filter(
                ParserOutboxEvent.id == event_id,
                ParserOutboxEvent.status == "publishing",
                ParserOutboxEvent.claim_token == claim_token,
            ).update(
                {
                    ParserOutboxEvent.status: "pending",
                    ParserOutboxEvent.claim_token: None,
                    ParserOutboxEvent.lease_until: None,
                    ParserOutboxEvent.last_error: str(error)[:4000],
                },
                synchronize_session=False,
            )

    def list_jobs(self, *, status: str | None = None) -> list[dict[str, Any]]:
        with get_db_session() as db:
            query = db.query(ParserTemplateJob)
            if status:
                query = query.filter(ParserTemplateJob.status == status)
            rows = query.order_by(ParserTemplateJob.updated_at.desc()).all()
            job_ids = [row.id for row in rows]
            variant_counts = (
                dict(
                    db.query(
                        ParserJobSample.job_id,
                        func.count(func.distinct(ParserJobSample.output_signature)),
                    )
                    .filter(
                        ParserJobSample.job_id.in_(job_ids),
                        ParserJobSample.lifecycle_status == "current",
                    )
                    .group_by(ParserJobSample.job_id)
                    .all()
                )
                if job_ids
                else {}
            )
            retry_projections = _parser_job_retry_projections(db, job_ids)
            sample_rows_by_job: dict[str, list[ParserJobSample]] = {}
            if job_ids:
                for sample in (
                    db.query(ParserJobSample)
                    .filter(
                        ParserJobSample.job_id.in_(job_ids),
                        ParserJobSample.lifecycle_status == "current",
                    )
                    .all()
                ):
                    sample_rows_by_job.setdefault(sample.job_id, []).append(sample)
            return [
                _with_parser_job_retryability(
                    {
                        **row.to_dict(),
                        "structure_variant_count": int(variant_counts.get(row.id) or 0),
                        **(
                            {
                                "generation_readiness": _parser_generation_readiness(
                                    db,
                                    row,
                                    sample_rows_by_job.get(row.id, []),
                                )
                            }
                            if row.pipeline_version == "simple_v2"
                            else {}
                        ),
                    },
                    retry_projections.get(row.id),
                )
                for row in rows
            ]

    def backlog_summary(self) -> dict[str, int]:
        with get_db_session() as db:
            production_open = (
                db.query(func.count(ParserTemplateJob.id))
                .filter(
                    ParserTemplateJob.source_mode == "production",
                    ParserTemplateJob.status.in_(OPEN_JOB_STATUSES),
                )
                .scalar()
            )
            sample_counts = dict(
                db.query(
                    ParserJobSample.sanitization_status,
                    func.count(ParserJobSample.id),
                )
                .join(ParserTemplateJob, ParserTemplateJob.id == ParserJobSample.job_id)
                .filter(
                    ParserTemplateJob.source_mode == "production",
                    ParserTemplateJob.status.in_(OPEN_JOB_STATUSES),
                    ParserJobSample.lifecycle_status == "current",
                )
                .group_by(ParserJobSample.sanitization_status)
                .all()
            )
            return {
                "production_open_jobs": int(production_open or 0),
                "pending_sanitization_samples": int(sample_counts.get("pending_sanitization") or 0),
                "sanitized_samples": int(sample_counts.get("sanitized") or 0),
                "sanitization_failed_samples": int(sample_counts.get("failed") or 0),
            }

    def transition_job_status(
        self,
        job_id: str,
        status: str,
        error: str | None = None,
        *,
        failure_code: str | None = None,
        celery_task_id: str | None = None,
        clear_dispatch: bool = False,
        expected_status: str | None = None,
        expected_celery_task_id: str | None = None,
    ) -> dict[str, Any]:
        with get_db_session() as db:
            job = (
                db.query(ParserTemplateJob)
                .filter(ParserTemplateJob.id == job_id)
                .with_for_update()
                .one()
            )
            current = job.status
            if expected_status is not None and current != expected_status:
                raise ParserJobStateError(
                    f"parser job {job_id} expected {expected_status}, found {current}"
                )
            if expected_celery_task_id is not None and str(
                job.celery_task_id or ""
            ) != str(expected_celery_task_id):
                raise ParserJobStateError(
                    f"parser job {job_id} is assigned to a different Celery task"
                )
            if status != current and status not in JOB_TRANSITIONS.get(current, set()):
                raise ParserJobStateError(
                    f"parser job {job_id} cannot transition from {current} to {status}"
                )
            job.status = status
            job.last_error = error
            job.failure_code = failure_code
            job.updated_at = utcnow()
            if clear_dispatch:
                job.celery_task_id = None
                job.dispatched_at = None
                job.started_at = None
                job.completed_at = None
            elif celery_task_id:
                job.celery_task_id = celery_task_id
            if status == "running":
                job.attempts = int(job.attempts or 0) + 1
                job.started_at = utcnow()
            if status in TERMINAL_JOB_STATUSES:
                completed_at = utcnow()
                job.completed_at = completed_at
                terminal_reason = failure_code or status
                _abort_reserved_model_calls(
                    db,
                    job_id=job_id,
                    reason=terminal_reason,
                    completed_at=completed_at,
                )
                state = dict(job.agent_state_json or {})
                progress = dict(state.get("generation_progress") or {})
                progress.update(
                    {
                        "stage": status,
                        "heartbeat_at": completed_at.isoformat(),
                        "completed_at": completed_at.isoformat(),
                        "terminal_reason": terminal_reason,
                    }
                )
                state["generation_progress"] = progress
                job.agent_state_json = state
            db.flush()
            return job.to_dict()

    def update_generation_progress(
        self,
        job_id: str,
        *,
        generation_run_id: str,
        stage: str,
        attempt_number: int | None = None,
        deadline_at: str | None = None,
        request_deadline_at: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_stage = str(stage or "").strip()
        if normalized_stage not in GENERATION_PROGRESS_STAGES:
            raise ParserJobStateError(
                f"invalid Parser generation progress stage: {normalized_stage}"
            )
        normalized_run_id = str(generation_run_id or "").strip()
        if not normalized_run_id:
            raise ParserJobStateError("generation_run_id is required")
        with get_db_session() as db:
            job = (
                db.query(ParserTemplateJob)
                .filter(ParserTemplateJob.id == job_id)
                .with_for_update()
                .one_or_none()
            )
            if job is None:
                raise ParserJobStateError(f"parser job {job_id} not found")
            if job.status in TERMINAL_JOB_STATUSES:
                return job.to_dict()
            now = utcnow()
            state = dict(job.agent_state_json or {})
            previous = dict(state.get("generation_progress") or {})
            same_run = previous.get("generation_run_id") == normalized_run_id
            heartbeat_only = bool(
                same_run and dict(details or {}).get("heartbeat_only") is True
            )
            started_at = previous.get("started_at") if same_run else now.isoformat()
            effective_stage = (
                str(previous.get("stage") or normalized_stage)
                if heartbeat_only
                else normalized_stage
            )
            effective_attempt_number = (
                int(previous.get("attempt_number") or attempt_number or 1)
                if heartbeat_only
                else max(1, int(attempt_number or 1))
            )
            if heartbeat_only:
                effective_request_deadline = previous.get("request_deadline_at")
            elif normalized_stage in {"model_generating", "model_revising"}:
                effective_request_deadline = (
                    request_deadline_at or previous.get("request_deadline_at")
                )
            else:
                effective_request_deadline = request_deadline_at
            progress_details = {
                **dict(previous.get("details") or {}),
                **dict(details or {}),
            }
            if not heartbeat_only:
                progress_details.pop("heartbeat_only", None)
            progress = {
                **(previous if same_run else {}),
                "generation_run_id": normalized_run_id,
                "stage": effective_stage,
                "attempt_number": effective_attempt_number,
                "started_at": started_at,
                "heartbeat_at": now.isoformat(),
                "deadline_at": deadline_at or previous.get("deadline_at"),
                "request_deadline_at": effective_request_deadline,
                "cancellable": job.status in {"pending", "running", "validating"},
                "details": progress_details,
            }
            try:
                started = datetime.fromisoformat(str(started_at))
                if started.tzinfo is None:
                    started = started.replace(tzinfo=timezone.utc)
                progress["elapsed_ms"] = max(
                    0,
                    round((now - started).total_seconds() * 1000),
                )
            except (TypeError, ValueError):
                progress["elapsed_ms"] = 0
            state["generation_progress"] = progress
            job.agent_state_json = state
            job.agent_budget_json = {
                **dict(job.agent_budget_json or {}),
                "generation_run_id": normalized_run_id,
                "deadline_at": progress.get("deadline_at"),
                "request_deadline_at": progress.get("request_deadline_at"),
                "model_call_limit": progress["details"].get("model_call_limit"),
            }
            job.updated_at = now
            db.flush()
            return job.to_dict()

    def mark_job_dispatched(self, job_id: str, celery_task_id: str, *,
                            actor: str | None = None, actor_user_id: str | None = None) -> dict[str, Any]:
        actor_user_id = normalize_actor_user_id(actor_user_id)
        with get_db_session() as db:
            job = (
                db.query(ParserTemplateJob)
                .filter(ParserTemplateJob.id == job_id)
                .with_for_update()
                .one()
            )
            if job.status not in {"collecting", "pending"}:
                raise ParserJobStateError(
                    f"parser job {job_id} cannot be dispatched from {job.status}"
                )
            if job.celery_task_id:
                raise ParserJobStateError(
                    f"parser job {job_id} is already dispatched"
                )
            samples = (
                db.query(ParserJobSample)
                .filter(
                    ParserJobSample.job_id == job_id,
                    ParserJobSample.lifecycle_status == "current",
                )
                .order_by(ParserJobSample.created_at, ParserJobSample.id)
                .all()
            )
            readiness = _parser_generation_readiness(db, job, samples)
            if not readiness["ready"]:
                raise ParserJobStateError(
                    "parser job source or generation readiness changed before dispatch: "
                    + ", ".join(str(item) for item in readiness["blockers"])
                )
            job.status = "pending"
            job.celery_task_id = celery_task_id
            state = dict(job.agent_state_json or {})
            identity = dict(state.get("execution_identity") or {})
            # Beat may dispatch a human-requested retry. Reuse only the pending
            # request, never the actor of a previous dispatched execution.
            if actor is not None or actor_user_id is not None or identity.get("task_id"):
                identity = {"user_id": actor_user_id, "display": actor}
            identity.setdefault("user_id", None)
            identity.setdefault("display", None)
            identity.update(task_id=celery_task_id, dispatched_at=utcnow().isoformat())
            state["execution_identity"] = identity
            job.agent_state_json = state
            job.dispatched_at = utcnow()
            job.updated_at = utcnow()
            job.last_error = None
            job.failure_code = None
            db.flush()
            return job.to_dict()

    def fail_stale_jobs(
        self,
        *,
        pending_seconds: int | None = None,
        running_seconds: int | None = None,
    ) -> list[str]:
        settings = get_settings()
        if pending_seconds is None:
            pending_seconds = max(1, int(settings.PARSER_JOB_PENDING_STALE_MINUTES)) * 60
        if running_seconds is None:
            running_seconds = max(1, int(settings.PARSER_JOB_RUNNING_STALE_MINUTES)) * 60
        failed: list[str] = []
        with get_db_session() as db:
            now_utc, now_local = database_clocks(db)
            jobs = (
                db.query(ParserTemplateJob)
                .filter(ParserTemplateJob.status.in_(["pending", "running", "validating"]))
                .with_for_update(skip_locked=True)
                .all()
            )
            for job in jobs:
                reference = (
                    job.dispatched_at
                    if job.status == "pending"
                    else job.started_at or job.updated_at
                )
                if reference is None:
                    continue
                max_age = pending_seconds if job.status == "pending" else running_seconds
                if (
                    elapsed_seconds(
                        reference,
                        now_utc=now_utc,
                        now_local=now_local,
                    )
                    < max_age
                ):
                    continue
                previous_status = job.status
                job.status = "failed"
                job.failure_code = (
                    "dispatch_timeout" if previous_status == "pending" else "worker_timeout"
                )
                job.last_error = (
                    "parser job was not claimed by a worker before timeout"
                    if previous_status == "pending"
                    else "parser job exceeded the worker execution timeout"
                )
                job.updated_at = now_utc
                job.completed_at = now_utc
                _abort_reserved_model_calls(
                    db,
                    job_id=job.id,
                    reason=job.failure_code,
                    completed_at=now_utc,
                )
                state = dict(job.agent_state_json or {})
                progress = dict(state.get("generation_progress") or {})
                progress.update(
                    {
                        "stage": "failed",
                        "heartbeat_at": now_utc.isoformat(),
                        "completed_at": now_utc.isoformat(),
                        "terminal_reason": job.failure_code,
                    }
                )
                state["generation_progress"] = progress
                job.agent_state_json = state
                failed.append(job.id)
        return failed

    def finalize_durable_successes(
        self,
        *,
        grace_seconds: int = 60,
    ) -> list[str]:
        """Close jobs whose fully validated candidate survived a worker failure.

        Candidate persistence intentionally happens before the worker marks the
        job terminal. A process can therefore die in that small interval. The
        durable template validation is the source of truth for recovery; UI
        projections and ParserSpec serialization are not part of this decision.
        """

        finalized: list[str] = []
        with get_db_session() as db:
            now_utc, now_local = database_clocks(db)
            jobs = (
                db.query(ParserTemplateJob)
                .filter(
                    ParserTemplateJob.status.in_(["running", "validating"]),
                    ParserTemplateJob.best_attempt_id.isnot(None),
                )
                .with_for_update(skip_locked=True)
                .all()
            )
            for job in jobs:
                template = (
                    db.query(ParserTemplate)
                    .filter(
                        ParserTemplate.source_job_id == job.id,
                        ParserTemplate.status.in_(["pending_approval", "active"]),
                    )
                    .order_by(ParserTemplate.created_at.desc())
                    .first()
                )
                if template is None:
                    continue
                validation = dict(template.validation_json or {})
                simple = dict(validation.get("simple_parser") or {})
                regression = dict(validation.get("regression_validation") or {})
                if (
                    simple.get("status") != "candidate_ready"
                    or str(simple.get("best_attempt_id") or "") != str(job.best_attempt_id)
                    or regression.get("passed") is not True
                ):
                    continue
                reference = template.created_at or job.updated_at
                if reference is not None and elapsed_seconds(
                    reference,
                    now_utc=now_utc,
                    now_local=now_local,
                ) < max(0, int(grace_seconds)):
                    continue
                previous_status = job.status
                job.status = "succeeded"
                job.failure_code = None
                job.last_error = None
                job.updated_at = now_utc
                job.completed_at = now_utc
                db.add(
                    AuditLogRecord(
                        id=str(uuid.uuid4()),
                        user_id="system",
                        username="system",
                        action="parser_job_durable_success_reconciled",
                        resource_type="parser_template_job",
                        resource_id=job.id,
                        detail={
                            "previous_status": previous_status,
                            "template_id": template.id,
                            "best_attempt_id": job.best_attempt_id,
                        },
                        status="success",
                        created_at=utcnow(),
                    )
                )
                finalized.append(job.id)
        return finalized

    def get_job_lifecycle(self, job_id: str) -> dict[str, Any] | None:
        """Read worker lifecycle state without building control-plane projections."""

        with get_db_session() as db:
            job = db.query(ParserTemplateJob).filter(ParserTemplateJob.id == job_id).one_or_none()
            if job is None:
                return None
            return {
                "id": job.id,
                "status": job.status,
                "best_attempt_id": job.best_attempt_id,
                "failure_code": job.failure_code,
                "last_error": job.last_error,
                "celery_task_id": job.celery_task_id,
                "execution_identity": dict((job.agent_state_json or {}).get("execution_identity") or {}),
                "parser_spec_suggestion": dict((job.agent_state_json or {}).get("parser_spec_suggestion") or {}),
                "updated_at": job.updated_at,
            }

    def recover_stale_job(
        self,
        job_id: str,
        *,
        actor: str = "system",
        reason: str = "recover stale parser job",
    ) -> dict[str, Any]:
        """Fail one stuck open job so operators can retry/dispatch it again.

        This is the operator-facing escape hatch for jobs whose Celery worker
        died (or whose task was lost) after the job entered pending/running:
        the periodic reconcile task runs on the same worker pool, so it cannot
        help when that pool is down. The stale timeout keeps the normal 409
        guards intact for genuinely running jobs.
        """
        normalized_actor = str(actor or "").strip()
        normalized_reason = str(reason or "").strip()
        if not normalized_actor or not normalized_reason:
            raise ParserJobStateError("parser job recovery requires actor and reason")
        settings = get_settings()
        pending_seconds = max(1, int(settings.PARSER_JOB_PENDING_STALE_MINUTES)) * 60
        running_seconds = max(1, int(settings.PARSER_JOB_RUNNING_STALE_MINUTES)) * 60
        with get_db_session() as db:
            job = (
                db.query(ParserTemplateJob)
                .filter(ParserTemplateJob.id == job_id)
                .with_for_update()
                .one_or_none()
            )
            if job is None:
                raise ParserJobStateError(f"parser job {job_id} not found")
            if job.status not in {"pending", "running", "validating"}:
                raise ParserJobStateError(
                    f"parser job {job_id} is {job.status} and does not need recovery"
                )
            now_utc, now_local = database_clocks(db)
            reference = (
                (job.dispatched_at if job.status == "pending" else job.started_at)
                or job.updated_at
                or job.created_at
            )
            max_age = pending_seconds if job.status == "pending" else running_seconds
            if reference is not None:
                age = elapsed_seconds(reference, now_utc=now_utc, now_local=now_local)
                if age < max_age:
                    remaining = max(1, int(max_age - age))
                    raise ParserJobStateError(
                        f"parser job {job_id} is still {job.status} within the stale "
                        f"timeout; recovery is allowed in {remaining} seconds"
                    )
            previous_status = job.status
            stale_task_id = job.celery_task_id
            job.status = "failed"
            job.failure_code = "stale_running_recovered"
            job.last_error = (
                f"parser job was stuck in {previous_status} beyond the stale timeout "
                "and was recovered by an operator"
            )
            job.celery_task_id = None
            job.updated_at = now_utc
            job.completed_at = now_utc
            db.add(
                AuditLogRecord(
                    id=str(uuid.uuid4()),
                    user_id=normalized_actor,
                    username=normalized_actor,
                    action="parser_job_stale_recovered",
                    resource_type="parser_template_job",
                    resource_id=job_id,
                    detail={
                        "reason": normalized_reason,
                        "previous_status": previous_status,
                        "stale_celery_task_id": stale_task_id,
                        "failure_code": "stale_running_recovered",
                    },
                    status="success",
                    created_at=utcnow(),
                )
            )
            db.flush()
            item = job.to_dict()
            item["recovered_celery_task_id"] = stale_task_id
            return item

    def get_job(
        self,
        job_id: str,
        *,
        include_sanitized_payload: bool = False,
    ) -> dict[str, Any] | None:
        with get_db_session() as db:
            job = db.query(ParserTemplateJob).filter(ParserTemplateJob.id == job_id).first()
            if not job:
                return None
            result = _with_parser_job_retryability(
                job.to_dict(),
                _parser_job_retry_projections(db, [job_id]).get(job_id),
            )
            result["parser_spec_revisions"] = [
                revision.to_dict()
                for revision in (
                    db.query(ParserSpecRevisionRecord)
                    .filter(ParserSpecRevisionRecord.job_id == job_id)
                    .order_by(ParserSpecRevisionRecord.revision_number.desc())
                    .limit(20)
                    .all()
                )
            ]
            sample_rows = (
                db.query(ParserJobSample)
                .filter(
                    ParserJobSample.job_id == job_id,
                    ParserJobSample.lifecycle_status == "current",
                )
                .order_by(ParserJobSample.created_at)
                .all()
            )
            # The database trigger owns the durable counter. Projecting from the
            # rows here also keeps old/test databases truthful before migration.
            result["sample_count"] = len(sample_rows)
            result["retired_sample_count"] = int(
                db.query(func.count(ParserJobSample.id))
                .filter(
                    ParserJobSample.job_id == job_id,
                    ParserJobSample.lifecycle_status == "retired",
                )
                .scalar()
                or 0
            )
            snapshot_ids = [str(sample.snapshot_id) for sample in sample_rows]
            snapshot_contexts: dict[str, tuple[CommandSnapshot, PatrolRun]] = {}
            if snapshot_ids:
                snapshot_contexts = {
                    str(snapshot.id): (snapshot, run)
                    for snapshot, run in (
                        db.query(CommandSnapshot, PatrolRun)
                        .join(PatrolRun, PatrolRun.id == CommandSnapshot.run_id)
                        .filter(CommandSnapshot.id.in_(snapshot_ids))
                        .all()
                    )
                }
            samples: list[dict[str, Any]] = []
            for sample in sample_rows:
                item = sample.to_dict()
                sanitized_text = (
                    str(item.get("sanitized_text") or "")
                    if item.get("sanitization_status") == "sanitized"
                    else ""
                )
                if item.get("sanitization_status") != "sanitized":
                    item.pop("sanitized_text", None)
                item["content_classification"] = classify_parser_generation_sample(sanitized_text)
                item["sanitized_preview"] = sanitized_text[:320] + (
                    "..." if len(sanitized_text) > 320 else ""
                )
                context = snapshot_contexts.get(str(sample.snapshot_id))
                if context:
                    snapshot, run = context
                    snapshot_metadata = dict(snapshot.metadata_json or {})
                    run_metadata = dict(run.metadata_json or {})
                    item["source_context"] = {
                        "run_id": snapshot.run_id,
                        "offline_job_id": run_metadata.get("offline_job_id"),
                        "period_role": run_metadata.get("period_role"),
                        "source_file_name": _safe_source_file_name(snapshot_metadata),
                        "device_name": snapshot.device_name,
                        "management_ip": snapshot.management_ip,
                        "vendor": snapshot.vendor,
                        "model": snapshot.model,
                        "command": snapshot.command,
                        "canonical_command": snapshot.canonical_command,
                        "observed_at": snapshot.observed_at,
                        "parse_status": snapshot.parse_status,
                        "output_status": snapshot.output_status,
                        "start_line": snapshot.start_line,
                        "end_line": snapshot.end_line,
                    }
                samples.append(item)
            if not include_sanitized_payload:
                for sample in samples:
                    sample.pop("sanitized_text", None)
            result["samples"] = samples
            if job.pipeline_version == "simple_v2":
                result["generation_readiness"] = _parser_generation_readiness(
                    db,
                    job,
                    sample_rows,
                )
            else:
                result["generation_readiness"] = {
                    "ready": False,
                    "blockers": ["legacy_pipeline"],
                    "role_counts": {},
                }
            structure_signatures = sorted(
                {
                    str(sample.get("output_signature") or "")
                    for sample in samples
                    if str(sample.get("output_signature") or "")
                }
            )
            result["structure_variant_count"] = len(structure_signatures)
            result["structure_signatures"] = structure_signatures
            result["compatibility_scope"] = (
                "family"
                if job.family and str(job.profile).casefold() == str(job.family).casefold()
                else "model"
            )
            result["attempt_summary"] = {
                "total": int(
                    db.query(func.count(ParserGenerationAttempt.id))
                    .filter(ParserGenerationAttempt.job_id == job_id)
                    .scalar()
                    or 0
                ),
                "best_attempt_id": job.best_attempt_id,
                "contract_revisions": int(job.contract_revision_count or 0),
                "template_attempts": int(job.template_attempt_count or 0),
            }
            attempt_rows = (
                db.query(ParserGenerationAttempt)
                .filter(ParserGenerationAttempt.job_id == job_id)
                .order_by(ParserGenerationAttempt.attempt_number)
                .all()
            )
            result["generation_attempts"] = []
            for attempt in attempt_rows:
                attempt_data = attempt.to_dict()
                attempt_data.pop("template_text", None)
                result["generation_attempts"].append(attempt_data)
            model_call_rows = (
                db.query(ParserModelCallLedger)
                .filter(ParserModelCallLedger.job_id == job_id)
                .order_by(ParserModelCallLedger.call_number)
                .all()
            )
            result["model_call_ledger"] = [row.to_dict() for row in model_call_rows]
            return result

    def list_jobs_page(
        self,
        *,
        status: str | None = None,
        job_type: str | None = None,
        search: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        page_size = max(1, min(int(limit), 200))
        page_offset = max(0, int(offset))
        with get_db_session() as db:
            query = db.query(ParserTemplateJob)
            if status:
                query = query.filter(ParserTemplateJob.status == status)
            if job_type:
                query = query.filter(ParserTemplateJob.job_type == job_type)
            pattern = f"%{str(search).strip()}%" if str(search or "").strip() else None
            if pattern:
                query = query.filter(
                    or_(
                        ParserTemplateJob.id.ilike(pattern),
                        ParserTemplateJob.vendor.ilike(pattern),
                        ParserTemplateJob.model.ilike(pattern),
                        ParserTemplateJob.family.ilike(pattern),
                        ParserTemplateJob.profile.ilike(pattern),
                        ParserTemplateJob.canonical_command.ilike(pattern),
                    )
                )
            total = query.order_by(None).count()
            rows = (
                query.order_by(ParserTemplateJob.updated_at.desc(), ParserTemplateJob.id.desc())
                .offset(page_offset)
                .limit(page_size)
                .all()
            )
            job_ids = [row.id for row in rows]
            variant_counts = (
                dict(
                    db.query(
                        ParserJobSample.job_id,
                        func.count(func.distinct(ParserJobSample.output_signature)),
                    )
                    .filter(
                        ParserJobSample.job_id.in_(job_ids),
                        ParserJobSample.lifecycle_status == "current",
                    )
                    .group_by(ParserJobSample.job_id)
                    .all()
                )
                if job_ids
                else {}
            )
            retry_projections = _parser_job_retry_projections(db, job_ids)
            sample_rows_by_job: dict[str, list[ParserJobSample]] = {}
            if job_ids:
                for sample in (
                    db.query(ParserJobSample)
                    .filter(
                        ParserJobSample.job_id.in_(job_ids),
                        ParserJobSample.lifecycle_status == "current",
                    )
                    .all()
                ):
                    sample_rows_by_job.setdefault(sample.job_id, []).append(sample)
            items: list[dict[str, Any]] = []
            for row in rows:
                item = _with_parser_job_retryability(
                    row.to_dict(),
                    retry_projections.get(row.id),
                )
                item["structure_variant_count"] = int(variant_counts.get(row.id) or 0)
                if row.pipeline_version == "simple_v2":
                    item["generation_readiness"] = _parser_generation_readiness(
                        db,
                        row,
                        sample_rows_by_job.get(row.id, []),
                    )
                items.append(item)
            return {
                "items": items,
                "total": total,
                "limit": page_size,
                "offset": page_offset,
            }

    def job_status_counts(self) -> dict[str, int]:
        with get_db_session() as db:
            return {
                str(status): int(count)
                for status, count in db.query(
                    ParserTemplateJob.status,
                    func.count(ParserTemplateJob.id),
                )
                .group_by(ParserTemplateJob.status)
                .all()
            }

    def open_job_type_counts(self) -> dict[str, int]:
        with get_db_session() as db:
            return {
                str(job_type): int(count)
                for job_type, count in db.query(
                    ParserTemplateJob.job_type,
                    func.count(ParserTemplateJob.id),
                )
                .filter(ParserTemplateJob.status.in_(OPEN_JOB_STATUSES))
                .group_by(ParserTemplateJob.job_type)
                .all()
            }

    def create_generation_attempt(
        self,
        *,
        job_id: str,
        stage: str,
        action: str,
        status: str,
        textfsm_runtime_version: str,
        dialect_version: str,
        dialect_hash: str,
        validation_report: dict[str, Any],
        parent_attempt_id: str | None = None,
        model_provider: str | None = None,
        model_name: str | None = None,
        prompt_version: str | None = None,
        prompt_hash: str | None = None,
        contract_hash: str | None = None,
        train_sample_set_hash: str | None = None,
        holdout_sample_set_hash: str | None = None,
        template_text: str | None = None,
        failure_signature: str | None = None,
        candidate_rank: float | None = None,
        token_usage: dict[str, Any] | None = None,
        latency_ms: int | None = None,
    ) -> dict[str, Any]:
        if stage != "template":
            raise ValueError(f"unsupported parser generation stage: {stage}")
        with get_db_session() as db:
            job = (
                db.query(ParserTemplateJob)
                .filter(ParserTemplateJob.id == job_id)
                .with_for_update()
                .one_or_none()
            )
            if job is None:
                raise ParserJobStateError(f"parser job {job_id} not found")
            number = (
                int(
                    db.query(func.max(ParserGenerationAttempt.attempt_number))
                    .filter(ParserGenerationAttempt.job_id == job_id)
                    .scalar()
                    or 0
                )
                + 1
            )
            template_sha256 = (
                hashlib.sha256(template_text.encode("utf-8")).hexdigest()
                if template_text is not None
                else None
            )
            row = ParserGenerationAttempt(
                id=str(uuid.uuid4()),
                job_id=job_id,
                attempt_number=number,
                parent_attempt_id=parent_attempt_id,
                stage=stage,
                action=action,
                model_provider=model_provider,
                model_name=model_name,
                textfsm_runtime_version=textfsm_runtime_version,
                dialect_version=dialect_version,
                dialect_hash=dialect_hash,
                prompt_version=prompt_version,
                prompt_hash=prompt_hash,
                contract_hash=contract_hash,
                train_sample_set_hash=train_sample_set_hash,
                holdout_sample_set_hash=holdout_sample_set_hash,
                template_text=template_text,
                template_sha256=template_sha256,
                validation_report_json=dict(validation_report or {}),
                failure_signature=failure_signature,
                candidate_rank=candidate_rank,
                status=status,
                token_usage_json=dict(token_usage or {}) or None,
                latency_ms=latency_ms,
                created_at=utcnow(),
            )
            db.add(row)
            db.flush()
            job.template_attempt_count = int(job.template_attempt_count or 0) + 1

            current_best = (
                db.get(ParserGenerationAttempt, job.best_attempt_id)
                if job.best_attempt_id
                else None
            )
            if candidate_rank is not None and (
                current_best is None
                or current_best.candidate_rank is None
                or float(candidate_rank) > float(current_best.candidate_rank)
            ):
                job.best_attempt_id = row.id
            job.updated_at = utcnow()
            db.flush()
            return row.to_dict()

    def reserve_model_call(
        self,
        job_id: str,
        *,
        generation_run_id: str,
        max_calls: int,
        model_provider: str = "deepseek",
        protocol_version: str | None = None,
    ) -> dict[str, Any]:
        normalized_run_id = str(generation_run_id or "").strip()
        if not normalized_run_id:
            raise ParserJobStateError("generation_run_id is required")
        limit = max(1, int(max_calls))
        with get_db_session() as db:
            job = (
                db.query(ParserTemplateJob)
                .filter(ParserTemplateJob.id == job_id)
                .with_for_update()
                .one_or_none()
            )
            if job is None:
                raise ParserJobStateError(f"parser job {job_id} not found")
            if job.pipeline_version != "simple_v2":
                raise ParserJobStateError("model calls require a simple_v2 Parser job")
            used = int(job.model_calls_used or 0)
            if used >= limit:
                raise ParserJobStateError(
                    f"parser job model-call budget exhausted ({used}/{limit})"
                )
            run_call_number = used + 1
            call_number = (
                int(
                    db.query(func.max(ParserModelCallLedger.call_number))
                    .filter(ParserModelCallLedger.job_id == job_id)
                    .scalar()
                    or 0
                )
                + 1
            )
            job.model_calls_used = run_call_number
            job.updated_at = utcnow()
            row = ParserModelCallLedger(
                id=str(uuid.uuid4()),
                job_id=job_id,
                call_number=call_number,
                generation_run_id=normalized_run_id,
                status="reserved",
                model_provider=str(model_provider or "").strip() or None,
                protocol_version=str(protocol_version or "").strip() or None,
                result_json={},
                created_at=utcnow(),
            )
            db.add(row)
            db.flush()
            return {
                **row.to_dict(),
                "run_call_number": run_call_number,
            }

    def complete_model_call(
        self,
        call_id: str,
        *,
        status: str,
        model_name: str | None = None,
        candidate_sha256: str | None = None,
        failure_signature: str | None = None,
        result: dict[str, Any] | None = None,
        token_usage: dict[str, Any] | None = None,
        latency_ms: int | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        terminal_statuses = {
            "candidate_ready",
            "validation_failed",
            "acceptance_failed",
            "semantic_review_failed",
            "protocol_failed",
            "provider_failed",
            "review_requested",
        }
        normalized_status = str(status or "").strip()
        if normalized_status not in terminal_statuses:
            raise ParserJobStateError(f"invalid model-call terminal status: {status}")
        with get_db_session() as db:
            row = (
                db.query(ParserModelCallLedger)
                .filter(ParserModelCallLedger.id == call_id)
                .with_for_update()
                .one_or_none()
            )
            if row is None:
                raise ParserJobStateError(f"parser model call {call_id} not found")
            if row.status != "reserved":
                if row.status == normalized_status:
                    return row.to_dict()
                raise ParserJobStateError(f"parser model call {call_id} is already {row.status}")
            row.status = normalized_status
            row.model_name = str(model_name or "").strip() or None
            row.candidate_sha256 = str(candidate_sha256 or "").strip() or None
            row.failure_signature = str(failure_signature or "").strip() or None
            row.result_json = dict(result or {})
            row.token_usage_json = dict(token_usage or {}) or None
            row.latency_ms = int(latency_ms) if latency_ms is not None else None
            row.error = str(error or "").strip()[:2000] or None
            row.completed_at = utcnow()
            db.flush()
            return row.to_dict()

    def release_model_call_budget(self, call_id: str) -> dict[str, Any]:
        with get_db_session() as db:
            row = (
                db.query(ParserModelCallLedger)
                .filter(ParserModelCallLedger.id == call_id)
                .with_for_update()
                .one_or_none()
            )
            if row is None:
                raise ParserJobStateError(f"parser model call {call_id} not found")
            result = dict(row.result_json or {})
            if row.status != "provider_failed" or result.get("retryable") is not True:
                raise ParserJobStateError(
                    "only retryable provider failures can release model-call budget"
                )
            job = (
                db.query(ParserTemplateJob)
                .filter(ParserTemplateJob.id == row.job_id)
                .with_for_update()
                .one()
            )
            if result.get("budget_released") is not True:
                job.model_calls_used = max(0, int(job.model_calls_used or 0) - 1)
                job.updated_at = utcnow()
                result["budget_released"] = True
                row.result_json = result
                db.flush()
            return {
                **row.to_dict(),
                "model_calls_used": int(job.model_calls_used or 0),
            }

    def list_model_calls(self, job_id: str) -> list[dict[str, Any]]:
        with get_db_session() as db:
            rows = (
                db.query(ParserModelCallLedger)
                .filter(ParserModelCallLedger.job_id == job_id)
                .order_by(ParserModelCallLedger.call_number)
                .all()
            )
            return [row.to_dict() for row in rows]

    def list_generation_attempts(
        self,
        job_id: str,
        *,
        include_template: bool = False,
    ) -> list[dict[str, Any]]:
        with get_db_session() as db:
            rows = (
                db.query(ParserGenerationAttempt)
                .filter(ParserGenerationAttempt.job_id == job_id)
                .order_by(ParserGenerationAttempt.attempt_number)
                .all()
            )
            output = [row.to_dict() for row in rows]
            if not include_template:
                for item in output:
                    item.pop("template_text", None)
            return output

    def list_rejected_candidate_hashes(self, job_id: str) -> list[str]:
        """Return immutable contents explicitly rejected for this Parser job."""

        with get_db_session() as db:
            return [
                str(value)
                for (value,) in (
                    db.query(ParserTemplate.sha256)
                    .filter(
                        ParserTemplate.source_job_id == job_id,
                        ParserTemplate.status == "rejected",
                    )
                    .all()
                )
                if str(value or "").strip()
            ]

    def save_parse_result(
        self,
        *,
        snapshot_id: str,
        template_id: str,
        template_version: str,
        status: str,
        structured_data: Any,
        failure_code: str | None,
    ) -> bool:
        with get_db_session() as db:
            existing = (
                db.query(SnapshotParseResult)
                .filter(
                    SnapshotParseResult.snapshot_id == snapshot_id,
                    SnapshotParseResult.template_id == template_id,
                )
                .first()
            )
            if existing:
                return False
            db.add(
                SnapshotParseResult(
                    id=str(uuid.uuid4()),
                    snapshot_id=snapshot_id,
                    template_id=template_id,
                    template_version=template_version,
                    parse_status=status,
                    structured_json=structured_data,
                    failure_code=failure_code,
                    parsed_at=utcnow(),
                )
            )
            return True

    def get_latest_parse_result(self, snapshot_id: str) -> dict[str, Any] | None:
        with get_db_session() as db:
            row = (
                db.query(SnapshotParseResult, ParserTemplate)
                .join(ParserTemplate, ParserTemplate.id == SnapshotParseResult.template_id)
                .filter(
                    SnapshotParseResult.snapshot_id == snapshot_id,
                    SnapshotParseResult.parse_status.in_(["success", "empty"]),
                )
                .order_by(SnapshotParseResult.parsed_at.desc())
                .first()
            )
            if not row:
                return None
            result, template = row
            validation = dict(template.validation_json or {})
            return {
                "template_id": result.template_id,
                "template_version": result.template_version,
                "structured_json": result.structured_json,
                "entity_type": validation.get("entity_type") or template.category,
                "primary_keys": list(validation.get("primary_keys") or []),
            }

    def record_coverage_violation(
        self,
        *,
        snapshot_id: str,
        device_id: str,
        command: str,
        failure_code: str,
        template_id: str | None,
        template_version: str | None,
    ) -> None:
        with get_db_session() as db:
            self._add_outbox(
                db,
                event_type="parser.coverage.violation",
                aggregate_id=snapshot_id,
                payload={
                    "event_name": "ParserCoverageViolation",
                    "snapshot_id": snapshot_id,
                    "device_id": device_id,
                    "canonical_command": command,
                    "failure_code": failure_code,
                    "template_id": template_id,
                    "template_version": template_version,
                },
            )

    @staticmethod
    def _add_outbox(db, *, event_type: str, aggregate_id: str, payload: dict[str, Any]) -> None:
        add_outbox_row(
            db,
            event_type=event_type,
            aggregate_id=aggregate_id,
            payload=payload,
        )


class ParserTemplateRepository:
    """Template lifecycle aggregate with atomic activation and outbox writes."""

    backfill_contract_metadata = ParserRepository.backfill_contract_metadata
    resolve_active = ParserRepository.resolve_active
    register_template = ParserRepository.register_template
    activate_template = ParserRepository.activate_template
    record_semantic_review = ParserRepository.record_semantic_review
    rollback_template = ParserRepository.rollback_template
    list_templates = ParserRepository.list_templates
    list_templates_page = ParserRepository.list_templates_page
    template_status_counts = ParserRepository.template_status_counts
    get_template = ParserRepository.get_template
    get_candidate_sample_evidence = ParserRepository.get_candidate_sample_evidence
    find_template_version = ParserRepository.find_template_version
    has_trusted_family = ParserRepository.has_trusted_family
    set_template_status = ParserRepository.set_template_status
    record_regression_validation = ParserRepository.record_regression_validation
    _add_outbox = staticmethod(ParserRepository._add_outbox)


class ParserJobRepository:
    """Parser generation job aggregate, including candidate sample creation."""

    get_auto_dispatch_enabled = ParserRepository.get_auto_dispatch_enabled
    set_auto_dispatch_enabled = ParserRepository.set_auto_dispatch_enabled
    create_candidate = ParserRepository.create_candidate
    retire_non_authoritative_samples = ParserRepository.retire_non_authoritative_samples
    supersede_legacy_jobs = ParserRepository.supersede_legacy_jobs
    cancel_job = ParserRepository.cancel_job
    retry_job = ParserRepository.retry_job
    delete_job = ParserRepository.delete_job
    delete_jobs = ParserRepository.delete_jobs
    set_parser_spec = ParserRepository.set_parser_spec
    queue_parser_spec_suggestion = ParserRepository.queue_parser_spec_suggestion
    claim_parser_spec_suggestion = ParserRepository.claim_parser_spec_suggestion
    record_parser_spec_suggestion = ParserRepository.record_parser_spec_suggestion
    set_sample_role = ParserRepository.set_sample_role
    purge_duplicate_asset_jobs = ParserRepository.purge_duplicate_asset_jobs
    list_dispatchable_jobs = ParserRepository.list_dispatchable_jobs
    list_jobs = ParserRepository.list_jobs
    list_jobs_page = ParserRepository.list_jobs_page
    job_status_counts = ParserRepository.job_status_counts
    open_job_type_counts = ParserRepository.open_job_type_counts
    backlog_summary = ParserRepository.backlog_summary
    transition_job_status = ParserRepository.transition_job_status
    update_generation_progress = ParserRepository.update_generation_progress
    mark_job_dispatched = ParserRepository.mark_job_dispatched
    finalize_durable_successes = ParserRepository.finalize_durable_successes
    fail_stale_jobs = ParserRepository.fail_stale_jobs
    recover_stale_job = ParserRepository.recover_stale_job
    get_job_lifecycle = ParserRepository.get_job_lifecycle
    get_job = ParserRepository.get_job
    create_generation_attempt = ParserRepository.create_generation_attempt
    list_generation_attempts = ParserRepository.list_generation_attempts
    list_rejected_candidate_hashes = ParserRepository.list_rejected_candidate_hashes
    reserve_model_call = ParserRepository.reserve_model_call
    complete_model_call = ParserRepository.complete_model_call
    release_model_call_budget = ParserRepository.release_model_call_budget
    list_model_calls = ParserRepository.list_model_calls
    _delete_job_graph = staticmethod(ParserRepository._delete_job_graph)
    _add_outbox = staticmethod(ParserRepository._add_outbox)


class ParserSampleRepository:
    """Sanitization boundary for samples already owned by a parser job."""

    sanitize_pending_samples = ParserRepository.sanitize_pending_samples


class ParserParseResultRepository:
    """Runtime parse evidence and coverage violation persistence."""

    save_parse_result = ParserRepository.save_parse_result
    get_latest_parse_result = ParserRepository.get_latest_parse_result
    record_coverage_violation = ParserRepository.record_coverage_violation
    _add_outbox = staticmethod(ParserRepository._add_outbox)


class ParserMetadataRepository:
    """Command aliases and device identity metadata used by parser discovery."""

    list_command_aliases = ParserRepository.list_command_aliases
    upsert_command_alias = ParserRepository.upsert_command_alias
    list_device_profiles = ParserRepository.list_device_profiles
    find_device_profile = ParserRepository.find_device_profile
    upsert_device_profile = ParserRepository.upsert_device_profile
    list_identity_signatures = ParserRepository.list_identity_signatures
    register_identity_signature = ParserRepository.register_identity_signature


class ParserGovernanceProjectionRepository:
    """Cross-aggregate read projection for control-plane alert counters."""

    governance_alert_summary = ParserRepository.governance_alert_summary
