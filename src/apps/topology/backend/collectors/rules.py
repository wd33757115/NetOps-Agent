"""Topology facts from rule-governance check items (owner decision 2026-10-04).

Rule governance publishes the system base check items topology needs, their same-name
Values, one collection implementation per device family (command) and its TextFSM
template. Topology consumes them only through the public contract
``src.apps.rule_governance.runtime`` and projects the Values into its own fact records.
The projections only normalize by Value meaning
(interface names, addresses, address families); nothing here reads vendor CLI text.

A capability listed in CHECK_ITEMS is served by rules for every device family rule
governance has an implementation for; a device without one gets a ``no_implementation``
result instead of a hand-written fallback.

How a defect is used is topology's decision: a row-wise table (ARP, VRF detail, EVPN
peers) keeps its good rows and is partial; a route table is only used whole, so any
rejected line, count mismatch or invalid row fails it (WHOLE_TABLES). Cut-off output
(paging, truncation, an incomplete transport) is ``collection_incomplete``.
"""

from __future__ import annotations

import ipaddress
import re
import time
from collections import Counter

from .aclnames import names as acl_names
from .families import cli
from .normalize import (
    interface_address,
    ip_address,
    mac_address,
    network_prefix,
    speed_mbps,
    vrf_name,
)
from .parse import ParseResult
from .rules_tables import ASSEMBLE
from .tiers.common import IFNAME, interface, vlan

# capability -> system check item
CHECK_ITEMS = {
    "arp": "topology.arp",
    "routes": "topology.routes",
    "vrf_detail": "topology.vrf_detail",
    "evpn_peers": "topology.evpn_peers",
    "ldp_peers": "topology.ldp_peers",
    "mpls_lfib": "topology.mpls_lfib",
    "evpn_instances": "topology.evpn_instances",
    "vxlan_vnis": "topology.vxlan_vnis",
    "tunnel_interfaces": "topology.tunnel_interfaces",
    "evpn_mac": "topology.evpn_mac",
    "vrfs": "topology.vrfs",
    "bgp_peers": "topology.bgp_ipv4_peers",
    "bgp_vpnv4": "topology.bgp_vpnv4_peers",
    "bgp_evpn": "topology.bgp_evpn_peers",
    "ospf_neighbors": "topology.ospf_neighbors",
    "vrrp": "topology.vrrp",
    "lag": "topology.lag",
    "stp": "topology.stp_ports",
    "interfaces": "topology.interfaces",
    "ip": "topology.ip_interfaces",
    "mac": "topology.mac",
    "vlan": "topology.vlans",
    "lldp": "topology.lldp_neighbors",
    "version": "topology.device_version",
    "lldp_local": "topology.lldp_local",
    "trunk": "topology.trunks",
    "switchport": "topology.switchports",
    "ospf_interfaces": "topology.ospf_interfaces",
    "ospf_process": "topology.ospf_process",
    "pbr_bindings": "topology.pbr_bindings",
    "pbr_rules": "topology.pbr_rules",
    "nat_bindings": "topology.nat_bindings",
    "acl_rules": "topology.acl_rules",
    "acl_bindings": "topology.acl_bindings",
    "security_zones": "topology.security_zones",
    "nat_rules": "topology.nat_rules",
}
WHOLE_TABLES = frozenset({"routes"})
SOURCE = "published"  # tests read the catalog shipped with rule governance ("shipped")
REFRESH_SECONDS = 60  # a newly published implementation is used by the next jobs
UINT32 = 2**32 - 1
_readers = {}


def _current():
    from src.apps.rule_governance.runtime import CheckItemSelectionReader

    cached = _readers.get(SOURCE)
    if cached is None or time.monotonic() - cached["at"] > REFRESH_SECONDS:
        cached = _readers[SOURCE] = {
            "reader": CheckItemSelectionReader(source=SOURCE),
            "at": time.monotonic(),
            "families": None,
        }
    return cached


def reader():
    return _current()["reader"]


def families():
    """{platform family: vendor} rule governance publishes topology implementations for.

    The vendor is the implementation's own: the family decides the CLI (an HPE Comware
    device speaks the H3C Comware CLI), so a device is matched as its family's vendor.
    A family is served as soon as rule governance publishes for it; no code names it.
    """
    cached = _current()
    if cached["families"] is None:
        found = {}
        for row in cached["reader"].commands(CHECK_ITEMS.values()):
            for platform in row["platforms"]:
                found.setdefault(platform, row.get("vendor"))
        cached["families"] = found
    return cached["families"]


def served(identity, capability):
    return capability in CHECK_ITEMS and _family(identity) in families()


def _family(identity):
    if isinstance(identity, dict):
        return identity.get("os_family", "unknown")
    return getattr(identity, "os_family", "unknown")


def device(identity, model=None):
    family = _family(identity)
    found = {"vendor": families().get(family), "platform": family}
    if model:
        found["model"] = model
    return found


def published_commands():
    """{(family, command)} rule governance publishes for the topology check items, in
    their global form and only when read-only (L1) for that family's CLI; per-VRF forms
    are granted per job (collect.scoped_command)."""
    from src.platform.device_access.command_policy import classify

    found = set()
    for row in reader().commands(CHECK_ITEMS.values()):
        if "{vrf}" in row["command"]:
            continue
        for dialect in {cli(family) for family in row["platforms"]} - {None}:
            decision = classify(dialect, row["command"])
            if decision.level == "L1" and decision.normalized == row["command"]:
                found.add((dialect, row["command"]))
    return found


def scoped_form(identity, capability, model=None):
    """The published per-VRF form of a capability's command ("{vrf}"), if any.

    An instance-only command (one that itself names the instance) is its own form.
    """
    chosen = selection(identity, capability, model)
    if chosen is None:
        return None
    if "{vrf}" in chosen.command:
        return chosen.command
    return chosen.scoped_command


def selection(identity, capability, model=None):
    return (
        reader()
        .select([CHECK_ITEMS[capability]], device(identity, model))
        .get(CHECK_ITEMS[capability])
    )


