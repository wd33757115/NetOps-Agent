"""Offline coverage of the old collectors' actual Netmiko initialization boundary."""

import importlib

import pytest

from src.core.ingestion.adapters import guarded_cli
from src.core.ingestion.adapters.cli import connect_cli_device_with_retries
from src.platform.device_access.contracts import DeviceAccessError


class Operation:
    def __init__(self, allowed=None, audit_failure=False):
        self.allowed = allowed
        self.audit_failure = audit_failure
        self.events = []

    def check_command(self, command, **kwargs):
        if self.allowed is not None and command not in self.allowed:
            raise DeviceAccessError("policy_denied", "not allowed")
        return 1

    def before_command(self, command, phase="command", **kwargs):
        self.events.append(("requested", command, phase))
        if self.audit_failure:
            raise DeviceAccessError("audit_unavailable", "audit unavailable")
        self.check_command(command)
        return {"command": command, "phase": phase}

    def after_command(self, ticket, output="", status="succeeded", **kwargs):
        self.events.append(("finished", ticket["command"], status, self.redact(output)))

    def event(self, kind, **kwargs):
        self.events.append((kind, kwargs))

    def redact(self, value):
        return value.replace("test-secret", "[redacted]")


@pytest.fixture
def wire(monkeypatch, request):
    writes, connections = [], []
    dispatcher = importlib.import_module("netmiko.ssh_dispatcher")
    device_type = getattr(request, "param", "hp_comware")
    original = dispatcher.CLASS_MAPPER[device_type]

    class OfflineDevice(original):
        def __init__(self, *, auto_connect, **kwargs):
            assert auto_connect is False
            self.global_cmd_verify = False
            self.global_delay_factor = 0
            self.RETURN = "\n"
            self.disconnected = False
            connections.append(self)

        def _open(self):
            # Exercise the installed Netmiko vendor's real setup, not a stand-in list.
            self.session_preparation()

        def _test_channel_read(self, **kwargs):
            return "<SW1>"

        def set_base_prompt(self, **kwargs):
            return "SW1"

        def write_channel(self, data):
            writes.append(data)

        def read_channel(self):
            return "device output test-secret\n<SW1>"

        def read_until_prompt(self):
            return self.read_channel()

        def read_until_pattern(self, **kwargs):
            return self.read_channel()

        def clear_buffer(self):
            return self.read_channel()

        def send_command(self, command_string, **kwargs):
            self.write_channel("\n")
            self.write_channel(command_string + "\n")
            return self.read_channel()

        def find_prompt(self):
            self.write_channel("\n")
            return "<SW1>"

        def disconnect(self):
            self.cleanup()
            self.disconnected = True

        def cleanup(self):
            self.write_channel("quit\n")

    monkeypatch.setattr(dispatcher, "ssh_dispatcher", lambda device_type: OfflineDevice)
    yield writes, connections, device_type
    assert dispatcher.CLASS_MAPPER[device_type] is original


@pytest.mark.parametrize(
    ("wire", "expected"),
    [
        ("cisco_ios", ["terminal width 511", "terminal length 0"]),
        ("cisco_nxos", ["terminal width 511", "terminal length 0"]),
        ("huawei", ["screen-length 0 temporary"]),
    ],
    indirect=["wire"],
)
def test_other_installed_vendor_initialization_commands_are_governed(wire, expected):
    operation = Operation()
    connection = guarded_cli.connect(operation, device_type=wire[2], host="127.0.0.1")
    assert wire[0] == [command + "\n" for command in expected]
    assert [event[1] for event in operation.events if event[0] == "requested"] == expected
    assert [event[1] for event in operation.events if event[0] == "finished"] == expected
    connection.disconnect()


def test_vendor_initialization_is_checked_before_write_and_records_output(wire):
    operation = Operation()
    connection = guarded_cli.connect(operation, device_type="hp_comware", host="127.0.0.1")
    assert wire[0] == ["screen-length disable\n"]
    assert ("requested", "screen-length disable", "initialization") in operation.events
    assert (
        "finished",
        "screen-length disable",
        "succeeded",
        "device output [redacted]\n<SW1>",
    ) in operation.events
    assert connection.send_command("display version") == "device output [redacted]\n<SW1>"
    assert ("requested", "display version", "command") in operation.events
    assert not hasattr(connection, "write_channel")
    assert not hasattr(connection, "channel")
    connection.disconnect()
    assert wire[1][0].disconnected
    assert "quit\n" not in wire[0]


@pytest.mark.parametrize("unavailable", [False, True])
def test_setup_denial_or_audit_failure_sends_nothing_and_closes(wire, unavailable):
    operation = Operation(allowed={"display version"}, audit_failure=unavailable)
    with pytest.raises(DeviceAccessError) as error:
        guarded_cli.connect(operation, device_type="hp_comware", host="127.0.0.1")
    assert error.value.failure_code == ("audit_unavailable" if unavailable else "policy_denied")
    assert wire[0] == []
    assert wire[1][0].disconnected


