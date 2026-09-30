from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src.apps.device_access.backend import api, batches, saved, service
from src.apps.device_access.backend.models import Base
from src.apps.device_access.backend.schemas import (
    BatchCommands,
    CommandPolicy,
    SessionCreate,
    SessionUpdate,
)
from src.auth.models import CurrentUser
from src.infrastructure.db.models import AuditLogRecord, SSHPolicyRecord
from src.platform.device_access import governance
from src.platform.device_access.contracts import DeviceAccessError
from src.platform.device_access.human_cli import HumanCLI


@pytest.fixture
def world(monkeypatch):
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    AuditLogRecord.__table__.create(engine)
    SSHPolicyRecord.__table__.create(engine)

    @contextmanager
    def sessions():
        with Session(engine) as session, session.begin():
            yield session

    with sessions() as session:
        session.add(
            SSHPolicyRecord(
                id=1,
                patterns=[".*"],
                version=1,
                updated_at=datetime.now(timezone.utc),
                updated_by="test",
            )
        )
    monkeypatch.setattr(service, "sessions", sessions)
    monkeypatch.setattr(governance, "get_db_session", sessions)
    monkeypatch.setattr(service, "_guard", lambda *args: lambda: True)
    monkeypatch.setenv("DEVICE_ACCESS_CREDENTIAL_KEY", Fernet.generate_key().decode())
    yield sessions
    engine.dispose()


class Channel:
    closed = False

    def __init__(self, replies=None):
        self.pending = [b"<R1>"]
        self.sent = []
        self.replies = replies or {}

    def recv_ready(self):
        return bool(self.pending)

    def recv(self, size):
        return self.pending.pop(0)

    def exit_status_ready(self):
        return False

    def settimeout(self, timeout):
        pass

    def sendall(self, value):
        self.sent.append(value)
        command = value.decode().strip()
        self.pending.append(self.replies.get(command, command + "\r\nok\r\n<R1>").encode())


def operation():
    return governance.SSHOperation(
        "u", "device-access", "s1", "10.0.0.1", secrets=("lab-test-secret",)
    )


def test_mode_changing_commands_and_device_errors_have_results(world):
    channel = Channel(
        {
            "system-view": "system-view\r\n[R1]",
            "display invalid": "display invalid\r\n% Wrong parameter\r\n[R1]",
        }
    )
    cli = HumanCLI(channel, operation(), lambda: None)
    cli.initialize()
    assert cli.execute("system-view")["status"] == "succeeded"
    assert cli.execute("display invalid")["status"] == "failed"
    rows = governance.query_audit(actor_id="u", kind="command_finished")["items"]
    assert {row["status"] for row in rows} == {"succeeded", "failed"}


@pytest.mark.parametrize(
    "command", ["display version\nreboot", "display version\rreboot", "\x1b[A", "show\x08"]
)
def test_multiline_and_control_input_never_reaches_channel(world, command):
    channel = Channel()
    cli = HumanCLI(channel, operation(), lambda: None)
    cli.initialize()
    with pytest.raises(DeviceAccessError):
        cli.execute(command)
    assert channel.sent == []


def test_live_policy_change_blocks_existing_terminal(world):
    channel = Channel()
    cli = HumanCLI(channel, operation(), lambda: None)
    cli.initialize()
    cli.execute("display version")
    governance.save_policy([], "admin")
    with pytest.raises(DeviceAccessError, match="白名单"):
        cli.execute("display version")
    assert len(channel.sent) == 1


def test_interactive_question_stops_channel_and_masks_output(world):
    channel = Channel({"save": "save\r\nlab-test-secret\r\nContinue? [Y/N]:"})
    cli = HumanCLI(channel, operation(), lambda: None)
    cli.initialize()
    with pytest.raises(DeviceAccessError) as error:
        cli.execute("save")
    assert error.value.failure_code == "interaction_required"
    assert not cli.usable
    row = governance.query_audit(kind="command_finished")["items"][0]
    assert "lab-test-secret" not in str(row)


def test_audit_failure_blocks_actual_send(world, monkeypatch):
    def fail(**kwargs):
        raise DeviceAccessError("audit_unavailable", "unavailable")

    channel = Channel()
    cli = HumanCLI(channel, operation(), lambda: None)
    cli.initialize()
    monkeypatch.setattr(governance, "write_event", fail)
    with pytest.raises(DeviceAccessError):
        cli.execute("display version")
    assert channel.sent == []


