"""Keep the legacy Netmiko collectors behind the platform SSH command boundary."""

from __future__ import annotations

from src.platform.device_access.contracts import DeviceAccessError
from src.platform.device_access.errors import failure_code

OUTPUT_LIMIT = 256 * 1024


class _GovernedNetmiko:
    """Installed before Netmiko opens SSH, including vendor session preparation.

    This is a local subclass, never a mutation of Netmiko's global class registry.
    Only the narrow facade below escapes to collectors; raw channel writes and
    redispatch cannot be used by a caller to bypass the command boundary.
    """

    def __init__(self, *, operation, **kwargs):
        self._operation = operation
        self._preparing = True
        self._active = None
        self._setup = None
        self._captured = ""
        self._governed_closed = False
        super().__init__(auto_connect=False, **kwargs)

    def _finish_setup(self, *, status="succeeded", code=None):
        if self._setup is not None:
            ticket, self._setup = self._setup, None
            self._operation.after_command(
                ticket, output=self._captured, status=status, failure_code=code
            )
            self._captured = ""

    def write_channel(self, out_data):
        if not isinstance(out_data, str):
            raise DeviceAccessError("invalid_request", "SSH input must be text")
        command = out_data.strip("\r\n")
        # Netmiko's prompt discovery sends empty returns; they are framing, not commands.
        if command:
            if self._active is not None:
                if command != self._active["command"]:
                    raise DeviceAccessError("policy_denied", "Unreviewed SSH channel write")
                self._operation.check_command(command, phase=self._active["phase"])
            elif self._preparing:
                self._finish_setup()
                self._setup = self._operation.before_command(command, phase="initialization")
                self._operation.check_command(command, phase="initialization")
            else:
                raise DeviceAccessError("policy_denied", "SSH commands require a recorded request")
        return super().write_channel(out_data)

    def read_channel(self):
        value = super().read_channel()
        if self._active is not None or self._setup is not None:
            if len(self._captured.encode()) + len(value.encode()) > OUTPUT_LIMIT:
                raise DeviceAccessError("output_limit", "SSH command output exceeded its limit")
            self._captured += value
        return value

    def send_command(self, command_string, *args, **kwargs):
        if self._active is not None or self._governed_closed:
            raise DeviceAccessError("session_busy", "SSH session is unavailable")
        self._finish_setup()
        ticket = self._operation.before_command(
            command_string, phase="initialization" if self._preparing else "command"
        )
        self._active, self._captured = ticket, ""
        try:
            output = super().send_command(command_string, *args, **kwargs)
        except Exception as exc:
            code = failure_code(exc)
            self._operation.after_command(
                ticket, output=self._captured, status="failed", failure_code=code
            )
            raise DeviceAccessError(code, "SSH command failed") from None
        else:
            self._operation.after_command(ticket, output=output)
            return self._operation.redact(output)
        finally:
            self._active, self._captured = None, ""

    def cleanup(self, *args, **kwargs):
        # Vendor cleanup may enter/leave configuration mode or issue quit/end.
        # Closing the transport needs no unrequested device commands.
        return None

    def disconnect(self):
        if self._governed_closed:
            return
        self._governed_closed = True
        try:
            super().disconnect()
        finally:
            self._operation.event("closed")


class CLIConnection:
    """Collector API; intentionally does not expose a channel or a redispatchable object."""

    def __init__(self, connection):
        self._connection = connection

    def send_command(self, command, **kwargs):
        return self._connection.send_command(command, **kwargs)

    def find_prompt(self):
        return self._connection._operation.redact(self._connection.find_prompt())

    def disconnect(self):
        self._connection.disconnect()


def connect(operation, **kwargs):
    from netmiko.ssh_dispatcher import ssh_dispatcher

    device_type = kwargs["device_type"]
    if device_type.endswith(("_telnet", "_serial")):
        raise DeviceAccessError("invalid_request", "The CLI collector requires SSH")
    connection_type = type(
        "Governed" + device_type, (_GovernedNetmiko, ssh_dispatcher(device_type)), {}
    )
    connection = None
    try:
        operation.event("opening", driver="netmiko")
        connection = connection_type(operation=operation, **kwargs)
        connection._open()
        connection._finish_setup()
        connection._preparing = False
        operation.event("opened", driver="netmiko")
        return CLIConnection(connection)
    except Exception as exc:
        code = failure_code(exc)
        try:
            operation.event("open_failed", status="failed", failure_code=code)
            if connection is not None:
                connection._finish_setup(status="failed", code=code)
        finally:
            if connection is not None:
                connection.disconnect()
            else:
                operation.event("closed", failure_code=code)
        raise DeviceAccessError(code, "SSH connection could not be opened") from None
