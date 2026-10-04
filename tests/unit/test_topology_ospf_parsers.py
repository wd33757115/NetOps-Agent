"""Small verbatim fragments of the 20261003-015834 read-only campus captures."""

import hashlib
import importlib
import json
from pathlib import Path

import pytest

from src.apps.topology.backend.collectors import rules
from src.apps.topology.backend.collectors.parse import parse
from src.apps.topology.backend.facts.repository import FactInput, validate_records

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/topology/campus-wan/ospf"
MANIFEST = json.loads((FIXTURES / "manifest.json").read_text("utf-8"))

IOS_BRIEF = """Interface    PID   Area            IP Address/Mask    Cost  State Nbrs F/C
Lo0          1     0               10.255.0.3/32      1     LOOP  0/0
Et0/1        1     0               10.0.13.2/30       10    DR    1/1
"""
DIST_BRIEF = """Interface    PID   Area            IP Address/Mask    Cost  State Nbrs F/C
Vl20         1     10              172.16.20.2/24     1     DR    0/0
"""
COMWARE_BRIEF = """\t OSPF Process 1 with Router ID 10.255.0.1
\t\t Interfaces

 Area: 0.0.0.0
 IP Address      Type      State    Cost  Pri   DR              BDR
 10.0.13.1       Broadcast BDR      1     1     10.0.13.2       10.0.13.1
 10.255.0.1      PTP       Loopback 0     1     0.0.0.0         0.0.0.0
"""
COMWARE_PROCESS = """\t OSPF Process 1 with Router ID 10.255.0.1
\t\t OSPF Protocol Information

 RouterID: 10.255.0.1       Router type:
 Route tag: 0
 Multi-VPN-Instance is not enabled
 Area: 0.0.0.0          (MPLS TE not enabled)
 Authentication type: None    Area flag: Normal
 Interface: 10.0.13.1 (GigabitEthernet3/0)
 Cost: 1       State: BDR       Type: Broadcast    MTU: 1500
 Priority: 1
 Interface: 10.255.0.1 (LoopBack0)
 Cost: 0       State: Loopback    Type: PTP       MTU: 1536
 Timers: Hello 10, Dead 40, Poll 40, Retransmit 5, Transmit Delay 1
"""
IOS_PROCESS = """ Routing Process "ospf 1" with ID 10.255.0.3
 Start time: 00:00:00.973, Time elapsed: 00:23:35.830
 Supports only single TOS(TOS0) routes
 Reference bandwidth unit is 100 mbps
    Area BACKBONE(0)
        Number of interfaces in this area is 6 (1 loopback)
\tArea has no authentication
"""


def interfaces(text, family):
    """OSPF facts are read through the rule-governance check items topology.ospf_*."""
    return parse({"os_family": family}, "ospf_interfaces", text)


def process(text, family):
    return parse({"os_family": family}, "ospf_process", text)


def rows(parsed):
    assert parsed.records == []
    return parsed.summary.get("interfaces", [])


def test_ios_brief_preserves_actual_interface_cost_address_and_area():
    parsed = interfaces(IOS_BRIEF + DIST_BRIEF.split("\n", 1)[1], "ios")
    assert not parsed.rejected_count, parsed.warnings
    by_name = {row["ifname"]: row for row in rows(parsed)}
    assert by_name["Loopback0"] == {
        "process_id": "1",
        "ifname": "Loopback0",
        "raw_name": "Lo0",
        "area": "0",
        "address": "10.255.0.3",
        "prefix_length": 32,
        "cost": 1,
        "state": "LOOP",
    }
    assert by_name["Ethernet0/1"]["cost"] == 10
    assert by_name["Vlan20"]["area"] == "10"
    assert all("router_id" not in row and "vrf" not in row for row in by_name.values())


def test_comware_brief_has_no_invented_ifname_or_prefix_or_vrf():
    parsed = interfaces(COMWARE_BRIEF, "comware")
    assert not parsed.rejected_count, parsed.warnings
    assert rows(parsed)[1]["cost"] == 0
    assert rows(parsed)[1]["state"] == "Loopback"
    assert rows(parsed)[0]["router_id"] == "10.255.0.1"
    assert all(
        "ifname" not in row and "prefix_length" not in row and "vrf" not in row
        for row in rows(parsed)
    )
    assert parsed.sources[0]["lines"] == [1, 4, 6]


