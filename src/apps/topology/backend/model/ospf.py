"""OSPF observation binding, separate from native topology/device YANG trees.

RFC 8346 leaves metric1/metric2 have no endpoint or IGP/TE assignment.  For a
SIMAP bidirectional link this helper projects metric1 only under the declared
policy that every termination has a unique observed interface output cost and
all costs agree.  Asymmetry and missing evidence remain gaps; metric2 is never
used as a reverse-direction cost.  This does not build an OSPF LSDB or prove
adjacency, forwarding availability, or a complete RFC 9129 device instance.
"""

from __future__ import annotations

import ipaddress
from collections import defaultdict
from copy import deepcopy


def _text(value):
    return (
        isinstance(value, str)
        and bool(value.strip())
        and value == value.strip()
        and not any(ord(char) < 32 or ord(char) == 127 for char in value)
    )


def _ipv4(value):
    if not isinstance(value, str):
        raise ValueError("invalid IPv4")
    return str(ipaddress.IPv4Address(value))


def _pid(value):
    if not isinstance(value, str) or not value.isascii() or not value.isdigit():
        raise ValueError("invalid process ID")
    if int(value) < 1:
        raise ValueError("invalid process ID")
    return str(int(value))


def _area(value):
    if isinstance(value, str) and value.isascii() and value.isdigit():
        return str(ipaddress.IPv4Address(int(value)))
    return _ipv4(value)


def _summary(facts, capability, gaps):
    value = facts.get(capability)
    if value is None:
        gaps.append({"code": "ospf_capability_not_observed", "capability": capability})
        return {}
    warnings = []
    if isinstance(value, dict) and "summary" in value:
        if "status" in value and (
            not isinstance(value["status"], str)
            or value["status"] not in {"complete", "partial", "empty_verified"}
        ):
            gaps.append({"code": "ospf_capability_unusable", "capability": capability})
            return {}
        warnings = value.get("warnings", [])
        value = value["summary"]
    if not isinstance(value, dict):
        gaps.append({"code": "invalid_ospf_summary", "capability": capability})
        return {"invalid_structure": True}
    metadata = value.get("parse_metadata", {})
    if not isinstance(metadata, dict) or not isinstance(warnings, list):
        gaps.append({"code": "invalid_ospf_parse_metadata", "capability": capability})
        return {**value, "invalid_structure": True}
    summary_warnings = metadata.get("warnings", [])
    if not isinstance(summary_warnings, list):
        gaps.append({"code": "invalid_ospf_parse_metadata", "capability": capability})
        return {**value, "invalid_structure": True}
    if "collection_incomplete" in [*warnings, *summary_warnings] or (
        "collection_incomplete" in value and value["collection_incomplete"] is not False
    ):
        gaps.append({"code": "ospf_collection_incomplete", "capability": capability})
        return {**value, "invalid_structure": True}
    return value


def _gates(summaries, gaps):
    """Normalize the parser's veto markers; malformed markers fail closed."""
    selectors, processes, blocked = [], set(), False
    for capability, summary in summaries.items():
        if summary.get("invalid_structure", False) is not False:
            gaps.append({"code": "ospf_invalid_structure", "capability": capability})
            blocked = True
        try:
            invalid_processes = summary.get("invalid_processes", [])
            invalid_interfaces = summary.get("invalid_interfaces", [])
            if not isinstance(invalid_processes, list) or not isinstance(invalid_interfaces, list):
                raise ValueError("invalid gates")
            processes.update(_pid(pid) for pid in invalid_processes)
            for record in invalid_interfaces:
                if (
                    not isinstance(record, dict)
                    or not record
                    or set(record) - {"process_id", "address", "ifname"}
                ):
                    raise ValueError("invalid interface gate")
                selector = {}
                for key, value in record.items():
                    if key == "process_id":
                        selector[key] = _pid(value)
                    elif key == "address":
                        selector[key] = _ipv4(value)
                    elif _text(value):
                        selector[key] = value
                    else:
                        raise ValueError("invalid interface gate")
                selectors.append(selector)
        except (ValueError, TypeError):
            gaps.append({"code": "invalid_ospf_veto_markers", "capability": capability})
            blocked = True
    return selectors, processes, blocked


def _matches(row, selectors):
    return any(all(row.get(key) == value for key, value in item.items()) for item in selectors)


def _records(summary, name, capability, gaps):
    records = summary.get(name, [])
    if not isinstance(records, list):
        gaps.append({"code": "invalid_ospf_records", "capability": capability, "field": name})
        return [], True
    return records, False


def _ip_scope(records, row):
    """An exact fact interface/address match, never an address-in-subnet guess."""
    if not isinstance(records, list):
        return None
    matches = []
    for record in records:
        if not isinstance(record, dict) or record.get("ifname") != row.get("ifname"):
            continue
        try:
            same_address = _ipv4(record.get("address")) == row["address"]
        except ValueError:
            continue
        if same_address:
            matches.append(record)
    if len(matches) != 1 or not _text(matches[0].get("vrf")):
        return None
    return matches[0]["vrf"]


