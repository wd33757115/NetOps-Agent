"""Real Paramiko client/server tests on loopback; no external network devices."""

import socket
from contextlib import contextmanager
from threading import Event, Thread

import paramiko
import pytest

from src.platform.device_access.contracts import (
    AccessContext,
    AccessGrant,
    BatchRequest,
    Command,
    ConnectionProfile,
    DeviceAccessError,
    Target,
)
from src.platform.device_access.drivers.paramiko_driver import CLISession
from src.platform.device_access.errors import CommandExecutionError
from src.platform.device_access.service import DeviceAccessService


class LocalAdmission:
    @contextmanager
    def hold(self, target, profile):
        yield lambda: None


def test_disconnect_after_peer_eof_still_closes_transport():
    from types import SimpleNamespace

    closed = []

    def peer_closed():
        raise EOFError()

    connection = CLISession(
        SimpleNamespace(close=lambda: closed.append(True)),
        SimpleNamespace(close=peer_closed),
        ConnectionProfile("test", "operator"),
    )
    connection.disconnect()
    assert closed == [True] and not connection.usable


@pytest.mark.parametrize(
    "platform,expected",
    [
        (None, []),
        ("comware", ["screen-length disable"]),
        ("nxos", ["terminal width 511", "terminal length 0"]),
        ("ios", ["terminal width 511", "terminal length 0"]),
        ("vrp", ["screen-length 0 temporary"]),
    ],
)
def test_identified_platform_prepares_terminal_before_collection(tmp_path, platform, expected):
    from src.platform.device_access.drivers.paramiko_driver import ParamikoDriver

    with ssh_server(tmp_path) as (port, known, commands, _):
        connection = ParamikoDriver().connect(
            Target("switch", "127.0.0.1"),
            ConnectionProfile(
                "manual",
                "operator",
                "password",
                port=port,
                known_hosts_file=str(known),
                platform=platform,
            ),
        )
        try:
            assert list(connection.initialization_commands) == expected
            connection.send_command("display version", 2)
            assert commands == expected + ["display version"]
        finally:
            connection.disconnect()


def test_rejected_terminal_preparation_stops_before_collection(tmp_path):
    from src.platform.device_access.drivers.paramiko_driver import ParamikoDriver

    with ssh_server(tmp_path, responses={"screen-length disable": "Unrecognized command"}) as (
        port,
        known,
        commands,
        _,
    ):
        with pytest.raises(DeviceAccessError) as error:
            ParamikoDriver().connect(
                Target("switch", "127.0.0.1"),
                ConnectionProfile(
                    "manual",
                    "operator",
                    "password",
                    port=port,
                    known_hosts_file=str(known),
                    platform="comware",
                ),
            )
        assert error.value.failure_code == "platform_unsupported"
        assert commands == ["screen-length disable"]


def test_real_human_terminal_preserves_banner_and_closes(tmp_path):
    with ssh_server(tmp_path) as (port, known_hosts, commands, _):
        access = DeviceAccessService(
            target_resolver=lambda _: Target("switch", "127.0.0.1"),
            profile_resolver=lambda _: ConnectionProfile(
                "manual", "operator", "password", port=port, known_hosts_file=str(known_hosts)
            ),
            admission=LocalAdmission(),
        )
        context = AccessContext(
            "user", "device-access", (AccessGrant("switch", "manual", action="device.terminal"),)
        )
        with access.terminal(context, "switch", "manual") as (channel, guard):
            guard()
            channel.settimeout(2)
            assert b"Welcome" in channel.recv(4096)
            channel.sendall(b"display version\n")
            assert b"result for display version" in channel.recv(4096)
        assert channel.closed
        assert commands == ["display version"]


def test_real_human_terminal_reports_a_rejected_login_as_authentication_failed(tmp_path):
    with ssh_server(tmp_path) as (port, known_hosts, commands, _):
        access = DeviceAccessService(
            target_resolver=lambda _: Target("switch", "127.0.0.1"),
            profile_resolver=lambda _: ConnectionProfile(
                "manual", "operator", "wrong-secret", port=port, known_hosts_file=str(known_hosts)
            ),
            admission=LocalAdmission(),
        )
        context = AccessContext(
            "user", "device-access", (AccessGrant("switch", "manual", action="device.terminal"),)
        )
        with pytest.raises(DeviceAccessError) as error:
            with access.terminal(context, "switch", "manual"):
                pytest.fail("rejected login opened a channel")
        assert error.value.failure_code == "authentication_failed"
        assert "wrong-secret" not in str(error.value)
        assert commands == []


