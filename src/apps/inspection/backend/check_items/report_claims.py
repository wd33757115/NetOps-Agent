"""Deterministic, evidence-bounded factual claims for patrol reports."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_

from src.core.check_items.assessment_policy import validate_assessment_basis
from src.core.check_items.report_contract import (
    OFFLINE_HISTORICAL,
    PRODUCTION_CURRENT,
    normalize_report_purpose,
    report_conclusion_permission_is_valid,
)
from src.core.check_items.risk_issue_projection import (
    build_risk_issue_projection,
)
from src.infrastructure.db.models import ReportClaimRecord, ReportManifestRecord
from src.infrastructure.db.postgres import get_db_session

REPORT_CLAIM_LEDGER_SCHEMA_VERSION = "report-claim-ledger/v1"
_LEDGER_FIELDS = {
    "claim_basis_digest",
    "claim_ledger",
    "claim_ledger_digest",
}


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def report_claim_basis_facts(
    source_facts: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the frozen facts covered by claims, excluding the ledger itself."""

    return {
        str(key): deepcopy(value)
        for key, value in source_facts.items()
        if str(key) not in _LEDGER_FIELDS
    }


def report_claim_basis_digest(source_facts: Mapping[str, Any]) -> str:
    return _digest(report_claim_basis_facts(source_facts))


def _unique_strings(values: Sequence[Any]) -> list[str]:
    return sorted({str(item).strip() for item in values if item is not None and str(item).strip()})


def _assessment_basis_evidence_refs(basis: Mapping[str, Any]) -> list[str]:
    return _unique_strings(
        [
            reference
            for entry in _rows(basis.get("entries"))
            for reference in entry.get("evidence_refs") or []
        ]
    )


def _claim(
    *,
    batch_id: str,
    basis_digest: str,
    authority_context_digest: str,
    claim_code: str,
    claim_scope: str,
    assertion_level: str,
    allowed_phrase_code: str,
    subject: Mapping[str, Any],
    fact_refs: Sequence[Any],
    evidence_refs: Sequence[Any] = (),
    limitation_refs: Sequence[Any] = (),
) -> dict[str, Any]:
    claim_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"netops/report-claim/{batch_id}/{basis_digest}/{claim_code}",
        )
    )
    payload = {
        "claim_id": claim_id,
        "claim_code": claim_code,
        "claim_scope": claim_scope,
        "assertion_level": assertion_level,
        "allowed_phrase_code": allowed_phrase_code,
        "subject": dict(subject),
        "fact_refs": _unique_strings(fact_refs),
        "evidence_refs": _unique_strings(evidence_refs),
        "limitation_refs": _unique_strings(limitation_refs),
        "authority_context_digest": authority_context_digest,
        # This digest deliberately covers the fact set before the ledger is
        # attached, avoiding a self-referential source digest.
        "source_facts_digest": basis_digest,
    }
    payload["claim_digest"] = _digest(payload)
    return payload


def _report_assertion_level(
    *,
    report_purpose: str,
    current_ready: bool,
    limited: bool = False,
) -> str:
    if limited:
        return "limited"
    if report_purpose == OFFLINE_HISTORICAL:
        return "historical"
    if report_purpose == PRODUCTION_CURRENT and current_ready:
        return "current"
    return "assessed"


def _overall_phrase(
    permission: Mapping[str, Any],
    *,
    report_purpose: str,
) -> str:
    if report_purpose != PRODUCTION_CURRENT:
        return {
            OFFLINE_HISTORICAL: "historical_result",
            "shadow_validation": "shadow_validation_result",
            "parser_validation": "parser_validation_result",
            "rule_asset_replay": "rule_asset_replay_result",
        }.get(report_purpose, "non_production_result")
    counts = dict(permission.get("counts") or {})
    at_risk = int(counts.get("at_risk") or 0)
    unable = int(counts.get("unable_to_determine") or 0)
    unresolved = int(counts.get("unresolved_in_scope_risk_case_count") or 0)
    if not permission.get("current_conclusion_ready"):
        return "current_authority_blocked"
    if at_risk and unable:
        return "at_risk_with_unknown"
    if at_risk:
        return "at_risk"
    if unresolved and unable:
        return "unresolved_risk_with_unknown"
    if unresolved:
        return "unresolved_risk"
    if unable:
        return (
            "evaluated_scope_healthy_with_unknown"
            if permission.get("evaluated_scope_no_abnormality_allowed")
            else "partially_unknown"
        )
    if permission.get("overall_healthy_allowed"):
        return "healthy"
    if permission.get("evaluated_scope_no_abnormality_allowed"):
        return "evaluated_scope_no_abnormality"
    return "informational_only"


