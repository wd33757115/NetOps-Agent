"""Frozen, scalable content acceptance before formal report release."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import String, cast, func, or_

from src.common.governance import actor_matches_any
from src.core.check_items.report_claims import validate_report_claim_ledger
from src.core.check_items.reporting import (
    build_check_item_report_view,
    verify_attached_report_artifacts,
)
from src.infrastructure.db.models import (
    ReportClaimRecord,
    ReportContentReviewItemRecord,
    ReportContentReviewRecord,
    ReportManifestRecord,
)
from src.infrastructure.db.postgres import get_db_session


CONTENT_REVIEW_SCHEMA_VERSION = "report-content-review/v1"
REQUIRED_REPORT_ARTIFACTS = {
    "markdown",
    "docx",
    "excel",
    "json",
    "evidence",
}
REQUIRED_CONTENT_CONFIRMATIONS = {
    "machine_reconciliation_reviewed",
    "critical_unknown_and_lifecycle_reviewed",
    "sampled_facts_reviewed",
    "traceability_verified",
    "narrative_readability_accepted",
    "actions_actionable",
}
HIGH_SEVERITIES = {"critical", "major", "p0", "p1"}
REVIEW_CATEGORIES = {
    "p0_p1",
    "unable_to_determine",
    "scope_exclusion",
    "recovery",
    "closure",
    "recovery_or_closure",
    "remediation",
    "ordinary_fact_sample",
    "check_item_traceability",
}
FINAL_REPORT_SOURCE_SCHEMA = "patrol-report-source/v5"
FINAL_REPORT_MANIFEST_SCHEMA = "patrol-report-manifest/v5"


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
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _valid_sha256(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return len(text) == 64 and all(char in "0123456789abcdef" for char in text)


def _pair(row: Mapping[str, Any]) -> tuple[str, str]:
    return (
        str(row.get("device_id") or "").strip(),
        str(row.get("check_item_version_id") or "").strip(),
    )


def _fact_id(row: Mapping[str, Any], *, prefix: str) -> str:
    return str(
        row.get("id")
        or row.get("claim_id")
        or row.get("execution_id")
        or f"{prefix}:{_digest(dict(row))}"
    )


def _machine_validation(
    db: Any,
    manifest: ReportManifestRecord,
    *,
    object_storage: Any,
) -> dict[str, Any]:
    payload = manifest.to_dict()
    source_facts = dict(manifest.source_facts_json or {})
    checks: list[dict[str, Any]] = []

    def add(check_id: str, passed: bool, detail: Mapping[str, Any]) -> None:
        checks.append(
            {
                "id": check_id,
                "passed": bool(passed),
                "detail": dict(detail),
            }
        )

    add(
        "manifest_final_v5",
        str(manifest.status) == "final"
        and str(manifest.schema_version) == FINAL_REPORT_MANIFEST_SCHEMA
        and str(source_facts.get("schema_version") or "") == FINAL_REPORT_SOURCE_SCHEMA,
        {
            "manifest_status": str(manifest.status),
            "manifest_schema": str(manifest.schema_version),
            "source_schema": str(source_facts.get("schema_version") or ""),
        },
    )
    add(
        "source_facts_digest",
        _digest(source_facts) == str(manifest.source_digest),
        {"source_facts_digest": str(manifest.source_digest)},
    )

    view_error = ""
    try:
        build_check_item_report_view(payload)
        validate_report_claim_ledger(source_facts)
    except (AssertionError, KeyError, TypeError, ValueError) as exc:
        view_error = f"{type(exc).__name__}: {str(exc)[:300]}"
    add("report_contract_validation", not view_error, {"error": view_error or None})

    artifacts = {
        str(name): str(value).strip().lower()
        for name, value in dict(manifest.artifact_sha256_json or {}).items()
    }
    object_keys = {
        str(name): str(value)
        for name, value in dict(manifest.artifact_object_keys_json or {}).items()
    }
    artifact_names = set(artifacts)
    add(
        "artifact_set_complete",
        artifact_names == set(object_keys)
        and REQUIRED_REPORT_ARTIFACTS.issubset(artifact_names)
        and all(_valid_sha256(value) for value in artifacts.values()),
        {
            "artifact_names": sorted(artifact_names),
            "required_artifact_names": sorted(REQUIRED_REPORT_ARTIFACTS),
        },
    )
    artifact_integrity = verify_attached_report_artifacts(
        payload,
        object_storage=object_storage,
    )
    add(
        "artifact_objects_verified",
        bool(artifact_integrity["passed"]),
        artifact_integrity,
    )

    ledger = dict(source_facts.get("claim_ledger") or {})
    frozen_claims = _rows(ledger.get("claims"))
    persisted_claims = (
        db.query(ReportClaimRecord)
        .filter(ReportClaimRecord.report_manifest_id == manifest.id)
        .order_by(ReportClaimRecord.claim_code)
        .all()
    )
    persisted_digests = [str(item.claim_digest) for item in persisted_claims]
    frozen_digests = [str(item.get("claim_digest") or "") for item in frozen_claims]
    add(
        "claim_ledger_reconciled",
        bool(str(manifest.claim_ledger_digest or ""))
        and str(ledger.get("claim_ledger_digest") or "") == str(manifest.claim_ledger_digest or "")
        and len(frozen_claims) == int(manifest.claim_count or 0)
        and persisted_digests == frozen_digests,
        {
            "manifest_claim_count": int(manifest.claim_count or 0),
            "frozen_claim_count": len(frozen_claims),
            "persisted_claim_count": len(persisted_claims),
        },
    )

    target_pairs = _rows(source_facts.get("target_pairs"))
    executions = _rows(source_facts.get("executions"))
    assessments = _rows(source_facts.get("assessments"))
    expected_pairs = {_pair(item) for item in target_pairs}
    execution_pairs = {_pair(item) for item in executions}
    assessment_pairs = {_pair(item) for item in assessments}
    valid_pairs = all(all(part for part in pair) for pair in expected_pairs)
    add(
        "target_execution_assessment_reconciled",
        bool(expected_pairs)
        and valid_pairs
        and expected_pairs == execution_pairs == assessment_pairs
        and len(expected_pairs) == len(target_pairs)
        and len(execution_pairs) == len(executions)
        and len(assessment_pairs) == len(assessments),
        {
            "expected_target_count": len(expected_pairs),
            "execution_count": len(executions),
            "assessment_count": len(assessments),
        },
    )
    unaccounted = [
        str(item.get("id") or _pair(item))
        for item in executions
        if not (item.get("evidence_qualification_id") and item.get("coverage_decision_id"))
        and not list(item.get("reason_codes_json") or [])
    ]
    add(
        "evidence_accounted",
        not unaccounted,
        {"unaccounted_execution_ids": unaccounted[:100], "count": len(unaccounted)},
    )
    readiness = dict(source_facts.get("readiness") or {})
    add(
        "terminal_report_readiness",
        readiness.get("ready_for_final") is True,
        {
            "ready_for_final": readiness.get("ready_for_final"),
            "reasons": list(readiness.get("reasons") or [])[:100],
        },
    )
    passed = all(item["passed"] for item in checks)
    result = {
        "schema_version": "report-content-machine-validation/v1",
        "passed": passed,
        "checks": checks,
        "failed_check_ids": [item["id"] for item in checks if not item["passed"]],
    }
    result["digest"] = _digest(result)
    return result


def _build_review_plan(manifest: ReportManifestRecord) -> dict[str, Any]:
    facts = dict(manifest.source_facts_json or {})
    source_digest = str(manifest.source_digest or "")
    check_item_by_version = {
        str(key): str(value)
        for key, value in dict(facts.get("check_item_by_version") or {}).items()
    }
    evidence_index = _rows(facts.get("evidence_index"))
    evidence_by_pair: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for evidence in evidence_index:
        device_id = str(evidence.get("device_id") or "")
        for check_item_id in evidence.get("check_item_ids") or []:
            evidence_by_pair[(device_id, str(check_item_id))].append(evidence)

    items: dict[tuple[str, str], dict[str, Any]] = {}

    def add_item(
        *,
        category: str,
        fact_type: str,
        row: Mapping[str, Any],
        check_item_id: str = "",
        device_id: str = "",
        severity: str = "",
        conclusion_status: str = "",
        stratum_key: str = "",
        evidence_refs: Iterable[Any] = (),
        summary: Mapping[str, Any] | None = None,
    ) -> None:
        fact_id = _fact_id(row, prefix=fact_type)
        key = (fact_type, fact_id)
        existing = items.get(key)
        if existing is not None:
            reasons = set(existing["summary"].get("review_reasons") or [])
            reasons.add(category)
            existing["summary"]["review_reasons"] = sorted(reasons)
            return
        review_item_id = _digest([str(manifest.id), source_digest, fact_type, fact_id])
        safe_summary = dict(summary or {})
        safe_summary["review_reasons"] = [category]
        items[key] = {
            "review_item_id": review_item_id,
            "category": category,
            "stratum_key": stratum_key or None,
            "fact_type": fact_type,
            "fact_id": fact_id,
            "device_id": device_id or None,
            "check_item_id": check_item_id or None,
            "severity": severity or None,
            "conclusion_status": conclusion_status or None,
            "evidence_refs": sorted({str(item) for item in evidence_refs if str(item).strip()}),
            "summary": safe_summary,
        }

    high_risk_pairs: set[tuple[str, str]] = set()
    for fact_type in ("findings", "risks", "events"):
        for row in _rows(facts.get(fact_type)):
            severity = str(row.get("severity") or "").lower()
            if severity not in HIGH_SEVERITIES:
                continue
            check_item_id = str(
                row.get("check_item_id")
                or check_item_by_version.get(
                    str(row.get("check_item_version_id") or ""),
                    "",
                )
            )
            device_id = str(row.get("device_id") or "")
            high_risk_pairs.add((device_id, check_item_id))
            add_item(
                category="p0_p1",
                fact_type=fact_type.rstrip("s"),
                row=row,
                check_item_id=check_item_id,
                device_id=device_id,
                severity=severity,
                conclusion_status=str(row.get("status") or ""),
                evidence_refs=(
                    row.get("evidence_refs")
                    or row.get("evidence_refs_json")
                    or row.get("source_finding_ids_json")
                    or []
                ),
                summary={
                    "risk_type": row.get("risk_type") or row.get("event_type"),
                    "entity_type": row.get("entity_type"),
                    "entity_key": row.get("entity_key"),
                    "field": row.get("field"),
                    "actual_value": (
                        row.get("actual_value")
                        if "actual_value" in row
                        else row.get("current_value")
                    ),
                    "reference_value": row.get("reference_value"),
                    "reference_source": row.get("reference_source"),
                    "unit": row.get("unit"),
                    "status": row.get("status"),
                    "owner": row.get("owner"),
                    "due_at": row.get("due_at"),
                },
            )

    assessments = _rows(facts.get("assessments"))
    executions = {str(item.get("id") or ""): item for item in _rows(facts.get("executions"))}
    for row in assessments:
        if str(row.get("assessment_status") or "") != "unable_to_determine":
            continue
        check_item_id = check_item_by_version.get(
            str(row.get("check_item_version_id") or ""),
            "",
        )
        add_item(
            category="unable_to_determine",
            fact_type="assessment",
            row=row,
            check_item_id=check_item_id,
            device_id=str(row.get("device_id") or ""),
            conclusion_status="unable_to_determine",
            evidence_refs=[
                row.get("evidence_qualification_id"),
                row.get("coverage_decision_id"),
            ],
            summary={
                "blocking_reason_codes": list(row.get("blocking_reason_codes_json") or []),
                "completeness_status": row.get("completeness_status"),
                "comparison_status": row.get("comparison_status"),
            },
        )

    for row in _rows(facts.get("scope_decisions")):
        if str(row.get("decision") or "") not in {"excluded", "not_applicable"}:
            continue
        check_item_id = check_item_by_version.get(
            str(row.get("check_item_version_id") or ""),
            str(row.get("check_item_id") or ""),
        )
        add_item(
            category="scope_exclusion",
            fact_type="scope_decision",
            row=row,
            check_item_id=check_item_id,
            device_id=str(row.get("device_id") or ""),
            conclusion_status=str(row.get("decision") or ""),
            evidence_refs=row.get("evidence_refs") or [],
            summary={"reason_codes": list(row.get("reason_codes") or [])},
        )

    for row in _rows(facts.get("risks")):
        status = str(row.get("status") or "").lower()
        verification = str(row.get("verification_status") or "").lower()
        category = ""
        if verification == "recovery_pending":
            category = "recovery"
        elif status in {"closed", "resolved"}:
            category = "closure"
        if category:
            add_item(
                category=category,
                fact_type="risk",
                row=row,
                check_item_id=str(row.get("check_item_id") or ""),
                device_id=str(row.get("device_id") or ""),
                severity=str(row.get("severity") or ""),
                conclusion_status=verification or status,
                evidence_refs=row.get("resolution_evidence_refs_json") or [],
                summary={
                    "risk_type": row.get("risk_type"),
                    "status": status,
                    "verification_status": verification,
                },
            )

    for row in _rows(facts.get("risk_occurrences")):
        occurrence_type = str(row.get("occurrence_type") or row.get("status") or "").lower()
        if occurrence_type not in {"recovery", "recovered", "resolved", "closed"}:
            continue
        add_item(
            category="recovery_or_closure",
            fact_type="risk_occurrence",
            row=row,
            check_item_id=str(row.get("check_item_id") or ""),
            device_id=str(row.get("device_id") or ""),
            conclusion_status=occurrence_type,
            evidence_refs=row.get("evidence_refs_json") or [],
            summary={"occurrence_type": occurrence_type},
        )

    for row in _rows(facts.get("risk_remediations")):
        status = str(row.get("status") or "").lower()
        add_item(
            category="remediation",
            fact_type="risk_remediation",
            row=row,
            conclusion_status=status,
            evidence_refs=row.get("evidence_refs_json") or [],
            summary={
                "risk_case_id": row.get("risk_case_id"),
                "status": status,
                "owner": row.get("owner"),
                "due_at": row.get("due_at"),
                "action_text": row.get("action_text"),
            },
        )

    ordinary_by_stratum: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for assessment in assessments:
        if str(assessment.get("assessment_status") or "") == "unable_to_determine":
            continue
        version_id = str(assessment.get("check_item_version_id") or "")
        check_item_id = check_item_by_version.get(version_id, "")
        device_id = str(assessment.get("device_id") or "")
        if (device_id, check_item_id) in high_risk_pairs:
            continue
        execution = executions.get(str(assessment.get("execution_id") or ""), {})
        evidence_rows = evidence_by_pair.get((device_id, check_item_id), [])
        profile_parts = sorted(
            {
                "/".join(
                    [
                        str(item.get("vendor") or "unknown"),
                        str(item.get("model") or "unknown"),
                        str(
                            item.get("implementation_version_id")
                            or item.get("parser_template_id")
                            or execution.get("implementation_version_id")
                            or "unknown"
                        ),
                    ]
                )
                for item in evidence_rows
            }
        )
        compatibility_profile = profile_parts[0] if profile_parts else "unknown/unknown/unknown"
        conclusion_status = str(assessment.get("assessment_status") or "unknown")
        stratum_key = "|".join(
            [check_item_id or "unknown", compatibility_profile, conclusion_status]
        )
        ordinary_by_stratum[stratum_key].append(
            {
                "assessment": assessment,
                "check_item_id": check_item_id,
                "device_id": device_id,
                "conclusion_status": conclusion_status,
                "evidence_refs": [item.get("snapshot_id") for item in evidence_rows],
                "compatibility_profile": compatibility_profile,
            }
        )
    for stratum_key, candidates in sorted(ordinary_by_stratum.items()):
        ordered = sorted(
            candidates,
            key=lambda item: _digest(
                [
                    source_digest,
                    stratum_key,
                    _fact_id(item["assessment"], prefix="assessment"),
                ]
            ),
        )
        for candidate in ordered[:3]:
            assessment = candidate["assessment"]
            add_item(
                category="ordinary_fact_sample",
                fact_type="assessment",
                row=assessment,
                check_item_id=candidate["check_item_id"],
                device_id=candidate["device_id"],
                conclusion_status=candidate["conclusion_status"],
                stratum_key=stratum_key,
                evidence_refs=candidate["evidence_refs"],
                summary={
                    "compatibility_profile": candidate["compatibility_profile"],
                    "completeness_status": assessment.get("completeness_status"),
                    "comparison_status": assessment.get("comparison_status"),
                },
            )

    by_check_item: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for assessment in assessments:
        check_item_id = check_item_by_version.get(
            str(assessment.get("check_item_version_id") or ""),
            "",
        )
        if check_item_id:
            by_check_item[check_item_id].append(assessment)
    for check_item_id, candidates in sorted(by_check_item.items()):
        selected = sorted(
            candidates,
            key=lambda item: _digest(
                [source_digest, check_item_id, _fact_id(item, prefix="assessment")]
            ),
        )[0]
        add_item(
            category="check_item_traceability",
            fact_type="assessment",
            row=selected,
            check_item_id=check_item_id,
            device_id=str(selected.get("device_id") or ""),
            conclusion_status=str(selected.get("assessment_status") or ""),
            evidence_refs=[
                selected.get("evidence_qualification_id"),
                selected.get("coverage_decision_id"),
            ],
            summary={"traceability_required": True},
        )

    frozen_items = sorted(items.values(), key=lambda item: item["review_item_id"])
    category_counts: dict[str, int] = defaultdict(int)
    for item in frozen_items:
        item["review_reasons"] = list(item["summary"].get("review_reasons") or [item["category"]])
        for reason in item["review_reasons"]:
            category_counts[str(reason)] += 1
    payload = {
        "schema_version": CONTENT_REVIEW_SCHEMA_VERSION,
        "report_manifest_id": str(manifest.id),
        "patrol_batch_id": str(manifest.patrol_batch_id),
        "source_facts_digest": source_digest,
        "claim_ledger_digest": str(manifest.claim_ledger_digest or ""),
        "artifact_sha256": dict(manifest.artifact_sha256_json or {}),
        "sampling_rule": {
            "ordinary_fact_sample_size_per_stratum": 3,
            "stratum_dimensions": [
                "check_item_id",
                "compatibility_profile",
                "conclusion_status",
            ],
            "selection": "sha256_deterministic",
        },
        "category_counts": dict(sorted(category_counts.items())),
        "items": frozen_items,
    }
    payload["review_plan_digest"] = _digest(payload)
    return payload


class ReportContentReviewService:
    """Create, progress and accept one immutable review plan per manifest."""

    def __init__(
        self,
        session_scope: Callable[[], AbstractContextManager[Any]] = get_db_session,
        *,
        object_storage: Any | None = None,
    ) -> None:
        self._session_scope = session_scope
        self._object_storage = object_storage

    def _storage(self) -> Any:
        if self._object_storage is None:
            from src.infrastructure.storage.minio_client import (
                get_minio_storage,
            )

            self._object_storage = get_minio_storage()
        return self._object_storage

    @staticmethod
    def _summary(row: ReportContentReviewRecord) -> dict[str, Any]:
        pending = max(
            0,
            int(row.required_item_count or 0)
            - int(row.accepted_item_count or 0)
            - int(row.rejected_item_count or 0),
        )
        return {
            **row.to_dict(),
            "pending_item_count": pending,
            "can_accept": (
                str(row.status) == "in_review"
                and pending == 0
                and int(row.rejected_item_count or 0) == 0
            ),
        }

    @staticmethod
    def _refresh_counts(db: Any, row: ReportContentReviewRecord) -> None:
        counts = {
            str(status): int(count)
            for status, count in (
                db.query(
                    ReportContentReviewItemRecord.review_status,
                    func.count(),
                )
                .filter(ReportContentReviewItemRecord.review_id == row.id)
                .group_by(ReportContentReviewItemRecord.review_status)
                .all()
            )
        }
        row.accepted_item_count = counts.get("accepted", 0)
        row.rejected_item_count = counts.get("rejected", 0)
        if row.rejected_item_count:
            row.status = "revision_required"
        row.updated_at = datetime.now(timezone.utc)

    def preview(self, report_manifest_id: str) -> dict[str, Any]:
        with self._session_scope() as db:
            manifest = db.get(ReportManifestRecord, report_manifest_id)
            if manifest is None:
                raise ValueError("report manifest not found")
            existing = (
                db.query(ReportContentReviewRecord)
                .filter(ReportContentReviewRecord.report_manifest_id == manifest.id)
                .first()
            )
            machine = _machine_validation(
                db,
                manifest,
                object_storage=self._storage(),
            )
            if existing is not None:
                return {
                    "review_status": str(existing.status),
                    "review": self._summary(existing),
                    "machine_validation": machine,
                    "machine_validation_at_start": dict(existing.machine_validation_json or {}),
                }
            plan = _build_review_plan(manifest)
            return {
                "review_status": "not_started",
                "review": None,
                "machine_validation": machine,
                "review_plan_digest": plan["review_plan_digest"],
                "required_item_count": len(plan["items"]),
                "category_counts": plan["category_counts"],
                "sampling_rule": plan["sampling_rule"],
                "can_start": bool(machine["passed"]),
            }

    def start(self, report_manifest_id: str, *, created_by: str) -> dict[str, Any]:
        actor = str(created_by or "").strip()
        if not actor:
            raise ValueError("content review requires an actor")
        with self._session_scope() as db:
            manifest = (
                db.query(ReportManifestRecord)
                .filter(ReportManifestRecord.id == report_manifest_id)
                .with_for_update()
                .first()
            )
            if manifest is None:
                raise ValueError("report manifest not found")
            if actor_matches_any(actor, [manifest.generated_by]):
                raise ValueError(
                    "content review requires an actor different from the manifest generator"
                )
            existing = (
                db.query(ReportContentReviewRecord)
                .filter(ReportContentReviewRecord.report_manifest_id == manifest.id)
                .first()
            )
            if existing is not None:
                return self._summary(existing)
            machine = _machine_validation(
                db,
                manifest,
                object_storage=self._storage(),
            )
            if not machine["passed"]:
                raise ValueError(
                    "report content machine validation failed: "
                    + ", ".join(machine["failed_check_ids"])
                )
            plan = _build_review_plan(manifest)
            now = datetime.now(timezone.utc)
            review_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"netops/report-content-review/{plan['review_plan_digest']}",
                )
            )
            row = ReportContentReviewRecord(
                id=review_id,
                report_manifest_id=manifest.id,
                patrol_batch_id=manifest.patrol_batch_id,
                schema_version=CONTENT_REVIEW_SCHEMA_VERSION,
                status="in_review",
                source_facts_digest=manifest.source_digest,
                claim_ledger_digest=str(manifest.claim_ledger_digest or ""),
                artifact_sha256_json=dict(manifest.artifact_sha256_json or {}),
                review_plan_digest=plan["review_plan_digest"],
                machine_validation_json=machine,
                machine_validation_digest=machine["digest"],
                required_item_count=len(plan["items"]),
                accepted_item_count=0,
                rejected_item_count=0,
                required_confirmations_json=sorted(REQUIRED_CONTENT_CONFIRMATIONS),
                created_by=actor,
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            db.flush()
            db.bulk_save_objects(
                [
                    ReportContentReviewItemRecord(
                        id=str(
                            uuid.uuid5(
                                uuid.NAMESPACE_URL,
                                f"netops/report-content-review-item/{review_id}/{item['review_item_id']}",
                            )
                        ),
                        review_id=review_id,
                        review_item_id=item["review_item_id"],
                        category=item["category"],
                        review_reasons_json=item["review_reasons"],
                        stratum_key=item["stratum_key"],
                        fact_type=item["fact_type"],
                        fact_id=item["fact_id"],
                        device_id=item["device_id"],
                        check_item_id=item["check_item_id"],
                        severity=item["severity"],
                        conclusion_status=item["conclusion_status"],
                        evidence_refs_json=item["evidence_refs"],
                        summary_json=item["summary"],
                        review_status="pending",
                        created_at=now,
                    )
                    for item in plan["items"]
                ]
            )
            db.flush()
            return self._summary(row)

    def get_for_manifest(self, report_manifest_id: str) -> dict[str, Any] | None:
        with self._session_scope() as db:
            row = (
                db.query(ReportContentReviewRecord)
                .filter(ReportContentReviewRecord.report_manifest_id == report_manifest_id)
                .first()
            )
            return self._summary(row) if row is not None else None

    def list_for_manifest_ids(
        self,
        report_manifest_ids: Iterable[str],
    ) -> dict[str, dict[str, Any]]:
        ids = sorted({str(item).strip() for item in report_manifest_ids if str(item).strip()})
        if not ids:
            return {}
        if len(ids) > 200:
            raise ValueError("content review batch lookup is limited to 200 manifests")
        with self._session_scope() as db:
            rows = (
                db.query(ReportContentReviewRecord)
                .filter(ReportContentReviewRecord.report_manifest_id.in_(ids))
                .all()
            )
            return {str(row.report_manifest_id): self._summary(row) for row in rows}

    def list_items(
        self,
        report_manifest_id: str,
        *,
        page: int = 1,
        page_size: int = 50,
        review_status: str | None = None,
        category: str | None = None,
    ) -> dict[str, Any]:
        if page < 1 or page_size < 1 or page_size > 200:
            raise ValueError("invalid content review pagination")
        normalized_category = str(category or "").strip() or None
        if normalized_category and normalized_category not in REVIEW_CATEGORIES:
            raise ValueError("invalid content review category")
        with self._session_scope() as db:
            review = (
                db.query(ReportContentReviewRecord)
                .filter(ReportContentReviewRecord.report_manifest_id == report_manifest_id)
                .first()
            )
            if review is None:
                raise ValueError("report content review not found")
            query = db.query(ReportContentReviewItemRecord).filter(
                ReportContentReviewItemRecord.review_id == review.id
            )
            if review_status:
                query = query.filter(ReportContentReviewItemRecord.review_status == review_status)
            if normalized_category:
                query = query.filter(
                    or_(
                        ReportContentReviewItemRecord.category == normalized_category,
                        cast(
                            ReportContentReviewItemRecord.review_reasons_json,
                            String,
                        ).like(f'%"{normalized_category}"%'),
                    )
                )
            total = query.count()
            rows = (
                query.order_by(
                    ReportContentReviewItemRecord.category,
                    ReportContentReviewItemRecord.id,
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
                .all()
            )
            return {
                "review": self._summary(review),
                "total": total,
                "page": page,
                "page_size": page_size,
                "items": [item.to_dict() for item in rows],
            }

    def decide_items(
        self,
        report_manifest_id: str,
        *,
        review_item_ids: Iterable[str],
        decision: str,
        reviewed_by: str,
        review_comment: str,
    ) -> dict[str, Any]:
        ids = sorted({str(item).strip() for item in review_item_ids if str(item).strip()})
        actor = str(reviewed_by or "").strip()
        comment = str(review_comment or "").strip()
        if not ids or len(ids) > 200:
            raise ValueError("content review decision requires 1 to 200 items")
        if decision not in {"accepted", "rejected"}:
            raise ValueError("content review decision is invalid")
        if not actor:
            raise ValueError("content review decision requires an actor")
        if len(comment) < 3:
            raise ValueError("content review decision requires a comment")
        with self._session_scope() as db:
            review = (
                db.query(ReportContentReviewRecord)
                .filter(ReportContentReviewRecord.report_manifest_id == report_manifest_id)
                .with_for_update()
                .first()
            )
            if review is None:
                raise ValueError("report content review not found")
            manifest = db.get(ReportManifestRecord, report_manifest_id)
            if manifest is None:
                raise ValueError("report manifest not found")
            if actor_matches_any(actor, [manifest.generated_by]):
                raise ValueError(
                    "content review requires an actor different from the manifest generator"
                )
            rows = (
                db.query(ReportContentReviewItemRecord)
                .filter(
                    ReportContentReviewItemRecord.review_id == review.id,
                    ReportContentReviewItemRecord.review_item_id.in_(ids),
                )
                .with_for_update()
                .all()
            )
            if len(rows) != len(ids):
                raise ValueError("one or more content review items were not found")
            non_pending = [item for item in rows if str(item.review_status) != "pending"]
            if non_pending:
                if len(non_pending) == len(rows) and all(
                    str(item.review_status) == decision
                    and actor_matches_any(actor, [item.reviewed_by])
                    and str(item.review_comment or "") == comment
                    for item in rows
                ):
                    self._refresh_counts(db, review)
                    return self._summary(review)
                raise ValueError("content review decisions are immutable")
            if str(review.status) != "in_review":
                raise ValueError("report content review is not open for decisions")
            now = datetime.now(timezone.utc)
            for item in rows:
                item.review_status = decision
                item.reviewed_by = actor
                item.reviewed_at = now
                item.review_comment = comment
            db.flush()
            self._refresh_counts(db, review)
            db.flush()
            return self._summary(review)

    def accept(
        self,
        report_manifest_id: str,
        *,
        accepted_by: str,
        acceptance_comment: str,
        confirmations: Iterable[str],
        verified_artifact_sha256: Mapping[str, str],
        accepted_at: datetime | None = None,
    ) -> dict[str, Any]:
        actor = str(accepted_by or "").strip()
        comment = str(acceptance_comment or "").strip()
        provided_confirmations = sorted(
            {str(item).strip() for item in confirmations if str(item).strip()}
        )
        verified = {
            str(name): str(value).strip().lower()
            for name, value in verified_artifact_sha256.items()
        }
        if not actor or len(comment) < 3:
            raise ValueError("content acceptance requires an actor and comment")
        if set(provided_confirmations) != REQUIRED_CONTENT_CONFIRMATIONS:
            raise ValueError("content acceptance confirmations are incomplete")
        with self._session_scope() as db:
            review = (
                db.query(ReportContentReviewRecord)
                .filter(ReportContentReviewRecord.report_manifest_id == report_manifest_id)
                .with_for_update()
                .first()
            )
            if review is None:
                raise ValueError("report content review not found")
            manifest = db.get(ReportManifestRecord, report_manifest_id)
            if manifest is None:
                raise ValueError("report manifest not found")
            machine = _machine_validation(
                db,
                manifest,
                object_storage=self._storage(),
            )
            if not machine["passed"]:
                raise ValueError("report content machine validation no longer passes")
            if str(review.status) == "accepted":
                if (
                    not actor_matches_any(actor, [review.accepted_by])
                    or str(review.acceptance_comment or "") != comment
                    or sorted(review.accepted_confirmations_json or []) != provided_confirmations
                    or dict(review.artifact_sha256_json or {}) != verified
                ):
                    raise ValueError(
                        "content acceptance is immutable and the retry payload differs"
                    )
                return self._summary(review)
            if str(review.status) != "in_review":
                raise ValueError("report content review requires a new manifest revision")
            if actor_matches_any(actor, [manifest.generated_by]):
                raise ValueError(
                    "content acceptance requires an actor different from the manifest generator"
                )
            plan = _build_review_plan(manifest)
            if (
                str(review.source_facts_digest) != str(manifest.source_digest)
                or str(review.claim_ledger_digest) != str(manifest.claim_ledger_digest or "")
                or dict(review.artifact_sha256_json or {}) != verified
                or dict(manifest.artifact_sha256_json or {}) != verified
                or str(review.review_plan_digest) != str(plan["review_plan_digest"])
                or str(review.machine_validation_digest) != str(machine["digest"])
            ):
                raise ValueError("content review evidence no longer matches the manifest")
            self._refresh_counts(db, review)
            pending = (
                int(review.required_item_count or 0)
                - int(review.accepted_item_count or 0)
                - int(review.rejected_item_count or 0)
            )
            if pending or int(review.rejected_item_count or 0):
                raise ValueError("content review has pending or rejected items")
            occurred_at = accepted_at or datetime.now(timezone.utc)
            if occurred_at.tzinfo is None:
                occurred_at = occurred_at.replace(tzinfo=timezone.utc)
            acceptance_payload = {
                "report_manifest_id": str(manifest.id),
                "review_id": str(review.id),
                "source_facts_digest": str(review.source_facts_digest),
                "claim_ledger_digest": str(review.claim_ledger_digest),
                "artifact_sha256": verified,
                "review_plan_digest": str(review.review_plan_digest),
                "machine_validation_digest": str(review.machine_validation_digest),
                "accepted_item_count": int(review.accepted_item_count or 0),
                "confirmations": provided_confirmations,
                "accepted_by": actor,
                "accepted_at": occurred_at.isoformat(),
                "acceptance_comment": comment,
            }
            review.status = "accepted"
            review.accepted_confirmations_json = provided_confirmations
            review.accepted_by = actor
            review.accepted_at = occurred_at
            review.acceptance_comment = comment
            review.acceptance_digest = _digest(acceptance_payload)
            review.updated_at = datetime.now(timezone.utc)
            db.flush()
            return self._summary(review)

    def validate_for_release_in_session(
        self,
        db: Any,
        report_manifest_id: str,
    ) -> dict[str, Any]:
        manifest = db.get(ReportManifestRecord, report_manifest_id)
        if manifest is None:
            raise ValueError("report manifest not found")
        review = (
            db.query(ReportContentReviewRecord)
            .filter(ReportContentReviewRecord.report_manifest_id == report_manifest_id)
            .first()
        )
        if review is None or str(review.status) != "accepted":
            raise ValueError("report content has not been accepted")
        machine = _machine_validation(
            db,
            manifest,
            object_storage=self._storage(),
        )
        plan = _build_review_plan(manifest)
        if not machine["passed"]:
            raise ValueError("report content machine validation no longer passes")
        if (
            str(review.source_facts_digest) != str(manifest.source_digest)
            or str(review.claim_ledger_digest) != str(manifest.claim_ledger_digest or "")
            or dict(review.artifact_sha256_json or {}) != dict(manifest.artifact_sha256_json or {})
            or str(review.review_plan_digest) != str(plan["review_plan_digest"])
            or str(review.machine_validation_digest) != str(machine["digest"])
            or not _valid_sha256(review.acceptance_digest)
        ):
            raise ValueError("report content acceptance is stale or invalid")
        return self._summary(review)

    def validate_for_release(self, report_manifest_id: str) -> dict[str, Any]:
        with self._session_scope() as db:
            return self.validate_for_release_in_session(
                db,
                report_manifest_id,
            )


__all__ = [
    "CONTENT_REVIEW_SCHEMA_VERSION",
    "REQUIRED_CONTENT_CONFIRMATIONS",
    "REQUIRED_REPORT_ARTIFACTS",
    "REVIEW_CATEGORIES",
    "ReportContentReviewService",
]
