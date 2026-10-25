"""Pure snapshot association of current interface state and observed L2 history.

RFC 8343 device instances remain independent from RFC 8345/SIMAP. Identity,
availability, evidence, time and completeness are application metadata, never
additional YANG leaves or a claim that an observed baseline is intended state.
"""

from __future__ import annotations

from copy import deepcopy

from .builder import L2T, NT, ST
from .device_acl import project_device_acl
from .device_control import project_device_control
from .device_ip import enrich_device_ip
from .device_pbr import project_device_pbr
from .device_state import project_device_state
from .inventory import project_inventory
from .retained_links import UNAVAILABLE_OPER_STATUSES, retain_l2_links
from .standard_tables import builder_rows
from .standard_trees import project as project_standard


def _l2(tree):
    """The physical L2 networks; an overlay (e.g. VXLAN) is supported by another network."""
    return [
        network
        for network in tree.get("ietf-network:networks", {}).get("network", [])
        if L2T + "l2-topology" in network.get("network-types", {})
        and not network.get("supporting-network")
    ]


def _port_names(tree):
    names = {}
    for network in _l2(tree):
        for node in network.get("node", []):
            for point in node.get(NT + "termination-point", []):
                name = point.get(L2T + "l2-termination-point-attributes", {}).get("interface-name")
                if isinstance(name, str) and name:
                    names.setdefault(node["node-id"], {})[point["tp-id"]] = name
    return names


def _identity(collected, old_graph):
    candidates = {}
    for node in old_graph.get("nodes", []):
        if node.get("identity_status") in {"collected", "known"}:
            address, node_id = node.get("address"), node.get("id")
            if isinstance(address, str) and isinstance(node_id, str):
                candidates.setdefault(address, set()).add(node_id)
    for device in old_graph.get("state", {}).get("devices", []):
        address, node_id = device.get("address"), device.get("node_id")
        if isinstance(address, str) and isinstance(node_id, str):
            candidates.setdefault(address, set()).add(node_id)
    current = {}
    for node_id, source in collected.items():
        address = source.get("address")
        if isinstance(address, str):
            current.setdefault(address, set()).add(node_id)
    current_ids = {node_id for nodes in current.values() for node_id in nodes}
    # A current identity at a new address cannot also become a failed old address.
    for address, nodes in candidates.items():
        if address not in current:
            nodes.difference_update(current_ids)
    candidates.update(current)
    return {
        address: next(iter(nodes)) if len(nodes) == 1 else None
        for address, nodes in candidates.items()
    }


def _lldp_ports(data):
    if data.get("complete") is not True:
        return []
    caps = data.get("capabilities")
    if caps is not None and (not isinstance(caps, dict) or caps.get("lldp") != "complete"):
        return []
    facts = data.get("facts", {})
    rows = facts.get("lldp", []) if isinstance(facts, dict) else []
    if not isinstance(rows, list):
        return []
    return sorted(
        {
            row["local_if"]
            for row in rows
            if isinstance(row, dict) and isinstance(row.get("local_if"), str) and row["local_if"]
        }
    )


def _device_trees(observation, data):
    """(device tree, control sections) of one observation.

    A device read as standard records over NETCONF (netconf-api's conversion layer)
    exports the trees built from them, ACL and PBR included (their builders read the
    rows taken from the records). Other devices export the CLI projections.
    """
    standard = data.get("standard")
    records = standard.get("records") if isinstance(standard, dict) else None
    if isinstance(records, dict) and "interface" in records:
        identity = (data.get("platform") or {}).get("identity") or {}
        platform = identity.get("os_family", "")
        built = project_standard(records, platform)
        facts = data.get("facts") if isinstance(data.get("facts"), dict) else {}
        rows = {
            "data": {"facts": {**facts, **builder_rows(records, platform)}},
            **{k: observation[k] for k in ("id", "observed_at") if k in observation},
        }
        control = {
            **built["control"],
            "acl": project_device_acl(rows),
            "pbr": project_device_pbr(rows),
            "source": {"netconf_mapping": standard.get("mapping")},
        }
        return built["device"], control
    projected = enrich_device_ip(project_device_state(observation), observation)
    control = project_device_control(observation)
    control["pbr"] = project_device_pbr(observation or {})
    return projected, control