def new_session(host="10.0.0.1"):
    return saved.create(
        "u", SessionCreate(name=host, host=host, username="operator", password="lab-test-secret")
    )["id"]


def body(ids, commands=None, request_id="request1"):
    return BatchCommands(
        request_id=request_id,
        session_ids=ids,
        commands=commands or ["display version", "display interface brief"],
    )


def transport(monkeypatch, replies=None):
    channels = []

    class Access:
        @contextmanager
        def terminal(self, *args, **kwargs):
            op = governance.current_operation()
            op.event("connecting", mode="human")
            channel = Channel(replies)
            channel.operation = op
            channels.append(channel)
            yield channel, lambda: None
            channel.closed = True
            op.event("disconnected", mode="human")

    monkeypatch.setattr(service, "access", lambda *args, **kwargs: Access())
    return channels


def test_batch_is_sequential_and_deduplicated_with_exact_payload(world, monkeypatch):
    ids = [new_session(), new_session("10.0.0.2")]
    request = body(ids)
    view, created, targets = batches.create("u", request)
    assert created
    duplicate, created, _ = batches.create("u", request)
    assert not created and duplicate["id"] == view["id"]
    with pytest.raises(HTTPException) as conflict:
        batches.create("u", body(ids, ["display arp"]))
    assert conflict.value.status_code == 409
    saved.update("u", ids[0], SessionUpdate(name="renamed after request"))
    channels = transport(monkeypatch)
    batches.run("u", view["id"], targets, request.commands)
    assert [c.sent for c in channels] == [[b"display version\n", b"display interface brief\n"]] * 2
    completed = batches.read("u", view["id"])
    assert completed["status"] == "completed"
    assert [d["session_id"] for d in completed["results"]] == ids
    assert [d["connection_session_id"] for d in completed["results"]] == [
        c.operation.session_id for c in channels
    ]
    assert not set(ids) & {c.operation.session_id for c in channels}
    assert channels[0].operation.device_name == "10.0.0.1"
    with pytest.raises(HTTPException):
        batches.read("other", view["id"])


def test_batch_device_error_stops_only_that_device(world, monkeypatch):
    ids = [new_session(), new_session("10.0.0.2")]
    request = body(ids)
    view, _, targets = batches.create("u", request)
    channels = transport(monkeypatch, {"display version": "% Unrecognized command\r\n<R1>"})
    batches.run("u", view["id"], targets, request.commands)
    assert [len(c.sent) for c in channels] == [1, 1]
    results = batches.read("u", view["id"])["results"]
    assert all(d["commands"][1]["status"] == "not_executed" for d in results)
    events = governance.query_audit(kind="batch_device_finished")["items"]
    assert {e["detail"]["session_id"] for e in events} == {
        d["connection_session_id"] for d in results
    }


def test_cancel_before_worker_never_opens_connection(world, monkeypatch):
    request = body([new_session()])
    view, _, targets = batches.create("u", request)
    channels = transport(monkeypatch)
    batches.cancel("u", view["id"])
    batches.run("u", view["id"], targets, request.commands)
    assert channels == []
    result = batches.read("u", view["id"])
    assert result["status"] == "cancelled"
    assert result["results"][0]["connection_session_id"] is None
    assert all(
        "session_id" not in event["detail"]
        for event in governance.query_audit(kind="batch_device_finished")["items"]
    )


@pytest.mark.parametrize("connection_started", [False, True])
def test_batch_login_failure_has_only_its_actual_connection_id(
    world, monkeypatch, connection_started
):
    first = new_session()
    second = saved.create("u", SessionCreate(name="missing login", host="10.0.0.2"))["id"]
    request = body([first, second])
    view, _, targets = batches.create("u", request)
    seen = []

    class Access:
        @contextmanager
        def terminal(self, *args, **kwargs):
            op = governance.current_operation()
            seen.append(op.session_id)
            if connection_started:
                op.event("connecting", mode="human")
            raise DeviceAccessError("authentication_failed", "test login failure")
            yield  # A failed context manager still exercises the caller's cleanup path.

    monkeypatch.setattr(service, "access", lambda *args, **kwargs: Access())
    batches.run("u", view["id"], targets, request.commands)
    results = batches.read("u", view["id"])["results"]
    expected_id = seen[0] if connection_started else None
    assert [d["connection_session_id"] for d in results] == [expected_id, None]
    events = {
        event["detail"]["host"]: event["detail"]
        for event in governance.query_audit(kind="batch_device_finished")["items"]
    }
    assert events["10.0.0.1"].get("session_id") == expected_id
    assert "session_id" not in events["10.0.0.2"]