def test_comware_process_proves_scope_and_address_interface_mapping():
    parsed = process(COMWARE_PROCESS, "comware")
    assert not parsed.rejected_count, parsed.warnings
    assert parsed.summary["processes"] == [
        {
            "process_id": "1",
            "router_id": "10.255.0.1",
            "vrf": "default",
            "vrf_evidence": "output:Multi-VPN-Instance is not enabled",
        }
    ]
    by_name = {row["ifname"]: row for row in rows(parsed)}
    assert by_name["GigabitEthernet3/0"]["address"] == "10.0.13.1"
    assert by_name["LoopBack0"]["cost"] == 0
    assert by_name["LoopBack0"]["raw_name"] == "LoopBack0"
    assert all("prefix_length" not in row for row in rows(parsed))


def test_ios_process_header_proves_router_id_without_assuming_vrf():
    parsed = process(IOS_PROCESS, "ios")
    assert not parsed.rejected_count, parsed.warnings
    assert parsed.summary == {"processes": [{"process_id": "1", "router_id": "10.255.0.3"}]}


def test_comware_default_scope_requires_explicit_output_evidence():
    parsed = process(COMWARE_PROCESS.replace(" Multi-VPN-Instance is not enabled\n", ""), "comware")
    assert not parsed.rejected_count
    assert "vrf" not in parsed.summary["processes"][0]
    assert "vrf_evidence" not in parsed.summary["processes"][0]


@pytest.mark.parametrize("family", ["ios", "iosxe"])
@pytest.mark.parametrize("parser", [interfaces, process])
def test_actual_empty_ios_access_outputs_are_verified(parser, family):
    parsed = parser("\n", family)
    assert parsed.status == "complete"
    assert not parsed.summary and not parsed.records and not parsed.rejected_count


@pytest.mark.parametrize("parser", [interfaces, process])
@pytest.mark.parametrize("family", ["comware", "unknown"])
def test_empty_unverified_family_never_creates_a_process_or_cost(parser, family):
    parsed = parser("", family)
    assert parsed.status == "failed" and not parsed.summary and not parsed.records


@pytest.mark.parametrize("extra", ["Unknown global OSPF syntax\n", "VPN instance: MGMT\n"])
@pytest.mark.parametrize("prepend", [True, False])
@pytest.mark.parametrize(
    "parser,sample,family",
    [
        (interfaces, IOS_BRIEF, "ios"),
        (interfaces, COMWARE_BRIEF, "comware"),
        (process, IOS_PROCESS, "ios"),
        (process, COMWARE_PROCESS, "comware"),
    ],
)
def test_unknown_scope_or_syntax_carries_global_veto(parser, sample, family, prepend, extra):
    parsed = parser(extra + sample if prepend else sample + extra, family)
    assert parsed.rejected_count
    assert parsed.summary["invalid_structure"]


@pytest.mark.parametrize("cost", ["-1", "65536", "bad"])
def test_invalid_ios_cost_removes_known_interface_and_keeps_good_loopback(cost):
    parsed = interfaces(IOS_BRIEF.replace("10    DR", f"{cost}    DR"), "ios")
    assert parsed.rejected_count
    assert [row["ifname"] for row in rows(parsed)] == ["Loopback0"]
    assert parsed.summary["invalid_interfaces"] == [
        {"process_id": "1", "ifname": "Ethernet0/1", "address": "10.0.13.2"}
    ]


@pytest.mark.parametrize("cost", ["-1", "65536", "bad"])
def test_invalid_comware_cost_removes_known_interface(cost):
    parsed = process(COMWARE_PROCESS.replace("Cost: 1", f"Cost: {cost}"), "comware")
    assert parsed.rejected_count
    assert [row["ifname"] for row in rows(parsed)] == ["LoopBack0"]
    assert parsed.summary["invalid_interfaces"][0]["address"] == "10.0.13.1"


