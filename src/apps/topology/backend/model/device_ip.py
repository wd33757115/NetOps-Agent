"""Observed RFC 8344 IP/neighbor, RFC 8347 VRRP and RFC 8529 NI data in a partial tree.

Names, scope, address families and keys require evidence.  An interface whose
addresses all lie in one named VRF is bound to that network instance; different
VRFs are never folded into one native interface.  No interface type, protocol
version, configured priority, defaults, statistics or time is invented.  Strict full
validation must still expose the RFC 8343 mandatory gaps and any unobserved
mandatory VRRP version.
"""

from __future__ import annotations

import ipaddress
import re
from collections import defaultdict
from copy import deepcopy

IP = "ietf-ip:"
VRRP = "ietf-vrrp:"
NI = "ietf-network-instance:"


def _text(value):
    return (
        isinstance(value, str)
        and bool(value)
        and value == value.strip()
        and not any(ord(char) < 32 or ord(char) == 127 for char in value)
    )


def _address(value):
    if not isinstance(value, str) or "%" in value:
        raise ValueError("address must be unzoned text")
    return ipaddress.ip_address(value)


def _gap(result, code, **fields):
    gap = {"code": code, **fields}
    if gap not in result["gaps"]:
        result["gaps"].append(gap)


def _facts(observation, result):
    if not isinstance(observation, dict):
        _gap(result, "invalid_observation")
        return {}, None, {}
    data = observation.get("data", observation)
    if not isinstance(data, dict) or not isinstance(data.get("facts", data), dict):
        _gap(result, "invalid_observation_data")
        return {}, None, {}
    capabilities = data.get("capabilities")
    if "capabilities" in data and capabilities is None:
        capabilities = {}
    return data.get("facts", data), capabilities, data.get("fact_metadata", {})


def _records(facts, capabilities, fact_metadata, cap, result):
    if capabilities is not None:
        status = capabilities.get(cap) if isinstance(capabilities, dict) else None
        if not isinstance(status, str) or status not in {"complete", "partial"}:
            _gap(result, "device_ip_capability_unusable", capability=cap)
            return []
        if status == "partial":
            _gap(result, "device_ip_collection_partial", capability=cap)
    # Typed facts remain lists in an Observation.  Transport evidence therefore
    # lives beside them rather than inside a record wrapper.  A partial parser
    # result alone can still contain usable rows; transport loss cannot prove
    # that omitted rows would not conflict with the observed keys or scopes.
    metadata = fact_metadata.get(cap) if isinstance(fact_metadata, dict) else None
    warnings = metadata.get("warnings", []) if isinstance(metadata, dict) else []
    if isinstance(warnings, list) and "collection_incomplete" in warnings:
        _gap(result, "device_ip_collection_unusable", capability=cap)
        return []
    value = facts.get(cap)
    if value is None:
        _gap(result, "device_ip_not_observed", capability=cap)
        return []
    if isinstance(value, dict):
        status = value.get("status")
        metadata = value.get("parse_metadata", {})
        warnings = metadata.get("warnings", []) if isinstance(metadata, dict) else None
        if "status" in value and (
            not isinstance(status, str) or status not in {"complete", "partial"}
        ):
            _gap(result, "device_ip_capability_unusable", capability=cap)
            return []
        wrapper_warnings = value.get("warnings", [])
        if (
            not isinstance(warnings, list)
            or not isinstance(wrapper_warnings, list)
            or "collection_incomplete" in warnings
            or "collection_incomplete" in wrapper_warnings
            or ("collection_incomplete" in value and value["collection_incomplete"] is not False)
            or (value.get("invalid_structure", False) is not False)
        ):
            _gap(result, "device_ip_collection_unusable", capability=cap)
            return []
        value = value.get("records")
    if not isinstance(value, list):
        _gap(result, "invalid_device_ip_records", capability=cap)
        return []
    return value


