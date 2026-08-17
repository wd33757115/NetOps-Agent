from __future__ import annotations

import hashlib
import uuid
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.core.check_items.current_authority import (
    COMPLETENESS_EVIDENCE_OPTIONS,
    OBSERVED_AT_EVIDENCE_OPTIONS,
    CurrentAuthorityError,
    PatrolCurrentAuthorityService,
)
from src.infrastructure.db.models import (
    CommandSnapshot,
    FileMetadata,
    NetworkDevice,
    OfflinePatrolDeviceBindingRecord,
    OfflinePatrolJobRecord,
    OfflinePatrolPeriodRecord,
    OfflinePatrolSourceFileRecord,
    PatrolBatchDeviceRecord,
    PatrolBatchRecord,
    PatrolCollectionResult,
    PatrolCurrentAuthorityRequestRecord,
    PatrolRun,
)
from src.infrastructure.db.postgres import SessionLocal


def _service_fixture(
    *,
    report_intent: str = "current_attestation",
    production_readiness=None,
):
    db = SessionLocal()
    transaction = db.begin()
    suffix = uuid.uuid4().hex
    now = datetime.now(timezone.utc).replace(microsecond=0)
    job_id = f"offline-authority-{suffix[:20]}"
    device_id = f"authority-device-{suffix[:20]}"
    run_id = f"authority-run-{suffix[:20]}"
    file_id = f"authority-file-{suffix[:20]}"
    source_id = f"authority-source-{suffix[:20]}"
    raw_text = "Slot 0 CPU usage: 12%\n"
    raw_hash = hashlib.sha256(raw_text.encode()).hexdigest()
    db.add_all(
        [
            NetworkDevice(
                id=device_id,
                device_name=f"AUTH-SW-{suffix[:8]}",
                management_ip=f"192.0.2.{int(suffix[:2], 16) % 200 + 1}",
                vendor="H3C",
                model="S5130S-54S-HI-G",
                family="Comware7",
                status="active",
                attributes_json={"role": "access"},
                created_at=now,
                updated_at=now,
            ),
            FileMetadata(
                id=file_id,
                name="current.txt",
                object_key=f"tests/current-authority/{file_id}.txt",
                owner_id="importer-principal",
                visibility="private",
                size_bytes=len(raw_text.encode()),
                status="active",
                created_by="importer-principal",
                created_at=now,
                updated_at=now,
                is_deleted=False,
            ),
            OfflinePatrolJobRecord(
                id=job_id,
                idempotency_key=hashlib.sha256(job_id.encode()).hexdigest(),
                purpose="offline_patrol",
                job_type="single_patrol",
                report_intent=report_intent,
                name="current authority fixture",
                owner_id="importer-principal",
                status="completed",
                publish_events=False,
                progress_json={"phase": "completed", "percent": 100},
                result_json={
                    "device_count": 1,
                    "failed_device_count": 0,
                    "command_count": 1,
                },
                archived=False,
                created_at=now,
                updated_at=now,
                completed_at=now,
            ),
            PatrolRun(
                id=run_id,
                mode="production",
                source="offline_import",
                status="completed",
                collection_status="completed",
                parser_readiness_status="ready",
                fact_readiness_status="ready",
                analysis_status="completed",
                baseline_eligible=False,
                run_purpose="offline_patrol",
                logical_run_id=run_id,
                attempt_number=0,
                device_count=1,
                command_count=1,
                started_at=now,
                completed_at=now,
                metadata_json={
                    "offline_job_id": job_id,
                    "period_role": "current",
                },
                created_at=now,
                updated_at=now,
            ),
        ]
    )
    db.flush()
    db.add_all(
        [
            OfflinePatrolPeriodRecord(
                id=f"period-{suffix[:20]}",
                job_id=job_id,
                role="current",
                label="current",
                observed_at=now,
                dataset_id=f"dataset-{suffix}",
                patrol_run_id=run_id,
                status="completed",
                result_json={"snapshot_count": 1},
                created_at=now,
                updated_at=now,
            ),
            OfflinePatrolSourceFileRecord(
                id=source_id,
                job_id=job_id,
                period_role="current",
                file_id=file_id,
                relative_path="current.txt",
                sha256=raw_hash,
                size_bytes=len(raw_text.encode()),
                status="verified",
                created_at=now,
                updated_at=now,
            ),
            OfflinePatrolDeviceBindingRecord(
                id=f"binding-{suffix[:20]}",
                job_id=job_id,
                period_role="current",
                external_device_id=f"AUTH-SW-{suffix[:8]}",
                device_name=f"AUTH-SW-{suffix[:8]}",
                management_ip=f"192.0.2.{int(suffix[:2], 16) % 200 + 1}",
                inventory_device_id=device_id,
                match_status="confirmed",
                confidence=1.0,
                match_reason="test_fixture",
                confirmed_by="importer-principal",
                confirmed_at=now,
                created_at=now,
                updated_at=now,
            ),
            CommandSnapshot(
                id=f"snapshot-{suffix[:20]}",
                run_id=run_id,
                inventory_device_id=device_id,
                device_id=f"AUTH-SW-{suffix[:8]}",
                execution_item_id=f"execution-item-{suffix[:20]}",
                check_item_ids_json=[],
                device_name=f"AUTH-SW-{suffix[:8]}",
                management_ip=f"192.0.2.{int(suffix[:2], 16) % 200 + 1}",
                vendor="H3C",
                model="S5130S-54S-HI-G",
                command="display cpu-usage",
                canonical_command="display cpu-usage",
                observed_at=now,
                structured_json=[{"slot": "0", "cpu_usage": "12"}],
                raw_text=raw_text,
                raw_text_hash=raw_hash,
                parse_status="parsed",
                output_status="valid",
                snapshot_kind="full",
                source_file_hash=raw_hash,
                created_at=now,
            ),
        ]
    )
    db.flush()
    service = PatrolCurrentAuthorityService(
        session_scope=lambda: nullcontext(db),
        now=lambda: now + timedelta(minutes=1),
        production_readiness=(
            production_readiness
            or (lambda: {
                "status": "ready",
                "can_launch": True,
                "blockers": [],
            })
        ),
    )
    return db, transaction, service, job_id


