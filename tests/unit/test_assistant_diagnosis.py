import json
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.apps.assistant.backend import evidence
from src.apps.assistant.backend.workspace import DocumentWorkspace
from src.apps.inspection.backend import diagnostics as service
from src.infrastructure.db.models import (
    CommandSnapshot,
    NetworkDevice,
    NetworkEventRecord,
    PatrolRun,
)
from src.platform.inventory import facts


@pytest.fixture
def database(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'facts.sqlite'}")
    for model in (NetworkDevice, PatrolRun, CommandSnapshot, NetworkEventRecord):
        model.__table__.create(engine)

    @contextmanager
    def sessions():
        with Session(engine) as session, session.begin():
            yield session

    monkeypatch.setattr(service, "get_db_session", sessions)
    monkeypatch.setattr(facts, "get_db_session", sessions)
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with sessions() as db:
        db.add(
            NetworkDevice(
                id="d1",
                device_name="leaf1",
                management_ip="192.0.2.1",
                status="active",
                username="secret-user",
                credential_ref="secret-reference",
                attributes_json={"password": "NO-EXPOSURE"},
            )
        )
        db.add(
            PatrolRun(
                id="run1",
                logical_run_id="run1",
                mode="offline",
                source="upload",
                status="completed",
                started_at=timestamp,
            )
        )
        for i in range(3):
            db.add(
                CommandSnapshot(
                    id=f"s{i}",
                    run_id="run1",
                    inventory_device_id="d1",
                    device_id="leaf1",
                    device_name="leaf1",
                    execution_item_id=f"item{i}",
                    command="display interface",
                    canonical_command="display interface",
                    observed_at=timestamp,
                    parse_status="parse_failed",
                    raw_text="GE0/0/1 DOWN\npassword cipher NO-EXPOSURE\nMTU 1500\n",
                    raw_text_hash="a" * 64,
                )
            )
    yield sessions
    engine.dispose()


def test_discovery_to_paginated_raw_evidence_preserves_quality_and_time(database):
    devices = service.devices(service.Devices(query="leaf1"))
    assert devices["items"][0]["id"] == "d1"
    assert not any(
        k in json.dumps(devices) for k in ["secret-user", "secret-reference", "NO-EXPOSURE"]
    )
    runs = service.runs(service.Scope(device_id="d1"))
    assert runs["items"][0]["id"] == "run1"
    assert runs["items"][0]["source"] == "upload"
    first = service.snapshots(service.Scope(device_id="d1", limit=2))
    assert first["next_offset"] == 2 and len(first["items"]) == 2
    second = service.snapshots(service.Scope(device_id="d1", limit=2, offset=2))
    assert second["next_offset"] is None and len(second["items"]) == 1
    read = service.read_snapshot(service.Snapshot(snapshot_id="s0"))
    assert "DOWN" in read["text"] and "NO-EXPOSURE" not in read["text"]
    assert read["parse_status"] == "parse_failed" and read["redacted"]
    assert read["observed_at"].startswith("2026-01-01")
    assert service.snapshots(service.Scope(since="2026-09-01T00:00:00Z"))["items"] == []
    assert service.events(service.Scope(device_id="missing"))["items"] == []


def test_page_boundaries_cannot_reveal_secret_or_read_arbitrary_paths(database):
    full = service.read_snapshot(service.Snapshot(snapshot_id="s0"))["text"]
    chunks = [
        service.read_snapshot(service.Snapshot(snapshot_id="s0", offset=i, limit=3))["text"]
        for i in range(0, len(full), 3)
    ]
    assert "".join(chunks) == full
    with pytest.raises(HTTPException):
        service.read_snapshot(service.Snapshot(snapshot_id="../../private"))
    with pytest.raises(ValueError):
        service.Scope(limit=500)
    with pytest.raises(ValueError):
        service.Scope(since="2026-09-02T00:00:00Z", until="2026-09-01T00:00:00Z")
    assert service.devices(service.Devices(query="%"))["items"] == []


