from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.apps.rule_governance.backend.parser import check_item_binding as binding
from src.apps.rule_governance.backend.parser import repository
from src.infrastructure.db.models import (
    Base, OfflinePatrolJobRecord, RuleAssetCommandUnitRecord,
    ParserTemplateJob, ParserJobSample, AuditLogRecord,
)


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[OfflinePatrolJobRecord.__table__, RuleAssetCommandUnitRecord.__table__,
        ParserTemplateJob.__table__, ParserJobSample.__table__, AuditLogRecord.__table__])
    item = SimpleNamespace(id="interface.state", version_id="version-1", digest="digest-1", result_mode="structured")
    monkeypatch.setattr(binding.CheckItemCatalogRepository, "load_active", lambda self: SimpleNamespace(check_items=[item]))
    with Session(engine) as session:
        yield session, item
    engine.dispose()


def associate(db, *, version="version-1", item_id="interface.state", source_status="completed"):
    source = OfflinePatrolJobRecord(id="source", idempotency_key="source", purpose="rule_asset_source",
        job_type="parser_validation", name="test", owner_id="test", status=source_status,
        preview_json={"preproduction_check_item_associations": [{"decision": "pending_check_item_association",
            "asset_key": "asset", "check_item_id": item_id, "check_item_version_id": version}]})
    unit = RuleAssetCommandUnitRecord(id="unit", offline_job_id="source", asset_key="asset", vendor="test",
        profile="test", canonical_command="show interface", handling="structured", asset_mode="structured",
        status="pending", current_stage="parser", parser_job_id="parser")
    db.add_all([source, unit]); db.flush()
    return source, unit


def test_requires_explicit_association_even_for_direct_parser_link(db):
    session, _ = db
    with pytest.raises(binding.CheckItemBindingError, match="先在待归类"):
        binding.require_parser_check_item(session, "parser")
    source, _ = associate(session)
    source.preview_json = {}
    with pytest.raises(binding.CheckItemBindingError, match="关联有效"):
        binding.require_parser_check_item(session, "parser")


def test_resolves_reviewed_version_and_refuses_implicit_reassociation(db):
    session, _ = db
    associate(session)
    assert binding.require_parser_check_item(session, "parser") == {
        "check_item_id": "interface.state", "version_id": "version-1", "digest": "digest-1"}
    with pytest.raises(binding.CheckItemBindingError, match="关联不一致"):
        binding.require_parser_check_item(session, "parser", requested_id="other")


@pytest.mark.parametrize("version,mode,status,error", [
    ("old", "structured", "completed", "重新确认"),
    ("version-1", "text", "completed", "不需要 TextFSM"),
    ("version-1", "structured", "running", "先完成 CLI"),
])
def test_blocks_stale_semantics_text_commands_and_incomplete_imports(db, version, mode, status, error):
    session, item = db
    item.result_mode = mode
    associate(session, version=version, source_status=status)
    with pytest.raises(binding.CheckItemBindingError, match=error):
        binding.require_parser_check_item(session, "parser")


def test_generation_requires_spec_confirmed_against_same_semantics(db, monkeypatch):
    session, _ = db
    associate(session)
    monkeypatch.setattr(repository, "_simple_parser_readiness", lambda *args: {"ready": True, "blockers": []})
    job = SimpleNamespace(id="parser", agent_state_json={})
    assert not repository._parser_generation_readiness(session, job, [])["ready"]

    job.agent_state_json = {"parser_spec_check_item": binding.require_parser_check_item(session, "parser")}
    assert repository._parser_generation_readiness(session, job, [])["ready"]
    job.agent_state_json["parser_spec_check_item"]["digest"] = "old"
    assert not repository._parser_generation_readiness(session, job, [])["ready"]


def test_spec_request_inherits_association_and_rejects_changed_semantics_before_model(db, monkeypatch):
    session, item = db
    @contextmanager
    def scope():
        yield session
        session.flush()
    monkeypatch.setattr(repository, "get_db_session", scope)
    session.add(ParserTemplateJob(id="parser", job_type="CREATE_TEMPLATE", vendor="test", profile="test",
        canonical_command="show interface", output_signature="sig", idempotency_key="parser", status="collecting"))
    session.add(ParserJobSample(id="sample", job_id="parser", snapshot_id="snapshot", device_id="device",
        output_signature="sig", sanitization_status="sanitized", sanitized_text="Interface State\nGE1 UP"))
    session.flush()
    repo = repository.ParserJobRepository()
    with pytest.raises(repository.ParserJobStateError, match="先在待归类"):
        repo.queue_parser_spec_suggestion("parser", task_id="suggestion", actor="test", reason="test")
    associate(session)
    queued = repo.queue_parser_spec_suggestion("parser", task_id="suggestion", actor="test", reason="test")
    suggestion = queued["agent_state_json"]["parser_spec_suggestion"]
    assert suggestion["check_item_id"] == "interface.state"
    assert suggestion["check_item_binding"]["digest"] == "digest-1"
    item.version_id = "version-2"
    with pytest.raises(repository.ParserJobStateError, match="重新确认"):
        repo.record_parser_spec_suggestion("parser", task_id="suggestion", status="running")
