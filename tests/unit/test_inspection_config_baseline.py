import hashlib
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import paramiko
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from src.apps.inspection.backend import config_baseline as baseline
from src.apps.inspection.backend import diagnostics
from src.infrastructure.db import postgres
from src.infrastructure.db.models import CommandSnapshot, NetworkDevice, PatrolRun

TEXT = (
    "#\ninterface GigabitEthernet3/0\n port access vlan 100\n"
    " ospf authentication-mode md5 1 cipher TEST-SECRET\n#\nreturn"
)
NOW = datetime(2026, 9, 30, 1, tzinfo=timezone.utc)


@pytest.fixture
def database(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Baseline reading may not connect to real DB, device or model")

    monkeypatch.setattr(postgres.engine, "connect", forbidden)
    monkeypatch.setattr(paramiko.SSHClient, "connect", forbidden)
    monkeypatch.setattr(httpx.Client, "send", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)
    engine = create_engine(f"sqlite:///{tmp_path / 'baseline.sqlite'}")
    # No change/event/check-comparison tables exist: the reader cannot use them.
    for model in (NetworkDevice, PatrolRun, CommandSnapshot):
        model.__table__.create(engine)

    @contextmanager
    def sessions():
        with Session(engine) as db, db.begin():
            yield db

    monkeypatch.setattr(baseline, "get_db_session", sessions)
    monkeypatch.setattr(diagnostics, "get_db_session", sessions)
    with sessions() as db:
        db.add(
            NetworkDevice(
                id="asset-1", device_name="switch", management_ip="192.0.2.10", status="active"
            )
        )
        db.add(
            PatrolRun(
                id="baseline-run",
                logical_run_id="baseline-run",
                mode="offline",
                source="upload",
                status="completed",
                started_at=NOW,
                baseline_eligible=True,
            )
        )
        db.add(
            CommandSnapshot(
                id="snapshot",
                run_id="baseline-run",
                inventory_device_id="asset-1",
                device_id="old-display-name",
                device_name="switch",
                management_ip="192.0.2.10",
                execution_item_id="item",
                command="display current-configuration",
                canonical_command="display current-configuration",
                raw_text=TEXT,
                raw_text_hash=hashlib.sha256(TEXT.encode()).hexdigest(),
                observed_at=NOW,
                parse_status="raw_hashed",
                output_status="data",
                snapshot_kind="full",
            )
        )
    yield sessions
    engine.dispose()


def read(**kwargs):
    body = dict(
        snapshot_id="snapshot",
        host="192.0.2.10",
        platform="comware",
        section="interface",
        target="GigabitEthernet3/0",
    )
    body.update(kwargs)
    return baseline.read_config_section(
        SimpleNamespace(user_id="viewer"), baseline.ReadConfigSection(**body)
    )


def test_reads_only_selected_raw_baseline_with_asset_time_hash_and_run_provenance(database):
    result = read()
    assert result["fragment"]["complete"] and result["fragment"]["redacted"]
    assert result["baseline"]["device_id"] == "asset-1"
    assert result["baseline"]["run_mode"] == "offline"
    assert result["baseline"]["run_source"] == "upload"
    assert result["baseline"]["observed_at"] == NOW.isoformat()
    assert result["baseline"]["source_sha256"] == hashlib.sha256(TEXT.encode()).hexdigest()
    assert "TEST-SECRET" not in json.dumps(result)
    assert result["fragment"]["start_line"] == 2


@pytest.mark.parametrize(
    "change",
    [
        "different_asset",
        "same_name_without_address",
        "host_changed",
        "ambiguous_host",
        "deleted_asset",
        "missing_run",
    ],
)
def test_identity_is_exact_asset_mapping_never_name_guess(database, change):
    with database() as db:
        snapshot = db.get(CommandSnapshot, "snapshot")
        if change == "different_asset":
            snapshot.inventory_device_id = "asset-other"
        elif change == "same_name_without_address":
            snapshot.inventory_device_id = None
            snapshot.device_id = "switch"
            snapshot.management_ip = None
        elif change == "host_changed":
            snapshot.management_ip = "192.0.2.11"
        elif change == "ambiguous_host":
            db.add(
                NetworkDevice(
                    id="asset-other",
                    device_name="other-switch",
                    management_ip="192.0.2.10",
                    status="active",
                )
            )
        elif change == "deleted_asset":
            db.get(NetworkDevice, "asset-1").status = "deleted"
        else:
            snapshot.run_id = "missing"
    with pytest.raises(HTTPException) as error:
        read()
    assert error.value.status_code == 404


def test_offline_unlinked_snapshot_uses_only_explicit_historical_address(database):
    with database() as db:
        db.get(CommandSnapshot, "snapshot").inventory_device_id = None
        db.delete(db.get(NetworkDevice, "asset-1"))
    result = read()
    assert result["fragment"]["complete"]
    assert result["baseline"]["device_id"] == "old-display-name"
    assert result["baseline"]["identity_source"] == "snapshot_management_ip"
    assert "地址未复用" in result["baseline"]["identity_limitation"]
    with pytest.raises(HTTPException):
        read(host="192.0.2.11")
    with database() as db:
        db.get(PatrolRun, "baseline-run").mode = "online"
    with pytest.raises(HTTPException):
        read()


def test_snapshot_catalog_supports_exact_ip_and_raw_read_uses_shared_redaction(database):
    result = diagnostics.snapshots(diagnostics.SnapshotScope(host="192.0.2.10"))
    assert len(result["items"]) == 1
    assert result["items"][0]["management_ip"] == "192.0.2.10"
    assert result["items"][0]["snapshot_kind"] == "full"
    assert result["items"][0]["output_status"] == "data"
    assert not diagnostics.snapshots(diagnostics.SnapshotScope(host="192.0.2.1"))["items"]
    raw = diagnostics.read_snapshot(diagnostics.Snapshot(snapshot_id="snapshot"))
    assert "TEST-SECRET" not in raw["text"] and raw["redacted"]


@pytest.mark.parametrize(
    "change", ["hash", "partial", "truncated", "command", "paging", "error", "missing_block"]
)
def test_bad_baseline_never_returns_complete_fragment(database, change):
    with database() as db:
        snapshot = db.get(CommandSnapshot, "snapshot")
        if change == "hash":
            snapshot.raw_text_hash = "bad"
        elif change == "partial":
            snapshot.snapshot_kind = "hash_only"
        elif change == "truncated":
            snapshot.output_status = "truncated"
        elif change == "command":
            snapshot.canonical_command = "display current-configuration | include interface"
        else:
            text = {
                "paging": TEXT + "\n---- More ----",
                "error": TEXT + "\nWrong parameter found",
                "missing_block": "#\nospf 1\n#",
            }[change]
            snapshot.raw_text, snapshot.raw_text_hash = (
                text,
                hashlib.sha256(text.encode()).hexdigest(),
            )
    result = read()
    assert not result["fragment"]["complete"]
    assert result["fragment"]["quality_gaps"]


def test_helper_performs_select_only_and_is_not_advertised_as_evidence(database):
    with database() as db:
        engine = db.get_bind()

    def select_only(conn, cursor, statement, parameters, context, executemany):
        assert statement.lstrip().upper().startswith("SELECT"), statement

    event.listen(engine, "before_cursor_execute", select_only)
    try:
        read()
    finally:
        event.remove(engine, "before_cursor_execute", select_only)
    from src.apps.inspection.backend.tools import TOOLS

    helper = next(tool for tool in TOOLS if tool.name == "read_config_section")
    assert not helper.evidence and not helper.mutates
    assert "read_changes" not in {tool.name for tool in TOOLS}
