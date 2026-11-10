"""Counterexamples for address, device-local NI and VLAN attachment identity."""

from copy import deepcopy
from itertools import permutations

import pytest

from src.apps.topology.backend.model.builder import L3T, NT, ST, _Topology, build_topology
from src.apps.topology.backend.model.locate import locate

BASE = "urn:test:identity:"
PORT = "Ethernet0/1"
MAC = "00:00:00:00:00:99"


def device(chassis, address, vrf="default", ifname=PORT):
    return {
        "platform": "ios",
        "lldp_local": {"chassis_id": chassis},
        "ip": [{"address": address, "prefix_len": 24, "ifname": ifname, "vrf": vrf}],
        "interfaces": [{"ifname": ifname, "type": "ethernet"}],
    }


def arp(ip, ifname=PORT, vrf="default", mac=MAC):
    return {"ip": ip, "ifname": ifname, "vrf": vrf, "mac": mac}


def lldp(peer, local=PORT, remote=PORT, address=None):
    return {
        "local_if": local,
        "remote_chassis_id": peer,
        "remote_port_id": remote,
        "remote_port_subtype": "Interface name",
        "remote_mgmt_addresses": [address] if address else [],
    }


def networks(tree):
    return tree["ietf-network:networks"]["network"]


def test_duplicate_ip_preserves_all_owners_and_cannot_choose_by_iteration_order():
    rows = {
        "a": device("00:00:00:00:00:01", "10.0.0.1"),
        "b": device("00:00:00:00:00:02", "10.0.0.2"),
        "c": device("00:00:00:00:00:03", "10.0.0.2"),
    }
    rows["a"]["arp"] = [arp("10.0.0.2")]
    trees = []
    for order in permutations(rows):
        topo = _Topology({key: rows[key] for key in order}, BASE)
        assert topo.ip_candidates("10.0.0.2") == (("b", PORT, "default"), ("c", PORT, "default"))
        assert topo.neighbour_candidates("a", "default", "10.0.0.2", PORT) == ()
        tree, _ = topo.build()
        assert not networks(tree)[0][NT + "link"]
        assert not networks(tree)[1][NT + "link"]
        trees.append(tree)
    assert all(tree == trees[0] for tree in trees)


def test_duplicate_ip_cannot_supply_unique_advertised_chassis_identity():
    rows = {
        "a": device("00:00:00:00:00:01", "10.0.0.1"),
        "b": device("00:00:00:00:00:02", "10.0.0.2"),
        "c": device("00:00:00:00:00:03", "10.0.0.2"),
    }
    rows["b"].pop("lldp_local")
    rows["c"].pop("lldp_local")
    rows["a"]["lldp"] = [lldp("00:00:00:00:00:02", address="10.0.0.2")]
    topo = _Topology(rows, BASE)
    assert topo.device_node(topo.chassis["b"]) == BASE + "device:target:b"
    assert topo.device_node(topo.chassis["c"]) == BASE + "device:target:c"


def test_exact_lldp_peer_and_port_can_disambiguate_duplicate_ip():
    rows = {
        "a": device("00:00:00:00:00:01", "10.0.0.1"),
        "b": device("00:00:00:00:00:02", "10.0.0.2", "MGMT"),
        "c": device("00:00:00:00:00:03", "10.0.0.2"),
    }
    rows["a"]["lldp"] = [lldp("00:00:00:00:00:02")]
    topo = _Topology(rows, BASE)
    assert topo.neighbour_candidates("a", "default", "10.0.0.2", PORT) == (("b", PORT, "MGMT"),)
    tree, _ = topo.build()
    (link,) = networks(tree)[1][NT + "link"]
    assert {tp["node-ref"] for tp in link[ST + "tp"]} == {
        topo.l3_node("a", "default"),
        topo.l3_node("b", "MGMT"),
    }


@pytest.mark.parametrize("vrf", ["default", "BLUE"])
def test_same_vrf_name_and_prefix_without_observations_prove_no_adjacency(vrf):
    rows = {
        "a": device("00:00:00:00:00:01", "10.0.0.1", vrf),
        "b": device("00:00:00:00:00:02", "10.0.0.2", vrf),
    }
    tree, _ = build_topology(rows, BASE)
    assert len(networks(tree)) == 2
    assert not networks(tree)[1][NT + "link"]


def test_scoped_arp_associates_unique_peer_with_different_local_ni_name():
    rows = {
        "a": device("00:00:00:00:00:01", "10.0.0.1"),
        "b": device("00:00:00:00:00:02", "10.0.0.2", "MGMT"),
    }
    rows["a"]["arp"] = [arp("10.0.0.2")]
    topo = _Topology(rows, BASE)
    assert topo.neighbour_candidates("a", "default", "10.0.0.2", PORT) == (("b", PORT, "MGMT"),)
    with pytest.raises(ValueError, match="device scope"):
        topo.ip_candidates("10.0.0.2", vrf="MGMT")
    tree, _ = topo.build()
    (link,) = networks(tree)[1][NT + "link"]
    assert len(link[ST + "tp"]) == 2
    peer = next(n for n in networks(tree)[1]["node"] if n["node-id"] == topo.l3_node("b", "MGMT"))
    assert peer[ST + "name"] == "MGMT"
    assert peer["supporting-node"] == [
        {"network-ref": BASE + "l2", "node-ref": topo.device_node(topo.chassis["b"])}
    ]


