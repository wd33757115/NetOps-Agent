"""Per-VLAN forwarding over the campus-wan L2 links (implementation plan §8 M3).

Real rapid-PVST, trunk and EtherChannel captures (tests/fixtures/topology/campus-wan),
plus single named mutations of them for the scenarios the lab does not show.
"""

import copy
from types import SimpleNamespace

import pytest
from test_topology_campus_wan import EXPECTED, FIXTURES, IDENTITY, parsed

from src.apps.topology.backend.collectors.parse import parse
from src.apps.topology.backend.model.builder import build_topology
from src.apps.topology.backend.model.l2_forwarding import analyse
from src.apps.topology.backend.service import topology_observation

BASE = "urn:test:campus:"
ADDRESS = {name: spec["management"] for name, spec in EXPECTED["devices"].items()}
NAME = {address: name for name, address in ADDRESS.items()}


@pytest.fixture(scope="module")
def campus():
    devices = {}
    for name, spec in EXPECTED["devices"].items():
        identity = IDENTITY[spec["platform"]]
        outcome = topology_observation(
            SimpleNamespace(
                parsed=parsed(name),
                identity=identity,
                profile_id=identity.profile_id,
                mode="topology",
                driver="paramiko",
            )
        )
        devices[spec["management"]] = {**outcome["facts"], "platform": spec["platform"]}
    return devices


def run(devices):
    tree, _ = build_topology(devices, BASE)
    return analyse(devices, tree, BASE)


def link(result, a, a_port, b, b_port):
    wanted = {(ADDRESS[a], a_port), (ADDRESS[b], b_port)}
    for entry in result["links"].values():
        if {(end["device"], end["interface"]) for end in entry["ends"]} == wanted:
            return entry
    raise AssertionError(f"no link {a} {a_port} - {b} {b_port}")


def status(entry, vid):
    return entry["vlans"][str(vid)]["status"]


def port(devices, device, cap, ifname):
    return next(p for p in devices[ADDRESS[device]][cap]["ports"] if p["ifname"] == ifname)


def test_stp_parser_reads_per_vlan_roles_and_states():
    result = parse(IDENTITY["ios"], "stp", (FIXTURES / "acc1/stp.txt").read_text("utf-8"))
    assert result.status == "complete"
    rows = {(r["vlan"], r["ifname"]): (r["role"], r["state"]) for r in result.records}
    assert rows[(10, "Ethernet0/1")] == ("root", "forwarding")
    assert rows[(10, "Ethernet0/2")] == ("alternate", "blocking")
    assert rows[(20, "Ethernet0/1")] == ("alternate", "blocking")
    assert rows[(20, "Ethernet0/2")] == ("root", "forwarding")
    instances = {block["vlan"]: block for block in result.summary["instances"]}
    assert instances[10]["root_port"] == "Ethernet0/1" and not instances[10]["is_root"]


def test_stp_parser_marks_the_root_bridge_and_edge_ports():
    result = parse(IDENTITY["ios"], "stp", (FIXTURES / "dist1/stp.txt").read_text("utf-8"))
    roots = {b["vlan"]: b["is_root"] for b in result.summary["instances"]}
    assert roots == {10: True, 20: False}
    acc = parse(IDENTITY["ios"], "stp", (FIXTURES / "acc1/stp.txt").read_text("utf-8"))
    edge = {(r["vlan"], r["ifname"]): r["edge"] for r in acc.records}
    assert edge[(10, "Ethernet1/0")] is True and edge[(10, "Ethernet0/1")] is False


def test_dual_uplinks_forward_one_vlan_each_and_block_the_other(campus):
    result = run(campus)
    to_dist1 = link(result, "dist1", "Ethernet2/0", "acc1", "Ethernet0/1")
    to_dist2 = link(result, "dist2", "Ethernet2/1", "acc1", "Ethernet0/2")
    assert status(to_dist1, 10) == "forwarding" and status(to_dist2, 20) == "forwarding"
    blocked = to_dist1["vlans"]["20"]
    assert blocked == {
        "status": "blocked",
        "at": ADDRESS["acc1"],
        "port": "Ethernet0/1",
        "state": "blocking",
        "role": "alternate",
    }
    assert status(to_dist2, 10) == "blocked"
    # An up link is not a forwarding VLAN: the VLAN still reaches every switch once.
    for vid in ("10", "20"):
        assert [sorted(NAME[d] for d in group) for group in result["bridge_domains"][vid]] == [
            ["acc1", "acc2", "dist1", "dist2"]
        ]


