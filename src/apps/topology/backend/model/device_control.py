"""Partial native routing (RIBs, OSPF/BGP instances) and LLDP from one observation.

VRF association, collection evidence and preserved facts live outside RFC 7951
trees.  An observed VRF or an address family is not a native RIB name.  LLDP
neighbor identities likewise cannot supply agent MAC, time-mark or remote-index
keys.  This projector never manufactures those identifiers or claims a complete
device datastore.
"""

from __future__ import annotations

import ipaddress
import re
from collections import defaultdict
from copy import deepcopy

from .device_acl import project_device_acl
from .device_ip import enrich_device_ip
from .device_state import project_device_state
from .protocols import LDP, MPLS, OSPF, project_protocols

AF = {4: "ietf-ipv4-unicast-routing", 6: "ietf-ipv6-unicast-routing"}
PROTOCOLS = {
    "C": "ietf-routing:direct",
    "L": "ietf-routing:direct",
    "Direct": "ietf-routing:direct",
    "direct": "ietf-routing:direct",
    "S": "ietf-routing:static",
    "Static": "ietf-routing:static",
    "static": "ietf-routing:static",
    "O": "ietf-ospf:ospf",
    "O IA": "ietf-ospf:ospf",
    "O E1": "ietf-ospf:ospf",
    "O E2": "ietf-ospf:ospf",
    "O N1": "ietf-ospf:ospf",
    "O N2": "ietf-ospf:ospf",
    "O_INTRA": "ietf-ospf:ospf",
    "O_INTER": "ietf-ospf:ospf",
    "O_ASE1": "ietf-ospf:ospf",
    "O_ASE2": "ietf-ospf:ospf",
    "OSPF": "ietf-ospf:ospf",
    "ospf": "ietf-ospf:ospf",
    # draft-ietf-idr-bgp-model: identity bgp, base routing-protocol.
    "B": "ietf-bgp:bgp",
    "BGP": "ietf-bgp:bgp",
    "bgp": "ietf-bgp:bgp",
}
AF_NAMES = {4: "ipv4", 6: "ipv6"}
# An outgoing interface that discards, or delivers to the device itself (RFC 8349
# special-next-hop "blackhole" and "receive").
SPECIAL_INTERFACES = (("null", "blackhole"), ("inloop", "receive"))
PROTOCOLS.update({identity: identity for identity in set(PROTOCOLS.values())})
CHASSIS_SUBTYPES = frozenset(
    {
        "chassis-component",
        "interface-alias",
        "port-component",
        "mac-address",
        "network-address",
        "interface-name",
        "local",
    }
)
SPECIAL_HOPS = frozenset({"blackhole", "unreachable", "prohibit", "receive"})


def _text(value, maximum=None, empty=False):
    return (
        isinstance(value, str)
        and (empty or bool(value.strip()))
        and (maximum is None or len(value) <= maximum)
        and not any(ord(char) < 32 and char not in "\n\r\t" or ord(char) == 127 for char in value)
    )


def _identifier(value):
    return _text(value) and value == value.strip() and not any(ord(c) < 32 for c in value)


def _gap(result, code, **fields):
    gap = {"code": code, **fields}
    if gap not in result["gaps"]:
        result["gaps"].append(gap)


def _section(observation, standard, tree):
    metadata = {
        "standard": standard,
        "content": "operational",
        "completeness": "partial",
        "full_validation": "not_performed",
    }
    if isinstance(observation, dict):
        for source, target in (("id", "observation_id"), ("observed_at", "observed_at")):
            if isinstance(observation.get(source), str):
                metadata[target] = observation[source]
    return {"tree": tree, "metadata": metadata, "gaps": []}


