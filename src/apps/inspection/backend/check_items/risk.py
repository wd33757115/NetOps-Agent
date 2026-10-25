"""RiskCase projection with append-only occurrences and explicit recovery evidence."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import case as sql_case
from sqlalchemy import func, or_

from src.core.check_items.presentation import check_item_display_name
from src.infrastructure.db.models import (
    CheckAssessmentRecord,
    CheckExecutionRecord,
    CheckItemRecord,
    CheckItemRuntimeBundleVersionRecord,
    CheckItemVersionRecord,
    CoverageDecisionRecord,
    CurrentStateFindingRecord,
    EventRuleRecord,
    EventRuleVersionRecord,
    EvidenceQualificationRecord,
    NetworkChangeRecord,
    NetworkDevice,
    NetworkEventOccurrenceRecord,
    NetworkEventRecord,
    RiskCaseRecord,
    RiskOccurrenceRecord,
    RiskRemediationRecord,
    RuntimeBundleAssetRecord,
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
    aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc)


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


class RiskConflictError(ValueError):
    """The requested lifecycle action conflicts with the current risk state."""


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
                base = base.filter(RiskCaseRecord.remediation_status == remediation_status)
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
                by_severity[str(case_severity)] = by_severity.get(str(case_severity), 0) + value
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
                        by_status.get(item, 0) for item in ("open", "acknowledged", "recovering")
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
                .filter(
                    RiskRemediationRecord.risk_case_id == risk_case_id,
                    or_(
                        RiskRemediationRecord.risk_episode_id == case_record.episode_id,
                        RiskRemediationRecord.risk_episode_id.is_(None),
                    ),
                )
                .order_by(
                    RiskRemediationRecord.created_at.desc(),
                    RiskRemediationRecord.id.desc(),
                )
                .all()
            )
            finding_ids = [str(item) for item in case_record.source_finding_ids_json or [] if item]
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
                    "page_count": (occurrence_total + occurrence_page_size - 1)
                    // occurrence_page_size,
                },
                "remediations": [item.to_dict() for item in remediations],
                "findings": [item.to_dict() for item in findings],
                **self._recovery_confirmation_view(
                    db,
                    case_record=case_record,
                ),
                "closure": self._closure_view(
                    case_record,
                    remediations=remediations,
                ),
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
                        episode_id=str(uuid.uuid4()),
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
                        last_verification_execution_id=(finding.check_execution_id),
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
                    case.episode_id = str(uuid.uuid4())
                    case.closed_at = None
                    case.closed_by = None
                    case.closure_reason = None
                    case.closure_disposition = None
                    case.closure_evidence_refs_json = None
                    case.remediation_status = "not_started"
                    case.owner = None
                    case.due_at = None
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
                    _utc(case.last_successful_evaluation_at or finding.assessed_at),
                    _utc(finding.assessed_at),
                )
                case.verification_status = "confirmed_current"
                case.last_verified_at = max(
                    _utc(case.last_verified_at or finding.assessed_at),
                    _utc(finding.assessed_at),
                )
                case.last_verification_execution_id = finding.check_execution_id
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

    def confirm_recovery(
        self,
        risk_case_id: str,
        *,
        recovery_candidate_id: str,
        actor: str,
        reason: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Resolve a risk only after revalidating immutable runtime facts."""

        candidate_id = str(recovery_candidate_id or "").strip()
        if not candidate_id:
            raise ValueError("risk recovery requires a recovery candidate")
        if not actor.strip() or not reason.strip():
            raise ValueError("risk recovery confirmation requires actor and reason")
        if not idempotency_key.strip():
            raise ValueError("risk recovery confirmation requires an idempotency key")

        now = datetime.now(timezone.utc)
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
                risk_episode_id=case_record.episode_id,
                action=f"confirm_recovery:{candidate_id}",
                idempotency_key=idempotency_key,
            )
            if occurrence_key and self._occurrence_exists(db, occurrence_key):
                return {
                    "risk_case": {
                        **case_record.to_dict(),
                        "closure_status": self._summary_closure_status(case_record),
                    },
                    "confirmation": {
                        "candidate_id": candidate_id,
                        "idempotent_replay": True,
                    },
                }
            if case_record.status not in {"open", "acknowledged", "recovering"}:
                raise RiskConflictError(
                    f"risk recovery cannot be confirmed from {case_record.status}"
                )
            if case_record.verification_status != "recovery_pending":
                raise RiskConflictError(
                    "risk recovery cannot be confirmed without recovery_pending evidence"
                )

            candidates = self._build_recovery_candidates(
                db,
                case_record=case_record,
            )
            candidate = next(
                (item for item in candidates if str(item.get("id") or "") == candidate_id),
                None,
            )
            if candidate is None:
                raise RiskConflictError(
                    "risk recovery cannot be confirmed: candidate is stale or unavailable"
                )
            if not bool(candidate.get("eligible")):
                blockers = ", ".join(candidate.get("blocker_codes") or [])
                raise RiskConflictError(
                    "risk recovery cannot be confirmed: "
                    + (blockers or "candidate evidence is incomplete")
                )

            evidence_refs = sorted(
                {str(item) for item in candidate.get("evidence_refs") or [] if str(item)}
            )
            if not evidence_refs:
                raise RiskConflictError(
                    "risk recovery cannot be confirmed without server-verified evidence"
                )
            recovered_at = candidate.get("recovered_at")
            if not isinstance(recovered_at, datetime):
                recovered_at = self._parse_datetime(recovered_at)
            recovered_at = recovered_at or now

            case_record.status = "resolved"
            case_record.resolved_at = now
            case_record.last_successful_evaluation_at = recovered_at
            case_record.verification_status = "resolved"
            case_record.last_verified_at = recovered_at
            case_record.resolution_evidence_refs_json = evidence_refs
            case_record.updated_at = now
            source_event_id = str(candidate.get("source_event_id") or "") or None
            if source_event_id:
                event_ids = list(case_record.source_event_ids_json or [])
                if source_event_id not in event_ids:
                    event_ids.append(source_event_id)
                case_record.source_event_ids_json = event_ids
            self._append_occurrence(
                db,
                case=case_record,
                kind="resolved",
                observed_at=now,
                status_after="resolved",
                source_event_id=source_event_id,
                patrol_batch_id=str(candidate.get("patrol_batch_id") or "") or None,
                actor=actor,
                reason=reason,
                evidence_refs=evidence_refs,
                occurrence_idempotency_key=occurrence_key,
            )
            db.flush()
            return {
                "risk_case": {
                    **case_record.to_dict(),
                    "closure_status": self._summary_closure_status(case_record),
                },
                "confirmation": {
                    **candidate,
                    "confirmed_at": now,
                    "confirmed_by": actor,
                    "idempotent_replay": False,
                },
            }

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

    def _recovery_confirmation_view(
        self,
        db: Any,
        *,
        case_record: RiskCaseRecord,
    ) -> dict[str, Any]:
        candidates = self._build_recovery_candidates(
            db,
            case_record=case_record,
        )
        eligible = [item for item in candidates if bool(item.get("eligible"))]
        blockers = sorted(
            {
                str(code)
                for item in candidates
                for code in item.get("blocker_codes") or []
                if str(code)
            }
        )
        if case_record.status in {"resolved", "suppressed"}:
            state = "not_applicable"
        elif case_record.verification_status != "recovery_pending":
            state = "not_pending"
        elif eligible:
            state = "ready"
        else:
            state = "blocked"
        return {
            "recovery_candidates": candidates,
            "recovery_confirmation": {
                "state": state,
                "eligible_candidate_count": len(eligible),
                "candidate_count": len(candidates),
                "blocker_codes": blockers,
            },
        }

    def _build_recovery_candidates(
        self,
        db: Any,
        *,
        case_record: RiskCaseRecord,
    ) -> list[dict[str, Any]]:
        common_blockers: list[str] = []

        def block(code: str) -> None:
            if code not in common_blockers:
                common_blockers.append(code)

        if case_record.status not in {"open", "acknowledged", "recovering"}:
            block("risk_case_not_active")
        if case_record.verification_status != "recovery_pending":
            block("risk_not_recovery_pending")
        execution_id = str(case_record.last_verification_execution_id or "")
        execution = db.get(CheckExecutionRecord, execution_id) if execution_id else None
        if execution is None:
            block("verification_execution_missing")
        elif str(execution.execution_status) != "completed":
            block("verification_execution_not_completed")
        elif str(execution.device_id) != str(case_record.device_id):
            block("verification_execution_device_mismatch")

        check_version = (
            db.get(CheckItemVersionRecord, execution.check_item_version_id)
            if execution is not None
            else None
        )
        if execution is not None and check_version is None:
            block("check_item_version_missing")
        elif check_version is not None and str(check_version.check_item_id) != str(
            case_record.check_item_id
        ):
            block("verification_execution_check_item_mismatch")

        assessment = None
        if execution is not None:
            assessment = (
                db.get(CheckAssessmentRecord, execution.assessment_id)
                if execution.assessment_id
                else db.query(CheckAssessmentRecord)
                .filter(CheckAssessmentRecord.execution_id == execution.id)
                .first()
            )
        if assessment is None:
            block("healthy_assessment_missing")
        else:
            if str(assessment.assessment_status) != "healthy":
                block("assessment_not_healthy")
            if str(assessment.completeness_status) != "complete":
                block("assessment_incomplete")
            if list(assessment.blocking_reason_codes_json or []):
                block("assessment_has_blockers")
            if list(assessment.current_finding_ids_json or []):
                block("assessment_has_current_findings")
            if str(assessment.risk_verification_status) not in {
                "recovery_pending",
                "no_current_risk",
            }:
                block("assessment_does_not_support_recovery")

        qualification = (
            db.get(
                EvidenceQualificationRecord,
                execution.evidence_qualification_id,
            )
            if execution is not None and execution.evidence_qualification_id
            else None
        )
        if qualification is None:
            block("evidence_qualification_missing")
        else:
            if qualification.check_execution_id != execution_id:
                block("evidence_qualification_execution_mismatch")
            if qualification.source_identity_status != "confirmed":
                block("source_identity_not_confirmed")
            if qualification.output_quality_status != "complete":
                block("output_quality_not_complete")
            if qualification.freshness_status != "fresh":
                block("evidence_not_fresh")

        coverage = (
            db.get(CoverageDecisionRecord, execution.coverage_decision_id)
            if execution is not None and execution.coverage_decision_id
            else None
        )
        if coverage is None:
            block("coverage_decision_missing")
        else:
            if coverage.check_execution_id != execution_id:
                block("coverage_execution_mismatch")
            if coverage.coverage_status != "complete":
                block("coverage_not_complete")
            if list(coverage.reason_codes_json or []):
                block("coverage_has_blockers")

        bundle_version_id = (
            str(execution.runtime_bundle_version_id or "") if execution is not None else ""
        )
        bundle = (
            db.get(CheckItemRuntimeBundleVersionRecord, bundle_version_id)
            if bundle_version_id
            else None
        )
        if bundle is None:
            block("runtime_bundle_missing")
        elif execution is not None:
            if str(bundle.check_item_version_id) != str(execution.check_item_version_id):
                block("runtime_bundle_check_item_mismatch")
            if str(bundle.digest) != str(execution.runtime_bundle_digest or ""):
                block("runtime_bundle_digest_mismatch")

        assets = (
            db.query(RuntimeBundleAssetRecord)
            .filter(RuntimeBundleAssetRecord.bundle_version_id == bundle_version_id)
            .all()
            if bundle is not None
            else []
        )
        evidence_refs = {
            f"risk_case:{case_record.id}",
            *(
                str(item)
                for item in (execution.evidence_refs_json if execution is not None else [])
                if str(item)
            ),
        }
        if execution is not None:
            evidence_refs.add(f"check_execution:{execution.id}")
            evidence_refs.add(f"runtime_bundle:{execution.runtime_bundle_version_id}")
        if assessment is not None:
            evidence_refs.add(f"assessment:{assessment.id}")
        if qualification is not None:
            evidence_refs.add(f"evidence_qualification:{qualification.id}")
            evidence_refs.update(
                str(item) for item in qualification.evidence_refs_json or [] if str(item)
            )
        if coverage is not None:
            evidence_refs.add(f"coverage_decision:{coverage.id}")
            evidence_refs.update(
                str(item) for item in coverage.evidence_refs_json or [] if str(item)
            )

        changes = (
            db.query(NetworkChangeRecord)
            .filter(
                NetworkChangeRecord.check_execution_id == execution_id,
                NetworkChangeRecord.device_id == case_record.device_id,
                NetworkChangeRecord.entity_type == case_record.entity_type,
                NetworkChangeRecord.entity_key == case_record.entity_key,
            )
            .order_by(
                NetworkChangeRecord.observed_at.desc(),
                NetworkChangeRecord.id.desc(),
            )
            .all()
            if execution is not None
            else []
        )
        change_by_id = {str(item.id): item for item in changes}
        occurrences = (
            db.query(NetworkEventOccurrenceRecord)
            .filter(
                NetworkEventOccurrenceRecord.source_change_id.in_(list(change_by_id)),
                NetworkEventOccurrenceRecord.occurrence_kind == "recovery",
            )
            .order_by(
                NetworkEventOccurrenceRecord.observed_at.desc(),
                NetworkEventOccurrenceRecord.id.desc(),
            )
            .all()
            if change_by_id
            else []
        )

        candidates: list[dict[str, Any]] = []
        for occurrence in occurrences:
            blockers = list(common_blockers)
            payload = dict(occurrence.payload_json or {})
            rule_payload = dict(payload.get("rule") or {})
            change = change_by_id.get(str(occurrence.source_change_id or ""))
            event = db.get(NetworkEventRecord, occurrence.event_id)
            if not bool(payload.get("projection_applied", True)):
                blockers.append("recovery_event_superseded")
            if str(payload.get("event_class") or rule_payload.get("event_class")) != ("recovery"):
                blockers.append("event_is_not_recovery")
            if str(payload.get("device_id") or "") != str(case_record.device_id):
                blockers.append("recovery_event_device_mismatch")
            if str(payload.get("entity_type") or "") != str(case_record.entity_type):
                blockers.append("recovery_event_entity_type_mismatch")
            if str(payload.get("entity_key") or "") != str(case_record.entity_key):
                blockers.append("recovery_event_entity_key_mismatch")
            resolves = dict(rule_payload.get("recovery") or {}).get("resolves") or []
            if isinstance(resolves, str):
                resolves = [resolves]
            if str(case_record.risk_type) not in {str(item) for item in resolves}:
                blockers.append("recovery_rule_does_not_resolve_risk")

            rule_version = self._event_rule_version(
                db,
                rule_payload=rule_payload,
            )
            if rule_version is None:
                blockers.append("recovery_rule_version_missing")
            elif not any(
                item.asset_type == "event_rule"
                and str(item.asset_version_id) == str(rule_version.id)
                for item in assets
            ):
                blockers.append("recovery_rule_not_in_frozen_bundle")
            elif str(rule_version.event_class) == "fault":
                blockers.append("recovery_rule_class_mismatch")

            refs = set(evidence_refs)
            if change is not None:
                refs.add(f"change:{change.id}")
            refs.add(f"event_occurrence:{occurrence.id}")
            refs.add(f"network_event:{occurrence.event_id}")
            if rule_version is not None:
                refs.add(f"event_rule:{rule_version.id}")
            candidate = {
                "id": self._recovery_candidate_id(
                    risk_case_id=str(case_record.id),
                    kind="recovery_event",
                    execution_id=execution_id,
                    occurrence_id=str(occurrence.id),
                    rule_version_ids=([str(rule_version.id)] if rule_version is not None else []),
                ),
                "kind": "recovery_event",
                "eligible": not blockers,
                "blocker_codes": sorted(set(blockers)),
                "execution_id": execution_id or None,
                "assessment_id": (str(assessment.id) if assessment is not None else None),
                "patrol_batch_id": (
                    str(execution.patrol_batch_id) if execution is not None else None
                ),
                "source_change_id": str(occurrence.source_change_id or "") or None,
                "source_event_id": str(occurrence.event_id),
                "event_occurrence_id": str(occurrence.id),
                "event_type": str(payload.get("event_type") or ""),
                "recovery_rule_version_ids": (
                    [str(rule_version.id)] if rule_version is not None else []
                ),
                "recovered_at": occurrence.observed_at,
                "evidence_refs": sorted(refs),
                "source_event_status": (str(event.status) if event is not None else None),
            }
            candidates.append(candidate)

        current_state_assets = [item for item in assets if item.asset_type == "current_state_rule"]
        current_rule_versions = (
            db.query(EventRuleVersionRecord)
            .filter(
                EventRuleVersionRecord.id.in_(
                    [str(item.asset_version_id) for item in current_state_assets]
                )
            )
            .all()
            if current_state_assets
            else []
        )
        relevant_current_rules = [
            item
            for item in current_rule_versions
            if str(item.event_type) == str(case_record.risk_type)
            and str(item.event_class or "fault") == "fault"
        ]
        assessment_blockers = list(common_blockers)
        if not relevant_current_rules:
            assessment_blockers.append("matching_current_state_rule_not_in_frozen_bundle")
        assessment_candidate = {
            "id": self._recovery_candidate_id(
                risk_case_id=str(case_record.id),
                kind="healthy_assessment",
                execution_id=execution_id,
                occurrence_id=None,
                rule_version_ids=[str(item.id) for item in relevant_current_rules],
            ),
            "kind": "healthy_assessment",
            "eligible": not assessment_blockers,
            "blocker_codes": sorted(set(assessment_blockers)),
            "execution_id": execution_id or None,
            "assessment_id": (str(assessment.id) if assessment is not None else None),
            "patrol_batch_id": (str(execution.patrol_batch_id) if execution is not None else None),
            "source_change_id": None,
            "source_event_id": None,
            "event_occurrence_id": None,
            "event_type": None,
            "recovery_rule_version_ids": [str(item.id) for item in relevant_current_rules],
            "recovered_at": (assessment.assessed_at if assessment is not None else None),
            "evidence_refs": sorted(
                {
                    *evidence_refs,
                    *(f"current_state_rule:{item.id}" for item in relevant_current_rules),
                }
            ),
            "source_event_status": None,
        }
        candidates.append(assessment_candidate)
        return sorted(
            candidates,
            key=lambda item: (
                not bool(item.get("eligible")),
                0 if item.get("kind") == "recovery_event" else 1,
                str(item.get("recovered_at") or ""),
                str(item.get("id") or ""),
            ),
        )

    @staticmethod
    def _event_rule_version(
        db: Any,
        *,
        rule_payload: dict[str, Any],
    ) -> EventRuleVersionRecord | None:
        version_id = str(rule_payload.get("version_id") or "")
        if version_id:
            return db.get(EventRuleVersionRecord, version_id)
        rule_ref = str(rule_payload.get("rule_id") or "")
        try:
            version = int(rule_payload.get("version"))
        except (TypeError, ValueError):
            return None
        if not rule_ref:
            return None
        row = (
            db.query(EventRuleVersionRecord)
            .join(
                EventRuleRecord,
                EventRuleRecord.id == EventRuleVersionRecord.rule_id,
            )
            .filter(
                EventRuleVersionRecord.version == version,
                or_(
                    EventRuleRecord.id == rule_ref,
                    EventRuleRecord.name == rule_ref,
                ),
            )
            .first()
        )
        return row

    @staticmethod
    def _recovery_candidate_id(
        *,
        risk_case_id: str,
        kind: str,
        execution_id: str,
        occurrence_id: str | None,
        rule_version_ids: Iterable[str],
    ) -> str:
        return "recovery-candidate:v1:" + _digest(
            {
                "risk_case_id": risk_case_id,
                "kind": kind,
                "execution_id": execution_id,
                "occurrence_id": occurrence_id,
                "rule_version_ids": sorted(str(item) for item in rule_version_ids if str(item)),
            }
        )

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return _utc(value)
        if not value:
            return None
        try:
            return _utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
        except ValueError:
            return None

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
                risk_episode_id=case.episode_id,
                action="acknowledge",
                idempotency_key=idempotency_key,
            )
            if occurrence_key and self._occurrence_exists(db, occurrence_key):
                return case.to_dict()
            if case.status not in {"open", "recovering"}:
                raise RiskConflictError(f"risk case cannot be acknowledged from {case.status}")
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
            if case.status == "suppressed" or case.closed_at is not None:
                raise RiskConflictError(
                    "remediation cannot be created for a suppressed or closed risk case"
                )
            remediation_id = (
                str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        "netops/risk-remediation/"
                        f"{risk_case_id}/{case.episode_id}/{idempotency_key}",
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
                risk_episode_id=case.episode_id,
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
                raise RiskConflictError(
                    f"remediation cannot transition from {remediation.status} to {status}"
                )
            remediation.status = status
            remediation.updated_at = now
            if evidence:
                remediation.evidence_refs_json = evidence
            if status == "completed":
                remediation.completed_by = actor
                remediation.completed_at = now
            self._refresh_remediation_summary(
                db,
                case_record=case_record,
            )
            case_record.updated_at = now
            if status == "in_progress" and case_record.status in {"open", "acknowledged"}:
                case_record.status = "recovering"
                occurrence_key = self._action_idempotency_key(
                    risk_case_id,
                    risk_episode_id=case_record.episode_id,
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

    def accept_remediation(
        self,
        risk_case_id: str,
        remediation_id: str,
        *,
        actor: str,
        reason: str,
        evidence_refs: Iterable[str],
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Independently accept completed remediation evidence."""

        if not actor.strip() or not reason.strip():
            raise ValueError("remediation acceptance requires actor and reason")
        if not idempotency_key.strip():
            raise ValueError("remediation acceptance requires an idempotency key")
        evidence = sorted(set(str(item) for item in evidence_refs if str(item).strip()))
        if not evidence:
            raise ValueError("remediation acceptance requires evidence references")
        now = datetime.now(timezone.utc)
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
            if (
                remediation.risk_episode_id
                and remediation.risk_episode_id != case_record.episode_id
            ):
                raise RiskConflictError("remediation belongs to an earlier risk episode")
            if remediation.status == "accepted":
                return remediation.to_dict()
            if remediation.status != "completed":
                raise RiskConflictError("remediation can only be accepted after completion")
            if str(remediation.completed_by or "") == actor:
                raise RiskConflictError("remediation completer cannot accept their own work")
            remediation.status = "accepted"
            remediation.accepted_by = actor
            remediation.accepted_at = now
            remediation.acceptance_reason = reason
            remediation.acceptance_evidence_refs_json = evidence
            remediation.updated_at = now
            self._refresh_remediation_summary(
                db,
                case_record=case_record,
            )
            case_record.updated_at = now
            db.flush()
            return remediation.to_dict()

    def close_case(
        self,
        risk_case_id: str,
        *,
        actor: str,
        reason: str,
        disposition: str,
        evidence_refs: Iterable[str],
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Formally close a technically recovered risk after governance checks."""

        if disposition not in {
            "remediation_accepted",
            "no_remediation_required",
        }:
            raise ValueError("unsupported risk closure disposition")
        if not actor.strip() or not reason.strip():
            raise ValueError("risk closure requires actor and reason")
        if not idempotency_key.strip():
            raise ValueError("risk closure requires an idempotency key")
        evidence = sorted(set(str(item) for item in evidence_refs if str(item).strip()))
        if not evidence:
            raise ValueError("risk closure requires evidence references")
        now = datetime.now(timezone.utc)
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
                risk_episode_id=case_record.episode_id,
                action=f"close:{disposition}",
                idempotency_key=idempotency_key,
            )
            if occurrence_key and self._occurrence_exists(db, occurrence_key):
                return self._closure_action_payload(case_record)
            if case_record.closed_at is not None:
                raise RiskConflictError("risk case is already formally closed")
            if case_record.status != "resolved" or case_record.verification_status != "resolved":
                raise RiskConflictError("risk case cannot be closed before technical recovery")
            remediations = self._current_episode_remediations(
                db,
                case_record=case_record,
                lock=True,
            )
            active_remediations = [item for item in remediations if item.status != "cancelled"]
            if disposition == "remediation_accepted":
                if not active_remediations:
                    raise RiskConflictError("risk closure requires an accepted remediation")
                if any(item.status != "accepted" for item in active_remediations):
                    raise RiskConflictError(
                        "risk closure requires all current remediation work to be accepted"
                    )
            elif active_remediations:
                raise RiskConflictError(
                    "no-remediation closure is invalid while remediation work exists"
                )

            case_record.closed_at = now
            case_record.closed_by = actor
            case_record.closure_reason = reason
            case_record.closure_disposition = disposition
            case_record.closure_evidence_refs_json = evidence
            case_record.updated_at = now
            self._append_occurrence(
                db,
                case=case_record,
                kind="closed",
                observed_at=now,
                status_after=case_record.status,
                actor=actor,
                reason=reason,
                evidence_refs=evidence,
                occurrence_idempotency_key=occurrence_key,
            )
            db.flush()
            return self._closure_action_payload(case_record)

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
                risk_episode_id=case_record.episode_id,
                action="suppress",
                idempotency_key=idempotency_key,
            )
            if occurrence_key and self._occurrence_exists(db, occurrence_key):
                return case_record.to_dict()
            if case_record.status == "suppressed":
                return case_record.to_dict()
            if case_record.status == "resolved":
                raise RiskConflictError("resolved risk case cannot be suppressed")
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
    def _current_episode_remediations(
        db: Any,
        *,
        case_record: RiskCaseRecord,
        lock: bool = False,
    ) -> list[RiskRemediationRecord]:
        query = db.query(RiskRemediationRecord).filter(
            RiskRemediationRecord.risk_case_id == case_record.id,
            or_(
                RiskRemediationRecord.risk_episode_id == case_record.episode_id,
                RiskRemediationRecord.risk_episode_id.is_(None),
            ),
        )
        if lock:
            query = query.with_for_update()
        return query.order_by(
            RiskRemediationRecord.created_at,
            RiskRemediationRecord.id,
        ).all()

    def _refresh_remediation_summary(
        self,
        db: Any,
        *,
        case_record: RiskCaseRecord,
    ) -> None:
        remediations = self._current_episode_remediations(
            db,
            case_record=case_record,
        )
        active = [item for item in remediations if item.status != "cancelled"]
        if not active:
            status = "cancelled" if remediations else "not_started"
        elif any(item.status == "in_progress" for item in active):
            status = "in_progress"
        elif any(item.status == "planned" for item in active):
            status = "planned"
        elif any(item.status == "completed" for item in active):
            status = "completed"
        elif all(item.status == "accepted" for item in active):
            status = "accepted"
        else:
            status = "not_started"
        case_record.remediation_status = status

    @staticmethod
    def _closure_view(
        case_record: RiskCaseRecord,
        *,
        remediations: Iterable[RiskRemediationRecord],
    ) -> dict[str, Any]:
        rows = list(remediations)
        active = [item for item in rows if item.status != "cancelled"]
        if case_record.status == "suppressed":
            state = "suppressed"
            eligible = False
            dispositions: list[str] = []
            blockers = ["risk_case_suppressed"]
        elif case_record.closed_at is not None:
            state = "closed"
            eligible = False
            dispositions = []
            blockers = []
        elif case_record.status != "resolved" or case_record.verification_status != "resolved":
            state = "open"
            eligible = False
            dispositions = []
            blockers = ["technical_recovery_not_confirmed"]
        elif not active:
            state = "ready_to_close"
            eligible = True
            dispositions = ["no_remediation_required"]
            blockers = []
        elif any(item.status in {"planned", "in_progress"} for item in active):
            state = "pending_remediation"
            eligible = False
            dispositions = []
            blockers = ["remediation_not_completed"]
        elif any(item.status == "completed" for item in active):
            state = "pending_acceptance"
            eligible = False
            dispositions = []
            blockers = ["remediation_not_accepted"]
        elif all(item.status == "accepted" for item in active):
            state = "ready_to_close"
            eligible = True
            dispositions = ["remediation_accepted"]
            blockers = []
        else:
            state = "blocked"
            eligible = False
            dispositions = []
            blockers = ["remediation_state_inconsistent"]
        return {
            "state": state,
            "eligible": eligible,
            "allowed_dispositions": dispositions,
            "blocker_codes": blockers,
            "current_episode_id": case_record.episode_id,
            "current_remediation_count": len(active),
            "accepted_remediation_count": sum(item.status == "accepted" for item in active),
            "closed_at": case_record.closed_at,
            "closed_by": case_record.closed_by,
            "closure_reason": case_record.closure_reason,
            "closure_disposition": case_record.closure_disposition,
            "closure_evidence_refs": list(case_record.closure_evidence_refs_json or []),
        }

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
            "risk_episode_id": case.episode_id,
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
                risk_episode_id=case.episode_id,
                occurrence_kind=kind,
                source_finding_id=source_finding_id,
                source_event_id=source_event_id,
                patrol_batch_id=patrol_batch_id,
                observed_at=observed_at,
                status_after=status_after,
                actor=actor,
                reason=reason,
                evidence_refs_json=sorted(set(str(item) for item in evidence_refs if str(item))),
                created_at=datetime.now(timezone.utc),
            )
        )

    @staticmethod
    def _summary_closure_status(case_record: RiskCaseRecord) -> str:
        if case_record.status == "suppressed":
            return "suppressed"
        if case_record.closed_at is not None:
            return "closed"
        if case_record.status != "resolved":
            return "open"
        if case_record.remediation_status == "accepted":
            return "ready_to_close"
        if case_record.remediation_status == "completed":
            return "pending_acceptance"
        if case_record.remediation_status in {"planned", "in_progress"}:
            return "pending_remediation"
        return "technical_recovered"

    @staticmethod
    def _closure_action_payload(
        case_record: RiskCaseRecord,
    ) -> dict[str, Any]:
        payload = case_record.to_dict()
        for key, value in tuple(payload.items()):
            if isinstance(value, datetime):
                payload[key] = _utc(value)
        payload["closure_status"] = RiskCaseService._summary_closure_status(case_record)
        return payload

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
        closure_state = RiskCaseService._summary_closure_status(case_record)
        payload.update(
            {
                "device_name": device.device_name if device is not None else None,
                "management_ip": device.management_ip if device is not None else None,
                "check_item_name": (
                    check_item_display_name(check_item.id, check_item.name)
                    if check_item is not None
                    else None
                ),
                "age_days": max(0, int((now - first_seen).total_seconds() // 86400)),
                "overdue": bool(
                    case_record.due_at
                    and _utc(case_record.due_at) < now
                    and case_record.remediation_status not in {"completed", "accepted", "cancelled"}
                ),
                "closure_status": closure_state,
            }
        )
        return payload

    @staticmethod
    def _count_overdue(query: Any, *, now: datetime) -> int:
        return int(
            query.filter(
                RiskCaseRecord.due_at.is_not(None),
                RiskCaseRecord.due_at < now,
                RiskCaseRecord.remediation_status.notin_(["completed", "accepted", "cancelled"]),
            ).count()
        )

    @staticmethod
    def _action_idempotency_key(
        risk_case_id: str,
        *,
        risk_episode_id: str,
        action: str,
        idempotency_key: str | None,
    ) -> str | None:
        if not idempotency_key:
            return None
        return _digest(
            {
                "risk_case_id": risk_case_id,
                "risk_episode_id": risk_episode_id,
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
