"""Canvas projection of a SIMAP L2/L3 tree; the tree itself stays the exported model.

Devices and hosts are nodes. A p2p L2 link is one edge. An mp2mp L2 link (shared
media) is drawn as one segment node joined to each member port, because an
edge can only have two ends. Each edge lists the L3 subnets its L2 link carries.
"""

from .builder import L2T, L3T, NT, ST, host_node_prefix


def _network(tree, network_type):
    return next(
        n for n in tree["ietf-network:networks"]["network"] if network_type in n["network-types"]
    )


def canvas(tree, evidence, collected, base):
    """collected: {node-id: {"address", "evidence_id", "observed_at"}} for collected devices."""
    l2 = _network(tree, L2T + "l2-topology")
    l3 = _network(tree, L3T + "l3-unicast-topology")
    l3_nodes = {n["node-id"]: n for n in l3["node"]}
    carried = {}
    for link in l3.get(NT + "link", []):
        subnet = link["link-id"].removeprefix(base + "l3-link:")
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
            " ".join(filter(None, (tp.get(ST + "name"), ip)))
            for tp in l3_nodes.get(node_id, {}).get(NT + "termination-point", [])
            for ip in tp.get(L3T + "l3-termination-point-attributes", {}).get("ip-address", [])
        ]
        item = {"id": node_id, "tps": tps, "addresses": addresses, "freshness": "observed"}
        if node_id.startswith(host_node_prefix(base)):
            item.update(name=addresses[0] if addresses else tps[0]["name"], identity_status="host")
        else:
            name = node.get(L2T + "l2-node-attributes", {}).get("name") or node_id
            item.update(name=name, identity_status="advertised")
            if node_id in collected:
                item.update(collected[node_id], identity_status="collected")
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