def _capability(data, cap, result):
    facts = data.get("facts", data)
    sidecar = data.get("fact_metadata")
    metadata = sidecar.get(cap) if isinstance(sidecar, dict) else None
    summary = facts.get(cap) if isinstance(facts, dict) else None
    embedded = summary.get("parse_metadata") if isinstance(summary, dict) else None
    if isinstance(metadata, dict):
        result["metadata"].setdefault("fact_metadata", {})[cap] = deepcopy(metadata)
    if isinstance(embedded, dict):
        result["metadata"].setdefault("embedded_fact_metadata", {})[cap] = deepcopy(embedded)
    incomplete = any(
        isinstance(item, dict)
        and isinstance(item.get("warnings"), list)
        and "collection_incomplete" in item["warnings"]
        for item in (metadata, embedded)
    )
    capabilities = data.get("capabilities")
    status = capabilities.get(cap) if isinstance(capabilities, dict) else None
    if isinstance(status, str):
        result["metadata"].setdefault("capabilities", {})[cap] = status
    if incomplete:
        _gap(result, "transport_collection_incomplete", capability=cap)
        return False
    if capabilities is None:
        return True
    if not isinstance(status, str) or status not in {"complete", "partial"}:
        _gap(result, "capability_unusable", capability=cap)
        return False
    if status == "partial":
        _gap(result, "collection_partial", capability=cap)
    return True


def _owners(facts):
    """{interface: instances} from its addresses (IP facts) and from the device's VRF
    detail, which also names interfaces without an address (an L3 VNI's SVI)."""
    scopes = defaultdict(set)
    for record in facts.get("ip") or []:
        if isinstance(record, dict) and _identifier(record.get("ifname")):
            owner = record.get("vrf")
            scopes[record["ifname"]].add(owner if _identifier(owner) else None)
    for row in facts.get("vrf_detail") or []:
        if isinstance(row, dict) and _identifier(row.get("vrf")):
            for name in row.get("interfaces") or []:
                if _identifier(name):
                    scopes[name].add(row["vrf"])
    return scopes


def _scoped_interfaces(observation, data, facts, scope, result):
    """Only current interface identities with explicit IP-fact VRF ownership."""
    result["metadata"]["reference_facts"] = {
        cap: deepcopy(facts.get(cap)) for cap in ("ip", "interfaces")
    }
    if not _capability(data, "ip", result):
        return {}, []
    if not _capability(data, "interfaces", result):
        return {}, []
    records = facts.get("ip")
    if not isinstance(records, list):
        return {}, []
    scopes = _owners(facts)
    state = project_device_state(observation)
    entries = state["tree"]["ietf-interfaces:interfaces"]["interface"]
    names = {name for name, owners in scopes.items() if owners == {scope}}
    return {row["name"]: row for row in entries if row["name"] in names}, state["gaps"]


