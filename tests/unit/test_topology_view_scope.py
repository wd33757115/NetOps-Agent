"""Canvas identities must preserve the standard tree's scope and uncertainty."""

from src.apps.topology.backend.model.builder import build_topology, device_node_ids
from src.apps.topology.backend.model.view import canvas

BASE = "urn:example:scope:"


def test_local_instances_display_on_their_supporting_device():
    devices = {
        "router": {
            "platform": "ios",
            "lldp_local": {"chassis_id": "00:11:22:33:44:55", "hostname": "router"},
            "ip": [
                {
                    "ifname": "Ethernet0/0",
                    "address": "192.0.2.1",
                    "prefix_len": 24,
                    "vrf": "default",
                },
                {
                    "ifname": "Ethernet0/1",
                    "address": "198.51.100.1",
                    "prefix_len": 24,
                    "vrf": "BLUE",
                },
            ],
        }
    }
    tree, evidence = build_topology(devices, BASE)
    node_id = device_node_ids(devices, BASE)["router"]
    nodes, _ = canvas(tree, evidence, {node_id: {"address": "router"}}, BASE)
    device = next(node for node in nodes if node["id"] == node_id)
    assert device["addresses"] == ["default Ethernet0/0 192.0.2.1", "BLUE Ethernet0/1 198.51.100.1"]
    assert device["identity_status"] == "collected"


def test_arp_endpoint_does_not_assert_a_physical_host_type():
    devices = {
        "router": {
            "platform": "ios",
            "ip": [{"ifname": "Ethernet0/0", "address": "100.64.1.2", "prefix_len": 30}],
            "arp": [
                {
                    "ifname": "Ethernet0/0",
                    "ip": "100.64.1.1",
                    "mac": "00:11:22:33:44:66",
                    "vrf": "default",
                }
            ],
        }
    }
    tree, evidence = build_topology(devices, BASE)
    nodes, _ = canvas(tree, evidence, {}, BASE)
    endpoints = [node for node in nodes if node["identity_status"] == "endpoint"]
    assert len(endpoints) == 1
    assert endpoints[0]["addresses"] == ["100.64.1.1"]
    assert not any(node["identity_status"] == "host" for node in nodes)
