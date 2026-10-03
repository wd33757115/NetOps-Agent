"""OSPF/BGP protocol instances and adjacencies (implementation plan §8 M4).

Real campus-wan captures: OSPF process 1 in areas 0/10, iBGP AS 65000 with the cores as
route reflectors, eBGP from the borders to an ISP outside the collection.
"""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_topology_campus_wan import EXPECTED, FIXTURES, IDENTITY, parsed

from src.apps.topology.backend.collectors.parse import parse
from src.apps.topology.backend.model.device_control import project_device_control
from src.apps.topology.backend.model.protocols import adjacencies, project_protocols
from src.apps.topology.backend.service import topology_observation

YANG = Path(__file__).resolve().parents[2] / "src/apps/topology/resources/yang"
ADDRESS = {name: spec["management"] for name, spec in EXPECTED["devices"].items()}
NAME = {address: name for name, address in ADDRESS.items()}


def observation(name):
    identity = IDENTITY[EXPECTED["devices"][name]["platform"]]
    return topology_observation(
        SimpleNamespace(
            parsed=parsed(name),
            identity=identity,
            profile_id=identity.profile_id,
            mode="topology",
            driver="paramiko",
        )
    )


@pytest.fixture(scope="module")
def campus():
    return {
        ADDRESS[name]: {**observation(name)["facts"], "platform": spec["platform"]}
        for name, spec in EXPECTED["devices"].items()
    }


def sample(name, file):
    return (FIXTURES / "protocols" / name / file).read_text("utf-8")


def instances(facts, kind):
    return [i for i in project_protocols(facts)["instances"]["default"] if i["type"].endswith(kind)]


def test_ospf_neighbor_parser_reads_both_layouts():
    ios = parse(IDENTITY["ios"], "ospf_neighbors", sample("core1", "ospf.txt"))
    assert ios.status == "complete" and not ios.rejected_count
    rows = {r["neighbor_router_id"]: r for r in ios.records}
    # "FULL/  -" on a point-to-point link: full, no DR role.
    assert rows["10.255.0.4"] == {
        "neighbor_router_id": "10.255.0.4",
        "address": "10.0.34.1",
        "priority": 0,
        "state": "full",
        "role": "-",
        "dead_time": "00:00:33",
        "ifname": "Ethernet0/3",
    }
    comware = parse(IDENTITY["comware"], "ospf_neighbors", sample("br1", "ospf.txt"))
    assert {(r["process"], r["area"], r["state"]) for r in comware.records} == {
        ("1", "0.0.0.0", "full")
    }
    assert comware.summary["router_ids"] == {"1": "10.255.0.1"}


def test_bgp_peer_parser_reads_both_layouts_and_absent_bgp():
    ios = parse(IDENTITY["ios"], "bgp_peers", sample("core1", "bgp.txt"))
    assert ios.summary == {"router_id": "10.255.0.3", "local_as": 65000}
    assert {r["peer"]: r["prefixes_received"] for r in ios.records}["10.255.0.1"] == 4
    comware = parse(IDENTITY["comware"], "bgp_peers", sample("br1", "bgp.txt"))
    isp = next(r for r in comware.records if r["peer"] == "100.64.1.1")
    assert isp == {
        "peer": "100.64.1.1",
        "peer_as": 65100,
        "state": "established",
        "up_down": "01:40:21",
        "prefixes_received": 3,
    }
    # ACC1 runs neither: "% BGP not active" and an empty OSPF neighbour table.
    for cap, file in (("bgp_peers", "bgp.txt"), ("ospf_neighbors", "ospf.txt")):
        result = parse(IDENTITY["ios"], cap, sample("acc1", file))
        assert result.status == "complete" and result.records == []


def test_border_instances_name_the_process_and_the_as(campus):
    facts = campus[ADDRESS["br1"]]
    (ospf,) = instances(facts, "ospfv2")
    assert ospf["name"] == "1"
    assert ospf["ietf-ospf:ospf"]["router-id"] == "10.255.0.1"
    area = ospf["ietf-ospf:ospf"]["areas"]["area"][0]
    neighbours = {
        n["neighbor-router-id"]: n["state"]
        for i in area["interfaces"]["interface"]
        for n in i.get("neighbors", {}).get("neighbor", [])
    }
    assert neighbours == {"10.255.0.2": "full", "10.255.0.3": "full", "10.255.0.4": "full"}
    (bgp,) = instances(facts, "bgp")
    assert bgp["name"] == "65000"
    assert bgp["ietf-bgp:bgp"]["global"] == {"as": 65000, "identifier": "10.255.0.1"}
    types = {
        n["remote-address"]: n["peer-type"] for n in bgp["ietf-bgp:bgp"]["neighbors"]["neighbor"]
    }
    assert types == {"10.255.0.3": "internal", "10.255.0.4": "internal", "100.64.1.1": "external"}