def _next_hop(record, version, scope, interfaces, result, index, used, outside=None):
    outside = outside or {}  # interfaces proven in exactly one other instance
    special = record.get("special_next_hop")
    if special is not None:
        if record.get("next_hops"):
            _gap(result, "conflicting_route_next_hop", record_index=index)
            return None
        if isinstance(special, str) and special in SPECIAL_HOPS:
            return {"special-next-hop": special}
        _gap(result, "invalid_special_next_hop", record_index=index)
        return None
    raw = record.get("next_hops")
    if not isinstance(raw, list) or not raw:
        _gap(result, "route_next_hop_not_observed", record_index=index)
        return None
    if len(raw) == 1 and isinstance(raw[0], dict) and raw[0].get("vrf") == scope:
        name = str(raw[0].get("ifname") or "").lower()
        for prefix, special in SPECIAL_INTERFACES:
            if name.startswith(prefix):
                return {"special-next-hop": special}
    hops, failed = [], False
    for hop_index, hop in enumerate(raw):
        if not isinstance(hop, dict):
            _gap(result, "invalid_route_next_hop", record_index=index, next_hop_index=hop_index)
            failed = True
            continue
        other = hop.get("vrf")
        if other != scope:
            if not _identifier(other) or hop.get("ip") is None:
                _gap(
                    result,
                    "next_hop_scope_not_proven",
                    record_index=index,
                    next_hop_index=hop_index,
                )
                failed = True
                continue
            # A next hop in another instance (a leaked route, a VPN route over the
            # underlay): RFC 8349 has no instance for it, so the tree holds its address and
            # metadata says where it resolves, as acl_views do for ACLs.
            try:
                parsed = ipaddress.ip_address(hop["ip"])
            except ValueError:
                parsed = None
            if parsed is None or parsed.version != version:
                _gap(
                    result, "invalid_next_hop_address", record_index=index, next_hop_index=hop_index
                )
                failed = True
                continue
            hops.append({f"{AF[version]}:address": str(parsed)})
            result["metadata"].setdefault("next_hops_outside_instance", []).append(
                {
                    "prefix": record.get("prefix"),
                    "address": str(parsed),
                    "network_instance": other,
                    **({"interface": hop["ifname"]} if hop.get("ifname") else {}),
                }
            )
            continue
        entry = {}
        address = hop.get("ip")
        if address is not None:
            try:
                parsed = (
                    ipaddress.ip_address(address)
                    if isinstance(address, str) and "%" not in address
                    else None
                )
            except ValueError:
                parsed = None
            if parsed is None or parsed.version != version:
                _gap(
                    result, "invalid_next_hop_address", record_index=index, next_hop_index=hop_index
                )
                # An explicit bad/cross-family address cannot become a different
                # interface-only forwarding instruction.
                failed = True
                continue
            else:
                entry[f"{AF[version]}:address"] = str(parsed)
        name = hop.get("ifname")
        if name is not None:
            if isinstance(name, str) and name in interfaces:
                entry["outgoing-interface"] = name
                used.add(name)
            elif _identifier(name) and entry and name in outside:
                # The interface is in another instance (a VPN route over the underlay):
                # this instance's tree cannot reference it; metadata names it.
                result["metadata"].setdefault("next_hops_outside_instance", []).append(
                    {
                        "prefix": record.get("prefix"),
                        "address": next(iter(entry.values())),
                        "interface": name,
                        "interface_instance": outside[name],
                    }
                )
            elif _identifier(name) and entry:
                # The device printed an interface the interface tree does not prove in
                # this instance (no state observed, a duplicate row): the tree keeps the
                # next-hop address, metadata the interface the device named, so the path
                # still leaves through it with its state unknown.
                result["metadata"].setdefault("next_hop_interfaces_unproven", []).append(
                    {
                        "prefix": record.get("prefix"),
                        "address": next(iter(entry.values())),
                        "interface": name,
                    }
                )
            else:
                _gap(
                    result,
                    "outgoing_interface_reference_not_proven",
                    record_index=index,
                    next_hop_index=hop_index,
                    interface=name if isinstance(name, str) else None,
                )
                failed = True
        if entry:
            hops.append(entry)
    if failed or not hops:
        # A route is exported whole or not at all: a next hop without its interface, or
        # one left out, would send the path elsewhere than the device does.
        _gap(result, "route_next_hop_not_projected", record_index=index)
        return None
    if len(raw) == 1 and len(hops) == 1:
        entry = hops[0]
        key = f"{AF[version]}:address"
        if key in entry:
            entry[f"{AF[version]}:next-hop-address"] = entry.pop(key)
        return entry
    if len(hops) != len(raw):
        _gap(result, "next_hop_list_incomplete", record_index=index)
    # Operational next-hop-state-content has no index key (RFC 8349).
    return {"next-hop-list": {"next-hop": hops}}


