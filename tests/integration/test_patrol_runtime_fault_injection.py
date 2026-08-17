from __future__ import annotations

import hashlib
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from kombu import Connection

from src.auth.token_store import get_redis
from src.core.check_items.report_manifest import ReportManifestService
from src.core.check_items.reporting import (
    CheckItemReportMaterializer,
    build_check_item_report_view,
)
from src.core.events.idempotency import (
    claim_processing,
    is_processed,
    mark_processed,
    renew_processing,
)
from src.core.events.streams import (
    IDEMPOTENCY_PREFIX,
    IDEMPOTENCY_PROCESSING_PREFIX,
)
from src.core.parser.repository import ParserRepository
from src.core.patrol.batch_manifest import PatrolBatchManifestRepository
from src.core.patrol.change_detector import NetworkChange
from src.core.patrol.change_event_repository import (
    ChangeEventRepository,
    deterministic_change_id,
)
from src.core.patrol.change_policy_repository import ChangePolicyRepository
from src.core.patrol.concurrency import (
    PatrolConcurrencyLeaseLostError,
    RedisExclusiveFencedLease,
)
from src.core.patrol.event_builder import NetworkEvent
from src.core.patrol.repository import PatrolRepository
from src.core.workflows.patrol_batch_service import iter_patrol_event_export_rows
from src.core.workflows.repository import (
    cancel_workflow_run,
    create_notification,
    mark_step_completed,
    mark_step_failed,
    update_run_status,
)
from src.gateway.audit_service import write_audit_log
from src.infrastructure.db.models import (
    AuditLogRecord,
    ChangePolicyRecord,
    ChangePolicyVersionRecord,
    CheckAssessmentRecord,
    CheckExecutionRecord,
    CheckItemRuntimeManifestRecord,
    CheckItemVersionRecord,
    NetworkChangeRecord,
    NetworkEventOccurrenceRecord,
    NetworkEventRecord,
    Notification,
    ParserJobSample,
    ParserOutboxEvent,
    ParserTemplateJob,
    PatrolBatchDeviceRecord,
    PatrolBatchRecord,
    PatrolRun,
    WorkflowRun,
    WorkflowStepRecord,
)
from src.infrastructure.db.postgres import get_db_session
from src.infrastructure.storage.minio_client import get_minio_storage

pytestmark = pytest.mark.integration


def test_cancelled_patrol_run_rejects_late_success_callback(postgres_available):
    del postgres_available
    suffix = uuid.uuid4().hex
    batch_id = f"cancel-fault-{suffix}"
    run_id = f"cancel-run-{suffix}"
    target_id = f"cancel-target-{suffix}"
    step_id = f"cancel-step-{suffix}"
    now = datetime.now(timezone.utc)
    with get_db_session() as db:
        db.add(
            PatrolBatchRecord(
                patrol_batch_id=batch_id,
                workflow_name="patrol-change-event",
                launch_mode="workflow",
                patrol_mode="production",
                target_type="device",
                target_label="fault injection",
                selector_json={},
                target_count=1,
                launched_count=1,
                skipped_count=0,
                dispatch_status="completed",
                report_status="not_requested",
                archived=False,
                created_at=now,
                updated_at=now,
            )
        )
        db.flush()
        db.add(
            WorkflowRun(
                id=run_id,
                template_name="patrol-change-event",
                source="device-management",
                status="running",
                context={"patrol_batch_id": batch_id, "device_id": f"device-{suffix}"},
                created_at=now,
                updated_at=now,
            )
        )
        db.add(
            PatrolBatchDeviceRecord(
                id=target_id,
                patrol_batch_id=batch_id,
                device_id=f"device-{suffix}",
                selection_status="launched",
                preflight_status="ready",
                workflow_run_id=run_id,
                dispatch_status="dispatched",
                dispatch_attempts=1,
                created_at=now,
                updated_at=now,
            )
        )
        db.add(
            WorkflowStepRecord(
                id=step_id,
                run_id=run_id,
                step_index=0,
                step_name="collect",
                skill_name="device-patrol",
                celery_task_id=f"task-{suffix}",
                status="running",
                started_at=now,
                created_at=now,
            )
        )
    try:
        batch_cancel = PatrolBatchManifestRepository().cancel_dispatch(
            batch_id,
            actor="fault-injection",
            reason="verify terminal fencing",
        )
        run_cancel = cancel_workflow_run(
            run_id,
            actor="fault-injection",
            reason="verify terminal fencing",
        )
        repeated = cancel_workflow_run(
            run_id,
            actor="fault-injection",
            reason="verify terminal fencing",
        )

        assert batch_cancel is not None and batch_cancel["changed"] is True
        assert run_cancel["changed"] is True
        assert repeated["idempotent"] is True
        assert mark_step_completed(step_id, {"success": True}) is False
        assert mark_step_failed(step_id, "late worker failure") is False
        assert update_run_status(run_id, "completed") is False
        assert update_run_status(
            run_id,
            "failed",
            error="late worker failure",
        ) is False

        with get_db_session() as db:
            assert db.get(PatrolBatchRecord, batch_id).dispatch_status == "cancelled"
            assert db.get(WorkflowRun, run_id).status == "cancelled"
            assert db.get(WorkflowStepRecord, step_id).status == "cancelled"
            assert db.get(PatrolBatchDeviceRecord, target_id).dispatch_status == "cancelled"
    finally:
        with get_db_session() as db:
            db.query(WorkflowStepRecord).filter_by(run_id=run_id).delete()
            db.query(PatrolBatchDeviceRecord).filter_by(patrol_batch_id=batch_id).delete()
            db.query(WorkflowRun).filter_by(id=run_id).delete()
            db.query(PatrolBatchRecord).filter_by(patrol_batch_id=batch_id).delete()