def test_cancel_during_batch_preserves_sent_result_and_skips_remaining(world, monkeypatch):
    request = body([new_session(), new_session("10.0.0.2")])
    view, _, targets = batches.create("u", request)
    channels = transport(monkeypatch)
    original = HumanCLI.execute

    def execute(cli, command, **kwargs):
        result = original(cli, command, **kwargs)
        batches.cancel("u", view["id"])
        return result

    monkeypatch.setattr(HumanCLI, "execute", execute)
    batches.run("u", view["id"], targets, request.commands)
    assert len(channels) == 1 and len(channels[0].sent) == 1
    result = batches.read("u", view["id"])
    assert result["status"] == "cancelled"
    assert result["results"][0]["commands"][0]["status"] == "succeeded"
    assert result["results"][0]["commands"][1]["status"] == "not_executed"
    assert result["results"][1]["status"] == "not_executed"


def test_tightening_policy_during_batch_checks_next_send(world, monkeypatch):
    request = body([new_session()])
    view, _, targets = batches.create("u", request)
    channels = transport(monkeypatch)
    original = HumanCLI.execute

    def execute(cli, command, **kwargs):
        result = original(cli, command, **kwargs)
        governance.save_policy([], "admin")
        return result

    monkeypatch.setattr(HumanCLI, "execute", execute)
    batches.run("u", view["id"], targets, request.commands)
    assert len(channels[0].sent) == 1
    result = batches.read("u", view["id"])
    assert result["results"][0]["commands"][1]["failure_code"] == "policy_denied"


def test_cancel_while_saving_command_progress_is_checked_before_send(world, monkeypatch):
    request = body([new_session()])
    view, _, targets = batches.create("u", request)
    channels = transport(monkeypatch)
    original = batches._publish

    def publish(owner, batch_id, results, status=None):
        original(owner, batch_id, results, status)
        if any(c["status"] == "running" for d in results for c in d["commands"]):
            batches.cancel(owner, batch_id)

    monkeypatch.setattr(batches, "_publish", publish)
    batches.run("u", view["id"], targets, request.commands)
    assert len(channels) == 1 and channels[0].sent == []
    assert batches.read("u", view["id"])["status"] == "cancelled"


def test_finished_audit_failure_still_closes_batch_record(world, monkeypatch):
    request = body([new_session()])
    view, _, targets = batches.create("u", request)
    transport(monkeypatch)
    original = batches.write_event

    def write(**kwargs):
        if kwargs["kind"] == "batch_finished":
            raise DeviceAccessError("audit_unavailable", "unavailable")
        return original(**kwargs)

    monkeypatch.setattr(batches, "write_event", write)
    batches.run("u", view["id"], targets, request.commands)
    assert batches.read("u", view["id"])["status"] == "failed"


def test_batch_commands_credentials_are_redacted_before_storage(world):
    request = body([new_session()], ["password simple lab-test-secret", "echo lab-test-secret"])
    view, _, _ = batches.create("u", request)
    with world() as session:
        audit = list(session.scalars(select(AuditLogRecord)))
        assert "lab-test-secret" not in str([r.detail for r in audit])
    assert "lab-test-secret" not in str(view)


def test_policy_admin_and_audit_owner_scope(world):
    user = CurrentUser(user_id="u", username="u", role="operator")
    admin = CurrentUser(user_id="admin", username="admin", role="admin")
    with pytest.raises(HTTPException) as error:
        api.save_command_policy(CommandPolicy(patterns=[]), user)
    assert error.value.status_code == 403
    api.save_command_policy(CommandPolicy(patterns=[]), admin)
    governance.write_event(actor_id="u", source_app="device-access", kind="test")
    assert len(api.audit_records(limit=50, before=0, user=user)["items"]) == 1
    assert len(api.audit_records(limit=50, before=0, user=admin)["items"]) == 2


def test_management_change_and_audit_are_atomic(world, monkeypatch):
    def fail(**kwargs):
        raise DeviceAccessError("audit_unavailable", "unavailable")

    monkeypatch.setattr(saved, "write_event", fail)
    with pytest.raises(DeviceAccessError):
        new_session()
    assert saved.list_sessions("u") == []
