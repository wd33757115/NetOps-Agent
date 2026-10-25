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
        key = (row["interface"], row["dest-mac-address"], row["time-mark"], row["remote-index"])
        addresses = [
            {k: a[k] for k in ("address-subtype", "address", "if-subtype", "if-id") if k in a}
            for a in records.get("lldp-management-address", [])
            if (
                a.get("interface"),
                a.get("dest-mac-address"),
                a.get("time-mark"),
                a.get("remote-index"),
            )
            == key
            and "address-subtype" in a
            and "address" in a
        ]
        if addresses:
            entry["management-address"] = addresses
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


def _one_prefix(start, end):
    """The single prefix holding exactly start..end, or None."""
    try:
        found = list(
            ipaddress.summarize_address_range(
                ipaddress.ip_address(start), ipaddress.ip_address(end or start)
            )
        )
    except (TypeError, ValueError):
        return None
    return str(found[0]) if len(found) == 1 else None


def _subnets(rules):
    """([(rule, subnet)], None) when an ACL only permits source prefixes, else (None, why)."""
    found = []
    for rule in sorted(rules, key=lambda r: r.get("rule", 0)):
        if rule.get("unread") or rule.get("fragment") or rule.get("time-range"):
            return None, "acl_condition_not_a_subnet"
        if rule.get("network-instance"):
            return None, "acl_instance_condition"
        if rule.get("action") != "permit":
            return None, "acl_deny_rule"  # RFC 8512 matches subnets, it has no exceptions
        if rule.get("any-source"):
            subnet = "0.0.0.0/0"
        else:
            try:
                subnet = str(
                    ipaddress.ip_network(f"{rule['source-address']}/{rule['source-wildcard']}")
                )
            except (KeyError, ValueError):
                return None, "acl_wildcard_not_a_prefix"
        found.append((rule["rule"], subnet))
    return (found, None) if found else (None, "acl_rules_not_observed")


