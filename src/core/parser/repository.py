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
from src.core.parser.sanitization import sanitize_parser_sample
from src.core.patrol.repository import PatrolRepository
from src.infrastructure.db.models import (
    CommandSnapshot,
    ParserCommandAlias,
    ParserControlSetting,
    ParserDeviceProfile,
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
        dataset_id = context["dataset_id"] or dataset_id
        signature = output_signature(raw_text)
        profile = model or family or "Generic"
        key_parts = [source_mode, job_type, vendor, profile, command]
        if source_mode == "parser_validation":
            key_parts.append(signature)
        else:
            key_parts.append(str(failure_code or "parser_failure"))
        key_text = "|".join(key_parts)
        key = hashlib.sha256(key_text.encode("utf-8")).hexdigest()
        with get_db_session() as db:
            db.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": key},
            )
            job = (
                db.query(ParserTemplateJob)
                .filter(
                    ParserTemplateJob.idempotency_key == key,
                    ParserTemplateJob.status.in_(
                        ["pending", "collecting", "running", "validating"]
                    ),
                )
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
                    job.status = "superseded"
                    job.superseded_at = utcnow()
                    job.completed_at = utcnow()
                    job.updated_at = utcnow()
                    db.flush()
                    job = None
            if job is None:
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
                replaced = (
                    db.query(ParserTemplateJob)
                    .filter(
                        ParserTemplateJob.idempotency_key == key,
                        ParserTemplateJob.id != job.id,
                        ParserTemplateJob.status.in_(
                            ["succeeded", "failed", "cancelled", "superseded"]
                        ),
                    )
                    .all()
                )
                for old_job in replaced:
                    if old_job.status != "superseded":
                        old_job.status = "superseded"
                    old_job.superseded_by_job_id = job.id
                    old_job.superseded_at = utcnow()
                    old_job.updated_at = utcnow()
            exists = (
                db.query(ParserJobSample)
                .filter(
                    ParserJobSample.job_id == job.id,
                    ParserJobSample.snapshot_id == snapshot_id,
                )
                .first()
            )
            if exists is None:
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
                            "trusted-validation-v1" if source_mode == "parser_validation" else None
                        ),
                        sanitized_at=(utcnow() if source_mode == "parser_validation" else None),
                        created_at=utcnow(),
                    )
                )
                job.sample_count = int(job.sample_count or 0) + 1
                job.updated_at = utcnow()
            self._add_outbox(
                db,
                event_type="parser.candidate.discovered",
                aggregate_id=job.id,
                payload={"job_id": job.id, "job_type": job_type},
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
                    ParserTemplateJob.source_mode == "production",
                    ParserJobSample.sanitization_status == "pending_sanitization",
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
        template_id = str(uuid.uuid4())
        validation = dict(validation or {})
        digest = contract_digest(validation)
        with get_db_session() as db:
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
        """Retire jobs whose samples cannot be resolved from PostgreSQL."""
        count = 0
        with get_db_session() as db:
            jobs = (
                db.query(ParserTemplateJob).filter(ParserTemplateJob.status != "superseded").all()
            )
            for job in jobs:
                samples = db.query(ParserJobSample).filter(ParserJobSample.job_id == job.id).all()
                if not samples or not any(
                    sample.snapshot_source != "postgresql"
                    or db.get(CommandSnapshot, sample.snapshot_id) is None
                    for sample in samples
                ):
                    continue
                job.status = "superseded"
                job.superseded_at = utcnow()
                job.completed_at = job.completed_at or utcnow()
                job.updated_at = utcnow()
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

    def governance_alert_summary(self) -> dict[str, Any]:
        settings = get_settings()
        now = utcnow()
        stale_before = now - timedelta(minutes=max(1, settings.PARSER_CANDIDATE_STALE_MINUTES))
        recent_before = now - timedelta(hours=24)
        alerts: list[dict[str, Any]] = []
        with get_db_session() as db:
            production_open = db.query(func.count(ParserTemplateJob.id)).filter(
                ParserTemplateJob.source_mode == "production",
                ParserTemplateJob.status.in_(OPEN_JOB_STATUSES),
            ).scalar() or 0
            stale_candidates = db.query(func.count(ParserTemplateJob.id)).filter(
                ParserTemplateJob.source_mode == "production",
                ParserTemplateJob.status.in_(OPEN_JOB_STATUSES),
                ParserTemplateJob.updated_at < stale_before,
            ).scalar() or 0
            failed_generation = db.query(func.count(ParserTemplateJob.id)).filter(
                ParserTemplateJob.source_mode == "production",
                ParserTemplateJob.status == "failed",
                ParserTemplateJob.updated_at >= recent_before,
            ).scalar() or 0
            activation_publish_failures = db.query(func.count(ParserOutboxEvent.id)).filter(
                ParserOutboxEvent.event_type == "parser.template.activated",
                ParserOutboxEvent.status == "pending",
                ParserOutboxEvent.attempts > 0,
            ).scalar() or 0
            stale_approvals = db.query(func.count(ParserTemplate.id)).filter(
                ParserTemplate.status == "pending_approval",
                ParserTemplate.updated_at < stale_before,
            ).scalar() or 0
            recent_results = db.query(func.count(PatrolCollectionResult.id)).filter(
                PatrolCollectionResult.updated_at >= recent_before,
            ).scalar() or 0
            blocked_results = db.query(func.count(PatrolCollectionResult.id)).filter(
                PatrolCollectionResult.updated_at >= recent_before,
                PatrolCollectionResult.fact_status == "blocked_by_parser",
            ).scalar() or 0
            rule_jobs = db.query(RuleGenerationJobRecord).filter(
                RuleGenerationJobRecord.updated_at >= recent_before,
            ).all()

        blocked_ratio = float(blocked_results) / float(recent_results) if recent_results else 0.0
        rule_generation_failures = sum(
            item.status in {"failed", "validation_failed", "rejected"} for item in rule_jobs
        )
        replay_collisions = sum(
            int(((item.validation_report_json or {}).get("replay") or {}).get("collision_count") or 0)
            for item in rule_jobs
        )

        def add(code: str, count: int | float, severity: str, message: str) -> None:
            if count:
                alerts.append({"code": code, "count": count, "severity": severity, "message": message})

        if production_open >= max(1, settings.PARSER_BACKLOG_ALERT_THRESHOLD):
            add("parser_backlog_high", production_open, "warning", "Production Parser backlog exceeded its configured threshold.")
        add("parser_candidate_stale", stale_candidates, "warning", "Production Parser candidates exceeded the generation SLA.")
        add("parser_approval_stale", stale_approvals, "warning", "Generated Parser templates are waiting too long for approval.")
        add("parser_generation_failed", failed_generation, "error", "Production Parser generation failed in the last 24 hours.")
        add("template_activation_publish_failed", activation_publish_failures, "error", "Template activation outbox delivery is retrying.")
        if blocked_ratio >= settings.PARSER_BLOCKED_RATIO_ALERT_THRESHOLD and recent_results:
            add("blocked_by_parser_ratio_high", round(blocked_ratio, 4), "warning", "The 24-hour blocked-by-parser ratio exceeded its configured threshold.")
        add("rule_generation_failed", rule_generation_failures, "error", "Rule asset generation or validation failed in the last 24 hours.")
        add("rule_replay_collision", replay_collisions, "error", "Rule replay detected collisions; approval must remain blocked.")
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
        current = self.get_job(job_id)
        if not current:
            raise ParserJobStateError(f"parser job {job_id} not found")
        if current["status"] not in {"failed", "cancelled"}:
            raise ParserJobStateError(
                f"parser job {job_id} cannot be retried from {current['status']}"
            )
        return self.transition_job_status(job_id, "pending", clear_dispatch=True)

    def delete_job(self, job_id: str) -> dict[str, Any]:
        with get_db_session() as db:
            job = db.query(ParserTemplateJob).filter(ParserTemplateJob.id == job_id).first()
            if job is None:
                raise ParserJobStateError(f"parser job {job_id} not found")
            if job.status in OPEN_JOB_STATUSES:
                raise ParserJobStateError(f"parser job {job_id} must be cancelled before deletion")
            item = job.to_dict()
            sample_count = (
                db.query(ParserJobSample)
                .filter(ParserJobSample.job_id == job_id)
                .delete(synchronize_session=False)
            )
            db.delete(job)
            return {"job": item, "deleted_samples": int(sample_count or 0)}

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
                if job.source_mode == "production" and (
                    not samples
                    or any(sample.sanitization_status != "sanitized" for sample in samples)
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
            return [row.to_dict() for row in rows]

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
            return result

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
        db.add(
            ParserOutboxEvent(
                id=str(uuid.uuid4()),
                event_type=event_type,
                aggregate_id=aggregate_id,
                payload_json=payload,
                status="pending",
                created_at=utcnow(),
            )
        )
