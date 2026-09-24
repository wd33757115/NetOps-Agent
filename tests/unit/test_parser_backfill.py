from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.common.config import get_settings
from src.core.parser.backfill import ParserBackfillService, _historical_analysis_pairs
from src.core.parser.repository import ParserRepository
from src.core.patrol.repository import PatrolRepository
from src.infrastructure.db.models import (
    CommandSnapshot,
    CurrentStateRecord,
    NetworkChangeDecisionRecord,
    NetworkChangeRecord,
    NetworkEventOccurrenceRecord,
    NetworkEventOutboxRecord,
    NetworkEventRecord,
    OfflinePatrolJobRecord,
    OfflinePatrolPeriodRecord,
    ParserBackfillJobRecord,
    ParserBackfillResultRecord,
    ParserTemplate,
    PatrolRun,
    SnapshotParseResult,
    StateObservationRecord,
)
from src.infrastructure.db.postgres import get_db_session


def test_parser_backfill_is_dry_run_first_idempotent_and_silent(
    tmp_path: Path,
    monkeypatch,
):
    token = uuid.uuid4().hex
    refreshed_template_ids: list[str] = []
    monkeypatch.setattr(
        "src.core.parser.backfill._refresh_rule_asset_command_units",
        lambda template_id: refreshed_template_ids.append(template_id) or 1,
    )
    run_id = f"backfill-run-{token}"
    excluded_run_id = f"backfill-excluded-run-{token}"
    validation_run_id = f"backfill-validation-run-{token}"
    selected_dataset_id = f"backfill-selected-dataset-{token}"
    excluded_dataset_id = f"backfill-excluded-dataset-{token}"
    validation_dataset_id = f"backfill-validation-dataset-{token}"
    template_path = tmp_path / "display_interface_brief.textfsm"
    template_text = r"""Value Required INTERFACE (\S+)
Value Required STATUS (UP|DOWN)
Value Required PROTOCOL (UP|DOWN)

Start
  ^${INTERFACE}\s+${STATUS}\s+${PROTOCOL}\s*$$ -> Record
"""
    template_path.write_text(template_text, encoding="utf-8")
    template_id = ParserRepository().register_template(
        vendor="H3C",
        model=f"LAB-{token}",
        family="Comware7",
        profile=f"LAB-{token}",
        command="display interface brief",
        category="interface_l3",
        version="v1",
        asset_path=str(template_path),
        sha256=hashlib.sha256(template_path.read_bytes()).hexdigest(),
        validation={
            "entity_type": "interface_l3",
            "primary_keys": ["INTERFACE"],
            "required_fields": ["INTERFACE", "STATUS", "PROTOCOL"],
            "state_semantics": {
                "STATUS": {
                    "semantic_type": "state",
                    "observation_type": "interface.oper_status",
                },
                "PROTOCOL": {
                    "semantic_type": "state",
                    "observation_type": "interface.protocol_status",
                },
            },
            "regression_validation": {
                "passed": True,
                "sample_count": 1,
                "passed_samples": 1,
            },
        },
        status="active",
    )
    patrol = PatrolRepository()
    patrol.create_run(run_id=run_id, mode="production", source="unit-test")
    patrol.create_run(run_id=excluded_run_id, mode="production", source="unit-test")
    patrol.create_run(run_id=validation_run_id, mode="parser_validation", source="unit-test")
    snapshot_id, _ = patrol.save_snapshot_idempotent(
        run_id=run_id,
        device_id="SW1",
        device_name="SW1",
        vendor="H3C",
        model=f"LAB-{token}",
        command="display interface brief",
        command_canonical="display interface brief",
        raw_text="GE1/0/1 UP UP\n",
        parse_status="ignored",
        execution_item_id=f"item-{token}",
        import_fingerprint=f"snapshot-{token}",
    )
    excluded_snapshot_id, _ = patrol.save_snapshot_idempotent(
        run_id=excluded_run_id,
        device_id="SW1",
        device_name="SW1",
        vendor="H3C",
        model=f"LAB-{token}",
        command="display interface brief",
        command_canonical="display interface brief",
        raw_text="GE1/0/2 DOWN DOWN\n",
        parse_status="template_missing",
        execution_item_id=f"excluded-item-{token}",
        import_fingerprint=f"excluded-snapshot-{token}",
    )
    validation_snapshot_id, _ = patrol.save_snapshot_idempotent(
        run_id=validation_run_id,
        device_id="SW1",
        device_name="SW1",
        vendor="H3C",
        model=f"LAB-{token}",
        command="display interface brief",
        command_canonical="display interface brief",
        raw_text="GE1/0/3 UP UP\n",
        parse_status="template_missing",
        execution_item_id=f"validation-item-{token}",
        import_fingerprint=f"validation-snapshot-{token}",
    )
    with get_db_session() as db:
        db.get(CommandSnapshot, snapshot_id).dataset_id = selected_dataset_id
        db.get(CommandSnapshot, excluded_snapshot_id).dataset_id = excluded_dataset_id
        db.get(CommandSnapshot, validation_snapshot_id).dataset_id = validation_dataset_id
    service = ParserBackfillService()
    job_id = ""
    validation_job_id = ""
    try:
        first = service.create_job(
            template_id=template_id,
            requested_by="operator",
            filters={"device_ids": ["SW1"], "dataset_ids": [selected_dataset_id]},
        )
        second = service.create_job(
            template_id=template_id,
            requested_by="operator",
            filters={"device_ids": ["SW1"], "dataset_ids": [selected_dataset_id]},
        )
        job_id = first["id"]
        assert second["id"] == job_id

        preview = service.execute_dry_run(job_id)
        assert preview["status"] == "pending_approval", preview
        assert preview["summary"]["snapshot_count"] == 1
        assert preview["summary"]["observation_count"] == 2
        assert preview["summary"]["current_state_updates"] == 0
        assert preview["summary"]["notifications"] == 0
        rerun = service.execute_dry_run(job_id)
        assert rerun["status"] == "pending_approval"
        assert rerun["summary"] == preview["summary"]
        with get_db_session() as db:
            assert (
                db.query(StateObservationRecord)
                .filter(StateObservationRecord.snapshot_id == snapshot_id)
                .count()
                == 0
            )

        monkeypatch.setattr(get_settings(), "PARSER_BACKFILL_ENABLED", True)
        with get_db_session() as db:
            job = db.get(ParserBackfillJobRecord, job_id)
            job.status = "committing"
            job.owner_token = "other-owner"
            job.lease_until = datetime.now(timezone.utc) + timedelta(minutes=5)
        with pytest.raises(ValueError, match="already running"):
            service.commit(job_id, actor="approver", reason="duplicate approval")
        with get_db_session() as db:
            job = db.get(ParserBackfillJobRecord, job_id)
            job.status = "committing"
            job.owner_token = "interrupted-owner"
            job.lease_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        committed = service.commit(
            job_id,
            actor="approver",
            reason="validated historical repair",
            analyze_changes=True,
        )
        assert committed["status"] == "completed"
        assert committed["summary"]["created_observation_count"] == 2
        assert committed["summary"]["analysis_status"] == "completed"
        assert committed["summary"]["rule_asset_projection_refresh_count"] == 1
        assert refreshed_template_ids == [template_id]
        with get_db_session() as db:
            observations = (
                db.query(StateObservationRecord)
                .filter(StateObservationRecord.snapshot_id == snapshot_id)
                .all()
            )
            assert len(observations) == 2
            assert all(item.metadata_json["historical_backfill"] for item in observations)
            assert all(item.metadata_json["backfill_job_id"] == job_id for item in observations)
            assert (
                db.query(CurrentStateRecord)
                .filter(
                    CurrentStateRecord.source_observation_id.in_([item.id for item in observations])
                )
                .count()
                == 0
            )
            assert (
                db.query(SnapshotParseResult)
                .filter_by(
                    snapshot_id=snapshot_id,
                    template_id=template_id,
                )
                .count()
                == 1
            )
            assert (
                db.query(StateObservationRecord)
                .filter(StateObservationRecord.snapshot_id == excluded_snapshot_id)
                .count()
                == 0
            )
            job = db.get(ParserBackfillJobRecord, job_id)
            assert job.approved_by == "approver"
            assert job.approval_reason == "validated historical repair"
            changes = (
                db.query(NetworkChangeRecord)
                .filter(
                    NetworkChangeRecord.run_id == run_id,
                    NetworkChangeRecord.analysis_revision == f"historical_backfill:{job_id}",
                )
                .all()
            )
            assert len(changes) == committed["summary"]["change_count"]
            events = [
                event
                for event in db.query(NetworkEventRecord)
                .filter(NetworkEventRecord.run_id == run_id)
                .all()
                if (event.payload_json or {}).get("historical_backfill")
            ]
            assert len(events) == committed["summary"]["event_count"]
            assert all(event.status in {"historical", "resolved"} for event in events)
            assert (
                db.query(NetworkEventOutboxRecord)
                .filter(NetworkEventOutboxRecord.event_id.in_([event.id for event in events]))
                .count()
                == 0
            )

        validation_job = service.create_job(
            template_id=template_id,
            requested_by="operator",
            filters={
                "device_ids": ["SW1"],
                "dataset_ids": [validation_dataset_id],
                "run_modes": ["parser_validation"],
            },
        )
        validation_job_id = validation_job["id"]
        assert validation_job["filters_json"]["run_modes"] == ["parser_validation"]
        validation_preview = service.execute_dry_run(validation_job_id)
        assert validation_preview["status"] == "pending_approval"
        assert validation_preview["summary"]["snapshot_count"] == 1
        validation_commit = service.commit(
            validation_job_id,
            actor="approver",
            reason="",
        )
        assert validation_commit["summary"]["created_observation_count"] == 2
        assert validation_commit["summary"]["rule_asset_projection_refresh_count"] == 1
        assert refreshed_template_ids == [template_id, template_id]
        assert validation_commit["summary"]["production_run_count"] == 0
        assert validation_commit["summary"]["analysis_skipped_run_count"] == 1
        assert validation_commit["summary"]["change_count"] == 0
        assert validation_commit["summary"]["event_count"] == 0
        assert validation_commit["summary"]["analysis_status"] == "not_requested"
        with get_db_session() as db:
            assert db.get(ParserBackfillJobRecord, validation_job_id).approval_reason == ""
            validation_observations = (
                db.query(StateObservationRecord)
                .filter(StateObservationRecord.snapshot_id == validation_snapshot_id)
                .all()
            )
            assert len(validation_observations) == 2
            assert all(item.mode == "parser_validation" for item in validation_observations)
            assert db.query(NetworkChangeRecord).filter_by(run_id=validation_run_id).count() == 0
            assert db.query(NetworkEventRecord).filter_by(run_id=validation_run_id).count() == 0
    finally:
        with get_db_session() as db:
            event_ids = [
                row[0]
                for row in db.query(NetworkEventRecord.id)
                .filter(NetworkEventRecord.run_id == run_id)
                .all()
            ]
            if event_ids:
                db.query(NetworkEventOutboxRecord).filter(
                    NetworkEventOutboxRecord.event_id.in_(event_ids)
                ).delete(synchronize_session=False)
                db.query(NetworkEventOccurrenceRecord).filter(
                    NetworkEventOccurrenceRecord.event_id.in_(event_ids)
                ).delete(synchronize_session=False)
                db.query(NetworkEventRecord).filter(NetworkEventRecord.id.in_(event_ids)).delete(
                    synchronize_session=False
                )
            db.query(NetworkChangeDecisionRecord).filter_by(run_id=run_id).delete()
            db.query(NetworkChangeRecord).filter_by(run_id=run_id).delete()
            if job_id:
                db.query(ParserBackfillResultRecord).filter_by(job_id=job_id).delete()
                db.query(ParserBackfillJobRecord).filter_by(id=job_id).delete()
            if validation_job_id:
                db.query(ParserBackfillResultRecord).filter_by(job_id=validation_job_id).delete()
                db.query(ParserBackfillJobRecord).filter_by(id=validation_job_id).delete()
            db.query(SnapshotParseResult).filter_by(template_id=template_id).delete()
            db.query(StateObservationRecord).filter_by(snapshot_id=snapshot_id).delete()
            db.query(StateObservationRecord).filter_by(snapshot_id=excluded_snapshot_id).delete()
            db.query(StateObservationRecord).filter_by(snapshot_id=validation_snapshot_id).delete()
            db.query(PatrolRun).filter_by(id=run_id).delete()
            db.query(PatrolRun).filter_by(id=excluded_run_id).delete()
            db.query(PatrolRun).filter_by(id=validation_run_id).delete()
            pass
            db.query(ParserTemplate).filter_by(id=template_id).delete()


