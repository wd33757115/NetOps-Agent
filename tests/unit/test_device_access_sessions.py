"""Session reuse, explicit authority, audit fail-closed, lifetime and command budgets."""

import json
import time
from contextlib import contextmanager
from dataclasses import replace
from threading import Event, Thread
from types import SimpleNamespace

import pytest

from src.platform.device_access import AccessContext, AccessGrant, DeviceAccessService
from src.platform.device_access.contracts import ConnectionProfile, DeviceAccessError, Target
from src.platform.device_access.errors import CommandExecutionError
from src.platform.device_access.sessions import SessionBudget

PASSWORD = "session-test-secret"


@pytest.fixture
def harness():
    state = SimpleNamespace(connects=0, releases=0, closes=0, sent=[], events=[], closed=Event())

    class Driver:
        name, version = "test", "1"

        def connect(self, *args):
            state.connects += 1
            return SimpleNamespace(
                send_command=self.send, find_prompt=lambda: "<SW1>", disconnect=self.disconnect
            )

        def send(self, command, **kwargs):
            state.sent.append((command, kwargs["read_timeout"]))
            return "output " + PASSWORD

        def disconnect(self):
            state.closes += 1
            state.closed.set()

    class Admission:
        @contextmanager
        def hold(self, *args):
            try:
                yield lambda: None
            finally:
                state.releases += 1

    state.service = DeviceAccessService(
        target_resolver=lambda _: Target("target", "10.0.0.1"),
        profile_resolver=lambda _: ConnectionProfile("target", "operator", PASSWORD),
        driver=Driver(),
        admission=Admission(),
    )
    state.context = AccessContext(
        "alice", "device-access", (AccessGrant("target", "target", action="device.session"),)
    )

    def audit(event):
        state.events.append(event)
        return "evidence:" + event["id"]

    def open_session(**kw):
        return state.service.open_session(
            state.context,
            "target",
            **(dict(platform="comware", purpose="diagnose", audit=audit) | kw),
        )

    state.open = open_session
    return state


def test_reuses_one_connection_normalizes_and_audits_redacted_output(harness):
    with harness.open(purpose="investigate " + PASSWORD) as session:
        first = session.run(" dis   version ")
        second = session.run("display interface brief")
        assert not session.closed
    assert session.closed and harness.connects == harness.closes == harness.releases == 1
    assert [c for c, _ in harness.sent] == ["display version", "display interface brief"]
    assert first.policy_level == second.policy_level == "L1"
    assert first.output == "output [redacted]" and first.evidence_ref.startswith("evidence:")
    assert PASSWORD not in json.dumps(harness.events)
    assert [event["kind"] for event in harness.events] == [
        "opening",
        "opened",
        "command_requested",
        "command_finished",
        "command_requested",
        "command_finished",
        "closed",
    ]
    assert harness.events[3]["command"] == " dis   version "
    assert len(harness.events[3]["output_sha256"]) == 64


@pytest.mark.parametrize("action", ["device.collect", "device.terminal", "unknown"])
def test_old_grants_never_authorize_dynamic_sessions(harness, action):
    harness.context = replace(
        harness.context, grants=(AccessGrant("target", "target", action=action),)
    )
    harness.service.profile_resolver = lambda _: pytest.fail("resolved secret")
    with pytest.raises(DeviceAccessError) as error:
        harness.open()
    assert error.value.failure_code == "not_authorized"
    assert harness.connects == 0 and not harness.events


@pytest.mark.parametrize(
    "kw",
    [
        {"total_seconds": 601},
        {"idle_seconds": 61},
        {"total_seconds": float("nan")},
        {"max_commands": 61},
        {"max_commands": True},
        {"max_probes": 11},
    ],
)
def test_budget_limits_validate_before_ssh(harness, kw):
    with pytest.raises(DeviceAccessError):
        harness.open(budget=SessionBudget(**kw))
    assert harness.connects == 0


@pytest.mark.parametrize(
    "command,level,status",
    [
        ("system-view", "L4", "denied"),
        ("display version\nreboot", "L4", "denied"),
        ("display diagnostic-information", "L3", "pending_approval"),
        ("debugging ip packet", "L3", "pending_approval"),
    ],
)
def test_nonautomatic_commands_are_audited_but_never_sent(harness, command, level, status):
    with harness.open() as session:
        result = session.run(command)
    assert result.policy_level == level and result.execution_status == status
    assert not result.output_complete and not harness.sent
    assert harness.events[-2]["kind"] == "command_finished"


def test_probe_quota_and_explicit_vrf(harness):
    with harness.open(required_vrf="BLUE") as session:
        missing = session.run("ping 10.0.0.2")
        assert missing.execution_status == "denied"
        for _ in range(10):
            assert session.run("ping -vpn-instance BLUE 10.0.0.2").execution_status == "succeeded"
        exhausted = session.run("ping -vpn-instance BLUE 10.0.0.2")
        assert exhausted.failure_code == "probe_budget_exceeded"
        assert session.run("display version").execution_status == "succeeded"
    assert len(harness.sent) == 11
    assert "-c 3" in harness.sent[0][0] and "-t 2000" in harness.sent[0][0]
    assert harness.events[-1]["probes"] == 10


