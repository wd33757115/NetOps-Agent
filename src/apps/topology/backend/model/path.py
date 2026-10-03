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
  no (or more than one) instance is proven to receive the packet.

A next hop marked as a tunnel (``overlay.prepare``: MPLS VPN or VXLAN L3 VNI) is routed
in the underlay toward the tunnel endpoint; at the device owning the endpoint the packet
is routed again in the receiving instance. MPLS hops need an LSP for the endpoint in the
device's label table, VXLAN ingress needs the tunnel to that VTEP observed up.

A source host starts at the gateway of its subnet (every candidate when several could
be its gateway); a source that is a device starts at that device. Equal-cost next hops
branch, up to ``MAX_PATHS``. The reverse direction is traced the same way, and different
device sequences are reported as asymmetric.
"""

from __future__ import annotations

import ipaddress

from .builder import NT, ST
from .device_state import project_interfaces, read_interface_state
from .locate import _Locator, answer
from .overlay import egress_instances, prepare
from .retained_links import UNAVAILABLE_OPER_STATUSES

MAX_PATHS = 8
MAX_HOPS = 32
MAX_RECURSION = 4  # next-hop lookups before a next hop counts as unresolved
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
        devices = prepare(devices)
        self.devices = devices
        self.loc = _Locator(devices, base, observed)
        self.topo = self.loc.topo
        self.interface_trees = {
            key: project_interfaces(facts.get("interfaces")) for key, facts in devices.items()
        }
        self.observed = observed

    def interface_state(self, key, role, ifname, hop):
        """Check a real ingress/egress without inventing UP for missing facts."""
        if role == "in" and ifname is None:
            return None  # Device-originated traffic has no observed ingress port.
        entry = read_interface_state(self.interface_trees[key], ifname)
        status = entry.get("oper-status", "unknown") if entry else "unknown"
        state = {"interface": ifname, "interface_role": role, "oper_status": status}
        rows = self.devices[key].get("interfaces")
        matches = (
            [row for row in rows if isinstance(row, dict) and row.get("ifname") == ifname]
            if isinstance(rows, list)
            else []
        )
        if len(matches) == 1 and matches[0].get("admin_status") == "down":
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
        self, start, first_in, dst, start_vrf="default", initial_gaps=(), source_interface=None
    ):
        paths, truncated = [], False

        def end(trail, status, **extra):
            nonlocal truncated
            if len(paths) >= MAX_PATHS:
                truncated = True
                return
            paths.append({"hops": trail, "end": {"status": status, **extra}})

        def deliver_local(key, vrf, trail):
            own = self.topo.ip_candidates(dst, device=key, vrf=vrf)
            if len(own) != 1:
                return False
            unavailable = self.interface_state(key, "destination", own[0][1], trail[-1])
            if unavailable:
                end(trail, "interface_unavailable", **unavailable)
            else:
                end(trail, "delivered", to="device", interface=own[0][1])
            return True

        def step(key, vrf, in_if, trail, visited, tunnel=None):
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
            if tunnel is None and deliver_local(key, vrf, [*trail, hop]):
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
            if route is None:
                end([*trail, hop], "no_route")
                return
            matched = {k: route.get(k) for k in ("prefix", "protocol", "preference", "metric")}
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
                    if not deliver_local(key, next_vrf, here):
                        end(here, "endpoint_unresolved", interface=out, destination=dst)
                    continue
                if not ip or ip in {"0.0.0.0", "::"} or own:
                    # A connected route: the device's own address on the egress interface.
                    unavailable = self.interface_state(key, "out", out, current)
                    if unavailable:
                        end(here, "interface_unavailable", **unavailable)
                        continue
                    evidence = self.endpoint_evidence(key, next_vrf, out, dst)
                    if not evidence or len({row["mac"] for row in evidence}) != 1:
                        end(here, "endpoint_unresolved", interface=out, destination=dst)
                        continue
                    current["endpoint_evidence"] = evidence
                    end(here, "delivered", to="subnet", interface=out)
                    continue
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
                    here = [*trail, current]
                    if neighbour_out.upper().startswith("NULL"):
                        end(here, "blackhole")
                        continue
                    if neighbour_out.lower().startswith("inloop"):
                        if not deliver_local(key, next_vrf, here):
                            end(here, "unresolved_next_hop", next_hop=neighbour_ip)
                        continue
                    unavailable = self.interface_state(key, "out", neighbour_out, current)
                    if unavailable:
                        end(here, "interface_unavailable", **unavailable)
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
                        )

        step(start, start_vrf, first_in, [], frozenset())
        return paths, truncated

    def direction(self, source, dst, vrf=None):
        """source is the locate answer for the start address; ``vrf`` keeps only starts in
        that network instance of their first-hop device (device-local name)."""
        result = {"to": dst, "first_hop": "none", "paths": [], "truncated": False}
        masters = []
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
        if vrf is not None:
            starts = [start for start in starts if start[2] == vrf]
            masters = [start for start in masters if start[2] == vrf]
            result["vrf"] = vrf
        if not starts:
            result["status"] = "no_gateway"
            return result
        if len(starts) > 1:
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
            if not device_locations and (
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
            paths, truncated = self.walk(key, first_in, dst, vrf, initial_gaps, source_interface)
            room = MAX_PATHS - len(result["paths"])
            result["paths"].extend(paths[:room])
            result["truncated"] |= truncated or len(paths) > room
        delivered = bool(result["paths"]) and all(
            p["end"]["status"] == "delivered" for p in result["paths"]
        )
        state_gaps = any(hop.get("gaps") for path in result["paths"] for hop in path["hops"])
        result["status"] = (
            "complete" if delivered and not result["truncated"] and not state_gaps else "partial"
        )
        return result


def _sequences(direction, reverse=False):
    return {
        tuple(reversed(ids) if reverse else ids)
        for ids in (
            [hop["device"]["node_id"] for hop in path["hops"]] for path in direction["paths"]
        )
    }


def trace(devices, base, src, dst, observed=None, src_vrf=None, dst_vrf=None):
    """devices: builder input keyed by collected address; observed: {address: evidence}.

    ``src_vrf``/``dst_vrf`` name the network instance an end is in on its first-hop device,
    for addresses that several VPNs reuse.
    """
    src, dst = _unicast(src), _unicast(dst)
    if src == dst:
        raise ValueError("源和目的地址相同")
    tracer = _Tracer(devices, base, observed or {})
    source, destination = answer(tracer.loc, "ip", src), answer(tracer.loc, "ip", dst)
    forward = tracer.direction(source, dst, src_vrf)
    reverse = tracer.direction(destination, src, dst_vrf)
    forward["from"], reverse["from"] = src, dst
    # Candidate first hops are not observed choices, so they cannot show asymmetry.
    routed = (
        forward["paths"]
        and reverse["paths"]
        and forward["status"] == reverse["status"] == "complete"
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
        "source": source,
        "destination": destination,
        "forward": forward,
        "reverse": reverse,
        "symmetric": _sequences(forward) == _sequences(reverse, reverse=True) if routed else None,
        "gaps": gaps,
    }