def parse(identity, capability, raw, *, complete=True, model=None):
    """A ParseResult like the generic tier's, from the published check item."""
    from src.apps.rule_governance.runtime import SelectionError

    result = ParseResult(tier="rules", parser_ref=f"rules:{CHECK_ITEMS[capability]}")
    try:
        chosen = selection(identity, capability, model)
    except SelectionError as exc:
        result.warnings.append(f"implementation_invalid:{exc}")
        return result
    if chosen is None:
        result.warnings.append("no_implementation")
        return result
    result.parser_ref = (
        f"rules:{chosen.implementation_id}:{chosen.template_ref}@{chosen.template_sha256}"
    )
    result.confidence = "high"
    parsed = reader().parse(chosen, raw)
    if parsed.status == "unsupported":
        result.status = "unsupported"
        result.warnings.append("unsupported_command")
        return result
    incomplete = not complete or parsed.incomplete_output
    if incomplete:
        result.warnings.append("collection_incomplete")
    reasons = parsed.rejected_reasons or ("",) * len(parsed.rejected)
    result.warnings += [
        f"line:{n}:template_rejected" + (f":{why}" if why and why != "template_rejected" else "")
        for n, why in zip(parsed.rejected, reasons)
    ]
    result.warnings += list(parsed.problems)
    result.candidate_count = len(parsed.rows) + len(parsed.rejected)
    result.rejected_count = len(parsed.rejected)
    family = _family(identity)
    output_failed = parsed.reason in {"command_error", "empty_unverified"}
    if capability in ASSEMBLE and not (output_failed and not incomplete):
        # A table with vetoes: a damaged or cut-off table is still a (vetoing) fact.
        return ASSEMBLE[capability](result, parsed, family, incomplete)
    if parsed.status == "failed":
        result.warnings.append("unparsed_output" if parsed.reason == "data" else parsed.reason)
        return result
    text = raw.splitlines()
    return _project(
        result,
        chosen,
        parsed,
        capability,
        family,
        incomplete,
        lambda lines: {
            "check_item": chosen.check_item_id,
            "text": "\n".join(text[n - 1] for n in lines),
        },
    )


def project_netconf(identity, capability, chosen, data, ref):
    """A ParseResult from a NETCONF read of the check item (collectors/netconf.py).

    ``data`` is netconf-api's NetconfRead: the same-name Value rows the CLI template
    would give, so the same projections apply. A row's "lines" are the device rows it
    came from. A problem the definition reports (an unlisted code, a lookup miss) is a
    defect like a rejected CLI line, and a row it could not output is a rejected row.
    """
    from src.apps.rule_governance.runtime import ParsedRows

    result = ParseResult(tier="netconf", parser_ref=f"netconf:{ref}", confidence="high")
    problems = tuple(f"row:{p['row']}:{p['kind']}:{p['value']}" for p in data.problems)
    result.warnings += list(problems)
    result.candidate_count = data.read
    result.rejected_count = data.read - len(data.rows)
    parsed = ParsedRows("complete", data.rows, "data", [[n] for n in data.refs], problems=problems)
    return _project(
        result,
        chosen,
        parsed,
        capability,
        _family(identity),
        False,
        lambda rows: {"check_item": chosen.check_item_id, "netconf": ref, "rows": rows},
        cli=False,
    )


def _project(result, chosen, parsed, capability, family, incomplete, source, *, cli=True):
    """Project Value rows into facts; ``source(lines)`` is a row's raw evidence."""
    projected = []
    for row, lines in zip(parsed.rows, parsed.lines):
        if cli:
            # A bare number in a Value whose unit the implementation declares gets that
            # unit; a Value fixed for the platform (and never printed) gets the declared
            # value. Both describe the CLI implementation, not a NETCONF definition.
            row = {
                **chosen.defaults,
                **row,
                **{
                    name: f"{row[name]} {unit}"
                    for name, unit in chosen.units.items()
                    if str(row.get(name) or "").isdigit()
                },
            }
        try:
            record = ROWS[capability](row, family, lines, result.warnings)
        except (KeyError, ValueError) as exc:
            result.rejected_count += 1
            result.warnings.append(f"line:{lines[0] if lines else 0}:invalid_fact_value:{exc}")
            continue
        # A row may hold several facts (an interface block with its addresses).
        projected += [(item, lines) for item in (record if isinstance(record, list) else [record])]
    if key := UNIQUE.get(capability):
        # One device cannot list the same entity twice; neither listing is kept.
        counts = Counter(tuple(record.get(field) for field in key) for record, _ in projected)
        for record, lines in projected:
            if counts[tuple(record.get(field) for field in key)] > 1:
                result.rejected_count += 1
                result.warnings.append(f"line:{lines[0] if lines else 0}:duplicate_entry")
        projected = [
            (record, lines)
            for record, lines in projected
            if counts[tuple(record.get(field) for field in key)] == 1
        ]
    defects = result.rejected_count or parsed.problems
    if capability in WHOLE_TABLES and defects:
        return result  # a route table with an unread or invalid line is not used
    merged = MERGE.get(capability, lambda rows: rows)(projected)
    result.records = [record for record, _ in merged]
    result.sources = [{"lines": lines, "raw": source(lines)} for _, lines in merged]
    if not result.records and (defects or incomplete):
        # Nothing read: a defect or a cut-off output cannot prove an empty table.
        result.records, result.sources = [], []
        return result
    result.status = "partial" if incomplete or defects else "complete"
    if capability in SUMMARIES:
        # A summary capability: its rows are one fact about the device.
        result.summary = SUMMARIES[capability](result.records)
        result.records = []
    if capability in SPLITS:
        result.summary.update(SPLITS[capability](result.records))
    if capability in HEADERS:
        # Table-header Values (Filldown on every row) are facts about the device.
        result.summary.update(HEADERS[capability](parsed.rows, family))
    return result


def _ifname(value, family):
    return interface(value, family).name if value else None


ARP_TYPES = {"d": "dynamic", "dynamic": "dynamic", "s": "static", "static": "static"}
AGE_UNITS = {"s": 1, "sec": 1, "min": 60, "h": 3600}


def _present(value):
    """A field the device printed, or None for its "none" marker ("--")."""
    return value if value not in (None, "", "--") else None


def _arp(row, family, lines, warnings):
    record = {
        "vrf": _present(row.get("VRF")) or "default",
        "ip": ip_address(row["IP_ADDRESS"]),
        "mac": mac_address(row["MAC_ADDRESS"]),
    }
    column = "".join(row.get("VLAN_ID") or [])
    if column.isdigit():
        record["vlan"] = vlan(column)
    elif _present(column):
        record["vsi"] = column  # learned in a VSI (VXLAN)
    if vsi := _present(row.get("VSI")):
        record["vsi"] = vsi
    port = row.get("INTERFACE")
    if gateway := _present(row.get("VSI_INTERFACE")):
        # A VSI entry belongs to the VSI's gateway interface; its port column is a link.
        record["ifname"] = _ifname(gateway, family)
    elif port and not ("vsi" in record and port.isdigit() and not IFNAME.fullmatch(port)):
        # A VSI entry learned over a tunnel link shows a link ID instead of an interface.
        record["ifname"] = _ifname(port, family)
    if kind := ARP_TYPES.get(str(row.get("TYPE") or "").lower()):
        record["type"] = kind
    age = str(row.get("AGE") or "")
    if match := re.fullmatch(r"(\d+) (s|sec|min|h)", age):
        record["age_s"] = int(match.group(1)) * AGE_UNITS[match.group(2)]
    return record


