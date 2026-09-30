"""Bounded Netmiko adapter. No inspection imports, implicit enable, or config writes."""

import math
import re
import tempfile
import time
from contextlib import contextmanager
from dataclasses import replace
from importlib.metadata import version
from pathlib import Path
from threading import Event, Timer

import paramiko

from ..catalog import TERMINAL_PREPARATION
from ..contracts import DeviceAccessError
from ..errors import CommandExecutionError, failure_code
from ..governance import SSHOperation, current_operation
from ..policy import validate_profile

DEVICE_TYPES = {"comware": "hp_comware", "nxos": "cisco_nxos", "ios": "cisco_ios", "vrp": "huawei"}
PREPARATION = {platform: frozenset(commands) for platform, commands in TERMINAL_PREPARATION.items()}


class _Guard:
    def __init__(self, profile, operation=None):
        self.profile = profile
        self.operation = operation or current_operation()
        self.setup_ticket = None
        self.setup_offset = 0
        self.reset(profile.session_setup_timeout, frozenset())

    def reset(self, seconds, allowed, *, phase="command"):
        self.deadline = time.monotonic() + seconds
        self.allowed = allowed
        self.raw = bytearray()
        self.writes = []
        self.input_required = False
        self.phase = phase

    def finish_setup(self, *, status="succeeded", failure_code=None):
        if self.setup_ticket is not None:
            ticket, self.setup_ticket = self.setup_ticket, None
            self.operation.after_command(
                ticket,
                output=self.output()[self.setup_offset :],
                status=status,
                failure_code=failure_code,
            )

    def check(self):
        if time.monotonic() >= self.deadline:
            raise CommandExecutionError("timeout", "Netmiko operation timed out", self.output())

    def output(self):
        value = bytes(self.raw).decode(self.profile.encoding, errors="replace")
        if self.profile.password:
            value = value.replace(self.profile.password, "[redacted]")
        return value

    def receive(self, raw):
        self.check()
        remaining = self.profile.max_output_bytes - len(self.raw)
        self.raw.extend(raw[:remaining])
        if len(raw) > remaining:
            raise CommandExecutionError(
                "output_limit", "Netmiko output limit exceeded", self.output()
            )
        tail = self.output()[-4096:]
        if re.search(r"(?i)(new password\s*:|password.*expired|change now|\[Y/N\])", tail):
            self.input_required = True
        if any(marker in tail for marker in self.profile.paging_markers):
            raise CommandExecutionError(
                "output_incomplete", "unexpected paging prompt", self.output()
            )

    def write(self, raw):
        self.check()
        text = raw.decode(self.profile.encoding) if isinstance(raw, bytes) else raw
        command = text.strip("\r\n")
        if self.input_required:
            raise DeviceAccessError(
                "waiting_for_input", "interactive device input requires a human"
            )
        if command and command not in self.allowed:
            raise DeviceAccessError("policy_denied", "driver attempted an unapproved command")
        if command:
            if self.operation is None:
                raise DeviceAccessError("policy_unavailable", "SSH operation context is required")
            if self.phase == "setup":
                self.finish_setup()
                self.setup_ticket = self.operation.before_command(command, phase="setup")
                self.operation.check_command(command, phase="setup")
                self.setup_offset = len(self.output())
            else:
                self.operation.check_command(command)
            self.writes.append(command)


class _Channel:
    def __init__(self, channel, guard):
        self._channel, self._guard = channel, guard

    def __getattr__(self, name):
        return getattr(self._channel, name)

    def recv(self, size):
        self._guard.check()
        raw = self._channel.recv(
            min(size, max(1, self._guard.profile.max_output_bytes - len(self._guard.raw) + 1))
        )
        self._guard.receive(raw)
        return raw

    def sendall(self, raw):
        self._guard.write(raw)
        return self._channel.sendall(raw)


class _Client(paramiko.SSHClient):
    def __init__(self, guard):
        super().__init__()
        self._guard = guard

    def invoke_shell(self, *args, **kwargs):
        return _Channel(super().invoke_shell(*args, **kwargs), self._guard)