def test_parser_backfill_uses_only_the_authoritative_offline_period_run(
    tmp_path: Path,
) -> None:
    token = uuid.uuid4().hex
    dataset_id = f"backfill-authoritative-dataset-{token}"
    offline_job_id = f"offline-backfill-{token[:20]}"
    run_ids = [f"backfill-authority-{token[:16]}-{attempt}" for attempt in range(1, 4)]
    template_path = tmp_path / "show_interface_transceiver_detail.textfsm"
    template_text = r"""Value Required INTERFACE (\S+)
Value Required RX_POWER_DBM (-?\d+(?:\.\d+)?)

Start
  ^${INTERFACE}\s+${RX_POWER_DBM}\s*$$ -> Record
"""
    template_path.write_text(template_text, encoding="utf-8")
    template_id = ParserRepository().register_template(
        vendor="Cisco",
        model=f"N9K-{token[:8]}",
        family="Nexus",
        profile=f"N9K-{token[:8]}",
        command="show interface transceiver detail",
        category="interface_optical",
        version="v1",
        asset_path=str(template_path),
        sha256=hashlib.sha256(template_path.read_bytes()).hexdigest(),
        validation={
            "entity_type": "interface_optical",
            "primary_keys": ["INTERFACE"],
            "required_fields": ["INTERFACE", "RX_POWER_DBM"],
            "regression_validation": {
                "passed": True,
                "sample_count": 1,
                "passed_samples": 1,
            },
        },
        status="active",
    )
    patrol = PatrolRepository()
    snapshot_ids: list[str] = []
    try:
        for attempt, run_id in enumerate(run_ids, start=1):
            patrol.create_run(
                run_id=run_id,
                mode="parser_validation",
                source="offline_import",
            )
            snapshot_id, _ = patrol.save_snapshot_idempotent(
                run_id=run_id,
                device_id="N9K-1",
                device_name="N9K-1",
                vendor="Cisco",
                model=f"N9K-{token[:8]}",
                command="show interface transceiver detail",
                command_canonical="show interface transceiver detail",
                raw_text="Eth1/1 -7.20\n",
                parse_status="template_missing",
                execution_item_id=f"item-{token}-{attempt}",
                import_fingerprint=f"snapshot-{token}-{attempt}",
            )
            snapshot_ids.append(snapshot_id)

        now = datetime.now(timezone.utc)
        with get_db_session() as db:
            for snapshot_id in snapshot_ids:
                db.get(CommandSnapshot, snapshot_id).dataset_id = dataset_id
            db.add(
                OfflinePatrolJobRecord(
                    id=offline_job_id,
                    idempotency_key=hashlib.sha256(offline_job_id.encode()).hexdigest(),
                    purpose="rule_asset_source",
                    job_type="parser_validation",
                    name="authoritative Parser backfill fixture",
                    owner_id="pytest",
                    status="completed",
                    progress_json={"phase": "completed", "percent": 100},
                    created_at=now,
                    updated_at=now,
                    completed_at=now,
                )
            )
            db.flush()
            db.add(
                OfflinePatrolPeriodRecord(
                    id=f"period-{token[:20]}",
                    job_id=offline_job_id,
                    role="sample",
                    label="sample",
                    observed_at=now,
                    dataset_id=dataset_id,
                    patrol_run_id=run_ids[-1],
                    status="completed",
                    created_at=now,
                    updated_at=now,
                )
            )

        targets = ParserBackfillService()._target_snapshots(
            template_id,
            {
                "dataset_ids": [dataset_id],
                "run_modes": ["parser_validation"],
                "limit": 1000,
            },
        )

        assert [(snapshot.run_id, mode) for snapshot, mode in targets] == [
            (run_ids[-1], "parser_validation")
        ]
    finally:
        with get_db_session() as db:
            db.query(OfflinePatrolPeriodRecord).filter_by(job_id=offline_job_id).delete()
            db.query(OfflinePatrolJobRecord).filter_by(id=offline_job_id).delete()
            db.query(PatrolRun).filter(PatrolRun.id.in_(run_ids)).delete(
                synchronize_session=False
            )
            pass
            db.query(ParserTemplate).filter_by(id=template_id).delete()


