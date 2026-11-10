"""Independent Netmiko adapter using a real SSH server, without inspection code."""

import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.platform.device_access.contracts import ConnectionProfile, DeviceAccessError, Target
from src.platform.device_access.drivers.netmiko_driver import (
    DEVICE_TYPES,
    NetmikoDriver,
    NetmikoSession,
    _Channel,
    _Guard,
    _ReadOnlyMixin,
)
from tests.unit.test_device_access_ssh import ssh_server


def profile(port=22, known=None, **kwargs):
    return ConnectionProfile(
        "profile",
        "operator",
        "password",
        port=port,
        known_hosts_file=str(known) if known else None,
        **kwargs,
    )


def test_real_netmiko_comware_prepares_session_and_returns_raw_output(tmp_path):
    with ssh_server(tmp_path, banner=b"Welcome\r\n\x00<SW1>") as (port, known, commands, auth):
        driver = NetmikoDriver("comware")
        connection = driver.connect(Target("target", "127.0.0.1"), profile(port, known))
        try:
            assert connection.find_prompt() == "<SW1>"
            assert connection.send_command("display version", 3) == "result for display version"
            assert (
                connection.send_command("display interface brief", 3)
                == "result for display interface brief"
            )
            assert connection._connection.system_host_keys is False
            assert connection._connection.session_log is None
            assert not connection._connection.allow_auto_change
            with pytest.raises(DeviceAccessError):
                connection._connection.enable()
        finally:
            connection.disconnect()
        assert auth == ["operator"]
        assert [c for c in commands if c] == [
            "screen-length disable",
            "display version",
            "display interface brief",
        ]


def test_database_generated_known_hosts_is_temporary_and_strict(tmp_path):
    with ssh_server(tmp_path) as (port, known, _, auth):
        provided = []

        def provider(target, selected_port):
            provided.append((target.host, selected_port))
            return known.read_text()

        driver = NetmikoDriver("comware", known_hosts_provider=provider)
        connection = driver.connect(Target("target", "127.0.0.1"), profile(port))
        from pathlib import Path

        path = Path(connection._connection.alt_key_file)
        assert not path.exists()
        connection.disconnect()
        assert provided == [("127.0.0.1", port)] and auth == ["operator"]


def test_real_library_debug_logs_and_output_do_not_expose_login_secret(tmp_path, caplog):
    secret = "NETMIKO-SECRET-FOR-TEST-ONLY"
    with caplog.at_level("DEBUG", logger="netmiko"):
        with ssh_server(
            tmp_path, password=secret, responses={"display version": "echo " + secret}
        ) as (port, known, _, _):
            connection = NetmikoDriver("comware").connect(
                Target("target", "127.0.0.1"), replace(profile(port, known), password=secret)
            )
            try:
                assert connection.send_command("display version", 3) == "echo [redacted]"
            finally:
                connection.disconnect()
    assert secret not in caplog.text


def test_unknown_key_rejected_before_password(tmp_path):
    with ssh_server(tmp_path, known=False) as (port, known, _, auth):
        with pytest.raises(DeviceAccessError) as error:
            NetmikoDriver("comware").connect(Target("target", "127.0.0.1"), profile(port, known))
        assert error.value.failure_code == "host_key_rejected"
        assert not auth


def test_rejected_paging_initialization_never_opens_usable_session(tmp_path):
    with ssh_server(tmp_path, responses={"screen-length disable": "Unrecognized command"}) as (
        port,
        known,
        commands,
        _,
    ):
        with pytest.raises(DeviceAccessError) as error:
            NetmikoDriver("comware").connect(Target("target", "127.0.0.1"), profile(port, known))
        assert error.value.failure_code == "platform_unsupported"
        assert [c for c in commands if c] == ["screen-length disable"]


def test_authentication_failed_once_and_sanitized(tmp_path):
    with ssh_server(tmp_path, password="different") as (port, known, _, auth):
        with pytest.raises(DeviceAccessError) as error:
            NetmikoDriver("comware").connect(Target("target", "127.0.0.1"), profile(port, known))
        assert error.value.failure_code == "authentication_failed"
        assert "password" not in str(error.value)
        assert auth == ["operator"]


