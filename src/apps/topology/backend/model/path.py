"""Forwarding path between two IPs over one snapshot's observations.

Deterministic and evidence-bound: every routed hop names the device, the route it
matched in the device's own table (RFC 8349 RIB facts) and the observation behind it.
Where the data ends, a branch ends with a reason instead of a guess:

- ``delivered``: a device-local destination or a directly connected ARP-resolved endpoint;
- ``endpoint_unresolved``: a connected prefix without evidence for the endpoint;
- ``blackhole``: the matched route points at a NULL interface;
- ``no_route``: nothing in the table covers the destination;
- ``unmanaged_next_hop``: the next hop is not an address of any collected device;
- ``unresolved_next_hop``: a next-hop address with no route to it in the device's RIB;
- ``routes_missing``: this device's routing table was not collected;
- ``loop``: the walk came back to a device it already passed;
- ``tunnel_down``: the VXLAN tunnel the route uses is observed down;
- ``tunnel_egress_unresolved`` / ``tunnel_egress_ambiguous``: where a VPN tunnel ends,
  no (or more than one) instance is proven to receive the packet;
- ``other_instance``: delivered, but in another instance than the one named for that end
  (the address is reused by another VPN, e.g. two customers' 10.1.1.0/24);
- ``denied_in`` / ``denied_out``: an ACL applied to the interface the packet enters or
  leaves by drops it (the entry, or "default" for the ACL's action for unmatched packets).

A next hop marked as a tunnel (``overlay.prepare``: MPLS VPN or VXLAN L3 VNI) is routed
in the underlay toward the tunnel endpoint; at the device owning the endpoint the packet
is routed again in the receiving instance. MPLS hops need an LSP for the endpoint in the
device's label table, VXLAN ingress needs the tunnel to that VTEP observed up.

The route table is not the whole decision where a hop's interface is bound to a policy
route or to NAT. The path is still the route-table path, but such a hop carries a gap
(so the direction is partial) naming the device, interface and policy, for someone to
read that policy on the device; its content is not evaluated here:

- ``policy_may_redirect``: a policy route is applied to packets arriving on the hop's
  ingress interface of a device whose policy content was not read (where it was, the
  policy is applied, below);
- ``nat_may_translate``: NAT is bound to the hop's ingress or egress interface of a device
  whose NAT rules were not read (where they were, NAT is applied, below).

NAT is applied to the packet from the device's own rules (model/nat.py): destination NAT
on the interface it arrives by, after the inbound ACL and before the lookup; source NAT
on the interface it leaves by, after the lookup and before the outbound ACL. Each hop
lists its ``translations``; a rule that cannot be applied is ``nat_undetermined``, an
address a pool gives is ``nat_address_unknown``. The reply is sent from the translated
destination to the translated source and each translation is undone where it was made
(the device's session), so where the forward direction translated, the reverse one is
derived from it rather than traced on its own.

A policy route is applied after the inbound ACL and before NAT and the lookup, from the
query table taken from the device's OpenConfig policy-forwarding tree (device_pbr.py,
pbr.py): the first node that matches decides. A permit node with a next hop sends the
packet to it, resolved like a route's next hop (the hop's route has protocol
``policy-forwarding``); a deny node, or no node, leaves it to the routing table. The hop's
``policy_route`` names the policy, node and rule. A policy that cannot decide is
``pbr_undetermined``; a next hop the device cannot resolve falls back to the routing table
(Comware) with ``pbr_next_hop_unresolved``; a local policy (packets the device sends
itself) has no place in the tree: ``local_policy_not_computed``.

A source outside the collected network (no collected device or subnet has it) comes in
where the devices would send to it through a next hop no collected device owns, the way
back assumed to be the way in; when the destination is on one of those interfaces'
subnets, there. Such a start is ``external_entry_assumed``.

A traced packet is one flow: its addresses and, as far as the caller gives them, the
protocol and ports (an ICMP flow is an echo request; the reverse direction is its reply,
for TCP a segment with ACK set). Each hop checks the IPv4 ACL applied to the interface the
packet enters by and the one it leaves by (model/acl.py), as the ACL query table taken
from the device's RFC 8519 / RFC 9899 tree has them (acl_table.py). Where the entries
cannot decide (an open port, an entry the tree cannot express, an attachment it cannot
hold) the hop carries ``filter_undetermined`` and the walk goes on. An interface in a
security zone carries ``security_policy_not_evaluated``: between zones the device denies
what no security policy permits, which is not evaluated here. Inside an MPLS or VXLAN
tunnel the packet is encapsulated, so the core's ACLs (which see the outer header) are
not checked; a device does not filter what it originates on the way out.

A hop on a device whose ACL, binding or zone tables were not all observed completely lists
them (``not_observed``). Absent evidence is not evidence of a policy either way: when a
table the walk computes (ACL, NAT, policy routing) is among them, the hop carries
``policy_not_computed``, so the direction is never ``complete`` on it (an assistant once
read "complete" there as "no ACL"). Security zones stay a note: their policy is not
evaluated even when observed. A direction is ``complete`` when every path is delivered with
no gap, ``blocked`` when every path ends at an ACL deny with no gap, otherwise ``partial``.

A source host starts at the gateway of its subnet (every candidate when several could
be its gateway); a source that is a device starts at that device. Equal-cost next hops
branch, up to ``MAX_PATHS``. The reverse direction is traced the same way, and different
device sequences are reported as asymmetric.
"""

from __future__ import annotations

import ipaddress

from .acl import ICMP, TCP, UDP, evaluate
from .acl_table import acl_tables
from .acl_table import entries as acl_entries
from .builder import NT, ST
from .device_pbr import project_device_pbr
from .device_state import project_interfaces, read_interface_state
from .locate import _Locator, answer, locate
from .nat import destination as nat_destination
from .nat import source as nat_source
from .overlay import egress_instances, prepare
from .pbr import evaluate as pbr_evaluate
from .pbr import query_table as pbr_table
from .retained_links import UNAVAILABLE_OPER_STATUSES
from .route_table import route_tables, standard_records, uncovered
from .route_table import routes as tree_routes
from .standard_trees import device_tree