def test_a_device_without_protocols_has_no_instances(campus):
    assert project_protocols(campus[ADDRESS["acc1"]])["instances"] == {}


def test_ospf_instance_validates_with_the_frozen_rfc9129_modules():
    yangson = pytest.importorskip("yangson", reason="Install tests/topology-requirements.txt")
    content = pytest.importorskip("yangson.enumerations").ContentType.all
    model = yangson.DataModel((YANG / "device-library.json").read_text(), [str(YANG)])
    outcome = observation("dist1")
    obs = {"data": {k: outcome[k] for k in ("facts", "capabilities", "fact_metadata")}}
    tree = project_device_control(obs)["routing"][0]["tree"]
    routing = tree["ietf-routing:routing"]["control-plane-protocols"]
    # BGP (draft -21) is validated by libyang in the M1 cases; yangson cannot load it.
    routing["control-plane-protocol"] = [
        i for i in routing["control-plane-protocol"] if i["type"].endswith("ospfv2")
    ]
    # Test-only completion of the interface counters the CLI never gives.
    for interface in tree["ietf-interfaces:interfaces"]["interface"]:
        interface["statistics"] = {"discontinuity-time": "2000-01-01T00:00:00Z"}
    model.from_raw(tree).validate(ctype=content)


def test_every_ospf_adjacency_is_full_from_both_ends(campus):
    result = adjacencies(campus)["ospf"]
    pairs = sorted(sorted([NAME[x["a"]["device"]], NAME[x["b"]["device"]]]) for x in result)
    assert pairs == [
        ["br1", "br2"],
        ["br1", "core1"],
        ["br1", "core2"],
        ["br2", "core1"],
        ["br2", "core2"],
        ["core1", "core2"],
        ["core1", "dist1"],
        ["core1", "dist2"],
        ["core2", "dist1"],
        ["core2", "dist2"],
    ]
    assert {x["status"] for x in result} == {"full"}


def test_bgp_sessions_pair_once_and_name_the_isp_as_outside(campus):
    result = adjacencies(campus)["bgp"]
    internal = [x for x in result if x["status"] == "established"]
    assert len(internal) == 9 and {x["type"] for x in internal} == {"internal"}
    outside = sorted((NAME[x["a"]["device"]], x["peer"]) for x in result if "b" not in x)
    assert outside == [("br1", "100.64.1.1"), ("br2", "100.64.2.1")]
    assert all(
        x["status"] == "peer_outside_collection" and x["type"] == "external"
        for x in result
        if "b" not in x
    )


def test_a_one_sided_or_stuck_adjacency_is_not_full(campus):
    devices = copy.deepcopy(campus)
    core1 = devices[ADDRESS["core1"]]
    core1["ospf_neighbors"] = [
        r for r in core1["ospf_neighbors"] if r["neighbor_router_id"] != "10.255.0.5"
    ]
    dist2 = next(r for r in core1["ospf_neighbors"] if r["neighbor_router_id"] == "10.255.0.6")
    dist2["state"] = "exstart"
    result = {
        tuple(sorted([NAME[x["a"]["device"]], NAME[x["b"]["device"]]])): x["status"]
        for x in adjacencies(devices)["ospf"]
    }
    assert result[("core1", "dist1")] == "one_sided"
    assert result[("core1", "dist2")] == "not_full"


def test_a_router_id_claimed_twice_is_ambiguous_not_overwritten(campus):
    devices = copy.deepcopy(campus)
    # DIST2 also reports DIST1's router ID; an OSPF ID is no device identity.
    process = devices[ADDRESS["dist2"]]["ospf_process"]
    process["processes"][0]["router_id"] = "10.255.0.5"
    for row in process.get("interfaces", []):
        row.pop("router_id", None)
    statuses = {
        x["status"]
        for x in adjacencies(devices)["ospf"]
        if x.get("neighbor_router_id") == "10.255.0.5"
    }
    assert "router_id_ambiguous" in statuses


def test_a_bgp_session_up_on_one_end_only_is_not_established(campus):
    devices = copy.deepcopy(campus)
    peers = devices[ADDRESS["dist1"]]["bgp_peers"]["peers"]
    next(p for p in peers if p["peer"] == "10.255.0.3")["state"] = "active"
    session = next(
        x
        for x in adjacencies(devices)["bgp"]
        if {NAME.get(x["a"]["device"]), NAME.get(x.get("b", {}).get("device"))}
        == {"core1", "dist1"}
    )
    assert session["status"] == "not_established"


def test_a_neighbour_on_an_interface_without_ospf_facts_is_a_gap(campus):
    facts = copy.deepcopy(campus[ADDRESS["br1"]])
    facts["ospf_neighbors"][0]["ifname"] = "GigabitEthernet9/0"
    result = project_protocols(facts)
    assert {
        "code": "ospf_neighbor_interface_unbound",
        "record_index": 0,
        "interface": "GigabitEthernet9/0",
    } in result["gaps"]
    json.dumps(result)  # JSON-friendly
