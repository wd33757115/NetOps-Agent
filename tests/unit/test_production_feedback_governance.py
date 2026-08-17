from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.core.check_items.production_feedback import (
    ProductionFeedbackConflictError,
    ProductionFeedbackService,
    aggregate_feedback_metrics,
)
from src.infrastructure.db.models import (
    Base,
    CheckAssessmentRecord,
    CheckExecutionRecord,
    CheckItemRecord,
    CheckItemVersionRecord,
    NetworkEventOccurrenceRecord,
    NetworkEventRecord,
    OfflinePatrolJobRecord,
    ProductionFeedbackRecord,
    ProductionFeedbackWorkItemRecord,
    RuleGenerationJobRecord,
)


@contextmanager
def _session_scope(session_factory):
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _runtime(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'production-feedback.db'}")
    Base.metadata.create_all(
        engine,
        tables=[
            CheckItemRecord.__table__,
            CheckItemVersionRecord.__table__,
            CheckExecutionRecord.__table__,
            CheckAssessmentRecord.__table__,
            NetworkEventRecord.__table__,
            NetworkEventOccurrenceRecord.__table__,
            OfflinePatrolJobRecord.__table__,
            RuleGenerationJobRecord.__table__,
            ProductionFeedbackRecord.__table__,
            ProductionFeedbackWorkItemRecord.__table__,
        ],
    )
    session_factory = sessionmaker(bind=engine)
    scope = lambda: _session_scope(session_factory)
    now = datetime.now(timezone.utc)
    with scope() as db:
        db.add(
            CheckItemRecord(
                id="interface.oper_status",
                name="Interface status",
                category="interface",
                status="active",
                current_version=1,
                created_at=now,
                updated_at=now,
            )
        )
        db.add(
            CheckItemVersionRecord(
                id="check-interface-v1",
                check_item_id="interface.oper_status",
                version=1,
                result_mode="structured",
                assessment_mode="health",
                canonical_contract_json={"observations": []},
                report_projection="interface_status",
                status="active",
                digest="a" * 64,
                created_by="seed",
                created_at=now,
                activated_at=now,
            )
        )
        execution = CheckExecutionRecord(
            id="execution-1",
            idempotency_key="feedback-execution-1",
            patrol_batch_id="batch-1",
            device_id="device-1",
            check_item_version_id="check-interface-v1",
            collection_action_ids_json=[],
            runtime_bundle_id="bundle-interface",
            runtime_bundle_version_id="bundle-interface-v2",
            runtime_bundle_digest="b" * 64,
            execution_status="completed",
            reason_codes_json=[],
            target_manifest_digest="c" * 64,
            evidence_refs_json=[{"type": "snapshot", "id": "snapshot-1"}],
            completed_at=now,
            created_at=now,
            updated_at=now,
        )
        assessment = CheckAssessmentRecord(
            id="assessment-1",
            idempotency_key="feedback-assessment-1",
            patrol_batch_id="batch-1",
            device_id="device-1",
            check_item_version_id="check-interface-v1",
            execution_id="execution-1",
            assessment_status="healthy",
            completeness_status="complete",
            comparison_status="comparable",
            observed_entity_count=12,
            evaluated_entity_count=12,
            threshold_unknown_count=0,
            current_finding_ids_json=[],
            risk_case_ids_json=[],
            blocking_reason_codes_json=[],
            assessment_basis_json={"policy_version_id": "policy-v1"},
            assessment_basis_digest="d" * 64,
            evidence_digest="e" * 64,
            assessed_at=now,
            created_at=now,
        )
        execution.assessment_id = assessment.id
        db.add_all([execution, assessment])
    return ProductionFeedbackService(session_scope=scope), session_factory, scope


def test_confirmed_feedback_creates_governance_work_item_without_model_side_effects(
    tmp_path,
):
    service, session_factory, _scope = _runtime(tmp_path)
    submitted = service.submit(
        execution_id="execution-1",
        assessment_id="assessment-1",
        feedback_type="missed_detection",
        statement="Port GE1/0/8 was down but no event was emitted.",
        reported_by="operator-a",
        idempotency_key="feedback-submit-001",
    )
    replay = service.submit(
        execution_id="execution-1",
        assessment_id="assessment-1",
        feedback_type="missed_detection",
        statement="Port GE1/0/8 was down but no event was emitted.",
        reported_by="operator-a",
        idempotency_key="feedback-submit-001",
    )
    assert submitted["id"] == replay["id"]
    assert submitted["source_context_json"]["runtime_bundle_version_id"] == (
        "bundle-interface-v2"
    )
    assert submitted["source_context_json"]["evidence_digest"] == "e" * 64

    with pytest.raises(ProductionFeedbackConflictError, match="cannot triage"):
        service.triage(
            submitted["id"],
            outcome="confirmed",
            reason="Confirmed against the raw evidence.",
            triaged_by="operator-a",
            work_item_kind="rule_sample",
            idempotency_key="feedback-triage-self-001",
        )

    triaged = service.triage(
        submitted["id"],
        outcome="confirmed",
        reason="Confirmed against the immutable evidence package.",
        triaged_by="reviewer-b",
        work_item_kind="rule_sample",
        idempotency_key="feedback-triage-001",
    )
    assert triaged["status"] == "work_item_created"
    assert triaged["work_item"]["work_item_kind"] == "rule_sample"
    assert triaged["work_item"]["action_payload_json"]["next_action"] == (
        "import_sanitized_rule_asset_source"
    )
    assert triaged["historical_facts_mutated"] is False
    assert triaged["model_generation_started"] is False

    with _session_scope(session_factory) as db:
        assert db.query(ProductionFeedbackRecord).count() == 1
        assert db.query(ProductionFeedbackWorkItemRecord).count() == 1
        assert db.query(RuleGenerationJobRecord).count() == 0
        execution = db.get(CheckExecutionRecord, "execution-1")
        assessment = db.get(CheckAssessmentRecord, "assessment-1")
        assert execution.execution_status == "completed"
        assert assessment.assessment_status == "healthy"
        assert assessment.evidence_digest == "e" * 64