def _route(row, family, lines, warnings):
    vrf = row.get("VRF") or "default"
    preference, metric = int(row.get("DISTANCE") or 0), int(row.get("METRIC") or 0)
    if not (0 <= preference <= UINT32 and 0 <= metric <= UINT32):
        raise ValueError("invalid_route_metric")
    hop = {
        "ip": ip_address(row["NEXTHOP_IP"]) if row.get("NEXTHOP_IP") else None,
        "ifname": _ifname(row.get("NEXTHOP_IF"), family),
        "vrf": vrf,
    }
    if not hop["ip"] and not hop["ifname"]:
        raise ValueError("route_without_next_hop")
    return {
        "vrf": vrf,
        "prefix": network_prefix(f"{row['NETWORK']}/{row['PREFIX_LENGTH']}"),
        "protocol": " ".join(p for p in (row.get("PROTOCOL"), row.get("TYPE")) if p),
        "preference": preference,
        "metric": metric,
        "next_hops": [hop],
    }


def _merge_routes(rows):
    """Consecutive rows of one prefix/protocol/preference/metric are its equal-cost hops."""
    routes = []
    for record, lines in rows:
        key = tuple(record[k] for k in ("vrf", "prefix", "protocol", "preference", "metric"))
        if routes and routes[-1][0] == key:
            hops = routes[-1][1]["next_hops"]
            hops.extend(hop for hop in record["next_hops"] if hop not in hops)
            routes[-1][2].extend(lines)
            continue
        routes.append((key, record, list(lines)))
    return [(route, sorted(set(lines))) for _, route, lines in routes]


# Address-family names as the devices print them. IOS-XE "stitching" targets are the ones
# a VRF uses with EVPN.
FAMILY_NAMES = {
    "ipv4": "ipv4",
    "ipv6": "ipv6",
    "stitching": "evpn",
    "IPv4": "ipv4",
    "IPv6": "ipv6",
    "EVPN": "evpn",
    "IPv4 Flowspec": "ipv4-flowspec",
    "IPv6 Flowspec": "ipv6-flowspec",
}


def _vrf_family(row, family, lines, warnings):
    rd = row.get("RD")
    return {
        "vrf": row["VRF"],
        "rd": None if rd in (None, "<not set>", "--") else rd,
        "interfaces": [interface(value, family).name for value in row.get("INTERFACES") or []],
        "route_targets": {
            FAMILY_NAMES[row["ADDRESS_FAMILY"]]: {
                "import": list(row.get("IMPORT_RT") or []),
                "export": list(row.get("EXPORT_RT") or []),
            }
        },
    }


def _merge_vrfs(rows):
    """One record per VRF from its per-address-family rows."""
    records = {}
    for row, lines in rows:
        record, seen = records.setdefault(
            row["vrf"], ({**row, "interfaces": [], "route_targets": {}}, [])
        )
        seen.extend(lines)
        for name in row["interfaces"]:
            if name not in record["interfaces"]:
                record["interfaces"].append(name)
        record["route_targets"].update(row["route_targets"])
    return [(record, sorted(set(lines))) for record, lines in records.values()]


PEER_TYPES = {"L2CP": "l2", "L2DP": "l2", "L3CP": "l3", "L3DP": "l3"}


def _evpn_peer(row, family, lines, warnings):
    record = {"vni": int(row["VNI"]), "peer": str(ipaddress.ip_address(row["PEER"]))}
    # An IMET route (no type column) only exists for an L2 VNI.
    record["type"] = PEER_TYPES.get(row.get("TYPE"), "l2" if "INSTANCE" in row else None)
    if record["type"] is None:
        del record["type"]
    if row.get("INTERFACE"):
        record["nve"] = interface(row["INTERFACE"], family).name
    if row.get("STATE"):
        record["state"] = row["STATE"].lower()
    for key, field in (("instance", "INSTANCE"), ("rd", "RD"), ("mode", "ENCAPSULATION")):
        if row.get(field):
            record[key] = row[field].lower() if key == "mode" else row[field]
    if row.get("TUNNEL_ADDRESS"):
        record["tunnel_address"] = ip_address(row["TUNNEL_ADDRESS"])
    return record


# RFC 9070 session-state names.
LDP_STATES = {
    "oper": "operational",
    "operational": "operational",
    "nonexistent": "non-existent",
    "non-existent": "non-existent",
    "initialized": "initialized",
    "openrec": "openrec",
    "opensent": "opensent",
}


def _ldp_peer(row, family, lines, warnings):
    record = {
        "peer_lsr_id": ip_address(row["PEER_LSR_ID"]),
        "label_space": int(row.get("LABEL_SPACE") or 0),
    }
    if row.get("STATE"):
        record["state"] = LDP_STATES.get(row["STATE"].lower().replace(" ", ""))
        if record["state"] is None:
            raise ValueError("unknown_ldp_state")
    else:
        record["state_missing"] = True
    if row.get("ROLE"):
        record["role"] = row["ROLE"].lower()
    if row.get("LOCAL_LSR_ID"):
        record["local_lsr_id"] = ip_address(row["LOCAL_LSR_ID"])
        record["local_label_space"] = int(row.get("LOCAL_LABEL_SPACE") or 0)
    if row.get("DISCOVERY_INTERFACE") or row.get("LOCAL_LSR_ID"):
        record["discovery"] = [
            {"ifname": interface(name, family).name, "address": ip_address(address)}
            for name, address in zip(
                row.get("DISCOVERY_INTERFACE") or [],
                row.get("DISCOVERY_ADDRESS") or [],
                strict=True,
            )
        ]
        record["addresses"] = [ip_address(address) for address in row.get("ADDRESSES") or []]
    return record


def _label(value):
    """An MPLS label by meaning: a number, implicit-null (pop), untagged, or none."""
    if value in {None, "-", "None"}:
        return None
    if value in {"Pop Label", "3"}:
        return "implicit-null"
    if value == "No Label":
        return "untagged"
    if not value.isdigit() or int(value) > 1048575:
        raise ValueError("invalid_mpls_label")
    return int(value)


def _lfib_entry(row, family, lines, warnings):
    record = {
        "fec": row["FEC"],
        "in_label": _label(row.get("LOCAL_LABEL")),
        "out_label": _label(row.get("OUT_LABEL")),
    }
    if row.get("PROTOCOL"):
        record["protocol"] = row["PROTOCOL"].lower()
    if row.get("VRF"):
        record["vrf"] = row["VRF"]  # a VPN aggregate label: resolved in the VRF's table
    if row.get("INTERFACE") not in {None, "-", "drop"}:
        record["ifname"] = interface(row["INTERFACE"], family).name
    if row.get("NEXTHOP"):
        try:
            record["next_hop"] = ip_address(row["NEXTHOP"])
        except ValueError:
            record["next_hop"] = row["NEXTHOP"]  # e.g. a point-to-point link
    return record