def _ip_rows(records, result, blocked):
    groups = defaultdict(list)
    scopes = defaultdict(set)
    for index, row in enumerate(records):
        try:
            if not isinstance(row, dict) or not _text(row.get("ifname")):
                raise ValueError("invalid interface")
            name = row["ifname"]
            if _text(row.get("vrf")):
                scopes[name].add(row["vrf"])
            address = _address(row.get("address"))
            prefix = row.get("prefix_len")
            if (
                not _text(row.get("vrf"))
                or type(prefix) is not int
                or not (0 <= prefix <= address.max_prefixlen)
            ):
                raise ValueError("invalid scope or prefix")
            groups[(name, str(address))].append(
                {
                    "ifname": name,
                    "vrf": row["vrf"],
                    "address": address,
                    "prefix_len": prefix,
                    "network": ipaddress.ip_network(f"{address}/{prefix}", strict=False),
                }
            )
        except (ValueError, TypeError):
            _gap(result, "invalid_ip_address_fact", record_index=index)
            if isinstance(row, dict) and _text(row.get("ifname")):
                blocked.add(row["ifname"])
    for name, vrfs in scopes.items():
        if len(vrfs) > 1:
            blocked.add(name)
            _gap(result, "interface_multiple_vrf_scopes", interface=name, scopes=sorted(vrfs))
    valid = []
    for (name, address), rows in sorted(groups.items()):
        if len(rows) > 1:
            _gap(result, "duplicate_ip_address_fact", interface=name, address=address)
        elif name not in blocked:
            valid.append(rows[0])
    return valid


def _ensure_interface(name, interfaces, result):
    if name not in interfaces:
        interfaces[name] = {"name": name}
        _gap(result, "interface_state_not_observed", interface=name)
        _gap(result, "interface_type_unmapped", interface=name, field="type")
        _gap(result, "oper_status_not_observed_or_invalid", interface=name, field="oper-status")
        _gap(
            result,
            "mandatory_statistics_not_observed",
            interface=name,
            field="statistics/discontinuity-time",
        )
    return interfaces[name]


def _arp_interface(row, address, ip_rows, blocked):
    direct = row.get("ifname")
    if direct is not None and not _text(direct):
        return None
    if direct in blocked:
        return None
    candidates = {
        ip["ifname"]
        for ip in ip_rows
        if ip["vrf"] == row["vrf"] and ip["address"].version == address.version
    }
    if direct in candidates:
        return direct
    if direct in {ip["ifname"] for ip in ip_rows}:
        return None  # Explicitly names a real L3 interface in a different scope/AF.
    containing = {
        ip["ifname"]
        for ip in ip_rows
        if ip["vrf"] == row["vrf"]
        and ip["address"].version == address.version
        and address in ip["network"]
    }
    return next(iter(containing)) if len(containing) == 1 else None


def _neighbors(records, ip_rows, interfaces, result, blocked):
    groups = defaultdict(list)
    for index, row in enumerate(records):
        try:
            if not isinstance(row, dict) or not _text(row.get("vrf")):
                raise ValueError("invalid scope")
            address = _address(row.get("ip"))
            name = _arp_interface(row, address, ip_rows, blocked)
            if name is None:
                _gap(result, "neighbor_interface_scope_unproven_or_ambiguous", record_index=index)
                continue
            key = (name, address.version, str(address))
            mac = row.get("mac")
            if not isinstance(mac, str) or not re.fullmatch(
                r"[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}", mac
            ):
                groups[key].append(None)
                raise ValueError("invalid MAC")
            entry = {"ip": str(address), "link-layer-address": mac.lower()}
            if isinstance(row.get("type"), str) and row["type"] in {"dynamic", "static"}:
                entry["origin"] = row["type"]
            else:
                _gap(result, "neighbor_origin_not_observed", interface=name, address=str(address))
            groups[key].append(entry)
        except (ValueError, TypeError):
            _gap(result, "invalid_neighbor_fact", record_index=index)
    for (name, family, address), entries in sorted(groups.items()):
        if len(entries) != 1 or entries[0] is None:
            _gap(result, "duplicate_or_invalid_neighbor_fact", interface=name, address=address)
            continue
        target = _ensure_interface(name, interfaces, result).setdefault(IP + f"ipv{family}", {})
        target.setdefault("neighbor", []).append(entries[0])


