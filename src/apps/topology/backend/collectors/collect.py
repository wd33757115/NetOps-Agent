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
from .tiers.template import IOS_ROUTE_TABLE

MAX_VRF_SCOPES = 64


IOS_SCOPED = {"arp": "show ip arp vrf {}", "routes": "show ip route vrf {}"}
# Per-VRF commands whose layout is verified: IOS ARP and route tables (campus-wan), NX-OS
# ARP, Comware VPN-instance route tables and instance detail (vpn-fabric).
SCOPED_COMMANDS = {
    "ios": IOS_SCOPED,
    "iosxe": IOS_SCOPED,
    "nxos": {"arp": IOS_SCOPED["arp"]},
    "comware": {
        "routes": "display ip routing-table vpn-instance {}",
        "vrf_detail": "display ip vpn-instance instance-name {}",
    },
}
# No table of their own outside the VRFs: only the per-VRF commands run.
INSTANCE_ONLY = frozenset({"vrf_detail"})


def scoped_command(capability, scope, family="ios"):
    """Only literal VRF tokens, never wildcards, options, separators or CLI filters."""
    if not isinstance(scope, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.:-]{0,63}", scope):
        raise ValueError("unsafe VRF command token")
    if scope.lower() in {"all", "default"}:
        raise ValueError("reserved VRF command token")
    command = SCOPED_COMMANDS[family][capability].format(scope)
    decision = classify("comware" if family == "comware" else "ios", command)
    if decision.level != "L1" or decision.normalized != command:
        raise ValueError("VRF command is not canonical read-only CLI")
    return command


def scoped_arp_command(scope):
    return scoped_command("arp", scope)


def _scope_headers(capability, text):
    """The table names an output gives itself: ARP context/VRF lines, IOS route tables."""
    if capability == "routes":
        return {m[1] for line in text.splitlines() if (m := IOS_ROUTE_TABLE.fullmatch(line))}
    return {vrf_header(line) for line in text.splitlines() if vrf_header(line)}


def _bound_elsewhere(result, parsed, scope):
    """A Comware VPN route table does not name itself; a connected route out of an
    interface bound to another instance proves the table is not this scope's."""
    ip = result.parsed.get("ip")
    if not ip or ip.status != "complete":
        return False
    bound = {row["ifname"]: row.get("vrf", "default") for row in ip.records if "ifname" in row}
    return any(
        bound.get(hop.get("ifname"), scope) != scope
        for row in parsed.records
        if str(row.get("protocol", "")).lower() == "direct"
        for hop in row.get("next_hops", [])
    )


def _collect_scoped(
    capability,
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
    family = result.identity.os_family
    instance_only = capability in INSTANCE_ONLY
    if capability not in SCOPED_COMMANDS.get(family, {}) or (
        not instance_only and (capability not in result.parsed or capability not in result.results)
    ):
        return
    if not instance_only:
        global_table = result.parsed[capability]
        global_output = result.results[capability].output
        if _scope_headers(capability, global_output) - {"default"}:
            global_table.status = "failed"
            global_table.warnings.append("vrf_output_scope_mismatch")
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
    coverage = (
        {}
        if instance_only
        else {"default": {"status": global_table.status, "command_id": capability}}
    )
    combined = ParseResult(
        status="complete",
        parser_ref=f"generic:{capability}:scoped:v1",
        summary={"scope_coverage": {"enumeration_status": enumeration_status, "scopes": coverage}},
    )
    if not instance_only:
        # The merged table is read by the global table's parser tier.
        combined.tier, combined.confidence = global_table.tier, global_table.confidence

    def add_scope(scope, parsed, command_id):
        combined.candidate_count += parsed.candidate_count
        combined.rejected_count += parsed.rejected_count
        # Prefix warnings so transport incompleteness vetoes just this scope;
        # discarded rows never reach the builder or masquerade as an empty table.
        combined.warnings.extend(f"scope:{scope}:{warning}" for warning in parsed.warnings)
        if parsed.status != "complete":
            combined.status = "partial"
            combined.warnings.append(f"scope:{scope}:{capability}_not_complete")
            return
        for row in parsed.records:
            row = {**row, "vrf": scope}
            if "next_hops" in row:  # a route's next hops resolve in the same table
                row["next_hops"] = [{**hop, "vrf": scope} for hop in row["next_hops"]]
            combined.records.append(row)
        combined.sources.extend(
            {**source, "command_id": command_id, "vrf": scope} for source in parsed.sources
        )

    if not instance_only:
        add_scope("default", global_table, capability)
    if enumeration_status != "complete":
        combined.status = "partial"
        combined.warnings.append("vrf_enumeration_incomplete")
    commands = []
    scope_by_id = {}
    for scope in sorted(scopes):
        try:
            text = scoped_command(capability, scope, family)
        except ValueError:
            coverage[scope] = {"status": "missing", "reason": "unsafe_or_reserved_vrf_name"}
            combined.status = "partial"
            continue
        if len(commands) >= MAX_VRF_SCOPES:
            coverage[scope] = {"status": "missing", "reason": "vrf_scope_budget"}
            combined.status = "partial"
            continue
        command_id = f"{capability}-vrf-{len(commands)}"
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
            capability,
            item.output,
            complete=item.output_complete and not item.failure_code,
        )
        if capability == "vrf_detail":
            explicit_scopes = {row["vrf"] for row in parsed.records}
        else:
            explicit_scopes = _scope_headers(capability, item.output)
        # An IOS VRF route table and an instance detail name themselves; one without (or
        # with another) name is refused. A Comware table must not contradict its bindings.
        named = capability == "vrf_detail" or (capability == "routes" and family != "comware")
        if (
            explicit_scopes - {scope}
            or (named and explicit_scopes != {scope})
            or (
                family == "comware"
                and capability == "routes"
                and _bound_elsewhere(result, parsed, scope)
            )
        ):
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
            context = context_factory(capability, tuple(scope_by_id.values()), family)
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
        combined.warnings.append(f"{capability}_scope_coverage_incomplete")
    result.parsed[capability] = combined


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
    scoped_context=None,
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
    _collect_scoped(
        "arp",
        result,
        collector,
        scoped_context,
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
    _collect_scoped(
        "routes",
        result,
        collector,
        scoped_context,
        target_ref,
        connection_profile_id,
        remaining,
        should_continue,
        on_result,
    )
    if "vrf_detail" in MODE_CAPABILITIES[mode]:
        # Comware gives each VPN instance's RD, targets and interfaces one by one.
        _collect_scoped(
            "vrf_detail",
            result,
            collector,
            scoped_context,
            target_ref,
            connection_profile_id,
            remaining,
            should_continue,
            on_result,
        )
    return result
