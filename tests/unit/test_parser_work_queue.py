from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.core.parser import work_queue
from src.core.parser.work_queue import ParserWorkQueueService, _age_seconds
from src.infrastructure.db.models import (
    Base,
    ParserBackfillJobRecord,
    ParserTemplateJob,
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


def _repository(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'parser-work-queue.db'}")
    Base.metadata.create_all(
        engine,
        tables=[
            ParserTemplateJob.__table__,
            ParserBackfillJobRecord.__table__,
            RuleGenerationJobRecord.__table__,
        ],
    )
    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(
        work_queue,
        "get_db_session",
        lambda: _session_scope(session_factory),
    )
    return session_factory


def test_work_queue_merges_sources_with_truthful_priority_and_pagination(monkeypatch, tmp_path):
    session_factory = _repository(monkeypatch, tmp_path)
    now = datetime.now(timezone.utc)
    with _session_scope(session_factory) as db:
        db.add_all([
            ParserTemplateJob(
                id="parser-failed",
                job_type="CREATE_TEMPLATE",
                source_mode="parser_validation",
                vendor="H3C",
                model="S5130",
                profile="h3c-s5130",
                canonical_command="display interface brief",
                output_signature="sig-1",
                idempotency_key="key-1",
                status="failed",
                sample_count=9,
                failure_code="validation_failed",
                created_at=now - timedelta(days=2),
                updated_at=now - timedelta(days=1),
            ),
            ParserTemplateJob(
                id="parser-complete",
                job_type="CREATE_TEMPLATE",
                source_mode="parser_validation",
                vendor="Cisco",
                profile="ios",
                canonical_command="show version",
                output_signature="sig-2",
                idempotency_key="key-2",
                status="succeeded",
                sample_count=1,
                created_at=now,
                updated_at=now,
            ),
            ParserBackfillJobRecord(
                id="backfill-review",
                idempotency_key="backfill-1",
                template_id="template-1",
                status="pending_approval",
                requested_by="tester",
                summary_json={"snapshot_count": 120},
                created_at=now - timedelta(days=1),
                updated_at=now,
            ),
            RuleGenerationJobRecord(
                id="rule-draft",
                job_type="RULE_ASSET_BUNDLE",
                status="draft",
                source_ref="parser_template:template-1",
                asset_type="RULE_ASSET_BUNDLE",
                payload_json={"dataset_ids": ["q1", "q2"]},
                created_at=now - timedelta(hours=1),
                updated_at=now,
            ),
        ])

    first_page = ParserWorkQueueService().list_items(limit=2, offset=0)
    second_page = ParserWorkQueueService().list_items(limit=2, offset=2)

    assert first_page["total"] == 3
    assert [item["id"] for item in first_page["items"]] == [
        "parser-failed",
        "backfill-review",
    ]
    assert first_page["items"][0]["priority"] == "high"
    assert first_page["items"][0]["blocker"] == "validation_failed"
    assert first_page["items"][1]["scope"] == "120 个快照"
    assert [item["id"] for item in second_page["items"]] == ["rule-draft"]


def test_work_queue_search_and_type_filters_are_applied_before_count(monkeypatch, tmp_path):
    session_factory = _repository(monkeypatch, tmp_path)
    with _session_scope(session_factory) as db:
        db.add_all([
            ParserTemplateJob(
                id="parser-interface",
                job_type="CREATE_TEMPLATE",
                source_mode="parser_validation",
                vendor="Huawei",
                profile="vrp",
                canonical_command="display interface brief",
                output_signature="sig-3",
                idempotency_key="key-3",
                status="pending",
                sample_count=2,
            ),
            ParserTemplateJob(
                id="parser-version",
                job_type="CREATE_TEMPLATE",
                source_mode="parser_validation",
                vendor="Huawei",
                profile="vrp",
                canonical_command="display version",
                output_signature="sig-4",
                idempotency_key="key-4",
                status="pending",
                sample_count=2,
            ),
        ])

    result = ParserWorkQueueService().list_items(
        task_type="parser",
        search="interface",
        limit=20,
    )

    assert result["total"] == 1
    assert result["items"][0]["id"] == "parser-interface"


def test_work_queue_excludes_terminal_rejected_rule_jobs(monkeypatch, tmp_path):
    session_factory = _repository(monkeypatch, tmp_path)
    with _session_scope(session_factory) as db:
        db.add_all([
            RuleGenerationJobRecord(
                id="rule-rejected",
                job_type="EVENT_RULE",
                status="rejected",
                source_ref="parser_template:template-old",
            ),
            RuleGenerationJobRecord(
                id="rule-draft",
                job_type="EVENT_RULE",
                status="draft",
                source_ref="parser_template:template-current",
            ),
        ])

    result = ParserWorkQueueService().list_items(task_type="rule_generation", limit=20)

    assert result["total"] == 1
    assert result["items"][0]["id"] == "rule-draft"


def test_work_queue_age_uses_database_clock_for_naive_timestamps():
    now_utc = datetime(2026, 7, 19, 13, 30, tzinfo=timezone.utc)
    now_local = datetime(2026, 7, 19, 21, 30)

    assert _age_seconds(
        datetime(2026, 7, 19, 20, 45),
        now_utc=now_utc,
        now_local=now_local,
    ) == 45 * 60
