"""Server-side check-item result queries for patrol batch operations."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from sqlalchemy import and_, func, or_

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


class CheckItemBatchResultService:
    """Expose scalable live and terminal views of the target-check matrix."""

    def __init__(
        self,
        session_scope: Callable[[], AbstractContextManager[Any]] = (
            get_db_session
        ),
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
                .filter(
                    CheckExecutionRecord.patrol_batch_id
                    == patrol_batch_id
                )
                .group_by(CheckExecutionRecord.execution_status)
                .all()
            )
            assessment_count_rows = (
                db.query(
                    CheckAssessmentRecord.assessment_status,
                    func.count(CheckAssessmentRecord.id),
                )
                .filter(
                    CheckAssessmentRecord.patrol_batch_id
                    == patrol_batch_id
                )
                .group_by(CheckAssessmentRecord.assessment_status)
                .all()
            )
            completeness_count_rows = (
                db.query(
                    CheckAssessmentRecord.completeness_status,
                    func.count(CheckAssessmentRecord.id),
                )
                .filter(
                    CheckAssessmentRecord.patrol_batch_id
                    == patrol_batch_id
                )
                .group_by(CheckAssessmentRecord.completeness_status)
                .all()
            )
            comparison_count_rows = (
                db.query(
                    CheckAssessmentRecord.comparison_status,
                    func.count(CheckAssessmentRecord.id),
                )
                .filter(
                    CheckAssessmentRecord.patrol_batch_id
                    == patrol_batch_id
                )
                .group_by(CheckAssessmentRecord.comparison_status)
                .all()
            )
            blocker_rows = (
                db.query(
                    CheckAssessmentRecord.blocking_reason_codes_json
                )
                .filter(
                    CheckAssessmentRecord.patrol_batch_id
                    == patrol_batch_id
                )
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
                dict(runtime_manifest.manifest_json or {})
                if runtime_manifest is not None
                else {}
            )
            per_device = dict(
                runtime_payload.get("device_check_item_versions") or {}
            )
            expected_count = (
                sum(len(value or []) for value in per_device.values())
                if per_device
                else sum(
                    int(count)
                    for _status, count in execution_count_rows
                )
            )
            target_count = int(batch.target_count or 0)
            report_status = str(batch.report_status or "not_requested")
            report_manifest_id = batch.report_manifest_id
            report_manifest_digest = batch.report_manifest_digest
            runtime_manifest_id = batch.runtime_manifest_id
            runtime_manifest_digest = batch.runtime_manifest_digest
            target_manifest_digest = batch.target_manifest_digest
        execution_counts = Counter(
            {
                str(status): int(count)
                for status, count in execution_count_rows
            }
        )
        assessment_counts = Counter(
            {
                str(status): int(count)
                for status, count in assessment_count_rows
            }
        )
        completeness_counts = Counter(
            {
                str(status): int(count)
                for status, count in completeness_count_rows
            }
        )
        comparison_counts = Counter(
            {
                str(status): int(count)
                for status, count in comparison_count_rows
            }
        )
        blocker_counts = Counter(
            str(code)
            for (codes,) in blocker_rows
            for code in codes or []
        )
        execution_count = sum(execution_counts.values())
        assessment_count = sum(assessment_counts.values())
        terminal_count = sum(
            count
            for status, count in execution_counts.items()
            if status in _TERMINAL_EXECUTION_STATUSES
        )
        if (
            report_manifest is not None
            and str(report_manifest.get("status") or "") == "final"
        ):
            report = build_check_item_report_view(report_manifest)
            conclusion = dict(report["conclusion"])
            coverage = dict(report["coverage"])
            source_digest = str(report["source_digest"])
        else:
            all_terminal = (
                expected_count > 0
                and terminal_count == expected_count
                and assessment_count == expected_count
            )
            conclusion = {
                "code": (
                    "pending_report"
                    if all_terminal
                    else "in_progress"
                ),
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
                "insufficient_check_count": completeness_counts[
                    "insufficient"
                ],
            }
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
            "coverage": coverage,
            "execution_counts": dict(sorted(execution_counts.items())),
            "assessment_counts": dict(sorted(assessment_counts.items())),
            "completeness_counts": dict(
                sorted(completeness_counts.items())
            ),
            "comparison_counts": dict(sorted(comparison_counts.items())),
            "blocking_reason_counts": dict(
                sorted(blocker_counts.items())
            ),
        }

    def list_results(
        self,
        patrol_batch_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
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
                    CheckItemVersionRecord.id
                    == CheckExecutionRecord.check_item_version_id,
                )
                .join(
                    CheckItemRecord,
                    CheckItemRecord.id
                    == CheckItemVersionRecord.check_item_id,
                )
                .outerjoin(
                    CheckAssessmentRecord,
                    CheckAssessmentRecord.execution_id
                    == CheckExecutionRecord.id,
                )
                .outerjoin(
                    PatrolBatchDeviceRecord,
                    and_(
                        PatrolBatchDeviceRecord.patrol_batch_id
                        == CheckExecutionRecord.patrol_batch_id,
                        PatrolBatchDeviceRecord.device_id
                        == CheckExecutionRecord.device_id,
                    ),
                )
                .filter(
                    CheckExecutionRecord.patrol_batch_id
                    == patrol_batch_id
                )
            )
            if assessment_status:
                if assessment_status == "pending":
                    query = query.filter(
                        CheckAssessmentRecord.id.is_(None)
                    )
                else:
                    query = query.filter(
                        CheckAssessmentRecord.assessment_status
                        == assessment_status
                    )
            if completeness_status:
                query = query.filter(
                    CheckAssessmentRecord.completeness_status
                    == completeness_status
                )
            if source_identity_status:
                query = query.filter(
                    CheckExecutionRecord.source_identity_status
                    == source_identity_status
                )
            if output_quality_status:
                query = query.filter(
                    CheckExecutionRecord.output_quality_status
                    == output_quality_status
                )
            if freshness_status:
                query = query.filter(
                    CheckExecutionRecord.freshness_status
                    == freshness_status
                )
            if coverage_status:
                query = query.filter(
                    CheckExecutionRecord.coverage_status
                    == coverage_status
                )
            pattern = f"%{str(search).strip()}%" if search else ""
            if pattern:
                query = query.filter(
                    or_(
                        CheckExecutionRecord.device_id.ilike(pattern),
                        PatrolBatchDeviceRecord.device_name.ilike(pattern),
                        PatrolBatchDeviceRecord.management_ip.ilike(
                            pattern
                        ),
                        CheckItemRecord.id.ilike(pattern),
                        CheckItemRecord.name.ilike(pattern),
                    )
                )
            total = int(query.count())
            rows = (
                query.order_by(
                    PatrolBatchDeviceRecord.device_name,
                    CheckExecutionRecord.device_id,
                    CheckItemRecord.category,
                    CheckItemRecord.id,
                )
                .offset(safe_offset)
                .limit(safe_limit)
                .all()
            )
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
            return {
                "patrol_batch_id": patrol_batch_id,
                "total": total,
                "limit": safe_limit,
                "offset": safe_offset,
                "items": items,
            }

    @staticmethod
    def _result_item(
        execution: CheckExecutionRecord,
        assessment: CheckAssessmentRecord | None,
        check_version: CheckItemVersionRecord,
        check_item: CheckItemRecord,
        target: PatrolBatchDeviceRecord | None,
    ) -> dict[str, Any]:
        return {
            "execution_id": str(execution.id),
            "device_id": str(execution.device_id),
            "device_name": (
                target.device_name if target is not None else None
            ),
            "management_ip": (
                target.management_ip if target is not None else None
            ),
            "check_item_id": str(check_item.id),
            "check_item_name": str(check_item.name),
            "check_item_category": str(check_item.category),
            "check_item_version_id": str(check_version.id),
            "result_mode": str(check_version.result_mode),
            "assessment_mode": str(check_version.assessment_mode),
            "report_projection": str(check_version.report_projection),
            "implementation_version_id": (
                execution.implementation_version_id
            ),
            "execution_status": str(execution.execution_status),
            "assessment_status": (
                str(assessment.assessment_status)
                if assessment is not None
                else "pending"
            ),
            "completeness_status": (
                str(assessment.completeness_status)
                if assessment is not None
                else "pending"
            ),
            "comparison_status": (
                str(assessment.comparison_status)
                if assessment is not None
                else "pending"
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
                if (
                    assessment is not None
                    and assessment.expected_entity_count is not None
                )
                else execution.expected_entity_count
            ),
            "blocking_reason_codes": list(
                assessment.blocking_reason_codes_json
                if assessment is not None
                else execution.reason_codes_json or []
            ),
            "observation_count": int(execution.observation_count or 0),
            "observed_entity_count": int(
                execution.observed_entity_count or 0
            ),
            "evaluated_entity_count": int(
                assessment.evaluated_entity_count
                if assessment is not None
                else 0
            ),
            "change_count": int(execution.change_count or 0),
            "event_count": int(execution.event_count or 0),
            "current_finding_count": int(
                execution.current_finding_count or 0
            ),
            "runtime_bundle_version_id": (
                execution.runtime_bundle_version_id
            ),
            "runtime_bundle_digest": execution.runtime_bundle_digest,
            "comparison_context_id": execution.comparison_context_id,
            "observation_min_at": execution.observation_min_at,
            "observation_max_at": execution.observation_max_at,
            "evidence_digest": (
                assessment.evidence_digest
                if assessment is not None
                else None
            ),
            "started_at": execution.started_at,
            "completed_at": execution.completed_at,
        }


__all__ = ["CheckItemBatchResultService"]