def test_policy_is_rechecked_when_the_driver_actually_writes(wire):
    operation = Operation()
    connection = guarded_cli.connect(operation, device_type="hp_comware", host="127.0.0.1")
    previous = operation.before_command

    def revoke_after_request(command, **kwargs):
        ticket = previous(command, **kwargs)
        operation.allowed = set()
        return ticket

    operation.before_command = revoke_after_request
    with pytest.raises(DeviceAccessError, match="SSH command failed"):
        connection.send_command("display version")
    assert "display version\n" not in wire[0]
    assert any(event[:3] == ("finished", "display version", "failed") for event in operation.events)
    connection.disconnect()


def test_policy_failure_is_never_retried():
    attempts = []

    def denied(device):
        attempts.append(device)
        raise DeviceAccessError("policy_denied", "denied")

    with pytest.raises(DeviceAccessError):
        connect_cli_device_with_retries(
            {"attributes": {"connection_attempts": 3, "connection_retry_delay": 0}},
            connector=denied,
        )
    assert len(attempts) == 1


def test_real_policy_and_audit_keep_actor_source_task_and_redact_output(
    wire, ssh_governance_database
):
    from src.core.ingestion.adapters.cli import connect_cli_device
    from src.platform.device_access import governance

    assert ssh_governance_database is not None
    connection = connect_cli_device(
        {
            "device_id": "sw-1", "device_name": "核心交换机", "ip": "127.0.0.1",
            "vendor": "h3c", "password": "test-secret",
        },
        actor_id="operator-1",
        source_app="inspection",
        task_id="patrol-1",
    )
    assert "test-secret" not in connection.send_command("display version")
    governance.save_policy(["screen-length disable"], "admin")
    with pytest.raises(DeviceAccessError) as error:
        connection.send_command("display interface")
    assert error.value.failure_code == "policy_denied"
    connection.disconnect()
    events = governance.query_audit(actor_id="operator-1", task_id="patrol-1")["items"]
    assert {event["kind"] for event in events} >= {
        "opening",
        "opened",
        "closed",
        "command_requested",
        "command_finished",
        "command_denied",
    }
    assert all(event["detail"]["source_app"] == "inspection" for event in events)
    assert all(event["detail"]["target_ref"] == "sw-1" for event in events)
    assert all(event["detail"]["device_name"] == "核心交换机" for event in events)
    assert "test-secret" not in str(events)
    assert "display interface\n" not in wire[0]


def test_legacy_shared_database_sessions_are_isolated(ssh_governance_database, tmp_path):
    from src.infrastructure.db import postgres

    with ssh_governance_database() as audit, postgres.get_db_session() as business:
        assert business.get_bind() is audit.get_bind()
        assert business.get_bind().url.database == str(tmp_path / "ssh-governance.sqlite")
    with pytest.raises(pytest.fail.Exception, match="platform database"):
        postgres.engine.connect()


def test_first_log_save_failure_never_opens_legacy_connection_and_records_close(wire, monkeypatch):
    from src.platform.device_access import governance, session_logs

    class UnavailableStorage:
        def upload_file(self, *args):
            return False

    monkeypatch.setattr(session_logs, "_storage", UnavailableStorage)
    operation = governance.SSHOperation("test", "legacy-cli", "sw", "127.0.0.1")
    with pytest.raises(DeviceAccessError) as error:
        guarded_cli.connect(operation, device_type="hp_comware", host="127.0.0.1")
    assert error.value.failure_code == "audit_unavailable"
    assert wire[0] == [] and wire[1] == []
    events = governance.query_audit(session_id=operation.session_id)["items"]
    assert {event["kind"] for event in events} == {"opening", "open_failed", "closed"}
    assert session_logs.list_logs(actor_id="test")["items"][0]["status"] == "failed"


def test_identity_probe_passes_explicit_operator_context(monkeypatch):
    from src.platform.inventory import identity_discovery as identity

    calls = []

    def connect(device, **context):
        calls.append(context)
        raise DeviceAccessError("policy_denied", "not allowed")

    monkeypatch.setattr(identity, "connect_cli_device", connect)
    result = identity.discover_device_identity(
        {"ip": "127.0.0.1"}, connector=connect, actor_id="operator-2", task_id="probe-1"
    )
    assert result.failure_code == "policy_denied"
    assert calls == [
        {"actor_id": "operator-2", "source_app": "device-management", "task_id": "probe-1"}
    ]


def test_capture_admission_overrides_caller_supplied_actor_with_authenticated_user(monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from src.apps.inspection.backend import access as inspection
    from src.apps.rule_governance.backend.access import capture_admission

    @contextmanager
    def admitted(plan_id):
        assert plan_id == "plan-1"
        yield SimpleNamespace(user_id="authenticated-user")

    monkeypatch.setattr(inspection, "diagnostic_admission", admitted)

    @capture_admission
    def work(*, execution_plan_id, **kwargs):
        return kwargs

    assert work(execution_plan_id="plan-1", ssh_actor_id="forged-user", ssh_source_app="other") == {
        "ssh_actor_id": "authenticated-user",
        "ssh_source_app": "inspection",
    }