# A route distinguisher or route target in its text forms (RFC 4364: ASN:n or IPv4:n).
TARGET = re.compile(r"(?:\d{1,10}|\d{1,3}(?:\.\d{1,3}){3}):\d{1,10}")


def _target(value):
    if not TARGET.fullmatch(value):
        raise ValueError("invalid_route_target")
    return value


def _vni(value):
    if not str(value).isdigit() or not 1 <= int(value) <= 16777215:
        raise ValueError("invalid_vni")
    return int(value)


def _ports(names, others, family, key, convert):
    return [
        {"ifname": interface(name, family).name, key: convert(other)}
        for name, other in zip(names or [], others or [], strict=True)
    ]


def _evpn_instance(row, family, lines, warnings):
    record = {
        "instance": row["INSTANCE"],
        "rt_import": [_target(value) for value in row.get("IMPORT_RT") or []],
        "rt_export": [_target(value) for value in row.get("EXPORT_RT") or []],
    }
    if row.get("SERVICE"):
        record["service"] = row["SERVICE"].lower().replace(" ", "-")
    if row.get("RD"):
        record["rd"] = _target(row["RD"])
    if state := row.get("STATE") or row.get("INSTANCE_STATE"):
        record["state"] = state.lower()  # a VLAN segment's own state wins
    if row.get("ENCAPSULATION"):
        record["encapsulation"] = row["ENCAPSULATION"].lower()
    if row.get("VLAN_ID"):
        record["vlan"] = int(row["VLAN_ID"])
    for key, field in (("vni", "VNI"), ("l3_vni", "L3_VNI")):
        if row.get(field, "0") != "0":  # 0: none
            record[key] = _vni(row[field])
    if row.get("VTEP_IP"):
        record["vtep"] = ip_address(row["VTEP_IP"])
    if row.get("VRF"):
        record["vrf"] = row["VRF"]
    if row.get("GATEWAY_INTERFACE"):
        record["gateway"] = interface(row["GATEWAY_INTERFACE"], family).name
    if row.get("CORE_INTERFACE"):
        record["core_interface"] = interface(row["CORE_INTERFACE"], family).name
    if row.get("ACCESS_INTERFACE"):
        record["ports"] = _ports(
            row["ACCESS_INTERFACE"], row.get("SERVICE_INSTANCE"), family, "service_instance", int
        )
    if row.get("PEER"):
        record["peers"] = [ip_address(peer) for peer in row["PEER"]]
    return record


VNI_TYPES = {"L2CP": "l2", "L2DP": "l2", "L3CP": "l3"}


def _vxlan_vni(row, family, lines, warnings):
    record = {}
    if row.get("INSTANCE"):
        record["instance"] = row["INSTANCE"]
    if row.get("INTERFACE"):
        record["nve"] = interface(row["INTERFACE"], family).name
    record["type"] = (
        VNI_TYPES[row["MODE"]] if row.get("MODE") else ("l3" if row.get("L3_VNI") else "l2")
    )
    if vni := row.get("VNI") or row.get("L3_VNI"):
        record["vni"] = _vni(vni)
    if row.get("STATE"):
        record["state"] = row["STATE"].lower()
    if row.get("SEGMENT"):
        record[row["SEGMENT_TYPE"].lower()] = int(row["SEGMENT"])
    if row.get("VRF") not in (None, "N/A"):
        record["vrf"] = row["VRF"]
    if row.get("GATEWAY_INTERFACE"):
        # "VSI-interface 920": the interface name with its number set apart.
        record["gateway"] = interface(row["GATEWAY_INTERFACE"].replace(" ", ""), family).name
    if row.get("TUNNEL"):
        record["tunnels"] = _ports(
            row["TUNNEL"], row.get("TUNNEL_STATE"), family, "state", str.lower
        )
    if row.get("ACCESS_INTERFACE"):
        record["ports"] = _ports(
            row["ACCESS_INTERFACE"], row.get("ACCESS_STATE"), family, "state", str.lower
        )
    return record


def _tunnel(row, family, lines, warnings):
    record = {"ifname": interface(row["INTERFACE"], family).name}
    if row.get("SOURCE") or row.get("DESTINATION"):
        record["source"] = ip_address(row["SOURCE"])
        record["destination"] = ip_address(row["DESTINATION"])
    if row.get("MODE"):
        # The transport by meaning: a UDP VXLAN tunnel is a VXLAN tunnel.
        record["mode"] = "vxlan" if row["MODE"].upper().startswith("UDP_VXLAN") else row["MODE"]
    if row.get("STATE"):
        record["state"] = row["STATE"].lower()
    if row.get("PROTOCOL_STATE"):
        record["protocol_state"] = row["PROTOCOL_STATE"].lower()
    return record


def _evpn_mac(row, family, lines, warnings):
    record = {"mac": mac_address(row["MAC_ADDRESS"]), "instance": row["INSTANCE"]}
    if row.get("VLAN_ID"):
        record["vlan"] = int(row["VLAN_ID"])
    if row.get("FLAGS"):
        record["flags"] = row["FLAGS"]
    if row.get("NEXTHOP_VTEP"):
        record["next_hop"] = {"vtep": ip_address(row["NEXTHOP_VTEP"])}
    elif row.get("NEXTHOP_INTERFACE"):
        record["next_hop"] = {
            "ifname": interface(row["NEXTHOP_INTERFACE"], family).name,
            "service_instance": int(row["SERVICE_INSTANCE"]),
        }
    elif row.get("LOCAL_FLAG"):
        record["next_hop"] = {"local": True}  # learned on a local access circuit
    return record


# draft-ietf-idr-bgp-model session-state names.
BGP_STATES = {
    "idle": "idle",
    "connect": "connect",
    "active": "active",
    "opensent": "opensent",
    "openconfirm": "openconfirm",
    "established": "established",
}


def _bgp_peer(row, family, lines, warnings):
    # A session that reports received prefixes is established (prefixes are exchanged
    # only then); IOS shows the count in place of the state.
    state = row.get("STATE") or ("Established" if row.get("PREFIXES_RECEIVED") else "")
    normalized = BGP_STATES.get(re.sub(r"\s*\(.*\)$", "", state).lower())
    if normalized is None:
        raise ValueError("unknown_bgp_state")
    record = {
        "peer": str(ipaddress.ip_address(row["PEER"])),
        "peer_as": int(row["PEER_AS"]),
        "state": normalized,
        "up_down": row.get("UP_DOWN"),
    }
    if row.get("PREFIXES_RECEIVED"):
        record["prefixes_received"] = int(row["PREFIXES_RECEIVED"])
    if normalized != state.lower():
        record["state_detail"] = state  # e.g. "Idle (Admin)"
    return record