def _prepare(service: PatrolCurrentAuthorityService, job_id: str, **overrides):
    values = {
        "offline_job_id": job_id,
        "requested_by_principal_id": "importer-principal",
        "requested_by_display": "Importer",
        "idempotency_key": "prepare-authority-key",
        "completeness_statement": (
            "All selected devices, successful outputs and failures are "
            "accounted for in the frozen ledgers."
        ),
        "completeness_evidence_refs": list(
            COMPLETENESS_EVIDENCE_OPTIONS
        ),
        "observed_at_evidence_refs": list(
            OBSERVED_AT_EVIDENCE_OPTIONS
        ),
        "collection_tool": "netops-offline-collector/2.0",
        "collector_identity": "field-engineer-a",
    }
    values.update(overrides)
    return service.prepare(**values)


def test_prepare_maps_catalog_command_and_is_idempotent() -> None:
    db, transaction, service, job_id = _service_fixture()
    try:
        created = _prepare(service, job_id)
        replay = _prepare(service, job_id)

        assert created["status"] == "ready_for_attestation"
        assert created["selected_check_item_count"] == 1
        assert created["candidate_batch_id"]
        assert created["idempotent_replay"] is False
        assert replay["request_id"] == created["request_id"]
        assert replay["idempotent_replay"] is True
        assert (
            db.get(PatrolBatchRecord, created["candidate_batch_id"])
            .dispatch_status
            == "awaiting_source_qualification"
        )
    finally:
        transaction.rollback()
        db.close()


def test_prepare_rejects_historical_intent_and_idempotency_conflict() -> None:
    db, transaction, service, job_id = _service_fixture()
    try:
        _prepare(service, job_id)
        with pytest.raises(CurrentAuthorityError) as conflict:
            _prepare(
                service,
                job_id,
                completeness_statement="A different frozen proof statement.",
            )
        assert conflict.value.code == "idempotency_conflict"
    finally:
        transaction.rollback()
        db.close()

    db, transaction, service, job_id = _service_fixture(
        report_intent="historical"
    )
    try:
        with pytest.raises(CurrentAuthorityError) as historical:
            _prepare(service, job_id)
        assert historical.value.code == "historical_intent_only"
    finally:
        transaction.rollback()
        db.close()


def test_prepare_persists_structured_evidence_blockers() -> None:
    db, transaction, service, job_id = _service_fixture()
    try:
        result = _prepare(
            service,
            job_id,
            completeness_evidence_refs=["source_manifest"],
            observed_at_evidence_refs=["user_supplied_timestamp"],
        )
        assert result["status"] == "blocked"
        assert result["candidate_batch_id"] is None
        assert set(result["blocker_codes"]) == {
            "completeness_evidence_incomplete",
            "observed_at_evidence_invalid",
        }
    finally:
        transaction.rollback()
        db.close()


