"""Parser 生产缺口台账真实断言单测（SQLite fixture）。

覆盖 upsert 计数、阈值提升、production_impact 快速通道与幂等复用。
"""

from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.core.parser import gap_ledger, production_impact
from src.core.parser.gap_ledger import ParserGapLedgerService
from src.infrastructure.db.models import Base, ParserGapLedgerRecord, ParserTemplateJob


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


def _fixture(monkeypatch, tmp_path, *, impact_scores=None):
    engine = create_engine(f"sqlite:///{tmp_path / 'gap-ledger.db'}")
    Base.metadata.create_all(
        engine,
        tables=[ParserGapLedgerRecord.__table__, ParserTemplateJob.__table__],
    )
    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(
        gap_ledger,
        "get_db_session",
        lambda: _session_scope(session_factory),
    )
    scores = impact_scores or {}
    monkeypatch.setattr(
        production_impact,
        "production_impact_for_pairs",
        lambda pairs, **kwargs: {pair: scores.get(pair, 0) for pair in pairs},
    )
    return session_factory


def test_record_gap_upserts_occurrence_count(monkeypatch, tmp_path) -> None:
    session_factory = _fixture(monkeypatch, tmp_path)
    service = ParserGapLedgerService()

    first = service.record_gap(
        vendor="huawei",
        canonical_command="display power",
        failure_code="template_missing",
        run_id="run-1",
        device_id="dev-1",
    )
    assert first is not None
    assert first["occurrence_count"] == 1

    second = service.record_gap(
        vendor="huawei",
        canonical_command="display power",
        failure_code="template_missing",
        run_id="run-2",
        device_id="dev-2",
    )
    assert second["id"] == first["id"]
    assert second["occurrence_count"] == 2
    assert second["last_run_id"] == "run-2"

    with _session_scope(session_factory) as db:
        assert db.query(ParserGapLedgerRecord).count() == 1


def test_record_gap_ignores_blank_vendor_or_command(monkeypatch, tmp_path) -> None:
    _fixture(monkeypatch, tmp_path)
    service = ParserGapLedgerService()
    assert service.record_gap(vendor="", canonical_command="x", failure_code="f") is None
    assert service.record_gap(vendor="huawei", canonical_command=" ", failure_code="f") is None


def test_promote_respects_occurrence_threshold(monkeypatch, tmp_path) -> None:
    session_factory = _fixture(monkeypatch, tmp_path)
    service = ParserGapLedgerService()
    # 默认阈值为 3：出现 2 次且无生产影响时不应提升
    for _ in range(2):
        service.record_gap(
            vendor="h3c",
            canonical_command="display arp",
            failure_code="template_missing",
        )
    result = service.promote_to_draft_jobs()
    assert result["created_count"] == 0

    service.record_gap(
        vendor="h3c",
        canonical_command="display arp",
        failure_code="template_missing",
    )
    result = service.promote_to_draft_jobs()
    assert result["created_count"] == 1
    with _session_scope(session_factory) as db:
        job = db.query(ParserTemplateJob).one()
        assert job.job_type == "CREATE_TEMPLATE"
        assert job.source_mode == "production_gap"
        ledger = db.query(ParserGapLedgerRecord).one()
        assert ledger.draft_job_id == job.id

    # 已挂 draft_job_id 后再次提升不应重复建 Job
    result = service.promote_to_draft_jobs()
    assert result["created_count"] == 0


def test_promote_fast_tracks_high_impact_gap(monkeypatch, tmp_path) -> None:
    session_factory = _fixture(
        monkeypatch,
        tmp_path,
        impact_scores={("huawei", "display version"): 999},
    )
    service = ParserGapLedgerService()
    # 核心命令：出现 1 次但 production_impact 超过快速通道阈值，应立即提升
    service.record_gap(
        vendor="huawei",
        canonical_command="display version",
        failure_code="template_missing",
    )
    result = service.promote_to_draft_jobs()
    assert result["created_count"] == 1
    with _session_scope(session_factory) as db:
        job = db.query(ParserTemplateJob).one()
        assert "production_impact=999" in (job.last_error or "")


def test_promote_reuses_existing_job_via_idempotency_key(monkeypatch, tmp_path) -> None:
    from datetime import datetime, timezone

    session_factory = _fixture(monkeypatch, tmp_path)
    service = ParserGapLedgerService()
    for _ in range(3):
        service.record_gap(
            vendor="cisco",
            canonical_command="show inventory",
            failure_code="parse_failed",
        )
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with _session_scope(session_factory) as db:
        db.add(
            ParserTemplateJob(
                id="existing-job",
                job_type="REPAIR_TEMPLATE",
                source_mode="production_gap",
                vendor="cisco",
                profile="unknown",
                canonical_command="show inventory",
                output_signature="gap:parse_failed",
                idempotency_key="production_gap|cisco|show inventory|parse_failed",
                status="pending",
                sample_count=0,
                created_at=now,
                updated_at=now,
            )
        )
    result = service.promote_to_draft_jobs()
    assert result["created_count"] == 0
    assert result["skipped_job_ids"] == ["existing-job"]
    with _session_scope(session_factory) as db:
        ledger = db.query(ParserGapLedgerRecord).one()
        assert ledger.draft_job_id == "existing-job"
