"""Immutable source-fact manifests for reproducible patrol deliverables."""

from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from src.infrastructure.db.models import (
    CheckAssessmentRecord,
    CheckExecutionRecord,
    CheckItemRecord,
    CheckItemRuntimeManifestRecord,
    CheckItemVersionRecord,
    CommandSnapshot,
    ComparisonContextRecord,
    CoverageDecisionRecord,
    CurrentStateFindingRecord,
    EvidenceQualificationRecord,
    NetworkChangeRecord,
    NetworkEventRecord,
    PatrolBatchDeviceRecord,
    PatrolBatchRecord,
    ReportManifestRecord,
    RiskCaseRecord,
    RiskOccurrenceRecord,
    RiskRemediationRecord,
    StateObservationRecord,
    WorkflowRun,
)
from src.infrastructure.db.postgres import get_db_session


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


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        normalized = (
            value.replace(tzinfo=timezone.utc)
            if value.tzinfo is None
            else value.astimezone(timezone.utc)
        )
        return normalized.isoformat()
    if isinstance(value, dict):
        return {
            str(key): _jsonable(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return value


def _fact(row: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    return {
        field: _jsonable(getattr(row, field))
        for field in fields
    }


class ReportManifestService:
    """Freeze the exact facts used by Markdown, DOCX, Excel and evidence exports."""

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

    def __init__(
        self,
        session_scope: Callable[[], AbstractContextManager[Any]] = get_db_session,
    ) -> None:
        self._session_scope = session_scope

    def build(
        self,
        patrol_batch_id: str,
        *,
        generated_by: str,
        generator_version: str = "check-item-report/v2",
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        with self._session_scope() as db:
            batch = (
                db.query(PatrolBatchRecord)
                .filter(PatrolBatchRecord.patrol_batch_id == patrol_batch_id)
                .with_for_update()
                .first()
            )
            if batch is None:
                raise ValueError("patrol batch not found")
            runtime_manifest = (
                db.query(CheckItemRuntimeManifestRecord)
                .filter(
                    CheckItemRuntimeManifestRecord.patrol_batch_id
                    == patrol_batch_id
                )
                .first()
            )
            runtime_manifest_issues = self._runtime_manifest_issues(
                batch=batch,
                runtime_manifest=runtime_manifest,
            )
            executions = (
                db.query(CheckExecutionRecord)
                .filter(
                    CheckExecutionRecord.patrol_batch_id == patrol_batch_id
                )
                .order_by(
                    CheckExecutionRecord.device_id,
                    CheckExecutionRecord.check_item_version_id,
                )
                .all()
            )
            assessments = (
                db.query(CheckAssessmentRecord)
                .filter(
                    CheckAssessmentRecord.patrol_batch_id == patrol_batch_id
                )
                .order_by(
                    CheckAssessmentRecord.device_id,
                    CheckAssessmentRecord.check_item_version_id,
                )
                .all()
            )
            execution_ids = [str(item.id) for item in executions]
            qualifications = (
                db.query(EvidenceQualificationRecord)
                .filter(
                    EvidenceQualificationRecord.check_execution_id.in_(
                        execution_ids
                    )
                )
                .order_by(EvidenceQualificationRecord.check_execution_id)
                .all()
                if execution_ids
                else []
            )
            coverage_decisions = (
                db.query(CoverageDecisionRecord)
                .filter(
                    CoverageDecisionRecord.check_execution_id.in_(
                        execution_ids
                    )
                )
                .order_by(CoverageDecisionRecord.check_execution_id)
                .all()
                if execution_ids
                else []
            )
            version_ids = sorted(
                {str(item.check_item_version_id) for item in executions}
            )
            version_rows = (
                db.query(CheckItemVersionRecord)
                .filter(CheckItemVersionRecord.id.in_(version_ids))
                .all()
                if version_ids
                else []
            )
            check_item_rows = (
                db.query(CheckItemRecord)
                .filter(
                    CheckItemRecord.id.in_(
                        sorted(
                            {
                                str(item.check_item_id)
                                for item in version_rows
                            }
                        )
                    )
                )
                .all()
                if version_rows
                else []
            )
            check_item_metadata = {
                str(item.id): item
                for item in check_item_rows
            }
            check_item_by_version = {
                str(item.id): str(item.check_item_id)
                for item in version_rows
            }
            check_item_facts = {
                str(item.id): {
                    "check_item_id": str(item.check_item_id),
                    "version": int(item.version),
                    "name": str(
                        getattr(
                            check_item_metadata.get(
                                str(item.check_item_id)
                            ),
                            "name",
                            item.check_item_id,
                        )
                    ),
                    "category": str(
                        getattr(
                            check_item_metadata.get(
                                str(item.check_item_id)
                            ),
                            "category",
                            "other",
                        )
                    ),
                    "description": getattr(
                        check_item_metadata.get(
                            str(item.check_item_id)
                        ),
                        "description",
                        None,
                    ),
                    "result_mode": str(item.result_mode),
                    "assessment_mode": str(item.assessment_mode),
                    "canonical_contract": dict(
                        item.canonical_contract_json or {}
                    ),
                    "report_projection": str(item.report_projection),
                    "digest": str(item.digest),
                }
                for item in version_rows
            }
            comparison_ids = sorted(
                {
                    str(item.comparison_context_id)
                    for item in executions
                    if item.comparison_context_id
                }
            )
            comparisons = (
                db.query(ComparisonContextRecord)
                .filter(ComparisonContextRecord.id.in_(comparison_ids))
                .order_by(ComparisonContextRecord.id)
                .all()
                if comparison_ids
                else []
            )
            findings = (
                db.query(CurrentStateFindingRecord)
                .filter(
                    CurrentStateFindingRecord.check_execution_id.in_(
                        execution_ids
                    )
                )
                .order_by(CurrentStateFindingRecord.id)
                .all()
                if execution_ids
                else []
            )
            changes = (
                db.query(NetworkChangeRecord)
                .filter(
                    NetworkChangeRecord.check_execution_id.in_(execution_ids)
                )
                .order_by(NetworkChangeRecord.id)
                .all()
                if execution_ids
                else []
            )
            events = (
                db.query(NetworkEventRecord)
                .filter(
                    NetworkEventRecord.check_execution_id.in_(execution_ids)
                )
                .order_by(NetworkEventRecord.id)
                .all()
                if execution_ids
                else []
            )
            assessment_risk_ids = {
                str(risk_id)
                for item in assessments
                for risk_id in item.risk_case_ids_json or []
                if risk_id
            }
            risk_occurrences = (
                db.query(RiskOccurrenceRecord)
                .filter(
                    RiskOccurrenceRecord.patrol_batch_id
                    == patrol_batch_id
                )
                .order_by(
                    RiskOccurrenceRecord.observed_at,
                    RiskOccurrenceRecord.id,
                )
                .all()
            )
            risk_ids = sorted(
                {
                    *assessment_risk_ids,
                    *(
                        str(item.risk_case_id)
                        for item in risk_occurrences
                        if item.risk_case_id
                    ),
                }
            )
            risks = (
                db.query(RiskCaseRecord)
                .filter(RiskCaseRecord.id.in_(risk_ids))
                .order_by(RiskCaseRecord.id)
                .all()
                if risk_ids
                else []
            )
            risk_remediations = (
                db.query(RiskRemediationRecord)
                .filter(
                    RiskRemediationRecord.risk_case_id.in_(risk_ids)
                )
                .order_by(
                    RiskRemediationRecord.created_at,
                    RiskRemediationRecord.id,
                )
                .all()
                if risk_ids
                else []
            )
            targets = (
                db.query(PatrolBatchDeviceRecord)
                .filter(
                    PatrolBatchDeviceRecord.patrol_batch_id
                    == patrol_batch_id
                )
                .order_by(PatrolBatchDeviceRecord.device_id)
                .all()
            )

            execution_facts = [
                _fact(
                    item,
                    (
                        "id",
                        "device_id",
                        "check_item_version_id",
                        "implementation_version_id",
                        "runtime_bundle_version_id",
                        "runtime_bundle_digest",
                        "comparison_context_id",
                        "execution_status",
                        "reason_codes_json",
                        "observation_count",
                        "observed_entity_count",
                        "change_count",
                        "event_count",
                        "current_finding_count",
                        "assessment_id",
                        "evidence_qualification_id",
                        "coverage_decision_id",
                        "source_identity_status",
                        "output_quality_status",
                        "freshness_status",
                        "empty_result_classification",
                        "coverage_status",
                        "coverage_basis",
                        "risk_verification_status",
                        "expected_entity_count",
                        "observation_min_at",
                        "observation_max_at",
                        "target_manifest_digest",
                        "asset_manifest_digest",
                        "evidence_refs_json",
                        "started_at",
                        "completed_at",
                    ),
                )
                for item in executions
            ]
            assessment_facts = [
                _fact(
                    item,
                    (
                        "id",
                        "execution_id",
                        "device_id",
                        "check_item_version_id",
                        "assessment_status",
                        "completeness_status",
                        "comparison_status",
                        "expected_entity_count",
                        "observed_entity_count",
                        "evaluated_entity_count",
                        "threshold_unknown_count",
                        "current_finding_ids_json",
                        "risk_case_ids_json",
                        "evidence_qualification_id",
                        "coverage_decision_id",
                        "source_identity_status",
                        "output_quality_status",
                        "freshness_status",
                        "empty_result_classification",
                        "coverage_status",
                        "coverage_basis",
                        "risk_verification_status",
                        "blocking_reason_codes_json",
                        "assessment_rule_bundle_digest",
                        "comparison_context_digest",
                        "evidence_digest",
                        "assessed_at",
                    ),
                )
                for item in assessments
            ]
            qualification_facts = [
                _fact(
                    item,
                    (
                        "id",
                        "digest",
                        "check_execution_id",
                        "device_id",
                        "check_item_version_id",
                        "implementation_version_id",
                        "runtime_bundle_version_id",
                        "runtime_bundle_digest",
                        "source_identity_status",
                        "output_quality_status",
                        "freshness_status",
                        "empty_result_classification",
                        "snapshot_ids_json",
                        "observation_min_at",
                        "observation_max_at",
                        "qualification_reason_codes_json",
                        "policy_digest",
                        "profile_version_id",
                        "profile_digest",
                        "evidence_refs_json",
                        "qualified_at",
                    ),
                )
                for item in qualifications
            ]
            coverage_facts = [
                _fact(
                    item,
                    (
                        "id",
                        "digest",
                        "check_execution_id",
                        "device_id",
                        "check_item_version_id",
                        "coverage_status",
                        "coverage_basis",
                        "expected_entity_count",
                        "observed_entity_count",
                        "evaluated_entity_count",
                        "expected_entity_set_digest",
                        "observed_entity_set_digest",
                        "denominator_ref_json",
                        "missing_entity_sample_json",
                        "unexpected_entity_sample_json",
                        "reason_codes_json",
                        "policy_digest",
                        "evidence_refs_json",
                        "decided_at",
                    ),
                )
                for item in coverage_decisions
            ]
            risk_facts = [
                _fact(
                    item,
                    (
                        "id",
                        "correlation_key",
                        "check_item_id",
                        "device_id",
                        "entity_type",
                        "entity_key",
                        "correlation_dimensions_json",
                        "risk_type",
                        "severity",
                        "status",
                        "first_seen_at",
                        "last_seen_at",
                        "last_successful_evaluation_at",
                        "verification_status",
                        "last_verified_at",
                        "last_verification_execution_id",
                        "resolved_at",
                        "remediation_status",
                        "owner",
                        "due_at",
                        "source_finding_ids_json",
                        "source_event_ids_json",
                        "resolution_evidence_refs_json",
                        "updated_at",
                    ),
                )
                for item in risks
            ]
            comparison_facts = [
                {
                    "id": str(item.id),
                    "digest": str(item.digest),
                    "comparison_status": (
                        (item.metadata_json or {}).get(
                            "comparison_status",
                            "baseline_missing",
                        )
                    ),
                    "baseline_run_id": item.baseline_run_id,
                    "baseline_snapshot_ids": list(
                        item.baseline_snapshot_ids_json or []
                    ),
                }
                for item in comparisons
            ]
            evidence_refs = sorted(
                {
                    *(str(ref) for item in executions for ref in item.evidence_refs_json or []),
                    *(str(ref) for item in findings for ref in item.evidence_refs_json or []),
                    *(str(item.previous_snapshot_id) for item in changes if item.previous_snapshot_id),
                    *(str(item.current_snapshot_id) for item in changes if item.current_snapshot_id),
                    *(str(item.source_change_id) for item in events if item.source_change_id),
                    *(str(item.id) for item in findings),
                    *(str(item.id) for item in changes),
                    *(str(item.id) for item in events),
                }
            )
            snapshots = (
                db.query(CommandSnapshot)
                .filter(CommandSnapshot.id.in_(evidence_refs))
                .order_by(
                    CommandSnapshot.device_id,
                    CommandSnapshot.observed_at,
                    CommandSnapshot.id,
                )
                .all()
                if evidence_refs
                else []
            )
            snapshot_ids = [str(item.id) for item in snapshots]
            snapshot_id_set = set(snapshot_ids)
            observations = (
                db.query(StateObservationRecord)
                .filter(
                    StateObservationRecord.snapshot_id.in_(snapshot_ids)
                )
                .order_by(
                    StateObservationRecord.device_id,
                    StateObservationRecord.entity_type,
                    StateObservationRecord.entity_key,
                    StateObservationRecord.observation_type,
                    StateObservationRecord.id,
                )
                .all()
                if snapshot_ids
                else []
            )
            execution_by_id = {
                str(item.id): item
                for item in executions
            }
            execution_snapshot_ids = {
                execution_id: {
                    str(reference)
                    for reference in row.evidence_refs_json or []
                    if str(reference) in snapshot_id_set
                }
                for execution_id, row in execution_by_id.items()
            }
            expected_types_by_execution = {
                execution_id: {
                    str(observation.get("observation_type"))
                    for observation in dict(
                        check_item_facts.get(
                            str(row.check_item_version_id),
                            {},
                        )
                    )
                    .get("canonical_contract", {})
                    .get("observations", [])
                    if isinstance(observation, dict)
                    and observation.get("observation_type")
                }
                for execution_id, row in execution_by_id.items()
            }
            observation_facts: list[dict[str, Any]] = []
            for item in observations:
                matching_execution_ids = sorted(
                    execution_id
                    for execution_id, linked_snapshot_ids in (
                        execution_snapshot_ids.items()
                    )
                    if str(item.snapshot_id) in linked_snapshot_ids
                    and (
                        not expected_types_by_execution.get(execution_id)
                        or str(item.observation_type or "")
                        in expected_types_by_execution[execution_id]
                    )
                )
                observation_facts.append(
                    {
                        "id": str(item.id),
                        "execution_ids": matching_execution_ids,
                        "snapshot_id": str(item.snapshot_id),
                        "device_id": str(item.device_id),
                        "entity_type": str(item.entity_type),
                        "entity_key": str(item.entity_key),
                        "field": str(item.field_name),
                        "observation_type": item.observation_type,
                        "semantic_type": item.semantic_type,
                        "unit": item.unit,
                        "dimensions": dict(item.dimensions_json or {}),
                        "value": item.value_json,
                        "value_text": item.value_text,
                        "value_number": item.value_number,
                        "value_type": str(item.value_type),
                        "observed_at": item.observed_at,
                        "source": str(item.source),
                        "source_type": str(item.source_type),
                        "parser_template_id": item.parser_template_id,
                        "parser_version": item.parser_version,
                        "contract_version": item.contract_version,
                        "confidence": float(item.confidence or 0),
                    }
                )
            evidence_index = [
                {
                    "snapshot_id": str(item.id),
                    "run_id": str(item.run_id),
                    "dataset_id": item.dataset_id,
                    "device_id": str(item.device_id),
                    "inventory_device_id": item.inventory_device_id,
                    "device_name": str(item.device_name),
                    "management_ip": item.management_ip,
                    "vendor": item.vendor,
                    "model": item.model,
                    "command": str(item.command),
                    "canonical_command": str(item.canonical_command),
                    "collection_action_id": item.collection_action_id,
                    "implementation_version_id": (
                        item.implementation_version_id
                    ),
                    "check_item_ids": list(
                        item.check_item_ids_json or []
                    ),
                    "observed_at": item.observed_at,
                    "raw_text_hash": str(item.raw_text_hash),
                    "parse_status": str(item.parse_status),
                    "output_status": str(item.output_status),
                    "parser_template_id": item.parser_template_id,
                    "parser_version": item.parser_version,
                    "contract_version": item.contract_version,
                    "source_file_hash": item.source_file_hash,
                    "source_lines": {
                        "start": item.start_line,
                        "end": item.end_line,
                    },
                }
                for item in snapshots
            ]
            target_facts = [
                _fact(
                    item,
                    (
                        "device_id",
                        "device_name",
                        "management_ip",
                        "selection_status",
                        "preflight_status",
                        "dispatch_status",
                        "failure_code",
                        "workflow_run_id",
                        "celery_task_id",
                    ),
                )
                for item in targets
            ]
            check_execution_fact_digest = _digest(execution_facts)
            check_assessment_fact_digest = _digest(assessment_facts)
            qualification_fact_digest = _digest(qualification_facts)
            coverage_fact_digest = _digest(coverage_facts)
            risk_case_revision = _digest(
                {
                    "risks": risk_facts,
                    "occurrences": [
                        item.to_dict() for item in risk_occurrences
                    ],
                    "remediations": [
                        item.to_dict() for item in risk_remediations
                    ],
                }
            )
            evidence_index_digest = _digest(evidence_index)
            target_manifest_digest = str(
                batch.target_manifest_digest or ""
            )
            runtime_manifest_digest = str(
                runtime_manifest.digest if runtime_manifest else ""
            )
            readiness = self._readiness(
                db,
                batch=batch,
                targets=targets,
                executions=executions,
                assessments=assessments,
                runtime_manifest=runtime_manifest,
                runtime_manifest_issues=runtime_manifest_issues,
            )
            terminal_fact_times = [
                *(
                    item.assessed_at
                    for item in assessments
                    if item.assessed_at is not None
                ),
                *(
                    item.completed_at
                    for item in executions
                    if item.completed_at is not None
                ),
            ]
            latest = (
                db.query(ReportManifestRecord)
                .filter(
                    ReportManifestRecord.patrol_batch_id
                    == patrol_batch_id
                )
                .order_by(ReportManifestRecord.version.desc())
                .first()
            )
            report_cutoff_at = (
                latest.report_cutoff_at
                if latest is not None
                else batch.report_requested_at
            ) or (
                max(terminal_fact_times) if terminal_fact_times else now
            )
            source_facts = _jsonable({
                "schema_version": "patrol-report-source/v2",
                "patrol_batch_id": patrol_batch_id,
                "readiness": readiness,
                "report_cutoff_at": report_cutoff_at,
                "target_manifest_digest": target_manifest_digest,
                "runtime_manifest_digest": runtime_manifest_digest,
                "target_facts": target_facts,
                "comparison_contexts": comparison_facts,
                "check_item_by_version": check_item_by_version,
                "check_items": check_item_facts,
                "executions": execution_facts,
                "assessments": assessment_facts,
                "evidence_qualifications": qualification_facts,
                "coverage_decisions": coverage_facts,
                "observations": observation_facts,
                "findings": [
                    {
                        "id": str(item.id),
                        "execution_id": str(item.check_execution_id),
                        "device_id": str(item.device_id),
                        "check_item_version_id": str(
                            item.check_item_version_id
                        ),
                        "implementation_version_id": (
                            item.implementation_version_id
                        ),
                        "event_type": str(item.event_type),
                        "severity": str(item.severity),
                        "entity_type": str(item.entity_type),
                        "entity_key": str(item.entity_key),
                        "dimensions": dict(item.dimensions_json or {}),
                        "observation_id": str(item.observation_id),
                        "observation_type": item.observation_type,
                        "actual_value": item.actual_value_json,
                        "actual_value_text": item.actual_value_text,
                        "actual_value_number": item.actual_value_number,
                        "unit": item.unit,
                        "reference_value": item.reference_value_json,
                        "reference_value_text": item.reference_value_text,
                        "reference_value_number": (
                            item.reference_value_number
                        ),
                        "reference_source": item.reference_source,
                        "event_rule_version_id": str(
                            item.event_rule_version_id
                        ),
                        "rule_digest": str(item.rule_digest),
                        "runtime_bundle_version_id": str(
                            item.runtime_bundle_version_id
                        ),
                        "runtime_bundle_digest": str(
                            item.runtime_bundle_digest
                        ),
                        "observed_at": item.observed_at,
                        "assessed_at": item.assessed_at,
                        "evidence_refs": list(
                            item.evidence_refs_json or []
                        ),
                        "evidence_digest": str(item.evidence_digest),
                    }
                    for item in findings
                ],
                "changes": [
                    {
                        "id": str(item.id),
                        "execution_id": str(item.check_execution_id),
                        "device_id": item.device_id,
                        "check_item_id": item.check_item_id,
                        "check_item_version_id": (
                            item.check_item_version_id
                        ),
                        "run_id": item.run_id,
                        "canonical_command": item.canonical_command,
                        "observation_type": item.observation_type,
                        "change_type": str(item.change_type),
                        "entity_type": str(item.entity_type),
                        "entity_key": str(item.entity_key),
                        "dimensions": dict(item.dimensions_json or {}),
                        "field": str(item.field),
                        "old": item.old_value_json,
                        "new": item.new_value_json,
                        "old_number": item.old_number,
                        "new_number": item.new_number,
                        "previous_snapshot_id": (
                            item.previous_snapshot_id
                        ),
                        "current_snapshot_id": item.current_snapshot_id,
                        "previous_observation_id": (
                            item.previous_observation_id
                        ),
                        "current_observation_id": (
                            item.current_observation_id
                        ),
                        "change_policy_id": item.change_policy_id,
                        "change_policy_version_id": (
                            item.change_policy_version_id
                        ),
                        "comparison_type": item.comparison_type,
                        "decision_reason": item.decision_reason,
                        "event_status": str(item.event_status),
                        "analysis_revision": str(item.analysis_revision),
                        "rule_bundle_digest": item.rule_bundle_digest,
                        "observed_at": item.observed_at,
                        "detected_at": item.detected_at,
                    }
                    for item in changes
                ],
                "events": [
                    {
                        "id": str(item.id),
                        "execution_id": str(item.check_execution_id),
                        "device_id": item.device_id,
                        "check_item_id": item.check_item_id,
                        "check_item_version_id": (
                            item.check_item_version_id
                        ),
                        "event_type": str(item.event_type),
                        "severity": str(item.severity),
                        "status": str(item.status),
                        "entity_type": str(item.entity_type),
                        "entity_key": str(item.entity_key),
                        "correlation_key": str(item.correlation_key),
                        "correlation_dimensions": dict(
                            item.correlation_dimensions_json or {}
                        ),
                        "source_change_id": item.source_change_id,
                        "run_id": item.run_id,
                        "latest_change_id": item.latest_change_id,
                        "occurred_at": item.occurred_at,
                        "first_seen_at": item.first_seen_at,
                        "last_seen_at": item.last_seen_at,
                        "occurrence_count": int(
                            item.occurrence_count or 0
                        ),
                    }
                    for item in events
                ],
                "risks": risk_facts,
                "risk_occurrences": [
                    _fact(
                        item,
                        (
                            "id",
                            "risk_case_id",
                            "occurrence_kind",
                            "source_finding_id",
                            "source_event_id",
                            "patrol_batch_id",
                            "observed_at",
                            "status_after",
                            "actor",
                            "reason",
                            "evidence_refs_json",
                        ),
                    )
                    for item in risk_occurrences
                ],
                "risk_remediations": [
                    _fact(
                        item,
                        (
                            "id",
                            "risk_case_id",
                            "status",
                            "owner",
                            "action_text",
                            "due_at",
                            "evidence_refs_json",
                            "created_by",
                            "completed_by",
                            "created_at",
                            "completed_at",
                            "updated_at",
                        ),
                    )
                    for item in risk_remediations
                ],
                "evidence_index": evidence_index,
                "evidence_refs": evidence_refs,
            })
            source_digest = _digest(source_facts)
            existing = (
                db.query(ReportManifestRecord)
                .filter(
                    ReportManifestRecord.patrol_batch_id
                    == patrol_batch_id,
                    ReportManifestRecord.source_digest == source_digest,
                )
                .first()
            )
            if existing is not None:
                return existing.to_dict()
            if latest is not None:
                # The prior cutoff is used above only to establish whether the
                # frozen facts changed. A changed fact set is an explicit
                # report revision and receives a new cutoff; unchanged retries
                # return the existing manifest without manufacturing versions.
                report_cutoff_at = now
                source_facts["report_cutoff_at"] = _jsonable(
                    report_cutoff_at
                )
                source_digest = _digest(source_facts)
                existing_revision = (
                    db.query(ReportManifestRecord)
                    .filter(
                        ReportManifestRecord.patrol_batch_id
                        == patrol_batch_id,
                        ReportManifestRecord.source_digest
                        == source_digest,
                    )
                    .first()
                )
                if existing_revision is not None:
                    return existing_revision.to_dict()
            version = int(latest.version) + 1 if latest else 1
            if latest is not None and latest.status != "superseded":
                latest.status = "superseded"
            status = (
                "final"
                if readiness["ready_for_final"]
                else "partial"
                if executions
                else "draft"
            )
            manifest_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"netops/report-manifest/{patrol_batch_id}/"
                    f"{source_digest}",
                )
            )
            row = ReportManifestRecord(
                id=manifest_id,
                patrol_batch_id=patrol_batch_id,
                version=version,
                schema_version="patrol-report-manifest/v2",
                status=status,
                target_manifest_digest=target_manifest_digest,
                runtime_manifest_digest=runtime_manifest_digest,
                comparison_context_digests_json=[
                    str(item.digest) for item in comparisons
                ],
                check_execution_fact_digest=check_execution_fact_digest,
                check_assessment_fact_digest=(
                    check_assessment_fact_digest
                ),
                risk_case_revision=risk_case_revision,
                evidence_index_digest=evidence_index_digest,
                generator_version=generator_version,
                source_facts_json=source_facts,
                source_digest=source_digest,
                artifact_object_keys_json={},
                artifact_sha256_json={},
                generated_by=generated_by,
                generated_at=now,
                report_cutoff_at=report_cutoff_at,
                qualification_fact_digest=qualification_fact_digest,
                coverage_fact_digest=coverage_fact_digest,
                created_at=now,
            )
            db.add(row)
            batch.report_manifest_id = manifest_id
            batch.report_manifest_digest = source_digest
            batch.updated_at = now
            db.flush()
            return row.to_dict()

    def attach_artifacts(
        self,
        manifest_id: str,
        *,
        object_keys: Mapping[str, str],
        sha256_by_artifact: Mapping[str, str],
    ) -> dict[str, Any]:
        keys = {str(key): str(value) for key, value in object_keys.items()}
        digests = {
            str(key): str(value).lower()
            for key, value in sha256_by_artifact.items()
        }
        if set(keys) != set(digests):
            raise ValueError("artifact object keys and SHA-256 names must match")
        if any(
            len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)
            for value in digests.values()
        ):
            raise ValueError("every artifact requires a lowercase SHA-256 digest")
        with self._session_scope() as db:
            row = (
                db.query(ReportManifestRecord)
                .filter(ReportManifestRecord.id == manifest_id)
                .with_for_update()
                .first()
            )
            if row is None:
                raise ValueError("report manifest not found")
            existing_keys = dict(row.artifact_object_keys_json or {})
            existing_digests = dict(row.artifact_sha256_json or {})
            if existing_keys or existing_digests:
                if existing_keys != keys or existing_digests != digests:
                    raise ValueError(
                        "report manifest artifacts are immutable once attached"
                    )
                return row.to_dict()
            row.artifact_object_keys_json = keys
            row.artifact_sha256_json = digests
            db.flush()
            return row.to_dict()

    def get(self, manifest_id: str) -> dict[str, Any] | None:
        with self._session_scope() as db:
            row = db.get(ReportManifestRecord, manifest_id)
            return row.to_dict() if row is not None else None

    def latest_for_batch(
        self,
        patrol_batch_id: str,
    ) -> dict[str, Any] | None:
        with self._session_scope() as db:
            row = (
                db.query(ReportManifestRecord)
                .filter(
                    ReportManifestRecord.patrol_batch_id
                    == patrol_batch_id,
                    ReportManifestRecord.status != "superseded",
                )
                .order_by(ReportManifestRecord.version.desc())
                .first()
            )
            return row.to_dict() if row is not None else None

    def _readiness(
        self,
        db: Any,
        *,
        batch: PatrolBatchRecord,
        targets: list[PatrolBatchDeviceRecord],
        executions: list[CheckExecutionRecord],
        assessments: list[CheckAssessmentRecord],
        runtime_manifest: CheckItemRuntimeManifestRecord | None,
        runtime_manifest_issues: list[str],
    ) -> dict[str, Any]:
        reasons: list[str] = list(runtime_manifest_issues)
        target_count = int(batch.target_count or 0)
        target_device_ids = {
            str(item.device_id) for item in targets if item.device_id
        }
        execution_device_ids = {
            str(item.device_id) for item in executions
        }
        if runtime_manifest is None:
            reasons.append("runtime_manifest_missing")
        if not batch.target_manifest_digest:
            reasons.append("target_manifest_digest_missing")
        if target_count <= 0:
            reasons.append("target_scope_empty")
        if len(target_device_ids) != target_count:
            reasons.append("target_manifest_count_mismatch")
        if target_device_ids - execution_device_ids:
            reasons.append("target_devices_without_check_execution")
        if not executions:
            reasons.append("check_executions_missing")
        runtime_payload = (
            dict(runtime_manifest.manifest_json or {})
            if runtime_manifest is not None
            else {}
        )
        device_check_item_versions = dict(
            runtime_payload.get("device_check_item_versions") or {}
        )
        if device_check_item_versions:
            expected_pairs = {
                (str(device_id), str(check_item_version_id))
                for device_id, version_ids in (
                    device_check_item_versions.items()
                )
                for check_item_version_id in version_ids or []
            }
        else:
            expected_pairs = {
                (device_id, str(check_item_version_id))
                for device_id in target_device_ids
                for check_item_version_id in (
                    runtime_manifest.check_item_versions_json
                    if runtime_manifest is not None
                    else []
                )
            }
        actual_pairs = {
            (
                str(item.device_id),
                str(item.check_item_version_id),
            )
            for item in executions
        }
        missing_pairs = sorted(expected_pairs - actual_pairs)
        unexpected_pairs = sorted(actual_pairs - expected_pairs)
        if missing_pairs:
            reasons.append("check_execution_matrix_incomplete")
        if unexpected_pairs and runtime_manifest is not None:
            reasons.append("check_execution_matrix_unexpected")
        nonterminal = [
            str(item.id)
            for item in executions
            if str(item.execution_status)
            not in self._TERMINAL_EXECUTION_STATUSES
        ]
        if nonterminal:
            reasons.append("check_executions_not_terminal")
        assessed_execution_ids = {
            str(item.execution_id) for item in assessments
        }
        missing_assessments = [
            str(item.id)
            for item in executions
            if str(item.id) not in assessed_execution_ids
        ]
        if missing_assessments:
            reasons.append("check_assessments_missing")
        health_conclusions = [
            item
            for item in assessments
            if str(item.assessment_status) in {"healthy", "at_risk"}
        ]
        if any(
            not item.evidence_qualification_id
            for item in health_conclusions
        ):
            reasons.append("evidence_qualifications_missing")
        if any(
            not item.coverage_decision_id
            for item in health_conclusions
        ):
            reasons.append("coverage_decisions_missing")
        workflow_ids = sorted(
            {
                str(item.workflow_run_id)
                for item in targets
                if item.workflow_run_id
            }
        )
        nonterminal_workflows: list[str] = []
        if workflow_ids:
            rows = (
                db.query(WorkflowRun.id, WorkflowRun.status)
                .filter(WorkflowRun.id.in_(workflow_ids))
                .all()
            )
            status_by_id = {
                str(run_id): str(status) for run_id, status in rows
            }
            nonterminal_workflows = [
                run_id
                for run_id in workflow_ids
                if status_by_id.get(run_id)
                not in {"completed", "failed", "cancelled"}
            ]
            if nonterminal_workflows:
                reasons.append("workflow_runs_not_terminal")
        return {
            "ready_for_final": not reasons,
            "reason_codes": list(dict.fromkeys(reasons)),
            "target_count": target_count,
            "target_record_count": len(target_device_ids),
            "execution_device_count": len(execution_device_ids),
            "execution_count": len(executions),
            "expected_execution_count": len(expected_pairs),
            "assessment_count": len(assessments),
            "missing_execution_pairs": [
                {
                    "device_id": device_id,
                    "check_item_version_id": check_item_version_id,
                }
                for device_id, check_item_version_id in missing_pairs
            ],
            "unexpected_execution_pairs": [
                {
                    "device_id": device_id,
                    "check_item_version_id": check_item_version_id,
                }
                for device_id, check_item_version_id in unexpected_pairs
            ],
            "nonterminal_execution_ids": nonterminal,
            "missing_assessment_execution_ids": missing_assessments,
            "nonterminal_workflow_run_ids": nonterminal_workflows,
        }

    @staticmethod
    def _runtime_manifest_issues(
        *,
        batch: PatrolBatchRecord,
        runtime_manifest: CheckItemRuntimeManifestRecord | None,
    ) -> list[str]:
        if runtime_manifest is None:
            return ["runtime_manifest_missing"]
        issues: list[str] = []
        if (
            batch.runtime_manifest_id
            and str(batch.runtime_manifest_id) != str(runtime_manifest.id)
        ):
            issues.append("runtime_manifest_id_mismatch")
        if (
            batch.runtime_manifest_digest
            and str(batch.runtime_manifest_digest)
            != str(runtime_manifest.digest)
        ):
            issues.append("runtime_manifest_digest_mismatch")
        payload = dict(runtime_manifest.manifest_json or {})
        embedded_digest = str(payload.pop("digest", "") or "")
        calculated_digest = _digest(payload)
        if (
            not embedded_digest
            or embedded_digest != str(runtime_manifest.digest)
            or calculated_digest != str(runtime_manifest.digest)
        ):
            issues.append("runtime_manifest_content_digest_invalid")
        if (
            str(runtime_manifest.target_manifest_digest)
            != str(batch.target_manifest_digest or "")
        ):
            issues.append("runtime_target_manifest_digest_mismatch")
        if (
            str(runtime_manifest.inventory_snapshot_digest)
            != str(batch.inventory_snapshot_digest or "")
        ):
            issues.append("runtime_inventory_snapshot_digest_mismatch")
        payload_action_ids = sorted(
            str(item.get("id"))
            for item in payload.get("collection_actions", [])
            if isinstance(item, dict) and item.get("id")
        )
        payload_comparison_ids = sorted(
            str(item.get("id"))
            for item in payload.get("comparison_contexts", [])
            if isinstance(item, dict) and item.get("id")
        )
        payload_checks = {
            "patrol_batch_id": str(runtime_manifest.patrol_batch_id),
            "target_manifest_digest": str(
                runtime_manifest.target_manifest_digest
            ),
            "inventory_snapshot_digest": str(
                runtime_manifest.inventory_snapshot_digest
            ),
            "check_item_versions": sorted(
                str(item)
                for item in runtime_manifest.check_item_versions_json or []
            ),
            "runtime_bundle_refs": list(
                runtime_manifest.runtime_bundle_refs_json or []
            ),
            "entity_identity_policy_versions": sorted(
                str(item)
                for item in (
                    runtime_manifest.entity_identity_policy_versions_json
                    or []
                )
            ),
            "assessment_schema_version": str(
                runtime_manifest.assessment_schema_version
            ),
            "report_schema_version": str(
                runtime_manifest.report_schema_version
            ),
        }
        for field_name, expected in payload_checks.items():
            actual = payload.get(field_name)
            if isinstance(expected, list) and field_name != "runtime_bundle_refs":
                actual = sorted(str(item) for item in actual or [])
            if actual != expected:
                issues.append(f"runtime_manifest_{field_name}_mismatch")
        if payload_action_ids != sorted(
            str(item)
            for item in runtime_manifest.collection_actions_json or []
        ):
            issues.append("runtime_manifest_collection_actions_mismatch")
        if payload_comparison_ids != sorted(
            str(item)
            for item in runtime_manifest.comparison_context_ids_json or []
        ):
            issues.append("runtime_manifest_comparison_contexts_mismatch")
        return list(dict.fromkeys(issues))