def _collection(data, target):
    if data.get("complete") is True:
        return "complete"
    if data.get("complete") is False or target.get("status") == "failed":
        return "failed"
    return "unknown"


def _link_state(link, names, known):
    sources, proof_times = [], []
    proven = False
    for point in link.get(ST + "tp", []):
        node_id, tp_id = point["node-ref"], point["tp-ref"]
        name = names.get(node_id, {}).get(tp_id)
        device = known.get(node_id, {})
        interface = device.get("interfaces", {}).get(name, {})
        source = {
            "node_id": node_id,
            "tp_id": tp_id,
            "interface_name": name,
            "collection_status": device.get("collection_status", "unknown"),
            "oper_status": interface.get("oper_status", "unknown"),
        }
        for key in ("address", "evidence_id", "observed_at"):
            if key in device:
                source[key] = deepcopy(device[key])
        if "admin_status" in interface:
            source["admin_status"] = interface["admin_status"]
        if "evidence_id" in source:
            source["evidence_id_source"] = "current_observation"
        if "observed_at" in source:
            source["observed_at_source"] = "current_observation"
        sources.append(source)
        if device.get("collection_status") == "complete" and name in device.get("lldp_ports", []):
            proven = True
            if isinstance(device.get("observed_at"), str):
                proof_times.append(device["observed_at"])
    down = any(source["oper_status"] == "down" for source in sources)
    unavailable = any(
        source["oper_status"] in UNAVAILABLE_OPER_STATUSES or source.get("admin_status") == "down"
        for source in sources
    )
    up = bool(sources) and proven and all(source["oper_status"] == "up" for source in sources)
    entry = {
        "state": "down" if down else "unavailable" if unavailable else "up" if up else "unknown",
        "available": False if unavailable else True if up else None,
        "state_sources": sources,
        "historical": False,
        "current_lldp_proven": proven,
    }
    if proof_times:
        entry["last_seen"] = max(proof_times)
        entry["last_seen_source"] = "lldp_observation"
    return entry


