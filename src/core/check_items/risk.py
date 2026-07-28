"""RiskCase projection with append-only occurrences and explicit recovery evidence."""

from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from sqlalchemy import case as sql_case
from sqlalchemy import func, or_

from src.infrastructure.db.models import (
    CheckItemRecord,
    CurrentStateFindingRecord,
    NetworkDevice,
    RiskCaseRecord,
    RiskOccurrenceRecord,
    RiskRemediationRecord,
)
from src.infrastructure.db.postgres import get_db_session

_SEVERITY_RANK = {
    "info": 0,
    "warning": 1,
    "minor": 2,
    "major": 3,
    "critical": 4,
}


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _correlation_key(
    *,
    check_item_id: str,
    device_id: str,
    entity_type: str,
    entity_key: str,
    dimensions: dict[str, Any],
    risk_type: str,
) -> str:
    digest = _digest(
        {
            "check_item_id": check_item_id,
            "device_id": device_id,
            "entity_type": entity_type,
            "entity_key": entity_key,
            "dimensions": dimensions,
            "risk_type": risk_type,
        }
    )
    return f"risk:v1:{digest}"


class RiskCaseService:
    def __init__(
        self,
        session_scope: Callable[[], AbstractContextManager[Any]] = get_db_session,
    ) -> None:
        self._session_scope = session_scope

    def list_cases(
        self,
        *,
        page: int = 1,
        page_size: int = 25,
        search: str | None = None,
        status: str | None = None,
        severity: str | None = None,
        remediation_status: str | None = None,
        check_item_id: str | None = None,
        device_id: str | None = None,
        owner: str | None = None,
        overdue_only: bool = False,
    ) -> dict[str, Any]:
        page = max(1, int(page))
        page_size = max(1, min(int(page_size), 100))
        now = datetime.now(timezone.utc)
        with self._session_scope() as db:
            base = (
                db.query(RiskCaseRecord, NetworkDevice, CheckItemRecord)
                .outerjoin(NetworkDevice, NetworkDevice.id == RiskCaseRecord.device_id)
                .outerjoin(CheckItemRecord, CheckItemRecord.id == RiskCaseRecord.check_item_id)
            )
            if search and search.strip():
                pattern = f"%{search.strip()}%"
                base = base.filter(
                    or_(
                        RiskCaseRecord.device_id.ilike(pattern),
                        RiskCaseRecord.entity_key.ilike(pattern),
                        RiskCaseRecord.risk_type.ilike(pattern),
                        RiskCaseRecord.owner.ilike(pattern),
                        RiskCaseRecord.check_item_id.ilike(pattern),
                        NetworkDevice.device_name.ilike(pattern),
                        NetworkDevice.management_ip.ilike(pattern),
                        CheckItemRecord.name.ilike(pattern),
                    )
                )
            if check_item_id:
                base = base.filter(RiskCaseRecord.check_item_id == check_item_id)
            if device_id:
                base = base.filter(RiskCaseRecord.device_id == device_id)
            if owner:
                base = base.filter(RiskCaseRecord.owner == owner)
            if severity:
                base = base.filter(RiskCaseRecord.severity == severity)
            if remediation_status:
                base = base.filter(
                    RiskCaseRecord.remediation_status == remediation_status
                )
            if overdue_only:
                base = base.filter(
                    RiskCaseRecord.due_at.is_not(None),
                    RiskCaseRecord.due_at < now,
                    RiskCaseRecord.remediation_status.notin_(["completed", "cancelled"]),
                )

            summary_rows = (
                base.with_entities(
                    RiskCaseRecord.status,
                    RiskCaseRecord.severity,
                    RiskCaseRecord.remediation_status,
                    func.count(RiskCaseRecord.id),
                )
                .group_by(
                    RiskCaseRecord.status,
                    RiskCaseRecord.severity,
                    RiskCaseRecord.remediation_status,
                )
                .all()
            )
            by_status: dict[str, int] = {}
            by_severity: dict[str, int] = {}
            by_remediation_status: dict[str, int] = {}
            total_all = 0
            for case_status, case_severity, case_remediation, count in summary_rows:
                value = int(count or 0)
                total_all += value
                by_status[str(case_status)] = by_status.get(str(case_status), 0) + value
                by_severity[str(case_severity)] = (
                    by_severity.get(str(case_severity), 0) + value
                )
                by_remediation_status[str(case_remediation)] = (
                    by_remediation_status.get(str(case_remediation), 0) + value
                )

            query = base
            if status:
                query = query.filter(RiskCaseRecord.status == status)
            total = int(query.count())
            rows = (
                query.order_by(
                    sql_case(
                        (RiskCaseRecord.status.in_(["open", "acknowledged", "recovering"]), 0),
                        (RiskCaseRecord.status == "resolved", 1),
                        else_=2,
                    ),
                    sql_case(
                        (RiskCaseRecord.severity == "critical", 0),
                        (RiskCaseRecord.severity == "major", 1),
                        (RiskCaseRecord.severity == "minor", 2),
                        (RiskCaseRecord.severity == "warning", 3),
                        else_=4,
                    ),
                    RiskCaseRecord.last_seen_at.desc(),
                    RiskCaseRecord.id.asc(),
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
                .all()
            )
            return {
                "items": [
                    self._serialize_case(case_record, device, check_item, now=now)
                    for case_record, device, check_item in rows
                ],
                "page": page,
                "page_size": page_size,
                "total": total,
                "page_count": (total + page_size - 1) // page_size,
                "summary": {
                    "total": total_all,
                    "active": sum(
                        by_status.get(item, 0)
                        for item in ("open", "acknowledged", "recovering")
                    ),
                    "resolved": by_status.get("resolved", 0),
                    "suppressed": by_status.get("suppressed", 0),
                    "overdue": self._count_overdue(base, now=now),
                    "by_status": by_status,
                    "by_severity": by_severity,
                    "by_remediation_status": by_remediation_status,
                },
            }

    def get_case(
        self,
        risk_case_id: str,
        *,
        occurrence_page: int = 1,
        occurrence_page_size: int = 50,
    ) -> dict[str, Any]:
        occurrence_page = max(1, int(occurrence_page))
        occurrence_page_size = max(1, min(int(occurrence_page_size), 100))
        now = datetime.now(timezone.utc)
        with self._session_scope() as db:
            row = (
                db.query(RiskCaseRecord, NetworkDevice, CheckItemRecord)
                .outerjoin(NetworkDevice, NetworkDevice.id == RiskCaseRecord.device_id)
                .outerjoin(CheckItemRecord, CheckItemRecord.id == RiskCaseRecord.check_item_id)
                .filter(RiskCaseRecord.id == risk_case_id)
                .first()
            )
            if row is None:
                raise ValueError("risk case not found")
            case_record, device, check_item = row
            occurrence_query = db.query(RiskOccurrenceRecord).filter(
                RiskOccurrenceRecord.risk_case_id == risk_case_id
            )
            occurrence_total = int(occurrence_query.count())
            occurrences = (
                occurrence_query.order_by(
                    RiskOccurrenceRecord.observed_at.desc(),
                    RiskOccurrenceRecord.id.desc(),
                )
                .offset((occurrence_page - 1) * occurrence_page_size)
                .limit(occurrence_page_size)
                .all()
            )
            remediations = (
                db.query(RiskRemediationRecord)
                .filter(RiskRemediationRecord.risk_case_id == risk_case_id)
                .order_by(
                    RiskRemediationRecord.created_at.desc(),
                    RiskRemediationRecord.id.desc(),
                )
                .all()
            )
            finding_ids = [
                str(item) for item in case_record.source_finding_ids_json or [] if item
            ]
            findings = (
                db.query(CurrentStateFindingRecord)
                .filter(CurrentStateFindingRecord.id.in_(finding_ids))
                .order_by(
                    CurrentStateFindingRecord.observed_at.desc(),
                    CurrentStateFindingRecord.id.desc(),
                )
                .limit(100)
                .all()
                if finding_ids
                else []
            )
            return {
                **self._serialize_case(case_record, device, check_item, now=now),
                "occurrences": [item.to_dict() for item in occurrences],
                "occurrence_pagination": {
                    "page": occurrence_page,
                    "page_size": occurrence_page_size,
                    "total": occurrence_total,
                    "page_count": (
                        occurrence_total + occurrence_page_size - 1
                    )
                    // occurrence_page_size,
                },
                "remediations": [item.to_dict() for item in remediations],
                "findings": [item.to_dict() for item in findings],
            }

    def observe_findings(
        self,
        *,
        check_item_id: str,
        finding_ids: Iterable[str],
    ) -> list[dict[str, Any]]:
        ids = sorted(set(str(item) for item in finding_ids if str(item)))
        if not ids:
            return []
        now = datetime.now(timezone.utc)
        with self._session_scope() as db:
            findings = (
                db.query(CurrentStateFindingRecord)
                .filter(CurrentStateFindingRecord.id.in_(ids))
                .order_by(CurrentStateFindingRecord.observed_at, CurrentStateFindingRecord.id)
                .all()
            )
            if len(findings) != len(ids):
                raise ValueError("one or more current-state Findings do not exist")
            cases: dict[str, RiskCaseRecord] = {}
            for finding in findings:
                dimensions = dict(finding.dimensions_json or {})
                correlation = _correlation_key(
                    check_item_id=check_item_id,
                    device_id=str(finding.device_id),
                    entity_type=str(finding.entity_type),
                    entity_key=str(finding.entity_key),
                    dimensions=dimensions,
                    risk_type=str(finding.event_type),
                )
                case = (
                    db.query(RiskCaseRecord)
                    .filter(RiskCaseRecord.correlation_key == correlation)
                    .with_for_update()
                    .first()
                )
                occurrence_kind = "observed"
                if case is None:
                    case = RiskCaseRecord(
                        id=str(
                            uuid.uuid5(
                                uuid.NAMESPACE_URL,
                                f"netops/risk-case/{correlation}",
                            )
                        ),
                        correlation_key=correlation,
                        check_item_id=check_item_id,
                        device_id=finding.device_id,
                        entity_type=finding.entity_type,
                        entity_key=finding.entity_key,
                        correlation_dimensions_json=dimensions,
                        risk_type=finding.event_type,
                        severity=finding.severity,
                        status="open",
                        first_seen_at=finding.observed_at,
                        last_seen_at=finding.observed_at,
                        last_successful_evaluation_at=finding.assessed_at,
                        verification_status="confirmed_current",
                        last_verified_at=finding.assessed_at,
                        last_verification_execution_id=(
                            finding.check_execution_id
                        ),
                        source_finding_ids_json=[],
                        source_event_ids_json=[],
                        remediation_status="not_started",
                        created_at=now,
                        updated_at=now,
                    )
                    db.add(case)
                    db.flush()
                    occurrence_kind = "opened"
                elif case.status == "resolved":
                    case.status = "open"
                    case.resolved_at = None
                    occurrence_kind = "reopened"
                finding_ids_json = list(case.source_finding_ids_json or [])
                if finding.id not in finding_ids_json:
                    finding_ids_json.append(finding.id)
                case.source_finding_ids_json = finding_ids_json
                case.last_seen_at = max(
                    _utc(case.last_seen_at),
                    _utc(finding.observed_at),
                )
                case.last_successful_evaluation_at = max(
                    _utc(
                        case.last_successful_evaluation_at
                        or finding.assessed_at
                    ),
                    _utc(finding.assessed_at),
                )
                case.verification_status = "confirmed_current"
                case.last_verified_at = max(
                    _utc(case.last_verified_at or finding.assessed_at),
                    _utc(finding.assessed_at),
                )
                case.last_verification_execution_id = (
                    finding.check_execution_id
                )
                if _SEVERITY_RANK.get(str(finding.severity), 0) > _SEVERITY_RANK.get(
                    str(case.severity), 0
                ):
                    case.severity = finding.severity
                case.updated_at = now
                self._append_occurrence(
                    db,
                    case=case,
                    kind=occurrence_kind,
                    observed_at=finding.observed_at,
                    status_after=case.status,
                    source_finding_id=finding.id,
                    patrol_batch_id=finding.patrol_batch_id,
                    evidence_refs=list(finding.evidence_refs_json or []),
                )
                cases[case.id] = case
            db.flush()
            return [cases[key].to_dict() for key in sorted(cases)]

    def resolve_with_evidence(
        self,
        risk_case_id: str,
        *,
        successful_current_evaluation: bool,
        observed_at: datetime,
        patrol_batch_id: str,
        evidence_refs: Iterable[str],
        recovery_rule_version_id: str | None = None,
        source_event_id: str | None = None,
        actor: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        evidence = sorted(set(str(item) for item in evidence_refs if str(item)))
        if not successful_current_evaluation:
            raise ValueError("risk recovery requires a successful current-state evaluation")
        if not recovery_rule_version_id and not source_event_id:
            raise ValueError("risk recovery requires an approved recovery rule or event")
        if not evidence:
            raise ValueError("risk recovery requires evidence references")
        with self._session_scope() as db:
            case = (
                db.query(RiskCaseRecord)
                .filter(RiskCaseRecord.id == risk_case_id)
                .with_for_update()
                .first()
            )
            if case is None:
                raise ValueError("risk case not found")
            if case.status == "resolved":
                return case.to_dict()
            case.status = "resolved"
            case.resolved_at = observed_at
            case.last_successful_evaluation_at = observed_at
            case.verification_status = "resolved"
            case.last_verified_at = observed_at
            case.resolution_evidence_refs_json = evidence
            case.updated_at = datetime.now(timezone.utc)
            if source_event_id:
                event_ids = list(case.source_event_ids_json or [])
                if source_event_id not in event_ids:
                    event_ids.append(source_event_id)
                case.source_event_ids_json = event_ids
            self._append_occurrence(
                db,
                case=case,
                kind="resolved",
                observed_at=observed_at,
                status_after="resolved",
                source_event_id=source_event_id,
                patrol_batch_id=patrol_batch_id,
                actor=actor,
                reason=reason or f"recovery_rule:{recovery_rule_version_id or ''}",
                evidence_refs=evidence,
            )
            db.flush()
            return case.to_dict()

    def record_unable_to_confirm(
        self,
        risk_case_id: str,
        *,
        patrol_batch_id: str,
        observed_at: datetime,
        reason: str,
        evidence_refs: Iterable[str] = (),
    ) -> dict[str, Any]:
        with self._session_scope() as db:
            case = (
                db.query(RiskCaseRecord)
                .filter(RiskCaseRecord.id == risk_case_id)
                .with_for_update()
                .first()
            )
            if case is None:
                raise ValueError("risk case not found")
            case.verification_status = "not_reverified"
            case.updated_at = datetime.now(timezone.utc)
            self._append_occurrence(
                db,
                case=case,
                kind="unable_to_confirm",
                observed_at=observed_at,
                status_after=case.status,
                patrol_batch_id=patrol_batch_id,
                reason=reason,
                evidence_refs=list(evidence_refs),
            )
            db.flush()
            return case.to_dict()

    def acknowledge(
        self,
        risk_case_id: str,
        *,
        actor: str,
        reason: str,
        observed_at: datetime | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if not actor.strip() or not reason.strip():
            raise ValueError("risk acknowledgement requires actor and reason")
        occurred_at = observed_at or datetime.now(timezone.utc)
        with self._session_scope() as db:
            case = (
                db.query(RiskCaseRecord)
                .filter(RiskCaseRecord.id == risk_case_id)
                .with_for_update()
                .first()
            )
            if case is None:
                raise ValueError("risk case not found")
            occurrence_key = self._action_idempotency_key(
                risk_case_id,
                action="acknowledge",
                idempotency_key=idempotency_key,
            )
            if occurrence_key and self._occurrence_exists(db, occurrence_key):
                return case.to_dict()
            if case.status not in {"open", "recovering"}:
                raise ValueError(f"risk case cannot be acknowledged from {case.status}")
            case.status = "acknowledged"
            case.updated_at = datetime.now(timezone.utc)
            self._append_occurrence(
                db,
                case=case,
                kind="acknowledged",
                observed_at=occurred_at,
                status_after=case.status,
                actor=actor,
                reason=reason,
                occurrence_idempotency_key=occurrence_key,
            )
            db.flush()
            return case.to_dict()

    def create_remediation(
        self,
        risk_case_id: str,
        *,
        action_text: str,
        owner: str,
        created_by: str,
        due_at: datetime | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if not action_text.strip():
            raise ValueError("remediation action is required")
        if not owner.strip():
            raise ValueError("remediation owner is required")
        now = datetime.now(timezone.utc)
        with self._session_scope() as db:
            case = db.get(RiskCaseRecord, risk_case_id)
            if case is None:
                raise ValueError("risk case not found")
            if case.status in {"resolved", "suppressed"}:
                raise ValueError(
                    f"remediation cannot be created for risk case in {case.status}"
                )
            remediation_id = (
                str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"netops/risk-remediation/{risk_case_id}/{idempotency_key}",
                    )
                )
                if idempotency_key
                else str(uuid.uuid4())
            )
            existing = db.get(RiskRemediationRecord, remediation_id)
            if existing is not None:
                return existing.to_dict()
            remediation = RiskRemediationRecord(
                id=remediation_id,
                risk_case_id=risk_case_id,
                status="planned",
                owner=owner,
                action_text=action_text,
                due_at=due_at,
                evidence_refs_json=[],
                created_by=created_by,
                created_at=now,
                updated_at=now,
            )
            db.add(remediation)
            case.owner = owner
            case.due_at = due_at
            case.remediation_status = "planned"
            case.updated_at = now
            db.flush()
            return remediation.to_dict()

    def update_remediation(
        self,
        risk_case_id: str,
        remediation_id: str,
        *,
        status: str,
        actor: str,
        reason: str,
        evidence_refs: Iterable[str] = (),
        observed_at: datetime | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if status not in {"in_progress", "completed", "cancelled"}:
            raise ValueError("unsupported remediation status")
        if not actor.strip() or not reason.strip():
            raise ValueError("remediation update requires actor and reason")
        evidence = sorted(set(str(item) for item in evidence_refs if str(item).strip()))
        if status == "completed" and not evidence:
            raise ValueError("completed remediation requires evidence references")
        now = observed_at or datetime.now(timezone.utc)
        with self._session_scope() as db:
            case_record = (
                db.query(RiskCaseRecord)
                .filter(RiskCaseRecord.id == risk_case_id)
                .with_for_update()
                .first()
            )
            remediation = (
                db.query(RiskRemediationRecord)
                .filter(
                    RiskRemediationRecord.id == remediation_id,
                    RiskRemediationRecord.risk_case_id == risk_case_id,
                )
                .with_for_update()
                .first()
            )
            if case_record is None or remediation is None:
                raise ValueError("risk case or remediation not found")
            if remediation.status == status:
                return remediation.to_dict()
            allowed = {
                "planned": {"in_progress", "cancelled"},
                "in_progress": {"completed", "cancelled"},
                "completed": set(),
                "cancelled": set(),
            }
            if status not in allowed.get(str(remediation.status), set()):
                raise ValueError(
                    f"remediation cannot transition from {remediation.status} to {status}"
                )
            remediation.status = status
            remediation.updated_at = now
            if evidence:
                remediation.evidence_refs_json = evidence
            if status == "completed":
                remediation.completed_by = actor
                remediation.completed_at = now
            case_record.remediation_status = status
            case_record.updated_at = now
            if status == "in_progress" and case_record.status in {"open", "acknowledged"}:
                case_record.status = "recovering"
                occurrence_key = self._action_idempotency_key(
                    risk_case_id,
                    action=f"remediation:{remediation_id}:in_progress",
                    idempotency_key=idempotency_key,
                )
                self._append_occurrence(
                    db,
                    case=case_record,
                    kind="recovering",
                    observed_at=now,
                    status_after=case_record.status,
                    actor=actor,
                    reason=reason,
                    evidence_refs=evidence,
                    occurrence_idempotency_key=occurrence_key,
                )
            db.flush()
            return remediation.to_dict()

    def suppress(
        self,
        risk_case_id: str,
        *,
        actor: str,
        reason: str,
        observed_at: datetime | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if not actor.strip() or not reason.strip():
            raise ValueError("risk suppression requires actor and reason")
        now = observed_at or datetime.now(timezone.utc)
        with self._session_scope() as db:
            case_record = (
                db.query(RiskCaseRecord)
                .filter(RiskCaseRecord.id == risk_case_id)
                .with_for_update()
                .first()
            )
            if case_record is None:
                raise ValueError("risk case not found")
            occurrence_key = self._action_idempotency_key(
                risk_case_id,
                action="suppress",
                idempotency_key=idempotency_key,
            )
            if occurrence_key and self._occurrence_exists(db, occurrence_key):
                return case_record.to_dict()
            if case_record.status == "suppressed":
                return case_record.to_dict()
            if case_record.status == "resolved":
                raise ValueError("resolved risk case cannot be suppressed")
            case_record.status = "suppressed"
            case_record.updated_at = now
            self._append_occurrence(
                db,
                case=case_record,
                kind="suppressed",
                observed_at=now,
                status_after=case_record.status,
                actor=actor,
                reason=reason,
                occurrence_idempotency_key=occurrence_key,
            )
            db.flush()
            return case_record.to_dict()

    @staticmethod
    def _append_occurrence(
        db: Any,
        *,
        case: RiskCaseRecord,
        kind: str,
        observed_at: datetime,
        status_after: str,
        source_finding_id: str | None = None,
        source_event_id: str | None = None,
        patrol_batch_id: str | None = None,
        actor: str | None = None,
        reason: str | None = None,
        evidence_refs: Iterable[str] = (),
        occurrence_idempotency_key: str | None = None,
    ) -> None:
        identity = {
            "risk_case_id": case.id,
            "kind": kind,
            "source_finding_id": source_finding_id,
            "source_event_id": source_event_id,
            "patrol_batch_id": patrol_batch_id,
            "observed_at": observed_at.isoformat(),
        }
        idempotency_key = occurrence_idempotency_key or _digest(identity)
        existing = (
            db.query(RiskOccurrenceRecord)
            .filter(RiskOccurrenceRecord.idempotency_key == idempotency_key)
            .first()
        )
        if existing is not None:
            return
        db.add(
            RiskOccurrenceRecord(
                id=str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"netops/risk-occurrence/{idempotency_key}",
                    )
                ),
                idempotency_key=idempotency_key,
                risk_case_id=case.id,
                occurrence_kind=kind,
                source_finding_id=source_finding_id,
                source_event_id=source_event_id,
                patrol_batch_id=patrol_batch_id,
                observed_at=observed_at,
                status_after=status_after,
                actor=actor,
                reason=reason,
                evidence_refs_json=sorted(
                    set(str(item) for item in evidence_refs if str(item))
                ),
                created_at=datetime.now(timezone.utc),
            )
        )

    @staticmethod
    def _serialize_case(
        case_record: RiskCaseRecord,
        device: NetworkDevice | None,
        check_item: CheckItemRecord | None,
        *,
        now: datetime,
    ) -> dict[str, Any]:
        payload = case_record.to_dict()
        first_seen = _utc(case_record.first_seen_at)
        payload.update(
            {
                "device_name": device.device_name if device is not None else None,
                "management_ip": device.management_ip if device is not None else None,
                "check_item_name": check_item.name if check_item is not None else None,
                "age_days": max(0, int((now - first_seen).total_seconds() // 86400)),
                "overdue": bool(
                    case_record.due_at
                    and _utc(case_record.due_at) < now
                    and case_record.remediation_status not in {"completed", "cancelled"}
                ),
            }
        )
        return payload

    @staticmethod
    def _count_overdue(query: Any, *, now: datetime) -> int:
        return int(
            query.filter(
                RiskCaseRecord.due_at.is_not(None),
                RiskCaseRecord.due_at < now,
                RiskCaseRecord.remediation_status.notin_(["completed", "cancelled"]),
            ).count()
        )

    @staticmethod
    def _action_idempotency_key(
        risk_case_id: str,
        *,
        action: str,
        idempotency_key: str | None,
    ) -> str | None:
        if not idempotency_key:
            return None
        return _digest(
            {
                "risk_case_id": risk_case_id,
                "action": action,
                "idempotency_key": idempotency_key,
            }
        )

    @staticmethod
    def _occurrence_exists(db: Any, idempotency_key: str) -> bool:
        return (
            db.query(RiskOccurrenceRecord.id)
            .filter(RiskOccurrenceRecord.idempotency_key == idempotency_key)
            .first()
            is not None
        )
