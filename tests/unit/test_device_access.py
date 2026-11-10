"""Public device access safety, isolation and transport behavior (no live SSH)."""

from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest
from paramiko import AuthenticationException, SSHException

from src.platform.device_access.contracts import (
    AccessContext,
    AccessGrant,
    BatchRequest,
    Command,
    ConnectionProfile,
    DeviceAccessError,
    Target,
)
from src.platform.device_access.service import DeviceAccessService


class Connection:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.commands = []
        self.closed = False

    def send_command(self, text, read_timeout):
        self.commands.append((text, read_timeout))
        response = self.responses.get(text, "output")
        if isinstance(response, Exception):
            raise response
        return response

    def find_prompt(self):
        return "SW1#"

    def disconnect(self):
        self.closed = True


class Driver:
    name = "fake"
    version = "1.0"

    def __init__(self, connection):
        self.connection = connection
        self.connects = 0
        self.error = None

    def connect(self, target, profile):
        self.connects += 1
        if self.error:
            raise self.error
        return self.connection


class Admission:
    def __init__(self):
        self.entered = self.released = self.lost = False

    def guard(self):
        if self.lost:
            from src.platform.device_access.concurrency import ConcurrencyLeaseLostError

            raise ConcurrencyLeaseLostError("fenced")

    @contextmanager
    def hold(self, target, profile):
        self.entered = True
        try:
            yield self.guard
        finally:
            self.released = True


@pytest.fixture
def batch():
    connection = Connection()
    driver = Driver(connection)
    admission = Admission()
    target = Target("sw1", "192.0.2.1", device_id="device-1")
    profile = ConnectionProfile("profile1", "operator", password="sensitive-password")
    request = BatchRequest(
        "sw1", "profile1", (Command("version", "show version"), Command("clock", "show clock"))
    )
    context = AccessContext(
        "user1",
        "topology",
        (AccessGrant("sw1", "profile1", frozenset(c.text for c in request.commands)),),
    )
    service = DeviceAccessService(
        target_resolver=lambda ref: target,
        profile_resolver=lambda ref: profile,
        driver=driver,
        admission=admission,
    )
    return SimpleNamespace(**locals())


@pytest.mark.parametrize("change", ["actor", "grant", "target", "profile", "command", "action"])
def test_unauthorized_never_resolves_profile_or_connects(batch, change):
    context, request = batch.context, batch.request
    if change == "actor":
        context = replace(context, actor_id="")
    elif change == "grant":
        context = replace(context, grants=())
    elif change == "target":
        request = replace(request, target_ref="sw2")
    elif change == "profile":
        request = replace(request, connection_profile_id="another")
    elif change == "command":
        request = replace(request, commands=(Command("change", "configure terminal"),))
    else:
        context = replace(context, grants=(replace(context.grants[0], action="device.change"),))
    batch.service.profile_resolver = lambda ref: pytest.fail("must not resolve secrets")
    with pytest.raises(DeviceAccessError, match="not authorized"):
        batch.service.execute_batch(context, request)
    assert batch.driver.connects == 0
    assert not batch.admission.entered


def test_sequential_batch_uses_one_connection_and_emits_evidence_refs(batch):
    emitted = []

    def persist(result):
        assert not batch.admission.released
        emitted.append(result)
        return f"evidence:{result.command_id}"

    result = batch.service.execute_batch(batch.context, batch.request, on_result=persist)
    assert batch.driver.connects == 1
    assert batch.connection.commands == [("show version", 30), ("show clock", 30)]
    assert [r.command_id for r in emitted] == ["version", "clock"]
    assert result.execution_status == "succeeded"
    assert all(r.output_complete and r.started_at and r.finished_at for r in result.results)
    assert result.results[0].evidence_ref == "evidence:version"
    assert result.results[0].driver_version == "1.0"
    assert batch.connection.closed and batch.admission.released


def test_probe_uses_same_admission_and_does_not_send_commands(batch):
    result = batch.service.execute_batch(
        batch.context, replace(batch.request, commands=(), capture_prompt=True)
    )
    assert result.prompt == "SW1#"
    assert batch.admission.entered and batch.admission.released
    assert not batch.connection.commands and batch.connection.closed


def test_dsh_diagnostics_can_use_same_service_without_topology_dependency(batch):
    context = replace(batch.context, source_app="assistant", task_id="diagnostic-run")
    result = batch.service.execute_batch(context, batch.request)
    assert result.execution_status == "succeeded"
    assert len(result.results) == 2


@pytest.mark.parametrize(
    "error,code",
    [
        (
            AuthenticationException("authentication failed sensitive-password"),
            "authentication_failed",
        ),
        (SSHException("Server sw1 not found in known_hosts"), "host_key_rejected"),
        (ValueError("invalid device_type"), "invalid_request"),
    ],
)
def test_auth_trust_and_invalid_requests_never_retry(batch, error, code):
    batch.service.profile_resolver = lambda ref: replace(
        batch.profile, connection_attempts=2, retry_delay=0
    )
    batch.driver.error = error
    with pytest.raises(DeviceAccessError) as caught:
        batch.service.execute_batch(batch.context, batch.request)
    assert caught.value.failure_code == code
    assert "sensitive-password" not in str(caught.value)
    assert batch.driver.connects == 1
    assert batch.admission.released


