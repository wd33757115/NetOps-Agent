"""Build SIMAP (RFC 8345 + draft-havel-nmop-simap-yang-03) L2/L3 networks from parsed facts.

Input is per collected device: parser records for lldp_local (summary), lldp, interfaces,
ip, arp and mac. Output is RFC 7951 JSON plus the evidence behind every link.

- A device is identified by the chassis ID it reports in lldp_local, the same key its
  neighbours advertise, so a collected device and an advertised neighbour are one node.
- Links use SIMAP bidirectional links with an st:tp list instead of RFC 8345 pairs of
  unidirectional links. Shared media is one mp2mp link, never an invented hub node.
- An L2 link is L2 adjacency, not a proven cable. Unmanaged switches stay invisible.
"""

from __future__ import annotations

import hashlib
import ipaddress
from urllib.parse import quote

from ..collectors.ifname import normalize_ifname

ST = "ietf-simap-topology:"
NT = "ietf-network-topology:"
L2T = "ietf-l2-topology:"
L3T = "ietf-l3-unicast-topology:"
L2_PORT_TYPES = frozenset({"ethernet", "lag"})


def _uri(value):
    return quote(str(value), safe="-._~:")


def _dash(mac):
    return mac.replace(":", "-")


class _Topology:
    def __init__(self, devices, base):
        self.devices = devices
        self.base = base
        self.l2_net = base + "l2"
        self.l3_net = base + "l3"
        self.chassis = {}
        for key, facts in devices.items():
            chassis = (facts.get("lldp_local") or {}).get("chassis_id")
            self.chassis[key] = chassis or f"target:{key}"
        self.by_chassis = {c: k for k, c in self.chassis.items()}
        # Every address on a collected device, so ARP entries for it are not hosts.
        self.owner = {}
        for key, facts in devices.items():
            for rec in facts.get("ip", []):
                self.owner[(rec.get("vrf", "default"), rec["address"])] = (key, rec["ifname"])
        self.l2_nodes = {}
        self.host_ips = {}

    # identifiers -------------------------------------------------------------
    def device_node(self, chassis):
        return self.base + "device:" + _uri(_dash(chassis))

    def port_tp(self, ifname):
        return self.base + "if:" + _uri(ifname)

    def host_node(self, mac):
        return self.base + "host:" + _dash(mac)

    def nic_tp(self, mac):
        return self.base + "nic:" + _dash(mac)

    def ref(self, member):
        if member[0] == "port":
            return self.device_node(member[1]), self.port_tp(member[2])
        return self.host_node(member[1]), self.nic_tp(member[1])

    # L2 ------------------------------------------------------------------------
    def remote_port(self, chassis, port_id, subtype):
        key = self.by_chassis.get(chassis)
        if key and str(subtype).lower() == "interface name":
            return normalize_ifname(port_id, self.devices[key].get("platform", "")).name
        return port_id

    def observations(self):
        """Pairs of L2 endpoints with evidence.

        Hosts attach where the evidence is most specific: an edge port (no LLDP
        neighbour) from the MAC table or ARP beats a sighting on an LLDP port, which
        in a switch chain only means "somewhere behind that neighbour". A host seen
        only on LLDP ports (a hub segment) keeps those sightings.
        """
        found, hosts = [], {}
        managed_macs = set()
        for key, facts in self.devices.items():
            me = self.chassis[key]
            lldp_ports = {rec["local_if"] for rec in facts.get("lldp", [])}
            advertised = {}
            for rec in facts.get("lldp", []):
                local, peer = rec["local_if"], rec["remote_chassis_id"]
                port = self.remote_port(peer, rec["remote_port_id"], rec.get("remote_port_subtype"))
                self.l2_node(peer, rec.get("remote_sysname"))
                for address in rec.get("remote_mgmt_addresses", []):
                    advertised[(local, address)] = ("port", peer, port)
                found.append(
                    (
                        ("port", me, local),
                        ("port", peer, port),
                        f"{key} lldp {local} -> {peer} {port}",
                    )
                )
            for rec in facts.get("arp", []):
                vrf, port = rec.get("vrf", "default"), self.attachment(facts, rec)
                if (vrf, rec["ip"]) in self.owner:
                    managed_macs.add(rec["mac"])
                    other_key, other_if = self.owner[(vrf, rec["ip"])]
                    if port and port not in lldp_ports:
                        other = ("port", self.chassis[other_key], other_if)
                        why = f"{key} arp {rec['ip']} {rec['mac']} {port}"
                        found.append((("port", me, port), other, why))
                    continue  # on an LLDP port the adjacency is already recorded
                if (rec["ifname"], rec["ip"]) in advertised:
                    managed_macs.add(rec["mac"])
                    continue
                self.host_ips.setdefault(rec["mac"], set()).add((vrf, rec["ip"]))
                if port:
                    strength = "weak" if port in lldp_ports else "strong"
                    hosts.setdefault(rec["mac"], {"strong": [], "weak": []})[strength].append(
                        (("port", me, port), f"{key} arp {rec['ip']} {rec['mac']} {port}")
                    )
            for rec in facts.get("mac", []):
                port = rec["port"]
                strength = "weak" if port in lldp_ports else "strong"
                hosts.setdefault(rec["mac"], {"strong": [], "weak": []})[strength].append(
                    (
                        ("port", me, port),
                        f"{key} mac {rec['mac']} vlan {rec.get('vlan_or_bd')} {port}",
                    )
                )
        for mac, seen in sorted(hosts.items()):
            if mac in managed_macs:
                continue  # a collected or advertised device's own interface
            for member, why in seen["strong"] or seen["weak"]:
                found.append((member, ("nic", mac), why))
        return found

    def attachment(self, facts, arp):
        """Physical port behind an ARP row: its own port, or the MAC table for VLAN rows."""
        platform = facts.get("platform", "")
        name = normalize_ifname(arp["ifname"], platform)
        if name.kind in L2_PORT_TYPES:
            return name.name
        for rec in facts.get("mac", []):
            if rec["mac"] == arp["mac"]:
                return rec["port"]
        return None

    def l2_node(self, chassis, name=None, collected=False):
        node_id = self.device_node(chassis)
        node = self.l2_nodes.setdefault(node_id, {"node-id": node_id, NT + "termination-point": []})
        if name and (collected or L2T + "l2-node-attributes" not in node):
            node[L2T + "l2-node-attributes"] = {"name": name}
        return node

    def l2_port(self, chassis, ifname):
        node = self.l2_node(chassis)
        tp_id = self.port_tp(ifname)
        tps = node[NT + "termination-point"]
        if not any(tp["tp-id"] == tp_id for tp in tps):
            attrs = {"interface-name": ifname}
            if normalize_ifname(ifname, self.platform_of(chassis)).kind in L2_PORT_TYPES:
                attrs["encapsulation-type"] = L2T + "ethernet"
            tps.append({"tp-id": tp_id, L2T + "l2-termination-point-attributes": attrs})

    def platform_of(self, chassis):
        key = self.by_chassis.get(chassis)
        return self.devices[key].get("platform", "") if key else ""

    # assembly ------------------------------------------------------------------
    def build(self):
        for key, facts in self.devices.items():
            me = self.chassis[key]
            self.l2_node(me, (facts.get("lldp_local") or {}).get("hostname"), collected=True)
            for rec in facts.get("interfaces", []):
                if rec.get("type") in L2_PORT_TYPES:
                    self.l2_port(me, rec["ifname"])
        segments = []
        for a, b, why in self.observations():
            for member in (a, b):
                if member[0] == "port":
                    self.l2_port(member[1], member[2])
            merged = ({a, b}, {why})
            for seg in [s for s in segments if a in s[0] or b in s[0]]:
                segments.remove(seg)
                merged[0].update(seg[0])
                merged[1].update(seg[1])
            segments.append(merged)
        for mac in sorted({m[1] for s in segments for m in s[0] if m[0] == "nic"}):
            self.l2_nodes[self.host_node(mac)] = {
                "node-id": self.host_node(mac),
                NT + "termination-point": [
                    {
                        "tp-id": self.nic_tp(mac),
                        L2T + "l2-termination-point-attributes": {
                            "mac-address": mac,
                            "encapsulation-type": L2T + "ethernet",
                        },
                    }
                ],
            }
        evidence, l2_links, link_of = {}, [], {}
        for members, why in sorted(segments, key=lambda s: sorted(map(str, s[0]))):
            refs = sorted(self.ref(m) for m in members)
            link_id = self.base + "l2-link:" + hashlib.sha256(repr(refs).encode()).hexdigest()[:12]
            l2_links.append(self.link(self.l2_net, link_id, refs))
            evidence[link_id] = sorted(why)
            for m in members:
                link_of[m] = link_id
        l3_nodes, l3_links = self.layer3(link_of, evidence)
        l2_nodes = dict(sorted(self.l2_nodes.items()))
        for net, nodes, links in (
            (self.l2_net, l2_nodes, l2_links),
            (self.l3_net, l3_nodes, l3_links),
        ):
            self.navigation(net, nodes, links)
        return {
            "ietf-network:networks": {
                "network": [
                    self.network(self.l2_net, L2T + "l2-topology", l2_nodes, l2_links),
                    self.network(
                        self.l3_net,
                        L3T + "l3-unicast-topology",
                        l3_nodes,
                        l3_links,
                        {"supporting-network": [{"network-ref": self.l2_net}]},
                    ),
                ]
            }
        }, evidence

    def layer3(self, link_of, evidence):
        nodes, subnets = {}, {}
        for key, facts in sorted(self.devices.items()):
            me = self.chassis[key]
            platform = facts.get("platform", "")
            tps, prefixes = [], []
            for rec in sorted(facts.get("ip", []), key=lambda r: r["ifname"]):
                vrf = rec.get("vrf", "default")
                address = ipaddress.ip_interface(f"{rec['address']}/{rec['prefix_len']}")
                kind = normalize_ifname(rec["ifname"], platform).kind
                carriers = set()
                if kind in L2_PORT_TYPES:
                    carriers.add(("port", me, rec["ifname"]))
                else:
                    # A VLAN interface is carried by the ports where its subnet's hosts attach.
                    for arp in facts.get("arp", []):
                        if arp.get("vrf", "default") == vrf and (
                            ipaddress.ip_address(arp["ip"]) in address.network
                        ):
                            port = self.attachment(facts, arp)
                            if port and ("port", me, port) in link_of:
                                carriers.add(("port", me, port))
                tp = {
                    "tp-id": self.port_tp(rec["ifname"]),
                    ST + "name": rec["ifname"],
                    L3T + "l3-termination-point-attributes": {"ip-address": [str(address.ip)]},
                }
                if carriers:
                    tp["supporting-termination-point"] = [
                        {"network-ref": self.l2_net, "node-ref": n, "tp-ref": t}
                        for n, t in sorted(self.ref(c) for c in carriers)
                    ]
                tps.append(tp)
                prefixes.append({"prefix": str(address.network)})
                if address.network.prefixlen < address.max_prefixlen:
                    subnets.setdefault((vrf, address.network), []).append(
                        ((self.device_node(me), self.port_tp(rec["ifname"])), carriers)
                    )
            node_id = self.device_node(me)
            nodes[node_id] = {
                "node-id": node_id,
                "supporting-node": [{"network-ref": self.l2_net, "node-ref": node_id}],
                L3T + "l3-node-attributes": {"prefix": prefixes},
                NT + "termination-point": tps,
            }
        for mac, addresses in sorted(self.host_ips.items()):
            nic = ("nic", mac)
            if nic not in link_of:
                continue  # no L2 attachment evidence for this host
            node_id = self.host_node(mac)
            tps = []
            for vrf, ip in sorted(addresses):
                tp_id = self.base + "ip:" + ip
                tps.append(
                    {
                        "tp-id": tp_id,
                        L3T + "l3-termination-point-attributes": {"ip-address": [ip]},
                        "supporting-termination-point": [
                            {
                                "network-ref": self.l2_net,
                                "node-ref": node_id,
                                "tp-ref": self.nic_tp(mac),
                            }
                        ],
                    }
                )
                for (net_vrf, network), members in subnets.items():
                    if net_vrf == vrf and ipaddress.ip_address(ip) in network:
                        members.append(((node_id, tp_id), {nic}))
            nodes[node_id] = {
                "node-id": node_id,
                "supporting-node": [{"network-ref": self.l2_net, "node-ref": node_id}],
                NT + "termination-point": tps,
            }
        links = []
        for (vrf, network), members in sorted(subnets.items(), key=lambda s: str(s[0])):
            refs = sorted({ref for ref, _ in members})
            if len(refs) < 2:
                continue
            scope = "" if vrf == "default" else _uri(vrf) + ":"
            link_id = self.base + "l3-link:" + scope + str(network)
            link = self.link(self.l3_net, link_id, refs)
            supporting = sorted({link_of[c] for _, cs in members for c in cs if c in link_of})
            if supporting:
                link["supporting-link"] = [
                    {"network-ref": self.l2_net, "link-ref": ref} for ref in supporting
                ]
            links.append(link)
            evidence[link_id] = [f"subnet {vrf} {network}"]
        return dict(sorted(nodes.items())), links

    @staticmethod
    def link(network, link_id, refs):
        return {
            "link-id": link_id,
            ST + "link-direction": ST + "link-direction-bi",
            ST + "link-type": ST + ("link-type-p2p" if len(refs) == 2 else "link-type-mp2mp"),
            ST + "tp": [
                {
                    "network-ref": network,
                    "node-ref": node,
                    "tp-ref": tp,
                    "tp-direction": ST + "tp-direction-symmetric",
                }
                for node, tp in refs
            ],
        }

    @staticmethod
    def navigation(network, nodes, links):
        """SIMAP read-only navigation: each TP/node lists what its links reach."""
        for link in links:
            ends = [(t["node-ref"], t["tp-ref"]) for t in link[ST + "tp"]]
            for node_ref, tp_ref in ends:
                node = nodes[node_ref]
                tp = next(t for t in node[NT + "termination-point"] if t["tp-id"] == tp_ref)
                for other_node, other_tp in ends:
                    if (other_node, other_tp) == (node_ref, tp_ref):
                        continue
                    tp.setdefault(ST + "linked-termination-point", []).append(
                        {"network-ref": network, "node-ref": other_node, "tp-ref": other_tp}
                    )
                    linked = node.setdefault(ST + "linked-node", [])
                    entry = {"network-ref": network, "node-ref": other_node}
                    if other_node != node_ref and entry not in linked:
                        linked.append(entry)

    @staticmethod
    def network(network_id, network_type, nodes, links, extra=None):
        return {
            "network-id": network_id,
            "network-types": {network_type: {}},
            ST + "network-category": ST + "network-category-live",
            **(extra or {}),
            "node": list(nodes.values()),
            NT + "link": links,
        }


def device_node_ids(devices, base):
    """{target_key: node-id} for collected devices, using the builder's identity rule."""
    topology = _Topology(devices, base)
    return {key: topology.device_node(chassis) for key, chassis in topology.chassis.items()}


def host_node_prefix(base):
    return base + "host:"


def build_topology(devices, base):
    """devices: {target_key: {"platform", "lldp_local", "lldp", "interfaces", "ip", "arp", "mac"}}.

    Returns (RFC 7951 ietf-network tree with an L2 and an L3 network, {link-id: evidence}).
    """
    return _Topology(devices, base).build()