def test_prepare_reports_identity_root_cause_with_remediation() -> None:
    db, transaction, service, job_id = _service_fixture()
    try:
        binding = (
            db.query(OfflinePatrolDeviceBindingRecord)
            .filter(OfflinePatrolDeviceBindingRecord.job_id == job_id)
            .one()
        )
        binding.inventory_device_id = None
        binding.match_status = "unmatched"
        binding.confirmed_by = None
        binding.confirmed_at = None
        db.flush()

        result = _prepare(service, job_id)

        assert result["status"] == "blocked"
        assert "identity_binding_unconfirmed" in result["blocker_codes"]
        assert "scope_check_items_missing" not in result["blocker_codes"]
        blocker = next(
            item
            for item in result["blockers"]
            if item["code"] == "identity_binding_unconfirmed"
        )
        assert blocker["remediation"] == {
            "code": "confirm_inventory_binding",
            "label": "绑定 Inventory 设备",
            "target": "sources",
        }
    finally:
        transaction.rollback()
        db.close()


def test_prepare_rejects_collection_failure_without_observed_at() -> None:
    db, transaction, service, job_id = _service_fixture()
    try:
        run = (
            db.query(PatrolRun)
            .filter(
                PatrolRun.metadata_json["offline_job_id"].as_string()
                == job_id
            )
            .one()
        )
        snapshot = (
            db.query(CommandSnapshot)
            .filter(CommandSnapshot.run_id == run.id)
            .one()
        )
        db.add(
            PatrolCollectionResult(
                id=f"collection-{uuid.uuid4().hex[:20]}",
                run_id=run.id,
                inventory_device_id=snapshot.inventory_device_id,
                device_id=snapshot.device_id,
                execution_item_id=f"failed-{uuid.uuid4().hex[:20]}",
                canonical_command="display cpu-usage",
                status="collection_failed",
                execution_status="failed",
                parse_status="not_attempted",
                output_status="missing",
                fact_status="blocked_by_collection",
                normalization_status="blocked",
                baseline_eligible=False,
                failure_code="connection_failed",
                observed_at=None,
            )
        )
        db.flush()

        result = _prepare(service, job_id)

        assert result["status"] == "blocked"
        assert result["candidate_batch_id"] is None
        assert "observed_at_untrusted" in result["blocker_codes"]
    finally:
        transaction.rollback()
        db.close()


def test_independent_attestation_and_revocation_are_governed() -> None:
    db, transaction, service, job_id = _service_fixture()
    try:
        prepared = _prepare(service, job_id)
        with pytest.raises(CurrentAuthorityError) as same_actor:
            service.attest(
                offline_job_id=job_id,
                request_digest=prepared["request_digest"],
                attested_by_principal_id="importer-principal",
                attested_by_display="Same person with another label",
                idempotency_key="attest-same-actor-key",
                reason="I reviewed my own import.",
            )
        assert same_actor.value.code == "same_actor_forbidden"

        attested = service.attest(
            offline_job_id=job_id,
            request_digest=prepared["request_digest"],
            attested_by_principal_id="independent-admin",
            attested_by_display="Independent Admin",
            idempotency_key="attest-independent-key",
            reason="The frozen files, identities, scope and ledgers agree.",
        )
        assert attested["success"] is True
        assert attested["status"] == "approved"
        batch = db.get(PatrolBatchRecord, attested["candidate_batch_id"])
        assert batch.source_qualification_id == attested["qualification_id"]
        assert batch.dispatch_status == "awaiting_current_plan"
        target_before_planning = (
            db.query(PatrolBatchDeviceRecord)
            .filter(
                PatrolBatchDeviceRecord.patrol_batch_id
                == attested["candidate_batch_id"]
            )
            .one()
        )
        assert target_before_planning.preflight_status == (
            "awaiting_current_plan"
        )
        assert target_before_planning.dispatch_status == (
            "awaiting_current_plan"
        )

        with pytest.raises(CurrentAuthorityError) as unavailable:
            service.prepare_dispatch(attested["request_id"])

        assert unavailable.value.code == "authority_production_plan_incomplete"
        assert batch.dispatch_status == "failed"
        assert batch.runtime_manifest_id
        assert batch.runtime_manifest_digest
        assert "authority_production_plan_incomplete" in str(
            batch.dispatch_error
        )
        target = (
            db.query(PatrolBatchDeviceRecord)
            .filter(
                PatrolBatchDeviceRecord.patrol_batch_id
                == attested["candidate_batch_id"]
            )
            .one()
        )
        assert target.selection_status == "selected"
        assert target.preflight_status == "failed"
        assert target.dispatch_status == "failed"
        assert target.failure_code == "authority_production_plan_incomplete"
        assert target.workflow_run_id is None

        source_job = db.get(OfflinePatrolJobRecord, job_id)
        source_run = (
            db.query(PatrolRun)
            .filter(
                PatrolRun.metadata_json["offline_job_id"].as_string()
                == job_id
            )
            .one()
        )
        assert source_job.status == "completed"
        assert source_run.source == "offline_import"
        assert source_run.run_purpose == "offline_patrol"

        revoked = service.revoke(
            offline_job_id=job_id,
            revoked_by_principal_id="security-admin",
            revoked_by_display="Security Admin",
            idempotency_key="revoke-authority-key",
            reason="The source chain of custody must be reviewed again.",
        )
        assert revoked["success"] is False
        assert revoked["status"] == "revoked"
        assert batch.dispatch_status == "revoked"
    finally:
        transaction.rollback()
        db.close()