def test_historical_analysis_pairs_require_an_explicit_offline_baseline() -> None:
    token = uuid.uuid4().hex
    production_id = f"production-{token}"
    baseline_id = f"baseline-{token}"
    current_id = f"current-{token}"
    single_id = f"single-{token}"
    patrol = PatrolRepository()
    try:
        patrol.create_run(run_id=production_id, mode="production", source="unit-test")
        patrol.create_run(run_id=baseline_id, mode="parser_validation", source="unit-test")
        patrol.create_run(run_id=current_id, mode="parser_validation", source="unit-test")
        patrol.create_run(run_id=single_id, mode="parser_validation", source="unit-test")
        with get_db_session() as db:
            baseline = db.get(PatrolRun, baseline_id)
            current = db.get(PatrolRun, current_id)
            single = db.get(PatrolRun, single_id)
            baseline.started_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
            baseline.metadata_json = {
                "offline_job_id": f"offline-pair-{token}",
                "period_role": "baseline",
            }
            current.started_at = datetime(2026, 2, 1, tzinfo=timezone.utc)
            current.metadata_json = {
                "offline_job_id": f"offline-pair-{token}",
                "period_role": "current",
            }
            single.started_at = datetime(2026, 3, 1, tzinfo=timezone.utc)
            single.metadata_json = {
                "offline_job_id": f"offline-single-{token}",
                "period_role": "current",
            }

        assert _historical_analysis_pairs(
            {production_id, baseline_id, current_id, single_id}
        ) == [
            (current_id, baseline_id),
            (production_id, None),
        ]
    finally:
        with get_db_session() as db:
            db.query(PatrolRun).filter(
                PatrolRun.id.in_([production_id, baseline_id, current_id, single_id])
            ).delete(synchronize_session=False)