def test_terminal_authorization_rechecked_after_admission_before_connect():
    calls = []
    access = DeviceAccessService(
        target_resolver=lambda _: Target("switch", "127.0.0.1"),
        profile_resolver=lambda _: ConnectionProfile("manual", "operator", "password"),
        driver=type("Driver", (), {"connect": lambda *args, **kwargs: calls.append(True)})(),
        admission=LocalAdmission(),
    )
    context = AccessContext(
        "user", "device-access", (AccessGrant("switch", "manual", action="device.terminal"),)
    )
    with pytest.raises(DeviceAccessError, match="cancelled"):
        with access.terminal(context, "switch", "manual", should_continue=lambda: False):
            pytest.fail("cancelled channel opened")
    assert not calls


@contextmanager
def ssh_server(
    tmp_path, *, known=True, password="password", responses=None, banner=b"Welcome\r\n<SW1>"
):
    key = paramiko.RSAKey.generate(2048)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(5)
    port = listener.getsockname()[1]
    stopped, shell = Event(), Event()
    commands, authentications, errors = [], [], []
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text(
        f"[127.0.0.1]:{port} {key.get_name()} {key.get_base64()}\n" if known else "",
        encoding="utf-8",
    )

    class Server(paramiko.ServerInterface):
        def check_auth_password(self, username, supplied_password):
            authentications.append(username)
            return (
                paramiko.AUTH_SUCCESSFUL if supplied_password == password else paramiko.AUTH_FAILED
            )

        def get_allowed_auths(self, username):
            return "password"

        def check_channel_request(self, kind, chanid):
            return (
                paramiko.OPEN_SUCCEEDED
                if kind == "session"
                else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED
            )

        def check_channel_pty_request(
            self, channel, term, width, height, pixelwidth, pixelheight, modes
        ):
            return True

        def check_channel_shell_request(self, channel):
            shell.set()
            return True

    def serve():
        transport = None
        try:
            sock, _ = listener.accept()
            transport = paramiko.Transport(sock)
            transport.add_server_key(key)
            transport.start_server(server=Server())
            channel = transport.accept(3)
            if channel is None or not shell.wait(3):
                return
            channel.settimeout(0.2)
            channel.sendall(banner)
            pending = b""
            while not stopped.is_set():
                try:
                    data = channel.recv(4096)
                except TimeoutError:
                    continue
                if not data:
                    break
                pending += data
                while b"\n" in pending:
                    command, pending = pending.split(b"\n", 1)
                    commands.append(command.decode())
                    output = (responses or {}).get(
                        command.decode(), "result for " + command.decode()
                    )
                    channel.sendall(command + b"\r\n" + output.encode() + b"\r\n<SW1>")
        except (EOFError, OSError):
            pass
        except Exception as exc:
            errors.append(exc)
        finally:
            if transport is not None:
                transport.close()

    thread = Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield port, known_hosts, commands, authentications
    finally:
        stopped.set()
        listener.close()
        thread.join(6)
        assert not thread.is_alive()
        assert not errors


def test_comware_nul_padded_first_prompt_does_not_break_next_command(tmp_path):
    from src.platform.device_access.drivers.paramiko_driver import ParamikoDriver

    with ssh_server(tmp_path, banner=b"Welcome\r\n\x00<SW1>") as (port, known, commands, _):
        connection = ParamikoDriver().connect(
            Target("switch", "127.0.0.1"),
            ConnectionProfile(
                "manual",
                "operator",
                "password",
                port=port,
                known_hosts_file=str(known),
                prompt_timeout=2,
            ),
        )
        try:
            assert connection.find_prompt() == "<SW1>"
            assert connection.send_command("screen-length disable", 2) == (
                "result for screen-length disable\n"
            )
            assert "display version" in connection.send_command("display version", 2)
        finally:
            connection.disconnect()
        assert commands == ["screen-length disable", "display version"]


def execute(port, known_hosts):
    target = Target("switch", "127.0.0.1")
    profile = ConnectionProfile(
        "manual",
        "operator",
        password="password",
        port=port,
        known_hosts_file=str(known_hosts),
        prompt_timeout=2,
    )
    service = DeviceAccessService(
        target_resolver=lambda ref: target,
        profile_resolver=lambda ref: profile,
        admission=LocalAdmission(),
    )
    request = BatchRequest(
        "switch", "manual", (Command("version", "show version"), Command("lldp", "show lldp"))
    )
    context = AccessContext(
        "user",
        "topology",
        (AccessGrant("switch", "manual", frozenset(c.text for c in request.commands)),),
    )
    return service.execute_batch(context, request)


def test_real_ssh_batch_with_manual_password_and_known_host(tmp_path):
    with ssh_server(tmp_path) as (port, known_hosts, commands, authentications):
        result = execute(port, known_hosts)
        assert result.execution_status == "succeeded"
        assert result.results[0].output == "result for show version\n"
        assert result.results[1].output == "result for show lldp\n"
        assert result.results[0].driver == "paramiko"
        assert commands == ["show version", "show lldp"]
        assert authentications == ["operator"]


def test_real_unknown_host_sends_no_password(tmp_path):
    with ssh_server(tmp_path, known=False) as (port, known_hosts, commands, authentications):
        with pytest.raises(DeviceAccessError) as caught:
            execute(port, known_hosts)
        assert caught.value.failure_code == "host_key_rejected"
        assert not authentications and not commands


