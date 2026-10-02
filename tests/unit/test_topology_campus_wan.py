"""Campus + WAN lab replay: H3C VSR borders and Cisco IOL core, distribution and access.

Real outputs (tests/fixtures/topology/campus-wan) through the platform parsers and the
SIMAP builder, checked against the lab wiring in expected.json.
"""

import json
from pathlib import Path

import pytest

from src.apps.topology.backend.collectors.identify import Identity
from src.apps.topology.backend.collectors.parse import parse
from src.apps.topology.backend.model.builder import (
    L2T,
    L3T,
    NT,
    ST,
    VIRTUAL_ROUTER_MACS,
    build_topology,
    device_node_ids,
)
from src.apps.topology.backend.model.locate import locate

FIXTURES = Path(__file__).resolve().parents[2] / "tests/fixtures/topology/campus-wan"
MANIFEST = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
EXPECTED = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))
IDENTITY = {
    "comware": Identity("H3C", "comware", "h3c-comware7", "display", "high", "identified"),
    "ios": Identity("Cisco", "ios", "cisco-ios", "show", "high", "identified"),
}
# Builder fact <- fixture file, per platform (the profiles' topology commands).
FACTS = {
    "comware": {
        "lldp_local": "lldp_local",
        "lldp": "lldp_verbose",
        "interfaces": "interfaces",
        "ip": "ip_interfaces",
        "arp": "arp",
        "mac": "mac",
        "routes": "routes",
    },
    "ios": {
        "lldp": "lldp_verbose",
        "interfaces": "interfaces_description",
        "ip": "ip_interface",
        "arp": "arp",
        "mac": "mac",
        "routes": "routes",
    },
}
BASE = "urn:test:campus:"


def parsed(device):
    platform = EXPECTED["devices"][device]["platform"]
    return {
        cap: parse(IDENTITY[platform], cap, (FIXTURES / device / f"{name}.txt").read_text("utf-8"))
        for cap, name in FACTS[platform].items()
    }


@pytest.fixture(scope="module")
def devices():
    result = {}
    for device, spec in EXPECTED["devices"].items():
        facts = {"platform": spec["platform"]}
        for cap, outcome in parsed(device).items():
            if outcome.status in {"complete", "partial"}:
                facts[cap] = outcome.summary if cap == "lldp_local" else outcome.records
        result[spec["management"]] = facts
    return result


@pytest.fixture(scope="module")
def l2(devices):
    tree, _ = build_topology(devices, BASE)
    return tree["ietf-network:networks"]["network"]


def ends(network, link):
    nodes = {n["node-id"]: n for n in network["node"]}
    out = []
    for end in link[ST + "tp"]:
        node = nodes[end["node-ref"]]
        name = node.get(L2T + "l2-node-attributes", {}).get("name")
        tp = next(t for t in node[NT + "termination-point"] if t["tp-id"] == end["tp-ref"])
        port = tp.get(L2T + "l2-termination-point-attributes", {}).get("interface-name")
        out.append((name, port) if port else ("host", end["node-ref"]))
    return out


def test_every_modeled_capability_parses_completely():
    expected = {
        (o["device"], o["capability"]): o["expected_status"] for o in MANIFEST["observations"]
    }
    for device, spec in EXPECTED["devices"].items():
        for cap, outcome in parsed(device).items():
            fixture = FACTS[spec["platform"]][cap]
            assert outcome.status == expected[(device, fixture)], (device, cap, outcome.warnings)
            assert not outcome.rejected_count, (device, cap, outcome.warnings)


def test_cisco_devices_are_the_chassis_their_neighbours_advertise(devices):
    # IOS has no local LLDP command; its own addresses in neighbours' LLDP identify it.
    ids = device_node_ids(devices, BASE)
    assert len(set(ids.values())) == 8
    assert not [i for i in ids.values() if "target" in i]