def test_arp_from_another_local_ni_does_not_prove_next_hop():
    rows = {
        "a": device("00:00:00:00:00:01", "10.0.0.1", "BLUE"),
        "b": device("00:00:00:00:00:02", "10.0.0.2"),
    }
    rows["a"]["arp"] = [arp("10.0.0.2", vrf="default")]
    topo = _Topology(rows, BASE)
    assert topo.neighbour_candidates("a", "BLUE", "10.0.0.2", PORT) == ()


def test_svi_attachment_requires_exact_vid_and_unique_port_in_any_row_order():
    facts = device("00:00:00:00:00:01", "10.0.0.1", ifname="Vlan10")
    facts["mac"] = [
        {"mac": MAC, "port": "Ethernet0/2", "vlan_or_bd": "20"},
        {"mac": MAC, "port": PORT, "vlan_or_bd": "10"},
    ]
    row = arp("10.0.0.99", "Vlan10")
    for order in permutations(facts["mac"]):
        facts["mac"] = list(order)
        assert _Topology({"a": facts}, BASE).attachment(facts, row) == PORT
    facts["mac"].append({"mac": MAC, "port": "Ethernet0/3", "vlan_or_bd": "10"})
    assert _Topology({"a": facts}, BASE).attachment(facts, row) is None


def test_svi_never_uses_a_mac_row_from_another_vid_or_unknown_logical_interface():
    facts = device("00:00:00:00:00:01", "10.0.0.1", ifname="Vlan10")
    facts["mac"] = [{"mac": MAC, "port": PORT, "vlan_or_bd": "20"}]
    topo = _Topology({"a": facts}, BASE)
    assert topo.attachment(facts, arp("10.0.0.99", "Vlan10")) is None
    assert topo.attachment(facts, arp("10.0.0.99", "Tunnel10")) is None


def test_same_mac_across_vids_keeps_facts_but_does_not_join_endpoints():
    facts = device("00:00:00:00:00:01", "10.0.0.1", ifname="Vlan10")
    facts["arp"] = [arp("10.0.0.99", "Vlan10")]
    facts["mac"] = [
        {"mac": MAC, "port": PORT, "vlan_or_bd": "10"},
        {"mac": MAC, "port": "Ethernet0/2", "vlan_or_bd": "20"},
    ]
    original = deepcopy(facts)
    topo = _Topology({"a": facts}, BASE)
    tree, _ = topo.build()
    assert MAC in topo.ambiguous_macs
    assert all(not network[NT + "link"] for network in networks(tree))
    assert facts == original


def test_different_vid_endpoints_on_same_port_do_not_form_one_l2_segment():
    facts = device("00:00:00:00:00:01", "10.0.0.1")
    facts["mac"] = [
        {"mac": MAC, "port": PORT, "vlan_or_bd": "10"},
        {"mac": "00:00:00:00:00:98", "port": PORT, "vlan_or_bd": "20"},
    ]
    tree, _ = build_topology({"a": facts}, BASE)
    links = networks(tree)[0][NT + "link"]
    assert len(links) == 2
    assert all(len(link[ST + "tp"]) == 2 for link in links)


def test_reused_mac_in_isolated_bridges_does_not_join_the_bridges():
    rows = {
        "a": device("00:00:00:00:00:01", "10.0.0.1"),
        "b": device("00:00:00:00:00:02", "10.0.0.2"),
    }
    for facts in rows.values():
        facts["mac"] = [{"mac": MAC, "port": PORT, "vlan_or_bd": "10"}]
    topo = _Topology(rows, BASE)
    tree, _ = topo.build()
    assert MAC in topo.ambiguous_macs
    assert not networks(tree)[0][NT + "link"]


def test_secondary_interface_addresses_use_one_tp_and_unique_prefix_keys():
    facts = device("00:00:00:00:00:01", "10.0.0.1")
    facts["ip"].append({**facts["ip"][0], "address": "10.0.0.2"})
    facts["ip"].append(dict(facts["ip"][0]))
    tree, _ = build_topology({"a": facts}, BASE)
    (node,) = networks(tree)[1]["node"]
    (tp,) = node[NT + "termination-point"]
    assert tp[L3T + "l3-termination-point-attributes"]["ip-address"] == ["10.0.0.1", "10.0.0.2"]
    assert node[L3T + "l3-node-attributes"]["prefix"] == [{"prefix": "10.0.0.0/24"}]