def _device(facts):
    result = {"router_ids": {}, "interfaces": [], "processes": [], "observations": [], "gaps": []}
    gaps = result["gaps"]
    if not isinstance(facts, dict):
        gaps.append({"code": "invalid_device_facts"})
        return result
    summaries = {cap: _summary(facts, cap, gaps) for cap in ("ospf_interfaces", "ospf_process")}
    selectors, invalid_processes, blocked = _gates(summaries, gaps)
    raw_processes, bad = _records(summaries["ospf_process"], "processes", "ospf_process", gaps)
    blocked |= bad
    result["processes"] = deepcopy(raw_processes)
    groups = defaultdict(list)
    for capability, summary in summaries.items():
        records, bad = _records(summary, "interfaces", capability, gaps)
        blocked |= bad
        for index, record in enumerate(records):
            result["observations"].append({"capability": capability, "record": deepcopy(record)})
            try:
                if not isinstance(record, dict):
                    raise ValueError("invalid interface")
                row = deepcopy(record)
                row.update(
                    process_id=_pid(row.get("process_id")), address=_ipv4(row.get("address"))
                )
                cost = row.get("cost")
                if type(cost) is not int or not 0 <= cost <= 65535:
                    raise ValueError("invalid cost")
                if "ifname" in row and not _text(row["ifname"]):
                    raise ValueError("invalid interface name")
                if "area" in row:
                    row["area"] = _area(row["area"])
                if "router_id" in row:
                    row["router_id"] = _ipv4(row["router_id"])
                if "vrf" in row and not _text(row["vrf"]):
                    raise ValueError("invalid scope")
                groups[(row["process_id"], row["address"])].append((capability, row))
            except (ValueError, TypeError):
                gaps.append(
                    {
                        "code": "invalid_ospf_interface",
                        "capability": capability,
                        "record_index": index,
                    }
                )
                selector = {}
                if isinstance(record, dict):
                    for key, converter in (("process_id", _pid), ("address", _ipv4)):
                        try:
                            selector[key] = converter(record.get(key))
                        except (ValueError, TypeError):
                            pass
                    if _text(record.get("ifname")):
                        selector["ifname"] = record["ifname"]
                if selector:
                    selectors.append(selector)
                else:
                    blocked = True
    processes = {}
    for index, raw in enumerate(raw_processes):
        try:
            if not isinstance(raw, dict):
                raise ValueError("invalid process")
            process = deepcopy(raw)
            pid = _pid(process.get("process_id"))
            process.update(process_id=pid, router_id=_ipv4(process.get("router_id")))
            if "vrf" in process and not _text(process["vrf"]):
                raise ValueError("invalid scope")
            if pid in processes:
                invalid_processes.add(pid)
                gaps.append({"code": "duplicate_ospf_process", "process_id": pid})
            processes[pid] = process
        except (ValueError, TypeError):
            gaps.append({"code": "invalid_ospf_process", "record_index": index})
            try:
                invalid_processes.add(_pid(raw.get("process_id")))
            except (ValueError, TypeError, AttributeError):
                blocked = True
    if blocked:
        gaps.append({"code": "ospf_device_projection_blocked"})
        return result
    ambiguous_processes = set()
    for (pid, address), records in sorted(groups.items()):
        entry = {"process_id": pid, "address": address, "usable": False}
        capabilities = [capability for capability, _ in records]
        if len(capabilities) != len(set(capabilities)):
            gaps.append({"code": "duplicate_ospf_interface", "process_id": pid, "address": address})
            ambiguous_processes.add(pid)
            continue
        conflict = False
        for field in ("ifname", "cost", "area", "router_id", "vrf"):
            values = {row[field] for _, row in records if field in row}
            if len(values) > 1:
                conflict = True
            elif values:
                entry[field] = next(iter(values))
        if conflict:
            gaps.append(
                {"code": "conflicting_ospf_interface", "process_id": pid, "address": address}
            )
            ambiguous_processes.add(pid)
            continue
        entry["sources"] = sorted(capabilities)
        process = processes.get(pid)
        if process and "router_id" in entry and entry["router_id"] != process["router_id"]:
            invalid_processes.add(pid)
        if pid in invalid_processes or _matches(entry, selectors):
            gaps.append({"code": "ospf_interface_vetoed", "process_id": pid, "address": address})
            ambiguous_processes.add(pid)
            continue
        if "ifname" not in entry:
            gaps.append(
                {"code": "ospf_interface_name_unproven", "process_id": pid, "address": address}
            )
            ambiguous_processes.add(pid)
        else:
            scope = _ip_scope(facts.get("ip"), entry)
            if scope is None:
                entry.pop("vrf", None)
                gaps.append(
                    {
                        "code": "ospf_ip_scope_unproven_or_ambiguous",
                        "process_id": pid,
                        "ifname": entry["ifname"],
                        "address": address,
                    }
                )
                ambiguous_processes.add(pid)
            elif ("vrf" in entry and entry["vrf"] != scope) or (
                process and "vrf" in process and process["vrf"] != scope
            ):
                gaps.append(
                    {
                        "code": "conflicting_ospf_scope",
                        "process_id": pid,
                        "ifname": entry["ifname"],
                        "address": address,
                    }
                )
                invalid_processes.add(pid)
            else:
                entry.update(vrf=scope, usable=True)
        result["interfaces"].append(entry)
    for entry in result["interfaces"]:
        if entry["process_id"] in invalid_processes:
            entry["usable"] = False
    for pid, process in sorted(processes.items()):
        if pid in invalid_processes:
            gaps.append({"code": "ospf_process_vetoed", "process_id": pid})
            continue
        entries = [row for row in result["interfaces"] if row["process_id"] == pid]
        scopes = {row["vrf"] for row in entries if row.get("usable")}
        if len(scopes) != 1 or pid in ambiguous_processes:
            gaps.append({"code": "ospf_process_scope_unproven_or_ambiguous", "process_id": pid})
            continue
        scope = next(iter(scopes))
        result["router_ids"].setdefault(scope, []).append(process["router_id"])
    result["router_ids"] = {
        scope: sorted(set(ids)) for scope, ids in sorted(result["router_ids"].items())
    }
    return result