def test_command_quota_counts_denied_attempts(harness):
    session = harness.open(budget=SessionBudget(max_commands=1))
    assert session.run("reboot").execution_status == "denied"
    with pytest.raises(DeviceAccessError) as error:
        session.run("display version")
    assert error.value.failure_code == "budget_exceeded" and session.closed
    assert not harness.sent and harness.releases == 1


@pytest.mark.parametrize("failure_at", ["opening", "command_requested", "command_finished"])
def test_audit_failure_prevents_unrecorded_execution_or_result(harness, failure_at):
    def audit(event):
        if event["kind"] == failure_at:
            raise RuntimeError("storage unavailable " + PASSWORD)
        harness.events.append(event)

    with pytest.raises(DeviceAccessError) as error:
        with harness.open(audit=audit) as session:
            session.run("display version")
    assert error.value.failure_code == "audit_unavailable"
    assert PASSWORD not in str(error.value)
    assert len(harness.sent) == (1 if failure_at == "command_finished" else 0)
    assert harness.connects == (0 if failure_at == "opening" else 1)
    assert harness.closes == harness.releases == harness.connects


def test_idle_timer_closes_unused_handle_and_releases_once(harness):
    session = harness.open(budget=SessionBudget(idle_seconds=0.04))
    assert harness.closed.wait(2)
    session.close()
    assert session.closed and harness.closes == 1
    with pytest.raises(DeviceAccessError) as error:
        session.run("display version")
    assert error.value.failure_code == "idle_timeout"


def test_idle_is_suspended_during_active_command(harness):
    send = harness.service.driver.send

    def slow(*args, **kw):
        time.sleep(0.08)
        assert not harness.closed.is_set()
        return send(*args, **kw)

    harness.service.driver.send = slow
    with harness.open(budget=SessionBudget(idle_seconds=0.04)) as session:
        assert session.run("display version").execution_status == "succeeded"
        assert not session.closed


def test_total_deadline_interrupts_active_command_without_publishing(harness):
    def blocked(*args, **kw):
        assert harness.closed.wait(2)
        return "stale output"

    harness.service.driver.send = blocked
    session = harness.open(budget=SessionBudget(total_seconds=0.1))
    with pytest.raises(DeviceAccessError) as error:
        session.run("display version")
    assert error.value.failure_code == "budget_exceeded"
    assert session.closed and harness.closes == 1
    assert not any(event["kind"] == "command_finished" for event in harness.events)


def test_cancellation_before_and_after_command(harness):
    allowed = [True]
    send = harness.service.driver.send

    def revoke(*args, **kw):
        value = send(*args, **kw)
        allowed[0] = False
        return value

    harness.service.driver.send = revoke
    session = harness.open(should_continue=lambda: allowed[0])
    with pytest.raises(DeviceAccessError) as error:
        session.run("display version")
    assert error.value.failure_code == "cancelled" and session.closed
    assert not any(e["kind"] == "command_finished" for e in harness.events)
    with pytest.raises(DeviceAccessError):
        harness.open(should_continue=lambda: False)
    assert harness.connects == 1


def test_parallel_commands_are_rejected_and_explicit_cancel_unblocks(harness):
    started = Event()
    errors = []

    def blocked(*args, **kw):
        started.set()
        assert harness.closed.wait(2)
        return "closed"

    harness.service.driver.send = blocked
    session = harness.open()

    def run():
        try:
            session.run("display version")
        except DeviceAccessError as exc:
            errors.append(exc.failure_code)

    thread = Thread(target=run)
    thread.start()
    assert started.wait(2)
    with pytest.raises(DeviceAccessError) as error:
        session.run("display interface")
    assert error.value.failure_code == "session_busy"
    session.close("cancelled")
    thread.join(2)
    assert not thread.is_alive() and errors == ["cancelled"]
    assert harness.closes == harness.releases == 1


def test_partial_output_and_prompt_failure_close_connection(harness):
    def fail(*args, **kwargs):
        raise CommandExecutionError("output_incomplete", PASSWORD, "partial " + PASSWORD)

    harness.service.driver.send = fail
    with harness.open() as session:
        result = session.run("display version")
        assert session.closed
    assert result.output == "partial [redacted]" and not result.output_complete
    assert result.failure_code == "output_incomplete"
    assert PASSWORD not in json.dumps(harness.events)
    assert harness.releases == 1


def test_actual_paramiko_session_reuses_connection_and_blocks_configuration(tmp_path):
    from tests.unit.test_device_access_ssh import LocalAdmission, ssh_server

    with ssh_server(tmp_path) as (port, known, commands, authentications):
        service = DeviceAccessService(
            target_resolver=lambda _: Target("target", "127.0.0.1"),
            profile_resolver=lambda _: ConnectionProfile(
                "target", "operator", "password", port=port, known_hosts_file=str(known)
            ),
            admission=LocalAdmission(),
        )
        context = AccessContext(
            "alice", "test", (AccessGrant("target", "target", action="device.session"),)
        )
        events = []
        with service.open_session(
            context, "target", platform="comware", purpose="test", audit=events.append
        ) as session:
            assert session.run("display version").output_complete
            assert session.run("display interface brief").output_complete
            assert session.run("system-view").execution_status == "denied"
        assert commands == ["screen-length disable", "display version", "display interface brief"]
        assert events[0]["approved_setup_commands"] == ["screen-length disable"]
        assert events[1]["setup_commands"] == ["screen-length disable"]
        assert authentications == ["operator"]
        assert [e["kind"] for e in events].count("command_finished") == 3