MAX_PATHS = 8
MAX_HOPS = 32
MAX_RECURSION = 4  # next-hop lookups before a next hop counts as unresolved
# Tables a hop checks a packet against; a device without one of them is noted, not a gap.
POLICY_TABLES = (
    "acl_bindings",
    "acl_rules",
    "security_zones",
    "pbr_bindings",
    "nat_bindings",
    "nat_rules",
)
# Of those, the ones the walk computes with: without them the hop is not computed.
COMPUTED_TABLES = ("acl_bindings", "acl_rules", "pbr_bindings", "nat_bindings", "nat_rules")
PROTOCOLS = {"tcp": TCP, "udp": UDP, "icmp": ICMP}
ECHO_REQUEST, ECHO_REPLY = (8, 0), (0, 0)
# The host's own gateway setting is not observed; the VRRP master is the inferred gateway.
VRRP_FIRST_HOP = (
    "终端自己配置的网关看不到；该网段有 VRRP，按采集时的主设备（state=master）作为第一跳"
)


def _unicast(value):
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError:
        raise ValueError("请输入源和目的 IP 地址") from None
    if (
        address.is_multicast
        or address.is_loopback
        or address.is_unspecified
        or str(address) == "255.255.255.255"
    ):
        raise ValueError("只能计算单播地址之间的路径")
    return str(address)