def _routing(observation, data, facts):
    base = _section(observation, "RFC 8349", {"ietf-routing:routing": {}})
    records = facts.get("routes")
    base["metadata"]["records"] = deepcopy(records) if isinstance(records, list) else []
    if not _capability(data, "routes", base):
        return [{"vrf": None, **base}]
    if not isinstance(records, list):
        _gap(base, "routes_not_observed" if records is None else "invalid_routes")
        return [{"vrf": None, **base}]
    scopes = defaultdict(list)
    for index, record in enumerate(records):
        scope = record.get("vrf") if isinstance(record, dict) else None
        scope = scope if _identifier(scope) else None
        scopes[scope].append((index, record))
    if not scopes:
        return [{"vrf": None, **base}]
    results = []
    for scope in sorted(scopes, key=lambda s: (s is not None, s or "")):
        result = deepcopy(base)
        result["vrf"] = scope
        result["metadata"]["records"] = [deepcopy(row) for _, row in scopes[scope]]
        needs_references = any(
            isinstance(record, dict)
            and isinstance(record.get("next_hops"), list)
            and any(
                isinstance(hop, dict) and hop.get("ifname") is not None
                for hop in record["next_hops"]
            )
            for _, record in scopes[scope]
        )
        interfaces, interface_gaps = (
            _scoped_interfaces(observation, data, facts, scope, result)
            if needs_references
            else ({}, [])
        )
        owners = _owners(facts)
        outside = {
            name: next(iter(found))
            for name, found in owners.items()
            if len(found) == 1 and next(iter(found)) not in {scope, None}
        }
        result["metadata"]["record_indexes"] = [index for index, _ in scopes[scope]]
        ribs, versions, conflicts, used, derived = {}, {}, set(), set(), set()
        for index, record in scopes[scope]:
            if not isinstance(record, dict):
                _gap(result, "invalid_route_record", record_index=index)
                continue
            if scope is None:
                _gap(result, "route_scope_not_observed", record_index=index)
                continue
            name = record.get("rib_name")
            try:
                prefix = record.get("prefix")
                network = (
                    ipaddress.ip_network(prefix, strict=True)
                    if isinstance(prefix, str) and "%" not in prefix
                    else None
                )
            except ValueError:
                network = None
            if network is None:
                _gap(result, "invalid_route_prefix", record_index=index)
                continue
            version = network.version
            if name is None:
                # The device prints no RIB name: one per instance and address family,
                # named from those (owner decision 2026-10-06), as metadata says.
                name = AF_NAMES[version] if scope == "default" else f"{scope}:{AF_NAMES[version]}"
                derived.add(name)
            elif not _identifier(name):
                _gap(result, "native_rib_name_not_observed", record_index=index)
                continue
            if name in versions and versions[name] != version:
                conflicts.add(name)
                continue
            versions[name] = version
            protocol = record.get("protocol")
            protocol = PROTOCOLS.get(protocol) if isinstance(protocol, str) else None
            if protocol is None:
                _gap(result, "source_protocol_identity_unmapped", record_index=index)
                continue
            entry = {f"{AF[version]}:destination-prefix": str(network), "source-protocol": protocol}
            preference = record.get("preference")
            if preference is not None:
                if (
                    isinstance(preference, int)
                    and not isinstance(preference, bool)
                    and 0 <= preference <= 2**32 - 1
                ):
                    entry["route-preference"] = preference
                else:
                    _gap(result, "invalid_route_preference", record_index=index)
            # No OSPF metric: RFC 9129 adds it only for routes of an OSPF version
            # (derived-from 'ospf'), and the version is not inferred from the CLI code.
            hop = _next_hop(record, version, scope, interfaces, result, index, used, outside)
            if hop is None:
                continue  # RFC 8349 next-hop is the route's forwarding: no route without it
            entry["next-hop"] = hop
            ribs.setdefault(name, []).append(entry)
        for name in sorted(conflicts):
            ribs.pop(name, None)
            _gap(result, "native_rib_address_family_conflict", rib_name=name)
        if derived & set(ribs):
            result["metadata"]["derived_rib_names"] = {
                name: {
                    "network_instance": scope,
                    "address_family": AF_NAMES[versions[name]],
                    "reason": "the device prints no RIB name",
                }
                for name in sorted(derived & set(ribs))
            }
        if ribs:
            result["tree"]["ietf-routing:routing"]["ribs"] = {
                "rib": [
                    {
                        "name": name,
                        "address-family": f"{AF[versions[name]]}:ipv{versions[name]}-unicast",
                        "routes": {"route": rows},
                    }
                    for name, rows in sorted(ribs.items())
                ]
            }
        # Close native outgoing-interface references, without copying stale state.
        referenced = {
            hop.get("outgoing-interface")
            for rows in ribs.values()
            for row in rows
            for hop in (
                [row.get("next-hop", {})]
                + row.get("next-hop", {}).get("next-hop-list", {}).get("next-hop", [])
            )
        }
        used &= referenced
        if used:
            result["tree"]["ietf-interfaces:interfaces"] = {
                "interface": [deepcopy(interfaces[name]) for name in sorted(used)]
            }
            result["gaps"].extend(
                deepcopy([gap for gap in interface_gaps if gap.get("interface") in used])
            )
        results.append(result)
    return results


