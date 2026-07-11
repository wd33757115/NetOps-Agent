from __future__ import annotations

import uuid

from src.core.parser.repository import ParserRepository
from src.core.patrol.repository import PatrolRepository
from src.infrastructure.db.models import (
    ParserJobSample,
    ParserOutboxEvent,
    ParserTemplateJob,
    PatrolRun,
)
from src.infrastructure.db.postgres import get_db_session


def test_production_parser_backlog_deduplicates_and_requires_sanitization():
    token = uuid.uuid4().hex
    run_id = f"parser-production-{token}"
    patrol = PatrolRepository()
    parser = ParserRepository()
    patrol.create_run(run_id=run_id, mode="production", source="unit-test")
    snapshot_ids: list[str] = []
    job_id = ""
    try:
        for index in range(2):
            snapshot_id, created = patrol.save_snapshot_idempotent(
                run_id=run_id,
                device_id=f"SW{index + 1}",
                device_name=f"SW{index + 1}",
                vendor="H3C",
                model="S6860-54HF",
                command="display interface brief",
                command_canonical="display interface brief",
                raw_text=(
                    f"Username: admin\nPassword: secret-{index}\n"
                    f"Management IP: 10.0.0.{index + 1}\n"
                    "Interface Link Protocol\nGE1/0/1 UP UP\n"
                ),
                parse_status="template_missing",
                execution_item_id=f"item-{index}-{token}",
                import_fingerprint=f"fingerprint-{index}-{token}",
            )
            assert created is True
            snapshot_ids.append(snapshot_id)
            current_job_id = parser.create_candidate(
                job_type="CREATE_TEMPLATE",
                vendor="H3C",
                model="S6860-54HF",
                family="Comware7",
                command="display interface brief",
                snapshot_id=snapshot_id,
                device_id=f"SW{index + 1}",
                failure_code="template_missing",
            )
            job_id = job_id or current_job_id
            assert current_job_id == job_id

        job = parser.get_job(job_id, include_sanitized_payload=True)
        assert job is not None
        assert job["source_mode"] == "production"
        assert job["sample_count"] == 2
        assert {sample["sanitization_status"] for sample in job["samples"]} == {
            "pending_sanitization"
        }
        assert parser.list_dispatchable_jobs(job_id=job_id) == []

        sanitized = parser.sanitize_pending_samples(job_id=job_id)
        assert len(sanitized) == 2
        assert all(item["success"] for item in sanitized)
        job = parser.get_job(job_id, include_sanitized_payload=True)
        assert job is not None
        for sample in job["samples"]:
            text = sample["sanitized_text"]
            assert "secret-" not in text
            assert "10.0.0." not in text
            assert "GE1/0/1 UP UP" in text
        assert [item["id"] for item in parser.list_dispatchable_jobs(job_id=job_id)] == [job_id]
    finally:
        with get_db_session() as db:
            if job_id:
                db.query(ParserOutboxEvent).filter(ParserOutboxEvent.aggregate_id == job_id).delete(
                    synchronize_session=False
                )
                db.query(ParserJobSample).filter(ParserJobSample.job_id == job_id).delete(
                    synchronize_session=False
                )
                db.query(ParserTemplateJob).filter(ParserTemplateJob.id == job_id).delete(
                    synchronize_session=False
                )
            db.query(PatrolRun).filter(PatrolRun.id == run_id).delete(synchronize_session=False)
