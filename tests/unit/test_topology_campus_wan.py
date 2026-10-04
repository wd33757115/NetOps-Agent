"""Campus + WAN lab replay: H3C VSR borders and Cisco IOL core, distribution and access.

Real outputs (tests/fixtures/topology/campus-wan) through the platform parsers and the
SIMAP builder, checked against the lab wiring in expected.json.
"""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

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
from src.apps.topology.backend.model.path import trace
from src.apps.topology.backend.service import topology_observation

FIXTURES = Path(__file__).resolve().parents[2] / "tests/fixtures/topology/campus-wan"
MANIFEST = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
VLAN_FIXTURES = FIXTURES / "vlan-trunk"
VLAN_MANIFEST = json.loads((VLAN_FIXTURES / "manifest.json").read_text(encoding="utf-8"))
EXPECTED = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))
IDENTITY = {
    "comware": Identity("H3C", "comware", "h3c-comware7", "display", "high", "identified"),
    "ios": Identity("Cisco", "ios", "cisco-ios", "show", "high", "identified"),
}
# Builder fact <- fixture file, per platform (the profiles' topology commands).
FACTS = {
    "comware": {
        "version": "version",
        "lldp_local": "lldp_local",
        "lldp": "lldp_verbose",
        "interfaces": "interfaces",
        "ip": "ip_interfaces",
        "arp": "arp",
        "mac": "mac",
        "vlan": "vlan",
        "trunk": "trunk",
        "lag": "lag",
        "vrrp": "vrrp",
        "routes": "routes",
    },
    "ios": {
        "version": "version",
        "lldp": "lldp_verbose",
        "interfaces": "interfaces_description",
        "ip": "ip_interface",
        "arp": "arp",
        "mac": "mac",
        "vlan": "vlan",
        "trunk": "trunk",
        "switchport": "switchport",
        "lag": "lag",
        "stp": "stp",
        "vrrp": "vrrp",
        "routes": "routes",
    },
}
BASE = "urn:test:campus:"


# Protocol facts live in their own reviewed fixture directories: neighbours from the
# same capture, OSPF interface/process tables from the OSPF capture.
PROTOCOL_FACTS = {
    "ospf_neighbors": "protocols/{device}/ospf",
    "bgp_peers": "protocols/{device}/bgp",
    "ospf_interfaces": "ospf/{device}/ospf_interfaces",
    "ospf_process": "ospf/{device}/ospf_process",
}


def parsed(device):
    """Routers rejected the aggregation command, so it was not imported for them."""
    platform = EXPECTED["devices"][device]["platform"]
    protocols = {
        cap: parse(
            IDENTITY[platform],
            cap,
            (FIXTURES / f"{path.format(device=device)}.txt").read_text("utf-8"),
        )
        for cap, path in PROTOCOL_FACTS.items()
    }
    return protocols | {
        cap: parse(
            IDENTITY[platform],
            cap,
            (
                (VLAN_FIXTURES if cap in {"vlan", "trunk", "switchport"} else FIXTURES)
                / device
                / f"{name}.txt"
            ).read_text("utf-8"),
        )
        for cap, name in FACTS[platform].items()
        if (
            (VLAN_FIXTURES if cap in {"vlan", "trunk", "switchport"} else FIXTURES)
            / device
            / f"{name}.txt"
        ).exists()
    }


@pytest.fixture(scope="module")
def devices():
    result = {}
    for device, spec in EXPECTED["devices"].items():
        outcome = topology_observation(
            SimpleNamespace(
                parsed=parsed(device),
                identity=IDENTITY[spec["platform"]],
                profile_id=IDENTITY[spec["platform"]].profile_id,
                mode="topology",
                driver="paramiko",
            )
        )
        assert outcome["complete"] and outcome["failure_code"] is None
        result[spec["management"]] = {**outcome["facts"], "platform": spec["platform"]}
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
    protocol_manifests = [
        json.loads((FIXTURES / name / "manifest.json").read_text(encoding="utf-8"))
        for name in ("protocols", "ospf")
    ]
    expected = {
        (o["device"], o["capability"]): o["expected_status"]
        for o in [
            *MANIFEST["observations"],
            *VLAN_MANIFEST["observations"],
            *(o for m in protocol_manifests for o in m["observations"]),
        ]
    }
    for device, spec in EXPECTED["devices"].items():
        for cap, outcome in parsed(device).items():
            fixture = FACTS[spec["platform"]].get(cap) or PROTOCOL_FACTS[cap].rsplit("/", 1)[1]
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
    # The local MGMT name is not a second global broadcast/routing domain.
    assert {link[L3T + "l3-link-attributes"]["name"] for link in links} == {
        subnet.removeprefix("MGMT:") for subnet in EXPECTED["subnets"]
    }


