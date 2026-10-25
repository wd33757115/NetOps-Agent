"""Production parser gap ledger: register missing templates without LLM."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from src.common.config import get_settings
from src.infrastructure.db.models import ParserGapLedgerRecord, ParserTemplateJob
from src.infrastructure.db.postgres import get_db_session


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ParserGapLedgerService:
    def record_gap(
        self,
        *,
        vendor: str,
        canonical_command: str,
        failure_code: str,
        model: str | None = None,
        family: str | None = None,
        run_id: str | None = None,
        device_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        settings = get_settings()
        if not settings.PRODUCTION_GAP_LEDGER_ENABLED:
            return None
        vendor_key = str(vendor or "").strip()
        command_key = str(canonical_command or "").strip()
        code = str(failure_code or "").strip() or "template_missing"
        if not vendor_key or not command_key:
            return None
        now = _now()
        with get_db_session() as db:
            row = (
                db.query(ParserGapLedgerRecord)
                .filter(
                    ParserGapLedgerRecord.vendor == vendor_key,
                    ParserGapLedgerRecord.canonical_command == command_key,
                    ParserGapLedgerRecord.failure_code == code,
                )
                .first()
            )
            if row is None:
                row = ParserGapLedgerRecord(
                    id=f"gap-{uuid.uuid4().hex[:20]}",
                    vendor=vendor_key,
                    model=model,
                    family=family,
                    canonical_command=command_key,
                    failure_code=code,
                    occurrence_count=1,
                    first_seen_at=now,
                    last_seen_at=now,
                    last_run_id=run_id,
                    last_device_id=device_id,
                    metadata_json=dict(metadata or {}),
                    created_at=now,
                    updated_at=now,
                )
                db.add(row)
            else:
                row.occurrence_count = int(row.occurrence_count or 0) + 1
                row.last_seen_at = now
                row.last_run_id = run_id or row.last_run_id
                row.last_device_id = device_id or row.last_device_id
                if model:
                    row.model = model
                if family:
                    row.family = family
                row.updated_at = now
            db.flush()
            return row.to_dict()

    def promote_to_draft_jobs(self) -> dict[str, Any]:
        """Create pending Parser jobs for gaps that crossed the threshold.

        Impact-weighted: gaps whose production_impact (device bindings × recent
        template_missing) reaches PRODUCTION_GAP_LEDGER_IMPACT_FAST_TRACK are
        promoted at the first occurrence; low-impact gaps still need
        PRODUCTION_GAP_LEDGER_JOB_THRESHOLD occurrences.
        """
        from src.apps.rule_governance.backend.parser.production_impact import (
            production_impact_for_pairs,
        )

        settings = get_settings()
        threshold = max(1, int(settings.PRODUCTION_GAP_LEDGER_JOB_THRESHOLD))
        fast_track = max(1, int(settings.PRODUCTION_GAP_LEDGER_IMPACT_FAST_TRACK))
        created: list[str] = []
        skipped: list[str] = []
        with get_db_session() as db:
            rows = (
                db.query(ParserGapLedgerRecord)
                .filter(
                    ParserGapLedgerRecord.occurrence_count >= 1,
                    ParserGapLedgerRecord.draft_job_id.is_(None),
                )
                .order_by(ParserGapLedgerRecord.occurrence_count.desc())
                .limit(500)
                .all()
            )
            pairs = {
                (
                    str(row.vendor or "").strip().lower(),
                    str(row.canonical_command or "").strip().lower(),
                )
                for row in rows
            }
            impact_scores = production_impact_for_pairs(pairs)
            promotable = []
            for row in rows:
                impact = impact_scores.get(
                    (
                        str(row.vendor or "").strip().lower(),
                        str(row.canonical_command or "").strip().lower(),
                    ),
                    0,
                )
                occurrences = int(row.occurrence_count or 0)
                if occurrences >= threshold or impact >= fast_track:
                    promotable.append((row, impact))
            for row, impact in promotable[:100]:
                job_id = f"prod-gap-{uuid.uuid4().hex[:16]}"
                job = ParserTemplateJob(
                    id=job_id,
                    job_type=(
                        "CREATE_TEMPLATE"
                        if row.failure_code == "template_missing"
                        else "REPAIR_TEMPLATE"
                    ),
                    source_mode="production_gap",
                    vendor=row.vendor,
                    model=row.model,
                    family=row.family,
                    profile=row.family or row.model or "unknown",
                    canonical_command=row.canonical_command,
                    output_signature=f"gap:{row.failure_code}",
                    idempotency_key=f"production_gap|{row.vendor}|{row.canonical_command}|{row.failure_code}",
                    status="pending",
                    sample_count=0,
                    failure_code=row.failure_code,
                    last_error=(
                        "Drafted from production gap ledger "
                        f"(occurrences={int(row.occurrence_count or 0)}, "
                        f"production_impact={impact}); "
                        "attach parser-validation samples before dispatch."
                    ),
                    created_at=_now().replace(tzinfo=None),
                    updated_at=_now().replace(tzinfo=None),
                )
                existing = (
                    db.query(ParserTemplateJob)
                    .filter(ParserTemplateJob.idempotency_key == job.idempotency_key)
                    .first()
                )
                if existing is not None:
                    row.draft_job_id = existing.id
                    skipped.append(existing.id)
                    continue
                db.add(job)
                row.draft_job_id = job_id
                row.updated_at = _now()
                created.append(job_id)
            db.flush()
        return {
            "success": True,
            "created_count": len(created),
            "skipped_count": len(skipped),
            "created_job_ids": created,
            "skipped_job_ids": skipped,
            "threshold": threshold,
            "impact_fast_track": fast_track,
        }
