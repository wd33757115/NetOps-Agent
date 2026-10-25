"""Keep explicitly unavailable, previously observed managed-device L2 adjacency.

This is an observed baseline, not intended configuration. Historical provenance and
current interface state stay outside the YANG tree. No old L3, ARP, MAC, VLAN or
management facts are promoted into current observations.
"""

from __future__ import annotations

from copy import deepcopy

from .builder import L2T, NT, ST

UNAVAILABLE_OPER_STATUSES = frozenset(
    {"down", "testing", "dormant", "not-present", "lower-layer-down"}
)


def _networks(tree):
    return tree.get("ietf-network:networks", {}).get("network", [])


def _ends(link, network_id):
    """Only the frozen SIMAP two-device, same-network p2p form is eligible."""
    if link.get(ST + "link-type") != ST + "link-type-p2p":
        return None
    points = link.get(ST + "tp", [])
    if len(points) != 2 or any(p.get("network-ref") != network_id for p in points):
        return None
    ends = [(p.get("node-ref"), p.get("tp-ref")) for p in points]
    if any(not node or not tp for node, tp in ends) or ends[0][0] == ends[1][0]:
        return None
    return ends


def _interface(node, tp_id):
    tp = next((p for p in node.get(NT + "termination-point", []) if p["tp-id"] == tp_id), None)
    if tp is None:
        return None
    name = tp.get(L2T + "l2-termination-point-attributes", {}).get("interface-name")
    return name if isinstance(name, str) and name else None


def _source(node_id, tp_id, name, device):
    interface = device.get("interfaces", {}).get(name, {})
    source = {
        "node_id": node_id,
        "tp_id": tp_id,
        "interface_name": name,
        "collection_status": device.get("collection_status", "unknown"),
        "oper_status": interface.get("oper_status", "unknown"),
    }
    if "admin_status" in interface:
        source["admin_status"] = interface["admin_status"]
    if "address" in device:
        source["address"] = device["address"]
    for key in ("evidence_id", "observed_at"):
        if key in interface:
            source[key] = deepcopy(interface[key])
            source[key + "_source"] = "interface_observation"
        elif key in device:
            source[key] = deepcopy(device[key])
            source[key + "_source"] = "device_collection"
    reasons = []
    if device.get("collection_status") in {"failed", "unknown"}:
        reasons.append("collection_" + device["collection_status"])
    if interface.get("oper_status") in UNAVAILABLE_OPER_STATUSES | {"unknown"}:
        reasons.append("oper_" + interface["oper_status"])
    if interface.get("admin_status") == "down":
        reasons.append("admin_down")
    source["retention_reasons"] = reasons
    return source


def _navigation(network):
    """Rebuild only L2 navigation; no stale old navigation/supporting references."""
    nodes = {n["node-id"]: n for n in network.get("node", [])}
    for node in nodes.values():
        node.pop(ST + "linked-node", None)
        for tp in node.get(NT + "termination-point", []):
            tp.pop(ST + "linked-termination-point", None)
    for link in network.get(NT + "link", []):
        points = link.get(ST + "tp", [])
        for point in points:
            node = nodes[point["node-ref"]]
            tp = next(p for p in node[NT + "termination-point"] if p["tp-id"] == point["tp-ref"])
            for other in points:
                if other == point:
                    continue
                ref = {key: other[key] for key in ("network-ref", "node-ref", "tp-ref")}
                linked = tp.setdefault(ST + "linked-termination-point", [])
                if ref not in linked:
                    linked.append(ref)
                node_ref = {key: other[key] for key in ("network-ref", "node-ref")}
                linked_nodes = node.setdefault(ST + "linked-node", [])
                if node_ref["node-ref"] != node["node-id"] and node_ref not in linked_nodes:
                    linked_nodes.append(node_ref)