def build_report_claim_ledger(
    source_facts: Mapping[str, Any],
) -> dict[str, Any]:
    """Build every factual statement from one immutable pre-ledger fact set."""

    facts = report_claim_basis_facts(source_facts)
    batch_id = str(facts.get("patrol_batch_id") or "").strip()
    if not batch_id:
        raise ValueError("report claim ledger requires a patrol batch id")
    permission = dict(facts.get("conclusion_permission") or {})
    if not report_conclusion_permission_is_valid(permission):
        raise ValueError("report claim ledger requires a valid conclusion permission")
    authority = dict(facts.get("report_authority_context") or {})
    authority_digest = str(authority.get("digest") or "")
    if len(authority_digest) != 64:
        raise ValueError("report claim ledger requires an authority context digest")
    basis_digest = _digest(facts)
    report_purpose = normalize_report_purpose(facts.get("report_purpose"))
    current_ready = bool(permission.get("current_conclusion_ready"))
    claims: list[dict[str, Any]] = []

    scope_counts = dict(permission.get("counts") or {})
    scope_contract = dict(facts.get("scope_contract") or {})
    scope_limitations = list(permission.get("blocker_reason_codes") or [])
    scope_limitations.extend(
        f"scope_decision:{item.get('decision')}:{item.get('device_id')}:{item.get('check_item_version_id')}"
        for item in _rows(facts.get("scope_decisions"))
        if str(item.get("decision") or "") != "included"
    )
    claims.append(
        _claim(
            batch_id=batch_id,
            basis_digest=basis_digest,
            authority_context_digest=authority_digest,
            claim_code="scope.summary",
            claim_scope=(
                "network" if permission.get("overall_claim_scope_allowed") else "device_group"
            ),
            assertion_level=_report_assertion_level(
                report_purpose=report_purpose,
                current_ready=current_ready,
                limited=not bool(permission.get("scope_reconciled")),
            ),
            allowed_phrase_code="scope_reconciled"
            if permission.get("scope_reconciled")
            else "scope_limited",
            subject={
                "scope_contract_id": scope_contract.get("scope_contract_id"),
                "claim_boundary": permission.get("claim_boundary"),
                "intended_target_pair_count": int(
                    scope_counts.get("intended_target_pair_count") or 0
                ),
                "included_count": int(scope_counts.get("scope_included") or 0),
                "approved_exclusion_count": int(scope_counts.get("scope_approved_exclusion") or 0),
                "not_applicable_count": int(scope_counts.get("scope_not_applicable") or 0),
                "unresolved_count": int(scope_counts.get("scope_unresolved") or 0),
            },
            fact_refs=[
                f"PatrolScopeContract:{scope_contract.get('scope_contract_id') or 'missing'}",
                f"IntendedTargetMatrix:{facts.get('intended_target_matrix_digest') or 'missing'}",
            ],
            limitation_refs=scope_limitations,
        )
    )

    overall_phrase = _overall_phrase(
        permission,
        report_purpose=report_purpose,
    )
    overall_limitations = list(permission.get("blocker_reason_codes") or [])
    if report_purpose != PRODUCTION_CURRENT:
        overall_limitations.append(f"report_purpose:{report_purpose}")
    claims.append(
        _claim(
            batch_id=batch_id,
            basis_digest=basis_digest,
            authority_context_digest=authority_digest,
            claim_code="conclusion.overall",
            claim_scope=(
                "network" if permission.get("overall_claim_scope_allowed") else "device_group"
            ),
            assertion_level=_report_assertion_level(
                report_purpose=report_purpose,
                current_ready=current_ready,
                limited=overall_phrase
                in {
                    "current_authority_blocked",
                    "at_risk_with_unknown",
                    "unresolved_risk_with_unknown",
                    "evaluated_scope_healthy_with_unknown",
                    "partially_unknown",
                },
            ),
            allowed_phrase_code=overall_phrase,
            subject={
                "counts": scope_counts,
                "report_purpose": report_purpose,
            },
            fact_refs=[
                f"ConclusionPermission:{permission.get('decision_digest') or 'missing'}",
                f"ReportAuthorityContext:{authority_digest}",
            ],
            evidence_refs=[f"EvidenceSet:{facts.get('raw_evidence_set_digest') or 'missing'}"],
            limitation_refs=overall_limitations,
        )
    )

    execution_by_id = {
        str(item.get("id") or ""): item
        for item in _rows(facts.get("executions"))
        if str(item.get("id") or "")
    }
    for assessment in _rows(facts.get("assessments")):
        assessment_id = str(assessment.get("id") or "")
        execution_id = str(assessment.get("execution_id") or "")
        if not assessment_id or not execution_id:
            continue
        execution = execution_by_id.get(execution_id, {})
        status = str(assessment.get("assessment_status") or "unable_to_determine")
        limitations = list(assessment.get("blocking_reason_codes_json") or [])
        assessment_basis = dict(assessment.get("assessment_basis_json") or {})
        assessment_basis_digest = str(assessment.get("assessment_basis_digest") or "").strip()
        expected_policy_digest = str(
            execution.get("assessment_policy_snapshot_digest") or ""
        ).strip()
        actual_policy_digest = str(
            assessment.get("assessment_policy_snapshot_digest") or ""
        ).strip()
        basis_valid = bool(
            assessment_basis
            and assessment_basis_digest
            and validate_assessment_basis(assessment_basis)
            and str(assessment_basis.get("digest") or "") == assessment_basis_digest
        )
        if expected_policy_digest:
            if actual_policy_digest != expected_policy_digest:
                limitations.append("assessment_policy_snapshot_mismatch")
            if not assessment_basis or not assessment_basis_digest:
                limitations.append("assessment_basis_missing")
            elif not basis_valid:
                limitations.append("assessment_basis_invalid")
            elif (
                str(assessment_basis.get("policy_snapshot_digest") or "") != expected_policy_digest
            ):
                limitations.append("assessment_basis_policy_snapshot_mismatch")
        basis_summary = dict(assessment_basis.get("summary") or {})
        evidence_qualification_id = (
            assessment.get("evidence_qualification_id")
            or execution.get("evidence_qualification_id")
            or "missing"
        )
        coverage_decision_id = (
            assessment.get("coverage_decision_id")
            or execution.get("coverage_decision_id")
            or "missing"
        )
        if status == "unable_to_determine" and not limitations:
            limitations.append("unable_to_determine_without_reason")
        claims.append(
            _claim(
                batch_id=batch_id,
                basis_digest=basis_digest,
                authority_context_digest=authority_digest,
                claim_code=f"assessment.{assessment_id}",
                claim_scope="check_item",
                assertion_level=_report_assertion_level(
                    report_purpose=report_purpose,
                    current_ready=current_ready,
                    limited=status == "unable_to_determine",
                ),
                allowed_phrase_code=f"assessment_{status}",
                subject={
                    "assessment_id": assessment_id,
                    "execution_id": execution_id,
                    "device_id": assessment.get("device_id"),
                    "check_item_version_id": assessment.get("check_item_version_id"),
                    "assessment_status": status,
                    "completeness_status": assessment.get("completeness_status"),
                    "comparison_status": assessment.get("comparison_status"),
                    "observed_entity_count": assessment.get("observed_entity_count"),
                    "evaluated_entity_count": assessment.get("evaluated_entity_count"),
                    "assessment_policy_snapshot_digest": (actual_policy_digest or None),
                    "assessment_basis_digest": (assessment_basis_digest or None),
                    "assessment_basis_summary": basis_summary,
                },
                fact_refs=[
                    f"CheckAssessment:{assessment_id}",
                    f"CheckExecution:{execution_id}",
                    f"EvidenceQualification:{evidence_qualification_id}",
                    f"CoverageDecision:{coverage_decision_id}",
                ]
                + (
                    [f"AssessmentBasis:{assessment_basis_digest}"]
                    if assessment_basis_digest
                    else []
                ),
                evidence_refs=list(execution.get("evidence_refs_json") or [])
                + _assessment_basis_evidence_refs(assessment_basis),
                limitation_refs=list(dict.fromkeys(limitations)),
            )
        )

    for finding in _rows(facts.get("findings")):
        finding_id = str(finding.get("id") or "")
        if not finding_id:
            continue
        finding_basis = dict(finding.get("assessment_basis") or {})
        finding_basis_digest = str(finding.get("assessment_basis_digest") or "").strip()
        finding_subject = {
            key: value for key, value in finding.items() if key != "assessment_basis"
        }
        finding_subject["assessment_basis_summary"] = dict(finding_basis.get("summary") or {})
        claims.append(
            _claim(
                batch_id=batch_id,
                basis_digest=basis_digest,
                authority_context_digest=authority_digest,
                claim_code=f"finding.{finding_id}",
                claim_scope="entity",
                assertion_level=_report_assertion_level(
                    report_purpose=report_purpose,
                    current_ready=current_ready,
                ),
                allowed_phrase_code=(f"finding_{finding.get('event_type') or 'current_state'}"),
                subject=finding_subject,
                fact_refs=[f"CurrentStateFinding:{finding_id}"]
                + ([f"AssessmentBasis:{finding_basis_digest}"] if finding_basis_digest else []),
                evidence_refs=list(finding.get("evidence_refs") or [])
                + _assessment_basis_evidence_refs(finding_basis),
            )
        )

    occurrence_by_risk: dict[str, list[dict[str, Any]]] = {}
    for occurrence in _rows(facts.get("risk_occurrences")):
        occurrence_by_risk.setdefault(str(occurrence.get("risk_case_id") or ""), []).append(
            occurrence
        )
    for risk in _rows(facts.get("risks")):
        risk_id = str(risk.get("id") or "")
        if not risk_id:
            continue
        verification = str(risk.get("verification_status") or "not_reverified")
        current = bool(current_ready and verification == "confirmed_current")
        limitations = [] if current else [f"risk_verification:{verification}"]
        related_occurrences = occurrence_by_risk.get(risk_id, [])
        claims.append(
            _claim(
                batch_id=batch_id,
                basis_digest=basis_digest,
                authority_context_digest=authority_digest,
                claim_code=f"risk.{risk_id}",
                claim_scope=("entity" if risk.get("entity_key") else "device"),
                assertion_level=(
                    "current"
                    if current
                    else "historical"
                    if report_purpose == OFFLINE_HISTORICAL
                    else "limited"
                ),
                allowed_phrase_code=f"risk_{verification}",
                subject=risk,
                fact_refs=[f"RiskCase:{risk_id}"]
                + [
                    f"RiskOccurrence:{item.get('id')}"
                    for item in related_occurrences
                    if item.get("id")
                ],
                evidence_refs=(
                    list(risk.get("resolution_evidence_refs_json") or [])
                    + list(risk.get("source_finding_ids_json") or [])
                    + list(risk.get("source_event_ids_json") or [])
                ),
                limitation_refs=limitations,
            )
        )

    risk_rows = _rows(facts.get("risks"))
    risk_by_id = {
        str(item.get("id") or ""): item for item in risk_rows if str(item.get("id") or "")
    }
    grouped_projection = build_risk_issue_projection(
        risks=risk_rows,
        associations=_rows(facts.get("risk_episode_associations")),
        event_occurrences=_rows(facts.get("event_occurrences")),
    )
    potential_issues = {
        str(item.get("issue_id") or ""): item
        for item in _rows(grouped_projection.get("issues"))
        if str(item.get("issue_id") or "")
    }
    for risk in risk_rows:
        individual = build_risk_issue_projection(
            risks=[risk],
            associations=[],
            event_occurrences=_rows(facts.get("event_occurrences")),
        )
        for item in _rows(individual.get("issues")):
            potential_issues.setdefault(
                str(item.get("issue_id") or ""),
                item,
            )
    association_by_id = {
        str(item.get("id") or ""): item
        for item in _rows(facts.get("risk_episode_associations"))
        if str(item.get("id") or "")
    }
    for issue_id, issue in sorted(potential_issues.items()):
        member_ids = [
            str(item.get("risk_case_id") or "")
            for item in _rows(issue.get("members"))
            if str(item.get("risk_case_id") or "")
        ]
        members = [risk_by_id[item] for item in member_ids if item in risk_by_id]
        current_issue = bool(
            current_ready
            and members
            and all(
                str(item.get("verification_status") or "") == "confirmed_current"
                and str(item.get("status") or "") not in {"resolved", "suppressed"}
                for item in members
            )
        )
        association_ids = [str(item) for item in issue.get("association_ids") or [] if str(item)]
        association_rows = [
            association_by_id[item] for item in association_ids if item in association_by_id
        ]
        claims.append(
            _claim(
                batch_id=batch_id,
                basis_digest=basis_digest,
                authority_context_digest=authority_digest,
                claim_code=f"risk_issue.{issue_id}",
                claim_scope=(
                    "device_group" if int(issue.get("affected_device_count") or 0) > 1 else "entity"
                ),
                assertion_level=(
                    "current"
                    if current_issue
                    else "historical"
                    if report_purpose == OFFLINE_HISTORICAL
                    else "limited"
                ),
                allowed_phrase_code=(
                    "risk_issue_correlated"
                    if bool(issue.get("correlation_applied"))
                    else "risk_issue_individual"
                ),
                subject=issue,
                fact_refs=[
                    f"RiskIssue:{issue_id}",
                    *(f"RiskCase:{item}" for item in member_ids),
                    *(f"RiskEpisodeAssociation:{item}" for item in association_ids),
                ],
                evidence_refs=[
                    *(
                        evidence
                        for member in members
                        for evidence in (
                            list(member.get("source_finding_ids_json") or [])
                            + list(member.get("source_event_ids_json") or [])
                        )
                    ),
                    *(
                        evidence
                        for association in association_rows
                        for evidence in association.get("evidence_refs_json") or []
                    ),
                ],
                limitation_refs=([] if current_issue else ["risk_issue_not_confirmed_current"]),
            )
        )

    repeated_fact_sets = (
        ("change", "NetworkChange", "changes", "observed"),
        ("event", "NetworkEvent", "events", "assessed"),
        (
            "event_occurrence",
            "NetworkEventOccurrence",
            "event_occurrences",
            "assessed",
        ),
        (
            "remediation",
            "RiskRemediation",
            "risk_remediations",
            "assessed",
        ),
    )
    for prefix, fact_type, source_key, default_level in repeated_fact_sets:
        for item in _rows(facts.get(source_key)):
            item_id = str(item.get("id") or "")
            if not item_id:
                continue
            evidence = []
            for key in (
                "evidence_refs_json",
                "evidence_refs",
                "current_snapshot_id",
                "previous_snapshot_id",
                "source_change_id",
            ):
                value = item.get(key)
                evidence.extend(value if isinstance(value, list) else [value])
            level = (
                "historical"
                if report_purpose == OFFLINE_HISTORICAL
                else "current"
                if current_ready and prefix in {"event", "event_occurrence"}
                else default_level
            )
            phrase_state = (
                item.get("status")
                or item.get("change_type")
                or item.get("occurrence_kind")
                or "recorded"
            )
            claims.append(
                _claim(
                    batch_id=batch_id,
                    basis_digest=basis_digest,
                    authority_context_digest=authority_digest,
                    claim_code=f"{prefix}.{item_id}",
                    claim_scope=(
                        "entity" if item.get("entity_key") or item.get("risk_case_id") else "device"
                    ),
                    assertion_level=level,
                    allowed_phrase_code=f"{prefix}_{phrase_state}",
                    subject=item,
                    fact_refs=[f"{fact_type}:{item_id}"],
                    evidence_refs=evidence,
                    limitation_refs=(
                        [f"report_purpose:{report_purpose}"]
                        if report_purpose != PRODUCTION_CURRENT
                        else []
                    ),
                )
            )

    claims.sort(key=lambda item: str(item["claim_code"]))
    ledger = {
        "schema_version": REPORT_CLAIM_LEDGER_SCHEMA_VERSION,
        "patrol_batch_id": batch_id,
        "claim_basis_digest": basis_digest,
        "authority_context_digest": authority_digest,
        "claims": claims,
    }
    ledger["claim_ledger_digest"] = _digest(ledger)
    return ledger