def _version(value):
    if type(value) is int and value in {2, 3}:
        return VRRP + f"vrrp-v{value}"
    if isinstance(value, str) and value in {
        "2",
        "3",
        "vrrp-v2",
        "vrrp-v3",
        VRRP + "vrrp-v2",
        VRRP + "vrrp-v3",
    }:
        return VRRP + ("vrrp-v2" if value.endswith("2") else "vrrp-v3")
    return None


def _vrrp(records, ip_rows, interfaces, result, blocked):
    groups = defaultdict(list)
    bad_instances = set()
    for index, row in enumerate(records):
        try:
            if not isinstance(row, dict) or not _text(row.get("ifname")):
                raise ValueError("invalid interface")
            name, vrid = row["ifname"], row.get("vrid")
            if type(vrid) is not int or not 1 <= vrid <= 255:
                raise ValueError("invalid VRID")
            values = row.get("virtual_addresses")
            if not isinstance(values, list) or not 1 <= len(values) <= 16:
                raise ValueError("invalid virtual addresses")
            virtuals = [_address(value) for value in values]
            families = {address.version for address in virtuals}
            if len(families) != 1 or len({str(address) for address in virtuals}) != len(virtuals):
                raise ValueError("mixed or duplicate virtual addresses")
            family = next(iter(families))
            if family == 6 and (len(virtuals) > 2 or not virtuals[0].is_link_local):
                raise ValueError(
                    "IPv6 VRRP requires first link-local address and at most two addresses"
                )
            scopes = {
                ip["vrf"]
                for ip in ip_rows
                if ip["ifname"] == name and ip["address"].version == family
            }
            if name in blocked or len(scopes) != 1 or ("vrf" in row and row["vrf"] not in scopes):
                _gap(
                    result, "vrrp_interface_scope_unproven_or_ambiguous", interface=name, vrid=vrid
                )
                bad_instances.add((name, vrid))
                continue
            entry = {
                "vrid": vrid,
                f"virtual-ipv{family}-addresses": {
                    f"virtual-ipv{family}-address": [
                        {f"ipv{family}-address": str(address)} for address in virtuals
                    ]
                },
            }
            state = row.get("state")
            if isinstance(state, str) and state.removeprefix(VRRP) in {
                "initialize",
                "backup",
                "master",
            }:
                entry["state"] = VRRP + state.removeprefix(VRRP)
            else:
                _gap(
                    result,
                    "vrrp_state_not_observed_or_invalid",
                    interface=name,
                    vrid=vrid,
                    field="state",
                )
            version = _version(row.get("version"))
            if version is None:
                if row.get("version") is not None:
                    raise ValueError("invalid observed VRRP version")
                _gap(
                    result,
                    "mandatory_vrrp_version_not_observed",
                    interface=name,
                    vrid=vrid,
                    field="version",
                )
            elif family == 6 and version != VRRP + "vrrp-v3":
                raise ValueError("IPv6 requires VRRPv3")
            else:
                entry["version"] = version
            if row.get("last_adv_source") is not None:
                try:
                    source = _address(row["last_adv_source"])
                    if source.version != family:
                        raise ValueError("wrong source address family")
                    entry["last-adv-source"] = str(source)
                except (ValueError, TypeError):
                    _gap(result, "vrrp_last_adv_source_invalid", interface=name, vrid=vrid)
            groups[(name, family, vrid)].append(entry)
        except (ValueError, TypeError):
            _gap(result, "invalid_vrrp_fact", record_index=index)
            if isinstance(row, dict) and _text(row.get("ifname")) and type(row.get("vrid")) is int:
                bad_instances.add((row["ifname"], row["vrid"]))
    for (name, family, vrid), entries in sorted(groups.items()):
        if len(entries) != 1 or (name, vrid) in bad_instances:
            _gap(result, "duplicate_or_invalid_vrrp_instance", interface=name, vrid=vrid)
            continue
        target = _ensure_interface(name, interfaces, result).setdefault(IP + f"ipv{family}", {})
        target.setdefault(VRRP + "vrrp", {}).setdefault("vrrp-instance", []).append(entries[0])


