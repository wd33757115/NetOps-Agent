"""RFC 7951 trees from records named after standard leaves (netconf-api's conversion layer,
``src/apps/netconf_api/backend/standard.py``).

Records already carry the standard values; this module only places them: interfaces with
their IPv4/IPv6 addresses and neighbours (RFC 8343, RFC 8344), the routing of each
network instance (RFC 8349, with OSPF from RFC 9129) and LLDP (IEEE 802.1AB). It knows no
vendor. The sections have the shape of the CLI projections (``{"tree", "gaps",
"metadata"}``, routing one per instance), so the query tables read either.

What a model cannot hold is left out and listed in ``gaps``; nothing is filled in. RIB
names the device does not give are derived from instance and address family, as for
the CLI (owner decision 2026-10-06), and metadata says so.
"""

from __future__ import annotations

import ipaddress
from collections import defaultdict
from copy import deepcopy

from ..collectors.ifname import normalize_ifname
from .device_state import INTERFACE_TYPES

IF = "ietf-interfaces:interfaces"
RT = "ietf-routing:routing"
OSPF = "ietf-ospf:"
LLDP = "ieee802-dot1ab-lldp:lldp"
AF = {4: "ietf-ipv4-unicast-routing", 6: "ietf-ipv6-unicast-routing"}
AF_NAMES = {4: "ipv4", 6: "ipv6"}
LLDP_LEAVES = (
    "time-mark",
    "remote-index",
    "chassis-id-subtype",
    "chassis-id",
    "port-id-subtype",
    "port-id",
    "port-desc",
    "system-name",
    "system-description",
)
FAMILIES = {
    "ipv4": "ietf-ipv4-unicast-routing:ipv4-unicast",
    "ipv6": "ietf-ipv6-unicast-routing:ipv6-unicast",
}
UNSPECIFIED = {"0.0.0.0", "::"}


def _gap(gaps, code, **fields):
    gap = {"code": code, **fields}
    if gap not in gaps:
        gaps.append(gap)


def _section(standard, tree):
    return {
        "tree": tree,
        "gaps": [],
        "metadata": {"standards": [standard], "completeness": "partial", "source": "netconf"},
    }


def _interface_rows(records, platform, gaps):
    """{name: RFC 8343 entry} of the interface records, a duplicate name left out."""
    rows, duplicates = {}, set()
    for record in records.get("interface", []):
        name = record.get("name")
        if not name:
            _gap(gaps, "interface_name_not_observed")
            continue
        if name in rows:
            duplicates.add(name)
            continue
        entry = {"name": name}
        try:
            kind = normalize_ifname(name, platform).kind
        except ValueError:
            kind = None
        if kind not in INTERFACE_TYPES:
            # type is mandatory (RFC 8343): an interface without one is not exported.
            _gap(gaps, "interface_type_unmapped", interface=name, field="type")
            continue
        entry["type"] = INTERFACE_TYPES[kind]
        for leaf in ("description", "enabled", "oper-status"):
            if leaf in record:
                entry[leaf] = record[leaf]
        for leaf in ("enabled", "oper-status"):
            if leaf not in record:
                _gap(gaps, f"{leaf}_not_observed", interface=name, field=leaf)
        _gap(
            gaps,
            "mandatory_statistics_not_observed",
            interface=name,
            field="statistics/discontinuity-time",
        )
        rows[name] = entry
    for name in sorted(duplicates):
        del rows[name]
        _gap(gaps, "duplicate_interface", interface=name)
    return rows


def device_tree(records, platform):
    """RFC 8343/8344 interfaces with addresses and neighbours."""
    result = _section("RFC 8343", {IF: {"interface": []}})
    result["metadata"]["standards"] = ["RFC 8343", "RFC 8344"]
    gaps = result["gaps"]
    rows = _interface_rows(records, platform, gaps)
    for table, leaf in (("ipv4-address", "address"), ("ipv4-neighbor", "neighbor")):
        seen = defaultdict(list)
        for index, record in enumerate(records.get(table, [])):
            name, ip = record.get("interface"), record.get("ip")
            if name not in rows or ip is None:
                _gap(gaps, f"{table}_interface_not_proven", record_index=index)
                continue
            seen[(name, ip)].append(record)
        for (name, ip), found in sorted(seen.items()):
            if len(found) != 1:
                _gap(gaps, f"duplicate_{table}", interface=name, ip=ip)
                continue
            record = found[0]
            family = ipaddress.ip_address(ip).version
            entry = {"ip": ip}
            if leaf == "address":
                if "prefix-length" not in record:
                    _gap(gaps, "prefix_length_not_observed", interface=name, ip=ip)
                    continue
                entry["prefix-length"] = record["prefix-length"]
            else:
                if "link-layer-address" not in record:
                    _gap(gaps, "link_layer_address_not_observed", interface=name, ip=ip)
                    continue
                entry["link-layer-address"] = record["link-layer-address"]
            if "origin" in record:
                entry["origin"] = record["origin"]
            target = rows[name].setdefault(f"ietf-ip:ipv{family}", {})
            target.setdefault(leaf, []).append(entry)
    result["tree"][IF]["interface"] = [rows[name] for name in sorted(rows)]
    result["metadata"]["if_index"] = {
        r["name"]: r["if-index"] for r in records.get("interface", []) if "if-index" in r
    }
    return result