class _Tracer:
    def __init__(self, devices, base, observed):
        # Routes come from each device's RFC 8349 tree (route_table), not the facts.
        self._routes = {}
        from_tree = {}
        for key, facts in devices.items():
            records = standard_records(facts)
            if isinstance(facts, dict) and (
                isinstance(facts.get("routes"), list) or (records and "route" in records)
            ):
                self._routes[key] = route_tables(facts)
                facts = {**facts, "routes": tree_routes(self._routes[key])}
            from_tree[key] = facts
        devices = prepare(from_tree)
        self.devices = devices
        self._pbr = {}  # device key -> policy-routing query table (None: content not read)
        self._acl = {}  # device key -> ACL query table
        self.loc = _Locator(devices, base, observed)
        self.topo = self.loc.topo
        self.interface_trees = {
            key: (
                device_tree(standard_records(facts), facts.get("platform", ""))
                if standard_records(facts) and "interface" in standard_records(facts)
                else project_interfaces(facts.get("interfaces"))
            )
            for key, facts in devices.items()
        }
        self.observed = observed

    def interface_state(self, key, role, ifname, hop):
        """Check a real ingress/egress without inventing UP for missing facts."""
        if role == "in" and ifname is None:
            return None  # Device-originated traffic has no observed ingress port.
        entry = read_interface_state(self.interface_trees[key], ifname)
        status = entry.get("oper-status", "unknown") if entry else "unknown"
        state = {"interface": ifname, "interface_role": role, "oper_status": status}
        if entry and entry.get("enabled") is False:  # RFC 8343: shut down
            state["admin_status"] = "down"
        for name in ("evidence_id", "observed_at"):
            if name in self.observed.get(key, {}):
                state[name] = self.observed[key][name]
        hop.setdefault("interface_states", {})[role] = state
        if status in UNAVAILABLE_OPER_STATUSES or state.get("admin_status") == "down":
            return state
        if status != "up":
            hop.setdefault("gaps", []).append({"status": "interface_state_unknown", **state})
        return None

    def device(self, key):
        return self.loc.device(self.topo.device_node(self.topo.chassis[key]))

    def lookup(self, key, vrf, dst):
        """Longest prefix, then lowest preference and metric, as the device would pick."""
        address = ipaddress.ip_address(dst)
        best = None
        for route in self.devices[key].get("routes", []):
            if route.get("vrf", "default") != vrf:
                continue
            network = ipaddress.ip_network(route["prefix"])
            if network.version != address.version or address not in network:
                continue
            rank = (-network.prefixlen, route.get("preference") or 0, route.get("metric") or 0)
            if best is None or rank < best[0]:
                best = (rank, route)
        return best and best[1]

    def not_in_tree(self, key, vrf, dst, route):
        """Routes the device's tree could not hold that could win this lookup."""
        table = self._routes.get(key)
        if table is None:
            return []
        prefixlen = ipaddress.ip_network(route["prefix"]).prefixlen if route else None
        return uncovered(table, vrf, dst, prefixlen)

    def connected(self, key, vrf, ip):
        """All local interfaces covering the address; overlap never picks the first row."""
        address = ipaddress.ip_address(ip)
        interfaces = set()
        for rec in self.devices[key].get("ip", []):
            network = ipaddress.ip_interface(f"{rec['address']}/{rec['prefix_len']}").network
            if rec.get("vrf", "default") == vrf and network.prefixlen < network.max_prefixlen:
                if address in network:
                    interfaces.add(rec["ifname"])
        return sorted(interfaces)

    def resolve(self, key, vrf, ip, depth=0, egress=None):
        """Directly connected next hops behind a next-hop address: [(ip, out, chain)].

        RFC 8349 lets a next hop be an address without an outgoing interface (BGP
        next hops are often a peer's loopback). An address on one of the device's own
        subnets is the neighbour itself; any other is looked up again in the same
        RIB until it is, and each lookup is kept as evidence (chain).
        """
        interfaces = [
            out for out in self.connected(key, vrf, ip) if egress is None or out == egress
        ]
        if interfaces:
            return [(ip, out, []) for out in interfaces]
        route = self.lookup(key, vrf, ip) if depth < MAX_RECURSION else None
        if route is None:
            return []
        resolved = []
        for hop in route["next_hops"]:
            hop_ip, hop_out = hop.get("ip"), hop.get("ifname")
            link = {
                "for": ip,
                "prefix": route["prefix"],
                "protocol": route.get("protocol"),
                "next_hop": hop_ip,
                "out_interface": hop_out,
            }
            local = self.topo.ip_candidates(hop_ip, device=key, vrf=vrf) if hop_ip else ()
            if hop_out and (not hop_ip or hop_ip in {"0.0.0.0", "::"} or local):
                resolved.append((ip, hop_out, [link]))  # the address is on that interface
            elif hop_ip:
                for found in self.resolve(key, vrf, hop_ip, depth + 1, hop_out):
                    resolved.append((found[0], found[1], [link, *found[2]]))
        return resolved

    def redundancy(self, gateways):
        """VRRP members on the subnet's router interfaces holding a virtual address in it."""
        members = []
        for gateway in gateways:
            subnet = ipaddress.ip_network(gateway["subnet"])
            for key, rec in self.loc.vrrp():
                if (key, rec["ifname"]) != (gateway["address"], gateway["interface"]):
                    continue
                if any(ipaddress.ip_address(a) in subnet for a in rec["virtual_addresses"]):
                    members.append(
                        {
                            "name": gateway["name"],
                            "address": gateway["address"],
                            "interface": rec["ifname"],
                            "vrid": rec["vrid"],
                            "state": rec["state"],
                            "priority": rec.get("priority"),
                            "virtual_addresses": rec["virtual_addresses"],
                        }
                    )
        return members

    def link_between(self, a_key, a_if, b_key, b_if):
        """The L2 link joining the two routed ports, when LLDP observed one."""
        a = (self.topo.device_node(self.topo.chassis[a_key]), self.topo.port_tp(a_if))
        b = (self.topo.device_node(self.topo.chassis[b_key]), self.topo.port_tp(b_if))
        for link in self.loc.l2.get(NT + "link", []):
            ends = {(t["node-ref"], t["tp-ref"]) for t in link[ST + "tp"]}
            if a in ends and b in ends:
                return link["link-id"]
        return None

    def endpoint_evidence(self, key, vrf, ifname, ip):
        """Only a neighbour observation in this device's NI and interface closes a host hop."""
        facts = self.devices[key]
        vlan = self.topo.interface_vlan(facts, ifname)
        evidence = []
        for row in facts.get("arp", []):
            if row.get("vrf", "default") != vrf or row["ip"] != ip:
                continue
            if row["ifname"] != ifname:
                # Comware may report the physical learning port and explicit VID
                # instead of the SVI. Require both that VID and the local SVI scope;
                # prefix membership by itself is not a neighbour observation.
                if vlan is None or str(row.get("vlan")) != str(vlan):
                    continue
                if not any(
                    rec["ifname"] == ifname
                    and rec.get("vrf", "default") == vrf
                    and ipaddress.ip_address(ip)
                    in ipaddress.ip_interface(f"{rec['address']}/{rec['prefix_len']}").network
                    for rec in facts.get("ip", [])
                ):
                    continue
            evidence.append(
                {
                    "ip": ip,
                    "mac": row["mac"],
                    "interface": ifname,
                    "observed_interface": row["ifname"],
                    "vrf": vrf,
                }
            )
        return evidence

    def lsp_gap(self, key, endpoint):
        """MPLS forwarding toward a PE needs a labelled entry for its /32 in this device."""
        lfib = self.devices[key].get("mpls_lfib")
        if lfib is None:
            return {"status": "lfib_not_collected", "fec": endpoint}
        if any(
            row.get("fec") == f"{endpoint}/32" and row.get("out_label") not in {None, "untagged"}
            for row in lfib
        ):
            return None
        return {"status": "lsp_not_observed", "fec": endpoint}

    def policy_gaps(self, key, ifname, direction):
        """What may move a packet off the route-table path at this interface: a policy
        route applied to arriving packets (ingress only), NAT in either direction."""
        facts = self.devices[key]
        if not ifname:
            return []
        gaps = []
        if direction == "in" and "pbr_rules" not in facts:
            gaps += [
                {"status": "policy_may_redirect", "interface": ifname, "policy": row["policy"]}
                for row in facts.get("pbr_bindings") or []
                if row.get("ifname") == ifname
            ]
        if "nat_rules" in facts:
            return gaps  # NAT is applied to the packet itself (translate)
        gaps += [
            {
                "status": "nat_may_translate",
                "interface": ifname,
                "kind": row["kind"],
                **({"rule": row["rule"]} if row.get("rule") else {}),
            }
            for row in facts.get("nat_bindings") or []
            if row.get("ifname") == ifname
        ]
        return gaps

    def filters(self, key, ifname, direction, packet, hop):
        """Check the packet against the ACL applied to this direction of the interface.

        Returns the deny (end status details) when the ACL drops it; an undecided ACL, a
        binding that cannot be evaluated and a security zone are gaps of the hop.
        """
        facts = self.devices[key]
        if not ifname or "src" not in packet:
            return None
        zones = sorted(
            {row["zone"] for row in facts.get("security_zones") or [] if row["ifname"] == ifname}
        )
        if zones:
            hop.setdefault("gaps", []).append(
                {"status": "security_policy_not_evaluated", "interface": ifname, "zones": zones}
            )
        for row in self.acl_rows(key):
            if row["interface-id"] != ifname or row["direction"] != direction:
                continue
            check = {"interface": ifname, "direction": direction, "acl": row["acl"]}
            if not row["complete"]:
                # The attachment is not in the standard tree: a common ACL, a binding the
                # device reports as not in effect, entries not read, an instance condition.
                outcome = {"result": "undetermined", "rules": [], "needs": row["reasons"]}
            elif not row["entries"]:
                outcome = {"result": "undetermined", "rules": [], "needs": ["acl_entries"]}
            else:
                outcome = evaluate(
                    acl_entries(row), {**packet, "vrf": hop["vrf"]}, row["default-action"]
                )
            hop.setdefault("filters", []).append({**check, **outcome})
            if outcome["result"] == "deny":
                # What the refusing entry matches in the standard tree (and its hit count
                # when collected), so a reader can tell which traffic it stops.
                entry = next(
                    (e for e in row["entries"] if str(outcome["rule"]) == e["name"]), None
                )
                return {
                    **check,
                    "rule": outcome["rule"],
                    **({"entry": {k: v for k, v in entry.items() if k != "name"}} if entry else {}),
                }
            if outcome["result"] == "undetermined":
                hop.setdefault("gaps", []).append(
                    {
                        "status": "filter_undetermined",
                        **check,
                        "rules": outcome["rules"],
                        "needs": outcome["needs"],
                    }
                )
        return None

    def acl_rows(self, key):
        """The device's ACL query table, from its RFC 8519 / RFC 9899 tree (acl_table)."""
        if key not in self._acl:
            self._acl[key] = acl_tables(self.devices[key])
        return self._acl[key]

    def pbr_rows(self, key):
        """The device's policy-routing query table, from its standard tree (device_pbr)."""
        if key not in self._pbr:
            facts = self.devices[key]
            self._pbr[key] = (
                pbr_table(project_device_pbr({"facts": facts})) if "pbr_rules" in facts else None
            )
        return self._pbr[key]

    def policy_route(self, key, vrf, in_if, packet, hop):
        """The policy applied to packets arriving on in_if: the next hop it sends this one
        to, or None for the routing table. The hop says which policy and node decided; a
        next hop the device cannot resolve leaves it to the routing table (Comware), a gap."""
        rows = self.pbr_rows(key)
        if rows is None or not in_if:
            return None
        row = next((r for r in rows if r["interface-id"] == in_if), None)
        if row is None:
            return None
        outcome = pbr_evaluate(row, packet)
        where = {"interface": in_if, "policy": row["policy-id"]}
        if outcome["result"] == "undetermined":
            hop.setdefault("gaps", []).append({"status": "pbr_undetermined", **where, **outcome})
            return None
        hop["policy_route"] = {**where, **{k: v for k, v in outcome.items() if k != "undecided_before"}}
        if outcome["result"] != "next-hop":
            return None
        next_hop = outcome["next-hop"]
        if not self.resolve(key, vrf, next_hop):
            hop.setdefault("gaps", []).append(
                {
                    "status": "pbr_next_hop_unresolved",
                    **where,
                    "node": outcome["node"],
                    "next_hop": next_hop,
                }
            )
            return None
        return next_hop

    def translate(self, key, in_if, out_if, packet, hop, sessions):
        """NAT of a packet arriving (no out_if) or leaving by out_if: the session of the
        traced flow first (undoing a translation the forward direction made there), else
        the device's rules. Returns the packet as it goes on."""
        stage, ifname = ("in", in_if) if out_if is None else ("out", out_if)
        for session in sessions:
            if (session["key"], session["interface"], session["stage"]) != (key, ifname, stage):
                continue
            if any(
                value is not None and packet.get(field) != value
                for field, value in session["match"].items()
            ):
                continue
            undone = {
                "nat": "session",
                "interface": ifname,
                "before": {field: packet.get(field) for field in session["set"]},
                "after": dict(session["set"]),
            }
            hop["translations"] = [*hop.get("translations", []), undone]
            return {**packet, **session["set"]}
        facts = self.devices[key]
        if "nat_rules" not in facts:
            return packet
        if stage == "in":
            outcome = nat_destination(facts, ifname, packet)
        else:
            outcome = nat_source(facts, in_if, ifname, packet, hop["vrf"])
        if outcome is None:
            return packet
        if "undetermined" in outcome:
            gap = {"status": "nat_undetermined", "interface": ifname, **outcome["undetermined"]}
            hop["gaps"] = [*hop.get("gaps", []), gap]
            return packet
        hop["translations"] = [*hop.get("translations", []), outcome["translation"]]
        if outcome["packet"].get("src") is None:
            gap = {
                "status": "nat_address_unknown",
                "interface": ifname,
                "pool": outcome["translation"].get("pool"),
            }
            hop["gaps"] = [*hop.get("gaps", []), gap]
        return outcome["packet"]

    def entries(self, src, dst):
        """Where a packet from an address outside the collected network comes in: the
        interfaces by which the devices send to it through a next hop no collected device
        owns. When the destination is on one of those interfaces' subnets, only there. A
        route to a NULL interface discards what is sent to the address: no way in there."""
        found = set()
        for key in sorted(self.devices):
            route = self.lookup(key, "default", src) if "routes" in self.devices[key] else None
            for next_hop in (route or {}).get("next_hops") or []:
                ip = next_hop.get("ip")
                if not ip or next_hop.get("tunnel") or self.topo.ip_candidates(ip):
                    continue
                if str(next_hop.get("ifname") or "").upper().startswith("NULL"):
                    continue
                resolved = self.resolve(key, "default", ip, egress=next_hop.get("ifname"))
                if not resolved and next_hop.get("ifname"):
                    resolved = [(ip, next_hop["ifname"], [])]
                found |= {(key, out, ip) for _, out, _ in resolved}
        on_subnet = {entry for entry in found if entry[1] in self.connected(entry[0], "default", dst)}
        return sorted(on_subnet or found)

    def vxlan_state(self, key, tunnel):
        """The observed state of the tunnel to a VTEP: IOS NVE peers, Comware tunnels."""
        facts = self.devices[key]
        states = {
            row.get("state")
            for row in facts.get("evpn_peers") or []
            if row.get("peer") == tunnel["endpoint"] and row.get("vni") == tunnel["vni"]
        } | {
            row.get("state")
            for row in facts.get("tunnel_interfaces") or []
            if row.get("destination") == tunnel["endpoint"] and row.get("mode") == "vxlan"
        }
        if not states:
            return None
        return "up" if states == {"up"} else "down"

    def walk(
        self,
        start,
        first_in,
        dst,
        start_vrf="default",
        initial_gaps=(),
        source_interface=None,
        packet=None,
        sessions=(),
    ):
        paths, truncated = [], False
        packet = packet or {"dst": dst}

        def end(trail, status, **extra):
            nonlocal truncated
            if len(paths) >= MAX_PATHS:
                truncated = True
                return
            paths.append({"hops": trail, "end": {"status": status, **extra}})

        def deliver_local(key, vrf, trail, address):
            own = self.topo.ip_candidates(address, device=key, vrf=vrf)
            if len(own) != 1:
                return False
            unavailable = self.interface_state(key, "destination", own[0][1], trail[-1])
            if unavailable:
                end(trail, "interface_unavailable", **unavailable)
            else:
                end(trail, "delivered", to="device", interface=own[0][1])
            return True

        def step(key, vrf, in_if, trail, visited, tunnel=None, packet=packet):
            nonlocal truncated
            if len(paths) >= MAX_PATHS:
                truncated = True
                return
            # A hop that ends the branch before a lookup keeps the same shape, with no route.
            hop = {
                "device": self.device(key),
                "vrf": vrf,
                "in_interface": in_if,
                "out_interface": None,
                "route": None,
            }
            if tunnel is not None:
                hop["tunnel"] = {k: v for k, v in tunnel.items() if k != "import"}
            if not trail and initial_gaps:
                hop["gaps"] = list(initial_gaps)
            if not trail and source_interface:
                unavailable = self.interface_state(key, "source", source_interface, hop)
                if unavailable:
                    end([hop], "interface_unavailable", **unavailable)
                    return
            unavailable = self.interface_state(key, "in", in_if, hop)
            if unavailable:
                end([*trail, hop], "interface_unavailable", **unavailable)
                return
            if missing := [name for name in POLICY_TABLES if name not in self.devices[key]]:
                hop["not_observed"] = missing
                if uncomputed := [name for name in missing if name in COMPUTED_TABLES]:
                    hop.setdefault("gaps", []).append(
                        {"status": "policy_not_computed", "tables": uncomputed}
                    )
            redirect = None
            if tunnel is not None:
                hop["encapsulated"] = True  # arrived inside a tunnel: the outer header
            elif denied := self.filters(key, in_if, "in", packet, hop):
                end([*trail, hop], "denied_in", **denied)
                return
            elif in_if is not None:
                # Policy routing after the inbound ACL, before NAT and the lookup.
                redirect = self.policy_route(key, vrf, in_if, packet, hop)
                # Destination NAT, or the way back of the flow's source NAT, before the lookup.
                packet = self.translate(key, in_if, None, packet, hop, sessions)
            dst = packet["dst"]
            # What it originates is neither filtered nor translated on its way out.
            originated = not trail and in_if is None
            if originated and "pbr_rules" in self.devices[key]:
                # A local policy applies to what the device sends itself; OpenConfig has no
                # place for it (device_pbr), so it is not computed.
                hop.setdefault("gaps", []).extend(
                    {"status": "local_policy_not_computed", "policy": row["policy"]}
                    for row in self.devices[key].get("pbr_bindings") or []
                    if row.get("local")
                )
            if ingress := self.policy_gaps(key, in_if, "in"):
                hop.setdefault("gaps", []).extend(ingress)
            if tunnel is not None and self.topo.ip_candidates(
                tunnel["endpoint"], device=key, vrf=vrf
            ):
                # The tunnel ends here: route the packet again in the receiving instance.
                egress = egress_instances(self.devices[key], tunnel, dst)
                hop["tunnel"]["ends"] = True
                if len(egress) != 1:
                    status = "tunnel_egress_ambiguous" if egress else "tunnel_egress_unresolved"
                    end([*trail, hop], status, endpoint=tunnel["endpoint"], candidates=egress)
                    return
                vrf = hop["vrf"] = egress[0]
                tunnel = None
            if tunnel is None and deliver_local(key, vrf, [*trail, hop], dst):
                return
            target = tunnel["endpoint"] if tunnel else dst
            if (key, vrf, target) in visited or len(trail) >= MAX_HOPS:
                end([*trail, hop], "loop")
                return
            if "routes" not in self.devices[key]:
                end([*trail, hop], "routes_missing")
                return
            if (
                tunnel is not None
                and tunnel["type"] == "mpls"
                and (gap := self.lsp_gap(key, target))
            ):
                hop.setdefault("gaps", []).append(gap)
            route = self.lookup(key, vrf, target)
            hidden = self.not_in_tree(key, vrf, target, route)
            if hidden and route is not None:  # without a route the end names them
                hop.setdefault("gaps", []).append(
                    {"status": "route_not_in_tree", "prefixes": hidden}
                )
            if redirect is not None:
                # Forwarded to the policy's next hop, bypassing the lookup; the next hop is
                # resolved like a route's.
                route = {"protocol": "policy-forwarding", "next_hops": [{"ip": redirect}]}
            if route is None:
                if hidden:
                    # A route the device has but its tree could not hold covers dst.
                    end([*trail, hop], "route_not_in_tree", prefixes=hidden)
                else:
                    end([*trail, hop], "no_route")
                return
            matched = {k: route.get(k) for k in ("prefix", "protocol", "preference", "metric")}
            special = route.get("special_next_hop")
            if special == "blackhole":
                hop["route"] = {**matched, "next_hop": None, "special_next_hop": special}
                end([*trail, hop], "blackhole")
                return
            if special == "receive":
                hop["route"] = {**matched, "next_hop": None, "special_next_hop": special}
                if not deliver_local(key, vrf, [*trail, hop], dst):
                    end([*trail, hop], "endpoint_unresolved", destination=dst)
                return
            if not route.get("next_hops"):
                hop["route"] = {**matched, "next_hop": None}
                end([*trail, hop], "unresolved_next_hop", reason="route_next_hops_missing")
                return
            for next_hop in route["next_hops"]:
                ip, out = next_hop.get("ip"), next_hop.get("ifname") or ""
                next_vrf = next_hop.get("vrf") or vrf
                # A leaked route resolves its next hop in another local NI; say which.
                leak = {"next_hop_vrf": next_vrf} if next_vrf != vrf else {}
                entering = next_hop.get("tunnel")
                carried = tunnel or entering
                if entering:
                    leak["tunnel"] = {k: v for k, v in entering.items() if k != "import"}
                    out = ""  # the overlay interface is not the underlay egress
                current = {
                    **hop,
                    "out_interface": out,
                    "route": {**matched, "next_hop": ip, **leak},
                    **({"gaps": list(hop["gaps"])} if "gaps" in hop else {}),
                    **(
                        {"interface_states": dict(hop["interface_states"])}
                        if "interface_states" in hop
                        else {}
                    ),
                }
                here = [*trail, current]
                if entering and entering["type"] == "vxlan":
                    state = self.vxlan_state(key, entering)
                    if state == "down":
                        end(here, "tunnel_down", endpoint=entering["endpoint"])
                        continue
                    if state is None:
                        current.setdefault("gaps", []).append(
                            {"status": "vxlan_tunnel_not_observed", "endpoint": ip}
                        )
                if entering and entering["type"] == "mpls" and (gap := self.lsp_gap(key, ip)):
                    current.setdefault("gaps", []).append(gap)
                own = self.topo.ip_candidates(ip, device=key, vrf=next_vrf) if ip else ()
                if carried and (not ip or ip in {"0.0.0.0", "::"} or own):
                    # Inside a tunnel a connected route reaches the endpoint device itself.
                    ip = target
                    own = ()
                if out.upper().startswith("NULL"):
                    end(here, "blackhole")
                    continue
                if out.lower().startswith("inloop"):
                    if not deliver_local(key, next_vrf, here, dst):
                        end(here, "endpoint_unresolved", interface=out, destination=dst)
                    continue
                connected = not ip or ip in {"0.0.0.0", "::"} or own
                if connected and any(
                    candidate[0] != key
                    for candidate in self.topo.neighbour_candidates(key, next_vrf, dst, out)
                ):
                    # The destination is the address of a collected device proven on this
                    # subnet (ARP or LLDP): the packet goes on into that device (its ACL,
                    # NAT and local delivery).
                    ip, connected = dst, False
                    resolved = [(dst, out, [])]
                elif not connected:
                    resolved = None
                if connected:
                    # A connected route: the device's own address on the egress interface.
                    unavailable = self.interface_state(key, "out", out, current)
                    if unavailable:
                        end(here, "interface_unavailable", **unavailable)
                        continue
                    if egress := self.policy_gaps(key, out, "out"):
                        current.setdefault("gaps", []).extend(egress)
                    leaving = (
                        packet
                        if originated
                        else self.translate(key, in_if, out, packet, current, sessions)
                    )
                    if not originated and (
                        denied := self.filters(key, out, "out", leaving, current)
                    ):
                        end(here, "denied_out", **denied)
                        continue
                    evidence = self.endpoint_evidence(key, next_vrf, out, dst)
                    if not evidence or len({row["mac"] for row in evidence}) != 1:
                        end(here, "endpoint_unresolved", interface=out, destination=dst)
                        continue
                    current["endpoint_evidence"] = evidence
                    end(here, "delivered", to="subnet", interface=out)
                    continue
                if resolved is None:
                    resolved = self.resolve(key, next_vrf, ip, egress=out or None)
                if not resolved and out:
                    # Address and outgoing interface both given (RFC 8349 simple next hop)
                    # and no route to the address: it is reached on that interface.
                    resolved = [(ip, out, [])]
                if not resolved:
                    end(here, "unresolved_next_hop", next_hop=ip)
                base = current
                for neighbour_ip, neighbour_out, chain in resolved:
                    current = {
                        **base,
                        "out_interface": neighbour_out,
                        **({"gaps": list(base["gaps"])} if "gaps" in base else {}),
                        **(
                            {"interface_states": dict(base["interface_states"])}
                            if "interface_states" in base
                            else {}
                        ),
                    }
                    if chain:
                        current["resolved_via"] = chain
                    if egress := self.policy_gaps(key, neighbour_out, "out"):
                        current.setdefault("gaps", []).extend(egress)
                    if carried:
                        current["encapsulated"] = True  # leaves inside the tunnel
                    here = [*trail, current]
                    if neighbour_out.upper().startswith("NULL"):
                        end(here, "blackhole")
                        continue
                    if neighbour_out.lower().startswith("inloop"):
                        if not deliver_local(key, next_vrf, here, dst):
                            end(here, "unresolved_next_hop", next_hop=neighbour_ip)
                        continue
                    unavailable = self.interface_state(key, "out", neighbour_out, current)
                    if unavailable:
                        end(here, "interface_unavailable", **unavailable)
                        continue
                    leaving = (
                        packet
                        if carried or originated
                        else self.translate(key, in_if, neighbour_out, packet, current, sessions)
                    )
                    if not carried and not originated and (
                        denied := self.filters(key, neighbour_out, "out", leaving, current)
                    ):
                        end(here, "denied_out", **denied)
                        continue
                    proven = self.topo.neighbour_candidates(
                        key, next_vrf, neighbour_ip, neighbour_out
                    )
                    neighbours = proven or [
                        candidate
                        for candidate in self.topo.ip_candidates(neighbour_ip)
                        if candidate[0] != key
                    ]
                    if not neighbours:
                        end(here, "unmanaged_next_hop", next_hop=neighbour_ip)
                        continue
                    for neighbour_key, neighbour_if, neighbour_vrf in neighbours:
                        branch = {**current, "gaps": list(current.get("gaps", []))}
                        branch["link_id"] = self.link_between(
                            key, neighbour_out, neighbour_key, neighbour_if
                        )
                        if not proven or len(neighbours) > 1:
                            branch["gaps"].append(
                                {
                                    "status": "adjacency_unproven",
                                    "next_hop": neighbour_ip,
                                    "interface": neighbour_out,
                                    "candidate_count": len(neighbours),
                                }
                            )
                        step(
                            neighbour_key,
                            neighbour_vrf,
                            neighbour_if,
                            [*trail, branch],
                            visited | {(key, vrf, target)},
                            carried,
                            leaving,
                        )

        step(start, start_vrf, first_in, [], frozenset())
        return paths, truncated

    def direction(self, source, dst, vrf=None, dst_vrf=None, packet=None, sessions=()):
        """source is the locate answer for the start address; ``vrf`` keeps only starts in
        that network instance of their first-hop device (device-local name); ``dst_vrf`` is
        the instance the destination is named in; ``packet`` the flow ACLs are checked with."""
        result = {"to": dst, "first_hop": "none", "paths": [], "truncated": False}
        if vrf is not None:
            # The named instance decides which of several same-address ends is meant.
            source = _in_instance(source, vrf)
        masters, external = [], {}
        device_locations = [
            location for location in source["locations"] if location["status"] == "device"
        ]
        if device_locations:
            starts = [
                (location["device"]["address"], None, location["vrf"], location["interface"])
                for location in device_locations
            ]
        else:
            gateways = source["router_interfaces"]
            if any(
                ipaddress.ip_address(dst) in ipaddress.ip_network(g["subnet"]) for g in gateways
            ):
                # A common prefix does not prove the hosts' masks, broadcast domain,
                # or bridge forwarding state. Keep the relation without claiming a path.
                result.update(
                    status="partial",
                    relation="same_subnet",
                    gaps=[{"status": "same_subnet_unproven"}],
                )
                return result
            starts = [(g["address"], g["interface"], g["vrf"], None) for g in gateways]
            # With VRRP on the subnet the gateway is the group's master (RFC 8347 state).
            groups = self.redundancy(gateways)
            masters = sorted(
                {
                    (m["address"], m["interface"], g["vrf"], None)
                    for m in groups
                    if m["state"] == "master"
                    for g in gateways
                    if (g["address"], g["interface"]) == (m["address"], m["interface"])
                }
            )
            if groups:
                result["vrrp"] = groups
            if masters:
                starts = masters
            if not gateways and not source["locations"]:
                # Not in the collected network: it comes in from outside (entries).
                external = {
                    (key, ifname): next_hop
                    for key, ifname, next_hop in self.entries(source["query"], dst)
                }
                starts = [(key, ifname, "default", None) for key, ifname in external]
        if vrf is not None:
            starts = [start for start in starts if start[2] == vrf]
            masters = [start for start in masters if start[2] == vrf]
            result["vrf"] = vrf
        if not starts:
            result["status"] = "no_gateway"
            return result
        if external:
            result["first_hop"] = "external"
        elif len(starts) > 1:
            result["first_hop"] = "ambiguous"
        elif not device_locations and masters:
            result["first_hop"] = "vrrp_master"
            result["note"] = VRRP_FIRST_HOP
        else:
            result["first_hop"] = "single"
        for key, first_in, vrf, source_interface in starts:
            initial_gaps = []
            if source["status"] == "conflict":
                initial_gaps.append({"status": "source_identity_ambiguous"})
            if external:
                initial_gaps.append(
                    {
                        "status": "external_entry_assumed",
                        "interface": first_in,
                        "next_hop": external[(key, first_in)],
                    }
                )
            elif not device_locations and (
                not self.endpoint_evidence(key, vrf, first_in, source["query"])
                or (len(starts) > 1 and not masters)
            ):
                initial_gaps.append(
                    {
                        "status": "source_attachment_unproven",
                        "interface": first_in,
                        "source": source["query"],
                    }
                )
            paths, truncated = self.walk(
                key, first_in, dst, vrf, initial_gaps, source_interface, packet, sessions
            )
            room = MAX_PATHS - len(result["paths"])
            result["paths"].extend(paths[:room])
            result["truncated"] |= truncated or len(paths) > room
        for path in result["paths"]:
            reached = path["hops"][-1].get("vrf") if path["hops"] else None
            if dst_vrf and path["end"]["status"] == "delivered" and reached != dst_vrf:
                path["end"] = {
                    **path["end"],
                    "status": "other_instance",
                    "instance": reached,
                    "expected": dst_vrf,
                }
        ends = {p["end"]["status"] for p in result["paths"]}
        state_gaps = any(hop.get("gaps") for path in result["paths"] for hop in path["hops"])
        settled = bool(result["paths"]) and not result["truncated"] and not state_gaps
        if settled and ends == {"delivered"}:
            result["status"] = "complete"
        elif settled and ends <= {"denied_in", "denied_out"}:
            result["status"] = "blocked"
        else:
            result["status"] = "partial"
        return result