def test_l2_cables_are_the_lab_wiring(l2):
    network = l2[0]
    cables = {
        frozenset(map(tuple, ends(network, link)))
        for link in network[NT + "link"]
        if not any(name == "host" for name, _ in ends(network, link))
        and all(
            name in {d["name"] for d in EXPECTED["devices"].values()}
            for name, _ in ends(network, link)
        )
        and len(link[ST + "tp"]) == 2
    }
    assert cables == {frozenset(map(tuple, cable)) for cable in EXPECTED["cables"]}
    # No link joins more than two device ports: aggregates and transit MACs merge nothing.
    for link in network[NT + "link"]:
        assert sum(name != "host" for name, _ in ends(network, link)) <= 2


def test_gateway_and_transit_macs_are_not_hosts(l2):
    hosts = [n["node-id"] for n in l2[0]["node"] if n["node-id"].startswith(BASE + "host:")]
    macs = [h.rsplit(":", 1)[-1].replace("-", ":") for h in hosts]
    assert not [m for m in macs if m.startswith(VIRTUAL_ROUTER_MACS)]
    # Five PCs, two ISP interfaces and the management host; no switch port MACs.
    assert len(hosts) == 8


@pytest.mark.parametrize("ip", sorted(EXPECTED["endpoints"]))
def test_endpoints_attach_where_the_lab_wired_them(devices, ip):
    want = EXPECTED["endpoints"][ip]
    answer = locate(devices, BASE, ip)
    (location,) = answer["locations"]
    assert location["status"] == want["status"]
    assert sorted([p["name"], p["port"]] for p in location["ports"]) == sorted(want["ports"])


def test_l3_subnets(l2):
    links = [link for network in l2[1:] for link in network[NT + "link"]]
    assert {link["link-id"].rsplit("l3-link:", 1)[-1] for link in links} == set(EXPECTED["subnets"])


def test_each_vrf_is_its_own_l3_network_over_l2(l2):
    # RFC 8345 §3 / RFC 9375 §4.2: a VRF is a separate entry in the network list.
    default, mgmt = l2[1], l2[2]
    assert mgmt["network-id"] == default["network-id"] + ":MGMT"
    assert mgmt[L3T + "l3-topology-attributes"] == {"name": "MGMT"}
    for network in (default, mgmt):
        assert network["supporting-network"] == [{"network-ref": l2[0]["network-id"]}]
    names = {n["node-id"]: n.get(L2T + "l2-node-attributes", {}).get("name") for n in l2[0]["node"]}
    assert sorted(names[n["node-id"]] for n in mgmt["node"]) == sorted(
        d["name"] for d in EXPECTED["devices"].values() if d["platform"] == "ios"
    )
    assert [link["link-id"].rsplit("l3-link:", 1)[-1] for link in mgmt[NT + "link"]] == [
        "MGMT:10.10.1.0/24"
    ]


def test_ios_route_table_with_an_unknown_line_is_rejected():
    text = (FIXTURES / "dist1/routes.txt").read_text("utf-8")
    assert parse(IDENTITY["ios"], "routes", text).status == "complete"
    broken = text.replace(
        "Gateway of last resort", "B  10.9.9.0/24 something new\nGateway of last resort"
    )
    assert parse(IDENTITY["ios"], "routes", broken).status == "failed"


def test_ios_address_line_in_an_unknown_phrasing_is_not_dropped_silently():
    text = (FIXTURES / "dist1/ip_interface.txt").read_text("utf-8")
    odd = text.replace(
        "  Internet address is 10.0.35.2/30", "  Internet address will be 10.0.35.2/30", 1
    )
    outcome = parse(IDENTITY["ios"], "ip", odd)
    assert outcome.status == "partial"
    assert any(w.endswith("unrecognized_address_line") for w in outcome.warnings)


def test_export_validates_against_the_frozen_yang_modules(devices):
    yangson = pytest.importorskip("yangson", reason="Install tests/topology-requirements.txt")
    content = pytest.importorskip("yangson.enumerations").ContentType.all
    root = FIXTURES.parents[3] / "src/apps/topology/resources/yang"
    model = yangson.DataModel((root / "library.json").read_text(), [str(root)])
    tree, _ = build_topology(devices, "urn:netops:topology:campus:")
    model.from_raw(tree).validate(ctype=content)