def _lldp(observation, data, facts):
    result = _section(observation, "IEEE 802.1AB", {"ieee802-dot1ab-lldp:lldp": {}})
    local = facts.get("lldp_local")
    neighbors = facts.get("lldp")
    result["metadata"]["local_facts"] = deepcopy(local) if isinstance(local, dict) else None
    result["metadata"]["neighbor_records"] = (
        deepcopy(neighbors) if isinstance(neighbors, list) else []
    )
    if _capability(data, "lldp_local", result):
        if not isinstance(local, dict):
            _gap(result, "lldp_local_not_observed" if local is None else "invalid_lldp_local")
        else:
            projected = {}
            for source, target in (
                ("hostname", "system-name"),
                ("system_description", "system-description"),
            ):
                value = local.get(source)
                if value is not None:
                    if _text(value, maximum=255, empty=True):
                        projected[target] = value
                    else:
                        _gap(result, "invalid_lldp_local_field", field=target)
            chassis = local.get("chassis_id")
            subtype = local.get("chassis_subtype", local.get("chassis_id_subtype"))
            if chassis is not None:
                if (
                    "chassis_subtype" in local
                    and "chassis_id_subtype" in local
                    and local["chassis_subtype"] != local["chassis_id_subtype"]
                ):
                    _gap(result, "conflicting_local_chassis_subtype")
                elif not isinstance(subtype, str) or subtype not in CHASSIS_SUBTYPES:
                    _gap(result, "local_chassis_subtype_not_observed_or_invalid")
                elif not _identifier(chassis) or len(chassis) > 255:
                    _gap(result, "invalid_local_chassis_id")
                elif subtype == "network-address":
                    # IEEE's ID includes the AF octet and encoded address bytes;
                    # a CLI address string alone does not preserve that encoding.
                    _gap(result, "local_chassis_network_encoding_not_observed")
                else:
                    if subtype == "mac-address":
                        # A known encoding is required before canonicalizing octets.
                        if (
                            re.fullmatch(r"(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}", chassis)
                            and not int(chassis[:2], 16) & 1
                        ):
                            chassis = chassis.replace(":", "-").upper()
                        else:
                            _gap(result, "invalid_local_chassis_id")
                            chassis = None
                    if chassis is not None:
                        projected.update({"chassis-id-subtype": subtype, "chassis-id": chassis})
            if projected:
                result["tree"]["ieee802-dot1ab-lldp:lldp"]["local-system-data"] = projected
    if _capability(data, "lldp", result):
        if not isinstance(neighbors, list):
            _gap(
                result,
                "lldp_neighbors_not_observed" if neighbors is None else "invalid_lldp_neighbors",
            )
        else:
            for index, record in enumerate(neighbors):
                if not isinstance(record, dict):
                    _gap(result, "invalid_lldp_neighbor_record", record_index=index)
                    continue
                # No production collector currently preserves these native keys.
                missing = [
                    key
                    for key in ("dest_mac_address", "time_mark", "remote_index")
                    if record.get(key) is None
                ]
                _gap(
                    result,
                    "native_lldp_keys_not_observed"
                    if missing
                    else "native_lldp_port_projection_not_supported",
                    record_index=index,
                    missing_keys=missing,
                )
    return result


def project_device_control(observation):
    """Return independent native routing-per-VRF and local LLDP partial trees.

    Accept serialized Observation, its data, or facts.  Only usable current
    capabilities are consumed; preserved metadata is not a claim of projection
    completeness.  The caller supplies device/network association and history.
    """
    if not isinstance(observation, dict):
        data, facts, invalid = {}, {}, "invalid_observation"
    else:
        data = observation.get("data", observation)
        invalid = None
        if not isinstance(data, dict) or not isinstance(data.get("facts", data), dict):
            data, facts, invalid = {}, {}, "invalid_observation_data"
        else:
            facts = data.get("facts", data)
    result = {
        "routing": _routing(observation, data, facts),
        "lldp": _lldp(observation, data, facts),
        # RFC 8519 ACLs: independent of the routing trees, like LLDP.
        "acl": project_device_acl(observation if isinstance(observation, dict) else {}),
    }
    _protocols(observation, facts, result["routing"])
    if invalid:
        result["gaps"] = [{"code": invalid}]
    return result


