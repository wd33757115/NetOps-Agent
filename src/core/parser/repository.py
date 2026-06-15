"""PostgreSQL repository for parser control-plane state."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func

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
        )
    }
    encoded = json.dumps(
        contract, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class ParserRepository:
    def backfill_contract_metadata(self) -> int:
        count = 0
        with get_db_session() as db:
            rows = db.query(ParserTemplate).filter(
                ParserTemplate.contract_sha256.is_(None)
            ).all()
            for row in rows:
                validation = dict(row.validation_json or {})
                row.contract_version = int(
                    validation.get("contract_version") or 1
                )
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
            row = (
                db.query(ParserCommandAlias)
                .filter(ParserCommandAlias.alias == alias)
                .first()
            )
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
        raw_text: str,
        failure_code: str | None = None,
        dataset_id: str | None = None,
    ) -> str:
        context = PatrolRepository().get_snapshot_context(snapshot_id)
        if context is None:
            raise ValueError(f"parser candidate snapshot does not exist: {snapshot_id}")
        if context["mode"] != "parser_validation":
            raise ValueError(
                "parser template candidates must reference parser_validation snapshots"
            )
        snapshot = context["snapshot"]
        if snapshot.device_id != device_id:
            raise ValueError("parser candidate device does not match the snapshot")
        raw_text = snapshot.raw_text
        dataset_id = context["dataset_id"] or dataset_id
        signature = output_signature(raw_text)
        profile = model or family or "Generic"
        key_text = "|".join(
            ["parser_validation", job_type, vendor, profile, command, signature]
        )
        key = hashlib.sha256(key_text.encode("utf-8")).hexdigest()
        with get_db_session() as db:
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
            db.add(
                ParserTemplate(
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
            )
        return template_id

    def activate_template(self, template_id: str) -> ParserTemplate:
        with get_db_session() as db:
            template = db.query(ParserTemplate).filter(ParserTemplate.id == template_id).one()
            validation = dict(template.validation_json or {})
            regression = dict(validation.get("regression_validation") or {})
            if template.status != "active" and not regression.get("passed"):
                raise ValueError(
                    "template activation requires a passing regression validation set"
                )
            db.query(ParserTemplate).filter(
                ParserTemplate.vendor == template.vendor,
                ParserTemplate.profile == template.profile,
                ParserTemplate.canonical_command == template.canonical_command,
                ParserTemplate.status == "active",
                ParserTemplate.id != template.id,
            ).update({"status": "deprecated", "deprecated_at": utcnow()})
            db.flush()
            template.status = "active"
            validation["contract_approval_status"] = "approved"
            validation["contract_approved_at"] = utcnow().isoformat()
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
            self._add_outbox(
                db,
                event_type="parser.template.activated",
                aggregate_id=template.id,
                payload={"template_id": template.id},
            )
            db.flush()
            db.expunge(template)
            return template

    def supersede_legacy_jobs(self) -> int:
        """Retire jobs whose samples cannot be resolved from PostgreSQL."""
        count = 0
        with get_db_session() as db:
            jobs = db.query(ParserTemplateJob).filter(
                ParserTemplateJob.status != "superseded"
            ).all()
            for job in jobs:
                samples = (
                    db.query(ParserJobSample)
                    .filter(ParserJobSample.job_id == job.id)
                    .all()
                )
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

    def set_template_status(self, template_id: str, status: str) -> None:
        with get_db_session() as db:
            row = db.query(ParserTemplate).filter(ParserTemplate.id == template_id).one()
            row.status = status
            row.updated_at = utcnow()

    def record_regression_validation(
        self,
        template_id: str,
        *,
        passed: bool,
        sample_count: int,
        passed_samples: int,
    ) -> None:
        with get_db_session() as db:
            row = db.query(ParserTemplate).filter(
                ParserTemplate.id == template_id
            ).one()
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
                samples = (
                    db.query(ParserJobSample)
                    .filter(ParserJobSample.job_id == job.id)
                    .all()
                )
                recent_samples = [
                    sample
                    for sample in samples
                    if sample.created_at >= (now - timedelta(hours=24)).replace(tzinfo=None)
                ]
                age_seconds = (now - job.created_at.replace(tzinfo=timezone.utc)
                               if job.created_at.tzinfo is None
                               else now - job.created_at).total_seconds()
                ready = (
                    job.job_type == "CREATE_TEMPLATE"
                    and (len(samples) >= 2 or age_seconds >= 1800)
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

    def list_pending_outbox(self, limit: int = 100) -> list[ParserOutboxEvent]:
        with get_db_session() as db:
            rows = (
                db.query(ParserOutboxEvent)
                .filter(ParserOutboxEvent.status == "pending")
                .order_by(ParserOutboxEvent.created_at)
                .limit(limit)
                .all()
            )
            for row in rows:
                db.expunge(row)
            return rows

    def mark_outbox_published(self, event_id: str) -> None:
        with get_db_session() as db:
            row = db.query(ParserOutboxEvent).filter(ParserOutboxEvent.id == event_id).one()
            row.status = "published"
            row.published_at = utcnow()
            row.attempts = int(row.attempts or 0) + 1

    def list_jobs(self, *, status: str | None = None) -> list[dict[str, Any]]:
        with get_db_session() as db:
            query = db.query(ParserTemplateJob)
            if status:
                query = query.filter(ParserTemplateJob.status == status)
            rows = query.order_by(ParserTemplateJob.updated_at.desc()).all()
            return [row.to_dict() for row in rows]

    def transition_job_status(
        self,
        job_id: str,
        status: str,
        error: str | None = None,
        *,
        failure_code: str | None = None,
        celery_task_id: str | None = None,
        clear_dispatch: bool = False,
    ) -> dict[str, Any]:
        with get_db_session() as db:
            job = db.query(ParserTemplateJob).filter(ParserTemplateJob.id == job_id).one()
            current = job.status
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
            job = db.query(ParserTemplateJob).filter(ParserTemplateJob.id == job_id).one()
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
                job.status = "failed"
                job.failure_code = (
                    "dispatch_timeout" if job.status == "pending" else "worker_timeout"
                )
                job.last_error = (
                    "parser job was not claimed by a worker before timeout"
                    if job.status == "pending"
                    else "parser job exceeded the worker execution timeout"
                )
                job.updated_at = now
                job.completed_at = now
                failed.append(job.id)
        return failed

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with get_db_session() as db:
            job = db.query(ParserTemplateJob).filter(ParserTemplateJob.id == job_id).first()
            if not job:
                return None
            result = job.to_dict()
            result["samples"] = [
                sample.to_dict()
                for sample in db.query(ParserJobSample)
                .filter(ParserJobSample.job_id == job_id)
                .order_by(ParserJobSample.created_at)
                .all()
            ]
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