def test_attestation_rejects_snapshot_content_changed_after_prepare() -> None:
    db, transaction, service, job_id = _service_fixture()
    try:
        prepared = _prepare(service, job_id)
        snapshot = (
            db.query(CommandSnapshot)
            .filter(CommandSnapshot.run_id.like("authority-run-%"))
            .one()
        )
        snapshot.raw_text = "Slot 0 CPU usage: 99%\n"
        db.flush()

        with pytest.raises(CurrentAuthorityError) as changed:
            service.attest(
                offline_job_id=job_id,
                request_digest=prepared["request_digest"],
                attested_by_principal_id="independent-admin",
                attested_by_display="Independent Admin",
                idempotency_key="attest-tampered-content-key",
                reason="The frozen evidence was independently reviewed.",
            )

        assert changed.value.code == "snapshot_content_hash_mismatch"
    finally:
        transaction.rollback()
        db.close()


def test_prepare_dispatch_revalidates_attested_request_before_planning() -> None:
    db, transaction, service, job_id = _service_fixture()
    try:
        prepared = _prepare(service, job_id)
        attested = service.attest(
            offline_job_id=job_id,
            request_digest=prepared["request_digest"],
            attested_by_principal_id="independent-admin",
            attested_by_display="Independent Admin",
            idempotency_key="attest-before-planning-drift",
            reason="The frozen evidence was independently reviewed.",
        )
        request = db.get(
            PatrolCurrentAuthorityRequestRecord,
            attested["request_id"],
        )
        changed = dict(request.request_json or {})
        changed_proof = dict(changed.get("proof") or {})
        changed_proof["collector_identity"] = "tampered-after-attestation"
        changed["proof"] = changed_proof
        request.request_json = changed
        db.flush()

        with pytest.raises(CurrentAuthorityError) as drift:
            service.prepare_dispatch(attested["request_id"])

        assert drift.value.code == "authority_digest_mismatch"
        assert request.status == "failed"
        batch = db.get(PatrolBatchRecord, attested["candidate_batch_id"])
        assert batch.dispatch_status == "failed"
        assert batch.runtime_manifest_id is None
        assert "authority_digest_mismatch" in str(batch.dispatch_error)
    finally:
        transaction.rollback()
        db.close()


