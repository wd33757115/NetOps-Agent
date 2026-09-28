"""Run with yangson==1.6.3 to validate exported data against the frozen modules."""

import copy
import hashlib
import json
from pathlib import Path

import pytest

from src.apps.topology.backend.model.builder import build_topology

ROOT = Path(__file__).resolve().parents[2] / "src/apps/topology/resources/yang"


def test_frozen_yang_checksums():
    sources = json.loads((ROOT / "sources.json").read_text())
    assert {p.name for p in ROOT.glob("*.yang")} == set(sources)
    for filename, provenance in sources.items():
        assert hashlib.sha256((ROOT / filename).read_bytes()).hexdigest() == provenance["sha256"]


def model():
    yangson = pytest.importorskip("yangson", reason="Install tests/topology-requirements.txt")
    return yangson.DataModel((ROOT / "library.json").read_text(), [str(ROOT)])


def all_content():
    # A discovered topology is operational state: it includes config-false SIMAP navigation.
    return pytest.importorskip("yangson.enumerations").ContentType.all


def lab_export():
    # Router with an LLDP neighbour on a hub segment, a host there and a host on an edge port.
    router = {
        "platform": "comware",
        "lldp_local": {"chassis_id": "aa:aa:aa:aa:aa:aa", "hostname": "r1"},
        "lldp": [
            {
                "local_if": "GigabitEthernet1/0",
                "remote_chassis_id": "bb:bb:bb:bb:bb:bb",
                "remote_port_id": "GigabitEthernet1/0",
                "remote_port_subtype": "Interface name",
                "remote_sysname": "r2",
                "remote_mgmt_addresses": ["10.0.0.2"],
            }
        ],
        "interfaces": [
            {"ifname": "GigabitEthernet1/0", "type": "ethernet"},
            {"ifname": "GigabitEthernet3/0", "type": "ethernet"},
        ],
        "ip": [
            {
                "ifname": "GigabitEthernet1/0",
                "vrf": "default",
                "address": "10.0.0.1",
                "prefix_len": 24,
            },
            {
                "ifname": "Vlan-interface100",
                "vrf": "default",
                "address": "1.1.1.1",
                "prefix_len": 24,
            },
        ],
        "arp": [
            {
                "vrf": "default",
                "ip": "10.0.0.2",
                "mac": "0c:00:00:00:00:02",
                "ifname": "GigabitEthernet1/0",
            },
            {
                "vrf": "default",
                "ip": "10.0.0.9",
                "mac": "00:00:00:00:00:09",
                "ifname": "GigabitEthernet1/0",
            },
            {
                "vrf": "default",
                "ip": "1.1.1.2",
                "mac": "00:00:00:00:00:12",
                "ifname": "GigabitEthernet3/0",
                "vlan": 100,
            },
        ],
        "mac": [{"mac": "00:00:00:00:00:12", "port": "GigabitEthernet3/0", "vlan_or_bd": "100"}],
    }
    tree, _ = build_topology({"10.0.0.1": router}, "urn:example:lab:")
    return tree


def test_simap_l2_l3_export_validates():
    data = model()
    tree = lab_export()
    data.from_raw(tree).validate(ctype=all_content())
    kinds = {
        link["ietf-simap-topology:link-type"]
        for network in tree["ietf-network:networks"]["network"]
        for link in network["ietf-network-topology:link"]
    }
    assert kinds == {"ietf-simap-topology:link-type-p2p", "ietf-simap-topology:link-type-mp2mp"}
    broken = copy.deepcopy(tree)
    broken["ietf-network:networks"]["network"][0]["unknown-property"] = "invalid"
    with pytest.raises(Exception):
        data.from_raw(broken).validate(ctype=all_content())


def test_pre_simap_snapshot_export_still_validates():
    pytest.importorskip("fastapi", reason="needs the app environment")
    from src.apps.topology.backend.normalize import rfc8345

    legacy = {
        "network_id": "lab-network",
        "nodes": [
            {"id": "a", "tps": [{"id": "a1"}]},
            {"id": "b", "tps": [{"id": "b1"}]},
        ],
        "links": [
            {
                "id": "a-b",
                "source": "a",
                "source_tp": "a1",
                "target": "b",
                "target_tp": "b1",
                "freshness": "observed",
            }
        ],
    }
    model().from_raw(rfc8345(legacy)).validate()
