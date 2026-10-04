"""SIMAP L2/L3 topology builder: lab samples plus the attachment rules behind them."""

from pathlib import Path

import pytest

from src.apps.topology.backend.collectors.parse import parse
from src.apps.topology.backend.model.builder import build_topology

ROOT = Path(__file__).resolve().parents[1] / "fixtures/topology/h3c/vsr1000-7.1.064"
FILES = {
    "lldp_local": "lldp_local",
    "lldp": "lldp_verbose",
    "interfaces": "interfaces",
    "ip": "ip_interfaces",
    "arp": "arp",
    "mac": "mac",
}
BASE = "urn:example:lab:"
ST = "ietf-simap-topology:"
VSR1, VSR2 = "74:25:8a:e3:ee:8e", "74:25:8a:e4:12:52"


def lab_facts(device):
    facts = {"platform": "comware"}
    for capability, name in FILES.items():
        raw = (ROOT / device / f"{name}.txt").read_text(encoding="utf-8")
        result = parse({"os_family": "comware"}, capability, raw)
        assert result.status == "complete", (device, capability, result.warnings)
        facts[capability] = result.summary if capability == "lldp_local" else result.records
    return facts


def networks(tree):
    return {n["network-id"].removeprefix(BASE): n for n in tree["ietf-network:networks"]["network"]}


def label(ref):
    node, tp = ref["node-ref"].removeprefix(BASE), ref["tp-ref"].removeprefix(BASE)
    if node.startswith("host:"):
        return tp
    return node.removeprefix("device:") + " " + tp.removeprefix("if:").replace("%2F", "/")


def links(network):
    return {
        frozenset(label(t) for t in link[ST + "tp"]): link[ST + "link-type"].rsplit("-", 1)[1]
        for link in network["ietf-network-topology:link"]
    }


def dev(chassis, port):
    return chassis.replace(":", "-") + " " + port


def test_lab_topology_matches_design():
    tree, evidence = build_topology(
        {"10.10.1.1": lab_facts("vsr-1"), "10.10.1.2": lab_facts("vsr-2")}, BASE
    )
    nets = networks(tree)
    assert len(nets["l2"]["node"]) == 5  # each VSR once, three hosts
    assert links(nets["l2"]) == {
        frozenset({dev(VSR1, "GigabitEthernet2/0"), dev(VSR2, "GigabitEthernet2/0")}): "p2p",
        frozenset(
            {
                dev(VSR1, "GigabitEthernet1/0"),
                dev(VSR2, "GigabitEthernet1/0"),
                "nic:00-15-5d-01-03-14",
            }
        ): "mp2mp",
        frozenset({dev(VSR2, "GigabitEthernet3/0"), "nic:00-50-79-66-68-04"}): "p2p",
        frozenset({dev(VSR1, "GigabitEthernet3/0"), "nic:00-50-79-66-68-05"}): "p2p",
    }
    assert links(nets["l3"]) == {
        frozenset({dev(VSR1, "GigabitEthernet2/0"), dev(VSR2, "GigabitEthernet2/0")}): "p2p",
        frozenset(
            {dev(VSR1, "GigabitEthernet1/0"), dev(VSR2, "GigabitEthernet1/0"), "ip:10.10.1.254"}
        ): "mp2mp",
        frozenset({dev(VSR2, "Vlan-interface100"), "ip:1.1.1.2"}): "p2p",
        frozenset({dev(VSR1, "Vlan-interface200"), "ip:192.168.1.2"}): "p2p",
    }
    vsr2 = next(n for n in nets["l3"]["node"] if n["node-id"].endswith(VSR2.replace(":", "-")))
    vlan = next(t for t in vsr2["ietf-network-topology:termination-point"] if "Vlan" in t["tp-id"])
    assert [label(s) for s in vlan["supporting-termination-point"]] == [
        dev(VSR2, "GigabitEthernet3/0")
    ]
    assert all(link.get("supporting-link") for link in nets["l3"]["ietf-network-topology:link"])
    assert all(evidence.values())


def test_uncollected_neighbour_is_the_same_node_it_will_become():
    tree, _ = build_topology({"10.10.1.2": lab_facts("vsr-2")}, BASE)
    nets = networks(tree)
    ids = {n["node-id"].removeprefix(BASE) for n in nets["l2"]["node"]}
    assert "device:" + VSR1.replace(":", "-") in ids  # advertised chassis, not an address node
    shared = frozenset(
        {dev(VSR1, "GigabitEthernet1/0"), dev(VSR2, "GigabitEthernet1/0"), "nic:00-15-5d-01-03-14"}
    )
    assert links(nets["l2"])[shared] == "mp2mp"
    # 10.10.1.1 is VSR-1's advertised management address: never a separate host.
    assert not any(i.startswith("host:0c-d2") for i in ids)


def records(**caps):
    return {"platform": "comware", **caps}


def lldp(local, peer, port, address=None):
    return {
        "local_if": local,
        "remote_chassis_id": peer,
        "remote_port_id": port,
        "remote_port_subtype": "Interface name",
        "remote_sysname": "peer",
        "remote_mgmt_addresses": [address] if address else [],
    }


