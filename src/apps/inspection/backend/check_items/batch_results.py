"""Server-side check-item result queries for patrol batch operations."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
from collections import Counter
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from sqlalchemy import and_, func, or_

from src.common.config import settings
from src.core.check_items.assessment_policy import (
    validate_assessment_basis,
    validate_assessment_policy_snapshot,
)
from src.core.check_items.presentation import check_item_display_name
from src.core.check_items.reporting import build_check_item_report_view
from src.infrastructure.db.models import (
    CheckAssessmentRecord,
    CheckExecutionRecord,
    CheckItemRecord,
    CheckItemRuntimeManifestRecord,
    CheckItemVersionRecord,
    PatrolBatchDeviceRecord,
    PatrolBatchRecord,
    ReportManifestRecord,
)
from src.infrastructure.db.postgres import get_db_session

_TERMINAL_EXECUTION_STATUSES = {
    "completed",
    "not_applicable",
    "unsupported",
    "blocked_asset_missing",
    "collection_failed",
    "parse_failed",
    "normalization_failed",
    "comparison_failed",
    "assessment_failed",
    "cancelled",
}
_CURSOR_VERSION = 3


def _decode_base64url_segment(segment: str) -> bytes:
    if not segment or "=" in segment:
        raise ValueError("invalid base64url segment")
    try:
        decoded = base64.urlsafe_b64decode(segment + ("=" * (-len(segment) % 4)))
    except (ValueError, binascii.Error) as exc:
        raise ValueError("invalid base64url segment") from exc
    canonical = base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=")
    if not hmac.compare_digest(canonical, segment):
        raise ValueError("non-canonical base64url segment")
    return decoded


def _cursor_scope(
    *,
    search: str | None,
    assessment_status: str | None,
    completeness_status: str | None,
    source_identity_status: str | None,
    output_quality_status: str | None,
    freshness_status: str | None,
    coverage_status: str | None,
) -> str:
    payload = {
        "search": str(search or "").strip(),
        "assessment_status": assessment_status,
        "completeness_status": completeness_status,
        "source_identity_status": source_identity_status,
        "output_quality_status": output_quality_status,
        "freshness_status": freshness_status,
        "coverage_status": coverage_status,
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _encode_cursor(
    *,
    patrol_batch_id: str,
    scope_digest: str,
    values: tuple[str, ...],
    total: int,
    secret: str | bytes | None = None,
    now: int | None = None,
    ttl_seconds: int | None = None,
) -> str:
    issued_at = int(time.time()) if now is None else int(now)
    ttl = (
        int(settings.CHECK_ITEM_RESULT_CURSOR_TTL_SECONDS)
        if ttl_seconds is None
        else int(ttl_seconds)
    )
    if ttl <= 0:
        raise ValueError("check-result cursor TTL must be positive")
    payload = {
        "v": _CURSOR_VERSION,
        "batch": patrol_batch_id,
        "scope": scope_digest,
        "values": list(values),
        "total": int(total),
        "iat": issued_at,
        "exp": issued_at + ttl,
    }
    payload_token = (
        base64.urlsafe_b64encode(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        .decode("ascii")
        .rstrip("=")
    )
    signature = hmac.new(
        _cursor_secret(secret),
        payload_token.encode("ascii"),
        hashlib.sha256,
    ).digest()
    signature_token = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
    return f"{payload_token}.{signature_token}"


def _decode_cursor(
    cursor: str,
    *,
    patrol_batch_id: str,
    scope_digest: str,
    secret: str | bytes | None = None,
    now: int | None = None,
) -> tuple[tuple[str, ...], int]:
    try:
        token = str(cursor).strip()
        payload_token, signature_token = token.split(".", 1)
        signature = _decode_base64url_segment(signature_token)
        expected_signature = hmac.new(
            _cursor_secret(secret),
            payload_token.encode("ascii"),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(signature, expected_signature):
            raise ValueError("signature mismatch")
        payload = json.loads(_decode_base64url_segment(payload_token).decode("utf-8"))
    except (
        ValueError,
        TypeError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise ValueError("invalid check-result cursor") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("v") != _CURSOR_VERSION
        or payload.get("batch") != patrol_batch_id
        or payload.get("scope") != scope_digest
    ):
        raise ValueError("check-result cursor does not match this query")
    values = payload.get("values")
    if (
        not isinstance(values, list)
        or len(values) != 3
        or not all(isinstance(item, str) for item in values)
    ):
        raise ValueError("invalid check-result cursor values")
    total = payload.get("total")
    if not isinstance(total, int) or total < 0:
        raise ValueError("invalid check-result cursor total")
    issued_at = payload.get("iat")
    expires_at = payload.get("exp")
    current_time = int(time.time()) if now is None else int(now)
    if (
        not isinstance(issued_at, int)
        or not isinstance(expires_at, int)
        or expires_at <= issued_at
        or issued_at > current_time + 60
    ):
        raise ValueError("invalid check-result cursor lifetime")
    if current_time >= expires_at:
        raise ValueError("check-result cursor has expired")
    return tuple(values), total


def _cursor_secret(secret: str | bytes | None = None) -> bytes:
    resolved = (
        secret
        if secret not in (None, "", b"")
        else settings.CHECK_ITEM_RESULT_CURSOR_SECRET or settings.JWT_SECRET_KEY
    )
    if isinstance(resolved, bytes):
        return resolved
    return str(resolved).encode("utf-8")


def _keyset_after(
    expressions: tuple[Any, ...],
    values: tuple[str, ...],
) -> Any:
    return or_(
        *[
            and_(
                *[expressions[prior] == values[prior] for prior in range(index)],
                expression > values[index],
            )
            for index, expression in enumerate(expressions)
        ]
    )


class CheckItemBatchResultService:
    """Expose scalable live and terminal views of the target-check matrix."""

    def __init__(
        self,
        session_scope: Callable[[], AbstractContextManager[Any]] = (get_db_session),
    ) -> None:
        self._session_scope = session_scope

    def summary(self, patrol_batch_id: str) -> dict[str, Any] | None:
        report_manifest: dict[str, Any] | None = None
        with self._session_scope() as db:
            batch = db.get(PatrolBatchRecord, patrol_batch_id)
            if batch is None or not batch.runtime_manifest_id:
                return None
            runtime_manifest = db.get(
                CheckItemRuntimeManifestRecord,
                batch.runtime_manifest_id,
            )
            execution_count_rows = (
                db.query(
                    CheckExecutionRecord.execution_status,
                    func.count(CheckExecutionRecord.id),
                )
                .filter(CheckExecutionRecord.patrol_batch_id == patrol_batch_id)
                .group_by(CheckExecutionRecord.execution_status)
                .all()
            )
            assessment_count_rows = (
                db.query(
                    CheckAssessmentRecord.assessment_status,
                    func.count(CheckAssessmentRecord.id),
                )
                .filter(CheckAssessmentRecord.patrol_batch_id == patrol_batch_id)
                .group_by(CheckAssessmentRecord.assessment_status)
                .all()
            )
            completeness_count_rows = (
                db.query(
                    CheckAssessmentRecord.completeness_status,
                    func.count(CheckAssessmentRecord.id),
                )
                .filter(CheckAssessmentRecord.patrol_batch_id == patrol_batch_id)
                .group_by(CheckAssessmentRecord.completeness_status)
                .all()
            )
            comparison_count_rows = (
                db.query(
                    CheckAssessmentRecord.comparison_status,
                    func.count(CheckAssessmentRecord.id),
                )
                .filter(CheckAssessmentRecord.patrol_batch_id == patrol_batch_id)
                .group_by(CheckAssessmentRecord.comparison_status)
                .all()
            )
            blocker_rows = (
                db.query(CheckAssessmentRecord.blocking_reason_codes_json)
                .filter(CheckAssessmentRecord.patrol_batch_id == patrol_batch_id)
                .all()
            )
            if batch.report_manifest_id:
                row = db.get(
                    ReportManifestRecord,
                    batch.report_manifest_id,
                )
                if row is not None:
                    report_manifest = row.to_dict()
            runtime_payload = (
                dict(runtime_manifest.manifest_json or {}) if runtime_manifest is not None else {}
            )
            per_device = dict(runtime_payload.get("device_check_item_versions") or {})
            expected_count = (
                sum(len(value or []) for value in per_device.values())
                if per_device
                else sum(int(count) for _status, count in execution_count_rows)
            )
            target_count = int(batch.target_count or 0)
            report_status = str(batch.report_status or "not_requested")
            report_manifest_id = batch.report_manifest_id
            report_manifest_digest = batch.report_manifest_digest
            runtime_manifest_id = batch.runtime_manifest_id
            runtime_manifest_digest = batch.runtime_manifest_digest
            target_manifest_digest = batch.target_manifest_digest
        execution_counts = Counter(
            {str(status): int(count) for status, count in execution_count_rows}
        )
        assessment_counts = Counter(
            {str(status): int(count) for status, count in assessment_count_rows}
        )
        completeness_counts = Counter(
            {str(status): int(count) for status, count in completeness_count_rows}
        )
        comparison_counts = Counter(
            {str(status): int(count) for status, count in comparison_count_rows}
        )
        blocker_counts = Counter(str(code) for (codes,) in blocker_rows for code in codes or [])
        execution_count = sum(execution_counts.values())
        assessment_count = sum(assessment_counts.values())
        terminal_count = sum(
            count
            for status, count in execution_counts.items()
            if status in _TERMINAL_EXECUTION_STATUSES
        )
        if report_manifest is not None and str(report_manifest.get("status") or "") == "final":
            report = build_check_item_report_view(report_manifest)
            conclusion = dict(report["conclusion"])
            conclusion_dimensions = dict(report["conclusion_dimensions"])
            coverage = dict(report["coverage"])
            source_digest = str(report["source_digest"])
        else:
            all_terminal = (
                expected_count > 0
                and terminal_count == expected_count
                and assessment_count == expected_count
            )
            conclusion = {
                "code": ("pending_report" if all_terminal else "in_progress"),
                "text": (
                    "检查已终态，等待冻结正式报告"
                    if all_terminal
                    else "巡检进行中，正式结论尚未形成"
                ),
                "basis": (
                    f"计划 {expected_count} 项，已终态 {terminal_count} 项，"
                    f"已评估 {assessment_count} 项。"
                ),
                "trust": "过程数据",
                "formal": False,
            }
            coverage = {
                "target_device_count": target_count,
                "expected_check_count": expected_count,
                "executed_check_count": execution_count,
                "assessed_check_count": assessment_count,
                "complete_check_count": completeness_counts["complete"],
                "partial_check_count": completeness_counts["partial"],
                "insufficient_check_count": completeness_counts["insufficient"],
            }
            conclusion_dimensions = None
            source_digest = ""
        return {
            "schema_version": "check-item-batch-summary/v1",
            "patrol_batch_id": patrol_batch_id,
            "report_status": report_status,
            "report_manifest_id": report_manifest_id,
            "report_manifest_digest": report_manifest_digest,
            "source_digest": source_digest,
            "runtime_manifest_id": runtime_manifest_id,
            "runtime_manifest_digest": runtime_manifest_digest,
            "target_manifest_digest": target_manifest_digest,
            "conclusion": conclusion,
            "conclusion_dimensions": conclusion_dimensions,
            "coverage": coverage,
            "execution_counts": dict(sorted(execution_counts.items())),
            "assessment_counts": dict(sorted(assessment_counts.items())),
            "completeness_counts": dict(sorted(completeness_counts.items())),
            "comparison_counts": dict(sorted(comparison_counts.items())),
            "blocking_reason_counts": dict(sorted(blocker_counts.items())),
        }

    def list_results(
        self,
        patrol_batch_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        cursor: str | None = None,
        search: str | None = None,
        assessment_status: str | None = None,
        completeness_status: str | None = None,
        source_identity_status: str | None = None,
        output_quality_status: str | None = None,
        freshness_status: str | None = None,
        coverage_status: str | None = None,
    ) -> dict[str, Any]:
        safe_limit = min(max(int(limit), 1), 200)
        safe_offset = max(int(offset), 0)
        if cursor and safe_offset:
            raise ValueError("check-result cursor cannot be combined with a non-zero offset")
        if not cursor and safe_offset > int(settings.CHECK_ITEM_RESULT_MAX_OFFSET):
            raise ValueError(
                "check-result offset exceeds the shallow-page limit; continue with next_cursor"
            )
        scope_digest = _cursor_scope(
            search=search,
            assessment_status=assessment_status,
            completeness_status=completeness_status,
            source_identity_status=source_identity_status,
            output_quality_status=output_quality_status,
            freshness_status=freshness_status,
            coverage_status=coverage_status,
        )
        decoded_cursor = (
            _decode_cursor(
                cursor,
                patrol_batch_id=patrol_batch_id,
                scope_digest=scope_digest,
            )
            if cursor
            else None
        )
        cursor_values = decoded_cursor[0] if decoded_cursor is not None else None
        cursor_total = decoded_cursor[1] if decoded_cursor is not None else None
        with self._session_scope() as db:
            batch = db.get(PatrolBatchRecord, patrol_batch_id)
            if batch is None:
                raise ValueError("patrol batch not found")
            query = (
                db.query(
                    CheckExecutionRecord,
                    CheckAssessmentRecord,
                    CheckItemVersionRecord,
                    CheckItemRecord,
                    PatrolBatchDeviceRecord,
                )
                .join(
                    CheckItemVersionRecord,
                    CheckItemVersionRecord.id == CheckExecutionRecord.check_item_version_id,
                )
                .join(
                    CheckItemRecord,
                    CheckItemRecord.id == CheckItemVersionRecord.check_item_id,
                )
                .outerjoin(
                    CheckAssessmentRecord,
                    CheckAssessmentRecord.execution_id == CheckExecutionRecord.id,
                )
                .outerjoin(
                    PatrolBatchDeviceRecord,
                    and_(
                        PatrolBatchDeviceRecord.patrol_batch_id
                        == CheckExecutionRecord.patrol_batch_id,
                        PatrolBatchDeviceRecord.device_id == CheckExecutionRecord.device_id,
                    ),
                )
                .filter(CheckExecutionRecord.patrol_batch_id == patrol_batch_id)
            )
            if assessment_status:
                if assessment_status == "pending":
                    query = query.filter(CheckAssessmentRecord.id.is_(None))
                else:
                    query = query.filter(
                        CheckAssessmentRecord.assessment_status == assessment_status
                    )
            if completeness_status:
                query = query.filter(
                    CheckAssessmentRecord.completeness_status == completeness_status
                )
            if source_identity_status:
                query = query.filter(
                    CheckExecutionRecord.source_identity_status == source_identity_status
                )
            if output_quality_status:
                query = query.filter(
                    CheckExecutionRecord.output_quality_status == output_quality_status
                )
            if freshness_status:
                query = query.filter(CheckExecutionRecord.freshness_status == freshness_status)
            if coverage_status:
                query = query.filter(CheckExecutionRecord.coverage_status == coverage_status)
            pattern = f"%{str(search).strip()}%" if search else ""
            if pattern:
                query = query.filter(
                    or_(
                        CheckExecutionRecord.device_id.ilike(pattern),
                        PatrolBatchDeviceRecord.device_name.ilike(pattern),
                        PatrolBatchDeviceRecord.management_ip.ilike(pattern),
                        CheckItemRecord.id.ilike(pattern),
                        CheckItemRecord.name.ilike(pattern),
                    )
                )
            has_filters = any(
                (
                    pattern,
                    assessment_status,
                    completeness_status,
                    source_identity_status,
                    output_quality_status,
                    freshness_status,
                    coverage_status,
                )
            )
            if cursor_total is not None:
                total = cursor_total
            elif not has_filters:
                total = int(
                    db.query(func.count(CheckExecutionRecord.id))
                    .filter(CheckExecutionRecord.patrol_batch_id == patrol_batch_id)
                    .scalar()
                    or 0
                )
            else:
                total = int(
                    query.with_entities(func.count(CheckExecutionRecord.id)).order_by(None).scalar()
                    or 0
                )
            sort_expressions = (
                CheckExecutionRecord.device_id,
                CheckExecutionRecord.check_item_version_id,
                CheckExecutionRecord.id,
            )
            if has_filters:
                page_query = query.with_entities(
                    CheckExecutionRecord.id.label("execution_id"),
                    CheckExecutionRecord.device_id.label("device_id"),
                    CheckExecutionRecord.check_item_version_id.label("check_item_version_id"),
                )
            else:
                page_query = db.query(
                    CheckExecutionRecord.id.label("execution_id"),
                    CheckExecutionRecord.device_id.label("device_id"),
                    CheckExecutionRecord.check_item_version_id.label("check_item_version_id"),
                ).filter(CheckExecutionRecord.patrol_batch_id == patrol_batch_id)
            if cursor_values is not None:
                page_query = page_query.filter(_keyset_after(sort_expressions, cursor_values))
            page_query = page_query.order_by(*sort_expressions)
            if cursor_values is None:
                page_query = page_query.offset(safe_offset)
            page_rows = page_query.limit(safe_limit + 1).all()
            has_more = len(page_rows) > safe_limit
            page_rows = page_rows[:safe_limit]
            execution_ids = [str(item.execution_id) for item in page_rows]
            detail_rows = (
                query.filter(CheckExecutionRecord.id.in_(execution_ids)).all()
                if execution_ids
                else []
            )
            detail_by_id = {
                str(execution.id): (
                    execution,
                    assessment,
                    check_version,
                    check_item,
                    target,
                )
                for (
                    execution,
                    assessment,
                    check_version,
                    check_item,
                    target,
                ) in detail_rows
            }
            rows = [
                detail_by_id[execution_id]
                for execution_id in execution_ids
                if execution_id in detail_by_id
            ]
            items = [
                self._result_item(
                    execution,
                    assessment,
                    check_version,
                    check_item,
                    target,
                )
                for (
                    execution,
                    assessment,
                    check_version,
                    check_item,
                    target,
                ) in rows
            ]
            next_cursor = None
            if has_more and page_rows:
                last = page_rows[-1]
                next_cursor = _encode_cursor(
                    patrol_batch_id=patrol_batch_id,
                    scope_digest=scope_digest,
                    values=(
                        str(last.device_id),
                        str(last.check_item_version_id),
                        str(last.execution_id),
                    ),
                    total=total,
                )
            return {
                "patrol_batch_id": patrol_batch_id,
                "total": total,
                "limit": safe_limit,
                "offset": 0 if cursor else safe_offset,
                "pagination_mode": "cursor" if cursor else "offset",
                "next_cursor": next_cursor,
                "has_more": has_more,
                "items": items,
            }

    def get_result_detail(
        self,
        patrol_batch_id: str,
        execution_id: str,
        *,
        entry_limit: int = 50,
        entry_offset: int = 0,
    ) -> dict[str, Any]:
        safe_entry_limit = min(max(int(entry_limit), 1), 200)
        safe_entry_offset = max(int(entry_offset), 0)
        with self._session_scope() as db:
            batch = db.get(PatrolBatchRecord, str(patrol_batch_id))
            if batch is None:
                raise ValueError("patrol batch not found")
            row = (
                db.query(
                    CheckExecutionRecord,
                    CheckAssessmentRecord,
                    CheckItemVersionRecord,
                    CheckItemRecord,
                    PatrolBatchDeviceRecord,
                )
                .join(
                    CheckItemVersionRecord,
                    CheckItemVersionRecord.id == CheckExecutionRecord.check_item_version_id,
                )
                .join(
                    CheckItemRecord,
                    CheckItemRecord.id == CheckItemVersionRecord.check_item_id,
                )
                .outerjoin(
                    CheckAssessmentRecord,
                    CheckAssessmentRecord.execution_id == CheckExecutionRecord.id,
                )
                .outerjoin(
                    PatrolBatchDeviceRecord,
                    and_(
                        PatrolBatchDeviceRecord.patrol_batch_id
                        == CheckExecutionRecord.patrol_batch_id,
                        PatrolBatchDeviceRecord.device_id == CheckExecutionRecord.device_id,
                    ),
                )
                .filter(
                    CheckExecutionRecord.id == str(execution_id),
                    CheckExecutionRecord.patrol_batch_id == str(patrol_batch_id),
                )
                .one_or_none()
            )
            if row is None:
                raise ValueError("check result not found")
            execution, assessment, check_version, check_item, target = row
            result = self._result_item(
                execution,
                assessment,
                check_version,
                check_item,
                target,
            )
            policy_snapshot = dict(execution.assessment_policy_snapshot_json or {})
            assessment_basis = (
                dict(assessment.assessment_basis_json or {}) if assessment is not None else {}
            )
            basis_entries = [
                dict(entry)
                for entry in assessment_basis.get("entries") or []
                if isinstance(entry, dict)
            ]
            public_basis = {
                **assessment_basis,
                "entries": basis_entries[safe_entry_offset : safe_entry_offset + safe_entry_limit],
            }
            snapshot_present = bool(policy_snapshot)
            basis_present = bool(assessment_basis)
            evidence_refs = sorted(
                {
                    str(item)
                    for item in [
                        *(execution.evidence_refs_json or []),
                        *(assessment_basis.get("evidence_refs") or []),
                        *(
                            reference
                            for entry in assessment_basis.get("entries") or []
                            if isinstance(entry, dict)
                            for reference in entry.get("evidence_refs") or []
                        ),
                    ]
                    if item is not None and str(item).strip()
                }
            )
            return {
                **result,
                "schema_version": "patrol-check-result-detail/v1",
                "assessment_policy_snapshot": policy_snapshot,
                "assessment_basis": public_basis,
                "assessment_basis_entries": {
                    "total": len(basis_entries),
                    "limit": safe_entry_limit,
                    "offset": safe_entry_offset,
                    "has_more": (safe_entry_offset + safe_entry_limit < len(basis_entries)),
                },
                "evidence_refs": evidence_refs,
                "integrity": {
                    "policy_snapshot_present": snapshot_present,
                    "policy_snapshot_valid": (
                        validate_assessment_policy_snapshot(policy_snapshot)
                        if snapshot_present
                        else False
                    ),
                    "assessment_basis_present": basis_present,
                    "assessment_basis_valid": (
                        validate_assessment_basis(assessment_basis) if basis_present else False
                    ),
                    "policy_snapshot_digest_matches": bool(
                        snapshot_present
                        and str(policy_snapshot.get("digest") or "")
                        == str(execution.assessment_policy_snapshot_digest or "")
                    ),
                    "assessment_basis_digest_matches": bool(
                        basis_present
                        and assessment is not None
                        and str(assessment_basis.get("digest") or "")
                        == str(assessment.assessment_basis_digest or "")
                    ),
                },
            }

    @staticmethod
    def _result_item(
        execution: CheckExecutionRecord,
        assessment: CheckAssessmentRecord | None,
        check_version: CheckItemVersionRecord,
        check_item: CheckItemRecord,
        target: PatrolBatchDeviceRecord | None,
    ) -> dict[str, Any]:
        assessment_basis = (
            dict(assessment.assessment_basis_json or {}) if assessment is not None else {}
        )
        assessment_basis_summary = dict(assessment_basis.get("summary") or {})
        return {
            "execution_id": str(execution.id),
            "assessment_id": str(assessment.id) if assessment is not None else None,
            "device_id": str(execution.device_id),
            "device_name": (target.device_name if target is not None else None),
            "management_ip": (target.management_ip if target is not None else None),
            "check_item_id": str(check_item.id),
            "check_item_name": check_item_display_name(
                str(check_item.id),
                str(check_item.name),
            ),
            "check_item_category": str(check_item.category),
            "check_item_version_id": str(check_version.id),
            "result_mode": str(check_version.result_mode),
            "assessment_mode": str(check_version.assessment_mode),
            "report_projection": str(check_version.report_projection),
            "implementation_version_id": (execution.implementation_version_id),
            "execution_status": str(execution.execution_status),
            "assessment_status": (
                str(assessment.assessment_status) if assessment is not None else "pending"
            ),
            "completeness_status": (
                str(assessment.completeness_status) if assessment is not None else "pending"
            ),
            "comparison_status": (
                str(assessment.comparison_status) if assessment is not None else "pending"
            ),
            "evidence_qualification_id": (
                assessment.evidence_qualification_id
                if assessment is not None
                else execution.evidence_qualification_id
            ),
            "coverage_decision_id": (
                assessment.coverage_decision_id
                if assessment is not None
                else execution.coverage_decision_id
            ),
            "source_identity_status": (
                str(assessment.source_identity_status)
                if assessment is not None
                else str(execution.source_identity_status)
            ),
            "output_quality_status": (
                str(assessment.output_quality_status)
                if assessment is not None
                else str(execution.output_quality_status)
            ),
            "freshness_status": (
                str(assessment.freshness_status)
                if assessment is not None
                else str(execution.freshness_status)
            ),
            "empty_result_classification": (
                str(assessment.empty_result_classification)
                if assessment is not None
                else str(execution.empty_result_classification)
            ),
            "coverage_status": (
                str(assessment.coverage_status)
                if assessment is not None
                else str(execution.coverage_status)
            ),
            "coverage_basis": (
                str(assessment.coverage_basis)
                if assessment is not None
                else str(execution.coverage_basis)
            ),
            "risk_verification_status": (
                str(assessment.risk_verification_status)
                if assessment is not None
                else str(execution.risk_verification_status)
            ),
            "expected_entity_count": (
                assessment.expected_entity_count
                if (assessment is not None and assessment.expected_entity_count is not None)
                else execution.expected_entity_count
            ),
            "blocking_reason_codes": list(
                assessment.blocking_reason_codes_json
                if assessment is not None
                else execution.reason_codes_json or []
            ),
            "observation_count": int(execution.observation_count or 0),
            "observed_entity_count": int(execution.observed_entity_count or 0),
            "evaluated_entity_count": int(
                assessment.evaluated_entity_count if assessment is not None else 0
            ),
            "change_count": int(execution.change_count or 0),
            "event_count": int(execution.event_count or 0),
            "current_finding_count": int(execution.current_finding_count or 0),
            "runtime_bundle_version_id": (execution.runtime_bundle_version_id),
            "runtime_bundle_digest": execution.runtime_bundle_digest,
            "assessment_policy_snapshot_digest": (
                assessment.assessment_policy_snapshot_digest
                if assessment is not None
                else execution.assessment_policy_snapshot_digest
            ),
            "assessment_basis_digest": (
                assessment.assessment_basis_digest if assessment is not None else None
            ),
            "assessment_basis_summary": {
                "entry_count": int(assessment_basis_summary.get("entry_count") or 0),
                "source_counts": dict(assessment_basis_summary.get("source_counts") or {}),
                "status_counts": dict(assessment_basis_summary.get("status_counts") or {}),
            },
            "comparison_context_id": execution.comparison_context_id,
            "observation_min_at": execution.observation_min_at,
            "observation_max_at": execution.observation_max_at,
            "evidence_digest": (assessment.evidence_digest if assessment is not None else None),
            "started_at": execution.started_at,
            "completed_at": execution.completed_at,
        }


__all__ = ["CheckItemBatchResultService"]