@pytest.mark.parametrize("sample,family", [(IOS_BRIEF, "ios"), (COMWARE_BRIEF, "comware")])
def test_duplicate_brief_row_invalidates_original_observation(sample, family):
    line = next(line for line in sample.splitlines() if "10.0.13." in line)
    parsed = interfaces(sample + line + "\n", family)
    assert parsed.rejected_count
    assert len(rows(parsed)) == 1
    assert parsed.summary["invalid_interfaces"]


def test_duplicate_table_header_is_global_veto():
    parsed = interfaces(IOS_BRIEF + IOS_BRIEF.splitlines()[0] + "\n", "ios")
    assert parsed.summary["invalid_structure"]
    assert parsed.rejected_count


def test_duplicate_comware_brief_area_vetoes_process_interfaces():
    parsed = interfaces(COMWARE_BRIEF + " Area: 0.0.0.0\n", "comware")
    assert parsed.rejected_count and parsed.summary["invalid_processes"] == ["1"]
    assert not rows(parsed)


def test_duplicate_comware_interface_label_vetoes_process_interfaces():
    parsed = interfaces(COMWARE_BRIEF + " Interfaces\n", "comware")
    assert parsed.rejected_count and parsed.summary["invalid_processes"] == ["1"]
    assert not rows(parsed)


def test_duplicate_full_process_area_vetoes_observations():
    parsed = process(COMWARE_PROCESS + " Area: 0.0.0.0\n", "comware")
    assert parsed.rejected_count and parsed.summary["invalid_processes"] == ["1"]
    assert not rows(parsed) and not parsed.summary.get("processes")


@pytest.mark.parametrize(
    "change",
    [
        lambda text: text.replace(
            "Cost: 1       State: BDR       Type: Broadcast    MTU: 1500\n", ""
        ),
        lambda text: text.replace(
            " Priority: 1\n", " Cost: 1       State: BDR       Type: Broadcast    MTU: 1500\n"
        ),
        lambda text: text.replace(
            " Interface: 10.255.0.1 (LoopBack0)", " Interface: 10.0.13.1 (LoopBack0)"
        ),
    ],
)
def test_missing_duplicate_or_conflicting_comware_interface_observation_has_no_fake_cost(change):
    parsed = process(change(COMWARE_PROCESS), "comware")
    assert parsed.rejected_count
    assert all(row["address"] != "10.0.13.1" for row in rows(parsed))
    assert parsed.summary["invalid_interfaces"]


@pytest.mark.parametrize("sample,family", [(COMWARE_PROCESS, "comware"), (IOS_PROCESS, "ios")])
def test_duplicate_process_header_vetoes_process_and_its_interfaces(sample, family):
    parsed = process(sample + sample.splitlines()[0] + "\n", family)
    assert parsed.rejected_count
    assert parsed.summary["invalid_processes"] == ["1"]
    assert not parsed.summary.get("processes") and not rows(parsed)


def test_conflicting_router_id_vetoes_comware_process():
    parsed = process(
        COMWARE_PROCESS.replace("RouterID: 10.255.0.1", "RouterID: 10.255.0.99"), "comware"
    )
    assert parsed.rejected_count
    assert parsed.summary["invalid_processes"] == ["1"]
    assert not parsed.summary.get("processes") and not rows(parsed)


@pytest.mark.parametrize(
    "capability,parser,sample,family",
    [
        ("ospf_interfaces", interfaces, IOS_BRIEF, "ios"),
        ("ospf_process", process, COMWARE_PROCESS, "comware"),
    ],
)
def test_summary_sentinel_survives_public_parse_as_partial(capability, parser, sample, family):
    module = importlib.import_module("src.apps.topology.backend.collectors.parse")
    parsed = module.parse({"os_family": family}, capability, "Unknown OSPF syntax\n" + sample)
    assert parsed.tier == "rules"
    assert parsed.status == "partial" and parsed.summary["invalid_structure"]
    assert parsed.fact_summary()["invalid_structure"]


