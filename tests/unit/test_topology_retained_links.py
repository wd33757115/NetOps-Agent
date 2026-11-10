"""Observed L2 baseline retention: current evidence and state remain distinct."""

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.apps.topology.backend.collectors.identify import Identity
from src.apps.topology.backend.collectors.parse import parse
from src.apps.topology.backend.model.builder import L2T, NT, ST, build_topology, device_node_ids
from src.apps.topology.backend.model.retained_links import retain_l2_links
from src.apps.topology.backend.service import topology_observation

ROOT = Path(__file__).resolve().parents[2]
CAPTURES = ROOT / "tests/fixtures/topology/campus-wan"
YANG = ROOT / "src/apps/topology/resources/yang"
EXPECTED = json.loads((CAPTURES / "expected.json").read_text())
BASE = "urn:test:retained:"
IDENTITIES = {
    "comware": Identity("H3C", "comware", "h3c-comware7", "display", "high", "identified"),
    "ios": Identity("Cisco", "ios", "cisco-ios", "show", "high", "identified"),
}
FILES = {
    "comware": {
        "lldp_local": "lldp_local",
        "lldp": "lldp_verbose",
        "interfaces": "interfaces",
        "ip": "ip_interfaces",
        "arp": "arp",
        "mac": "mac",
        "lag": "lag",
    },
    "ios": {
        "lldp": "lldp_verbose",
        "interfaces": "interfaces_description",
        "ip": "ip_interface",
        "arp": "arp",
        "mac": "mac",
        "lag": "lag",
    },
}


def network(tree):
    return next(
        n
        for n in tree["ietf-network:networks"]["network"]
        if L2T + "l2-topology" in n["network-types"]
    )


def model(pairs=(), base=BASE):
    """Small standard graph; node IDs stand for three known managed identities."""
    nodes = [
        {
            "node-id": base + name,
            NT + "termination-point": [
                {
                    "tp-id": base + "if:Ethernet0/1",
                    L2T + "l2-termination-point-attributes": {"interface-name": "Ethernet0/1"},
                }
            ],
        }
        for name in "ABC"
    ]
    links = [
        {
            "link-id": base + a + b,
            ST + "link-direction": ST + "link-direction-bi",
            ST + "link-type": ST + "link-type-p2p",
            ST + "tp": [
                {
                    "network-ref": base + "l2",
                    "node-ref": base + name,
                    "tp-ref": base + "if:Ethernet0/1",
                    "tp-direction": ST + "tp-direction-symmetric",
                }
                for name in (a, b)
            ],
        }
        for a, b in pairs
    ]
    return {
        "ietf-network:networks": {
            "network": [
                {
                    "network-id": base + "l2",
                    "network-types": {L2T + "l2-topology": {}},
                    ST + "network-category": ST + "network-category-live",
                    "node": nodes,
                    NT + "link": links,
                }
            ]
        }
    }


def states(status="complete", oper=None, admin=None):
    result = {
        BASE + n: {
            "address": "192.0.2." + str(i),
            "collection_status": status,
            "interfaces": {},
            "lldp_ports": ["Ethernet0/1"] if status == "complete" else [],
        }
        for i, n in enumerate("ABC", 1)
    }
    if oper is not None or admin is not None:
        interface = result[BASE + "A"]["interfaces"]["Ethernet0/1"] = {}
        if oper is not None:
            interface["oper_status"] = oper
        if admin is not None:
            interface["admin_status"] = admin
    return result


@pytest.fixture(scope="module")
def campus():
    devices = {}
    for key, spec in EXPECTED["devices"].items():
        identity = IDENTITIES[spec["platform"]]
        parsed = {
            cap: parse(identity, cap, (CAPTURES / key / (file + ".txt")).read_text())
            for cap, file in FILES[spec["platform"]].items()
            if (CAPTURES / key / (file + ".txt")).exists()
        }
        outcome = topology_observation(
            SimpleNamespace(
                parsed=parsed,
                identity=identity,
                profile_id=identity.profile_id,
                mode="topology",
                driver="paramiko",
            )
        )
        assert outcome["complete"]
        devices[spec["management"]] = {**outcome["facts"], "platform": spec["platform"]}
    return devices


def campus_states(devices):
    return {
        node_id: {
            "address": address,
            "collection_status": "complete",
            "evidence_id": "current:" + address,
            "observed_at": "2026-10-03T02:00:00Z",
            "lldp_ports": [row["local_if"] for row in devices[address]["lldp"]],
            "interfaces": {
                row["ifname"]: {"oper_status": row["oper_status"]}
                for row in devices[address]["interfaces"]
            },
        }
        for address, node_id in device_node_ids(devices, BASE).items()
    }