@pytest.mark.parametrize("platform", ["auto", "unknown", "hp_comware", ""])
def test_unknown_platform_cannot_select_a_driver(platform):
    with pytest.raises(DeviceAccessError):
        NetmikoDriver(platform)


def test_verified_platform_mapping():
    assert DEVICE_TYPES == {
        "comware": "hp_comware",
        "nxos": "cisco_nxos",
        "ios": "cisco_ios",
        "vrp": "huawei",
    }


def test_untrusted_profile_or_human_terminal_never_connects():
    for selected, interactive in [(replace(profile(), ssh_strict=False), False), (profile(), True)]:
        with pytest.raises(DeviceAccessError):
            NetmikoDriver("comware").connect(
                Target("target", "10.0.0.1"), selected, interactive=interactive
            )


def test_missing_explicit_trust_file_fails_closed():
    with pytest.raises(DeviceAccessError) as error:
        NetmikoDriver("comware").connect(Target("target", "10.0.0.1"), profile())
    assert error.value.failure_code == "host_key_rejected"


def test_transport_guard_bounds_stream_and_blocks_unapproved_writes():
    guard = _Guard(profile(max_output_bytes=10))
    guard.reset(1, frozenset({"display version"}))
    sent = []
    channel = _Channel(SimpleNamespace(sendall=sent.append, recv=lambda n: b"a" * n), guard)
    channel.sendall(b"display version\n")
    with pytest.raises(DeviceAccessError) as error:
        channel.sendall(b"system-view\n")
    assert error.value.failure_code == "policy_denied"
    assert sent == [b"display version\n"]
    with pytest.raises(DeviceAccessError) as error:
        channel.recv(65536)
    assert error.value.failure_code == "output_limit" and len(error.value.output) == 10


def test_guard_keeps_nul_packet_distinct_from_eof():
    guard = _Guard(profile())
    channel = _Channel(SimpleNamespace(recv=lambda n: b"\x00"), guard)
    assert channel.recv(4096) == b"\x00"


def test_driver_does_not_answer_password_change_prompt():
    guard = _Guard(profile())
    guard.receive(b"Password expired. New password:")
    with pytest.raises(DeviceAccessError) as error:
        guard.write(b"\n")
    assert error.value.failure_code == "waiting_for_input"


def test_preparation_cannot_enter_enable_or_configuration():
    connection = _ReadOnlyMixin()
    for action in (connection.enable, connection.config_mode, connection.send_config_set):
        with pytest.raises(DeviceAccessError) as error:
            action()
        assert error.value.failure_code == "policy_denied"
    assert connection.cleanup() is None


def test_read_timeout_closes_and_preserves_bounded_redacted_partial_output():
    from netmiko.exceptions import ReadTimeout

    guard = _Guard(profile())
    closed = []

    def send(*args, **kwargs):
        guard.receive(b"partial password")
        raise ReadTimeout("secret password")

    session = NetmikoSession(
        SimpleNamespace(send_command=send, disconnect=lambda: closed.append(True)), guard, "<SW1>"
    )
    with pytest.raises(DeviceAccessError) as error:
        session.send_command("display version", 1)
    assert error.value.failure_code == "timeout"
    assert error.value.output == "partial [redacted]"
    assert "password" not in str(error.value) and closed
    with pytest.raises(DeviceAccessError):
        session.send_command("display version", 1)


def test_guard_deadline_and_unexpected_paging():
    guard = _Guard(profile())
    guard.deadline = time.monotonic() - 1
    with pytest.raises(DeviceAccessError) as error:
        guard.check()
    assert error.value.failure_code == "timeout"
    guard.reset(1, frozenset())
    with pytest.raises(DeviceAccessError) as error:
        guard.receive(b"body\n--More--")
    assert error.value.failure_code == "output_incomplete"


@pytest.mark.parametrize(
    "command,timeout",
    [
        ("reboot\ndisplay version", 1),
        ("", 1),
        ("display version", -1),
        ("display version", float("nan")),
    ],
)
def test_invalid_run_input_never_writes(command, timeout):
    session = NetmikoSession(
        SimpleNamespace(send_command=lambda *a, **kw: pytest.fail("sent CLI")),
        _Guard(profile()),
        "<SW1>",
    )
    with pytest.raises(DeviceAccessError):
        session.send_command(command, timeout)