def test_app_explicit_session_opt_in_audit_ownership_and_revocation(harness, monkeypatch, tmp_path):
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from src.apps.device_access.backend import api, service
    from src.apps.device_access.backend import sessions as app_sessions
    from src.apps.device_access.backend.models import Base
    from src.auth.models import CurrentUser

    engine = create_engine(f"sqlite:///{tmp_path / 'session.db'}")
    Base.metadata.create_all(engine)

    @contextmanager
    def sessions():
        with Session(engine) as session, session.begin():
            yield session

    allowed = {"allow_session": False}
    monkeypatch.setattr(service, "sessions", sessions)
    monkeypatch.setattr(service, "connection", lambda *a: (allowed, ""))
    monkeypatch.setattr(service, "access", lambda *a: harness.service)
    monkeypatch.setattr(
        app_sessions,
        "authorize_worker",
        lambda *a: CurrentUser(user_id="alice", username="alice", role="operator"),
    )
    with pytest.raises(DeviceAccessError) as error:
        app_sessions.open_session("alice", "target", platform="comware", purpose="test")
    assert error.value.failure_code == "not_authorized" and harness.connects == 0
    allowed["allow_session"] = True
    session = app_sessions.open_session("alice", "target", platform="comware", purpose="test")
    result = session.run("display version")
    assert result.evidence_ref.startswith("device-access:audit:")
    records = app_sessions.read_audit("alice", session.session_id)
    assert [e["kind"] for e in records] == [
        "opening",
        "opened",
        "command_requested",
        "command_finished",
    ]
    assert PASSWORD not in json.dumps(records)
    with pytest.raises(HTTPException) as not_found:
        app_sessions.read_audit("bob", session.session_id)
    assert not_found.value.status_code == 404
    allowed["allow_session"] = False
    with pytest.raises(DeviceAccessError):
        session.run("display version")
    assert session.closed and len(harness.sent) == 1
    assert app_sessions.read_audit("alice", session.session_id)[-1]["kind"] == "closed"
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.actor] = lambda: CurrentUser(
        user_id="bob", username="bob", role="operator"
    )
    with TestClient(app) as client:
        assert client.get(f"/sessions/{session.session_id}/audit").status_code == 404
    engine.dispose()


def test_lost_lease_never_sends_another_command(harness):
    lease_valid = [True]

    class Admission:
        @contextmanager
        def hold(self, *args):
            def check():
                if not lease_valid[0]:
                    raise DeviceAccessError("concurrency_lease_lost", "lost")

            try:
                yield check
            finally:
                harness.releases += 1

    harness.service.admission = Admission()
    session = harness.open()
    session.run("display version")
    lease_valid[0] = False
    with pytest.raises(DeviceAccessError) as error:
        session.run("display interface brief")
    assert error.value.failure_code == "concurrency_lease_lost"
    assert session.closed and len(harness.sent) == 1 and harness.releases == 1


def test_output_cap_retains_bounded_evidence_and_closes(harness):
    original = harness.service.profile_resolver
    harness.service.profile_resolver = lambda ref: replace(original(ref), max_output_bytes=5)
    with harness.open() as session:
        result = session.run("display version")
        assert session.closed
    assert result.output == "outpu" and result.failure_code == "output_limit"
    assert not result.output_complete


def test_vault_secrets_remain_redacted_through_final_close_audit(harness):
    from src.apps.device_access.backend.vault_driver import RedactedConnection
    from src.platform.device_access.security import CredentialMaterial

    original = harness.service.driver.connect
    harness.service.driver.connect = lambda *args: RedactedConnection(
        original(*args), CredentialMaterial("operator", PASSWORD, "enable-test-secret")
    )
    original_profile = harness.service.profile_resolver
    harness.service.profile_resolver = lambda ref: replace(original_profile(ref), password=None)
    with harness.open(purpose=PASSWORD + " enable-test-secret") as session:
        session.run("display version")
    assert harness.events[0]["purpose"] is None
    assert harness.events[-1]["purpose"] == "[redacted] [redacted]"
    assert PASSWORD not in json.dumps(harness.events)
    assert "enable-test-secret" not in json.dumps(harness.events)


def test_peer_already_closed_still_releases_and_audits_once(harness):
    def disconnect():
        harness.closes += 1
        raise EOFError()

    harness.service.driver.disconnect = disconnect
    session = harness.open()
    session.close()
    session.close()
    assert harness.closes == harness.releases == 1
    assert harness.events[-1]["kind"] == "closed"