# RFC 9129 nbr-state-type names.
OSPF_STATES = {
    "full": "full",
    "loading": "loading",
    "exchange": "exchange",
    "exstart": "exstart",
    "2way": "2-way",
    "2-way": "2-way",
    "init": "init",
    "attempt": "attempt",
    "down": "down",
}


def _ospf_neighbor(row, family, lines, warnings):
    state = OSPF_STATES.get(row["STATE"].lower())
    if state is None:
        raise ValueError("unknown_ospf_neighbor_state")
    record = {
        "neighbor_router_id": row["NEIGHBOR_ID"],
        "address": row["ADDRESS"],
        "priority": int(row["PRIORITY"]),
        "state": state,
        "role": row["ROLE"],
        "dead_time": row["DEAD_TIME"],
        "ifname": interface(row["INTERFACE"], family).name,
    }
    for key, field in (("process", "PROCESS"), ("area", "AREA")):
        if row.get(field):
            record[key] = row[field]
    return record


# RFC 8347 VRRP states.
VRRP_STATES = {
    "initialize": "initialize",
    "init": "initialize",
    "backup": "backup",
    "master": "master",
}


def _vrrp(row, family, lines, warnings):
    """RFC 8347 operational state; a group without state or virtual address is refused."""
    if not row.get("STATE"):
        raise ValueError("vrrp_block_without_state")
    if not row.get("VIRTUAL_IP"):
        raise ValueError("vrrp_block_without_virtual_address")
    addresses = []
    for value in row["VIRTUAL_IP"]:
        if (address := ip_address(value)) not in addresses:
            addresses.append(address)
    try:
        master = ip_address(row["MASTER_IP"]) if row.get("MASTER_IP") else None
    except ValueError:
        master = None
    return {
        "ifname": interface(row["INTERFACE"], family).name,
        "vrid": int(row["VRID"]),
        "state": VRRP_STATES.get(row["STATE"].lower(), row["STATE"].lower()),
        "priority": int(row["PRIORITY"]) if row.get("PRIORITY") else None,
        "virtual_addresses": addresses,
        "last_adv_source": master,
    }


def _lag(row, family, lines, warnings):
    """An aggregate (RFC 8944 lag) with every listed member and the bundled ones."""
    members = [interface(name, family).name for name in row.get("MEMBERS") or []]
    flags = row.get("MEMBER_FLAGS") or []
    if len(flags) != len(members):
        raise ValueError("lag_member_flags_mismatch")
    return {
        "ifname": interface(row["INTERFACE"], family).name,
        "members": members,
        "bundled": [interface(name, family).name for name in row.get("BUNDLED") or []],
        "member_flags": dict(zip(members, flags)),
    }


# IEEE 802.1Q port roles and states by their IOS abbreviations' meaning.
STP_ROLES = {
    "Root": "root",
    "Desg": "designated",
    "Altn": "alternate",
    "Back": "backup",
    "Mstr": "master",
    "Dis": "disabled",
}
STP_STATES = {
    "FWD": "forwarding",
    "BLK": "blocking",
    "LRN": "learning",
    "LIS": "listening",
    "DIS": "disabled",
    "BKN": "broken",
}


def _stp_instance(row):
    block = {"instance": row["INSTANCE"]}
    if row.get("VLAN_ID"):
        block["vlan"] = int(row["VLAN_ID"])
    elif row.get("MSTI"):
        block["msti"] = int(row["MSTI"])
    return block


def _stp_port(row, family, lines, warnings):
    if row.get("ROLE") not in STP_ROLES or row.get("STATE") not in STP_STATES:
        raise ValueError("unrecognized_stp_port_row")
    kind = (row.get("TYPE") or "").split()
    return {
        **_stp_instance(row),
        "protocol": row.get("PROTOCOL"),
        "ifname": interface(row["INTERFACE"], family).name,
        "role": STP_ROLES[row["ROLE"]],
        "state": STP_STATES[row["STATE"]],
        "cost": int(row["COST"]),
        "port_id": row["PORT_ID"],
        "link_type": {"P2p": "point-to-point", "Shr": "shared"}.get(kind[0] if kind else ""),
        "edge": "Edge" in kind,
    }


def _stp_instances(rows, family):
    """Each instance block's root and bridge identifiers (PVST state is evidence only)."""
    instances = {}
    for row in rows:
        if row["INSTANCE"] in instances:
            continue
        block = _stp_instance(row)
        if row.get("PROTOCOL"):
            block["protocol"] = row["PROTOCOL"]
        for section in ("root", "bridge"):
            if row.get(f"{section.upper()}_PRIORITY"):
                block[f"{section}_priority"] = int(row[f"{section.upper()}_PRIORITY"])
            if row.get(f"{section.upper()}_ADDRESS"):
                block[f"{section}_address"] = row[f"{section.upper()}_ADDRESS"]
        if row.get("ROOT_PORT"):
            block["root_port"] = interface(row["ROOT_PORT"], family).name
        block["is_root"] = bool(row.get("IS_ROOT"))
        instances[row["INSTANCE"]] = block
    return {"instances": list(instances.values())}


# Link states that say the port is administratively shut down.
ADMIN_DOWN = {"adm", "*down", "admin down", "administratively down", "disabled"}
L2_MODES = {"A": "access", "T": "trunk", "H": "hybrid"}


def _interface_state(row, family, lines, warnings):
    name = interface(row["INTERFACE"], family)
    link = row["LINK"].lower()
    record = {
        "ifname": name.name,
        "raw_name": row["INTERFACE"],
        "type": name.kind,
        "oper_status": "up" if link in {"up", "up(s)", "connected"} else "down",
        # These tables report an administrative shutdown in the link column itself.
        "admin_status": "down" if link in ADMIN_DOWN else "up",
    }
    if row.get("MODE") == "bridge":
        record["speed_mbps"] = speed_mbps(row["SPEED"])
        record["l2_mode"] = L2_MODES[row["TYPE"]]
        pvid = vlan(row["PVID"])
        if row["TYPE"] == "A":
            record["access_vlan"] = pvid  # a trunk/hybrid PVID is not an access VLAN
    record["description"] = " ".join((row.get("DESCRIPTION") or "").split()) or None
    return record


def _interface_addresses(row, family, lines, warnings):
    """Each address an interface shows, with its mask; "--" is no address."""
    name = interface(row["INTERFACE"], family).name
    vrf = vrf_name(row["VRF"], family) if row.get("VRF") else "default"
    values = row.get("ADDRESSES") or ([row["ADDRESS"]] if row.get("ADDRESS") else [])
    return [
        {"ifname": name, "vrf": vrf, **interface_address(value)}
        for value in values
        if value.lower() not in {"--", "unassigned", "none"}
    ]


MAC_TYPES = {"dynamic", "static", "learned", "blackhole"}