def test_host_behind_managed_switch_attaches_at_its_edge_port():
    router = records(
        lldp_local={"chassis_id": "aa:aa:aa:aa:aa:aa", "hostname": "r"},
        lldp=[lldp("GigabitEthernet1/0", "bb:bb:bb:bb:bb:bb", "GigabitEthernet1/0/1")],
        interfaces=[{"ifname": "GigabitEthernet1/0", "type": "ethernet"}],
        ip=[
            {
                "ifname": "GigabitEthernet1/0",
                "vrf": "default",
                "address": "10.0.0.1",
                "prefix_len": 24,
            }
        ],
        arp=[
            {
                "vrf": "default",
                "ip": "10.0.0.9",
                "mac": "00:00:00:00:00:09",
                "ifname": "GigabitEthernet1/0",
            }
        ],
    )
    switch = records(
        lldp_local={"chassis_id": "bb:bb:bb:bb:bb:bb", "hostname": "s"},
        lldp=[lldp("GigabitEthernet1/0/1", "aa:aa:aa:aa:aa:aa", "GigabitEthernet1/0")],
        interfaces=[
            {"ifname": "GigabitEthernet1/0/1", "type": "ethernet"},
            {"ifname": "GigabitEthernet1/0/2", "type": "ethernet"},
        ],
        mac=[
            {"mac": "00:00:00:00:00:09", "port": "GigabitEthernet1/0/2", "vlan_or_bd": "1"},
            {"mac": "00:00:00:00:00:09", "port": "GigabitEthernet1/0/1", "vlan_or_bd": "1"},
        ],
    )
    tree, _ = build_topology({"r": router, "s": switch}, BASE)
    assert links(networks(tree)["l2"]) == {
        frozenset(
            {"aa-aa-aa-aa-aa-aa GigabitEthernet1/0", "bb-bb-bb-bb-bb-bb GigabitEthernet1/0/1"}
        ): "p2p",
        frozenset({"bb-bb-bb-bb-bb-bb GigabitEthernet1/0/2", "nic:00-00-00-00-00-09"}): "p2p",
    }


def test_several_hosts_on_one_edge_port_form_a_multipoint_segment():
    switch = records(
        lldp_local={"chassis_id": "bb:bb:bb:bb:bb:bb", "hostname": "s"},
        interfaces=[{"ifname": "GigabitEthernet1/0/2", "type": "ethernet"}],
        mac=[
            {"mac": "00:00:00:00:00:01", "port": "GigabitEthernet1/0/2", "vlan_or_bd": "1"},
            {"mac": "00:00:00:00:00:02", "port": "GigabitEthernet1/0/2", "vlan_or_bd": "1"},
        ],
    )
    tree, _ = build_topology({"s": switch}, BASE)
    assert links(networks(tree)["l2"]) == {
        frozenset(
            {
                "bb-bb-bb-bb-bb-bb GigabitEthernet1/0/2",
                "nic:00-00-00-00-00-01",
                "nic:00-00-00-00-00-02",
            }
        ): "mp2mp"
    }


@pytest.mark.parametrize("missing", ["lldp", "arp", "mac", "ip"])
def test_missing_capability_degrades_without_failing(missing):
    facts = lab_facts("vsr-2")
    facts.pop(missing)
    tree, _ = build_topology({"10.10.1.2": facts}, BASE)
    assert networks(tree)["l2"]["node"]


def lag_switch(lldp_on_members):
    """A switch whose Port-channel1 (Ethernet1/0-1) learned a host MAC."""
    lldp = [
        {
            "local_if": port,
            "remote_chassis_id": "00:00:00:00:00:09",
            "remote_port_id": port,
            "remote_port_subtype": "Interface name",
            "remote_sysname": "peer",
            "remote_mgmt_addresses": [],
        }
        for port in ("Ethernet1/0", "Ethernet1/1")
        if lldp_on_members
    ]
    return {
        "platform": "ios",
        "lldp_local": {"chassis_id": "00:00:00:00:00:01", "hostname": "sw"},
        "lldp": lldp,
        "lag": [
            {
                "ifname": "Port-channel1",
                "members": ["Ethernet1/0", "Ethernet1/1"],
                "bundled": ["Ethernet1/0", "Ethernet1/1"],
            }
        ],
        "mac": [{"mac": "00:00:00:00:00:77", "port": "Port-channel1", "vlan_or_bd": "10"}],
        "arp": [{"ip": "10.0.0.77", "mac": "00:00:00:00:00:77", "ifname": "Vlan10"}],
        "ip": [{"ifname": "Vlan10", "address": "10.0.0.1", "prefix_len": 24, "vrf": "default"}],
    }


def test_lag_members_decide_whether_an_aggregate_faces_a_peer():
    # RFC 8944 member-link-tp: no LLDP on the members (a server bundle), so the host
    # behind Port-channel1 attaches there; with LLDP on them it is only behind the peer.
    edge = links(networks(build_topology({"sw": lag_switch(False)}, BASE)[0])["l2"])
    assert frozenset({"00-00-00-00-00-01 Port-channel1", "nic:00-00-00-00-00-77"}) in edge
    uplink = links(networks(build_topology({"sw": lag_switch(True)}, BASE)[0])["l2"])
    assert not [ends for ends in uplink if "nic:00-00-00-00-00-77" in ends]