def _instance(record):
    return record.get("network-instance") or "default"


def _next_hops(rows, family, interfaces):
    """(RFC 8349 next-hop, [outside], problem) of one route's rows."""
    specials = {row.get("special-next-hop") for row in rows}
    if specials != {None}:
        if len(specials) != 1:
            return None, [], "conflicting_special_next_hop"
        return {"special-next-hop": specials.pop()}, [], None
    hops, unproven = [], []
    for row in rows:
        hop = {}
        address = row.get("next-hop-address")
        if address is not None and address not in UNSPECIFIED:
            if ipaddress.ip_address(address).version != family:
                return None, [], "next_hop_family_mismatch"
            hop["address"] = address
        name = row.get("outgoing-interface")
        if name is not None:
            if name in interfaces:
                hop["outgoing-interface"] = name
            elif "address" in hop:
                unproven.append(name)  # kept by address; metadata names the interface
            else:
                return None, [], "outgoing_interface_not_in_interfaces"
        if not hop:
            return None, [], "next_hop_not_observed"
        hops.append(hop)
    if len(hops) == 1:
        hop = hops[0]
        if "address" in hop:
            hop[f"{AF[family]}:next-hop-address"] = hop.pop("address")
        return hop, unproven, None
    for hop in hops:
        if "address" in hop:
            hop[f"{AF[family]}:address"] = hop.pop("address")
    # Operational next-hop-state-content has no index key (RFC 8349).
    return {"next-hop-list": {"next-hop": hops}}, unproven, None


def _ospf_instances(records, instance, interfaces, gaps):
    """RFC 9129 control-plane-protocol entries of one network instance."""
    found = []
    for process in records.get("ospf-instance", []):
        if _instance(process) != instance or "name" not in process:
            continue
        pid = process["name"]
        areas = defaultdict(list)
        neighbours = defaultdict(list)
        for row in records.get("ospf-neighbor", []):
            if row.get("instance") == pid and "neighbor-router-id" in row:
                neighbours[(row.get("area-id"), row.get("interface"))].append(row)
        for row in records.get("ospf-interface", []):
            if row.get("instance") != pid or "area-id" not in row:
                continue
            name = row.get("name")
            if name not in interfaces:
                _gap(gaps, "ospf_interface_not_in_interfaces", process_id=pid, interface=name)
                continue
            entry = {"name": name}
            for leaf in (
                "interface-type",
                "priority",
                "cost",
                "hello-interval",
                "dead-interval",
                "state",
            ):
                if leaf in row:
                    entry[leaf] = row[leaf]
            peers = []
            for nb in neighbours.get((row["area-id"], name), []):
                peer = {"neighbor-router-id": nb["neighbor-router-id"]}
                for leaf in ("address", "state"):
                    if leaf in nb:
                        peer[leaf] = nb[leaf]
                peers.append(peer)
            if peers:
                entry["neighbors"] = {"neighbor": peers}
            areas[row["area-id"]].append(entry)
        ospf = {}
        if "router-id" in process:
            ospf["router-id"] = process["router-id"]
        if areas:
            ospf["areas"] = {
                "area": [
                    {"area-id": area, "interfaces": {"interface": entries}}
                    for area, entries in sorted(areas.items())
                ]
            }
        found.append({"type": OSPF + "ospfv2", "name": pid, OSPF + "ospf": ospf})
    return found