def test_rule_sample_work_item_only_links_rule_asset_source_job(tmp_path):
    service, _session_factory, scope = _runtime(tmp_path)
    submitted = service.submit(
        execution_id="execution-1",
        assessment_id="assessment-1",
        feedback_type="missed_detection",
        statement="The expected interface fault was not detected.",
        reported_by="operator-a",
        idempotency_key="feedback-submit-002",
    )
    triaged = service.triage(
        submitted["id"],
        outcome="confirmed",
        reason="The evidence confirms a missed detection.",
        triaged_by="reviewer-b",
        work_item_kind="rule_sample",
        idempotency_key="feedback-triage-002",
    )
    now = datetime.now(timezone.utc)
    with scope() as db:
        db.add_all(
            [
                OfflinePatrolJobRecord(
                    id="offline-history-1",
                    idempotency_key="offline-history-idem-1",
                    purpose="offline_patrol",
                    job_type="parser_validation",
                    name="Historical import",
                    owner_id="operator-a",
                    status="ready",
                    progress_json={},
                    created_at=now,
                    updated_at=now,
                ),
                OfflinePatrolJobRecord(
                    id="offline-rule-source-1",
                    idempotency_key="offline-rule-source-idem-1",
                    purpose="rule_asset_source",
                    job_type="parser_validation",
                    name="Governed feedback sample",
                    owner_id="operator-a",
                    status="ready",
                    progress_json={},
                    created_at=now,
                    updated_at=now,
                ),
            ]
        )

    with pytest.raises(
        ProductionFeedbackConflictError,
        match="rule asset source workflow",
    ):
        service.link_candidate(
            triaged["work_item"]["id"],
            candidate_type="offline_job",
            candidate_id="offline-history-1",
            linked_by="engineer-c",
        )

    linked = service.link_candidate(
        triaged["work_item"]["id"],
        candidate_type="offline_job",
        candidate_id="offline-rule-source-1",
        linked_by="engineer-c",
    )
    assert linked["status"] == "candidate_linked"
    assert linked["candidate_ref_json"] == {
        "type": "offline_job",
        "id": "offline-rule-source-1",
        "status": "ready",
        "purpose": "rule_asset_source",
    }


def test_event_feedback_rejects_an_occurrence_from_another_execution(tmp_path):
    service, _session_factory, scope = _runtime(tmp_path)
    now = datetime.now(timezone.utc)
    with scope() as db:
        db.add(
            NetworkEventRecord(
                id="event-1",
                idempotency_key="event-idem-1",
                check_item_id="interface.oper_status",
                check_item_version_id="check-interface-v1",
                check_execution_id="execution-other",
                event_type="InterfaceDown",
                severity="major",
                device_id="device-1",
                entity_type="interface",
                entity_key="GE1/0/8",
                correlation_key="device-1|GE1/0/8",
                occurred_at=now,
                first_seen_at=now,
                last_seen_at=now,
                status="open",
                payload_json={},
                created_at=now,
            )
        )
        db.add(
            NetworkEventOccurrenceRecord(
                id="occurrence-1",
                idempotency_key="occurrence-idem-1",
                event_id="event-1",
                observed_at=now,
                severity="major",
                occurrence_kind="observed",
                payload_json={},
                created_at=now,
            )
        )

    with pytest.raises(ProductionFeedbackConflictError, match="does not belong"):
        service.submit(
            execution_id="execution-1",
            assessment_id="assessment-1",
            feedback_type="false_positive",
            statement="This event is a false positive.",
            reported_by="operator-a",
            event_occurrence_id="occurrence-1",
            idempotency_key="feedback-submit-003",
        )


def test_feedback_metrics_do_not_claim_precise_rate_for_small_samples():
    metrics = aggregate_feedback_metrics(
        [
            {"feedback_type": "false_positive", "triage_outcome": "confirmed"},
            {"feedback_type": "missed_detection", "triage_outcome": "confirmed"},
            {"feedback_type": "wrong_scope", "triage_outcome": "inconclusive"},
        ],
        candidate_event_count=4,
    )
    assert metrics["confirmed_false_positive_count"] == 1
    assert metrics["confirmed_missed_detection_count"] == 1
    assert metrics["confirmed_false_positive_rate"] == 0.25
    assert metrics["feedback_rate_sample_sufficient"] is False
    assert metrics["feedback_confidence"] == "insufficient"
