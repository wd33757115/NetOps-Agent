"""Address identity, device-local NI and observed adjacency regressions."""

from copy import deepcopy

import pytest

from src.apps.topology.backend.model.locate import locate
from src.apps.topology.backend.model.path import trace
from tests.unit.test_topology_path import BASE, lab, network, route, router


def pair(*, evidence=True, left_vrf="blue", right_vrf="green"):
    devices = {
        "left": router(
            "00:11:22:33:44:01",
            [("GigabitEthernet1/0", "192.0.2.1", 30), ("LoopBack0", "198.51.100.1", 32)],
            [route("198.51.100.2/32", ("192.0.2.2", "GigabitEthernet1/0"))],
        ),
        "right": router(
            "00:11:22:33:44:02",
            [("GigabitEthernet1/0", "192.0.2.2", 30), ("LoopBack0", "198.51.100.2", 32)],
            [route("198.51.100.1/32", ("192.0.2.1", "GigabitEthernet1/0"))],
        ),
    }
    for key, vrf in (("left", left_vrf), ("right", right_vrf)):
        for row in devices[key]["ip"] + devices[key]["routes"]:
            row["vrf"] = vrf
            for hop in row.get("next_hops", []):
                hop["vrf"] = vrf
        if evidence:
            devices[key]["arp"] = [
                {
                    "vrf": vrf,
                    "ip": "192.0.2.2" if key == "left" else "192.0.2.1",
                    "mac": "00:11:22:33:44:02" if key == "left" else "00:11:22:33:44:01",
                    "ifname": "GigabitEthernet1/0",
                }
            ]
    return devices


def test_trace_starts_in_source_ni_and_uses_remote_interface_ni():
    result = trace(pair(), BASE, "198.51.100.1", "198.51.100.2")
    assert result["forward"]["status"] == result["reverse"]["status"] == "complete"
    assert [hop["vrf"] for hop in result["forward"]["paths"][0]["hops"]] == ["blue", "green"]
    assert [hop["vrf"] for hop in result["reverse"]["paths"][0]["hops"]] == ["green", "blue"]
    assert result["gaps"] == []


def test_owned_source_on_down_interface_does_not_originate_a_complete_path():
    devices = pair()
    devices["left"]["interfaces"][1]["oper_status"] = "down"
    result = trace(devices, BASE, "198.51.100.1", "198.51.100.2")
    assert result["forward"]["status"] == "partial"
    assert result["forward"]["paths"][0]["end"]["interface_role"] == "source"
    assert result["forward"]["paths"][0]["end"]["status"] == "interface_unavailable"


def test_owned_destination_unknown_state_keeps_candidate_with_destination_gap():
    devices = pair()
    devices["right"]["interfaces"][1].pop("oper_status")
    result = trace(devices, BASE, "198.51.100.1", "198.51.100.2")
    assert result["forward"]["status"] == "partial"
    assert result["forward"]["paths"][0]["end"]["status"] == "delivered"
    assert any(
        gap["status"] == "interface_state_unknown" and gap["interface_role"] == "destination"
        for gap in result["gaps"]
    )


def test_ip_ownership_and_same_prefix_without_neighbour_evidence_are_only_candidates():
    result = trace(pair(evidence=False), BASE, "198.51.100.1", "198.51.100.2")
    assert result["forward"]["status"] == result["reverse"]["status"] == "partial"
    assert result["forward"]["paths"][0]["end"]["status"] == "delivered"
    assert {gap["status"] for gap in result["gaps"]} == {"adjacency_unproven"}
    assert result["symmetric"] is None


def test_route_egress_is_not_replaced_by_first_overlapping_connected_interface():
    devices = pair()
    devices["left"]["ip"].insert(
        0,
        {
            "ifname": "GigabitEthernet0/0",
            "address": "192.0.2.1",
            "prefix_len": 30,
            "vrf": "blue",
        },
    )
    devices["left"]["interfaces"].append(
        {
            "ifname": "GigabitEthernet0/0",
            "type": "ethernet",
            "oper_status": "down",
        }
    )
    result = trace(devices, BASE, "198.51.100.1", "198.51.100.2")
    assert result["forward"]["status"] == "complete"
    assert result["forward"]["paths"][0]["hops"][0]["out_interface"] == "GigabitEthernet1/0"


def test_connected_route_without_endpoint_arp_cannot_claim_delivery():
    result = trace(network(), BASE, "172.16.4.1", "172.16.4.99")
    assert result["forward"]["status"] == "partial"
    assert result["forward"]["paths"][0]["end"] == {
        "status": "endpoint_unresolved",
        "interface": "Vlan-interface40",
        "destination": "172.16.4.99",
    }


def test_arp_in_another_local_ni_does_not_close_connected_delivery():
    devices = network()
    devices["10.0.0.4"]["arp"][0]["vrf"] = "other"
    result = trace(devices, BASE, "172.16.4.1", "172.16.4.20")
    assert result["forward"]["paths"][0]["end"]["status"] == "endpoint_unresolved"


def test_physical_arp_port_needs_explicit_matching_vid_to_close_svi_delivery():
    devices = lab()
    row = next(row for row in devices["10.10.1.1"]["arp"] if row["ip"] == "192.168.1.2")
    row["vlan"] = 100
    result = trace(devices, BASE, "4.4.4.4", "192.168.1.2")
    assert result["forward"]["paths"][0]["end"]["status"] == "endpoint_unresolved"


def test_unobserved_source_is_a_gap_even_when_route_and_destination_are_observed():
    result = trace(network(), BASE, "172.16.1.99", "172.16.4.20")
    assert result["forward"]["status"] == "partial"
    assert all(path["end"]["status"] == "delivered" for path in result["forward"]["paths"])
    assert any(gap["status"] == "source_attachment_unproven" for gap in result["gaps"])


