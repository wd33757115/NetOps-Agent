"""One node budget across unknown-platform probing and identified collection."""

import math
import time
from dataclasses import dataclass, field, replace

from src.platform.device_access.contracts import BatchRequest, Command, DeviceAccessError
from src.platform.device_access.drivers.netmiko_driver import NetmikoDriver
from src.platform.device_access.policy import authorize
from src.platform.device_access.service import DeviceAccessService

from .identify import PROBES, Identity, Probe, identify
from .parse import ParseResult, parse
from .profiles import MODE_CAPABILITIES, load_profiles, select_profile, verified_driver


@dataclass
class CollectionResult:
    identity: Identity
    profile_id: str
    mode: str
    driver: str
    results: dict = field(default_factory=dict)
    parsed: dict = field(default_factory=dict)
    missing: dict = field(default_factory=dict)
    driver_warning: str | None = None


def command_allowlist(profiles=None):
    """For a backend-issued exact grant; this does not grant domain/CIDR access."""
    profiles = profiles if profiles is not None else load_profiles()
    return frozenset(
        {*PROBES, *(spec.command for p in profiles.values() for spec in p.capabilities.values())}
    )


def collect_node(
    access,
    context,
    target_ref,
    connection_profile_id,
    *,
    mode="standard",
    budget_seconds=None,
    profiles=None,
    should_continue=None,
    on_result=None,
    known_hosts_provider=None,
):
    """Backend only. Caller supplies authenticated grants, revocation guard and evidence sink.

    Probe connection always uses Paramiko; a second connection with the same login
    is initialized for the confirmed platform. Both share a hard budget.
    """
    profiles = profiles if profiles is not None else load_profiles()
    if mode not in MODE_CAPABILITIES:
        raise ValueError("unknown collection mode")
    limit = (
        budget_seconds
        if budget_seconds is not None
        else {"fast": 120, "standard": 300, "full": 600}[mode]
    )
    if type(limit) not in (int, float) or not math.isfinite(limit) or not 0 < limit <= 600:
        raise ValueError("invalid node budget")
    if access.driver.name != "paramiko":
        raise DeviceAccessError("invalid_request", "识别入口需要公共 Paramiko 驱动")
    started = time.monotonic()
    deadline = started + limit

    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise DeviceAccessError("budget_exceeded", "节点总采集预算已用尽")
        return value

    probe_commands = tuple(
        Command(f"detect-{cmd.split()[0]}", cmd, read_timeout=15) for cmd in PROBES
    )
    authorize(context, BatchRequest(target_ref, connection_profile_id, probe_commands))
    if not callable(should_continue):
        raise DeviceAccessError("invalid_request", "节点采集需要实时授权/取消检查")
    original_profile = access.profile_resolver(connection_profile_id)
    if original_profile.platform is not None:
        raise DeviceAccessError("invalid_request", "未知节点识别前不能预设平台")
    probes = []
    identity = Identity()

    def probe_result(result):
        nonlocal identity
        cmd = next(c.text for c in probe_commands if c.command_id == result.command_id)
        probes.append(Probe(cmd, result.output, result.output_complete and not result.failure_code))
        identity = identify(probes)
        return on_result(result) if on_result else None

    def select_probe(command, results):
        if any(r.failure_code or not r.output_complete for r in results):
            raise DeviceAccessError("output_incomplete", "版本识别采集未完成")
        if identity.status == "ambiguous":
            raise DeviceAccessError("platform_ambiguous", "版本证据冲突，需要人工处理")
        return identity.status not in {"identified", "generic"}

    probe_batch = access.execute_batch(
        context,
        BatchRequest(
            target_ref, connection_profile_id, probe_commands, budget_seconds=min(45, remaining())
        ),
        command_filter=select_probe,
        should_continue=should_continue,
        on_result=probe_result,
    )
    profile = select_profile(identity, profiles)
    deadline = min(deadline, started + getattr(profile.budgets, mode))
    # Do not restart the node budget after identification.
    remaining()
    selected = profile.selected(mode)
    # Reuse the successful own-version evidence even if the device exposed a CLI alias.
    version_result = probe_batch.results[-1]
    version = parse(
        identity, "version", version_result.output, complete=version_result.output_complete
    )
    driver_name = verified_driver(profile, version.summary)
    driver_warning = None
    if (
        driver_name == "netmiko"
        and known_hosts_provider is None
        and (
            getattr(access.driver, "host_key_verifier", None) is not None
            or not original_profile.known_hosts_file
        )
    ):
        # A database verifier cannot be replaced with an unrelated file trust source.
        driver_name = "paramiko"
        driver_warning = "netmiko_requires_matching_known_hosts_provider"
    if driver_name == "netmiko":
        driver = NetmikoDriver(profile.os_family, known_hosts_provider=known_hosts_provider)
    else:
        driver = access.driver
    connection = replace(
        original_profile,
        platform=profile.os_family,
        encoding=profile.session.encoding,
        max_output_bytes=profile.session.max_output_bytes,
        connection_attempts=1,
    )
    collector = DeviceAccessService(
        target_resolver=access.target_resolver,
        profile_resolver=lambda _: connection,
        driver=driver,
        admission=access.admission,
    )
    commands = tuple(
        Command(cap, spec.command, read_timeout=spec.timeout)
        for cap, spec in selected.items()
        if cap != "version"
    )
    batch = collector.execute_batch(
        context,
        BatchRequest(
            target_ref,
            connection_profile_id,
            commands,
            stop_on_error=False,
            budget_seconds=remaining(),
        ),
        should_continue=should_continue,
        on_result=on_result,
    )
    result = CollectionResult(
        identity,
        profile.id,
        mode,
        driver_name,
        results={"version": version_result},
        parsed={"version": version},
        missing={
            cap: "no_profile_command" for cap in MODE_CAPABILITIES[mode] if cap not in selected
        },
        driver_warning=driver_warning,
    )
    for item in batch.results:
        cap = item.command_id
        result.results[cap] = item
        # Even capabilities awaiting templates must classify unsupported/verified-empty output.
        parsed = parse(
            identity,
            cap,
            item.output,
            complete=item.output_complete and not item.failure_code,
            model=version.summary.get("model"),
        )
        if (
            selected[cap].parser is None
            and parsed.status not in {"failed", "unsupported"}
            and (parsed.records or parsed.summary)
        ):
            parsed = ParseResult(parser_ref=f"pending:{cap}", warnings=["parser_not_declared"])
        result.parsed[cap] = parsed
    return result
