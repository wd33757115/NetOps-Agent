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

from ..contracts import DeviceAccessError
from ..errors import CommandExecutionError

_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")


class CLISession:
    """Private driver session. A timed-out stream must never be reused."""

    def __init__(self, client, channel, profile):
        self.client, self.channel, self.profile = client, channel, profile
        self.prompt = None
        self.usable = True

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
            normalized = _ANSI.sub("", output).replace("\r", "")
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
        if not self.usable:
            raise CommandExecutionError("connection_closed", "SSH channel is no longer usable")
        deadline = time.monotonic() + read_timeout
        try:
            self.channel.settimeout(read_timeout)
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
        finally:
            self.client.close()


class ParamikoDriver:
    name = "paramiko"

    @property
    def version(self):
        return version("paramiko")

    def connect(self, target, profile, *, interactive=False):
        import paramiko

        from src.platform.inventory.credentials import CredentialResolver

        if not profile.ssh_strict:
            raise DeviceAccessError("host_key_rejected", "strict host-key verification is required")
        if profile.known_hosts_file and not Path(profile.known_hosts_file).is_file():
            raise DeviceAccessError(
                "host_key_rejected", "configured known-hosts file is unavailable"
            )
        if not target.host:
            raise DeviceAccessError("invalid_target", "device management address is required")
        password = profile.password or CredentialResolver().resolve_password(profile.credential_ref)
        if not password:
            raise DeviceAccessError("credential_unavailable", "device credential is unavailable")
        client = paramiko.SSHClient()
        expired = Event()

        def expire():
            expired.set()
            client.close()

        # PTY/shell request acknowledgement is not covered by recv's timeout.
        # Closing the transport unblocks those waits as well as a stalled login.
        timer = Timer(profile.session_setup_timeout, expire)
        timer.daemon = True
        timer.start()
        try:
            client.load_system_host_keys()
            if profile.known_hosts_file:
                client.load_host_keys(profile.known_hosts_file)
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
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
            session = CLISession(client, channel, profile)
            if not interactive:
                session.initialize()
            return session
        except Exception:
            client.close()
            raise
        finally:
            timer.cancel()
            timer.join(timeout=1)
            if expired.is_set():
                client.close()
                raise DeviceAccessError("timeout", "SSH session setup exceeded its time budget")