def project_ospf(devices):
    """Bind ``{device_address: facts}`` to JSON-friendly observation sidecars.

    OSPF summaries may be direct dictionaries or serialized ``{summary: ...}``
    values.  Interface names are supplied by a parser, never inferred from IP
    facts.  Every scope requires exactly one explicit typed IP ``ifname/address/
    vrf`` fact.  Process router IDs are bound only to one demonstrated scope;
    process IDs are local to their device.  Original process/interface records,
    including refused costs, stay in ``processes`` / ``observations`` outside
    any native YANG tree.  The caller selects the snapshot and usable capability
    facts; this pure helper does not fetch data or associate device YANG trees.
    """
    if not isinstance(devices, dict):
        return {"devices": {}, "gaps": [{"code": "invalid_devices"}]}
    result = {"devices": {}, "gaps": []}
    for address, facts in devices.items():
        if not _text(address):
            result["gaps"].append({"code": "invalid_device_address"})
            continue
        result["devices"][address] = _device(facts)
    return result


def link_metric(projection, refs):
    """Return a common-cost metric1 (RFC 7951 uint64 string), gaps and evidence.

    Each ref contains device ``address``, explicit ``vrf``, canonical ``ifname``
    and interface ``address_ip``.  All modeled terminations, including hosts,
    must be supplied.  Every one must match exactly one usable OSPF observation.
    The interface name comes from application IP facts: RFC 8346's TP choice
    makes interface-name mutually exclusive with its existing ip-address leaf.
    This evidence name is not inserted into that native YANG container.
    Missing/duplicate/ambiguous terminations or asymmetric costs omit metric1;
    equal local NI names, area IDs or process IDs across devices are not required.  A zero
    loopback cost remains an observation but is not a transmitting link cost.
    """
    result = {"gaps": [], "evidence": []}
    gaps = result["gaps"]
    if not isinstance(refs, list) or len(refs) < 2:
        gaps.append({"code": "link_requires_two_terminations"})
        return result
    devices = projection.get("devices") if isinstance(projection, dict) else None
    if not isinstance(devices, dict):
        gaps.append({"code": "invalid_ospf_projection"})
        return result
    seen, costs = set(), set()
    for index, ref in enumerate(refs):
        try:
            if not isinstance(ref, dict) or not all(
                _text(ref.get(key)) for key in ("address", "vrf", "ifname")
            ):
                raise ValueError("invalid termination")
            address_ip = _ipv4(ref.get("address_ip"))
        except (ValueError, TypeError):
            gaps.append({"code": "invalid_link_termination", "termination_index": index})
            continue
        identity = (ref["address"], ref["vrf"], ref["ifname"], address_ip)
        if identity in seen:
            gaps.append({"code": "duplicate_link_termination", "termination_index": index})
            continue
        seen.add(identity)
        device = devices.get(ref["address"])
        records = device.get("interfaces") if isinstance(device, dict) else None
        matches = (
            [
                row
                for row in records
                if isinstance(row, dict)
                and row.get("ifname") == ref["ifname"]
                and row.get("address") == address_ip
            ]
            if isinstance(records, list)
            else []
        )
        if (
            len(matches) != 1
            or matches[0].get("usable") is not True
            or matches[0].get("vrf") != ref["vrf"]
        ):
            gaps.append(
                {"code": "ospf_interface_unavailable_or_ambiguous", "termination_index": index}
            )
            continue
        row = matches[0]
        cost = row.get("cost")
        if type(cost) is not int or not 1 <= cost <= 65535:
            gaps.append({"code": "invalid_ospf_link_cost", "termination_index": index})
            continue
        costs.add(cost)
        evidence = {**ref, "address_ip": address_ip, "process_id": row["process_id"], "cost": cost}
        if "area" in row:
            evidence["area"] = row["area"]
        result["evidence"].append(evidence)
    if len(costs) > 1:
        gaps.append({"code": "asymmetric_ospf_cost", "costs": sorted(costs)})
    if not gaps and len(result["evidence"]) == len(refs):
        result["metric1"] = str(next(iter(costs)))
    return result