def routing_sections(records, platform):
    """One RFC 8349 section per network instance, with its OSPF instances."""
    device = device_tree(records, platform)
    interfaces = {row["name"]: row for row in device["tree"][IF]["interface"]}
    routes = records.get("route", [])
    scopes = sorted(
        {_instance(r) for r in routes} | {_instance(r) for r in records.get("ospf-instance", [])}
    )
    sections = []
    for scope in scopes:
        result = _section("RFC 8349", {RT: {}})
        result["vrf"] = scope
        result["metadata"]["standards"] = ["RFC 8349", "RFC 9129"]
        gaps, metadata = result["gaps"], result["metadata"]
        mine = [(i, r) for i, r in enumerate(routes) if _instance(r) == scope]
        metadata["records"] = [r for _, r in mine]
        metadata["record_indexes"] = [i for i, _ in mine]
        groups = defaultdict(list)
        for index, row in mine:
            prefix = row.get("destination-prefix")
            if prefix is None or "source-protocol" not in row:
                _gap(gaps, "route_not_expressible", record_index=index, prefix=prefix)
                continue
            key = (prefix, row["source-protocol"], row.get("route-preference"))
            groups[key].append((index, row))
        ribs, used, derived = defaultdict(list), set(), {}
        for (prefix, protocol, preference), rows in sorted(groups.items(), key=str):
            family = ipaddress.ip_network(prefix).version
            hop, unproven, problem = _next_hops([r for _, r in rows], family, interfaces)
            if problem is not None:
                for index, _ in rows:
                    _gap(gaps, problem, record_index=index, prefix=prefix)
                continue
            route = {f"{AF[family]}:destination-prefix": prefix, "source-protocol": protocol}
            if preference is not None:
                route["route-preference"] = preference
            route["next-hop"] = hop
            first = rows[0][1]
            if protocol.startswith(OSPF):
                for leaf in ("metric", "tag", "route-type"):
                    if f"ospf-{leaf}" in first:
                        route[OSPF + leaf] = first[f"ospf-{leaf}"]
            for name in unproven:
                metadata.setdefault("next_hop_interfaces_unproven", []).append(
                    {"prefix": prefix, "interface": name}
                )
            used.update(h["outgoing-interface"] for h in _hops(hop) if "outgoing-interface" in h)
            name = AF_NAMES[family] if scope == "default" else f"{scope}:{AF_NAMES[family]}"
            derived[name] = {
                "network_instance": scope,
                "address_family": AF_NAMES[family],
                "reason": "the device prints no RIB name",
            }
            ribs[name].append(route)
        tree = result["tree"][RT]
        if ribs:
            tree["ribs"] = {
                "rib": [
                    {
                        "name": name,
                        "address-family": FAMILIES[derived[name]["address_family"]],
                        "routes": {"route": entries},
                    }
                    for name, entries in sorted(ribs.items())
                ]
            }
            metadata["derived_rib_names"] = {name: derived[name] for name in sorted(ribs)}
        protocols = _ospf_instances(records, scope, interfaces, gaps)
        if protocols:
            tree["control-plane-protocols"] = {"control-plane-protocol": protocols}
            for instance in protocols:
                for area in instance[OSPF + "ospf"].get("areas", {}).get("area", []):
                    used.update(i["name"] for i in area["interfaces"]["interface"])
        if used:
            result["tree"][IF] = {
                "interface": [deepcopy(interfaces[name]) for name in sorted(used)]
            }
        sections.append(result)
    return sections


def _hops(hop):
    return hop.get("next-hop-list", {}).get("next-hop", [hop])


def lldp_section(records, platform):
    """IEEE 802.1AB remote systems per local port and agent address."""
    device = device_tree(records, platform)
    interfaces = {row["name"]: row for row in device["tree"][IF]["interface"]}
    result = _section("IEEE 802.1AB", {LLDP: {}})
    gaps = result["gaps"]
    ports = defaultdict(list)
    for index, row in enumerate(records.get("lldp-neighbor", [])):
        missing = [
            key
            for key in ("interface", "dest-mac-address", "time-mark", "remote-index")
            if key not in row
        ]
        if missing or row["interface"] not in interfaces:
            _gap(gaps, "lldp_neighbor_keys_not_observed", record_index=index, missing=missing)
            continue
        entry = {}
        for key in LLDP_LEAVES:
            if key not in row:
                continue
            if isinstance(row[key], str) and len(row[key].encode()) > 255:
                # IEEE 802.1AB caps these TLV strings at 255 octets; the device's text
                # is kept in metadata, not cut.
                _gap(gaps, "lldp_value_too_long", record_index=index, field=key)
                result["metadata"].setdefault("values_not_exported", []).append(
                    {"record_index": index, "field": key, "value": row[key]}
                )
                continue
            entry[key] = row[key]
        ports[(row["interface"], row["dest-mac-address"])].append(entry)
    if ports:
        result["tree"][LLDP]["port"] = [
            {"name": name, "dest-mac-address": mac, "remote-systems-data": entries}
            for (name, mac), entries in sorted(ports.items())
        ]
        result["tree"][IF] = {
            "interface": [deepcopy(interfaces[name]) for name, _ in sorted(ports)]
        }
    return result


def project(records, platform):
    """The device tree and the control sections from one device's standard records."""
    return {
        "device": device_tree(records, platform),
        "control": {
            "routing": routing_sections(records, platform),
            "lldp": lldp_section(records, platform),
        },
    }