def test_real_authentication_failure_is_single_attempt(tmp_path):
    with ssh_server(tmp_path, password="different") as (
        port,
        known_hosts,
        commands,
        authentications,
    ):
        with pytest.raises(DeviceAccessError) as caught:
            execute(port, known_hosts)
        assert caught.value.failure_code == "authentication_failed"
        assert authentications == ["operator"] and not commands


def test_shell_setup_deadline_closes_transport(monkeypatch):
    from src.platform.device_access.drivers.paramiko_driver import ParamikoDriver

    closed = Event()

    class StalledClient:
        def load_system_host_keys(self):
            pass

        def set_missing_host_key_policy(self, policy):
            assert isinstance(policy, paramiko.RejectPolicy)

        def connect(self, **kwargs):
            assert kwargs["allow_agent"] is False
            assert kwargs["look_for_keys"] is False

        def invoke_shell(self, **kwargs):
            assert closed.wait(1), "session setup timer did not close transport"
            raise paramiko.SSHException("transport closed")

        def close(self):
            closed.set()

    monkeypatch.setattr(paramiko, "SSHClient", StalledClient)
    profile = ConnectionProfile(
        "manual", "operator", password="password", session_setup_timeout=0.03
    )
    with pytest.raises(DeviceAccessError) as caught:
        ParamikoDriver().connect(Target("switch", "192.0.2.1"), profile)
    assert caught.value.failure_code == "timeout"
    assert closed.is_set()


class Channel:
    def __init__(self, chunks):
        self.chunks = iter(chunks)
        self.sent = []

    def recv(self, size):
        item = next(self.chunks, b"")
        if isinstance(item, Exception):
            raise item
        return item

    def sendall(self, data):
        self.sent.append(data)

    def settimeout(self, timeout):
        assert timeout > 0


def session(chunks, **profile_options):
    from src.platform.device_access.governance import SSHOperation

    profile = ConnectionProfile("manual", "operator", **profile_options)
    result = CLISession(None, Channel(chunks), profile, operation=SSHOperation(
        "test", "test-driver", "fixture", "127.0.0.1",
    ))
    result.prompt = "<SW1>"
    return result


def test_prompt_across_chunks_and_utf8_boundaries():
    body = "show version\r\n交换机\r\n<SW1>".encode()
    result = session([body[:17], body[17:-2], body[-2:]])
    assert result.send_command("show version", 2) == "交换机\n"


@pytest.mark.parametrize(
    "chunks,options,code",
    [
        ([b"partial", TimeoutError()], {}, "timeout"),
        ([b"partial", b""], {}, "connection_closed"),
        ([b"partial --More--"], {}, "output_incomplete"),
        ([b"123456"], {"max_output_bytes": 5}, "output_limit"),
    ],
)
def test_incomplete_output_is_preserved_and_channel_is_not_reused(chunks, options, code):
    result = session(chunks, **options)
    with pytest.raises(CommandExecutionError) as caught:
        result.send_command("show lldp", 2)
    assert caught.value.failure_code == code
    assert caught.value.output
    with pytest.raises(CommandExecutionError, match="no longer usable"):
        result.send_command("show next", 2)
    assert len(result.channel.sent) == 1


@pytest.mark.parametrize("vendor", ["comware", "nxos"])
def test_real_ssh_platform_detection_and_lldp_share_one_login(tmp_path, vendor):
    from src.platform.device_access.detection import DiscoveryPlan

    responses = {
        "display version": (
            "H3C Comware Software, Version 7.1" if vendor == "comware" else "% Invalid command"
        ),
        "show version": "Cisco Nexus Operating System (NX-OS) Software",
    }
    with ssh_server(tmp_path, responses=responses) as (
        port,
        known_hosts,
        commands,
        authentications,
    ):
        plan = DiscoveryPlan()
        access = DeviceAccessService(
            target_resolver=lambda _: Target("switch", "127.0.0.1"),
            profile_resolver=lambda _: ConnectionProfile(
                "detect", "operator", "password", port=port, known_hosts_file=str(known_hosts)
            ),
            admission=LocalAdmission(),
        )
        context = AccessContext(
            "user",
            "topology",
            (AccessGrant("switch", "detect", frozenset(c.text for c in plan.commands)),),
        )
        result = access.execute_batch(
            context, BatchRequest("switch", "detect", plan.commands), command_filter=plan.select
        )
        assert result.execution_status == "succeeded"
        assert plan.profile_id == vendor + "-lldp-v1"
        assert authentications == ["operator"]
        assert commands == (
            [
                "display version",
                "screen-length disable",
                "display lldp neighbor-information verbose",
            ]
            if vendor == "comware"
            else [
                "display version",
                "show version",
                "terminal length 0",
                "show lldp neighbors detail",
            ]
        )