def enrich_device_ip(projected, observation):
    """Copy an RFC 8343 partial projection and add current IP/ARP/VRRP facts.

    Input Observation uses ``data.facts`` plus ``data.capabilities``; extracted
    facts without capability metadata are also accepted.  Failed/unsupported
    capabilities carry no exported facts.  Existing owned IPv4/IPv6 containers
    are rebuilt from this observation, so a failed new collection cannot retain
    old IP data.  Exact explicit interface/VRF/address facts bind the scope.
    Missing interface observations permit only a name-only partial interface,
    with mandatory gaps.  Duplicate interfaces and multiple VRFs refuse binding.
    ``metadata.interface_scopes`` is application evidence, never a YANG leaf.
    """
    result = deepcopy(projected) if isinstance(projected, dict) else {}
    result.setdefault("gaps", [])
    result.setdefault("metadata", {})
    if not isinstance(result["gaps"], list) or not isinstance(result["metadata"], dict):
        return {
            "tree": {},
            "metadata": {"completeness": "partial"},
            "gaps": [{"code": "invalid_device_projection"}],
        }
    tree = result.get("tree")
    container = tree.get("ietf-interfaces:interfaces") if isinstance(tree, dict) else None
    rows = container.get("interface") if isinstance(container, dict) else None
    if not isinstance(rows, list) or any(
        not isinstance(row, dict) or not _text(row.get("name")) for row in rows
    ):
        _gap(result, "invalid_device_projection")
        return result
    interfaces, blocked = {}, set()
    for row in rows:
        name = row["name"]
        row.pop(IP + "ipv4", None)
        row.pop(IP + "ipv6", None)
        if name in interfaces:
            blocked.add(name)
            _gap(result, "duplicate_interface", interface=name)
        else:
            interfaces[name] = row
    blocked.update(
        gap["interface"]
        for gap in result["gaps"]
        if isinstance(gap, dict)
        and gap.get("code") == "duplicate_interface"
        and _text(gap.get("interface"))
    )
    for name in blocked:
        interfaces.pop(name, None)
    facts, capabilities, fact_metadata = _facts(observation, result)
    ip_rows = _ip_rows(_records(facts, capabilities, fact_metadata, "ip", result), result, blocked)
    for row in ip_rows:
        target = _ensure_interface(row["ifname"], interfaces, result).setdefault(
            IP + f"ipv{row['address'].version}", {}
        )
        target.setdefault("address", []).append(
            {"ip": str(row["address"]), "prefix-length": row["prefix_len"]}
        )
    _neighbors(
        _records(facts, capabilities, fact_metadata, "arp", result),
        ip_rows,
        interfaces,
        result,
        blocked,
    )
    _vrrp(
        _records(facts, capabilities, fact_metadata, "vrrp", result),
        ip_rows,
        interfaces,
        result,
        blocked,
    )
    # RFC 8529: an interface whose addresses all belong to one named VRF is bound to that
    # network instance; the global table ("default" in the facts) is not an NI entry.
    # The mandatory root-type case (vrf-root and its mounted routing) needs the VRF's own
    # data, which is not collected: an empty container would not establish it.
    tree.pop(NI + "network-instances", None)
    instances = set()
    for name, row in interfaces.items():
        row.pop(NI + "bind-ni-name", None)
        scopes = {ip["vrf"] for ip in ip_rows if ip["ifname"] == name}
        if len(scopes) == 1 and (scope := next(iter(scopes))) != "default":
            row[NI + "bind-ni-name"] = scope
            instances.add(scope)
    if instances:
        tree[NI + "network-instances"] = {
            "network-instance": [{"name": vrf} for vrf in sorted(instances)]
        }
        for vrf in sorted(instances):
            _gap(result, "network_instance_root_not_observed", network_instance=vrf)
    container["interface"] = [interfaces[name] for name in sorted(interfaces)]
    result["metadata"].update(
        standards=["RFC 8343", "RFC 8344", "RFC 8347", "RFC 8529"],
        completeness="partial",
        full_validation="not_performed",
        interface_scopes={row["ifname"]: row["vrf"] for row in ip_rows},
    )
    return result