def retain_l2_links(current_tree, prior_tree, device_states, prior_snapshot=None):
    """Return a copied tree and a {link-id: historical/current-state metadata} map.

    ``device_states`` is assembled by the caller from stable, known managed node
    identities, never from an old address alone. Values contain ``address``,
    ``collection_status`` (complete/failed/unknown), optional collection evidence
    and time, and ``interfaces`` keyed by canonical interface name with explicit
    ``oper_status``/``admin_status`` and optional observation evidence/time.
    ``lldp_ports`` lists canonical local ports from this run's complete LLDP
    observation. Only that evidence, not an ARP-derived current link or successful
    interface collection alone, establishes current adjacency priority.

    Only explicit unavailable/unknown or failed/unknown collection permits retaining a
    missing relationship. A complete device with an unobserved interface does not.
    Fresh LLDP adjacency occupying either endpoint wins, including an advertised
    uncollected peer or shared segment. ``prior_snapshot``
    may supply snapshot_id, evidence ({link-id: evidence}), last_seen and its source;
    no timestamp is invented or treated as an interface observation implicitly.
    The result retains L2 structure only; current L3 networks are unchanged.
    """
    tree, metadata = deepcopy(current_tree), {}
    prior_snapshot = prior_snapshot or {}
    prior = {n["network-id"]: n for n in _networks(prior_tree)}
    for network in _networks(tree):
        network_id = network["network-id"]
        old = prior.get(network_id)
        if (
            L2T + "l2-topology" not in network.get("network-types", {})
            or network.get("supporting-network")  # an overlay, not ports and cables
            or old is None
            or L2T + "l2-topology" not in old.get("network-types", {})
        ):
            continue
        old_nodes = {n["node-id"]: n for n in old.get("node", [])}
        nodes = {n["node-id"]: n for n in network.get("node", [])}
        links = network.setdefault(NT + "link", [])
        ids = {link["link-id"] for link in links}
        occupied = set()
        for link in links:
            points = link.get(ST + "tp", [])
            if len(points) < 2 or any(p.get("network-ref") != network_id for p in points):
                continue
            ends = [(p.get("node-ref"), p.get("tp-ref")) for p in points]
            for node_id, tp_id in ends:
                device = device_states.get(node_id, {})
                if device.get("collection_status") != "complete" or node_id not in nodes:
                    continue
                name = _interface(nodes[node_id], tp_id)
                ports = device.get("lldp_ports", [])
                if name and isinstance(ports, list) and name in ports:
                    occupied.update(ends)
                    break
        retained = False
        for link in old.get(NT + "link", []):
            ends = _ends(link, network_id)
            if not ends or link["link-id"] in ids or any(e in occupied for e in ends):
                continue
            if any(n not in device_states or n not in old_nodes for n, _ in ends):
                continue
            names = [_interface(old_nodes[n], tp) for n, tp in ends]
            if any(name is None for name in names):
                continue  # Old endpoint/MAC inference is not a managed port relationship.
            sources = [
                _source(n, tp, name, device_states[n])
                for (n, tp), name in zip(ends, names, strict=True)
            ]
            if not any(source["retention_reasons"] for source in sources):
                continue
            for (node_id, tp_id), name in zip(ends, names, strict=True):
                if node_id not in nodes:
                    nodes[node_id] = {"node-id": node_id, NT + "termination-point": []}
                    network.setdefault("node", []).append(nodes[node_id])
                points = nodes[node_id].setdefault(NT + "termination-point", [])
                if not any(p["tp-id"] == tp_id for p in points):
                    points.append(
                        {
                            "tp-id": tp_id,
                            L2T + "l2-termination-point-attributes": {"interface-name": name},
                        }
                    )
            kept = {
                key: deepcopy(link[key])
                for key in ("link-id", ST + "link-direction", ST + "link-type", ST + "tp")
                if key in link
            }
            links.append(kept)
            ids.add(link["link-id"])
            entry = {
                "network_id": network_id,
                "scope": "l2_only",
                "historical": True,
                "state": "down" if any(s["oper_status"] == "down" for s in sources) else "unknown",
                "available": False
                if any(
                    s["oper_status"] in UNAVAILABLE_OPER_STATUSES or s.get("admin_status") == "down"
                    for s in sources
                )
                else None,
                "state_sources": sources,
                "prior_evidence": deepcopy(
                    prior_snapshot.get("evidence", {}).get(link["link-id"], [])
                ),
            }
            if "snapshot_id" in prior_snapshot:
                entry["prior_snapshot_id"] = prior_snapshot["snapshot_id"]
            if "last_seen" in prior_snapshot:
                entry["last_seen"] = prior_snapshot["last_seen"]
                entry["last_seen_source"] = prior_snapshot.get("last_seen_source", "provided")
            metadata[link["link-id"]] = entry
            retained = True
        if retained:
            _navigation(network)
    return tree, metadata