def enrich_snapshot_state(tree, collected, observations, targets, prior=None):
    """Return a copied standard tree and independent ``{devices, links}`` metadata.

    Observations contain this run's latest record per address, including failed
    LLDP collection with usable interfaces. ``prior`` is the caller's authorized,
    same-network snapshot ({snapshot_id, graph, created_at}); it contributes stable
    identity and historical L2 structure only, never old current interface facts.
    """
    prior = prior or {}
    old_graph = prior.get("graph", {})
    old_tree = old_graph.get("simap", {})
    if not ({n["network-id"] for n in _l2(tree)} & {n["network-id"] for n in _l2(old_tree)}):
        old_graph, old_tree = {}, {}
    identities = _identity(collected, old_graph)
    by_address = {o["address"]: o for o in observations}
    target_map = {t["address"]: t for t in targets}
    old_names, current_names = _port_names(old_tree), _port_names(tree)
    current_labels = {
        node["node-id"]: node[L2T + "l2-node-attributes"]["name"]
        for network in _l2(tree)
        for node in network.get("node", [])
        if node.get(L2T + "l2-node-attributes", {}).get("name")
    }
    prior_labels = {
        node["id"]: node["name"]
        for node in old_graph.get("nodes", [])
        if node.get("id") and node.get("name")
    }
    known, devices = {}, []
    for address in sorted(set(by_address) | set(target_map) | set(identities)):
        observation = by_address.get(address)
        data = observation.get("data", {}) if observation else {}
        data = data if isinstance(data, dict) else {}
        projected, control = _device_trees(observation, data)
        target = target_map.get(address, {})
        node_id = identities.get(address)
        status = _collection(data, target)
        device = {
            "address": address,
            "node_id": node_id,
            "collection_status": status,
            **deepcopy(projected),
            "control": control,
            "inventory": project_inventory(observation or {}, node_id),
        }
        if node_id in current_labels:
            device["name"] = current_labels[node_id]
            device["name_source"] = "current_topology_observation"
        elif node_id in prior_labels:
            device["name"] = prior_labels[node_id]
            device["name_source"] = "prior_snapshot"
        for key in ("status", "reason"):
            if key in target:
                device["target_" + key] = deepcopy(target[key])
        devices.append(device)
        if node_id is None:
            device["gaps"].append({"code": "stable_topology_identity_not_observed"})
            continue
        interfaces = {
            row["name"]: {"oper_status": row.get("oper-status", "unknown")}
            for row in projected["tree"]["ietf-interfaces:interfaces"]["interface"]
        }
        facts = data.get("facts", {})
        rows = facts.get("interfaces", []) if isinstance(facts, dict) else []
        caps = data.get("capabilities")
        interfaces_usable = caps is None or (
            isinstance(caps, dict) and caps.get("interfaces") in {"complete", "partial"}
        )
        metadata = data.get("fact_metadata", {})
        details = metadata.get("interfaces", {}) if isinstance(metadata, dict) else {}
        if isinstance(details, dict) and "collection_incomplete" in details.get("warnings", []):
            interfaces_usable = False
        if interfaces_usable and isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict) or row.get("ifname") not in interfaces:
                    continue
                if row.get("admin_status") in {"up", "down", "testing"}:
                    interfaces[row["ifname"]]["admin_status"] = row["admin_status"]
        for name in {
            *old_names.get(node_id, {}).values(),
            *current_names.get(node_id, {}).values(),
        }:
            interfaces.setdefault(name, {"oper_status": "unknown"})
        source = {
            "address": address,
            "collection_status": status,
            "interfaces": interfaces,
            "lldp_ports": _lldp_ports(data),
        }
        if observation:
            for key, original in (("evidence_id", "id"), ("observed_at", "observed_at")):
                if original in observation:
                    source[key] = deepcopy(observation[original])
        known[node_id] = source
    history = {"evidence": old_graph.get("evidence", {})}
    if old_graph:
        if "snapshot_id" in prior:
            history["snapshot_id"] = prior["snapshot_id"]
        if prior.get("created_at") is not None:
            history["last_seen"] = prior["created_at"]
            history["last_seen_source"] = "snapshot_creation"
    new_tree, retained = retain_l2_links(tree, old_tree, known, history)
    names, links = _port_names(new_tree), {}
    previous_links = old_graph.get("state", {}).get("links", {})
    for network in _l2(new_tree):
        for link in network.get(NT + "link", []):
            link_id = link["link-id"]
            entry = _link_state(link, names, known)
            old_entry = previous_links.get(link_id, {})
            if link_id in retained or (
                old_entry.get("historical") is True and not entry["current_lldp_proven"]
            ):
                historical = deepcopy(
                    retained.get(
                        link_id,
                        {
                            "network_id": network["network-id"],
                            "scope": "l2_only",
                        },
                    )
                )
                if old_entry.get("historical") is True:
                    for key in (
                        "prior_snapshot_id",
                        "prior_evidence",
                        "last_seen",
                        "last_seen_source",
                    ):
                        historical.pop(key, None)
                        if key in old_entry:
                            historical[key] = deepcopy(old_entry[key])
                elif "last_seen" in old_entry:
                    historical["last_seen"] = deepcopy(old_entry["last_seen"])
                    historical["last_seen_source"] = old_entry.get("last_seen_source", "provided")
                # Fresh state always replaces the previous snapshot's state and sources.
                historical.update(
                    {
                        key: deepcopy(entry[key])
                        for key in ("state", "available", "state_sources", "current_lldp_proven")
                    }
                )
                historical["historical"] = True
                # A retained relation was not present in the current adjacency tree;
                # an LLDP local port alone cannot prove its historical peer.
                if link_id in retained:
                    historical["current_lldp_proven"] = False
                if not historical["current_lldp_proven"] and entry["available"] is True:
                    historical.update(state="unknown", available=None)
                entry = historical
            links[link_id] = entry
    return new_tree, {"devices": devices, "links": links}