def test_local_network_instances_have_distinct_nodes_in_observed_l3_graph(l2):
    # RFC 8529 NI names are device-local; logical RFC 8345 nodes support the L2 entity.
    assert len(l2) == 2
    observed = l2[1]
    assert observed["supporting-network"] == [{"network-ref": l2[0]["network-id"]}]
    names = {n["node-id"]: n.get(L2T + "l2-node-attributes", {}).get("name") for n in l2[0]["node"]}
    mgmt_nodes = [n for n in observed["node"] if n.get(ST + "name") == "MGMT"]
    assert sorted(names[n["supporting-node"][0]["node-ref"]] for n in mgmt_nodes) == sorted(
        d["name"] for d in EXPECTED["devices"].values() if d["platform"] == "ios"
    )
    # No same-name NI/prefix inference joins the six unobserved management attachments.
    management_ids = {n["node-id"] for n in mgmt_nodes}
    assert not any(
        tp["node-ref"] in management_ids for link in observed[NT + "link"] for tp in link[ST + "tp"]
    )


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


@pytest.mark.parametrize("vip", sorted(EXPECTED["vrrp"]))
def test_vrrp_virtual_address_locates_as_virtual_router(devices, vip):
    answer = locate(devices, BASE, vip)
    assert answer["status"] == "virtual_router"
    members = [[m["name"], m["interface"], m["vrid"], m["state"]] for m in answer["members"]]
    assert sorted(members) == sorted(EXPECTED["vrrp"][vip])
    # RFC 8347 last-adv-source: every member names the same master.
    assert len({m["last_adv_source"] for m in answer["members"]}) == 1


def test_first_hop_is_the_vrrp_master_of_each_subnet(devices):
    # PC1 and PC4 have different VRRP masters, so the candidate device sequences
    # differ. In this capture DIST1 holds PC4's ARP entry (delivery proven) but DIST2
    # holds none for PC1, so the way back is unproven and no symmetry is claimed.
    result = trace(devices, BASE, "172.16.10.11", "172.16.20.14")
    forward, reverse = result["forward"], result["reverse"]
    assert (forward["first_hop"], reverse["first_hop"]) == ("vrrp_master", "vrrp_master")
    assert [h["device"]["name"] for h in forward["paths"][0]["hops"]] == ["DIST1.lab.local"]
    assert [h["device"]["name"] for h in reverse["paths"][0]["hops"]] == ["DIST2.lab.local"]
    assert (forward["status"], forward["paths"][0]["end"]["status"]) == ("complete", "delivered")
    assert reverse["status"] == "partial"
    assert reverse["paths"][0]["end"]["status"] == "endpoint_unresolved"
    assert result["symmetric"] is None
    assert any(gap["status"] == "endpoint_unresolved" for gap in result["gaps"])


def test_vrrp_block_without_a_virtual_address_is_rejected():
    text = (FIXTURES / "br2/vrrp.txt").read_text("utf-8")
    assert parse(IDENTITY["comware"], "vrrp", text).status == "complete"
    odd = "\n".join(line for line in text.splitlines() if "Virtual IP" not in line)
    outcome = parse(IDENTITY["comware"], "vrrp", odd)
    assert outcome.status == "failed"
    assert any(w.endswith("vrrp_block_without_virtual_address") for w in outcome.warnings)


def test_recursive_bgp_next_hop_goes_through_the_core(devices):
    # DIST1's default and internet routes are iBGP via BR1's loopback 10.255.0.1 with no
    # interface (RFC 8349 simple next hop); OSPF resolves it over both cores.
    forward = trace(devices, BASE, "172.16.10.11", "198.51.100.10")["forward"]
    sequences = {
        tuple(h["device"]["name"].split(".")[0] for h in path["hops"]) for path in forward["paths"]
    }
    assert sequences == {("DIST1", "CORE1", "BR1"), ("DIST1", "CORE2", "BR1")}
    for path in forward["paths"]:
        assert path["end"] == {"status": "unmanaged_next_hop", "next_hop": "100.64.1.1"}
        first = path["hops"][0]
        assert first["route"]["next_hop"] == "10.255.0.1"
        assert [r["prefix"] for r in first["resolved_via"]] == ["10.255.0.1/32"]


def test_aggregate_is_an_rfc8944_lag_of_its_members(l2):
    nodes = {n.get(L2T + "l2-node-attributes", {}).get("name"): n for n in l2[0]["node"]}
    for switch in ("DIST1.lab.local", "DIST2.lab.local"):
        tps = {
            tp[L2T + "l2-termination-point-attributes"]["interface-name"]: tp
            for tp in nodes[switch][NT + "termination-point"]
        }
        attrs = tps["Port-channel1"][L2T + "l2-termination-point-attributes"]
        assert attrs["lag"] is True
        assert attrs["member-link-tp"] == [tps[m]["tp-id"] for m in ("Ethernet1/0", "Ethernet1/1")]


