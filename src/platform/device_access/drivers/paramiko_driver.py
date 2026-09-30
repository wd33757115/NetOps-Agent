"""Independent network CLI implementation over Paramiko, with no Netmiko dependency.

Prompt/paging behavior is supplied by server-controlled connection profiles.
The same SSH transport can later support a separate human terminal channel.
"""

from __future__ import annotations

import codecs
import re
import time
from importlib.metadata import version
from pathlib import Path
from threading import Event, Timer

from ..catalog import TERMINAL_PREPARATION
from ..contracts import DeviceAccessError
from ..errors import CommandExecutionError, failure_code
from ..governance import SSHOperation, current_operation

_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")


class CLISession:
    """Private driver session. A timed-out stream must never be reused."""

    def __init__(self, client, channel, profile, *, operation=None, owns_operation=False):
        self.client, self.channel, self.profile = client, channel, profile
        self.prompt = None
        self.usable = True
        self.initialization_commands = ()
        self.operation = operation or current_operation()
        self.owns_operation, self.phase = owns_operation, "command"

    def _read_to_prompt(self, timeout, *, initial=False):
        deadline = time.monotonic() + timeout
        decoder = codecs.getincrementaldecoder(self.profile.encoding)(errors="replace")
        output = ""
        size = 0
        pattern = (
            re.compile(self.profile.prompt_pattern)
            if initial
            else re.compile(rf"(?m)^{re.escape(self.prompt)} *$")
        )
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CommandExecutionError(
                    "timeout", "SSH prompt was not received before timeout", output
                )
            self.channel.settimeout(remaining)
            try:
                chunk = self.channel.recv(min(65536, self.profile.max_output_bytes - size + 1))
            except TimeoutError:
                raise CommandExecutionError(
                    "timeout", "SSH prompt was not received before timeout", output
                ) from None
            if not chunk:
                raise CommandExecutionError(
                    "connection_closed", "SSH channel closed before prompt", output
                )
            size += len(chunk)
            if size > self.profile.max_output_bytes:
                allowed = len(chunk) - (size - self.profile.max_output_bytes)
                output += decoder.decode(chunk[:allowed])
                raise CommandExecutionError(
                    "output_limit", "SSH output exceeds configured byte limit", output
                )
            output += decoder.decode(chunk)
            # Comware may prefix only the first prompt with terminal NUL padding.
            # It is not part of the hostname and must not become the remembered prompt.
            normalized = _ANSI.sub("", output).replace("\x00", "").replace("\r", "")
            if any(marker in normalized for marker in self.profile.paging_markers):
                raise CommandExecutionError(
                    "output_incomplete", "SSH output stopped at a paging prompt", output
                )
            matches = list(pattern.finditer(normalized))
            if matches and not normalized[matches[-1].end() :].strip():
                match = matches[-1]
                if initial:
                    self.prompt = match.group().strip()
                return normalized[: match.start()]

    def initialize(self):
        self._read_to_prompt(self.profile.prompt_timeout, initial=True)

    def find_prompt(self):
        return self.prompt

    def send_command(self, command, read_timeout):
        if self.operation is None:
            raise DeviceAccessError("policy_unavailable", "SSH operation context is required")
        return self.operation.execute(
            command,
            lambda: self._send_command(command, read_timeout),
            phase=self.phase,
        )

    def _send_command(self, command, read_timeout):
        if not self.usable:
            raise CommandExecutionError("connection_closed", "SSH channel is no longer usable")
        deadline = time.monotonic() + read_timeout
        try:
            self.channel.settimeout(read_timeout)
            self.operation.check_command(command, phase=self.phase)
            self.channel.sendall((command + "\n").encode(self.profile.encoding))
            output = self._read_to_prompt(max(0, deadline - time.monotonic()))
            lines = output.splitlines(keepends=True)
            # Remove only the exact first-line command echo, never matching body lines.
            if lines and lines[0].strip() == command.strip():
                output = "".join(lines[1:])
            return output
        except Exception:
            self.usable = False
            raise

    def disconnect(self):
        self.usable = False
        try:
            self.channel.close()
        except (EOFError, OSError):
            # The peer can close between channel EOF and our close acknowledgement.
            pass
        finally:
            self.client.close()
            if self.owns_operation:
                self.operation.event("disconnected", driver="paramiko")