def test_entirely_bad_known_interface_survives_public_parse_as_partial():
    module = importlib.import_module("src.apps.topology.backend.collectors.parse")
    sample = DIST_BRIEF.replace("1     DR", "bad     DR")
    parsed = module.parse({"os_family": "ios"}, "ospf_interfaces", sample)
    assert parsed.status == "partial" and not parsed.summary.get("interfaces")
    assert parsed.summary["invalid_interfaces"] == [
        {"process_id": "1", "address": "172.16.20.2", "ifname": "Vlan20"}
    ]


@pytest.mark.parametrize(
    "capability,parser",
    [
        ("ospf_interfaces", interfaces),
        ("ospf_process", process),
    ],
)
def test_empty_incomplete_transport_is_not_verified_and_unsupported_is_not_empty(
    capability, parser
):
    module = importlib.import_module("src.apps.topology.backend.collectors.parse")
    incomplete = module.parse({"os_family": "ios"}, capability, "", complete=False)
    unsupported = module.parse(
        {"os_family": "ios"},
        capability,
        "% Invalid input detected at '^' marker.\n",
    )
    assert incomplete.status == "failed" and not incomplete.summary
    assert unsupported.status == "unsupported" and not unsupported.summary


@pytest.mark.parametrize(
    "observation",
    # The verbose Comware interface tables are kept for the OSPF table rework (#3).
    [o for o in MANIFEST["observations"] if o["capability"] != "ospf_interfaces_verbose"],
    ids=lambda item: f"{item['device']}-{item['capability']}",
)
def test_all_real_ospf_captures_through_registered_public_parser_and_fact_contract(observation):
    module = importlib.import_module("src.apps.topology.backend.collectors.parse")
    family = "comware" if observation["device"].startswith("br") else "ios"
    identity = {
        "os_family": family,
        "profile_id": "h3c-comware7" if family == "comware" else "cisco-ios",
    }
    payloads = {}
    for suffix, descriptor in observation["files"].items():
        payload = (FIXTURES / descriptor["path"]).read_bytes()
        assert len(payload) == descriptor["bytes"]
        assert hashlib.sha256(payload).hexdigest() == descriptor["sha256"]
        payloads[suffix] = payload.decode("utf-8")
    prompt = [line.strip() for line in payloads["raw.txt"].splitlines() if line.strip()][-1]
    clean = module.parse(identity, observation["capability"], payloads["txt"])
    original = module.parse(
        identity,
        observation["capability"],
        payloads["raw.txt"],
        command=observation["command"],
        prompt=prompt,
    )
    assert clean.status == original.status == observation["expected_status"] == "complete"
    assert not clean.warnings and not original.warnings
    assert not clean.rejected_count and not original.rejected_count
    assert clean.records == original.records == []
    assert clean.summary == original.summary
    for parsed in (clean, original):
        validate_records(
            FactInput(
                capability=observation["capability"],
                observed_at=observation["observed_at"],
                status=parsed.status,
                source="offline",
                platform_profile=identity["profile_id"],
                parser_ref=parsed.parser_ref,
                evidence_ref="test:campus-ospf",
                records=parsed.records,
                summary=parsed.fact_summary(),
            )
        )
    if observation["device"].startswith("acc"):
        assert clean.summary == {} and clean.sources == []
    elif observation["capability"] == "ospf_interfaces":
        expected = (
            4 if family == "comware" else 6 if observation["device"].startswith("core") else 5
        )
        assert len(clean.summary["interfaces"]) == expected
    elif family == "comware":
        assert len(clean.summary["interfaces"]) == 4
        loopback = next(row for row in clean.summary["interfaces"] if row["ifname"] == "LoopBack0")
        assert loopback["cost"] == 0
        assert clean.summary["processes"][0]["vrf"] == "default"
    else:
        assert len(clean.summary["processes"]) == 1
        assert "vrf" not in clean.summary["processes"][0]