def nat_section(records, platform):
    """RFC 8512 NAT: outbound policies (pool or interface address, the subnets their ACL
    selects), and static mappings and internal servers as static mapping entries.

    Keys the device prints are the keys: a pool is its address group, a policy its ACL,
    a subscriber match the ACL rule. The device prints no NAT instance or mapping
    number: the instance is 1 and the mappings are numbered in the device's order, as
    metadata says. What RFC 8512 cannot hold (the interface's own address as the
    translation, an ACL with exceptions, ranges that are not one prefix) is a gap and
    that policy or mapping is left out whole.
    """
    device = device_tree(records, platform)
    interfaces = {row["name"]: row for row in device["tree"][IF]["interface"]}
    result = _section("RFC 8512", {})
    gaps, metadata = result["gaps"], result["metadata"]
    tables = ("nat-pool", "nat-outbound", "nat-server", "nat-static")
    if not any(table in records for table in tables):
        return result
    pools = {r["pool-id"]: r for r in records.get("nat-pool", []) if "pool-id" in r}
    rules = defaultdict(list)
    for rule in records.get("acl-basic-rule", []):
        if "acl" in rule:
            rules[rule["acl"]].append(rule)
    policies, used, kinds, by_interface = [], set(), {}, []
    for index, row in enumerate(records.get("nat-outbound", [])):
        acl, name = row.get("acl"), row.get("external-interface")
        if not (acl or "").isdigit() or name not in interfaces:
            _gap(gaps, "nat_policy_keys_not_observed", record_index=index)
            continue
        if any(p["id"] == int(acl) for p in policies):
            _gap(gaps, "nat_policy_acl_used_twice", record_index=index, acl=acl)
            continue
        if row.get("unread") or row.get("reversible") or row.get("port-preserved"):
            _gap(gaps, "nat_policy_option_not_expressible", record_index=index, acl=acl)
            continue
        if acl not in rules:
            _gap(gaps, "nat_selection_not_observed", record_index=index, acl=acl)
            continue
        matches, why = _subnets(rules[acl])
        if matches is None:
            _gap(gaps, why, record_index=index, acl=acl)
            continue
        policy = {"id": int(acl)}
        if "pool-id" in row:
            pool = pools.get(row["pool-id"])
            prefix = pool and _one_prefix(pool.get("start-address"), pool.get("end-address"))
            if not prefix:
                _gap(gaps, "nat_pool_not_one_prefix", record_index=index, pool_id=row["pool-id"])
                continue
            policy["external-ip-address-pool"] = [
                {"pool-id": row["pool-id"], "external-ip-pool": prefix}
            ]
        else:
            # Easy IP: the interface's own address; RFC 8512 has no leaf for it.
            by_interface.append(int(acl))
        policy["subscriber-match"] = [{"match-id": n, "subnet": subnet} for n, subnet in matches]
        policy["external-realm"] = {"external-interface": name}
        kinds[int(acl)] = "basic-nat44" if row.get("no-pat") else "napt44"
        used.add(name)
        policies.append(policy)
    entries = []
    for kind, table in (("static", "nat-static"), ("server", "nat-server")):
        for index, row in enumerate(records.get(table, [])):
            if row.get("unread") or row.get("reversible") or row.get("disabled"):
                _gap(gaps, f"nat_{kind}_option_not_expressible", record_index=index)
                continue
            if row.get("acl") or any(
                row.get(k)
                for k in (
                    "network-instance",
                    "internal-network-instance",
                    "external-network-instance",
                )
            ):
                _gap(gaps, f"nat_{kind}_condition_not_expressible", record_index=index)
                continue
            entry = {"index": len(entries) + 1, "type": "static"}
            if kind == "static":
                inside = _one_prefix(row.get("internal-src-start"), row.get("internal-src-end"))
                if not inside or "external-src-address" not in row:
                    _gap(gaps, "nat_static_range_not_one_prefix", record_index=index)
                    continue
                entry.update(
                    {
                        "internal-src-address": inside,
                        "external-src-address": row["external-src-address"],
                    }
                )
            else:
                if row.get("external-dst-end-address") or row.get("external-dst-end-port"):
                    _gap(gaps, "nat_server_range_not_expressible", record_index=index)
                    continue
                if not all(
                    k in row
                    for k in (
                        "transport-protocol",
                        "external-dst-address",
                        "internal-dst-address",
                    )
                ):
                    _gap(gaps, "nat_server_keys_not_observed", record_index=index)
                    continue
                entry.update(
                    {
                        "transport-protocol": row["transport-protocol"],
                        "internal-dst-address": f"{row['internal-dst-address']}/32",
                        "external-dst-address": f"{row['external-dst-address']}/32",
                    }
                )
                for side in ("internal", "external"):
                    if f"{side}-dst-port" in row:
                        entry[f"{side}-dst-port"] = {"start-port-number": row[f"{side}-dst-port"]}
            metadata.setdefault("mapping_entries", {})[entry["index"]] = {
                "kind": kind,
                **({"interface": row["interface"]} if row.get("interface") else {}),
            }
            entries.append(entry)
    instance = {"id": 1}
    if policies:
        instance["policy"] = policies
    if entries:
        instance["mapping-table"] = {"mapping-entry": entries}
    result["tree"]["ietf-nat:nat"] = {"instances": {"instance": [instance]}}
    _gap(gaps, "mandatory_statistics_not_observed", field="statistics/discontinuity-time")
    metadata["derived_keys"] = {
        "instance": {"id": 1, "reason": "the device has one NAT and prints no instance number"},
        "mapping-entry": "numbered in the device's order (static mappings, then servers)",
    }
    metadata["policy_types"] = {str(k): v for k, v in sorted(kinds.items())}
    if by_interface:
        metadata["translated_to_interface_address"] = sorted(by_interface)
    if used:
        result["tree"][IF] = {"interface": [deepcopy(interfaces[n]) for n in sorted(used)]}
    return result


def project(records, platform):
    """The device tree and the control sections from one device's standard records."""
    return {
        "device": device_tree(records, platform),
        "control": {
            "routing": routing_sections(records, platform),
            "lldp": lldp_section(records, platform),
            "nat": nat_section(records, platform),
        },
    }