def cable_pairs(tree, identities):
    nodes = {n["node-id"]: n for n in network(tree)["node"]}
    result = set()
    for link in network(tree)[NT + "link"]:
        points = link[ST + "tp"]
        if len(points) != 2 or any(p["node-ref"] not in identities for p in points):
            continue
        result.add(
            frozenset(
                (
                    p["node-ref"],
                    next(
                        tp[L2T + "l2-termination-point-attributes"]["interface-name"]
                        for tp in nodes[p["node-ref"]][NT + "termination-point"]
                        if tp["tp-id"] == p["tp-ref"]
                    ),
                )
                for p in points
            )
        )
    return result


def validate(tree):
    yangson = pytest.importorskip("yangson", reason="Install tests/topology-requirements.txt")
    content = pytest.importorskip("yangson.enumerations").ContentType.all
    validator = yangson.DataModel((YANG / "library.json").read_text(), [str(YANG)])
    validator.from_raw(tree).validate(ctype=content)


def test_complete_collection_missing_interface_does_not_retain():
    current, prior = model(), model([("A", "B")])
    result, metadata = retain_l2_links(current, prior, states())
    assert result == current and metadata == {}


@pytest.mark.parametrize("status", ["failed", "unknown"])
def test_collection_gap_retains_structure_without_claiming_down(status):
    result, metadata = retain_l2_links(model(), model([("A", "B")]), states(status))
    assert len(network(result)[NT + "link"]) == 1
    record = metadata[BASE + "AB"]
    assert record["state"] == "unknown" and record["available"] is None
    assert "last_seen" not in record and "prior_snapshot_id" not in record
    assert all(s["oper_status"] == "unknown" for s in record["state_sources"])
    validate(result)


@pytest.mark.parametrize("oper", ["down", "unknown"])
def test_only_explicit_interface_state_permits_retention(oper):
    result, metadata = retain_l2_links(model(), model([("A", "B")]), states(oper=oper))
    assert len(network(result)[NT + "link"]) == 1
    assert metadata[BASE + "AB"]["state"] == oper
    assert metadata[BASE + "AB"]["available"] is (False if oper == "down" else None)


def test_admin_down_does_not_rewrite_unknown_operational_state():
    _, metadata = retain_l2_links(
        model(), model([("A", "B")]), states(oper="unknown", admin="down")
    )
    record = metadata[BASE + "AB"]
    assert record["state"] == "unknown" and record["available"] is False
    source = next(s for s in record["state_sources"] if s["node_id"] == BASE + "A")
    assert source["oper_status"] == "unknown" and source["admin_status"] == "down"


@pytest.mark.parametrize("oper", ["testing", "dormant", "not-present", "lower-layer-down"])
def test_native_nonforwarding_states_retain_structure_without_rewriting_status(oper):
    result, metadata = retain_l2_links(model(), model([("A", "B")]), states(oper=oper))
    record = metadata[BASE + "AB"]
    assert len(network(result)[NT + "link"]) == 1
    assert record["available"] is False and record["state"] == "unknown"
    source = next(s for s in record["state_sources"] if s["node_id"] == BASE + "A")
    assert source["oper_status"] == oper and source["retention_reasons"] == ["oper_" + oper]


def test_fresh_same_port_peer_replaces_historical_peer():
    current, prior, observed = model([("A", "C")]), model([("A", "B")]), states(oper="down")
    observed[BASE + "B"]["collection_status"] = "failed"
    result, metadata = retain_l2_links(current, prior, observed)
    assert [link["link-id"] for link in network(result)[NT + "link"]] == [BASE + "AC"]
    assert metadata == {}


def test_complete_lldp_new_advertised_peer_replaces_old_managed_peer():
    current, prior, observed = model([("A", "C")]), model([("A", "B")]), states(oper="down")
    observed.pop(BASE + "C")  # LLDP identifies the new peer before it is managed.
    observed[BASE + "B"]["collection_status"] = "failed"
    result, metadata = retain_l2_links(current, prior, observed)
    assert [link["link-id"] for link in network(result)[NT + "link"]] == [BASE + "AC"]
    assert metadata == {}


