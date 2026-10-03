"""One node budget across unknown-platform probing and identified collection."""

import math
import re
import time
from dataclasses import dataclass, field, replace

from src.platform.device_access.command_policy import classify
from src.platform.device_access.contracts import BatchRequest, Command, DeviceAccessError
from src.platform.device_access.drivers.netmiko_driver import NetmikoDriver
from src.platform.device_access.policy import authorize
from src.platform.device_access.service import DeviceAccessService

from .identify import PROBES, Identity, Probe, identify
from .parse import ParseResult, parse
from .profiles import MODE_CAPABILITIES, load_profiles, select_profile, verified_driver
from .tiers.common import vrf_header

MAX_VRF_SCOPES = 64


def scoped_arp_command(scope):
    """Only literal VRF tokens, never wildcards, options, separators or CLI filters."""
    if not isinstance(scope, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.:-]{0,63}", scope):
        raise ValueError("unsafe VRF command token")
    if scope.lower() in {"all", "default"}:
        raise ValueError("reserved VRF command token")
    command = f"show ip arp vrf {scope}"
    decision = classify("ios", command)
    if decision.level != "L1" or decision.normalized != command:
        raise ValueError("VRF ARP command is not canonical read-only CLI")
    return command


def _collect_scoped_arp(
    result,
    collector,
    context_factory,
    target_ref,
    connection_profile_id,
    remaining,
    should_continue,
    on_result,
):
    """Merge only complete scopes and retain a coverage record for every attempt."""
    if result.identity.os_family not in {"ios", "iosxe", "nxos"} or "arp" not in result.parsed:
        return
    global_arp = result.parsed["arp"]
    global_output = result.results["arp"].output
    if any(vrf_header(line) not in {None, "default"} for line in global_output.splitlines()):
        global_arp.status = "failed"
        global_arp.warnings.append("vrf_output_scope_mismatch")
    enumeration = result.parsed.get("vrfs")
    enumeration_status = enumeration.status if enumeration else "missing"
    instances = enumeration.summary.get("instances", []) if enumeration else []
    scopes = {
        row["name"]
        for row in instances
        if not row.get("address_families") or "ipv4" in row["address_families"]
    }
    # A failed/unsupported enumeration still permits positively observed IP VRFs,
    # but cannot prove that all scopes have been covered.
    ip = result.parsed.get("ip")
    if ip and ip.status == "complete":
        scopes.update(row.get("vrf", "default") for row in ip.records)
    scopes.discard("default")
    coverage = {"default": {"status": global_arp.status, "command_id": "arp"}}
    combined = ParseResult(
        status="complete",
        parser_ref="generic:arp:scoped:v1",
        summary={"scope_coverage": {"enumeration_status": enumeration_status, "scopes": coverage}},
    )

    def add_scope(scope, parsed, command_id):
        combined.candidate_count += parsed.candidate_count
        combined.rejected_count += parsed.rejected_count
        # Prefix warnings so transport incompleteness vetoes just this scope;
        # discarded rows never reach the builder or masquerade as an empty table.
        combined.warnings.extend(f"scope:{scope}:{warning}" for warning in parsed.warnings)
        if parsed.status != "complete":
            combined.status = "partial"
            combined.warnings.append(f"scope:{scope}:arp_not_complete")
            return
        combined.records.extend({**row, "vrf": scope} for row in parsed.records)
        combined.sources.extend(
            {**source, "command_id": command_id, "vrf": scope} for source in parsed.sources
        )

    add_scope("default", global_arp, "arp")
    if enumeration_status != "complete":
        combined.status = "partial"
        combined.warnings.append("vrf_enumeration_incomplete")
    commands = []
    scope_by_id = {}
    for scope in sorted(scopes):
        try:
            text = scoped_arp_command(scope)
        except ValueError:
            coverage[scope] = {"status": "missing", "reason": "unsafe_or_reserved_vrf_name"}
            combined.status = "partial"
            continue
        if len(commands) >= MAX_VRF_SCOPES:
            coverage[scope] = {"status": "missing", "reason": "vrf_scope_budget"}
            combined.status = "partial"
            continue
        command_id = f"arp-vrf-{len(commands)}"
        commands.append(Command(command_id, text))
        scope_by_id[command_id] = scope
        coverage[scope] = {"status": "missing", "reason": "not_collected", "command_id": command_id}

    def record_scoped(item):
        evidence_ref = on_result(item) if on_result else None
        if isinstance(evidence_ref, str):
            item = replace(item, evidence_ref=evidence_ref)
        scope = scope_by_id[item.command_id]
        result.results[item.command_id] = item
        parsed = parse(
            result.identity,
            "arp",
            item.output,
            complete=item.output_complete and not item.failure_code,
        )
        explicit_scopes = {
            vrf_header(line) for line in item.output.splitlines() if vrf_header(line)
        }
        if explicit_scopes - {scope}:
            parsed.status = "failed"
            parsed.warnings.append("vrf_output_scope_mismatch")
        coverage[scope] = {
            "status": parsed.status,
            "command_id": item.command_id,
            "warnings": parsed.warnings,
        }
        add_scope(scope, parsed, item.command_id)
        return evidence_ref

    if commands and context_factory is not None:
        try:
            context = context_factory(tuple(scope_by_id.values()))
            collector.execute_batch(
                context,
                BatchRequest(
                    target_ref,
                    connection_profile_id,
                    tuple(commands),
                    stop_on_error=False,
                    budget_seconds=remaining(),
                ),
                should_continue=should_continue,
                on_result=record_scoped,
            )
        except DeviceAccessError as exc:
            # Expired budget retains earlier evidence. Authorization, host-key,
            # credential and connection errors keep the normal target retry flow.
            if exc.failure_code != "budget_exceeded":
                raise
            for scope in scope_by_id.values():
                if coverage[scope]["status"] == "missing":
                    coverage[scope]["reason"] = exc.failure_code
    elif commands:
        for scope in scope_by_id.values():
            coverage[scope]["reason"] = "scoped_collection_not_authorized"
    if any(row["status"] != "complete" for row in coverage.values()):
        combined.status = "partial"
        combined.warnings.append("arp_scope_coverage_incomplete")
    result.parsed["arp"] = combined


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
    scoped_arp_context=None,
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
        else {"fast": 120, "topology": 240, "standard": 300, "full": 600}[mode]
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
    defer_routes = profile.os_family in {"ios", "nxos"} and "arp" in selected
    commands = tuple(
        Command(cap, spec.command, read_timeout=spec.timeout)
        for cap, spec in selected.items()
        if cap != "version" and (cap != "routes" or not defer_routes)
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

    def record(item):
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

    for item in batch.results:
        record(item)
    _collect_scoped_arp(
        result,
        collector,
        scoped_arp_context,
        target_ref,
        connection_profile_id,
        remaining,
        should_continue,
        on_result,
    )
    # Large route tables come last, after VRF adjacency evidence, within the same deadline.
    if defer_routes and "routes" in selected:
        spec = selected["routes"]
        try:
            batch = collector.execute_batch(
                context,
                BatchRequest(
                    target_ref,
                    connection_profile_id,
                    (Command("routes", spec.command, read_timeout=spec.timeout),),
                    stop_on_error=False,
                    budget_seconds=remaining(),
                ),
                should_continue=should_continue,
                on_result=on_result,
            )
            for item in batch.results:
                record(item)
        except DeviceAccessError as exc:
            if exc.failure_code != "budget_exceeded":
                raise
            result.parsed["routes"] = ParseResult(warnings=[exc.failure_code])
    return result
