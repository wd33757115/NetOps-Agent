"""Forwarding path between two IPs over one snapshot's observations.

Deterministic and evidence-bound: every routed hop names the device, the route it
matched in the device's own table (RFC 8349 RIB facts) and the observation behind it.
Where the data ends, a branch ends with a reason instead of a guess:

- ``delivered``: the destination is on a directly connected subnet, or is the device;
- ``blackhole``: the matched route points at a NULL interface;
- ``no_route``: nothing in the table covers the destination;
- ``unmanaged_next_hop``: the next hop is not an address of any collected device;
- ``routes_missing``: this device's routing table was not collected;
- ``loop``: the walk came back to a device it already passed.

A source host starts at the gateway of its subnet (every candidate when several could
be its gateway); a source that is a device starts at that device. Equal-cost next hops
branch, up to ``MAX_PATHS``. The reverse direction is traced the same way, and different
device sequences are reported as asymmetric.
"""

from __future__ import annotations

import ipaddress

from .builder import NT, ST
from .locate import _Locator, answer

MAX_PATHS = 8
MAX_HOPS = 32
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
        self.devices = devices
        self.loc = _Locator(devices, base, observed)
        self.topo = self.loc.topo

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

    def walk(self, start, first_in, dst):
        paths, truncated = [], False

        def end(trail, status, **extra):
            nonlocal truncated
            if len(paths) >= MAX_PATHS:
                truncated = True
                return
            paths.append({"hops": trail, "end": {"status": status, **extra}})

        def step(key, vrf, in_if, trail, visited):
            # A hop that ends the branch before a lookup keeps the same shape, with no route.
            hop = {
                "device": self.device(key),
                "vrf": vrf,
                "in_interface": in_if,
                "out_interface": None,
                "route": None,
            }
            owner = self.topo.owner.get((vrf, dst))
            if owner and owner[0] == key:
                end([*trail, hop], "delivered", to="device", interface=owner[1])
                return
            if (key, vrf) in visited or len(trail) >= MAX_HOPS:
                end([*trail, hop], "loop")
                return
            if "routes" not in self.devices[key]:
                end([*trail, hop], "routes_missing")
                return
            route = self.lookup(key, vrf, dst)
            if route is None:
                end([*trail, hop], "no_route")
                return
            matched = {k: route.get(k) for k in ("prefix", "protocol", "preference", "metric")}
            for next_hop in route["next_hops"]:
                ip, out = next_hop.get("ip"), next_hop.get("ifname") or ""
                current = {**hop, "out_interface": out, "route": {**matched, "next_hop": ip}}
                here = [*trail, current]
                next_vrf = next_hop.get("vrf") or vrf
                owner = self.topo.owner.get((next_vrf, ip))
                if out.upper().startswith("NULL"):
                    end(here, "blackhole")
                elif out.lower().startswith("inloop"):
                    end(here, "delivered", to="device", interface=out)
                elif not ip or ip in {"0.0.0.0", "::"} or (owner and owner[0] == key):
                    # A connected route: the device's own address on the egress interface.
                    end(here, "delivered", to="subnet", interface=out)
                elif owner is None:
                    end(here, "unmanaged_next_hop", next_hop=ip)
                else:
                    current["link_id"] = self.link_between(key, out, *owner)
                    step(owner[0], next_vrf, owner[1], here, visited | {(key, vrf)})

        step(start, "default", first_in, [], frozenset())
        return paths, truncated

    def direction(self, source, dst):
        """source is the locate answer for the start address."""
        result = {"to": dst, "first_hop": "none", "paths": [], "truncated": False}
        masters = []
        if source["status"] == "device":
            starts = [(source["locations"][0]["device"]["address"], None)]
        else:
            gateways = source["router_interfaces"]
            if any(
                ipaddress.ip_address(dst) in ipaddress.ip_network(g["subnet"]) for g in gateways
            ):
                result["status"] = "same_subnet"
                return result
            starts = [(g["address"], g["interface"]) for g in gateways]
            # With VRRP on the subnet the gateway is the group's master (RFC 8347 state).
            groups = self.redundancy(gateways)
            masters = sorted(
                {(m["address"], m["interface"]) for m in groups if m["state"] == "master"}
            )
            if groups:
                result["vrrp"] = groups
            if masters:
                starts = masters
        if not starts:
            result["status"] = "no_gateway"
            return result
        if len(starts) > 1:
            result["first_hop"] = "ambiguous"
        elif source["status"] != "device" and masters:
            result["first_hop"] = "vrrp_master"
            result["note"] = VRRP_FIRST_HOP
        else:
            result["first_hop"] = "single"
        for key, first_in in starts:
            paths, truncated = self.walk(key, first_in, dst)
            room = MAX_PATHS - len(result["paths"])
            result["paths"].extend(paths[:room])
            result["truncated"] |= truncated or len(paths) > room
        delivered = all(p["end"]["status"] == "delivered" for p in result["paths"])
        result["status"] = "complete" if delivered and not result["truncated"] else "partial"
        return result


def _sequences(direction, reverse=False):
    return {
        tuple(reversed(ids) if reverse else ids)
        for ids in (
            [hop["device"]["node_id"] for hop in path["hops"]] for path in direction["paths"]
        )
    }


def trace(devices, base, src, dst, observed=None):
    """devices: builder input keyed by collected address; observed: {address: evidence}."""
    src, dst = _unicast(src), _unicast(dst)
    if src == dst:
        raise ValueError("源和目的地址相同")
    tracer = _Tracer(devices, base, observed or {})
    source, destination = answer(tracer.loc, "ip", src), answer(tracer.loc, "ip", dst)
    forward, reverse = tracer.direction(source, dst), tracer.direction(destination, src)
    forward["from"], reverse["from"] = src, dst
    # Candidate first hops are not observed choices, so they cannot show asymmetry.
    routed = (
        forward["paths"]
        and reverse["paths"]
        and "ambiguous" not in (forward["first_hop"], reverse["first_hop"])
    )
    gaps = [
        {
            "direction": name,
            "status": path["end"]["status"],
            "device": path["hops"][-1]["device"],
            **({"next_hop": path["end"]["next_hop"]} if "next_hop" in path["end"] else {}),
        }
        for name, part in (("forward", forward), ("reverse", reverse))
        for path in part["paths"]
        if path["end"]["status"] != "delivered"
    ] + [
        {"direction": name, "status": part["status"]}
        for name, part in (("forward", forward), ("reverse", reverse))
        if part["status"] == "no_gateway"
    ]
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
