"""Overlay reading of device facts: which network instance a fact belongs to, and which
next hops leave through a tunnel (plan §8 M5, VPN/EVPN/VXLAN).

Facts stay as collected; ``prepare`` returns a copy the builder and the path engine read
the same way. Every rule rests on a fact the device showed:

- Interface bindings come from the VRF detail (IOS "show vrf detail", Comware instance
  detail) and from the IP table's VRF column.
- A next hop out of an interface bound to another instance resolves there (a local
  route leak, e.g. a Comware VPN route imported from another instance).
- A next hop out of an L3 VNI interface (IOS "show nve vni" L3CP VLAN, Comware
  "Auto_L3VNI" VSI gateway) is a VXLAN tunnel to that VTEP; the VTEP is reached in the
  underlay (the instance of the local VTEP address).
- A BGP route in a VRF whose next hop is not in that VRF, on a router with established
  VPNv4 sessions, is an MPLS VPN route: the next hop is the remote PE, reached in the
  default instance over an LSP (RFC 4364).
"""

from __future__ import annotations

import ipaddress

# CLI codes, and the identity in the RFC 8349 tree.
BGP = {"B", "BGP", "bgp", "B*", "Bgp", "ietf-bgp:bgp"}


def bindings(facts):
    """{ifname: instance} from VRF detail, then from the IP table."""
    bound = {}
    for row in facts.get("ip") or []:
        if isinstance(row, dict) and row.get("ifname"):
            bound.setdefault(row["ifname"], row.get("vrf", "default"))
    for row in facts.get("vrf_detail") or []:
        for ifname in row.get("interfaces", []) if isinstance(row, dict) else []:
            bound[ifname] = row["vrf"]
    return bound


def l3vni_interfaces(facts):
    """{ifname: {"vni", "vrf"}} of the interfaces that carry an L3 VNI."""
    bound = bindings(facts)
    found = {}
    for row in facts.get("vxlan_vnis") or []:
        if not isinstance(row, dict) or row.get("type") != "l3" or "vni" not in row:
            continue
        if "vlan" in row:  # IOS: the core VLAN's SVI
            ifname = f"Vlan{row['vlan']}"
            vrf = row.get("vrf") or bound.get(ifname)
        elif row.get("gateway"):  # Comware: the VSI interface with the l3-vni
            ifname = row["gateway"]
            vrf = bound.get(ifname)
        else:
            continue
        if vrf:
            found[ifname] = {"vni": row["vni"], "vrf": vrf}
    return found


def vni_instance(facts, vni):
    """The VRF this device binds to an L3 VNI, if it shows one."""
    vrfs = {row["vrf"] for row in l3vni_interfaces(facts).values() if row["vni"] == vni}
    return next(iter(vrfs)) if len(vrfs) == 1 else None


def vtep_addresses(facts):
    """Local VTEP source addresses: IOS EVI "VTEP IP", Comware VXLAN tunnel sources."""
    found = {row["vtep"] for row in facts.get("evpn_instances") or [] if row.get("vtep")}
    found |= {
        row["source"]
        for row in facts.get("tunnel_interfaces") or []
        if row.get("mode") == "vxlan" and row.get("source")
    }
    return found


def vpnv4_established(facts):
    data = facts.get("bgp_vpnv4")
    peers = data.get("peers", []) if isinstance(data, dict) else []
    return any(p.get("state") == "established" for p in peers)


def route_targets(facts, vrf, direction, family="ipv4"):
    for row in facts.get("vrf_detail") or []:
        if row.get("vrf") == vrf:
            targets = row.get("route_targets", {})
            chosen = targets.get(family) or targets.get("instance") or {}
            return set(chosen.get(direction, []))
    return set()


def _connected(facts, vrf, address):
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    for row in facts.get("ip") or []:
        if row.get("vrf", "default") != vrf or "address" not in row:
            continue
        try:
            if ip in ipaddress.ip_interface(f"{row['address']}/{row['prefix_len']}").network:
                return True
        except (KeyError, ValueError):
            continue
    return False


def _routes(facts, bound):
    l3vni = l3vni_interfaces(facts)
    vteps = vtep_addresses(facts)
    # The underlay is the instance of the local VTEP address (default when unknown).
    underlay = {
        row.get("vrf", "default") for row in facts.get("ip") or [] if row.get("address") in vteps
    }
    underlay_vrf = underlay.pop() if len(underlay) == 1 else "default"
    vpn = vpnv4_established(facts)
    routes = []
    for route in facts.get("routes") or []:
        if not isinstance(route, dict):
            routes.append(route)
            continue
        vrf = route.get("vrf", "default")
        hops = []
        for hop in route.get("next_hops") or []:
            ifname, ip = hop.get("ifname"), hop.get("ip")
            if ifname in l3vni and ip:
                hop = {
                    **hop,
                    "vrf": underlay_vrf,
                    "tunnel": {"type": "vxlan", "vni": l3vni[ifname]["vni"], "endpoint": ip},
                }
            elif (
                vpn
                and vrf != "default"
                and route.get("protocol") in BGP
                and ip
                and bound.get(ifname, "default") == "default"
                and not _connected(facts, vrf, ip)
            ):
                hop = {
                    **hop,
                    "vrf": "default",
                    "tunnel": {
                        "type": "mpls",
                        "endpoint": ip,
                        "import": sorted(route_targets(facts, vrf, "import")),
                    },
                }
            elif ifname in bound and bound[ifname] != hop.get("vrf", vrf):
                hop = {**hop, "vrf": bound[ifname]}
            hops.append(hop)
        routes.append({**route, "next_hops": hops} if "next_hops" in route else route)
    return routes


def prepare(devices):
    """A copy of the devices' facts with instance bindings and tunnels made explicit."""
    prepared = {}
    for key, facts in devices.items():
        if not isinstance(facts, dict):
            prepared[key] = facts
            continue
        bound = bindings(facts)
        copy = dict(facts)
        if facts.get("routes"):
            copy["routes"] = _routes(facts, bound)
        if any(not row.get("ifname") for row in facts.get("arp") or []):
            # An entry the device ties to no interface (a VSI entry learned over a tunnel
            # link) supports no interface-based reasoning; it is left out here.
            copy["arp"] = [row for row in facts["arp"] if row.get("ifname")]
        prepared[key] = copy
    return prepared


def egress_instances(facts, tunnel, dst):
    """Where a tunnel ends on the remote device: the instance the packet is routed in.

    VXLAN: the VRF bound to the L3 VNI. MPLS VPN: the VRFs whose exported targets the
    ingress VRF imports and that originate (not import over BGP) a route to ``dst``.
    """
    if tunnel["type"] == "vxlan":
        vrf = vni_instance(facts, tunnel["vni"])
        return [vrf] if vrf else []
    try:
        address = ipaddress.ip_address(dst)
    except ValueError:
        return []
    imported = set(tunnel.get("import", []))
    found = []
    for row in facts.get("vrf_detail") or []:
        vrf = row.get("vrf")
        if not imported & route_targets(facts, vrf, "export"):
            continue
        best = None
        for route in facts.get("routes") or []:
            if route.get("vrf", "default") != vrf:
                continue
            try:
                network = ipaddress.ip_network(route["prefix"], strict=False)
            except (KeyError, ValueError):
                continue
            if address in network and (best is None or network.prefixlen > best[0]):
                best = (network.prefixlen, route.get("protocol"))
        if best and best[1] not in BGP:
            found.append(vrf)
    return sorted(found)