def test_business_citations_are_observed_bounded_and_permission_checked(monkeypatch):
    tool = "inspection__read_snapshot"
    monkeypatch.setattr(
        "src.apps.assistant.backend.workspace.registry.available_tools",
        lambda **kw: [{"name": tool, "evidence": True}],
    )
    monkeypatch.setattr(
        "src.apps.assistant.backend.workspace.registry.invoke",
        lambda *args, **kw: {"text": "GE0/0/1 DOWN", "observed_at": "2026-01-01"},
    )
    grants = []
    monkeypatch.setattr(
        "src.apps.assistant.backend.workspace.authorize_worker", lambda *args: grants.append(args)
    )
    workspace = DocumentWorkspace("alice", check=lambda: None)
    workspace.definitions()
    result = workspace.invoke(tool, {"snapshot_id": "s0"})
    references = workspace.references("观察到接口 DOWN " + result["citation"])
    assert references[0]["observed_at"] == "2026-01-01"
    assert grants == [("alice", "inspection")]
    with pytest.raises(ValueError):
        workspace.references("DOWN [evidence-invented]")
    with pytest.raises(ValueError):
        workspace.references("所有设备都正常")
    with pytest.raises(PermissionError):
        workspace.invoke("inspection__launch", {})
    with pytest.raises(ValueError):
        evidence.capture(tool, {"text": "x" * 30000})
    broken = {**references[0], "excerpt": "modified"}
    with pytest.raises(ValueError):
        evidence.validate(broken)


def test_private_key_blocks_are_redacted():
    value = "before\n-----BEGIN RSA PRIVATE KEY-----\nSECRET\n-----END RSA PRIVATE KEY-----\nafter"
    assert "SECRET" not in service.redact(value)


def test_official_mcp_reads_real_contract_and_returns_verified_business_citation(
    database, monkeypatch, tmp_path
):
    import asyncio
    import re
    from types import SimpleNamespace

    from src.apps.materials.backend.documents import DocumentReader
    from src.platform.agent_runtime.base import RunContext
    from src.platform.agent_runtime.dsh import DshRuntime
    from tests.unit.test_dsh_runtime import response

    monkeypatch.setattr(
        "src.platform.tools.registry.catalog",
        lambda: {"inspection__read_snapshot": ("inspection", service.TOOLS[-1])},
    )
    monkeypatch.setattr(
        "src.platform.tools.registry.authorize_worker",
        lambda *args: SimpleNamespace(user_id="alice"),
    )
    monkeypatch.setattr("src.apps.assistant.backend.workspace.authorize_worker", lambda *args: None)
    source = tmp_path / "empty"
    source.mkdir()
    workspace = DocumentWorkspace("alice", reader=DocumentReader(source), check=lambda: None)
    calls = []

    async def model(body):
        calls.append(body)
        if len(calls) == 1:
            return response(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "evidence",
                            "type": "function",
                            "function": {
                                "name": "mcp__netops__inspection__read_snapshot",
                                "arguments": '{"snapshot_id":"s0"}',
                            },
                        }
                    ]
                },
                "tool_calls",
            )
        content = json.dumps(body["messages"], ensure_ascii=False)
        assert "NO-EXPOSURE" not in content
        identifier = re.search(r"evidence-[0-9a-f]{24}", content).group()
        return response(
            {
                "content": (
                    "已有观测：接口 DOWN，记录来自2026-01-01；解析失败，"
                    f"不能认定当前状态。[{identifier}]"
                )
            },
            "stop",
        )

    async def run():
        return [
            e
            async for e in DshRuntime(transport=model).start(
                RunContext("business", "alice", "conversation", timeout_seconds=25), [], workspace
            )
        ]

    events = asyncio.run(run())
    assert events[-1].kind == "completed", [(e.kind, e.data) for e in events]
    reference = next(e.data for e in events if e.kind == "citation")
    assert reference["kind"] == "business" and reference["app_id"] == "inspection"
    finished = next(e.data for e in events if e.kind == "tool_finished")
    assert finished["evidence"]["id"] == reference["id"]
    assert finished["output"] is None
    assert "NO-EXPOSURE" not in reference["excerpt"]
    with database() as session:
        assert "NO-EXPOSURE" in session.get(CommandSnapshot, "s0").raw_text
