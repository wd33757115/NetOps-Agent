"""The route query table, taken from the RFC 8349 routing trees (device_control.py).

One row per route of each RIB in each network instance, with the tree's own content:
destination prefix, source-protocol identity, route-preference (no metric: RFC 9129
adds the OSPF metric only for routes of an OSPF version, which the CLI does not give),
and the next hops (address, outgoing interface) or
the special next hop (blackhole, receive). RIB names the device does not print are
derived from instance and address family (owner decision 2026-10-06); the tree's
metadata says which. A route the tree could not hold (an unmapped protocol, a next hop
it cannot express whole) is listed by prefix under ``missing``: a lookup it could win is
not decided as if it were absent.

``routes`` gives the rows in the shape the path walk reads.
"""

from __future__ import annotations

import ipaddress

from .device_control import project_routing
from .standard_trees import routing_sections

AF = ("ietf-ipv4-unicast-routing", "ietf-ipv6-unicast-routing")


def _hop(entry):
    for family in AF:
        for leaf in ("next-hop-address", "address"):
            if f"{family}:{leaf}" in entry:
                return {"ip": entry[f"{family}:{leaf}"], "ifname": entry.get("outgoing-interface")}
    return {"ip": None, "ifname": entry.get("outgoing-interface")}


def query_table(sections):
    """{"routes": [{vrf, rib, destination-prefix, source-protocol, route-preference,
    next-hops, special-next-hop}], "missing": {vrf: [prefix]}}."""
    rows, missing = [], {}
    for section in sections:
        vrf = section.get("vrf")
        if vrf is None:
            continue
        elsewhere = {
            (item["prefix"], item["address"]): item
            for key in ("next_hops_outside_instance", "next_hop_interfaces_unproven")
            for item in section["metadata"].get(key, [])
        }
        for rib in section["tree"].get("ietf-routing:routing", {}).get("ribs", {}).get("rib", []):
            for route in rib.get("routes", {}).get("route", []):
                prefix = next(
                    route[f"{family}:destination-prefix"]
                    for family in AF
                    if f"{family}:destination-prefix" in route
                )
                hop = route.get("next-hop", {})
                row = {
                    "vrf": vrf,
                    "rib": rib["name"],
                    "destination-prefix": prefix,
                    "source-protocol": route["source-protocol"],
                    "route-preference": route.get("route-preference"),
                }
                if "special-next-hop" in hop:
                    row["special-next-hop"] = hop["special-next-hop"]
                    row["next-hops"] = []
                elif "next-hop-list" in hop:
                    row["next-hops"] = [_hop(item) for item in hop["next-hop-list"]["next-hop"]]
                else:
                    row["next-hops"] = [_hop(hop)]
                for item in row["next-hops"]:
                    # Where metadata says the next hop resolves in another instance, or
                    # which interface the device named that the tree does not prove.
                    other = elsewhere.get((prefix, item["ip"]))
                    if other:
                        if other.get("network_instance"):
                            item["network-instance"] = other["network_instance"]
                        if other.get("interface"):
                            item["ifname"] = other["interface"]
                rows.append(row)
        records = section["metadata"].get("records") or []
        # Gaps number the device's records; the section keeps its instance's.
        position = {
            index: n for n, index in enumerate(section["metadata"].get("record_indexes") or [])
        }
        for gap in section.get("gaps") or []:
            n = position.get(gap.get("record_index"))
            if n is None or n >= len(records):
                continue
            prefix = records[n].get("prefix") if isinstance(records[n], dict) else None
            if prefix and prefix not in missing.setdefault(vrf, []):
                missing[vrf].append(prefix)
    return {"routes": rows, "missing": missing}


def routes(table):
    """The rows as the path walk reads routes (vrf, prefix, protocol, preference, metric,
    next_hops of ip and ifname, special_next_hop)."""
    found = []
    for row in table["routes"]:
        route = {
            "vrf": row["vrf"],
            "prefix": row["destination-prefix"],
            "protocol": row["source-protocol"],
            "preference": row["route-preference"],
            "metric": None,
            "next_hops": [
                {
                    "ip": hop["ip"],
                    "ifname": hop["ifname"],
                    "vrf": hop.get("network-instance", row["vrf"]),
                }
                for hop in row["next-hops"]
            ],
        }
        if row.get("special-next-hop"):
            route["special_next_hop"] = row["special-next-hop"]
        found.append(route)
    return found


def uncovered(table, vrf, dst, prefixlen):
    """Routes the tree could not hold that would match ``dst`` at least as specifically."""
    address = ipaddress.ip_address(dst)
    found = []
    for prefix in table["missing"].get(vrf, []):
        network = ipaddress.ip_network(prefix, strict=False)
        if network.version == address.version and address in network:
            if prefixlen is None or network.prefixlen >= prefixlen:
                found.append(prefix)
    return found


def standard_records(facts):
    """The device's NETCONF tables as standard records, when it was read that way."""
    standard = facts.get("standard") if isinstance(facts, dict) else None
    records = standard.get("records") if isinstance(standard, dict) else None
    return records if isinstance(records, dict) else None


def route_tables(facts):
    """The route query table from the device's RFC 8349 trees: built from its NETCONF
    tables when it was read that way, else from its CLI facts."""
    records = standard_records(facts)
    if records is not None and "route" in records:
        return query_table(routing_sections(records, facts.get("platform", "")))
    return query_table(project_routing({"facts": facts}))