def _routed(direction):
    """Every path delivered, nothing truncated, no gap but an uncomputed policy table: the
    routing is settled (symmetry compares routes, which ACLs and NAT do not change)."""
    return (
        bool(direction["paths"])
        and not direction["truncated"]
        and all(path["end"]["status"] == "delivered" for path in direction["paths"])
        and all(
            gap["status"] == "policy_not_computed"
            for path in direction["paths"]
            for hop in path["hops"]
            for gap in hop.get("gaps", [])
        )
    )


def _in_instance(source, vrf):
    """The locate answer narrowed to the ends in one network instance."""

    def inside(location):
        if location.get("status") == "device":
            return location.get("vrf") == vrf
        rows = location.get("arp") or []
        return not rows or any(row.get("vrf", "default") == vrf for row in rows)

    locations = [location for location in source["locations"] if inside(location)]
    scoped = {
        **source,
        "locations": locations,
        "router_interfaces": [g for g in source["router_interfaces"] if g["vrf"] == vrf],
    }
    if source.get("status") == "conflict":
        scoped["status"] = (
            "not_found"
            if not locations
            else locations[0]["status"]
            if len(locations) == 1
            else "conflict"
        )
    return scoped


def _sequences(direction, reverse=False):
    return {
        tuple(reversed(ids) if reverse else ids)
        for ids in (
            [hop["device"]["node_id"] for hop in path["hops"]] for path in direction["paths"]
        )
    }