class ParamikoDriver:
    name = "paramiko"

    def __init__(self, *, host_key_verifier=None):
        self.host_key_verifier = host_key_verifier

    @property
    def version(self):
        return version("paramiko")

    def connect(self, target, profile, *, interactive=False):
        import paramiko

        from src.platform.inventory.credentials import CredentialResolver

        if not profile.ssh_strict:
            raise DeviceAccessError("host_key_rejected", "strict host-key verification is required")
        if profile.platform is not None and profile.platform not in TERMINAL_PREPARATION:
            raise DeviceAccessError(
                "platform_unsupported", "unknown terminal initialization platform"
            )
        if (
            self.host_key_verifier is None
            and profile.known_hosts_file
            and not Path(profile.known_hosts_file).is_file()
        ):
            raise DeviceAccessError(
                "host_key_rejected", "configured known-hosts file is unavailable"
            )
        if not target.host:
            raise DeviceAccessError("invalid_target", "device management address is required")
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
        client = paramiko.SSHClient()
        expired = Event()

        def expire():
            expired.set()
            client.close()

        # PTY/shell request acknowledgement is not covered by recv's timeout.
        # Closing the transport unblocks those waits as well as a stalled login.
        timer = Timer(profile.session_setup_timeout, expire)
        deadline = time.monotonic() + profile.session_setup_timeout
        timer.daemon = True
        try:
            if owns_operation:
                operation.event("connecting", driver="paramiko")
            timer.start()
            if self.host_key_verifier is None:
                client.load_system_host_keys()
                if profile.known_hosts_file:
                    client.load_host_keys(profile.known_hosts_file)
                client.set_missing_host_key_policy(paramiko.RejectPolicy())
            else:
                from ..host_keys import DatabaseHostKeyPolicy

                client.set_missing_host_key_policy(
                    DatabaseHostKeyPolicy(target, profile, self.host_key_verifier)
                )
            client.connect(
                hostname=target.host,
                port=profile.port,
                username=profile.username,
                password=password,
                timeout=profile.conn_timeout,
                auth_timeout=profile.auth_timeout,
                banner_timeout=profile.banner_timeout,
                channel_timeout=profile.prompt_timeout,
                allow_agent=False,
                look_for_keys=False,
            )
            channel = client.invoke_shell(term="vt100", width=200, height=1000)
            session = CLISession(
                client, channel, profile, operation=operation, owns_operation=owns_operation
            )
            if not interactive:
                session.initialize()
                session.phase = "setup"
                for command in TERMINAL_PREPARATION.get(profile.platform, ()):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise DeviceAccessError("timeout", "terminal initialization timed out")
                    output = session.send_command(command, min(profile.prompt_timeout, remaining))
                    if re.search(
                        r"(?i)(unrecognized command|incomplete command|wrong parameter|"
                        r"%\s*invalid|not supported)",
                        output,
                    ):
                        raise DeviceAccessError(
                            "platform_unsupported", "terminal initialization rejected"
                        )
                    session.initialization_commands += (command,)
                session.phase = "command"
            if owns_operation:
                operation.event("connected", driver="paramiko")
            return session
        except Exception as exc:
            client.close()
            if owns_operation:
                try:
                    operation.event(
                        "connection_failed", status="failed", failure_code=failure_code(exc)
                    )
                finally:
                    operation.event("disconnected", driver="paramiko")
            raise
        finally:
            timer.cancel()
            if timer.ident is not None:
                timer.join(timeout=1)
            if expired.is_set():
                client.close()
                raise DeviceAccessError("timeout", "SSH session setup exceeded its time budget")