def _mac_entry(row, family, lines, warnings):
    kind = (row.get("TYPE") or row.get("STATE") or "").lower()
    return {
        "vlan_or_bd": str(vlan(row["VLAN_ID"])),
        "mac": mac_address(row["MAC_ADDRESS"]),
        "port": interface(row["PORT"], family).name,
        "type": kind if kind in MAC_TYPES else None,
    }


def _vlan(row, family, lines, warnings):
    """A VLAN row; its printed port column is evidence, not the full membership."""
    ports = []
    for text in filter(None, row.get("PORTS") or []):  # an absent optional item is None
        for value in re.split(r"[,\s]+", text.strip()):
            port = interface(value, family)
            if port.kind not in {"ethernet", "lag"}:
                raise ValueError("invalid_vlan_member_interface")
            if port.name in ports:
                raise ValueError("duplicate_vlan_port")
            ports.append(port.name)
    vid = vlan(row["VLAN_ID"])
    if not row.get("NAME"):
        raise ValueError("vlan_name_missing")
    member = {"vlan_id": vid, "ports": ports}
    if row.get("STATUS"):
        member["status"] = row["STATUS"]
    return {"vlan_id": vid, "name": row["NAME"], "_member": member}


def _vlan_members(records):
    """The port columns become ``summary.members``; the records keep ID and name."""
    members = [record.pop("_member") for record in records]
    return {"members": members} if members else {}


LLDP_LIMITS = {"CHASSIS_ID": 256, "PORT_ID": 256, "NEIGHBOR_NAME": 256}
LLDP_SUBTYPE_LIMIT = 32


def _lldp_neighbor(row, family, lines, warnings):
    if not row.get("LOCAL_INTERFACE") or not row.get("PORT_ID"):
        raise ValueError("incomplete_lldp_block")
    chassis, subtype = row["CHASSIS_ID"], row.get("CHASSIS_ID_TYPE")
    try:
        mac = mac_address(chassis)
    except ValueError:
        mac = None
    if subtype and "mac" in subtype.lower() and mac is None:
        raise ValueError("invalid_chassis_mac")
    if any(len(row.get(field) or "") > limit for field, limit in LLDP_LIMITS.items()) or any(
        len(row.get(field) or "") > LLDP_SUBTYPE_LIMIT
        for field in ("CHASSIS_ID_TYPE", "PORT_ID_TYPE")
    ):
        raise ValueError("lldp_field_over_limit")
    addresses = []
    for value in row.get("MGMT_ADDRESS") or []:
        try:
            address = ip_address(value)
        except ValueError:
            warnings.append(f"line:{lines[0] if lines else 0}:invalid_management_address")
            continue
        if address not in addresses:
            addresses.append(address)
    description = "\n".join(text for text in row.get("SYSTEM_DESCRIPTION") or [] if text)
    return {
        "local_if": interface(row["LOCAL_INTERFACE"], family).name,
        "remote_chassis_id": mac or chassis,
        "remote_chassis_subtype": subtype,
        "remote_port_id": row["PORT_ID"],
        "remote_port_subtype": row.get("PORT_ID_TYPE"),
        "remote_sysname": row.get("NEIGHBOR_NAME"),
        "remote_system_description": description or None,
        "remote_mgmt_addresses": addresses,
    }


VERSION_FIELDS = (
    "MODEL",
    "OS_VERSION",
    "SERIAL",
    "HOSTNAME",
    "BOOT_IMAGE",
    "BOOT_IMAGE_VERSION",
    "SYSTEM_IMAGE",
    "SYSTEM_IMAGE_VERSION",
    "IMAGE_NAME",
    "IMAGE_VERSION",
    "VIRTUAL_CPUS",
    "VIRTIO_NIC",
    "UNIX_PROCESSOR",
)


def _version(row, family, lines, warnings):
    """Identity candidates the output states; nothing is inferred from a hostname."""
    return {field.lower(): row[field] for field in VERSION_FIELDS if row.get(field)}


def _version_summary(rows):
    return dict(rows[0]) if rows else {}


def _lldp_local_row(row, family, lines, warnings):
    record = {}
    if row.get("CHASSIS_ID"):
        try:
            record["chassis_id"] = mac_address(row["CHASSIS_ID"])
        except ValueError:
            record["chassis_id"] = row["CHASSIS_ID"]
    for key, field in (
        ("hostname", "SYSTEM_NAME"),
        ("serial", "SERIAL"),
        ("vendor", "MANUFACTURER"),
        ("model", "MODEL"),
    ):
        if row.get(field):
            record[key] = row[field]
    if description := "\n".join(text for text in row.get("SYSTEM_DESCRIPTION") or [] if text):
        record["system_description"] = description
    value = row.get("MGMT_ADDRESS")
    if value and row.get("LOCAL_INTERFACE"):
        try:
            record["mgmt"] = {
                "ifname": interface(row["LOCAL_INTERFACE"], family).name,
                "address": ip_address(value),
            }
        except ValueError:
            try:
                # IEEE 802.1AB address subtype all802: a port without an IP advertises its
                # MAC. Only IP management addresses are modeled.
                mac_address(value)
            except ValueError:
                raise ValueError("invalid_local_management_address") from None
    return record


def _lldp_local_summary(rows):
    """The device's own LLDP identity and the management address each port advertises."""
    summary = {"management_addresses": []}
    for row in rows:
        for key, value in row.items():
            if key == "mgmt":
                if value not in summary["management_addresses"]:
                    summary["management_addresses"].append(value)
            else:
                summary.setdefault(key, value)
    return summary if summary.get("chassis_id") else {}


def _vrf(row, family, lines, warnings):
    """A device-local VRF / VPN-instance name, never a global identity."""
    protocols = row.get("PROTOCOLS")
    return {"name": row["NAME"], "address_families": protocols.split(",") if protocols else []}


def _pbr_binding(row, family, lines, warnings):
    """A policy applied to packets arriving on an interface, or (type Local, interface
    "local" or "N/A") to the packets the device itself sends: no interface, local."""
    port = row["INTERFACE"]
    if str(row.get("TYPE") or "forward").lower() == "local" or port.lower() in {"local", "n/a"}:
        return {"policy": row["POLICY"], "local": True}
    if str(row.get("TYPE") or "forward").lower() != "forward":
        return []
    return {"ifname": interface(port, family).name, "policy": row["POLICY"]}


def _pbr_rule(row, family, lines, warnings):
    """One node of a policy route: permit or deny, the ACL it matches, the next hop it
    sets. A clause the reading does not know is "unsupported": such a node is not
    evaluated as if the clause were absent."""
    record = {"policy": row["POLICY"], "node": int(row["NODE"])}
    mode = str(row.get("MODE") or "").lower()
    if mode not in {"permit", "deny"}:
        raise ValueError("pbr_mode")
    record["mode"] = mode
    if acl := row.get("MATCH_ACL"):
        record["acl"] = acl
    if next_hop := row.get("NEXT_HOP"):
        record["next_hop"] = ip_address(next_hop)
    if other := row.get("OTHER"):
        record["unsupported"] = [
            "clause:" + (" | ".join(other) if isinstance(other, list) else other)
        ]
    return record