def test_connection_failure_defaults_to_one_attempt(batch):
    batch.driver.error = TimeoutError("unreachable")
    with pytest.raises(DeviceAccessError) as caught:
        batch.service.execute_batch(batch.context, batch.request)
    assert caught.value.failure_code == "timeout" and batch.driver.connects == 1


def test_explicit_transient_retry_rechecks_guard(batch):
    batch.service.profile_resolver = lambda ref: replace(
        batch.profile, connection_attempts=2, retry_delay=0
    )
    attempts = []

    def connect(target, profile):
        attempts.append(1)
        if len(attempts) == 1:
            raise TimeoutError("banner timeout")
        return batch.connection

    batch.driver.connect = connect
    batch.service.execute_batch(batch.context, batch.request)
    assert len(attempts) == 2 and batch.connection.closed


@pytest.mark.parametrize("stop_on_error,count", [(True, 1), (False, 1)])
def test_command_timeout_is_incomplete_and_respects_stop_policy(batch, stop_on_error, count):
    batch.connection.responses["show version"] = TimeoutError("read timeout sensitive-password")
    result = batch.service.execute_batch(
        batch.context, replace(batch.request, stop_on_error=stop_on_error)
    )
    assert len(batch.connection.commands) == count
    assert result.results[0].failure_code == "timeout"
    assert not result.results[0].output_complete
    assert "sensitive-password" not in result.results[0].error
    assert result.results[1].execution_status == "not_executed"
    assert batch.connection.closed


def test_cancellation_before_connect(batch):
    with pytest.raises(DeviceAccessError) as caught:
        batch.service.execute_batch(batch.context, batch.request, should_continue=lambda: False)
    assert caught.value.failure_code == "cancelled"
    assert batch.driver.connects == 0 and batch.admission.released


def test_cancellation_between_commands(batch):
    with pytest.raises(DeviceAccessError) as caught:
        batch.service.execute_batch(
            batch.context, batch.request, should_continue=lambda: len(batch.connection.commands) < 1
        )
    assert caught.value.failure_code == "cancelled"
    assert len(batch.connection.commands) == 1 and batch.connection.closed


def test_lease_lost_during_command_does_not_publish_or_continue(batch):
    from src.platform.device_access.concurrency import ConcurrencyLeaseLostError

    def send(text, read_timeout):
        batch.admission.lost = True
        return "stale output"

    batch.connection.send_command = send
    with pytest.raises(ConcurrencyLeaseLostError):
        batch.service.execute_batch(
            batch.context, batch.request, on_result=lambda r: pytest.fail("stale output published")
        )
    assert batch.connection.closed and batch.admission.released


def test_callback_failure_still_closes_connection(batch):
    def persist(result):
        raise RuntimeError("storage unavailable")

    with pytest.raises(RuntimeError, match="storage unavailable"):
        batch.service.execute_batch(batch.context, batch.request, on_result=persist)
    assert batch.connection.closed and batch.admission.released
    assert len(batch.connection.commands) == 1


def test_budget_limits_command_and_stops_next_publish(batch, monkeypatch):
    now = [100.0]
    monkeypatch.setattr("src.platform.device_access.service.time.monotonic", lambda: now[0])

    def send(text, read_timeout):
        assert read_timeout == 2
        now[0] += 3
        return "late output"

    batch.connection.send_command = send
    with pytest.raises(DeviceAccessError) as caught:
        batch.service.execute_batch(
            batch.context,
            replace(batch.request, budget_seconds=2),
            on_result=lambda r: pytest.fail("expired output"),
        )
    assert caught.value.failure_code == "budget_exceeded"
    assert batch.connection.closed


def test_empty_output_does_not_claim_no_neighbors(batch):
    batch.connection.responses["show version"] = ""
    result = batch.service.execute_batch(batch.context, batch.request)
    assert result.results[0].execution_status == "succeeded"
    assert result.results[0].output == ""
    assert not hasattr(result.results[0], "parse_status")


def test_new_profile_cannot_disable_host_verification(batch):
    batch.service.profile_resolver = lambda ref: replace(batch.profile, ssh_strict=False)
    with pytest.raises(DeviceAccessError, match="strict host-key"):
        batch.service.execute_batch(batch.context, batch.request)
    assert batch.driver.connects == 0


def test_public_module_has_no_business_app_or_patrol_imports():
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "src/platform/device_access"
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            modules = (
                [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else ([alias.name for alias in node.names] if isinstance(node, ast.Import) else [])
            )
            assert not any(name.startswith(("src.apps.", "src.core.")) for name in modules), path
