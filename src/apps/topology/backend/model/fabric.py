"""Overlay networks of an EVPN-VXLAN fabric (plan §8 M5), beside the L2 and L3 networks.

VXLAN network (RFC 8944 l2-topology with the VXLAN feature, SIMAP supporting relations):

- A node per collected VTEP (a local VTEP address: IOS EVI "VTEP IP", Comware VXLAN
  tunnel source), supported by its node in the L3 network: the instance of that address.
- A termination point per VNI the device shows, encapsulation vxlan with its vni-id,
  supported by the L3 termination point that owns the VTEP address.
- A link per VNI between two VTEPs when one of them shows the other as a peer of that VNI
  (IOS "show nve peers", Comware IMET routes and the VXLAN tunnels bound to the VSI) and
  the other shows the VNI too. A peer outside the collection, or one that does not show
  the VNI, is a gap, not a link. The underlay path is not a supporting link: the tunnel
  rides whatever route the underlay chooses.

Fabric network (RFC 8542): devices joined by established BGP EVPN sessions, both ends
collected, with at least one VTEP among them, form one fabric node. A device with a VTEP
is a leaf; an EVPN speaker without one (a route reflector) is a spine; a border role is
not inferred. Device links are the L2 links between members. The fabric-id is
platform-assigned from the lowest member BGP router ID.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict

from .overlay import vtep_addresses

NT = "ietf-network-topology:"
L2T = "ietf-l2-topology:"
FABRIC = "ietf-dc-fabric-topology:"
FABRIC_TYPES = "ietf-dc-fabric-types:"


def _iid_node(network, node):
    return f"/ietf-network:networks/network[network-id='{network}']/node[node-id='{node}']"


def _iid_link(network, link):
    return f"/ietf-network:networks/network[network-id='{network}']/{NT}link[link-id='{link}']"


def _owners(devices, addresses_of):
    owner = defaultdict(set)
    for key, facts in devices.items():
        for address in addresses_of(facts):
            owner[address].add(key)
    return {address: keys.pop() for address, keys in owner.items() if len(keys) == 1}


def _vnis(facts):
    return sorted({row["vni"] for row in facts.get("vxlan_vnis") or [] if "vni" in row})


def _peers(facts):
    """{(vni, peer address)} this VTEP shows."""
    found = {
        (row["vni"], row["peer"])
        for row in facts.get("evpn_peers") or []
        if "vni" in row and row.get("peer")
    }
    tunnels = {
        row["ifname"]: row.get("destination")
        for row in facts.get("tunnel_interfaces") or []
        if row.get("mode") == "vxlan"
    }
    for row in facts.get("vxlan_vnis") or []:
        for tunnel in row.get("tunnels") or []:
            if tunnels.get(tunnel.get("ifname")) and "vni" in row:
                found.add((row["vni"], tunnels[tunnel["ifname"]]))
    return found


def vxlan_network(topology, gaps):
    """(network or None, {link-id: evidence}) of the VXLAN overlay."""
    devices, base = topology.devices, topology.base
    net = base + "vxlan"
    owner = _owners(devices, vtep_addresses)
    nodes, ends = {}, {}
    for key, facts in sorted(devices.items()):
        local = sorted(vtep_addresses(facts))
        vnis = _vnis(facts)
        if not local or not vnis:
            continue
        node_id = topology.device_node(topology.chassis[key])
        rows = [r for r in facts.get("ip") or [] if r.get("address") in local]
        support = {(r.get("vrf", "default"), r["ifname"]) for r in rows}
        node = {"node-id": node_id}
        if len({vrf for vrf, _ in support}) == 1:
            vrf = next(iter(support))[0]
            l3_node = topology.l3_node(key, vrf)
            node["supporting-node"] = [{"network-ref": topology.l3_net, "node-ref": l3_node}]
        else:
            l3_node = None
            gaps.append({"code": "vtep_address_not_observed", "device": key, "vtep": local})
        tps = []
        for vni in vnis:
            tp = {
                "tp-id": f"{base}vni:{vni}",
                L2T + "l2-termination-point-attributes": {
                    "encapsulation-type": L2T + "vxlan",
                    "vxlan": {"vni-id": vni},
                },
            }
            if l3_node and len(support) == 1:
                tp["supporting-termination-point"] = [
                    {
                        "network-ref": topology.l3_net,
                        "node-ref": l3_node,
                        "tp-ref": topology.port_tp(next(iter(support))[1]),
                    }
                ]
            tps.append(tp)
            ends[(key, vni)] = (node_id, tp["tp-id"])
        node[NT + "termination-point"] = tps
        nodes[node_id] = node
    if not nodes:
        return None, {}
    seen = defaultdict(set)
    for key, facts in sorted(devices.items()):
        for vni, peer in sorted(_peers(facts)):
            other = owner.get(peer)
            if (key, vni) not in ends:
                continue
            if other is None:
                gaps.append(
                    {"code": "vtep_outside_collection", "device": key, "vni": vni, "peer": peer}
                )
            elif (other, vni) not in ends:
                gaps.append({"code": "vni_not_on_peer", "device": key, "vni": vni, "peer": peer})
            elif other != key:
                pair = tuple(sorted((ends[(key, vni)], ends[(other, vni)])))
                seen[pair].add(f"vxlan peer {peer} vni {vni} seen by {key}")
    links, evidence = [], {}
    for refs, why in sorted(seen.items()):
        link_id = base + "vxlan-link:" + hashlib.sha256(repr(refs).encode()).hexdigest()[:12]
        links.append(topology.link(net, link_id, list(refs)))
        evidence[link_id] = sorted(why)
    nodes = dict(sorted(nodes.items()))
    topology.navigation(net, nodes, links)
    network = topology.network(
        net,
        L2T + "l2-topology",
        nodes,
        links,
        {"supporting-network": [{"network-ref": topology.l3_net}]},
    )
    return network, evidence


def _evpn_speakers(devices):
    """{key: (router ID, {peer address})} of devices with established BGP EVPN sessions."""
    speakers = {}
    for key, facts in devices.items():
        table = facts.get("bgp_evpn")
        if not isinstance(table, dict):
            continue
        peers = {p["peer"] for p in table.get("peers") or [] if p.get("state") == "established"}
        if peers:
            speakers[key] = (table.get("router_id"), peers)
    return speakers


def fabric_network(topology, l2_links, gaps):
    """The RFC 8542 fabric network, or None when no fabric is proven."""
    devices, base = topology.devices, topology.base
    speakers = _evpn_speakers(devices)
    owner = _owners(
        devices, lambda facts: {r["address"] for r in facts.get("ip") or [] if r.get("address")}
    )
    parent = {key: key for key in speakers}

    def root(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    for key, (_, peers) in sorted(speakers.items()):
        for peer in sorted(peers):
            other = owner.get(peer)
            if other in speakers and other != key:
                parent[root(key)] = root(other)
    groups = defaultdict(list)
    for key in sorted(speakers):
        groups[root(key)].append(key)
    nodes = {}
    for members in groups.values():
        leaves = [k for k in members if vtep_addresses(devices[k])]
        if not leaves:
            continue
        ids = {k: topology.device_node(topology.chassis[k]) for k in members}
        router_ids = sorted(speakers[k][0] for k in members if speakers[k][0])
        fabric_id = base + "fabric:" + (router_ids[0] if router_ids else min(ids.values()))
        member_ids = set(ids.values())
        links = [
            link["link-id"]
            for link in l2_links
            if {t["node-ref"] for t in link["ietf-simap-topology:tp"]} <= member_ids
        ]
        attributes = {
            "fabric-id": fabric_id,
            "type": FABRIC_TYPES + "vxlan-fabric",
            "device-nodes": [
                {
                    "device-ref": _iid_node(topology.l2_net, ids[k]),
                    "role": [FABRIC_TYPES + ("leaf" if k in leaves else "spine")],
                }
                for k in sorted(members, key=lambda k: ids[k])
            ],
        }
        if links:
            attributes["device-links"] = [
                {"link-ref": _iid_link(topology.l2_net, link)} for link in sorted(links)
            ]
        nodes[fabric_id] = {"node-id": fabric_id, FABRIC + "fabric-attributes": attributes}
    if not nodes:
        return None
    network = topology.network(
        base + "fabric",
        FABRIC + "fabric-network",
        dict(sorted(nodes.items())),
        [],
        {"supporting-network": [{"network-ref": topology.l2_net}]},
    )
    del network[NT + "link"]
    return network


def overlay_networks(topology, l2_links):
    """([VXLAN network, fabric network] that are proven, {link-id: evidence}, gaps)."""
    gaps = []
    networks, evidence = [], {}
    vxlan, vxlan_evidence = vxlan_network(topology, gaps)
    if vxlan:
        networks.append(vxlan)
        evidence.update(vxlan_evidence)
    fabric = fabric_network(topology, l2_links, gaps)
    if fabric:
        networks.append(fabric)
    return networks, evidence, gaps