def test_ospf_profiles_modes_import_contract_and_summary_only_storage():
    from scripts.topology.import_lab_fixtures import CAMPUS_OSPF, LABS, command_of
    from src.apps.topology.backend.collectors.profiles import load_profiles
    from src.apps.topology.backend.facts.models import RECORD_MODELS

    assert LABS["campus-ospf"] is CAMPUS_OSPF
    assert CAMPUS_OSPF.batch == MANIFEST["batch"] == "20261005-003643"
    # Two capabilities per device, plus the verbose interface table of the two Comware BRs.
    assert len(MANIFEST["observations"]) == 18 and len(CAMPUS_OSPF.devices) == 8
    for profile_id, commands in {
        "h3c-comware7": {
            "ospf_interfaces": "display ospf interface",
            "ospf_process": "display ospf",
        },
        "cisco-ios": {
            "ospf_interfaces": "show ip ospf interface brief",
            "ospf_process": "show ip ospf",
        },
    }.items():
        profile = load_profiles()[profile_id]
        family = profile.os_family
        for mode in ("topology", "standard", "full"):
            assert commands.keys() <= profile.selected(mode).keys()
        assert not commands.keys() & profile.selected("fast").keys()
        for cap, command in commands.items():
            # Rule governance publishes the command; the profile only selects the fact.
            assert rules.selection({"os_family": family}, cap).command == command
            assert command_of(cap, command)
            assert profile.capabilities[cap].parser == "rules"
            assert cap not in RECORD_MODELS


@pytest.mark.parametrize("capability", ["ospf_interfaces", "ospf_process"])
@pytest.mark.parametrize("damage", ["transport", "paging"])
def test_ospf_transport_gate_survives_public_observation_and_blocks_router_id_and_metric(
    capability, damage
):
    from types import SimpleNamespace

    from src.apps.topology.backend.collectors.identify import Identity
    from src.apps.topology.backend.model.builder import L3T, NT, build_topology_details
    from src.apps.topology.backend.service import topology_observation

    module = importlib.import_module("src.apps.topology.backend.collectors.parse")
    identity = Identity("Cisco", "ios", "cisco-ios", "show", "high", "identified")
    campus = FIXTURES.parent

    def build(damaged):
        devices = {}
        for index, name in ((13, "core1"), (14, "core2")):
            parsed = {}
            for cap, filename, directory in (
                ("lldp", "lldp_verbose", campus),
                ("interfaces", "interfaces_description", campus),
                ("ip", "ip_interface", campus),
                ("ospf_interfaces", "ospf_interfaces", FIXTURES),
                ("ospf_process", "ospf_process", FIXTURES),
            ):
                text = (directory / name / f"{filename}.txt").read_bytes().decode("utf-8")
                affected = damaged and name == "core1" and cap == capability
                parsed[cap] = module.parse(
                    identity,
                    cap,
                    text + "\n--More--\n" if affected and damage == "paging" else text,
                    complete=not (affected and damage == "transport"),
                )
            observation = topology_observation(
                SimpleNamespace(
                    parsed=parsed,
                    identity=identity,
                    profile_id=identity.profile_id,
                    mode="topology",
                    driver="paramiko",
                )
            )
            assert observation["complete"]
            if damaged and name == "core1":
                assert (
                    "collection_incomplete"
                    in observation["facts"][capability]["parse_metadata"]["warnings"]
                )
            devices[f"10.10.1.{index}"] = {"platform": "ios", **observation["facts"]}
        return build_topology_details(devices, "urn:test:ospf-transport:")

    def subnet_metric(tree):
        return next(
            link.get(L3T + "l3-link-attributes", {}).get("metric1")
            for network in tree["ietf-network:networks"]["network"]
            for link in network.get(NT + "link", [])
            if link.get(L3T + "l3-link-attributes", {}).get("name") == "10.0.34.0/31"
        )

    good, _, _ = build(False)
    assert subnet_metric(good) == "10"
    tree, _, protocols = build(True)
    assert subnet_metric(tree) is None
    device = protocols["devices"]["10.10.1.13"]
    assert device["router_ids"] == {} and not device["interfaces"]
    assert any(gap["code"] == "ospf_collection_incomplete" for gap in device["gaps"])
