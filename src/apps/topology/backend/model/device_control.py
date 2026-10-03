"""Partial native routing and LLDP instances from one current observation.

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

from .device_state import project_device_state

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
}
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
    scopes = defaultdict(set)
    for record in records:
        if isinstance(record, dict) and _identifier(record.get("ifname")):
            owner = record.get("vrf")
            scopes[record["ifname"]].add(owner if _identifier(owner) else None)
    state = project_device_state(observation)
    entries = state["tree"]["ietf-interfaces:interfaces"]["interface"]
    names = {name for name, owners in scopes.items() if owners == {scope}}
    return {row["name"]: row for row in entries if row["name"] in names}, state["gaps"]


def _next_hop(record, version, scope, interfaces, result, index, used):
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
    hops = []
    for hop_index, hop in enumerate(raw):
        if not isinstance(hop, dict):
            _gap(result, "invalid_route_next_hop", record_index=index, next_hop_index=hop_index)
            continue
        if hop.get("vrf") != scope:
            _gap(result, "next_hop_scope_not_proven", record_index=index, next_hop_index=hop_index)
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
                continue
            else:
                entry[f"{AF[version]}:address"] = str(parsed)
        name = hop.get("ifname")
        if name is not None:
            if isinstance(name, str) and name in interfaces:
                entry["outgoing-interface"] = name
                used.add(name)
            else:
                _gap(
                    result,
                    "outgoing_interface_reference_not_proven",
                    record_index=index,
                    next_hop_index=hop_index,
                    interface=name if isinstance(name, str) else None,
                )
        if entry:
            hops.append(entry)
    if not hops:
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
            and _identifier(record.get("rib_name"))
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
        ribs, versions, conflicts, used = {}, {}, set(), set()
        for index, record in scopes[scope]:
            if not isinstance(record, dict):
                _gap(result, "invalid_route_record", record_index=index)
                continue
            if scope is None:
                _gap(result, "route_scope_not_observed", record_index=index)
                continue
            name = record.get("rib_name")
            if not _identifier(name):
                _gap(result, "native_rib_name_not_observed", record_index=index)
                continue
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
            hop = _next_hop(record, version, scope, interfaces, result, index, used)
            if hop is not None:
                entry["next-hop"] = hop
            ribs.setdefault(name, []).append(entry)
        for name in sorted(conflicts):
            ribs.pop(name, None)
            _gap(result, "native_rib_address_family_conflict", rib_name=name)
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
    }
    if invalid:
        result["gaps"] = [{"code": invalid}]
    return result