class _ReadOnlyMixin:
    @staticmethod
    def _check_preparation(output):
        if re.search(
            r"(?i)(unrecognized command|incomplete command|wrong parameter|"
            r"%\s*invalid|not supported)",
            output,
        ):
            raise DeviceAccessError("platform_unsupported", "terminal initialization rejected")
        return output

    def disable_paging(self, *args, **kwargs):
        return self._check_preparation(super().disable_paging(*args, **kwargs))

    def set_terminal_width(self, *args, **kwargs):
        return self._check_preparation(super().set_terminal_width(*args, **kwargs))

    def read_channel(self):
        # Strip terminal padding after decoding, so a NUL-only packet isn't EOF.
        return super().read_channel().replace("\x00", "")

    def _get_ssh_client_instance(self):
        return self._bounded_client

    def cleanup(self, *args, **kwargs):
        # Vendor cleanup may exit modes or send CLI. Close the transport directly.
        return None

    def enable(self, *args, **kwargs):
        raise DeviceAccessError("policy_denied", "automatic enable mode is forbidden")

    def config_mode(self, *args, **kwargs):
        raise DeviceAccessError("policy_denied", "configuration mode is forbidden")

    def send_config_set(self, *args, **kwargs):
        raise DeviceAccessError("policy_denied", "configuration commands are forbidden")


class NetmikoSession:
    def __init__(self, connection, guard, prompt, *, owns_operation=False):
        self._connection, self._guard, self._prompt = connection, guard, prompt
        self._usable = True
        self.initialization_commands = tuple(guard.writes)
        self.owns_operation = owns_operation

    def find_prompt(self):
        return self._prompt

    def send_command(self, command, read_timeout):
        if self._guard.operation is None:
            raise DeviceAccessError("policy_unavailable", "SSH operation context is required")
        return self._guard.operation.execute(
            command,
            lambda: self._send_command(command, read_timeout),
        )

    def _send_command(self, command, read_timeout):
        if not self._usable:
            raise DeviceAccessError("connection_closed", "Netmiko session is no longer usable")
        if (
            not isinstance(command, str)
            or not command.strip()
            or any(c in command for c in "\r\n\x00")
            or type(read_timeout) not in (int, float)
            or not math.isfinite(read_timeout)
            or read_timeout <= 0
        ):
            raise DeviceAccessError("invalid_request", "invalid Netmiko command or timeout")
        self._guard.reset(read_timeout, frozenset({command}))
        expired = Event()

        def expire():
            expired.set()
            self.disconnect()

        timer = Timer(read_timeout, expire)
        timer.daemon = True
        timer.start()
        try:
            output = self._connection.send_command(
                command,
                read_timeout=read_timeout,
                auto_find_prompt=False,
                expect_string=rf"(?m)^{re.escape(self._prompt)}\s*$",
                use_textfsm=False,
                use_ttp=False,
                use_genie=False,
                strip_prompt=True,
                strip_command=True,
            )
            self._guard.check()
            output = str(output)
            secret = self._guard.profile.password
            return output.replace(secret, "[redacted]") if secret else output
        except Exception as exc:
            self.disconnect()
            code = failure_code(exc)
            if expired.is_set() or (
                code == "connection_failed"
                and type(exc).__name__ in {"ReadTimeout", "NetmikoTimeoutException"}
            ):
                code = "timeout"
            raise CommandExecutionError(
                code, "Netmiko command failed", self._guard.output()
            ) from None
        finally:
            timer.cancel()
            timer.join(timeout=1)

    def disconnect(self):
        was_usable = self._usable
        self._usable = False
        try:
            self._connection.disconnect()
        finally:
            if self.owns_operation and was_usable:
                self._guard.operation.event("disconnected", driver="netmiko")