def test_routed_links_are_not_judged_as_l2(campus):
    result = run(campus)
    devices = {NAME[end["device"]] for entry in result["links"].values() for end in entry["ends"]}
    assert devices == {"dist1", "dist2", "acc1", "acc2"}


def test_aggregate_member_links_are_judged_on_the_bundle(campus):
    result = run(campus)
    member = link(result, "dist1", "Ethernet1/0", "dist2", "Ethernet1/0")
    assert {end.get("aggregate") for end in member["ends"]} == {"Port-channel1"}
    assert status(member, 10) == status(member, 20) == "forwarding"


def test_a_member_that_left_the_bundle_does_not_carry_vlans(campus):
    devices = copy.deepcopy(campus)
    bundle = devices[ADDRESS["dist1"]]["lag"][0]
    bundle["bundled"].remove("Ethernet1/1")
    bundle["member_flags"]["Ethernet1/1"] = "s"
    result = run(devices)
    gone = link(result, "dist1", "Ethernet1/1", "dist2", "Ethernet1/1")
    kept = link(result, "dist1", "Ethernet1/0", "dist2", "Ethernet1/0")
    assert gone["vlans"]["10"] == {
        "status": "member-not-bundled",
        "at": ADDRESS["dist1"],
        "port": "Ethernet1/1",
    }
    assert status(kept, 10) == "forwarding"


def test_native_vlan_mismatch_is_an_issue(campus):
    devices = copy.deepcopy(campus)
    for cap in ("trunk", "switchport"):
        port(devices, "dist1", cap, "Ethernet2/0")["native_vlan"] = 99
    entry = link(run(devices), "dist1", "Ethernet2/0", "acc1", "Ethernet0/1")
    assert any(
        issue["code"] == "native_vlan_mismatch" and sorted(issue["native_vlans"]) == [1, 99]
        for issue in entry["issues"]
    )


def test_allowed_lists_without_a_common_vlan_carry_nothing(campus):
    devices = copy.deepcopy(campus)
    for cap in ("trunk", "switchport"):
        port(devices, "acc1", cap, "Ethernet0/1")["trunk_vlans"] = "30"
    trunk = port(devices, "acc1", "trunk", "Ethernet0/1")
    trunk["active_vlans"] = trunk["forwarding_vlans"] = "30"
    entry = link(run(devices), "dist1", "Ethernet2/0", "acc1", "Ethernet0/1")
    assert {"code": "allowed_vlans_disjoint"} in entry["issues"]
    assert {status(entry, vid) for vid in (10, 20, 30)} == {"not-admitted"}


def test_access_port_facing_a_trunk_is_a_tagging_mismatch(campus):
    devices = copy.deepcopy(campus)
    acc = devices[ADDRESS["acc1"]]
    acc["trunk"]["ports"] = [p for p in acc["trunk"]["ports"] if p["ifname"] != "Ethernet0/1"]
    port(devices, "acc1", "switchport", "Ethernet0/1").update(
        l2_mode="access", admin_mode="static access", oper_mode="static access", access_vlan=10
    )
    entry = link(run(devices), "dist1", "Ethernet2/0", "acc1", "Ethernet0/1")
    assert status(entry, 10) == "tagging-mismatch"
    assert {"code": "access_trunk_mismatch"} in entry["issues"]


def test_without_spanning_tree_evidence_forwarding_is_unknown(campus):
    devices = copy.deepcopy(campus)
    for name in ("dist1", "acc1"):
        facts = devices[ADDRESS[name]]
        facts.pop("stp", None)
        for trunk in facts["trunk"]["ports"]:
            trunk.pop("forwarding_vlans", None)
    entry = link(run(devices), "dist1", "Ethernet2/0", "acc1", "Ethernet0/1")
    assert entry["vlans"]["10"]["status"] == "unknown"
    assert entry["vlans"]["10"]["missing"] == "stp_state"