def _translations(path):
    return [
        (hop["device"]["address"], step)
        for hop in path["hops"]
        for step in hop.get("translations", [])
        if step["nat"] in {"source", "destination"}
    ]


def _undo(key, step):
    """The session a translation leaves: how the reply is translated back, where."""
    before, after = step["before"], step["after"]
    if step["nat"] == "source":
        match = {"dst": after.get("src"), "dst_port": after.get("src_port")}
        undone = {"dst": before["src"]} if "src" in before else {}
        if "src_port" in before:
            undone["dst_port"] = before["src_port"]
        stage = "in"
    else:
        match = {"src": after.get("dst"), "src_port": after.get("dst_port")}
        undone = {"src": before["dst"]} if "dst" in before else {}
        if "dst_port" in before:
            undone["src_port"] = before["dst_port"]
        stage = "out"
    return {"key": key, "interface": step["interface"], "stage": stage, "match": match, "set": undone}


def _reverse(tracer, forward, destination, src, dst_vrf, src_vrf, request, reply):
    """The reply's paths. Where the forward direction translated, the reply goes from its
    translated destination to its translated source and each translation is undone where
    it was made; otherwise it is the reply between the original addresses."""
    plans = []
    for path in forward["paths"]:
        steps = _translations(path)
        if steps not in plans:
            plans.append(steps)
    if not plans:
        plans = [[]]
    parts = []
    for steps in plans:
        if not steps:
            part = tracer.direction(destination, src, dst_vrf, src_vrf, reply)
            part["from"] = reply["src"]
            parts.append(part)
            continue
        final = dict(request)
        for _, step in steps:
            final.update(step["after"])
        if final["src"] is None or final["dst"] is None:
            parts.append(
                {
                    "to": final["src"],
                    "from": final["dst"],
                    "first_hop": "none",
                    "paths": [],
                    "truncated": False,
                    "status": "partial",
                    "gaps": [{"status": "reverse_not_derived", "reason": "nat_address_unknown"}],
                }
            )
            continue
        back = {
            **reply,
            "src": final["dst"],
            "dst": final["src"],
            "src_port": final.get("dst_port"),
            "dst_port": final.get("src_port"),
        }
        sessions = [_undo(key, step) for key, step in steps]
        part = tracer.direction(
            answer(tracer.loc, "ip", back["src"]), back["dst"], dst_vrf, src_vrf, back, sessions
        )
        part["from"] = back["src"]
        part["translated"] = {"src": back["src"], "dst": back["dst"]}
        parts.append(part)
    if len(parts) == 1:
        return parts[0]
    statuses = {part["status"] for part in parts}
    hops = {part["first_hop"] for part in parts}
    return {
        **parts[0],
        "paths": [path for part in parts for path in part["paths"]][:MAX_PATHS],
        "truncated": any(part["truncated"] for part in parts),
        "status": statuses.pop() if len(statuses) == 1 else "partial",
        "first_hop": hops.pop() if len(hops) == 1 else "ambiguous",
        "gaps": [gap for part in parts for gap in part.get("gaps", [])],
    }


