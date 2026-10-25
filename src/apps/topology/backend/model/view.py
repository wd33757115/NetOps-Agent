"""Canvas projection of a SIMAP L2/L3 tree; the tree itself stays the exported model.

Devices and unidentified endpoints are nodes. A p2p L2 link is one edge. An mp2mp L2 link (shared
media) is drawn as one segment node joined to each member port, because an
edge can only have two ends. Each edge lists the L3 subnets its L2 link carries.
"""

from .builder import L2T, L3T, NT, ST, host_node_prefix


def _network(tree, network_type):
    return next(
        n for n in tree["ietf-network:networks"]["network"] if network_type in n["network-types"]
    )


def canvas(tree, evidence, collected, base, state=None):
    """collected: {node-id: {"address", "evidence_id", "observed_at"}} for collected devices."""
    l2 = _network(tree, L2T + "l2-topology")
    state = state or {}
    known = {d["node_id"]: d for d in state.get("devices", []) if d.get("node_id")}
    # L3 nodes represent device-local instances, supported by their L2 device.
    # Older snapshots used one network per VRF name; retain their display fallback.
    l3_tps, carried = {}, {}
    for l3 in tree["ietf-network:networks"]["network"]:
        if L3T + "l3-unicast-topology" not in l3["network-types"]:
            continue
        vrf = l3.get(L3T + "l3-topology-attributes", {}).get("name")
        for node in l3["node"]:
            device_refs = [
                ref["node-ref"]
                for ref in node.get("supporting-node", [])
                if ref.get("network-ref") == l2["network-id"]
            ]
            device_id = device_refs[0] if len(device_refs) == 1 else node["node-id"]
            instance = node.get(ST + "name", vrf)
            for tp in node.get(NT + "termination-point", []):
                l3_tps.setdefault(device_id, []).append((instance, tp))
        for link in l3.get(NT + "link", []):
            subnet = link.get(L3T + "l3-link-attributes", {}).get("name") or link[
                "link-id"
            ].removeprefix(base + "l3-link:")
            for ref in link.get("supporting-link", []):
                carried.setdefault(ref["link-ref"], []).append(subnet)
    nodes, links = [], []
    for node in l2["node"]:
        node_id = node["node-id"]
        tps = []
        for tp in node[NT + "termination-point"]:
            attrs = tp.get(L2T + "l2-termination-point-attributes", {})
            tps.append(
                {"id": tp["tp-id"], "name": attrs.get("interface-name") or attrs.get("mac-address")}
            )
        addresses = [
            " ".join(filter(None, (vrf, tp.get(ST + "name"), ip)))
            for vrf, tp in l3_tps.get(node_id, [])
            for ip in tp.get(L3T + "l3-termination-point-attributes", {}).get("ip-address", [])
        ]
        item = {"id": node_id, "tps": tps, "addresses": addresses, "freshness": "observed"}
        if node_id.startswith(host_node_prefix(base)):
            # ARP/FDB identify an observed interface endpoint, not a physical host.
            # Keep existing node URIs so old snapshots and evidence remain addressable.
            item.update(
                name=addresses[0] if addresses else tps[0]["name"], identity_status="endpoint"
            )
        else:
            name = (
                node.get(L2T + "l2-node-attributes", {}).get("name")
                or known.get(node_id, {}).get("name")
                or node_id
            )
            item.update(name=name, identity_status="advertised")
            if node_id in collected:
                item.update(collected[node_id], identity_status="collected")
            elif node_id in known:
                item.update(
                    address=known[node_id]["address"],
                    identity_status="known",
                    freshness="historical",
                )
            if node_id in known:
                item["collection_status"] = known[node_id]["collection_status"]
        nodes.append(item)
    by_id = {n["id"]: n for n in nodes}

    def end(ref):
        node = by_id[ref["node-ref"]]
        port = next((tp["name"] for tp in node["tps"] if tp["id"] == ref["tp-ref"]), "")
        host = node.get("address") or node["name"]
        return {"node": node["name"], "address": host, "port": port}

    for link in l2.get(NT + "link", []):
        ends = link[ST + "tp"]
        # SIMAP link-direction; every endpoint of a bidirectional link is shown.
        direction = link.get(ST + "link-direction", "").rsplit("-", 1)[-1] or "unknown"
        members = [end(ref) for ref in ends]
        common = {
            "freshness": "observed",
            "direction": direction,
            "evidence": evidence.get(link["link-id"], []),
            "subnets": sorted(carried.get(link["link-id"], [])),
        }
        status = state.get("links", {}).get(link["link-id"])
        if status:
            common.update(status)
            common["freshness"] = "historical" if status.get("historical") else "observed"
        if len(ends) == 2:
            links.append(
                {
                    "id": link["link-id"],
                    "source": ends[0]["node-ref"],
                    "source_tp": ends[0]["tp-ref"],
                    "target": ends[1]["node-ref"],
                    "target_tp": ends[1]["tp-ref"],
                    "link_type": "p2p",
                    "label": (" ⇄ " if direction == "bi" else " → ").join(
                        m["port"] for m in members
                    ),
                    "ends": [f"{m['node']} {m['address']} {m['port']}".strip() for m in members],
                    **common,
                }
            )
            continue
        nodes.append(
            {
                "id": link["link-id"],
                "name": "共享网段",
                "identity_status": "segment",
                "tps": [],
                "addresses": common["subnets"],
                "members": len(ends),
                "ends": [f"{m['node']} {m['address']} {m['port']}".strip() for m in members],
                **common,
            }
        )
        for index, (ref, member) in enumerate(zip(ends, members, strict=True)):
            links.append(
                {
                    "id": f"{link['link-id']}#{index}",
                    "source": ref["node-ref"],
                    "source_tp": ref["tp-ref"],
                    "target": link["link-id"],
                    "target_tp": "",
                    "link_type": "mp2mp",
                    "label": member["port"],
                    "ends": [f"{member['node']} {member['address']} {member['port']}".strip()],
                    **common,
                }
            )
    return nodes, links