def test_nodes_carry_rfc8944_management_attributes(l2):
    # The address it was collected by plus what its neighbours' LLDP advertises: the
    # evidence that made the IOS core one node; the chassis MAC is the bridge base MAC.
    nodes = {n.get(L2T + "l2-node-attributes", {}).get("name"): n for n in l2[0]["node"]}
    core1 = nodes["CORE1.lab.local"][L2T + "l2-node-attributes"]
    assert core1["management-mac"] == "aa:bb:cc:00:02:00"
    assert {"10.10.1.13", "10.0.13.2", "10.0.35.1"} <= set(core1["management-address"])
    br1 = nodes["BR1"][L2T + "l2-node-attributes"]
    assert "10.10.1.11" in br1["management-address"] and br1["management-mac"]


@pytest.mark.parametrize("device", ["dist1", "dist2", "acc1", "acc2"])
def test_campus_trunk_tags_use_allowed_vlans_not_stp_subset(devices, l2, device):
    spec = EXPECTED["devices"][device]
    facts = devices[spec["management"]]
    node = next(
        n
        for n in l2[0]["node"]
        if n.get(L2T + "l2-node-attributes", {}).get("name") == spec["name"]
    )
    attrs = {
        tp[L2T + "l2-termination-point-attributes"]["interface-name"]: tp[
            L2T + "l2-termination-point-attributes"
        ]
        for tp in node[NT + "termination-point"]
    }
    for port in facts["trunk"]["ports"]:
        assert port["trunk_vlans"] == "10,20"
        assert attrs[port["ifname"]]["outer-tag"] == "10,20"
        assert attrs[port["ifname"]]["encapsulation-type"] == L2T + "vlan"
    if device.startswith("acc"):
        assert any(p["forwarding_vlans"] != "10,20" for p in facts["trunk"]["ports"])


@pytest.mark.parametrize(
    "device,port,vid",
    [("acc1", "Ethernet1/0", 10), ("acc1", "Ethernet1/1", 20), ("acc2", "Ethernet1/0", 10)],
)
def test_campus_access_pvid_is_retained_without_a_wire_tag(devices, l2, device, port, vid):
    spec = EXPECTED["devices"][device]
    rows = devices[spec["management"]]["switchport"]["ports"]
    record = next(p for p in rows if p["ifname"] == port)
    assert (record["l2_mode"], record["access_vlan"], record["pvid"]) == ("access", vid, vid)
    node = next(
        n
        for n in l2[0]["node"]
        if n.get(L2T + "l2-node-attributes", {}).get("name") == spec["name"]
    )
    attrs = next(
        tp[L2T + "l2-termination-point-attributes"]
        for tp in node[NT + "termination-point"]
        if tp[L2T + "l2-termination-point-attributes"]["interface-name"] == port
    )
    assert "outer-tag" not in attrs


@pytest.fixture(scope="module")
def with_mgmt(devices):
    """Add the IOS devices' "show ip route vrf MGMT" tables, as the scoped collector does."""
    result = copy.deepcopy(devices)
    for device, spec in EXPECTED["devices"].items():
        path = FIXTURES / "vrf-routes" / device / "routes_mgmt.txt"
        if path.exists():
            table = parse(IDENTITY["ios"], "routes", path.read_text("utf-8"))
            assert table.status == "complete" and {r["vrf"] for r in table.records} == {"MGMT"}
            result[spec["management"]]["routes"] += table.records
    return result


def test_same_prefix_in_the_management_vrf_stays_isolated(with_mgmt):
    # DIST1 holds 10.10.1.0/24 connected in MGMT only; the global lookup takes the BGP
    # default to BR1, where the prefix really is global, and no BR1 ARP proves .13.
    result = trace(with_mgmt, BASE, "172.16.10.11", "10.10.1.13")
    for path in result["forward"]["paths"]:
        assert [(h["device"]["name"].split(".")[0], h["vrf"]) for h in path["hops"]][::2] == [
            ("DIST1", "default"),
            ("BR1", "default"),
        ]
        assert path["hops"][0]["route"]["prefix"] == "0.0.0.0/0"
        assert path["end"]["status"] == "endpoint_unresolved"
    # CORE1's management table has no route back to a user subnet.
    [back] = result["reverse"]["paths"]
    assert [(h["device"]["name"].split(".")[0], h["vrf"]) for h in back["hops"]] == [
        ("CORE1", "MGMT")
    ]
    assert back["end"] == {"status": "no_route"}
    assert result["symmetric"] is None
