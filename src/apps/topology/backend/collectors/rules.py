"""Topology facts from rule-governance check items (owner decision 2026-10-04).

Rule governance publishes the system base check items topology needs, their same-name
Values, one collection implementation per device family (command) and its TextFSM
template. Topology consumes them only through the public contract
``src.apps.rule_governance.runtime`` and projects the Values into its own fact records
(docs/topology-rule-consumption.md). The projections only normalize by Value meaning
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

from .normalize import ip_address, mac_address, network_prefix
from .parse import ParseResult
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
}
# Families whose CLI output rule governance parses for the capabilities above.
FAMILIES = frozenset({"ios", "iosxe", "comware"})
# Rule governance matches an implementation by vendor and platform family; the family is
# what decides the CLI (an HPE Comware device speaks the H3C Comware CLI).
VENDORS = {"ios": "Cisco", "iosxe": "Cisco", "comware": "H3C"}
WHOLE_TABLES = frozenset({"routes"})
SOURCE = "published"  # tests read the catalog shipped with rule governance ("shipped")
REFRESH_SECONDS = 60  # a newly published implementation is used by the next jobs
UINT32 = 2**32 - 1
_readers = {}


def reader():
    from src.apps.rule_governance.runtime import CheckItemSelectionReader

    cached = _readers.get(SOURCE)
    if cached is None or time.monotonic() - cached[1] > REFRESH_SECONDS:
        cached = _readers[SOURCE] = (CheckItemSelectionReader(source=SOURCE), time.monotonic())
    return cached[0]


def served(identity, capability):
    return capability in CHECK_ITEMS and _family(identity) in FAMILIES


def _family(identity):
    if isinstance(identity, dict):
        return identity.get("os_family", "unknown")
    return getattr(identity, "os_family", "unknown")


def device(identity, model=None):
    family = _family(identity)
    found = {"vendor": VENDORS.get(family), "platform": family}
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
        for family in {"ios" if f == "iosxe" else f for f in set(row["platforms"]) & FAMILIES}:
            decision = classify(family, row["command"])
            if decision.level == "L1" and decision.normalized == row["command"]:
                found.add((family, row["command"]))
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
    result.warnings += [f"line:{n}:template_rejected" for n in parsed.rejected]
    result.warnings += list(parsed.problems)
    result.candidate_count = len(parsed.rows) + len(parsed.rejected)
    result.rejected_count = len(parsed.rejected)
    if parsed.status == "failed":
        result.warnings.append("unparsed_output" if parsed.reason == "data" else parsed.reason)
        return result
    family = _family(identity)
    projected, seen = [], set()
    for row, lines in zip(parsed.rows, parsed.lines):
        try:
            record = ROWS[capability](row, family, lines, result.warnings)
        except (KeyError, ValueError) as exc:
            result.rejected_count += 1
            result.warnings.append(f"line:{lines[0] if lines else 0}:invalid_fact_value:{exc}")
            continue
        if key := UNIQUE.get(capability):
            identity = tuple(record.get(field) for field in key)
            if identity in seen:  # one device cannot list the same entity twice
                result.rejected_count += 1
                result.warnings.append(f"line:{lines[0] if lines else 0}:duplicate_entry")
                continue
            seen.add(identity)
        projected.append((record, lines))
    defects = result.rejected_count or parsed.problems
    if capability in WHOLE_TABLES and defects:
        return result  # a route table with an unread or invalid line is not used
    merged = MERGE.get(capability, lambda rows: rows)(projected)
    text = raw.splitlines()
    result.records = [record for record, _ in merged]
    result.sources = [
        {
            "lines": lines,
            "raw": {
                "check_item": chosen.check_item_id,
                "text": "\n".join(text[n - 1] for n in lines),
            },
        }
        for _, lines in merged
    ]
    if not result.records and (defects or incomplete):
        # Nothing read: a defect or a cut-off output cannot prove an empty table.
        result.records, result.sources = [], []
        return result
    result.status = "partial" if incomplete or defects else "complete"
    if capability in SUMMARIES:
        # A summary capability: its rows are one fact about the device.
        result.summary = SUMMARIES[capability](result.records)
        result.records = []
    if capability in HEADERS:
        # Table-header Values (Filldown on every row) are facts about the device.
        result.summary.update(HEADERS[capability](parsed.rows))
    return result


def _ifname(value, family):
    return interface(value, family).name if value else None


def _arp(row, family, lines, warnings):
    record = {
        "vrf": "default",
        "ip": ip_address(row["IP_ADDRESS"]),
        "mac": mac_address(row["MAC_ADDRESS"]),
    }
    column = "".join(row.get("VLAN_ID") or [])
    if column.isdigit():
        record["vlan"] = vlan(column)
    elif column and column != "--":
        record["vsi"] = column  # learned in a VSI (VXLAN)
    port = row.get("INTERFACE")
    # A VSI entry learned over a tunnel link shows a link ID instead of an interface.
    if port and not ("vsi" in record and port.isdigit() and not IFNAME.fullmatch(port)):
        record["ifname"] = _ifname(port, family)
    if row.get("TYPE") and family == "comware":
        record["type"] = {"D": "dynamic", "S": "static"}.get(row["TYPE"], row["TYPE"])
        # The Comware "Aging" column has no unit; the raw age is not converted.
        warnings.append(f"line:{lines[0] if lines else 0}:age_unit_unknown")
    if family in {"ios", "iosxe"} and str(row.get("AGE", "")).isdigit():
        record["age_s"] = int(row["AGE"]) * 60  # IOS shows minutes
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


def _vrf(row, family, lines, warnings):
    """A device-local VRF / VPN-instance name, never a global identity."""
    protocols = row.get("PROTOCOLS")
    return {"name": row["NAME"], "address_families": protocols.split(",") if protocols else []}


ROWS = {
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
}
MERGE = {"routes": _merge_routes, "vrf_detail": _merge_vrfs}
# Capabilities whose rows form one summary fact (the VRF enumeration scopes collection).
SUMMARIES = {"vrfs": lambda rows: {"instances": rows}}


def _bgp_header(rows):
    """The local BGP speaker: router ID and AS from the table header."""
    found = {}
    for field, key, convert in (("ROUTER_ID", "router_id", str), ("LOCAL_AS", "local_as", int)):
        if value := next((row[field] for row in rows if row.get(field)), None):
            found[key] = convert(value)
    return found


def _ospf_router_ids(rows):
    """{process: router ID} from the per-process headers (Comware)."""
    ids = {row["PROCESS"]: row["ROUTER_ID"] for row in rows if row.get("PROCESS")}
    return {"router_ids": ids} if ids else {}


# Table-header Values that describe the device: {capability: rows -> summary}.
HEADERS = {
    "bgp_peers": _bgp_header,
    "bgp_vpnv4": _bgp_header,
    "bgp_evpn": _bgp_header,
    "ospf_neighbors": _ospf_router_ids,
}
# Tables listing each entity once: a repeated entity is a contradiction, not a new row.
UNIQUE = {
    "vrfs": ("name",),
    "ldp_peers": ("peer_lsr_id", "label_space"),
    "tunnel_interfaces": ("ifname",),
}