def validate_report_claim_ledger(
    source_facts: Mapping[str, Any],
) -> None:
    ledger = dict(source_facts.get("claim_ledger") or {})
    if not ledger:
        raise ValueError("report source facts have no claim ledger")
    expected = build_report_claim_ledger(source_facts)
    if ledger != expected:
        raise ValueError("report claim ledger does not match the frozen source facts")
    if str(source_facts.get("claim_basis_digest") or "") != str(expected["claim_basis_digest"]):
        raise ValueError("report claim basis digest is invalid")
    if str(source_facts.get("claim_ledger_digest") or "") != str(expected["claim_ledger_digest"]):
        raise ValueError("report claim ledger digest is invalid")


class ReportClaimService:
    """Persist and query the normalized projection of a frozen claim ledger."""

    def __init__(
        self,
        session_scope: Callable[[], AbstractContextManager[Any]] = get_db_session,
    ) -> None:
        self._session_scope = session_scope

    @staticmethod
    def persist_in_session(
        db: Any,
        *,
        report_manifest_id: str,
        patrol_batch_id: str,
        source_facts: Mapping[str, Any],
        created_at: datetime | None = None,
    ) -> list[dict[str, Any]]:
        validate_report_claim_ledger(source_facts)
        claims = _rows(dict(source_facts.get("claim_ledger") or {}).get("claims"))
        existing = (
            db.query(ReportClaimRecord)
            .filter(ReportClaimRecord.report_manifest_id == report_manifest_id)
            .order_by(ReportClaimRecord.claim_code)
            .all()
        )
        if existing:
            if [str(item.claim_digest) for item in existing] != [
                str(item["claim_digest"]) for item in claims
            ]:
                raise ValueError("persisted report claims do not match the immutable ledger")
            return [item.to_dict() for item in existing]
        occurred_at = created_at or datetime.now(timezone.utc)
        rows: list[ReportClaimRecord] = []
        for claim in claims:
            row = ReportClaimRecord(
                id=str(claim["claim_id"]),
                report_manifest_id=report_manifest_id,
                patrol_batch_id=patrol_batch_id,
                claim_code=str(claim["claim_code"]),
                claim_scope=str(claim["claim_scope"]),
                assertion_level=str(claim["assertion_level"]),
                allowed_phrase_code=str(claim["allowed_phrase_code"]),
                subject_json=dict(claim.get("subject") or {}),
                fact_refs_json=list(claim.get("fact_refs") or []),
                evidence_refs_json=list(claim.get("evidence_refs") or []),
                limitation_refs_json=list(claim.get("limitation_refs") or []),
                authority_context_digest=str(claim["authority_context_digest"]),
                source_facts_digest=str(claim["source_facts_digest"]),
                claim_digest=str(claim["claim_digest"]),
                created_at=occurred_at,
            )
            db.add(row)
            rows.append(row)
        db.flush()
        return [item.to_dict() for item in rows]

    def list_for_manifest(self, report_manifest_id: str) -> list[dict[str, Any]]:
        with self._session_scope() as db:
            rows = (
                db.query(ReportClaimRecord)
                .filter(ReportClaimRecord.report_manifest_id == report_manifest_id)
                .order_by(ReportClaimRecord.claim_code)
                .all()
            )
            return [row.to_dict() for row in rows]

    def page_for_manifest(
        self,
        report_manifest_id: str,
        *,
        page: int = 1,
        page_size: int = 50,
        search: str | None = None,
        claim_scope: str | None = None,
        assertion_level: str | None = None,
    ) -> dict[str, Any]:
        normalized_page = max(int(page), 1)
        normalized_size = min(max(int(page_size), 1), 200)
        with self._session_scope() as db:
            base_query = db.query(ReportClaimRecord).filter(
                ReportClaimRecord.report_manifest_id == report_manifest_id
            )
            persisted_total = int(base_query.count())
            query = base_query
            needle = str(search or "").strip()
            if needle:
                pattern = f"%{needle}%"
                query = query.filter(
                    or_(
                        ReportClaimRecord.id.ilike(pattern),
                        ReportClaimRecord.claim_code.ilike(pattern),
                        ReportClaimRecord.allowed_phrase_code.ilike(pattern),
                    )
                )
            if claim_scope:
                query = query.filter(ReportClaimRecord.claim_scope == str(claim_scope))
            if assertion_level:
                query = query.filter(ReportClaimRecord.assertion_level == str(assertion_level))
            total = int(query.count())
            rows = (
                query.order_by(ReportClaimRecord.claim_code)
                .offset((normalized_page - 1) * normalized_size)
                .limit(normalized_size)
                .all()
            )
            return {
                "total": total,
                "persisted_total": persisted_total,
                "page": normalized_page,
                "page_size": normalized_size,
                "items": [row.to_dict() for row in rows],
            }

    def list_for_batch(self, patrol_batch_id: str) -> list[dict[str, Any]]:
        with self._session_scope() as db:
            manifest = (
                db.query(ReportManifestRecord)
                .filter(ReportManifestRecord.patrol_batch_id == patrol_batch_id)
                .order_by(
                    ReportManifestRecord.version.desc(),
                    ReportManifestRecord.id.desc(),
                )
                .first()
            )
            if manifest is None:
                return []
            rows = (
                db.query(ReportClaimRecord)
                .filter(ReportClaimRecord.report_manifest_id == manifest.id)
                .order_by(ReportClaimRecord.claim_code)
                .all()
            )
            return [row.to_dict() for row in rows]


__all__ = [
    "REPORT_CLAIM_LEDGER_SCHEMA_VERSION",
    "ReportClaimService",
    "build_report_claim_ledger",
    "report_claim_basis_digest",
    "report_claim_basis_facts",
    "validate_report_claim_ledger",
]