def _nat_binding(row, family, lines, warnings):
    """NAT on interfaces: how each is bound (inside, outside, outbound, ...), one record each,
    with the role ("inside", "outside") the kind gives for rules of no interface of their own."""
    kind = " ".join(row["KIND"].lower().split())
    rule = row.get("RULE") if str(row.get("RULE") or "").strip("-") else None
    role = acl_names(cli(family))["nat_roles"].get(kind)
    return [
        {
            "ifname": interface(name, family).name,
            "kind": kind,
            **({"rule": rule} if rule else {}),
            **({"role": role} if role else {}),
        }
        for name in re.split(r"\s*,\s*", row["INTERFACE"].strip())
        if name
    ]


def _nat_port(value):
    port = int(value)
    if not 0 < port <= 65535:
        raise ValueError("nat_port")
    return port


def _nat_rule(row, family, lines, warnings):
    """One NAT rule, or one address range of a pool, read from its fields: a range is a pool;
    a local and a global address make a static mapping (of one protocol port when ports are
    given); anything else is source NAT by ACL ("---" or none: every packet) to an interface
    address or a pool. What the reading does not know is "unsupported": such a rule is not
    applied as if it were complete."""
    record = {"kind": " ".join(row["KIND"].split())}
    unsupported = []
    if other := row.get("OTHER"):
        unsupported.append("rule:" + (" | ".join(other) if isinstance(other, list) else other))
    if vrf := row.get("VRF"):
        unsupported.append(f"vrf:{vrf}")
    if (status := row.get("STATUS")) and status.lower() != "active":
        unsupported.append(f"status:{status}")
    if name := row.get("INTERFACE"):
        record["interface"] = interface(name, family).name
    if (pool := row.get("POOL")) and pool.strip("-"):
        record["pool"] = pool
    if row.get("POOL_START"):
        record.update(
            type="pool", start=ip_address(row["POOL_START"]), end=ip_address(row["POOL_END"])
        )
    elif row.get("LOCAL_ADDRESS"):
        record.update(type="static", local=ip_address(row["LOCAL_ADDRESS"]))
        if row.get("GLOBAL_ADDRESS"):
            record["global"] = ip_address(row["GLOBAL_ADDRESS"])
        elif row.get("GLOBAL_INTERFACE"):
            record["global_interface"] = interface(row["GLOBAL_INTERFACE"], family).name
        else:
            raise ValueError("nat_static_without_global")
        if protocol := str(row.get("PROTOCOL") or "").lower():
            number = (
                int(protocol)
                if protocol.isdigit()
                else acl_names(cli(family))["protocols"].get(protocol, -1)
            )
            if number not in {6, 17}:
                unsupported.append(f"protocol:{protocol}")
            else:
                record["protocol"] = number
        if row.get("LOCAL_PORT") or row.get("GLOBAL_PORT"):
            record["local_port"] = _nat_port(row["LOCAL_PORT"])
            record["global_port"] = _nat_port(row["GLOBAL_PORT"])
            if "protocol" not in record:
                unsupported.append("port_without_protocol")
    else:
        record["type"] = "source"
        if (acl := row.get("ACL_NAME")) and acl.strip("-"):
            record["acl"] = acl
        if row.get("TO_INTERFACE"):
            record["to_interface"] = interface(row["TO_INTERFACE"], family).name
        # Port translation: IOS "overload"; Comware unless NO-PAT.
        record["port_translation"] = bool(row.get("OVERLOAD")) or row.get("NO_PAT") == "N"
        if not ("acl" in record or row.get("ACL_NAME") or row.get("INTERFACE")):
            unsupported.append("rule_without_match")
    if unsupported:
        record["unsupported"] = unsupported
    return record


ADDRESS_FAMILIES = {"ip": "ipv4", "ipv4": "ipv4", "ipv6": "ipv6", "mac": "mac"}
ACL_DIRECTIONS = {"inbound": "in", "outgoing": "out", "outbound": "out"}
PORT_MATCHES = frozenset({"eq", "neq", "lt", "gt"})


def _acl_family(value):
    return ADDRESS_FAMILIES.get(str(value or "").lower())


def _acl_address(row, side):
    """{address, wildcard} of one end, None for any; the device prints a host as "host A"
    (IOS), as the bare address (IOS standard) or with wildcard "0" (Comware)."""
    if row.get(f"{side}_HOST"):
        return {"address": ip_address(row[f"{side}_HOST"]), "wildcard": "0.0.0.0"}
    if row.get(f"{side}_NETWORK"):
        wildcard = row.get(f"{side}_WILDCARD")
        if wildcard is None:
            raise ValueError("acl_address_without_wildcard")
        wildcard = "0.0.0.0" if wildcard == "0" else ip_address(wildcard)
        return {"address": ip_address(row[f"{side}_NETWORK"]), "wildcard": wildcard}
    return None


def _acl_port(value, protocol, tables, unsupported):
    if value.isdigit() and 0 <= int(value) <= 65535:
        return int(value)
    port = tables["ports"].get(protocol, {}).get(value.lower())
    if port is None:
        unsupported.append(f"port_name:{value}")
    return port


def _acl_ports(row, side, protocol, tables, unsupported):
    """{op, ports} of one end (op eq: any of the ports; range: [low, high]), or None."""
    if row.get(f"{side}_PORT_RANGE_START"):
        bounds = [
            _acl_port(row[f"{side}_PORT_RANGE_{edge}"], protocol, tables, unsupported)
            for edge in ("START", "END")
        ]
        return {"op": "range", "ports": bounds}
    if row.get(f"{side}_PORT_MATCH"):
        op = row[f"{side}_PORT_MATCH"].lower()
        if op not in PORT_MATCHES:
            raise ValueError("acl_port_operator")
        ports = [
            _acl_port(value, protocol, tables, unsupported)
            for value in str(row.get(f"{side}_PORT") or "").split()
        ]
        if not ports or (op != "eq" and len(ports) != 1):
            raise ValueError("acl_port_value")
        return {"op": op, "ports": ports}
    return None


