"""PostgreSQL repository for parser control-plane state."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, func, or_, text

from src.common.config import get_settings
from src.core.observation import ObservationContractRepository
from src.core.parser.outbox_repository import add_outbox_row
from src.core.parser.sanitization import sanitize_parser_sample
from src.core.parser.textfsm_dialect import (
    DEFAULT_TEXTFSM_DIALECT,
    installed_textfsm_version,
)
from src.core.parser.textfsm_dialect import (
    dialect_hash as current_dialect_hash,
)
from src.core.patrol.repository import PatrolRepository
from src.infrastructure.db.models import (
    CommandSnapshot,
    ObservationContractRecord,
    ParserBackfillJobRecord,
    ParserCommandAlias,
    ParserControlSetting,
    ParserDeviceProfile,
    ParserGenerationAttempt,
    ParserIdentitySignature,
    ParserJobSample,
    ParserOutboxEvent,
    ParserTemplate,
    ParserTemplateJob,
    PatrolCollectionResult,
    RuleGenerationJobRecord,
    SnapshotParseResult,
)
from src.infrastructure.db.postgres import get_db_session

OPEN_JOB_STATUSES = {"pending", "collecting", "running", "validating"}
TERMINAL_JOB_STATUSES = {"succeeded", "failed", "cancelled", "superseded"}
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


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def output_signature(raw_text: str) -> str:
    normalized = re.sub(r"\b\d+(?:\.\d+){1,3}\b", "<IP>", raw_text or "")
    normalized = re.sub(r"\b\d+\b", "<N>", normalized)
    normalized = "\n".join(line.strip() for line in normalized.splitlines()[:40])
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]


def parser_job_asset_key(*, vendor: str, profile: str, command: str) -> str:
    """Return the stable identity of one target TextFSM template asset."""

    normalized = "|".join(
        (
            "parser-template-asset-v2",
            " ".join(str(vendor or "").strip().casefold().split()),
            " ".join(str(profile or "").strip().casefold().split()),
            " ".join(str(command or "").strip().casefold().split()),
        )
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def parser_job_asset_identity(*, vendor: str, profile: str, command: str) -> tuple[str, str, str]:
    """Return the normalized fields that identify one governed Parser asset."""

    return tuple(
        " ".join(str(value or "").strip().casefold().split())
        for value in (vendor, profile, command)
    )


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


def generated_template_activation_errors(
    validation: dict[str, Any],
    *,
    source_job_id: str | None,
) -> list[str]:
    """Return hard-gate failures for an Agent-generated Parser candidate."""

    agent = validation.get("parser_agent")
    if not isinstance(agent, dict):
        return []
    errors: list[str] = []
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


def _deprecate_rule_generation_seeds(
    db: Any,
    *,
    template_id: str,
    actor: str,
    reason: str,
) -> int:
    """Retire unpublished rule jobs when their Parser source is retired."""

    now = utcnow()
    rows = (
        db.query(RuleGenerationJobRecord)
        .filter(
            RuleGenerationJobRecord.source_ref == f"parser_template:{template_id}",
            RuleGenerationJobRecord.status.in_(
                {"draft", "validating", "pending_approval", "rejected"}
            ),
        )
        .with_for_update()
        .all()
    )
    for row in rows:
        validation = dict(row.validation_report_json or {})
        validation["source_asset_lifecycle"] = {
            "status": "deprecated",
            "actor": actor,
            "reason": reason,
            "at": now.isoformat(),
        }
        row.validation_report_json = validation
        row.status = "deprecated"
        row.updated_at = now
        row.completed_at = now
    return len(rows)


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
                ObservationContractRepository().upsert_from_parser_template_row(
                    db,
                    row,
                    status="active" if row.status == "active" else "draft",
                )
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

    def set_auto_dispatch_enabled(self, enabled: bool) -> bool:
        with get_db_session() as db:
            row = (
                db.query(ParserControlSetting)
                .filter(ParserControlSetting.key == "auto_dispatch_enabled")
                .first()
            )
            if row is None:
                row = ParserControlSetting(
                    key="auto_dispatch_enabled",
                    value_json={"enabled": bool(enabled)},
                )
                db.add(row)
            else:
                row.value_json = {"enabled": bool(enabled)}
                row.updated_at = utcnow()
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
            sanitize_parser_sample(raw_text)
            if source_mode == "parser_validation"
            else None
        )
        dataset_id = context["dataset_id"] or dataset_id
        signature = output_signature(raw_text)
        profile = str(profile or model or family or "Generic")
        allowed_profiles = {str(item) for item in (model, family, "Generic") if item}
        if profile not in allowed_profiles:
            raise ValueError("parser candidate profile must match the device model or family")
        key = parser_job_asset_key(vendor=vendor, profile=profile, command=command)
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
                    .filter(ParserJobSample.job_id == job.id)
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
                    and (
                        job.status in {"collecting", "pending"}
                        or existing_sample is None
                    )
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
                    failure_code=failure_code,
                    sample_count=0,
                    created_at=utcnow(),
                    updated_at=utcnow(),
                )
                db.add(job)
                db.flush()
            sample_added = existing_sample is None
            if sample_added:
                db.add(
                    ParserJobSample(
                        id=str(uuid.uuid4()),
                        job_id=job.id,
                        snapshot_id=snapshot_id,
                        snapshot_source="postgresql",
                        device_id=device_id,
                        output_signature=signature,
                        dataset_id=dataset_id,
                        sanitization_status=(
                            "sanitized"
                            if source_mode == "parser_validation"
                            else "pending_sanitization"
                        ),
                        sanitization_version=(
                            trusted_sample.version if trusted_sample else None
                        ),
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
                    or_(
                        ParserJobSample.sanitization_status == "pending_sanitization",
                        ParserJobSample.sanitized_text.is_(None),
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
        validation: dict[str, Any],
        source_job_id: str | None = None,
        status: str = "draft",
        contract_version: int | None = None,
    ) -> str:
        validation = dict(validation or {})
        digest = contract_digest(validation)
        with get_db_session() as db:
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
                    and (existing.source_job_id or None) == (source_job_id or None)
                )
                if same_registration:
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
            ObservationContractRepository().upsert_from_parser_template_row(
                db,
                template,
                status="active" if status == "active" else "draft",
            )
        return template_id

    def activate_template(
        self,
        template_id: str,
        *,
        approved_by: str = "manual-api",
        reason: str = "approved after parser validation",
        force: bool = False,
        approval_kind: str = "approval",
    ) -> ParserTemplate:
        if not str(approved_by).strip() or not str(reason).strip():
            raise ValueError("template activation requires an actor and reason")
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
            regression = dict(validation.get("regression_validation") or {})
            sample_count = int(regression.get("sample_count") or 0)
            passed_samples = int(regression.get("passed_samples") or 0)
            if template.status not in {"draft", "pending_approval", "deprecated"}:
                raise ValueError(f"template cannot be activated from status {template.status}")
            if not force and (
                not regression.get("passed") or sample_count <= 0 or passed_samples != sample_count
            ):
                raise ValueError(
                    "template activation requires a non-empty, fully passing regression set"
                )
            agent_gate_errors = generated_template_activation_errors(
                validation,
                source_job_id=template.source_job_id,
            )
            if not force and agent_gate_errors:
                raise ValueError(
                    "template activation blocked by Parser Agent governance: "
                    + "; ".join(agent_gate_errors)
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
                ObservationContractRepository().upsert_from_parser_template_row(
                    db,
                    old_template,
                    status="deprecated",
                )
                _deprecate_rule_generation_seeds(
                    db,
                    template_id=str(old_template.id),
                    actor=str(approved_by).strip(),
                    reason=(
                        f"parser template superseded by {template.id}: "
                        f"{str(reason).strip()}"
                    ),
                )
            db.flush()
            template.status = "active"
            validation["contract_approval_status"] = "approved"
            validation["contract_approved_at"] = utcnow().isoformat()
            validation["contract_approved_by"] = str(approved_by).strip()
            validation["contract_approval_reason"] = str(reason).strip()
            validation["contract_approval_kind"] = approval_kind
            validation["contract_force_activated"] = bool(force)
            validation["approval_audit"] = [
                *list(validation.get("approval_audit") or []),
                {
                    "action": approval_kind,
                    "actor": str(approved_by).strip(),
                    "reason": str(reason).strip(),
                    "occurred_at": utcnow().isoformat(),
                },
            ]
            template.validation_json = validation
            template.activated_at = utcnow()
            template.updated_at = utcnow()
            ObservationContractRepository().upsert_from_parser_template_row(
                db,
                template,
                status="active",
            )
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
            self._add_outbox(
                db,
                event_type="parser.template.activated",
                aggregate_id=template.id,
                payload={"template_id": template.id},
            )
            db.flush()
            db.expunge(template)
            return template

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
                samples = db.query(ParserJobSample).filter(ParserJobSample.job_id == job.id).all()
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

    def get_template(self, template_id: str) -> ParserTemplate | None:
        with get_db_session() as db:
            row = db.query(ParserTemplate).filter(ParserTemplate.id == template_id).first()
            if row:
                db.expunge(row)
            return row

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
            ObservationContractRepository().upsert_from_parser_template_row(
                db,
                row,
                status="active" if status == "active" else "deprecated",
            )
            if status in {"deprecated", "rejected"}:
                _deprecate_rule_generation_seeds(
                    db,
                    template_id=str(row.id),
                    actor=str(actor or "parser-control-plane"),
                    reason=str(reason or f"parser template marked {status}"),
                )

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
                item.status in {"failed", "validation_failed", "rejected"}
                for item in rule_jobs
            )
            replay_collisions = sum(
                int(
                    ((item.validation_report_json or {}).get("replay") or {}).get(
                        "collision_count"
                    )
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

    def retry_job(self, job_id: str) -> dict[str, Any]:
        with get_db_session() as db:
            job = (
                db.query(ParserTemplateJob)
                .filter(ParserTemplateJob.id == job_id)
                .with_for_update()
                .one_or_none()
            )
            if job is None:
                raise ParserJobStateError(f"parser job {job_id} not found")
            retryable_status = job.status in {"failed", "cancelled"}
            if job.status == "succeeded":
                generated_templates = (
                    db.query(ParserTemplate)
                    .filter(ParserTemplate.source_job_id == job_id)
                    .with_for_update()
                    .all()
                )
                retryable_status = bool(generated_templates) and all(
                    template.status in {"deprecated", "rejected"}
                    for template in generated_templates
                )
                if not retryable_status:
                    raise ParserJobStateError(
                        "a succeeded parser job can be retried only after every "
                        "generated candidate is deprecated or rejected"
                    )
            if not retryable_status:
                raise ParserJobStateError(
                    f"parser job {job_id} cannot be retried from {job.status}"
                )
            job.status = "pending"
            job.last_error = None
            job.failure_code = None
            job.celery_task_id = None
            job.dispatched_at = None
            job.started_at = None
            job.completed_at = None
            # Explicit retry starts a fresh Agent loop. Immutable attempts remain
            # queryable as history and the next run continues their sequence.
            job.agent_state_json = None
            job.agent_budget_json = None
            job.best_attempt_id = None
            job.contract_revision_count = 0
            job.template_attempt_count = 0
            job.updated_at = utcnow()
            db.flush()
            return job.to_dict()

    @staticmethod
    def _delete_job_graph(db: Any, job: ParserTemplateJob) -> dict[str, int]:
        """Hard-delete one Parser task and its non-runtime historical artifacts."""

        job_id = str(job.id)
        template_rows = (
            db.query(ParserTemplate)
            .filter(ParserTemplate.source_job_id == job_id)
            .all()
        )
        active_templates = [row for row in template_rows if row.status == "active"]
        historical_templates = [row for row in template_rows if row.status != "active"]
        historical_template_ids = [str(row.id) for row in historical_templates]

        for template in active_templates:
            template.source_job_id = None
            template.updated_at = utcnow()
        deleted_rule_jobs = 0
        deleted_contracts = 0
        deleted_backfills = 0
        deleted_parse_results = 0
        contract_filters = [ObservationContractRecord.source_job_id == job_id]
        if historical_template_ids:
            contract_filters.append(
                ObservationContractRecord.source_template_id.in_(
                    historical_template_ids
                )
            )
        historical_contracts = (
            db.query(ObservationContractRecord)
            .filter(or_(*contract_filters))
            .all()
        )
        historical_template_id_set = set(historical_template_ids)
        for contract in historical_contracts:
            if contract.status == "active":
                if contract.source_job_id == job_id:
                    contract.source_job_id = None
                if str(contract.source_template_id or "") in historical_template_id_set:
                    contract.source_template_id = None
                contract.updated_at = utcnow()
            else:
                db.delete(contract)
                deleted_contracts += 1

        if historical_template_ids:
            source_refs = [
                f"parser_template:{template_id}"
                for template_id in historical_template_ids
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
            "deleted_contracts": deleted_contracts,
            "deleted_backfills": deleted_backfills,
            "deleted_parse_results": deleted_parse_results,
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

    def delete_job(self, job_id: str) -> dict[str, Any]:
        with get_db_session() as db:
            job = db.query(ParserTemplateJob).filter(ParserTemplateJob.id == job_id).first()
            if job is None:
                raise ParserJobStateError(f"parser job {job_id} not found")
            if job.status in OPEN_JOB_STATUSES:
                raise ParserJobStateError(f"parser job {job_id} must be cancelled before deletion")
            item = job.to_dict()
            return {"job": item, **self._delete_job_graph(db, job)}

    def delete_jobs(self, job_ids: list[str]) -> dict[str, Any]:
        deleted: list[dict[str, Any]] = []
        failed: list[dict[str, str]] = []
        for job_id in list(dict.fromkeys(job_ids)):
            try:
                deleted.append({"job_id": job_id, **self.delete_job(job_id)})
            except ParserJobStateError as exc:
                failed.append({"job_id": job_id, "error": str(exc)})
        return {
            "deleted_count": len(deleted),
            "failed_count": len(failed),
            "deleted": deleted,
            "failed": failed,
        }

    def list_dispatchable_jobs(
        self, *, limit: int | None = None, job_id: str | None = None
    ) -> list[dict[str, Any]]:
        now = utcnow()
        with get_db_session() as db:
            query = (
                db.query(ParserTemplateJob)
                .filter(ParserTemplateJob.status.in_(["pending", "collecting"]))
                .filter(ParserTemplateJob.celery_task_id.is_(None))
            )
            if job_id:
                query = query.filter(ParserTemplateJob.id == job_id)
            jobs = query.order_by(ParserTemplateJob.created_at).all()
            output: list[dict[str, Any]] = []
            for job in jobs:
                samples = db.query(ParserJobSample).filter(ParserJobSample.job_id == job.id).all()
                if not samples or any(
                    sample.sanitization_status != "sanitized" or not sample.sanitized_text
                    for sample in samples
                ):
                    continue
                recent_samples = [
                    sample
                    for sample in samples
                    if sample.created_at >= (now - timedelta(hours=24)).replace(tzinfo=None)
                ]
                age_seconds = (
                    now - job.created_at.replace(tzinfo=timezone.utc)
                    if job.created_at.tzinfo is None
                    else now - job.created_at
                ).total_seconds()
                ready = (
                    job.job_type == "CREATE_TEMPLATE" and (len(samples) >= 2 or age_seconds >= 1800)
                ) or (
                    job.job_type == "REPAIR_TEMPLATE"
                    and (
                        len({sample.snapshot_id for sample in recent_samples}) >= 3
                        or len({sample.device_id for sample in recent_samples}) >= 2
                    )
                )
                if ready:
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
                    .filter(ParserJobSample.job_id.in_(job_ids))
                    .group_by(ParserJobSample.job_id)
                    .all()
                )
                if job_ids
                else {}
            )
            return [
                {
                    **row.to_dict(),
                    "structure_variant_count": int(variant_counts.get(row.id) or 0),
                }
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
                job.completed_at = utcnow()
            db.flush()
            return job.to_dict()

    def mark_job_dispatched(self, job_id: str, celery_task_id: str) -> dict[str, Any]:
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
            job.status = "pending"
            job.celery_task_id = celery_task_id
            job.dispatched_at = utcnow()
            job.updated_at = utcnow()
            job.last_error = None
            job.failure_code = None
            db.flush()
            return job.to_dict()

    def fail_stale_jobs(
        self, *, pending_seconds: int = 600, running_seconds: int = 1800
    ) -> list[str]:
        now = utcnow()
        failed: list[str] = []
        with get_db_session() as db:
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
                reference_utc = (
                    reference.replace(tzinfo=timezone.utc)
                    if reference.tzinfo is None
                    else reference
                )
                max_age = pending_seconds if job.status == "pending" else running_seconds
                if (now - reference_utc).total_seconds() < max_age:
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
                job.updated_at = now
                job.completed_at = now
                failed.append(job.id)
        return failed

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
            result = job.to_dict()
            samples = [
                sample.to_dict()
                for sample in db.query(ParserJobSample)
                .filter(ParserJobSample.job_id == job_id)
                .order_by(ParserJobSample.created_at)
                .all()
            ]
            if not include_sanitized_payload:
                for sample in samples:
                    sample.pop("sanitized_text", None)
            result["samples"] = samples
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
            return result

    def get_generation_resume_state(self, job_id: str) -> dict[str, Any]:
        """Load an internal Agent checkpoint and its best candidate template."""

        with get_db_session() as db:
            job = db.get(ParserTemplateJob, job_id)
            if job is None:
                raise ParserJobStateError(f"parser job {job_id} not found")
            if not isinstance(job.agent_state_json, dict) or not job.agent_state_json:
                return {}
            state = dict(job.agent_state_json)
            state["job_id"] = job_id
            state.setdefault("thread_id", f"parser-template:{job_id}")
            best_attempt_id = state.get("best_attempt_id") or job.best_attempt_id
            if best_attempt_id:
                best = db.get(ParserGenerationAttempt, best_attempt_id)
                if best is not None and best.job_id == job_id and best.template_text:
                    state["best_attempt_id"] = best.id
                    state["best_template"] = best.template_text
                    state["best_rank"] = float(best.candidate_rank or -1.0)
            if not state.get("parent_attempt_id"):
                latest = (
                    db.query(ParserGenerationAttempt)
                    .filter(ParserGenerationAttempt.job_id == job_id)
                    .order_by(ParserGenerationAttempt.attempt_number.desc())
                    .first()
                )
                if latest is not None:
                    state["parent_attempt_id"] = latest.id
            return state

    def get_latest_valid_contract_attempt(
        self,
        job_id: str,
        *,
        train_sample_set_hash: str,
        holdout_sample_set_hash: str,
    ) -> dict[str, Any] | None:
        """Return the latest immutable Contract valid for the same corpus."""

        with get_db_session() as db:
            rows = (
                db.query(ParserGenerationAttempt)
                .filter(
                    ParserGenerationAttempt.job_id == job_id,
                    ParserGenerationAttempt.stage == "contract",
                    ParserGenerationAttempt.status == "passed",
                    ParserGenerationAttempt.train_sample_set_hash
                    == train_sample_set_hash,
                    ParserGenerationAttempt.holdout_sample_set_hash
                    == holdout_sample_set_hash,
                )
                .order_by(ParserGenerationAttempt.attempt_number.desc())
                .all()
            )
            for row in rows:
                report = dict(row.validation_report_json or {})
                contract = report.get("contract")
                if report.get("valid") is True and isinstance(contract, dict) and contract:
                    return {
                        "attempt_id": str(row.id),
                        "attempt_number": int(row.attempt_number),
                        "contract_hash": row.contract_hash,
                        "contract": dict(contract),
                    }
            return None

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
        if stage not in {"contract", "template", "validation", "holdout", "regression"}:
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
            number = int(
                db.query(func.max(ParserGenerationAttempt.attempt_number))
                .filter(ParserGenerationAttempt.job_id == job_id)
                .scalar()
                or 0
            ) + 1
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
            if stage == "contract":
                job.contract_revision_count = int(job.contract_revision_count or 0) + 1
            if stage in {"template", "validation", "holdout", "regression"}:
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

    def save_agent_checkpoint(
        self,
        job_id: str,
        *,
        state: dict[str, Any],
        budget: dict[str, Any] | None = None,
        best_attempt_id: str | None = None,
    ) -> dict[str, Any]:
        with get_db_session() as db:
            job = (
                db.query(ParserTemplateJob)
                .filter(ParserTemplateJob.id == job_id)
                .with_for_update()
                .one_or_none()
            )
            if job is None:
                raise ParserJobStateError(f"parser job {job_id} not found")
            job.agent_state_json = dict(state or {})
            job.agent_budget_json = dict(budget or {}) or job.agent_budget_json
            if best_attempt_id:
                best = db.get(ParserGenerationAttempt, best_attempt_id)
                if best is None or best.job_id != job_id:
                    raise ParserJobStateError("best parser attempt does not belong to the job")
                job.best_attempt_id = best_attempt_id
            job.updated_at = utcnow()
            db.flush()
            return job.to_dict()

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
    rollback_template = ParserRepository.rollback_template
    list_templates = ParserRepository.list_templates
    get_template = ParserRepository.get_template
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
    supersede_legacy_jobs = ParserRepository.supersede_legacy_jobs
    cancel_job = ParserRepository.cancel_job
    retry_job = ParserRepository.retry_job
    delete_job = ParserRepository.delete_job
    delete_jobs = ParserRepository.delete_jobs
    purge_duplicate_asset_jobs = ParserRepository.purge_duplicate_asset_jobs
    list_dispatchable_jobs = ParserRepository.list_dispatchable_jobs
    list_jobs = ParserRepository.list_jobs
    backlog_summary = ParserRepository.backlog_summary
    transition_job_status = ParserRepository.transition_job_status
    mark_job_dispatched = ParserRepository.mark_job_dispatched
    fail_stale_jobs = ParserRepository.fail_stale_jobs
    get_job = ParserRepository.get_job
    get_generation_resume_state = ParserRepository.get_generation_resume_state
    get_latest_valid_contract_attempt = ParserRepository.get_latest_valid_contract_attempt
    create_generation_attempt = ParserRepository.create_generation_attempt
    save_agent_checkpoint = ParserRepository.save_agent_checkpoint
    list_generation_attempts = ParserRepository.list_generation_attempts
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
