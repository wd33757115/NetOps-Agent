"""Scoped ARP stays device-local and incomplete collection never proves an empty VRF."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from src.apps.topology.backend.collectors import collect as runtime
from src.apps.topology.backend.collectors.parse import parse
from src.apps.topology.backend.model.builder import device_node_ids
from src.apps.topology.backend.service import _facts, _scoped_arp_context, topology_observation
from src.platform.device_access.contracts import (
    AccessContext,
    AccessGrant,
    BatchRequest,
    Command,
    ConnectionProfile,
    DeviceAccessError,
    Target,
)
from src.platform.device_access.errors import CommandExecutionError
from src.platform.device_access.policy import authorize
from src.platform.device_access.service import DeviceAccessService

VRFS = """  Name                             Default RD          Protocols   Interfaces
  blue                             <not set>           ipv4        Et0/1
  red                              65000:2             ipv4,ipv6   Et0/2
"""
ARP_HEADER = "Protocol  Address          Age (min)  Hardware Addr   Type   Interface\n"
BLUE_ARP = ARP_HEADER + "Internet  192.0.2.20       2          0011.2233.4455  ARPA   Ethernet0/1\n"
GLOBAL_ARP = (
    ARP_HEADER + "Internet  192.0.2.20       3          00aa.bbcc.ddee  ARPA   Ethernet0/0\n"
)


@pytest.fixture
def ios():
    state = SimpleNamespace(
        commands=[],
        evidence=[],
        on_command=None,
        outputs={
            "display version": "% Invalid input detected at '^' marker.",
            "show version": "Cisco IOS XE Software, Version 17.9",
            "show lldp neighbors detail": "No LLDP neighbors found.",
            "show interfaces description": (
                "Interface Status Protocol Description\nEt0/0 up up test"
            ),
            "show ip interface": "Ethernet0/1 is up, line protocol is up\n"
            "  Internet address is 192.0.2.1/24\n"
            '  VPN Routing/Forwarding "blue"\n',
            "show ip arp": GLOBAL_ARP,
            "show vrf": VRFS,
            "show ip arp vrf blue": BLUE_ARP,
            "show ip arp vrf red": "",
        },
    )

    class Driver:
        name, version = "paramiko", "fixture"

        def connect(self, *args, **kwargs):
            return self

        def send_command(self, command, read_timeout):
            state.commands.append(command)
            if state.on_command:
                state.on_command(command)
            output = state.outputs.get(command, "% Invalid input detected at '^' marker.")
            if isinstance(output, Exception):
                raise output
            return output

        def disconnect(self):
            pass

        @contextmanager
        def hold(self, *args):
            yield lambda: None

    driver = Driver()
    access = DeviceAccessService(
        target_resolver=lambda _: Target("node", "192.0.2.1"),
        profile_resolver=lambda _: ConnectionProfile("connection", "test-user", "test-password"),
        driver=driver,
        admission=driver,
    )
    context = AccessContext(
        "actor", "topology", (AccessGrant("node", "connection", runtime.command_allowlist()),)
    )

    def collect(*, scoped=True, **kwargs):
        return runtime.collect_node(
            access,
            context,
            "node",
            "connection",
            mode="topology",
            should_continue=lambda: True,
            on_result=lambda result: state.evidence.append(result),
            scoped_arp_context=(
                lambda scopes: _scoped_arp_context(context, "node", "connection", scopes)
            )
            if scoped
            else None,
            **kwargs,
        )

    state.collect = collect
    state.context = context
    return state


def test_all_enumerated_vrfs_are_collected_with_explicit_context_and_raw_evidence(ios):
    result = ios.collect()
    arp = result.parsed["arp"]
    assert arp.status == "complete"
    assert {(row["vrf"], row["ip"], row["mac"]) for row in arp.records} == {
        ("default", "192.0.2.20", "00:aa:bb:cc:dd:ee"),
        ("blue", "192.0.2.20", "00:11:22:33:44:55"),
    }
    coverage = arp.summary["scope_coverage"]
    assert coverage["enumeration_status"] == "complete"
    assert set(coverage["scopes"]) == {"default", "blue", "red"}
    assert coverage["scopes"]["red"]["status"] == "complete"  # Verified empty named VRF.
    assert ios.commands.count("show ip arp vrf red") == 1  # Even without an IP record.
    assert ios.commands.index("show ip arp vrf red") < ios.commands.index("show ip route")
    scoped_evidence = next(e for e in ios.evidence if e.command_id == "arp-vrf-0")
    assert scoped_evidence.output == BLUE_ARP
    assert arp.sources[-1]["command_id"] == "arp-vrf-0"
    observation = topology_observation(result)
    assert observation["fact_metadata"]["arp"]["scope_coverage"] == coverage
    assert _facts(observation)["arp"] == arp.records


@pytest.mark.parametrize(
    "failure",
    [
        "% Invalid input detected at '^' marker.",
        CommandExecutionError("timeout", "fixture timeout", BLUE_ARP),
        "VRF: unexpected\n" + BLUE_ARP,
    ],
)
def test_failed_truncated_or_mismatched_scope_keeps_good_scope_and_reports_gap(ios, failure):
    ios.outputs["show ip arp vrf blue"] = failure
    result = ios.collect()
    arp = result.parsed["arp"]
    assert arp.status == "partial"
    assert {row["vrf"] for row in arp.records} == {"default"}
    assert arp.summary["scope_coverage"]["scopes"]["blue"]["status"] != "complete"
    assert _facts(topology_observation(result))["arp"] == arp.records


def test_empty_global_is_not_complete_when_named_scope_was_not_authorized(ios):
    ios.outputs["show ip arp"] = ""
    arp = ios.collect(scoped=False).parsed["arp"]
    assert arp.status == "partial" and arp.records == []
    assert arp.summary["scope_coverage"]["scopes"]["default"]["status"] == "complete"
    assert (
        arp.summary["scope_coverage"]["scopes"]["blue"]["reason"]
        == "scoped_collection_not_authorized"
    )
    assert not any(command.startswith("show ip arp vrf") for command in ios.commands)


def test_failed_vrf_enumeration_uses_positive_ip_scope_but_keeps_coverage_gap(ios):
    ios.outputs["show vrf"] = "% Invalid input detected at '^' marker."
    arp = ios.collect().parsed["arp"]
    assert arp.status == "partial"
    assert {row["vrf"] for row in arp.records} == {"default", "blue"}
    assert arp.summary["scope_coverage"]["enumeration_status"] == "unsupported"
    assert "vrf_enumeration_incomplete" in arp.warnings


def test_duplicate_vrf_name_is_not_silently_complete(ios):
    ios.outputs["show vrf"] += (
        "  blue                             1:2                 ipv4        Et0/3\n"
    )
    result = ios.collect()
    assert result.parsed["vrfs"].status == "partial"
    assert result.parsed["arp"].status == "partial"
    assert ios.commands.count("show ip arp vrf blue") == 1


@pytest.mark.parametrize(
    "scope", ["bad;reload", "x|include", "x\nreload", "a b", "*", "all", "-x", "default"]
)
def test_dynamic_scope_tokens_cannot_broaden_or_inject_cli(ios, scope):
    with pytest.raises(ValueError):
        _scoped_arp_context(ios.context, "node", "connection", (scope,))


def test_exact_grant_cannot_authorize_another_scope_target_or_connection(ios):
    context = _scoped_arp_context(ios.context, "node", "connection", ("blue",))
    authorize(
        context, BatchRequest("node", "connection", (Command("one", "show ip arp vrf blue"),))
    )
    for target, connection, command in [
        ("other", "connection", "show ip arp vrf blue"),
        ("node", "other", "show ip arp vrf blue"),
        ("node", "connection", "show ip arp vrf red"),
    ]:
        with pytest.raises(DeviceAccessError):
            authorize(context, BatchRequest(target, connection, (Command("one", command),)))


def test_valid_header_only_vrf_table_is_a_verified_empty_enumeration():
    parsed = parse({"os_family": "ios"}, "vrfs", VRFS.splitlines()[0])
    assert parsed.status == "complete" and parsed.summary["instances"] == []
    assert parse({"os_family": "ios"}, "vrfs", "").status == "failed"


def test_nxos_vrf_table_is_device_local_scope_enumeration():
    parsed = parse(
        {"os_family": "nxos"},
        "vrfs",
        "VRF-Name VRF-ID State Reason\ndefault 1 Up --\nmanagement 2 Up --\n",
    )
    assert parsed.status == "complete"
    assert [row["name"] for row in parsed.summary["instances"]] == ["default", "management"]


def test_truncated_local_lldp_identity_cannot_feed_builder():
    data = {
        "facts": {"lldp_local": {"chassis_id": "00:11:22:33:44:55"}, "lldp": []},
        "fact_metadata": {"lldp_local": {"warnings": ["collection_incomplete"]}},
    }
    assert "lldp_local" not in _facts(data)
    assert _facts(data)["lldp"] == []
    assert data["facts"]["lldp_local"]  # Evidence is preserved, only builder input is gated.


@pytest.mark.parametrize("capability", ["trunk", "switchport", "ospf_interfaces", "ospf_process"])
def test_truncated_summary_retains_an_explicit_veto(capability):
    data = {
        "facts": {capability: {"ports": [{"ifname": "Ethernet0/1"}]}},
        "fact_metadata": {capability: {"warnings": ["collection_incomplete"]}},
    }
    assert _facts(data)[capability] == {"invalid_structure": True}


def test_budget_expiry_keeps_completed_scope_and_marks_remaining_scope_missing(ios):
    result = ios.collect()
    blue = result.results["arp-vrf-0"]
    result.parsed["arp"] = parse(result.identity, "arp", GLOBAL_ARP)

    class BudgetEnds:
        def execute_batch(self, context, request, *, on_result, **kwargs):
            on_result(blue)
            raise DeviceAccessError("budget_exceeded", "fixture budget expired")

    runtime._collect_scoped_arp(
        result,
        BudgetEnds(),
        lambda scopes: ios.context,
        "node",
        "connection",
        lambda: 1,
        lambda: True,
        None,
    )
    arp = result.parsed["arp"]
    assert arp.status == "partial"
    assert {row["vrf"] for row in arp.records} == {"default", "blue"}
    assert arp.summary["scope_coverage"]["scopes"]["red"]["reason"] == "budget_exceeded"


@pytest.mark.parametrize("failure", ["cancelled", "not_authorized", "host_key_rejected"])
def test_authorization_and_trust_errors_are_not_swallowed_as_coverage_gaps(ios, failure):
    result = ios.collect()

    class Rejected:
        def execute_batch(self, *args, **kwargs):
            raise DeviceAccessError(failure, "fixture rejection")

    with pytest.raises(DeviceAccessError) as exc:
        runtime._collect_scoped_arp(
            result,
            Rejected(),
            lambda scopes: ios.context,
            "node",
            "connection",
            lambda: 1,
            lambda: True,
            None,
        )
    assert exc.value.failure_code == failure


def test_scope_limit_is_explicit_without_overwriting_other_scope_coverage(ios, monkeypatch):
    monkeypatch.setattr(runtime, "MAX_VRF_SCOPES", 1)
    arp = ios.collect().parsed["arp"]
    assert arp.status == "partial"
    assert arp.summary["scope_coverage"]["scopes"]["blue"]["status"] == "complete"
    assert arp.summary["scope_coverage"]["scopes"]["red"]["reason"] == "vrf_scope_budget"
    assert "show ip arp vrf red" not in ios.commands


def test_global_query_never_relabels_an_explicit_different_vrf(ios):
    ios.outputs["show ip arp"] = "VRF: blue\n" + GLOBAL_ARP
    arp = ios.collect().parsed["arp"]
    assert arp.status == "partial"
    assert {row["vrf"] for row in arp.records} == {"blue"}
    assert arp.summary["scope_coverage"]["scopes"]["default"]["status"] == "failed"


def test_unsafe_name_in_device_output_is_a_gap_without_executing_it(ios):
    ios.outputs["show vrf"] += "  bad;reload  1:2  ipv4  Et0/3\n"
    arp = ios.collect().parsed["arp"]
    assert arp.status == "partial"
    assert arp.summary["scope_coverage"]["scopes"]["bad;reload"]["reason"] == (
        "unsafe_or_reserved_vrf_name"
    )
    assert not any("reload" in command for command in ios.commands)


def test_empty_global_with_verified_no_named_vrfs_is_complete(ios):
    ios.outputs["show vrf"] = VRFS.splitlines()[0]
    ios.outputs["show ip interface"] = "Ethernet0/0 is up, line protocol is up\n"
    ios.outputs["show ip arp"] = ""
    arp = ios.collect().parsed["arp"]
    assert arp.status == "complete" and arp.records == []
    assert set(arp.summary["scope_coverage"]["scopes"]) == {"default"}


@pytest.mark.parametrize("with_capabilities", [True, False])
def test_conflicting_local_chassis_values_cannot_supply_a_strong_identity(with_capabilities):
    parsed = parse(
        {"os_family": "comware"},
        "lldp_local",
        "Chassis ID : 00:11:22:33:44:01\nChassis ID : 00:11:22:33:44:02\n",
    )
    assert parsed.status == "partial"
    assert parsed.summary["chassis_id"] == "00:11:22:33:44:02"
    observation = {
        "facts": {"lldp_local": parsed.summary},
        "fact_metadata": {"lldp_local": {"warnings": parsed.warnings}},
    }
    if with_capabilities:
        observation["capabilities"] = {"lldp_local": parsed.status}
    facts = _facts(observation)
    assert "lldp_local" not in facts
    ids = device_node_ids(
        {
            "a": facts,
            "b": {
                "lldp_local": {
                    "chassis_id": "00:11:22:33:44:02",
                }
            },
        },
        "urn:test:",
    )
    assert ids["a"] != ids["b"]
    assert observation["facts"]["lldp_local"] == parsed.summary  # Preserve diagnostic evidence.


@pytest.mark.parametrize("status", ["partial", "failed", "unsupported"])
def test_explicitly_unsuccessful_local_identity_cannot_enter_builder(status):
    assert "lldp_local" not in _facts(
        {
            "facts": {"lldp_local": {"chassis_id": "00:11:22:33:44:01"}},
            "capabilities": {"lldp_local": status},
        }
    )


def test_historical_local_identity_without_status_or_known_conflict_remains_compatible():
    summary = {"chassis_id": "00:11:22:33:44:01"}
    assert _facts({"facts": {"lldp_local": summary}})["lldp_local"] == summary