def test_overlapping_subnets_keep_component_ids_and_evidence_independent():
    rows = {
        key: device(f"00:00:00:00:00:0{index}", f"10.0.0.{index}")
        for index, key in enumerate(("a", "b", "c", "d"), 1)
    }
    rows["a"]["lldp"] = [lldp("00:00:00:00:00:02")]
    rows["c"]["lldp"] = [lldp("00:00:00:00:00:04")]
    before, old_evidence = build_topology({key: rows[key] for key in ("a", "b")}, BASE)
    after, evidence = build_topology(rows, BASE)
    (old_link,) = networks(before)[1][NT + "link"]
    links = networks(after)[1][NT + "link"]
    assert len(links) == 2
    assert old_link in links
    assert evidence[old_link["link-id"]] == old_evidence[old_link["link-id"]]
    assert not any("c NI" in reason for reason in evidence[old_link["link-id"]])


def test_same_port_different_vid_links_support_only_their_own_l3_relationship():
    facts = device("00:00:00:00:00:01", "10.0.0.1", ifname="Vlan10")
    facts["interfaces"] = [{"ifname": PORT, "type": "ethernet"}]
    facts["ip"].append({**facts["ip"][0], "address": "10.0.0.2", "ifname": "Vlan20"})
    other_mac = "00:00:00:00:00:98"
    facts["arp"] = [arp("10.0.0.10", "Vlan10"), arp("10.0.0.20", "Vlan20", mac=other_mac)]
    facts["mac"] = [
        {"mac": MAC, "port": PORT, "vlan_or_bd": "10"},
        {"mac": other_mac, "port": PORT, "vlan_or_bd": "20"},
    ]
    topo = _Topology({"a": facts}, BASE)
    tree, _ = topo.build()
    assert len(networks(tree)[1][NT + "link"]) == 2
    for link in networks(tree)[1][NT + "link"]:
        vid = 10 if any(tp["tp-ref"].endswith("Vlan10") for tp in link[ST + "tp"]) else 20
        assert len(link["supporting-link"]) == 1
        assert topo.link_vlans[link["supporting-link"][0]["link-ref"]] == vid


def test_duplicate_reported_chassis_do_not_merge_collected_devices():
    rows = {
        "a": device("00:00:00:00:00:01", "10.0.0.1"),
        "b": device("00:00:00:00:00:01", "10.0.0.2"),
    }
    topo = _Topology(rows, BASE)
    assert len(set(topo.chassis.values())) == 2
    tree, _ = topo.build()
    assert len(networks(tree)[0]["node"]) == 2


def test_explicit_comware_arp_vids_cannot_merge_the_same_mac_across_local_nis():
    facts = {
        "platform": "comware",
        "lldp_local": {"chassis_id": "00:11:22:33:44:01"},
        "ip": [
            {"ifname": "Vlan-interface10", "address": "192.0.2.1", "prefix_len": 24, "vrf": "blue"},
            {
                "ifname": "Vlan-interface20",
                "address": "198.51.100.1",
                "prefix_len": 24,
                "vrf": "red",
            },
        ],
        "arp": [
            {**arp("192.0.2.2", "GigabitEthernet1/0", "blue"), "vlan": 10},
            {**arp("198.51.100.2", "GigabitEthernet1/0", "red"), "vlan": 20},
        ],
    }
    topo = _Topology({"a": facts}, BASE)
    tree, _ = topo.build()
    assert MAC in topo.ambiguous_macs
    assert all(not network[NT + "link"] for network in networks(tree))


@pytest.mark.parametrize(
    "chassis", [("rack:01", "rack-01"), ("rack:01", "rack%3A01"), ("rack", "rack:ni:BLUE")]
)
def test_non_mac_chassis_identity_is_injective_and_does_not_impersonate_a_local_ni(chassis):
    rows = {"a": device(chassis[0], "192.0.2.1", "BLUE"), "b": device(chassis[1], "198.51.100.1")}
    topo = _Topology(rows, BASE)
    assert topo.device_node(topo.chassis["a"]) != topo.device_node(topo.chassis["b"])
    assert topo.l3_node("a", "BLUE") != topo.l3_node("b", "default")
    tree, _ = topo.build()
    assert len(networks(tree)[0]["node"]) == 2
    assert locate(rows, BASE, "192.0.2.1")["locations"][0]["device"]["address"] == "a"
    assert locate(rows, BASE, "198.51.100.1")["locations"][0]["device"]["address"] == "b"


def test_reported_target_prefix_is_not_an_unreported_target_identity():
    rows = {"a": device("unused", "192.0.2.1"), "b": device("target:a", "198.51.100.1")}
    rows["a"].pop("lldp_local")
    topo = _Topology(rows, BASE)
    assert topo.device_node(topo.chassis["a"]) == BASE + "device:target:a"
    assert topo.device_node(topo.chassis["b"]) == BASE + "device:chassis:target%3Aa"
    tree, _ = topo.build()
    assert len(networks(tree)[0]["node"]) == 2


def test_mac_device_uri_remains_compatible_with_existing_snapshots():
    topo = _Topology({"a": device("00:11:22:33:44:55", "192.0.2.1")}, BASE)
    assert topo.device_node(topo.chassis["a"]) == BASE + "device:00-11-22-33-44-55"