def _protocols(observation, facts, routing):
    """Add OSPF/BGP instances (RFC 8349 control-plane-protocols) to their scope's tree.

    An OSPF interface is a reference into the interface list, so the interface is copied
    from the current interface projection; one that is not there is left out with a gap.
    An LDP interface must carry IPv4 (RFC 9070), so it is copied with its observed IPv4
    addresses; the MPLS interface list (RFC 8960) references interfaces the same way.
    """
    projected = project_protocols(facts)
    if not projected["instances"]:
        return
    state = project_device_state(observation)
    rows = {row["name"]: row for row in state["tree"]["ietf-interfaces:interfaces"]["interface"]}
    enriched = enrich_device_ip(state, observation)["tree"]
    ipv4 = {
        row["name"]: row["ietf-ip:ipv4"]
        for row in enriched.get("ietf-interfaces:interfaces", {}).get("interface", [])
        if row.get("ietf-ip:ipv4")
    }
    for scope, instances in sorted(projected["instances"].items()):
        entry = next((item for item in routing if item.get("vrf") == scope), None)
        if entry is None:
            entry = {
                "vrf": scope,
                **_section(observation, "RFC 8349", {"ietf-routing:routing": {}}),
            }
            routing.append(entry)
        entry["gaps"].extend(deepcopy(projected["gaps"]))
        used = set()
        for instance in instances:
            for area in instance.get(OSPF + "ospf", {}).get("areas", {}).get("area", []):
                kept = []
                for interface in area["interfaces"]["interface"]:
                    if interface["name"] in rows:
                        kept.append(interface)
                        used.add(interface["name"])
                    else:
                        _gap(
                            entry,
                            "ospf_interface_reference_not_proven",
                            interface=interface["name"],
                        )
                area["interfaces"]["interface"] = kept
        addressed = set()
        for instance in instances:
            discovery = instance.get(LDP + "mpls-ldp", {}).get("discovery", {})
            if not discovery:
                continue
            kept = []
            for interface in discovery["interfaces"]["interface"]:
                if interface["name"] in rows and interface["name"] in ipv4:
                    kept.append(interface)
                    addressed.add(interface["name"])
                else:
                    _gap(entry, "ldp_interface_reference_not_proven", interface=interface["name"])
            if kept:
                discovery["interfaces"]["interface"] = kept
            else:
                del instance[LDP + "mpls-ldp"]["discovery"]
        tree = entry["tree"]["ietf-routing:routing"]
        tree["control-plane-protocols"] = {"control-plane-protocol": deepcopy(instances)}
        mpls = []
        for name in projected["mpls"].get(scope, []):
            if name in rows:
                mpls.append({"name": name, "mpls-enabled": True})
                used.add(name)
            else:
                _gap(entry, "mpls_interface_reference_not_proven", interface=name)
        if mpls:
            tree[MPLS + "mpls"] = {"interfaces": {"interface": mpls}}
        used |= addressed
        if used:
            existing = entry["tree"].setdefault("ietf-interfaces:interfaces", {"interface": []})
            names = {row["name"] for row in existing["interface"]}
            for row in existing["interface"]:
                # Already referenced by a route: LDP still needs its IPv4 (RFC 9070).
                if row["name"] in addressed and "ietf-ip:ipv4" not in row:
                    row["ietf-ip:ipv4"] = deepcopy(ipv4[row["name"]])
            copies = []
            for name in sorted(used - names):
                row = deepcopy(rows[name])
                if name in addressed:
                    row["ietf-ip:ipv4"] = deepcopy(ipv4[name])
                copies.append(row)
            existing["interface"] = sorted(
                existing["interface"] + copies, key=lambda row: row["name"]
            )
        entry["metadata"]["standards"] = [
            "RFC 8349",
            "RFC 9129",
            "draft-ietf-idr-bgp-model-21",
            "RFC 9070",
            "RFC 8960",
        ]


def project_routing(observation):
    """The RFC 8349 routing sections of one observation, one per network instance."""
    data = observation.get("data", observation) if isinstance(observation, dict) else {}
    facts = data.get("facts", data) if isinstance(data, dict) else {}
    return _routing(observation, data, facts if isinstance(facts, dict) else {})