def test_prepare_dispatch_defers_when_patrol_platform_is_not_ready() -> None:
    db, transaction, service, job_id = _service_fixture(
        production_readiness=lambda: {
            "status": "blocked",
            "can_launch": False,
            "checked_at": "2026-08-01T12:00:00+00:00",
            "blockers": [
                {
                    "failure_code": "patrol_celery_beat_not_ready",
                    "message": "Celery Beat 心跳状态为 stale",
                    "remediation": "启动 Celery Beat",
                }
            ],
        }
    )
    try:
        prepared = _prepare(service, job_id)
        attested = service.attest(
            offline_job_id=job_id,
            request_digest=prepared["request_digest"],
            attested_by_principal_id="reviewer-principal",
            attested_by_display="Reviewer",
            idempotency_key="attest-platform-not-ready",
            reason="The frozen evidence and scope were independently reviewed.",
        )

        with pytest.raises(CurrentAuthorityError) as blocked:
            service.prepare_dispatch(attested["request_id"])

        assert blocked.value.code == "patrol_platform_not_ready"
        assert blocked.value.http_status == 503
        assert blocked.value.details["blockers"][0]["failure_code"] == (
            "patrol_celery_beat_not_ready"
        )
        request = db.get(
            PatrolCurrentAuthorityRequestRecord,
            attested["request_id"],
        )
        batch = db.get(PatrolBatchRecord, attested["candidate_batch_id"])
        target = (
            db.query(PatrolBatchDeviceRecord)
            .filter(
                PatrolBatchDeviceRecord.patrol_batch_id
                == attested["candidate_batch_id"]
            )
            .one()
        )
        assert request.status == "approved"
        assert batch.dispatch_status == "awaiting_current_plan"
        assert batch.launched_count == 0
        assert batch.runtime_manifest_id is None
        assert "patrol_platform_not_ready" in str(batch.dispatch_error)
        assert target.selection_status == "selected"
        assert target.preflight_status == "awaiting_current_plan"
        assert target.dispatch_status == "awaiting_current_plan"
        assert target.failure_code is None
        assert target.workflow_run_id is None
    finally:
        transaction.rollback()
        db.close()


def test_prepare_dispatch_fences_fresh_planning_redelivery() -> None:
    db, transaction, service, job_id = _service_fixture()
    try:
        prepared = _prepare(service, job_id)
        attested = service.attest(
            offline_job_id=job_id,
            request_digest=prepared["request_digest"],
            attested_by_principal_id="independent-admin",
            attested_by_display="Independent Admin",
            idempotency_key="attest-before-planning-redelivery",
            reason="The frozen evidence was independently reviewed.",
        )
        request = db.get(
            PatrolCurrentAuthorityRequestRecord,
            attested["request_id"],
        )
        request.status = "attesting"
        request.updated_at = datetime.now(timezone.utc)
        db.flush()

        replay = service.prepare_dispatch(attested["request_id"])

        assert replay["status"] == "attesting"
        assert replay["planning_in_progress"] is True
        batch = db.get(PatrolBatchRecord, attested["candidate_batch_id"])
        assert batch.runtime_manifest_id is None
        assert batch.dispatch_status == "awaiting_current_plan"
    finally:
        transaction.rollback()
        db.close()


def test_dispatch_plan_allows_governed_not_applicable_without_collection() -> None:
    frozen = SimpleNamespace(
        decisions=(
            {
                "device_id": "device-1",
                "check_item_id": "interface.optical_power",
                "status": "not_applicable",
                "reason_codes": ["capability_explicitly_absent"],
                "collection_action_ids": [],
            },
        ),
        collection_actions=(),
        manifest={"asset_gap_ids": []},
    )

    PatrolCurrentAuthorityService._validate_frozen_dispatch_plan(frozen)


def test_dispatch_plan_allows_partial_execution_with_terminal_asset_gap() -> None:
    frozen = SimpleNamespace(
        decisions=(
            {
                "device_id": "device-1",
                "check_item_id": "system.cpu_usage",
                "status": "resolved",
                "reason_codes": [],
                "collection_action_ids": ["action-1"],
            },
            {
                "device_id": "device-2",
                "check_item_id": "interface.optical_power",
                "status": "blocked_asset_missing",
                "reason_codes": ["active_runtime_bundle_missing"],
                "collection_action_ids": [],
            },
        ),
        collection_actions=({"id": "action-1", "device_id": "device-1"},),
        manifest={"asset_gap_ids": ["gap-1"]},
    )

    PatrolCurrentAuthorityService._validate_frozen_dispatch_plan(frozen)


def test_dispatch_plan_rejects_resolved_target_without_collection_action() -> None:
    frozen = SimpleNamespace(
        decisions=(
            {
                "device_id": "device-1",
                "check_item_id": "system.cpu_usage",
                "status": "resolved",
                "reason_codes": [],
                "collection_action_ids": [],
            },
        ),
        collection_actions=(),
        manifest={"asset_gap_ids": []},
    )

    with pytest.raises(CurrentAuthorityError) as incomplete:
        PatrolCurrentAuthorityService._validate_frozen_dispatch_plan(frozen)

    assert incomplete.value.code == "authority_production_plan_incomplete"