def test_cancelled_unlaunched_target_settles_full_check_item_denominator(
    postgres_available,
):
    del postgres_available
    suffix = uuid.uuid4().hex
    batch_id = f"cancel-denominator-{suffix}"
    manifest_id = f"manifest-{suffix}"
    manifest_digest = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"manifest|{suffix}",
    ).hex * 2
    completed_device = f"completed-{suffix}"
    cancelled_device = f"cancelled-{suffix}"
    failed_device = f"failed-{suffix}"
    timeout_device = f"timeout-{suffix}"
    run_id = f"completed-run-{suffix}"
    timeout_run_id = f"timeout-run-{suffix}"
    now = datetime.now(timezone.utc)
    with get_db_session() as db:
        check_version = (
            db.query(CheckItemVersionRecord)
            .filter(
                CheckItemVersionRecord.status == "active",
                CheckItemVersionRecord.check_item_id == "device.version",
            )
            .order_by(CheckItemVersionRecord.check_item_id)
            .first()
        )
        if check_version is None:
            pytest.skip("an active check-item version is required")
        check_version_id = str(check_version.id)
        completed_execution_id = f"execution-completed-{suffix}"
        cancelled_execution_id = f"execution-cancelled-{suffix}"
        completed_assessment_id = f"assessment-completed-{suffix}"
        target_manifest_digest = "a" * 64
        inventory_snapshot_digest = "b" * 64
        manifest_payload = {
            "patrol_batch_id": batch_id,
            "target_manifest_digest": target_manifest_digest,
            "inventory_snapshot_digest": inventory_snapshot_digest,
            "check_item_versions": [check_version_id],
            "collection_actions": [],
            "runtime_bundle_refs": [],
            "comparison_contexts": [],
            "entity_identity_policy_versions": [],
            "assessment_schema_version": "check-assessment/v1",
            "report_schema_version": "check-item-report/v5",
            "device_check_item_versions": {
                completed_device: [check_version_id],
                cancelled_device: [check_version_id],
                failed_device: [check_version_id],
                timeout_device: [check_version_id],
            }
        }
        manifest_digest = hashlib.sha256(
            json.dumps(
                manifest_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        manifest_payload["digest"] = manifest_digest
        db.add(
            PatrolBatchRecord(
                patrol_batch_id=batch_id,
                workflow_name="patrol-change-event",
                launch_mode="workflow",
                patrol_mode="production",
                report_purpose="shadow_validation",
                target_type="manual",
                target_label="cancel denominator fault injection",
                selector_json={},
                target_count=4,
                launched_count=2,
                skipped_count=0,
                target_manifest_digest=target_manifest_digest,
                inventory_snapshot_digest=inventory_snapshot_digest,
                runtime_manifest_id=manifest_id,
                runtime_manifest_digest=manifest_digest,
                dispatch_status="cancelled",
                report_status="not_requested",
                archived=False,
                created_at=now,
                updated_at=now,
            )
        )
        db.flush()
        db.add(
            CheckItemRuntimeManifestRecord(
                id=manifest_id,
                patrol_batch_id=batch_id,
                schema_version=2,
                target_manifest_digest=target_manifest_digest,
                inventory_snapshot_digest=inventory_snapshot_digest,
                check_item_versions_json=[check_version_id],
                collection_actions_json=[],
                runtime_bundle_refs_json=[],
                comparison_context_ids_json=[],
                entity_identity_policy_versions_json=[],
                assessment_schema_version="check-assessment/v1",
                report_schema_version="check-item-report/v5",
                manifest_json=manifest_payload,
                digest=manifest_digest,
                status="frozen",
                created_by="fault-injection",
                created_at=now,
            )
        )
        db.add(
            WorkflowRun(
                id=run_id,
                template_name="patrol-change-event",
                source="device-management",
                status="completed",
                context={
                    "patrol_batch_id": batch_id,
                    "device_id": completed_device,
                },
                created_at=now,
                updated_at=now,
                completed_at=now,
            )
        )
        db.add(
            WorkflowRun(
                id=timeout_run_id,
                template_name="patrol-change-event",
                source="device-management",
                status="failed",
                context={
                    "patrol_batch_id": batch_id,
                    "device_id": timeout_device,
                    "failure_code": "command_timeout",
                },
                error_message="command exceeded deadline",
                created_at=now,
                updated_at=now,
                completed_at=now,
            )
        )
        db.add_all(
            [
                PatrolBatchDeviceRecord(
                    id=f"target-completed-{suffix}",
                    patrol_batch_id=batch_id,
                    device_id=completed_device,
                    selection_status="launched",
                    preflight_status="ready",
                    workflow_run_id=run_id,
                    dispatch_status="dispatched",
                    dispatch_attempts=1,
                    created_at=now,
                    updated_at=now,
                ),
                PatrolBatchDeviceRecord(
                    id=f"target-cancelled-{suffix}",
                    patrol_batch_id=batch_id,
                    device_id=cancelled_device,
                    selection_status="selected",
                    preflight_status="ready",
                    dispatch_status="cancelled",
                    dispatch_attempts=0,
                    failure_code="patrol_batch_cancelled",
                    created_at=now,
                    updated_at=now,
                ),
                PatrolBatchDeviceRecord(
                    id=f"target-failed-{suffix}",
                    patrol_batch_id=batch_id,
                    device_id=failed_device,
                    selection_status="selected",
                    preflight_status="failed",
                    dispatch_status="failed",
                    dispatch_attempts=3,
                    failure_code="credential_missing",
                    error_message="credential is unavailable",
                    created_at=now,
                    updated_at=now,
                ),
                PatrolBatchDeviceRecord(
                    id=f"target-timeout-{suffix}",
                    patrol_batch_id=batch_id,
                    device_id=timeout_device,
                    selection_status="launched",
                    preflight_status="ready",
                    workflow_run_id=timeout_run_id,
                    dispatch_status="dispatched",
                    dispatch_attempts=1,
                    failure_code="command_timeout",
                    error_message="command exceeded deadline",
                    created_at=now,
                    updated_at=now,
                ),
            ]
        )
        db.add_all(
            [
                CheckExecutionRecord(
                    id=completed_execution_id,
                    idempotency_key=completed_execution_id,
                    patrol_batch_id=batch_id,
                    device_id=completed_device,
                    check_item_version_id=check_version_id,
                    collection_action_ids_json=[],
                    execution_status="completed",
                    assessment_id=completed_assessment_id,
                    reason_codes_json=[],
                    observation_count=1,
                    observed_entity_count=1,
                    target_manifest_digest="a" * 64,
                    evidence_refs_json=[run_id],
                    completed_at=now,
                    created_at=now,
                    updated_at=now,
                ),
                CheckExecutionRecord(
                    id=cancelled_execution_id,
                    idempotency_key=cancelled_execution_id,
                    patrol_batch_id=batch_id,
                    device_id=cancelled_device,
                    check_item_version_id=check_version_id,
                    collection_action_ids_json=[],
                    execution_status="planned",
                    reason_codes_json=[],
                    target_manifest_digest="a" * 64,
                    evidence_refs_json=[],
                    created_at=now,
                    updated_at=now,
                ),
                CheckExecutionRecord(
                    id=f"execution-failed-{suffix}",
                    idempotency_key=f"execution-failed-{suffix}",
                    patrol_batch_id=batch_id,
                    device_id=failed_device,
                    check_item_version_id=check_version_id,
                    collection_action_ids_json=[],
                    execution_status="planned",
                    reason_codes_json=[],
                    target_manifest_digest="a" * 64,
                    evidence_refs_json=[],
                    created_at=now,
                    updated_at=now,
                ),
                CheckExecutionRecord(
                    id=f"execution-timeout-{suffix}",
                    idempotency_key=f"execution-timeout-{suffix}",
                    patrol_batch_id=batch_id,
                    device_id=timeout_device,
                    check_item_version_id=check_version_id,
                    collection_action_ids_json=[],
                    execution_status="planned",
                    reason_codes_json=[],
                    target_manifest_digest="a" * 64,
                    evidence_refs_json=[timeout_run_id],
                    created_at=now,
                    updated_at=now,
                ),
            ]
        )
        db.flush()
        db.add(
            CheckAssessmentRecord(
                id=completed_assessment_id,
                idempotency_key=completed_assessment_id,
                patrol_batch_id=batch_id,
                device_id=completed_device,
                check_item_version_id=check_version_id,
                execution_id=completed_execution_id,
                assessment_status="informational",
                completeness_status="complete",
                comparison_status="first_observation",
                observed_entity_count=1,
                evaluated_entity_count=1,
                threshold_unknown_count=0,
                current_finding_ids_json=[],
                risk_case_ids_json=[],
                blocking_reason_codes_json=[],
                assessment_basis_json={},
                evidence_digest="c" * 64,
                assessed_at=now,
                created_at=now,
            )
        )
    try:
        requested = PatrolBatchManifestRepository().request_terminal_report(
            batch_id
        )

        assert requested is True
        PatrolBatchManifestRepository._reconcile_failed_check_item_workflows(
            batch_id
        )
        manifest = ReportManifestService().build(
            batch_id,
            generated_by="fault-injection",
        )
        report = build_check_item_report_view(manifest)
        assert manifest["status"] == "final"
        assert report["coverage"]["expected_check_count"] == 4
        assert report["coverage"]["assessed_check_count"] == 4
        assert report["assessment_counts"] == {
            "informational": 1,
            "unable_to_determine": 3,
        }
        materialized = CheckItemReportMaterializer().materialize(
            batch_id,
            generated_by="fault-injection",
        )
        assert set(materialized["artifact_object_keys"]) == {
            "markdown",
            "docx",
            "excel",
            "json",
            "evidence",
        }
        with get_db_session() as db:
            batch = db.get(PatrolBatchRecord, batch_id)
            executions = {
                str(item.device_id): item
                for item in db.query(CheckExecutionRecord)
                .filter(CheckExecutionRecord.patrol_batch_id == batch_id)
                .all()
            }
            assessments = {
                str(item.device_id): item
                for item in db.query(CheckAssessmentRecord)
                .filter(CheckAssessmentRecord.patrol_batch_id == batch_id)
                .all()
            }
            assert len(assessments) == 4
            assert batch.report_status == "queued"
            assert executions[completed_device].execution_status == "completed"
            assert assessments[completed_device].assessment_status == "informational"
            assert executions[cancelled_device].execution_status == "cancelled"
            assert assessments[cancelled_device].assessment_status == (
                "unable_to_determine"
            )
            assert "patrol_batch_cancelled" in (
                assessments[cancelled_device].blocking_reason_codes_json
            )
            assert executions[failed_device].execution_status == (
                "collection_failed"
            )
            assert assessments[failed_device].assessment_status == (
                "unable_to_determine"
            )
            assert "credential_missing" in (
                assessments[failed_device].blocking_reason_codes_json
            )
            assert executions[timeout_device].execution_status == (
                "collection_failed"
            )
            assert assessments[timeout_device].assessment_status == (
                "unable_to_determine"
            )
            assert "command_timeout" in (
                assessments[timeout_device].blocking_reason_codes_json
            )
    finally:
        get_minio_storage().delete_objects_by_prefix(
            f"reports/patrol/{batch_id}"
        )
        with get_db_session() as db:
            db.query(CheckAssessmentRecord).filter(
                CheckAssessmentRecord.patrol_batch_id == batch_id
            ).delete(synchronize_session=False)
            db.query(CheckExecutionRecord).filter(
                CheckExecutionRecord.patrol_batch_id == batch_id
            ).delete(synchronize_session=False)
            db.query(PatrolBatchDeviceRecord).filter_by(
                patrol_batch_id=batch_id
            ).delete(synchronize_session=False)
            db.query(WorkflowRun).filter(
                WorkflowRun.id.in_([run_id, timeout_run_id])
            ).delete(
                synchronize_session=False
            )
            db.query(CheckItemRuntimeManifestRecord).filter_by(
                id=manifest_id
            ).delete(synchronize_session=False)
            db.query(PatrolBatchRecord).filter_by(
                patrol_batch_id=batch_id
            ).delete(synchronize_session=False)


def test_patrol_batch_targets_are_claimed_once_across_concurrent_dispatchers(
    postgres_available,
):
    del postgres_available
    suffix = uuid.uuid4().hex
    batch_id = f"dispatch-fault-{suffix}"
    now = datetime.now(timezone.utc)
    with get_db_session() as db:
        db.add(
            PatrolBatchRecord(
                patrol_batch_id=batch_id,
                workflow_name="patrol-change-event",
                launch_mode="workflow",
                patrol_mode="production",
                target_type="manual",
                target_label="fault injection",
                selector_json={},
                target_count=40,
                launched_count=0,
                skipped_count=0,
                dispatch_status="pending",
                dispatch_requested_at=now,
                report_status="not_requested",
                archived=False,
                created_at=now,
                updated_at=now,
            )
        )
        db.flush()
        for index in range(40):
            db.add(
                PatrolBatchDeviceRecord(
                    id=str(uuid.uuid4()),
                    patrol_batch_id=batch_id,
                    device_id=f"{suffix}-device-{index}",
                    selection_status="selected",
                    preflight_status="ready",
                    dispatch_status="pending",
                    dispatch_attempts=0,
                    created_at=now,
                    updated_at=now,
                )
            )
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            claims = list(
                executor.map(
                    lambda _index: PatrolBatchManifestRepository().claim_dispatch_targets(
                        batch_id,
                        limit=40,
                    ),
                    range(2),
                )
            )
        target_ids = [str(item["target_id"]) for batch in claims for item in batch]
        assert len(target_ids) == 40
        assert len(set(target_ids)) == 40
    finally:
        with get_db_session() as db:
            db.query(PatrolBatchDeviceRecord).filter_by(patrol_batch_id=batch_id).delete()
            db.query(PatrolBatchRecord).filter_by(patrol_batch_id=batch_id).delete()


def test_patrol_event_export_remains_bound_to_immutable_occurrence(postgres_available):
    del postgres_available
    suffix = uuid.uuid4().hex
    event_id = f"event-{suffix}"
    occurrence_id = f"occurrence-{suffix}"
    run_id = f"snapshot-{suffix}"
    observed_at = datetime.now(timezone.utc)
    with get_db_session() as db:
        db.add(
            NetworkEventRecord(
                id=event_id,
                idempotency_key=f"event-key-{suffix}",
                event_type="InterfaceDown",
                severity="low",
                device_id=f"device-{suffix}",
                entity_type="interface_l2",
                entity_key="GigabitEthernet1/0/1",
                correlation_key=f"correlation-{suffix}",
                run_id=run_id,
                occurred_at=observed_at,
                first_seen_at=observed_at,
                last_seen_at=observed_at,
                last_seen_order_key="2",
                occurrence_count=2,
                status="resolved",
                payload_json={"new": "up"},
                created_at=observed_at,
            )
        )
        db.flush()
        db.add(
            NetworkEventOccurrenceRecord(
                id=occurrence_id,
                idempotency_key=f"occurrence-key-{suffix}",
                event_id=event_id,
                run_id=run_id,
                source_change_id=f"change-{suffix}",
                observed_at=observed_at,
                severity="high",
                occurrence_kind="opening",
                payload_json={
                    "event_type": "InterfaceDown",
                    "severity": "high",
                    "device_id": f"device-{suffix}",
                    "entity_type": "interface_l2",
                    "entity_key": "GigabitEthernet1/0/1",
                    "new": "down",
                    "aggregate_status_after_occurrence": "open",
                },
                created_at=observed_at,
            )
        )
    try:
        rows = list(
            iter_patrol_event_export_rows(
                [run_id],
                {f"device-{suffix}": "SW-1"},
            )
        )
        assert len(rows) == 1
        assert rows[0]["occurrence_id"] == occurrence_id
        assert rows[0]["status"] == "open"
        assert rows[0]["severity"] == "high"
    finally:
        with get_db_session() as db:
            db.query(NetworkEventOccurrenceRecord).filter_by(id=occurrence_id).delete()
            db.query(NetworkEventRecord).filter_by(id=event_id).delete()


def _redis_or_skip():
    client = get_redis()
    if client is None:
        pytest.skip("Redis is unavailable")
    try:
        client.ping()
    except Exception as exc:
        pytest.skip(f"Redis is unavailable: {exc}")
    return client


def test_real_redis_processing_claim_is_atomic():
    client = _redis_or_skip()
    group = f"fault-injection-{uuid.uuid4().hex}"
    occurrence_id = uuid.uuid4().hex
    owners = [uuid.uuid4().hex for _ in range(16)]
    try:
        with ThreadPoolExecutor(max_workers=len(owners)) as executor:
            claimed = list(
                executor.map(
                    lambda owner: claim_processing(
                        group,
                        occurrence_id,
                        owner_token=owner,
                        lease_ms=30_000,
                    ),
                    owners,
                )
            )
        assert claimed.count("claimed") == 1
        assert claimed.count("busy") == len(owners) - 1
        winner = owners[claimed.index("claimed")]

        mark_processed(group, occurrence_id, owner_token=winner)

        assert is_processed(group, occurrence_id) is True
        assert (
            claim_processing(
                group,
                occurrence_id,
                owner_token=uuid.uuid4().hex,
                lease_ms=30_000,
            )
            == "completed"
        )
    finally:
        client.delete(
            f"{IDEMPOTENCY_PREFIX}{group}:{occurrence_id}",
            f"{IDEMPOTENCY_PROCESSING_PREFIX}{group}:{occurrence_id}",
        )


def test_real_redis_processing_lease_can_be_renewed():
    client = _redis_or_skip()
    group = f"lease-renewal-{uuid.uuid4().hex}"
    event_id = uuid.uuid4().hex
    owner = uuid.uuid4().hex
    try:
        assert claim_processing(group, event_id, owner_token=owner, lease_ms=1_000) == "claimed"
        time.sleep(0.7)
        assert renew_processing(
            group, event_id, owner_token=owner, lease_ms=1_000
        ) is True
        time.sleep(0.5)
        assert (
            claim_processing(
                group, event_id, owner_token=uuid.uuid4().hex, lease_ms=1_000
            )
            == "busy"
        )
    finally:
        client.delete(f"{IDEMPOTENCY_PROCESSING_PREFIX}{group}:{event_id}")


def test_real_redis_device_fence_rejects_worker_after_lease_loss():
    client = _redis_or_skip()
    scope = f"device-{uuid.uuid4().hex}"
    leases = RedisExclusiveFencedLease(
        lease_seconds=30,
        wait_seconds=1,
        key_prefix=f"netops:test:patrol-device:{uuid.uuid4().hex}",
    )
    first = leases.acquire(scope)
    second = None
    try:
        client.delete(first.key)
        second = leases.acquire(scope)

        assert second.fencing_token > first.fencing_token
        with pytest.raises(PatrolConcurrencyLeaseLostError):
            leases.assert_lease(first)
        leases.release(first)
        leases.assert_lease(second)
    finally:
        if second is not None:
            leases.release(second)
        client.delete(first.key, first.fence_key)


def test_real_redis_pending_message_is_reclaimed_after_worker_loss():
    client = _redis_or_skip()
    stream = f"netops:fault-injection:{uuid.uuid4().hex}"
    group = f"group-{uuid.uuid4().hex}"
    try:
        client.xgroup_create(stream, group, id="0", mkstream=True)
        message_id = client.xadd(stream, {"event": "worker-loss"})
        delivered = client.xreadgroup(
            groupname=group,
            consumername="dead-worker",
            streams={stream: ">"},
            count=1,
        )
        assert delivered
        time.sleep(0.02)

        claimed = client.xautoclaim(
            stream,
            group,
            "replacement-worker",
            min_idle_time=10,
            start_id="0-0",
            count=1,
        )

        messages = claimed[1]
        assert len(messages) == 1
        assert str(messages[0][0]) == str(message_id)
        assert client.xack(stream, group, message_id) == 1
    finally:
        client.delete(stream)


def test_real_broker_redelivery_fences_live_database_lease(postgres_available):
    del postgres_available
    broker_url = "amqp://guest:guest@localhost:5672//"
    queue_name = f"netops-fault-injection-{uuid.uuid4().hex}"
    logical_run_id = f"patrol-{uuid.uuid4()}"
    repository = PatrolRepository()
    first_connection = None
    second_connection = None
    try:
        repository.create_run(
            run_id=logical_run_id,
            source="device-patrol",
            mode="production",
            logical_run_id=logical_run_id,
            worker_id="worker-A",
            lease_seconds=900,
        )
        first_connection = Connection(broker_url)
        first_connection.connect()
        first_queue = first_connection.SimpleQueue(queue_name)
        first_queue.put({"logical_run_id": logical_run_id})
        first_delivery = first_queue.get(block=True, timeout=5)
        assert first_delivery.delivery_info["redelivered"] is False

        # Abrupt channel/connection loss leaves the delivery unacknowledged.
        first_connection.close()
        first_connection = None

        second_connection = Connection(broker_url)
        second_connection.connect()
        second_queue = second_connection.SimpleQueue(queue_name)
        redelivery = second_queue.get(block=True, timeout=5)
        assert redelivery.delivery_info["redelivered"] is True

        decision = repository.prepare_redelivery_attempt(
            logical_run_id,
            worker_id="worker-B",
        )

        assert decision.action == "allocate_new_attempt"
        assert repository.get_run(logical_run_id)["status"] == "abandoned"
        assert repository.get_run(decision.run_id)["worker_id"] == "worker-B"
        redelivery.ack()
        second_connection.close()
        second_connection = None
    finally:
        if first_connection is not None:
            first_connection.close()
        if second_connection is not None:
            second_connection.close()
        try:
            with Connection(broker_url) as cleanup_connection:
                cleanup_connection.channel().queue_delete(queue=queue_name)
        except Exception:
            pass
        with get_db_session() as db:
            db.query(PatrolRun).filter(PatrolRun.logical_run_id == logical_run_id).delete(
                synchronize_session=False
            )


def test_irreversible_consumers_have_durable_occurrence_receipts(postgres_available):
    del postgres_available
    audit_key = f"audit-consumer:{uuid.uuid4().hex}"
    notification_key = f"notify-consumer:{uuid.uuid4().hex}"
    try:
        for _ in range(2):
            write_audit_log(
                action="fault_injection",
                resource_type="event_occurrence",
                resource_id=audit_key,
                idempotency_key=audit_key,
                raise_on_error=True,
            )
            create_notification(
                user_id="fault-injection-user",
                title="Occurrence handled",
                idempotency_key=notification_key,
            )
        with get_db_session() as db:
            assert (
                db.query(AuditLogRecord)
                .filter(AuditLogRecord.idempotency_key == audit_key)
                .count()
                == 1
            )
            assert (
                db.query(Notification)
                .filter(Notification.idempotency_key == notification_key)
                .count()
                == 1
            )
    finally:
        with get_db_session() as db:
            db.query(AuditLogRecord).filter(
                AuditLogRecord.idempotency_key == audit_key
            ).delete(synchronize_session=False)
            db.query(Notification).filter(
                Notification.idempotency_key == notification_key
            ).delete(synchronize_session=False)


def test_change_policy_activation_is_globally_unique_per_observation_type(
    postgres_available,
):
    del postgres_available
    observation_type = f"pytest.optical.{uuid.uuid4().hex}"
    repository = ChangePolicyRepository()
    versions = []
    try:
        for suffix, threshold in (("a", -17), ("b", -18)):
            versions.append(
                repository.create_policy_version(
                    name=f"pytest-policy-{suffix}-{uuid.uuid4().hex}",
                    policy={
                        "observation_type": observation_type,
                        "semantic_type": "gauge",
                        "compare_mode": "threshold_and_deadband",
                        "thresholds": {"low": threshold},
                        "recovery_threshold": threshold + 2,
                    },
                    validation_report={"success": True},
                    created_by="fault-injection",
                )
            )
        repository.activate_policy_version(versions[0]["version_id"])
        repository.activate_policy_version(versions[1]["version_id"])

        active = [
            item
            for item in repository.list_active_policies()
            if item["observation_type"] == observation_type
        ]
        assert len(active) == 1
        assert active[0]["version_id"] == versions[1]["version_id"]
        with get_db_session() as db:
            first = db.get(ChangePolicyVersionRecord, versions[0]["version_id"])
            assert first.status == "deprecated"
    finally:
        policy_ids = [item["policy_id"] for item in versions]
        with get_db_session() as db:
            db.query(ChangePolicyVersionRecord).filter(
                ChangePolicyVersionRecord.policy_id.in_(policy_ids)
            ).delete(synchronize_session=False)
            db.query(ChangePolicyRecord).filter(
                ChangePolicyRecord.id.in_(policy_ids)
            ).delete(synchronize_session=False)


def test_explicit_replay_has_distinct_change_derivation_identity(postgres_available):
    del postgres_available
    run_id = f"replay-{uuid.uuid4()}"
    repository = ChangeEventRepository()

    def change(revision: str, bundle: str) -> NetworkChange:
        return NetworkChange(
            change_id=str(uuid.uuid4()),
            run_id=run_id,
            device_id="SW1",
            command="show interface status",
            entity_type="interface_l2",
            entity_key='["Gi1/0/1"]',
            field="status",
            old_value="up",
            new_value="down",
            change_type="modified",
            previous_snapshot_id="snapshot-before",
            current_snapshot_id="snapshot-after",
            detected_at="2026-07-11T01:00:00+00:00",
            processed_at="2026-07-11T01:00:00+00:00",
            analysis_revision=revision,
            rule_bundle_digest=bundle,
        )

    try:
        assert repository.save_changes([change("original", "bundle-v1")]) == 1
        assert repository.save_changes([change("replay-v2", "bundle-v2")]) == 1
        with get_db_session() as db:
            rows = (
                db.query(NetworkChangeRecord)
                .filter(NetworkChangeRecord.run_id == run_id)
                .order_by(NetworkChangeRecord.analysis_revision)
                .all()
            )
            assert len(rows) == 2
            assert {row.analysis_revision for row in rows} == {"original", "replay-v2"}
            assert {row.rule_bundle_digest for row in rows} == {
                "bundle-v1",
                "bundle-v2",
            }
    finally:
        with get_db_session() as db:
            db.query(NetworkChangeRecord).filter(
                NetworkChangeRecord.run_id == run_id
            ).delete(synchronize_session=False)


def test_change_retry_uses_canonical_deterministic_id(postgres_available):
    del postgres_available
    run_id = f"deterministic-change-{uuid.uuid4()}"
    repository = ChangeEventRepository()

    def candidate() -> NetworkChange:
        return NetworkChange(
            change_id=str(uuid.uuid4()),
            run_id=run_id,
            device_id="SW1",
            command="show interface status",
            entity_type="interface_l2",
            entity_key='["Gi1/0/1"]',
            field="status",
            old_value="up",
            new_value="down",
            change_type="modified",
            previous_snapshot_id="before",
            current_snapshot_id="after",
            detected_at="2026-07-11T01:00:00+00:00",
            analysis_revision="original",
            rule_bundle_digest="bundle-v1",
        )

    first = candidate()
    second = candidate()
    expected_id = deterministic_change_id(first)
    try:
        assert repository.save_changes([first]) == 1
        assert repository.save_changes([second]) == 0
        with get_db_session() as db:
            row = db.query(NetworkChangeRecord).filter_by(run_id=run_id).one()
            assert row.id == expected_id == deterministic_change_id(second)
    finally:
        with get_db_session() as db:
            db.query(NetworkChangeRecord).filter_by(run_id=run_id).delete()


def test_parser_outbox_claim_has_single_publisher_owner(postgres_available):
    del postgres_available
    event_id = str(uuid.uuid4())
    with get_db_session() as db:
        db.add(
            ParserOutboxEvent(
                id=event_id,
                event_type="parser.template.activated",
                aggregate_id=str(uuid.uuid4()),
                payload_json={"template_id": "template-1"},
                status="pending",
                created_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
            )
        )
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            claims = list(
                executor.map(
                    lambda _index: ParserRepository().claim_pending_outbox(limit=1),
                    range(2),
                )
            )
        claimed = [row for batch in claims for row in batch if row.id == event_id]
        assert len(claimed) == 1
        assert claimed[0].claim_token
    finally:
        with get_db_session() as db:
            db.query(ParserOutboxEvent).filter_by(id=event_id).delete()


def test_production_parser_candidate_creation_is_concurrently_deduplicated(postgres_available):
    del postgres_available
    token = uuid.uuid4().hex
    run_id = f"parser-concurrency-{token}"
    patrol = PatrolRepository()
    patrol.create_run(run_id=run_id, mode="production", source="fault-injection")
    snapshot_ids = []
    for index in range(2):
        snapshot_id, _ = patrol.save_snapshot_idempotent(
            run_id=run_id,
            device_id=f"SW{index + 1}",
            device_name=f"SW{index + 1}",
            vendor="H3C",
            model="S6860-54HF",
            command="display interface brief",
            command_canonical="display interface brief",
            raw_text=f"GE1/0/{index + 1} UP UP\n",
            parse_status="template_missing",
            execution_item_id=f"item-{index}-{token}",
            import_fingerprint=f"fingerprint-{index}-{token}",
        )
        snapshot_ids.append(snapshot_id)

    def create(item: tuple[str, str]) -> str:
        snapshot_id, device_id = item
        return ParserRepository().create_candidate(
            job_type="CREATE_TEMPLATE",
            vendor="H3C",
            model="S6860-54HF",
            family="Comware7",
            command="display interface brief",
            snapshot_id=snapshot_id,
            device_id=device_id,
            failure_code="template_missing",
        )

    job_ids: list[str] = []
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            job_ids = list(executor.map(create, zip(snapshot_ids, ["SW1", "SW2"])))
        assert len(set(job_ids)) == 1
        with get_db_session() as db:
            assert db.query(ParserJobSample).filter_by(job_id=job_ids[0]).count() == 2
    finally:
        with get_db_session() as db:
            if job_ids:
                db.query(ParserOutboxEvent).filter_by(aggregate_id=job_ids[0]).delete()
                db.query(ParserJobSample).filter_by(job_id=job_ids[0]).delete()
                db.query(ParserTemplateJob).filter_by(id=job_ids[0]).delete()
            db.query(PatrolRun).filter_by(id=run_id).delete()


def test_event_batches_acquire_correlation_locks_in_global_order(postgres_available):
    del postgres_available
    suffix = uuid.uuid4().hex

    def event(rule_id: str, correlation_key: str, change_id: str) -> NetworkEvent:
        return NetworkEvent(
            event_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{suffix}|{change_id}")),
            event_type="SharedFault",
            severity="major",
            device_id="SW1",
            entity_type="interface_l2",
            entity_key='["Gi1/0/1"]',
            source_change_id=change_id,
            run_id=None,
            occurred_at="2026-07-11T01:00:00+00:00",
            status="open",
            payload={
                "correlation_key": correlation_key,
                "rule": {"rule_id": rule_id, "version": 1, "event_class": "fault"},
            },
        )

    a1 = event("FaultA", f"A-{suffix}", f"a1-{suffix}")
    b1 = event("FaultB", f"B-{suffix}", f"b1-{suffix}")
    a2 = event("FaultA", f"A-{suffix}", f"a2-{suffix}")
    b2 = event("FaultB", f"B-{suffix}", f"b2-{suffix}")
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda batch: ChangeEventRepository().save_events(batch),
                    ([a1, b1], [b2, a2]),
                )
            )
        assert sum(results) == 2
    finally:
        with get_db_session() as db:
            db.query(NetworkEventRecord).filter(
                NetworkEventRecord.correlation_key.in_([f"A-{suffix}", f"B-{suffix}"])
            ).delete(synchronize_session=False)