def _flow(protocol, src_port, dst_port):
    """(protocol number or None, source port, destination port) a caller asked for."""
    if protocol in (None, ""):
        if src_port is not None or dst_port is not None:
            raise ValueError("端口只适用于 TCP 或 UDP，请同时指定协议")
        return None, None, None
    name = str(protocol).strip().lower()
    number = PROTOCOLS.get(name, int(name) if name.isdigit() else -1)
    if not 0 <= number <= 255:
        raise ValueError("协议应为 tcp、udp、icmp 或 0-255 的协议号")
    for port in (src_port, dst_port):
        if port is None:
            continue
        if number not in {TCP, UDP}:
            raise ValueError("端口只适用于 TCP 或 UDP")
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("端口应为 1-65535")
    return number, src_port, dst_port


EXTERNAL_NOTE = (
    "该地址不在已采网络里（没有已采设备或网段拥有它）；exits 是已采设备发往它的出口："
    "出接口、不属于已采设备的下一跳和命中的路由。这是它相对本网络的位置，不是它本身在哪；"
    "出口之外的转发看不到"
)


def locate_with_exits(devices, base, query, observed=None):
    """Where an IP or MAC attaches (model/locate.py). An IP no collected device or subnet
    has (an internet address) is placed where the collected devices send to it instead:
    ``external``, with each exit's device, interface, next hop and route."""
    result = locate(devices, base, query, observed)
    if result["kind"] != "ip" or result["status"] != "not_found" or result["router_interfaces"]:
        return result
    tracer = _Tracer(devices, base, observed or {})
    exits = []
    for key, ifname, next_hop in tracer.entries(result["query"], result["query"]):
        route = tracer.lookup(key, "default", result["query"]) or {}
        exits.append(
            {
                "device": tracer.device(key),
                "interface": ifname,
                "next_hop": next_hop,
                "route": {name: route.get(name) for name in ("prefix", "protocol")},
            }
        )
    if exits:
        result.update(status="external", exits=exits, note=EXTERNAL_NOTE)
    return result