def _acl_rule(row, family, lines, warnings):
    """One ACL entry, in the device's match order, as conditions a packet is checked
    against. A condition the entry has but this projection cannot evaluate (an object
    group, an unknown name or keyword) is listed in "unsupported": such an entry is
    never treated as if the condition were absent."""
    tables = acl_names(cli(family))
    unsupported = []
    record = {
        "acl": row["ACL_NAME"],
        "seq": int(row["LINE_NUM"]),
        "family": _acl_family(row.get("ADDRESS_FAMILY")) or "ipv4",
    }
    action = str(row.get("ACTION") or "").lower()
    if action in {"permit", "deny"}:
        record["action"] = action
    else:
        unsupported.append("unknown_entry")
    if other := row.get("OTHER"):
        unsupported.append(f"condition:{other}")
    protocol = str(row.get("PROTOCOL") or "").lower()
    if protocol:
        number = int(protocol) if protocol.isdigit() else tables["protocols"].get(protocol, -1)
        if number == -1 or (number is not None and not 0 <= number <= 255):
            unsupported.append(f"protocol:{protocol}")
            number = None
        if number is not None:
            record["protocol"] = number
    for side, key in (("SRC", "src"), ("DST", "dst")):
        if group := row.get(f"{side}_NETWORK_OBJECT_GROUP_NAME"):
            unsupported.append(f"object_group:{group}")
        elif address := _acl_address(row, side):
            record[key] = address
        if ports := _acl_ports(row, side, record.get("protocol"), tables, unsupported):
            record[f"{key}_ports"] = ports
    if kind := row.get("ICMP_TYPE"):
        if kind.isdigit():
            code = row.get("ICMP_CODE")
            record["icmp"] = [int(kind), int(code) if code else None]
        elif kind.lower() in {name.lower() for name in tables["icmp"]}:
            found = next(v for n, v in tables["icmp"].items() if n.lower() == kind.lower())
            record["icmp"] = list(found)
        else:
            unsupported.append(f"icmp_type:{kind}")
    if flag := row.get("TCP_FLAG"):
        if flag.lower() == "established":
            record["established"] = True
        else:
            unsupported.append(f"tcp_flag:{flag}")
    if row.get("FRAGMENTS"):
        record["fragments"] = True
    if name := row.get("TIME"):
        state = str(row.get("STATE") or "").lower()
        if state not in {"active", "inactive"}:
            unsupported.append(f"time_range:{name}")
        record["time_range"] = {"name": name, "active": state == "active"}
    if "VRF" in row:
        record["vrf"] = vrf_name(row["VRF"], family)
    if row.get("MATCHES"):
        record["matches"] = int(row["MATCHES"])
    if unsupported:
        record["unsupported"] = unsupported
    return record


def _acl_binding(row, family, lines, warnings):
    """The ACL that filters one direction of an interface, or that direction's action for
    packets no entry matches; rows of one interface, direction and family are merged."""
    record = {
        "ifname": interface(row["INTERFACE"], family).name,
        "direction": ACL_DIRECTIONS[row["DIRECTION"].lower()],
        "family": _acl_family(row.get("ADDRESS_FAMILY")),
    }
    if name := row.get("ACL_NAME"):
        record["acl"] = name
    if scope := row.get("SCOPE"):
        record["scope"] = scope.lower()
    if status := row.get("STATUS"):
        record["status"] = status
    if default := row.get("DEFAULT_ACTION"):
        record["default_action"] = {"permit": "permit", "deny": "deny"}[default.lower()]
    return record


def _merge_acl_bindings(projected):
    merged, order = {}, []
    for record, lines in projected:
        key = (record["ifname"], record["direction"], record["family"], record.get("scope"))
        if key not in merged:
            merged[key] = ({}, [])
            order.append(key)
        target, target_lines = merged[key]
        for name, value in record.items():
            if name in target and target[name] != value:
                # Two rows disagree: the binding is not one a path can rely on.
                target["status"] = "conflicting"
                continue
            target[name] = value
        target_lines.extend(lines)
    return [merged[key] for key in order]


def _security_zone(row, family, lines, warnings):
    return {"zone": row["ZONE"], "ifname": interface(row["INTERFACE"], family).name}


ROWS = {
    "acl_rules": _acl_rule,
    "acl_bindings": _acl_binding,
    "security_zones": _security_zone,
    "nat_rules": _nat_rule,
    "pbr_bindings": _pbr_binding,
    "pbr_rules": _pbr_rule,
    "nat_bindings": _nat_binding,
    "arp": _arp,
    "routes": _route,
    "vrf_detail": _vrf_family,
    "evpn_peers": _evpn_peer,
    "ldp_peers": _ldp_peer,
    "mpls_lfib": _lfib_entry,
    "evpn_instances": _evpn_instance,
    "vxlan_vnis": _vxlan_vni,
    "tunnel_interfaces": _tunnel,
    "evpn_mac": _evpn_mac,
    "vrfs": _vrf,
    "bgp_peers": _bgp_peer,
    "bgp_vpnv4": _bgp_peer,
    "bgp_evpn": _bgp_peer,
    "ospf_neighbors": _ospf_neighbor,
    "vrrp": _vrrp,
    "lag": _lag,
    "stp": _stp_port,
    "interfaces": _interface_state,
    "ip": _interface_addresses,
    "mac": _mac_entry,
    "vlan": _vlan,
    "lldp": _lldp_neighbor,
    "version": _version,
    "lldp_local": _lldp_local_row,
}
MERGE = {
    "routes": _merge_routes,
    "vrf_detail": _merge_vrfs,
    "acl_bindings": _merge_acl_bindings,
}
# Capabilities whose rows form one summary fact (the VRF enumeration scopes collection).
SUMMARIES = {
    "vrfs": lambda rows: {"instances": rows},
    "version": _version_summary,
    "lldp_local": _lldp_local_summary,
}
# Capabilities whose records also carry a summary part: records -> summary (in place).
SPLITS = {"vlan": _vlan_members}


def _bgp_header(rows, family):
    """The local BGP speaker: router ID and AS from the table header."""
    found = {}
    for field, key, convert in (("ROUTER_ID", "router_id", str), ("LOCAL_AS", "local_as", int)):
        if value := next((row[field] for row in rows if row.get(field)), None):
            found[key] = convert(value)
    return found


def _ospf_router_ids(rows, family):
    """{process: router ID} from the per-process headers (Comware)."""
    ids = {row["PROCESS"]: row["ROUTER_ID"] for row in rows if row.get("PROCESS")}
    return {"router_ids": ids} if ids else {}


# Table-header Values that describe the device: {capability: (rows, family) -> summary}.
HEADERS = {
    "bgp_peers": _bgp_header,
    "bgp_vpnv4": _bgp_header,
    "bgp_evpn": _bgp_header,
    "ospf_neighbors": _ospf_router_ids,
    "stp": _stp_instances,
}
# Tables listing each entity once: a repeated entity is a contradiction, not a new row.
UNIQUE = {
    "vrfs": ("name",),
    "acl_rules": ("acl", "seq"),
    "pbr_rules": ("policy", "node"),
    "security_zones": ("zone", "ifname"),
    "ldp_peers": ("peer_lsr_id", "label_space"),
    "tunnel_interfaces": ("ifname",),
    "stp": ("instance", "ifname"),
    "interfaces": ("ifname",),
    "vlan": ("vlan_id",),
}