def test_current_lldp_shared_segment_supersedes_old_point_to_point_relationship():
    current, prior, observed = model([("A", "C")]), model([("A", "B")]), states(oper="down")
    link = network(current)[NT + "link"][0]
    link[ST + "link-type"] = ST + "link-type-mp2mp"
    link[ST + "tp"].append(
        {
            "network-ref": BASE + "l2",
            "node-ref": BASE + "B",
            "tp-ref": BASE + "if:Ethernet0/1",
            "tp-direction": ST + "tp-direction-symmetric",
        }
    )
    result, metadata = retain_l2_links(current, prior, observed)
    assert len(network(result)[NT + "link"]) == 1 and metadata == {}
    assert network(result)[NT + "link"][0][ST + "link-type"] == ST + "link-type-mp2mp"


@pytest.mark.parametrize("ports", [None, []])
def test_successful_interface_collection_or_arp_link_is_not_complete_lldp_adjacency(ports):
    current, prior, observed = model([("A", "C")]), model([("A", "B")]), states(oper="down")
    for device in observed.values():
        if ports is None:
            device.pop("lldp_ports")
        else:
            device["lldp_ports"] = ports
    result, metadata = retain_l2_links(current, prior, observed)
    assert {link["link-id"] for link in network(result)[NT + "link"]} == {BASE + "AB", BASE + "AC"}
    assert BASE + "AB" in metadata


def test_current_adjacency_and_evidence_are_not_marked_historical():
    current = model([("A", "B")])
    result, metadata = retain_l2_links(current, deepcopy(current), states("failed"))
    assert result == current and metadata == {}


def test_other_network_baseline_cannot_be_borrowed():
    current = model()
    result, metadata = retain_l2_links(current, model([("A", "B")], "urn:other:"), states("failed"))
    assert result == current and metadata == {}


@pytest.mark.parametrize("unknown_end", ["A", "B"])
def test_both_endpoints_must_have_stable_managed_identity(unknown_end):
    observed = states("failed")
    observed.pop(BASE + unknown_end)
    result, metadata = retain_l2_links(model(), model([("A", "B")]), observed)
    assert network(result)[NT + "link"] == [] and metadata == {}


def test_terminal_mac_and_shared_media_links_are_not_retained():
    prior = model([("A", "B")])
    node = next(n for n in network(prior)["node"] if n["node-id"] == BASE + "B")
    node[NT + "termination-point"][0][L2T + "l2-termination-point-attributes"] = {
        "mac-address": "00:11:22:33:44:55"
    }
    result, metadata = retain_l2_links(model(), prior, states("failed"))
    assert network(result)[NT + "link"] == [] and metadata == {}
    prior = model([("A", "B")])
    network(prior)[NT + "link"][0][ST + "link-type"] = ST + "link-type-mp2mp"
    result, metadata = retain_l2_links(model(), prior, states("failed"))
    assert network(result)[NT + "link"] == [] and metadata == {}


def test_sidecar_preserves_explicit_history_and_distinct_observation_sources():
    prior, current, observed = model([("A", "B")]), model(), states(oper="down")
    observed[BASE + "A"].update(evidence_id="device-evidence", observed_at="collection-time")
    observed[BASE + "A"]["interfaces"]["Ethernet0/1"].update(
        evidence_id="interface-evidence", observed_at="interface-time"
    )
    history = {
        "snapshot_id": 7,
        "last_seen": "snapshot-time",
        "last_seen_source": "snapshot",
        "evidence": {BASE + "AB": ["historical-lldp"]},
    }
    inputs = deepcopy((current, prior, observed, history))
    result, metadata = retain_l2_links(current, prior, observed, history)
    assert (current, prior, observed, history) == inputs
    record = metadata[BASE + "AB"]
    assert record["prior_snapshot_id"] == 7 and record["prior_evidence"] == ["historical-lldp"]
    assert record["last_seen"] == "snapshot-time" and record["last_seen_source"] == "snapshot"
    source = next(s for s in record["state_sources"] if s["node_id"] == BASE + "A")
    assert source["observed_at"] == "interface-time"
    assert source["observed_at_source"] == source["evidence_id_source"] == "interface_observation"
    assert source["evidence_id"] == "interface-evidence"
    assert "state" not in json.dumps(result) and "historical" not in json.dumps(result)