def trace(
    devices,
    base,
    src,
    dst,
    observed=None,
    src_vrf=None,
    dst_vrf=None,
    protocol=None,
    src_port=None,
    dst_port=None,
):
    """devices: builder input keyed by collected address; observed: {address: evidence}.

    ``src_vrf``/``dst_vrf`` name the network instance an end is in on its first-hop device,
    for addresses that several VPNs reuse. ``protocol`` (tcp, udp, icmp or a number) and
    the ports describe the flow ACLs are checked with; what is not given is left open.
    """
    src, dst = _unicast(src), _unicast(dst)
    if src == dst:
        raise ValueError("源和目的地址相同")
    number, sport, dport = _flow(protocol, src_port, dst_port)
    flow = {
        name: value
        for name, value in (("protocol", number), ("src_port", sport), ("dst_port", dport))
        if value is not None
    }
    tracer = _Tracer(devices, base, observed or {})
    source, destination = answer(tracer.loc, "ip", src), answer(tracer.loc, "ip", dst)
    request = {"src": src, "dst": dst, "protocol": number, "src_port": sport, "dst_port": dport}
    reply = {"src": dst, "dst": src, "protocol": number, "src_port": dport, "dst_port": sport}
    if number == ICMP:
        request["icmp"], reply["icmp"] = ECHO_REQUEST, ECHO_REPLY
    reply["reply"] = True
    forward = tracer.direction(source, dst, src_vrf, dst_vrf, request)
    forward["from"] = src
    reverse = _reverse(tracer, forward, destination, src, dst_vrf, src_vrf, request, reply)
    # Candidate first hops are not observed choices, so they cannot show asymmetry.
    routed = (
        _routed(forward)
        and _routed(reverse)
        and "ambiguous" not in (forward["first_hop"], reverse["first_hop"])
    )
    gaps = (
        [
            {
                "direction": name,
                "status": path["end"]["status"],
                "device": path["hops"][-1]["device"],
                **{key: value for key, value in path["end"].items() if key != "status"},
            }
            for name, part in (("forward", forward), ("reverse", reverse))
            for path in part["paths"]
            if path["end"]["status"] != "delivered"
        ]
        + [
            {"direction": name, "status": part["status"]}
            for name, part in (("forward", forward), ("reverse", reverse))
            if part["status"] == "no_gateway"
        ]
        + [
            {"direction": name, "device": hop["device"], **gap}
            for name, part in (("forward", forward), ("reverse", reverse))
            for path in part["paths"]
            for hop in path["hops"]
            for gap in hop.get("gaps", [])
        ]
        + [
            {"direction": name, **gap}
            for name, part in (("forward", forward), ("reverse", reverse))
            for gap in part.get("gaps", [])
        ]
    )
    return {
        "src": src,
        "dst": dst,
        "flow": flow,
        "source": source,
        "destination": destination,
        "forward": forward,
        "reverse": reverse,
        "symmetric": _sequences(forward) == _sequences(reverse, reverse=True) if routed else None,
        "gaps": gaps,
    }
