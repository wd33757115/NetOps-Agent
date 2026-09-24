from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.apps.rule_governance.backend import source_preparation
from src.infrastructure.db.models import Base, CommandSnapshot, OfflinePatrolJobRecord, OfflinePatrolPeriodRecord


def test_prepares_all_split_outputs_without_modifying_original_evidence(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[CommandSnapshot.__table__, OfflinePatrolJobRecord.__table__, OfflinePatrolPeriodRecord.__table__])
    with Session(engine) as db:
        @contextmanager
        def session_scope():
            yield db
            db.commit()
        monkeypatch.setattr(source_preparation, "get_db_session", session_scope)
        db.add(OfflinePatrolJobRecord(id="source", idempotency_key="source", purpose="rule_asset_source",
            job_type="parser_validation", name="test", owner_id="test", status="running", execution_idempotency_key="execution"))
        db.add(OfflinePatrolPeriodRecord(id="period", job_id="source", role="sample", label="sample",
            observed_at=datetime.now(timezone.utc), dataset_id="dataset", patrol_run_id="run", status="completed"))
        raw = "username operator password secret\nAddress 10.1.2.3\n"
        for i, status in enumerate(["parsed", "unparsed", "raw"]):
            db.add(CommandSnapshot(id=str(i), run_id="run", device_id="device", device_name="device",
                execution_item_id=str(i), command=f"show {i}", canonical_command=f"show {i}",
                observed_at=datetime.now(timezone.utc), raw_text=raw, raw_text_hash="original", parse_status=status))
        db.commit()
        result = source_preparation.prepare_source_samples("source", "execution")
        assert result["sample_count"] == 3
        for snapshot in db.query(CommandSnapshot).all():
            assert snapshot.raw_text == raw
            prepared = snapshot.metadata_json["rule_source_preparation"]
            assert prepared["status"] == "sanitized"
            assert "secret" not in prepared["text"]
            assert "10.1.2.3" not in prepared["text"]
        assert source_preparation.prepare_source_samples("source", "execution") == result
        with pytest.raises(ValueError, match="状态已变化"):
            source_preparation.prepare_source_samples("source", "stale-execution")
    engine.dispose()
