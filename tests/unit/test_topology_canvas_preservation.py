"""Every observed campus port pair survives the model-to-canvas projection."""

from collections import Counter

from test_topology_campus_wan import BASE, EXPECTED, devices  # noqa: F401

from src.apps.topology.backend.model.builder import (
    NT,
    ST,
    build_topology_details,
    device_node_ids,
)
from src.apps.topology.backend.model.view import canvas


def test_all_sixteen_campus_links_survive_canvas_with_distinct_ids(request):
    facts = request.getfixturevalue("devices")
    tree, evidence, _ = build_topology_details(facts, BASE)
    collected = {
        node_id: {"address": address} for address, node_id in device_node_ids(facts, BASE).items()
    }
    nodes, links = canvas(tree, evidence, collected, BASE)
    by_id = {node["id"]: node for node in nodes}

    assert len(by_id) == len(nodes)
    assert len({link["id"] for link in links}) == len(links)
    assert all(link[end] in by_id for link in links for end in ("source", "target"))

    def endpoint(link, end):
        node = by_id[link[end]]
        tp = next(tp for tp in node["tps"] if tp["id"] == link[end + "_tp"])
        return node["name"].removesuffix(".lab.local"), tp["name"]

    expected = {
        tuple(sorted((name.removesuffix(".lab.local"), port) for name, port in wire))
        for wire in EXPECTED["cables"]
    }
    assert len(expected) == 16
    observed = Counter()
    canvas_ids = set()
    for link in links:
        if link["source"] not in collected or link["target"] not in collected:
            continue
        if not any(" lldp " in item for item in link["evidence"]):
            continue
        assert link["link_type"] == "p2p"
        observed[tuple(sorted((endpoint(link, "source"), endpoint(link, "target"))))] += 1
        canvas_ids.add(link["id"])

    # Check the complete port pairs, not just a node pair or the original LLDP rows.
    # Parallel links and diagonal uplinks must each remain their own canvas edge.
    assert observed == Counter({pair: 1 for pair in expected})
    l2 = tree["ietf-network:networks"]["network"][0]
    model_ids = {
        link["link-id"]
        for link in l2[NT + "link"]
        if len(link[ST + "tp"]) == 2
        and all(tp["node-ref"] in collected for tp in link[ST + "tp"])
        and any(" lldp " in item for item in evidence[link["link-id"]])
    }
    assert canvas_ids == model_ids

    assert observed[tuple(sorted((("ACC1", "Ethernet0/1"), ("DIST1", "Ethernet2/0"))))] == 1
    assert observed[tuple(sorted((("ACC1", "Ethernet0/2"), ("DIST2", "Ethernet2/1"))))] == 1
    assert (
        sum(
            count
            for pair, count in observed.items()
            if {name for name, _ in pair} == {"DIST1", "DIST2"}
        )
        == 2
    )
