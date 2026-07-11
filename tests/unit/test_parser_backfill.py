from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.common.config import get_settings
from src.core.parser.backfill import ParserBackfillService
from src.core.parser.repository import ParserRepository
from src.core.patrol.repository import PatrolRepository
from src.infrastructure.db.models import (
    CurrentStateRecord,
    NetworkChangeDecisionRecord,
    NetworkChangeRecord,
    NetworkEventOccurrenceRecord,
    NetworkEventOutboxRecord,
    NetworkEventRecord,
    ObservationContractRecord,
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
    run_id = f"backfill-run-{token}"
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
    snapshot_id, _ = patrol.save_snapshot_idempotent(
        run_id=run_id,
        device_id="SW1",
        device_name="SW1",
        vendor="H3C",
        model=f"LAB-{token}",
        command="display interface brief",
        command_canonical="display interface brief",
        raw_text="GE1/0/1 UP UP\n",
        parse_status="template_missing",
        execution_item_id=f"item-{token}",
        import_fingerprint=f"snapshot-{token}",
    )
    service = ParserBackfillService()
    job_id = ""
    try:
        first = service.create_job(
            template_id=template_id,
            requested_by="operator",
            filters={"device_ids": ["SW1"]},
        )
        second = service.create_job(
            template_id=template_id,
            requested_by="operator",
            filters={"device_ids": ["SW1"]},
        )
        job_id = first["id"]
        assert second["id"] == job_id

        preview = service.execute_dry_run(job_id)
        assert preview["status"] == "pending_approval", preview
        assert preview["summary"]["snapshot_count"] == 1
        assert preview["summary"]["observation_count"] == 2
        assert preview["summary"]["current_state_updates"] == 0
        assert preview["summary"]["notifications"] == 0
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
        )
        assert committed["status"] == "completed"
        assert committed["summary"]["created_observation_count"] == 2
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
            db.query(SnapshotParseResult).filter_by(template_id=template_id).delete()
            db.query(StateObservationRecord).filter_by(snapshot_id=snapshot_id).delete()
            db.query(PatrolRun).filter_by(id=run_id).delete()
            db.query(ObservationContractRecord).filter_by(source_template_id=template_id).delete()
            db.query(ParserTemplate).filter_by(id=template_id).delete()
