"""In-process contracts; never deserialize authorization context from HTTP input."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Command:
    command_id: str
    text: str
    read_timeout: float = 30


@dataclass(frozen=True)
class AccessGrant:
    target_ref: str
    connection_profile_id: str
    commands: frozenset[str] = frozenset()
    action: str = "device.collect"


@dataclass(frozen=True)
class AccessContext:
    actor_id: str
    source_app: str
    grants: tuple[AccessGrant, ...] = ()
    task_id: str | None = None


@dataclass(frozen=True)
class Target:
    target_ref: str
    host: str
    device_id: str | None = None
    management_domain: str = "default"
    site: str = "__unassigned__"
    display_name: str | None = None


@dataclass(frozen=True)
class ConnectionProfile:
    profile_id: str
    username: str
    password: str | None = field(default=None, repr=False)
    credential_ref: str | None = field(default=None, repr=False)
    port: int = 22
    conn_timeout: float = 10
    auth_timeout: float = 10
    banner_timeout: float = 15
    prompt_timeout: float = 15
    session_setup_timeout: float = 30
    prompt_pattern: str = r"(?m)^[^\r\n]{1,128}[#>] *$"
    encoding: str = "utf-8"
    max_output_bytes: int = 4 * 1024 * 1024
    paging_markers: tuple[str, ...] = ("--More--", "---- More ----")
    ssh_strict: bool = True
    known_hosts_file: str | None = None
    connection_attempts: int = 1
    retry_delay: float = 1
    platform: str | None = None


@dataclass(frozen=True)
class BatchRequest:
    target_ref: str
    connection_profile_id: str
    commands: tuple[Command, ...] = ()
    stop_on_error: bool = True
    budget_seconds: float = 120
    capture_prompt: bool = False


@dataclass(frozen=True)
class CommandResult:
    execution_id: str
    command_id: str
    started_at: str | None
    finished_at: str
    execution_status: str
    output_complete: bool
    output: str = field(default="", repr=False)
    failure_code: str | None = None
    error: str | None = None
    evidence_ref: str | None = None
    driver: str = "paramiko"
    driver_version: str = "unknown"


@dataclass(frozen=True)
class BatchResult:
    execution_id: str
    results: tuple[CommandResult, ...]
    execution_status: str
    failure_code: str | None = None
    error: str | None = None
    prompt: str | None = None


class DeviceAccessError(RuntimeError):
    def __init__(self, failure_code: str, message: str):
        super().__init__(message)
        self.failure_code = failure_code