def test_missing_node_and_tp_get_only_minimal_identity_structure():
    prior, current = model([("A", "B")]), model()
    old = network(prior)["node"][1]
    old[L2T + "l2-node-attributes"] = {"name": "old-name", "management-address": ["192.0.2.2"]}
    attrs = old[NT + "termination-point"][0][L2T + "l2-termination-point-attributes"]
    attrs.update({"outer-tag": "10,20", "encapsulation-type": L2T + "vlan"})
    old["supporting-node"] = [{"network-ref": "urn:old:missing", "node-ref": "urn:old:node"}]
    old[NT + "termination-point"][0]["supporting-termination-point"] = [
        {"network-ref": "urn:old:missing", "node-ref": "urn:old:node", "tp-ref": "urn:old:tp"}
    ]
    network(current)["node"] = [n for n in network(current)["node"] if n["node-id"] != BASE + "B"]
    network(current)["node"][0][NT + "termination-point"] = []
    result, metadata = retain_l2_links(current, prior, states("failed"))
    restored = next(n for n in network(result)["node"] if n["node-id"] == BASE + "B")
    assert L2T + "l2-node-attributes" not in restored and "supporting-node" not in restored
    point = restored[NT + "termination-point"][0]
    assert point[L2T + "l2-termination-point-attributes"] == {"interface-name": "Ethernet0/1"}
    assert "supporting-termination-point" not in point
    assert metadata[BASE + "AB"]["scope"] == "l2_only"
    validate(result)


def test_real_campus_healthy_offline_and_recovered_keeps_sixteen_device_cables(campus):
    prior, evidence = build_topology(campus, BASE)
    observed = campus_states(campus)
    identities = device_node_ids(campus, BASE)
    by_name = {
        spec["name"]: identities[spec["management"]] for spec in EXPECTED["devices"].values()
    }
    wanted = {
        frozenset((by_name[name], interface) for name, interface in cable)
        for cable in EXPECTED["cables"]
    }
    assert len(wanted) == 16 and cable_pairs(prior, observed) == wanted
    healthy, metadata = retain_l2_links(prior, prior, observed)
    assert metadata == {} and healthy == prior

    # Simulated new capture: BR1 is unavailable and peers no longer report its LLDP.
    # ARP/MAC are omitted in this simulation; the baseline does not supply new facts.
    offline = deepcopy(campus)
    address = EXPECTED["devices"]["br1"]["management"]
    chassis = offline[address]["lldp_local"]["chassis_id"]
    offline.pop(address)
    for facts in offline.values():
        facts["lldp"] = [row for row in facts["lldp"] if row["remote_chassis_id"] != chassis]
        facts.pop("arp", None)
        facts.pop("mac", None)
    current, _ = build_topology(offline, BASE)
    l3_before = deepcopy(current["ietf-network:networks"]["network"][1:])
    observed[identities[address]] = {
        "address": address,
        "collection_status": "failed",
        "evidence_id": "failed-capture",
        "observed_at": "failure-time",
    }
    restored, metadata = retain_l2_links(
        current,
        prior,
        observed,
        {
            "snapshot_id": "healthy",
            "last_seen": "2026-10-03T01:00:00Z",
            "last_seen_source": "snapshot",
            "evidence": evidence,
        },
    )
    assert len(metadata) == 3 and cable_pairs(restored, observed) == wanted
    assert all(row["state"] == "unknown" for row in metadata.values())
    assert restored["ietf-network:networks"]["network"][1:] == l3_before
    assert not any(n["node-id"].startswith(BASE + "host:") for n in network(restored)["node"])
    validate(restored)
    recovered, metadata = retain_l2_links(prior, restored, campus_states(campus))
    assert recovered == prior and metadata == {} and cable_pairs(recovered, observed) == wanted
    validate(recovered)


def test_real_campus_explicit_down_retains_old_relationship_without_old_vlan(campus):
    prior, _ = build_topology(campus, BASE)
    observed = campus_states(campus)
    ids = device_node_ids(campus, BASE)
    first = ids[EXPECTED["devices"]["dist1"]["management"]]
    second = ids[EXPECTED["devices"]["acc1"]["management"]]
    target = next(
        link
        for link in network(prior)[NT + "link"]
        if {(p["node-ref"], p["tp-ref"]) for p in link[ST + "tp"]}
        == {(first, BASE + "if:Ethernet2%2F0"), (second, BASE + "if:Ethernet0%2F1")}
    )
    current = deepcopy(prior)
    network(current)[NT + "link"].remove(target)
    observed[first]["interfaces"]["Ethernet2/0"] = {
        "oper_status": "down",
        "admin_status": "up",
        "evidence_id": "down-capture",
        "observed_at": "down-time",
    }
    result, metadata = retain_l2_links(current, prior, observed)
    assert len(cable_pairs(result, observed)) == 16
    assert metadata[target["link-id"]]["state"] == "down"
    assert metadata[target["link-id"]]["available"] is False
    validate(result)
