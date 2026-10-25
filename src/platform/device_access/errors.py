"""Stable failures for App human-intervention workflows."""

from .contracts import DeviceAccessError


class CommandExecutionError(DeviceAccessError):
    def __init__(self, failure_code: str, message: str, output: str = ""):
        super().__init__(failure_code, message)
        self.output = output


def failure_code(exc: Exception) -> str:
    if isinstance(exc, DeviceAccessError):
        return exc.failure_code
    names = {cls.__name__ for cls in type(exc).__mro__}
    message = str(exc).lower()
    if "ConcurrencyLeaseLostError" in names:
        return "concurrency_lease_lost"
    if "ConcurrencyUnavailableError" in names:
        return "concurrency_control_unavailable"
    if "ConcurrencyWaitTimeoutError" in names:
        return "concurrency_wait_timeout"
    if "AuthenticationException" in names:
        return "authentication_failed"
    if "BadHostKeyException" in names or "known_hosts" in message or "host key" in message:
        return "host_key_rejected"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, (ValueError, TypeError)):
        return "invalid_request"
    return "connection_failed"
