"""Endpoint location (D-6): lab samples plus the four attachment outcomes."""

import pytest

from src.apps.topology.backend.model.locate import locate
from tests.unit.test_topology_builder import BASE, VSR1, VSR2, lab_facts, lldp, records


def lab(*devices):
    addresses = {"vsr-1": "10.10.1.1", "vsr-2": "10.10.1.2"}
    return {addresses[d]: lab_facts(d) for d in devices}


def ports(result):
    return [
        (p["address"], p["port"], p["basis"], p["vlan"])
        for location in result["locations"]
        for p in location.get("ports", [])
    ]


def test_pc_behind_an_access_port_is_confirmed_from_the_mac_table():
    observed = {"10.10.1.1": {"evidence_id": "obs-1", "observed_at": "2026-09-28T08:32:00+00:00"}}
    result = locate(lab("vsr-1", "vsr-2"), BASE, "192.168.1.2", observed)
    assert result["status"] == "confirmed"
    assert ports(result) == [("10.10.1.1", "GigabitEthernet3/0", "mac", "200")]
    location = result["locations"][0]
    assert location["mac"] == "00:50:79:66:68:01" and location["inferred"] is False
    assert location["ports"][0]["evidence_id"] == "obs-1"
    assert location["evidence"]
    assert [
        (g["address"], g["interface"], g["interface_ip"]) for g in result["router_interfaces"]
    ] == [("10.10.1.1", "Vlan-interface200", "192.168.1.1")]


@pytest.mark.parametrize("query", ["00:50:79:66:68:01", "0050.7966.6801", "00-50-79-66-68-01"])
def test_a_mac_in_any_common_notation_finds_the_same_port(query):
    result = locate(lab("vsr-1", "vsr-2"), BASE, query)
    assert result["kind"] == "mac" and result["status"] == "confirmed"
    assert ports(result) == [("10.10.1.1", "GigabitEthernet3/0", "mac", "200")]


def test_management_pc_behind_the_hub_is_a_shared_segment():
    result = locate(lab("vsr-1", "vsr-2"), BASE, "10.10.1.254")
    assert result["status"] == "shared_segment"
    assert ports(result) == [
        ("10.10.1.1", "GigabitEthernet1/0", "arp", None),
        ("10.10.1.2", "GigabitEthernet1/0", "arp", None),
    ]
    assert result["locations"][0]["inferred"] is True
    assert {g["interface_ip"] for g in result["router_interfaces"]} == {"10.10.1.1", "10.10.1.2"}


def test_a_segment_reaching_an_uncollected_device_is_not_a_confirmed_location():
    result = locate(lab("vsr-2"), BASE, "10.10.1.254")
    assert result["status"] == "behind_uncollected"
    location = result["locations"][0]
    assert [p["collected"] for p in location["ports"]] == [True, False]
    assert location["ports"][1]["node_id"] == BASE + "device:" + VSR1.replace(":", "-")


@pytest.mark.parametrize(
    "query,chassis,interface",
    [("10.10.1.2", VSR2, "GigabitEthernet1/0"), ("2.2.2.2", VSR1, "GigabitEthernet2/0")],
)
def test_an_address_of_a_collected_device_is_the_device_itself(query, chassis, interface):
    result = locate(lab("vsr-1", "vsr-2"), BASE, query)
    assert result["status"] == "device"
    location = result["locations"][0]
    assert location["device"]["node_id"] == BASE + "device:" + chassis.replace(":", "-")
    assert location["interface"] == interface


def switched(*macs, arp=()):
    """A router and a switch; hosts sit on the switch's edge ports."""
    router = records(
        lldp_local={"chassis_id": "aa:aa:aa:aa:aa:aa", "hostname": "r"},
        lldp=[lldp("GigabitEthernet1/0", "bb:bb:bb:bb:bb:bb", "GigabitEthernet1/0/1")],
        interfaces=[{"ifname": "Vlan-interface10", "type": "vlan"}],
        ip=[
            {
                "ifname": "Vlan-interface10",
                "vrf": "default",
                "address": "10.0.0.1",
                "prefix_len": 24,
            }
        ],
        arp=[
            {"vrf": "default", "ip": ip, "mac": mac, "ifname": "Vlan-interface10"}
            for ip, mac in arp
        ],
        mac=[{"mac": mac, "port": "GigabitEthernet1/0", "vlan_or_bd": "10"} for _, mac in arp],
    )
    switch = records(
        lldp_local={"chassis_id": "bb:bb:bb:bb:bb:bb", "hostname": "s"},
        lldp=[lldp("GigabitEthernet1/0/1", "aa:aa:aa:aa:aa:aa", "GigabitEthernet1/0")],
        interfaces=[{"ifname": f"GigabitEthernet1/0/{i}", "type": "ethernet"} for i in range(1, 4)],
        mac=[
            row
            for mac, port in macs
            for row in (
                {"mac": mac, "port": port, "vlan_or_bd": "10"},
                {"mac": mac, "port": "GigabitEthernet1/0/1", "vlan_or_bd": "10"},
            )
        ],
    )
    return {"r": router, "s": switch}


def test_the_uplink_is_excluded_and_the_edge_port_is_confirmed():
    devices = switched(
        ("00:00:00:00:00:09", "GigabitEthernet1/0/2"), arp=[("10.0.0.9", "00:00:00:00:00:09")]
    )
    result = locate(devices, BASE, "10.0.0.9")
    assert result["status"] == "confirmed"
    assert ports(result) == [("s", "GigabitEthernet1/0/2", "mac", "10")]
    assert [g["interface"] for g in result["router_interfaces"]] == ["Vlan-interface10"]


def test_one_ip_with_two_macs_is_reported_as_a_conflict():
    devices = switched(
        ("00:00:00:00:00:09", "GigabitEthernet1/0/2"),
        ("00:00:00:00:00:0a", "GigabitEthernet1/0/3"),
        arp=[("10.0.0.9", "00:00:00:00:00:09"), ("10.0.0.9", "00:00:00:00:00:0a")],
    )
    result = locate(devices, BASE, "10.0.0.9")
    assert result["status"] == "conflict"
    assert [location["mac"] for location in result["locations"]] == [
        "00:00:00:00:00:09",
        "00:00:00:00:00:0a",
    ]
    assert ports(result) == [
        ("s", "GigabitEthernet1/0/2", "mac", "10"),
        ("s", "GigabitEthernet1/0/3", "mac", "10"),
    ]


def test_a_known_mac_without_a_port_is_not_found_but_keeps_its_arp_evidence():
    devices = switched(arp=[("10.0.0.9", "00:00:00:00:00:09")])
    devices["s"]["mac"] = []
    devices["r"]["mac"] = []
    result = locate(devices, BASE, "10.0.0.9")
    assert result["status"] == "not_found"
    location = result["locations"][0]
    assert location["mac"] == "00:00:00:00:00:09" and location["ports"] == []
    assert location["arp"] == [{"device": "r", "ip": "10.0.0.9", "interface": "Vlan-interface10"}]


def test_an_unseen_address_still_names_its_gateway():
    result = locate(switched(), BASE, "10.0.0.77")
    assert result["status"] == "not_found" and result["locations"] == []
    assert [g["interface_ip"] for g in result["router_interfaces"]] == ["10.0.0.1"]
    assert locate(switched(), BASE, "172.16.0.1")["router_interfaces"] == []


@pytest.mark.parametrize("query", ["", "10.0.0", "zz:zz:zz:zz:zz:zz", "switch-a"])
def test_anything_but_an_ip_or_mac_is_rejected(query):
    with pytest.raises(ValueError):
        locate(switched(), BASE, query)