class NetmikoDriver:
    name = "netmiko"

    def __init__(self, platform, *, known_hosts_provider=None):
        if platform not in DEVICE_TYPES:
            raise DeviceAccessError(
                "platform_unsupported", "platform must be identified before Netmiko"
            )
        self.platform, self.known_hosts_provider = platform, known_hosts_provider

    @property
    def version(self):
        return version("netmiko")

    @property
    def preparation_commands(self):
        return tuple(sorted(PREPARATION[self.platform]))

    @contextmanager
    def _known_hosts(self, target, profile):
        if self.known_hosts_provider is None:
            if not profile.known_hosts_file or not Path(profile.known_hosts_file).is_file():
                raise DeviceAccessError(
                    "host_key_rejected", "Netmiko requires an explicit known-hosts file"
                )
            yield profile.known_hosts_file
        else:
            content = self.known_hosts_provider(target, profile.port)
            if not isinstance(content, str) or not content.strip():
                raise DeviceAccessError("host_key_rejected", "no pinned host key available")
            with tempfile.TemporaryDirectory(prefix="netops-known-hosts-") as directory:
                path = Path(directory) / "known_hosts"
                path.write_text(content, encoding="utf-8")
                yield str(path)

    def connect(self, target, profile, *, interactive=False):
        if interactive:
            raise DeviceAccessError("invalid_request", "human terminals use the Paramiko driver")
        validate_profile(profile)
        if profile.platform is not None and profile.platform != self.platform:
            raise DeviceAccessError(
                "platform_unsupported", "driver and identified platform disagree"
            )
        from netmiko.ssh_dispatcher import ssh_dispatcher

        from src.platform.inventory.credentials import CredentialResolver

        password = profile.password or CredentialResolver().resolve_password(profile.credential_ref)
        if not password:
            raise DeviceAccessError("credential_unavailable", "device credential is unavailable")
        operation = current_operation()
        owns_operation = operation is None
        operation = operation or SSHOperation(
            "system",
            "device-access-driver",
            target.target_ref,
            target.host,
            profile.port,
            secrets=(password,),
        )
        if password not in operation.secrets:
            operation.secrets += (password,)
        guard = _Guard(replace(profile, password=password, credential_ref=None), operation)
        guard.reset(profile.session_setup_timeout, PREPARATION[self.platform], phase="setup")
        client = _Client(guard)
        connection = None
        expired = Event()

        def expire():
            expired.set()
            client.close()

        timer = Timer(profile.session_setup_timeout, expire)
        timer.daemon = True
        try:
            if owns_operation:
                operation.event("connecting", driver="netmiko")
            timer.start()
            with self._known_hosts(target, profile) as known_hosts:
                base = ssh_dispatcher(DEVICE_TYPES[self.platform])
                cls = type("ReadOnlyNetmiko", (_ReadOnlyMixin, base), {})
                connection = cls(
                    host=target.host,
                    port=profile.port,
                    username=profile.username,
                    password=password,
                    device_type=DEVICE_TYPES[self.platform],
                    ssh_strict=True,
                    system_host_keys=False,
                    alt_host_keys=True,
                    alt_key_file=known_hosts,
                    allow_agent=False,
                    use_keys=False,
                    conn_timeout=profile.conn_timeout,
                    auth_timeout=profile.auth_timeout,
                    banner_timeout=profile.banner_timeout,
                    blocking_timeout=profile.prompt_timeout,
                    session_timeout=profile.session_setup_timeout,
                    encoding=profile.encoding,
                    auto_connect=False,
                    session_log=None,
                    allow_auto_change=False,
                    secret="",
                )
                connection._bounded_client = client
                connection._open()
                prompt = connection.find_prompt().replace("\x00", "").strip()
                if not re.fullmatch(profile.prompt_pattern, prompt):
                    raise DeviceAccessError(
                        "waiting_for_input", "device prompt needs human confirmation"
                    )
                guard.check()
                guard.finish_setup()
                if owns_operation:
                    operation.event("connected", driver="netmiko")
                return NetmikoSession(connection, guard, prompt, owns_operation=owns_operation)
        except Exception as exc:
            if connection is not None:
                connection.disconnect()
            client.close()
            code = failure_code(exc)
            if expired.is_set() or (
                code == "connection_failed"
                and type(exc).__name__ in {"ReadTimeout", "NetmikoTimeoutException"}
            ):
                code = "timeout"
            guard.finish_setup(status="failed", failure_code=code)
            if owns_operation:
                try:
                    operation.event("connection_failed", status="failed", failure_code=code)
                finally:
                    operation.event("disconnected", driver="netmiko")
            raise DeviceAccessError(code, "Netmiko connection failed") from None
        finally:
            timer.cancel()
            if timer.ident is not None:
                timer.join(timeout=1)
            if expired.is_set():
                client.close()
                raise DeviceAccessError("timeout", "Netmiko session setup budget exceeded")