@pytest.mark.parametrize("reverse_order", [False, True])
def test_duplicate_owned_ip_keeps_all_device_candidates_in_stable_order(reverse_order):
    devices = pair()
    devices["duplicate"] = deepcopy(devices["left"])
    devices["duplicate"]["lldp_local"]["chassis_id"] = "00:11:22:33:44:03"
    if reverse_order:
        devices = dict(reversed(list(devices.items())))
    result = locate(devices, BASE, "198.51.100.1")
    assert result["status"] == "conflict"
    assert [location["device"]["address"] for location in result["locations"]] == [
        "duplicate",
        "left",
    ]
    traced = trace(devices, BASE, "198.51.100.1", "198.51.100.2")
    assert traced["forward"]["first_hop"] == "ambiguous"
    assert traced["forward"]["status"] == "partial"
    assert any(gap["status"] == "source_identity_ambiguous" for gap in traced["gaps"])


def test_duplicate_ip_in_two_local_instances_is_not_collapsed():
    devices = pair()
    devices["left"]["ip"].append(
        {
            "ifname": "LoopBack1",
            "address": "198.51.100.1",
            "prefix_len": 32,
            "vrf": "red",
        }
    )
    result = locate(devices, BASE, "198.51.100.1")
    assert result["status"] == "conflict"
    assert {(location["interface"], location["vrf"]) for location in result["locations"]} == {
        ("LoopBack0", "blue"),
        ("LoopBack1", "red"),
    }


def test_duplicate_next_hop_address_does_not_choose_the_first_owner():
    devices = pair()
    devices["duplicate"] = deepcopy(devices["right"])
    devices["duplicate"]["lldp_local"]["chassis_id"] = "00:11:22:33:44:03"
    result = trace(devices, BASE, "198.51.100.1", "198.51.100.2")
    assert result["forward"]["status"] == "partial"
    assert {path["hops"][1]["device"]["address"] for path in result["forward"]["paths"]} == {
        "right",
        "duplicate",
    }
    assert any(gap["status"] == "adjacency_unproven" for gap in result["gaps"])


def test_lldp_remote_port_can_select_one_of_duplicate_next_hop_addresses():
    devices = pair()
    devices["duplicate"] = deepcopy(devices["right"])
    devices["duplicate"]["lldp_local"]["chassis_id"] = "00:11:22:33:44:03"
    devices["left"]["lldp"] = [
        {
            "local_if": "GigabitEthernet1/0",
            "remote_chassis_id": "00:11:22:33:44:02",
            "remote_port_id": "GigabitEthernet1/0",
            "remote_port_subtype": "Interface name",
        }
    ]
    result = trace(devices, BASE, "198.51.100.1", "198.51.100.2")
    assert result["forward"]["status"] == "complete"
    assert [path["hops"][1]["device"]["address"] for path in result["forward"]["paths"]] == [
        "right"
    ]


def test_mac_in_multiple_vids_preserves_observations_without_one_attachment():
    devices = network()
    mac = "00:00:00:00:01:10"
    devices["10.0.0.1"]["mac"] = [
        {"mac": mac, "port": "GigabitEthernet3/0", "vlan_or_bd": "10"},
        {"mac": mac, "port": "GigabitEthernet4/0", "vlan_or_bd": "20"},
    ]
    result = locate(devices, BASE, "172.16.1.10")
    assert result["status"] == "conflict"
    assert result["locations"][0]["ambiguity"] == "vlan_scope"
    assert result["locations"][0]["ports"] == []
    assert {row["vlan_or_bd"] for row in result["locations"][0]["fdb"]} == {"10", "20"}


def test_known_device_address_does_not_hide_unassociated_arp_in_another_domain():
    devices = pair()
    devices["isolated"] = router(
        "00:11:22:33:44:03",
        [("GigabitEthernet1/0", "10.0.0.1", 24)],
        [],
    )
    devices["isolated"]["arp"] = [
        {
            "vrf": "default",
            "ip": "198.51.100.1",
            "mac": "00:22:33:44:55:66",
            "ifname": "GigabitEthernet1/0",
        }
    ]
    result = locate(devices, BASE, "198.51.100.1")
    assert result["status"] == "conflict"
    assert result["locations"][0]["device"]["address"] == "left"
    assert result["locations"][1]["ambiguity"] == "address_scope"
    assert result["locations"][1]["arp"][0]["device"] == "isolated"


def test_device_mac_claim_does_not_hide_vlan_scope_conflict():
    devices = pair()
    mac = devices["left"]["arp"][0]["mac"]
    devices["left"]["mac"] = [
        {"mac": mac, "port": "GigabitEthernet2/0", "vlan_or_bd": "10"},
        {"mac": mac, "port": "GigabitEthernet3/0", "vlan_or_bd": "20"},
    ]
    result = locate(devices, BASE, mac)
    assert result["status"] == "conflict"
    assert result["locations"][0]["ambiguity"] == "vlan_scope"


def test_empty_next_hop_list_cannot_produce_vacuous_complete_path():
    devices = pair()
    devices["left"]["routes"][0]["next_hops"] = []
    result = trace(devices, BASE, "198.51.100.1", "198.51.100.2")
    assert result["forward"]["status"] == "partial"
    assert result["forward"]["paths"][0]["end"] == {
        "status": "unresolved_next_hop",
        "reason": "route_next_hops_missing",
    }
    assert any(gap.get("reason") == "route_next_hops_missing" for gap in result["gaps"])
