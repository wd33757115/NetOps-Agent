"""Whole-table facts with vetoes, assembled from rule-governance rows.

Trunk and switchport facts are summaries: the ports that are fully understood, the
ports whose observation is damaged (``invalid_ports``: a local veto) and whether
anything in the output could not be attributed to a port (``invalid_structure``: a
global veto). A veto is itself a fact, so such a result is partial, never failed.
Cut-off output is a global veto. These functions work on the check items' same-name
Values; the templates own the CLI text.
"""

from __future__ import annotations

import ipaddress
import re
from collections import defaultdict

from .tiers.common import interface, vlan


def vid_range(value):
    """Canonical VID list/range text; an explicit ``none`` has an empty set.

    ALL is accepted for the IOS switchport output that uses that literal.
    Overlaps, duplicates, reversed ranges and reserved IDs are rejected.
    """
    value = value.strip()
    if value.lower() == "none":
        return ""
    if value.lower() == "all":
        return "1-4094"
    if not value or not re.fullmatch(r"[0-9]+(?:-[0-9]+)?(?:,[0-9]+(?:-[0-9]+)?)*", value):
        raise ValueError("invalid_vlan_range")
    intervals = []
    for item in value.split(","):
        bounds = item.split("-")
        start, end = vlan(bounds[0]), vlan(bounds[-1])
        if start > end:
            raise ValueError("reversed_vlan_range")
        intervals.append((start, end))
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            raise ValueError("overlapping_vlan_range")
        if merged and start == merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return ",".join(str(start) if start == end else f"{start}-{end}" for start, end in merged)


class _Vetoes:
    """Collects local (per port) and global vetoes with their warnings."""

    def __init__(self, result, structure):
        self.result, self.ports, self.structure = result, set(), structure

    def reject(self, lines, reason, port=None):
        self.result.rejected_count += 1
        self.result.warnings.append(f"line:{lines[0] if lines else 0}:{reason}")
        if port is None:
            self.structure = True
        else:
            self.ports.add(port)

    def finish(self, ports, evidence):
        result, summary = self.result, {}
        kept = [port for port in ports if port["ifname"] not in self.ports]
        if kept:
            summary["ports"] = kept
        if self.ports:
            summary["invalid_ports"] = sorted(self.ports)
        if self.structure:
            summary["invalid_structure"] = True
        result.summary = summary
        result.sources = [
            {"lines": sorted(set(evidence[port["ifname"]])), "raw": {"port": port["raw_name"]}}
            for port in kept
        ]
        defects = self.ports or self.structure or result.rejected_count
        result.status = "partial" if defects else "complete"
        return result


TRUNK_SECTIONS = {
    "ALLOWED_VLANS": "trunk_vlans",
    "ACTIVE_VLANS": "active_vlans",
    "FORWARDING_VLANS": "forwarding_vlans",
}


def trunk(result, parsed, family, incomplete):
    """IOS ``show interfaces trunk``: four tables of the same ports, kept separate."""
    vetoes = _Vetoes(result, bool(parsed.rejected or parsed.problems or incomplete))
    tables = {"base": {}, **{key: {} for key in TRUNK_SECTIONS.values()}}
    evidence = defaultdict(list)
    for row, lines in zip(parsed.rows, parsed.lines):
        name = None
        try:
            port = interface(row["PORT"], family)
            if port.kind not in {"ethernet", "lag"}:
                raise ValueError("invalid_trunk_interface")
            name = port.name
            section = (
                "base"
                if row.get("MODE")
                else next((key for field, key in TRUNK_SECTIONS.items() if row.get(field)), None)
            )
            if section is None:
                raise ValueError("unrecognized_trunk_port_row")
            if name in tables[section]:
                raise ValueError("duplicate_trunk_port")
            if section == "base":
                if row.get("ENCAPSULATION") != "802.1q" or row.get("STATUS") != "trunking":
                    raise ValueError("unrecognized_trunk_port_row")
                value = {
                    "ifname": name,
                    "raw_name": row["PORT"],
                    "l2_mode": "trunk",
                    "encapsulation": "dot1q",
                    "native_vlan": vlan(row["NATIVE_VLAN"]),
                }
            else:
                field = next(f for f, key in TRUNK_SECTIONS.items() if key == section)
                value = vid_range(row[field])
            tables[section][name] = value
            evidence[name].extend(lines)
        except ValueError as exc:
            vetoes.reject(lines, str(exc), name)
    if parsed.problems:
        # A missing table cannot be filled in from the others.
        result.rejected_count += len(parsed.problems)
        result.summary = {"invalid_structure": True}
        if vetoes.ports:
            result.summary["invalid_ports"] = sorted(vetoes.ports)
        result.status = "partial"
        return result
    base = tables["base"]
    for key in TRUNK_SECTIONS.values():
        for name in sorted(set(base) ^ set(tables[key])):
            vetoes.reject([0], f"trunk_port_set_mismatch:{key}:{name}", name)
    ports = [
        {**record, **{key: tables[key][name] for key in TRUNK_SECTIONS.values()}}
        for name, record in base.items()
        if all(name in tables[key] for key in TRUNK_SECTIONS.values())
    ]
    return vetoes.finish(ports, evidence)


# Switchport field Values -> the field names the projection checks.
SWITCHPORT_FIELDS = {
    "SWITCHPORT": "Switchport",
    "ADMIN_MODE": "Administrative Mode",
    "OPER_MODE": "Operational Mode",
    "ADMIN_ENCAPSULATION": "Administrative Trunking Encapsulation",
    "OPER_ENCAPSULATION": "Operational Trunking Encapsulation",
    "ACCESS_VLAN": "Access Mode VLAN",
    "NATIVE_VLAN": "Trunking Native Mode VLAN",
    "ADMIN_NATIVE_TAGGING": "Administrative Native VLAN tagging",
    "OPER_NATIVE_TAGGING": "Operational Native VLAN tagging",
    "TRUNK_VLANS": "Trunking VLANs Enabled",
}
SWITCHPORT_REQUIRED = {
    "Switchport",
    "Administrative Mode",
    "Operational Mode",
    "Access Mode VLAN",
    "Trunking Native Mode VLAN",
    "Trunking VLANs Enabled",
}


def _switchport_vid(value):
    match = re.fullmatch(r"([0-9]+)(?: \([^()]+\))?", value)
    if not match:
        raise ValueError("invalid_switchport_vlan")
    return vlan(match[1])


def _switchport_port(name, fields, family):
    """Administrative and operational modes stay separate; an idle dynamic port's default
    access VLAN does not establish access mode or a PVID."""
    if not SWITCHPORT_REQUIRED <= fields.keys():
        raise ValueError("switchport_fields_missing")
    port = interface(name, family)
    if port.kind not in {"ethernet", "lag"} or fields["Switchport"] != "Enabled":
        raise ValueError("switchport_format_not_verified")
    admin, oper = fields["Administrative Mode"], fields["Operational Mode"]
    if admin not in {"dynamic auto", "dynamic desirable", "static access", "access", "trunk"}:
        raise ValueError("switchport_admin_mode_not_verified")
    trunk_mode = re.fullmatch(r"trunk(?: \(member of bundle ([^()]+)\))?", oper)
    if not trunk_mode and oper not in {"down", "static access", "access"}:
        raise ValueError("switchport_oper_mode_not_verified")
    access = _switchport_vid(fields["Access Mode VLAN"])
    native = _switchport_vid(fields["Trunking Native Mode VLAN"])
    record = {
        "ifname": port.name,
        "raw_name": name,
        "admin_mode": admin,
        "oper_mode": oper,
        "access_vlan": access,
        "native_vlan": native,
        "trunk_vlans": vid_range(fields["Trunking VLANs Enabled"]),
    }
    for field, target in (
        ("Administrative Native VLAN tagging", "admin_native_tagged"),
        ("Operational Native VLAN tagging", "native_tagged"),
    ):
        tagged = fields.get(field)
        if tagged is not None:
            if tagged not in {"enabled", "disabled"}:
                raise ValueError("switchport_native_tagging_not_verified")
            record[target] = tagged == "enabled"
    if trunk_mode:
        if fields.get("Operational Trunking Encapsulation") != "dot1q":
            raise ValueError("switchport_encapsulation_not_verified")
        record.update(l2_mode="trunk", encapsulation="dot1q", pvid=native)
        if trunk_mode[1]:
            parent = interface(trunk_mode[1], family)
            if parent.kind != "lag":
                raise ValueError("switchport_bundle_not_aggregate")
            record["lag_parent"] = parent.name
    elif oper in {"static access", "access"} or (
        oper == "down" and admin in {"static access", "access"}
    ):
        record.update(l2_mode="access", pvid=access)
    elif oper == "down" and admin == "trunk":
        if fields.get("Administrative Trunking Encapsulation") != "dot1q":
            raise ValueError("switchport_encapsulation_not_verified")
        record.update(l2_mode="trunk", encapsulation="dot1q", pvid=native)
    return record


def switchport(result, parsed, family, incomplete):
    """IOS ``show interfaces switchport``: one block per port."""
    vetoes = _Vetoes(result, bool(parsed.rejected or parsed.problems or incomplete))
    names, ports, evidence = set(), [], defaultdict(list)
    for row, lines in zip(parsed.rows, parsed.lines):
        name = None
        try:
            if not row.get("NAME"):
                raise ValueError("switchport_block_header_invalid")
            identity = interface(row["NAME"], family)
            if identity.kind not in {"ethernet", "lag"}:
                raise ValueError("switchport_interface_not_verified")
            name = identity.name
            if name in names:
                raise ValueError("duplicate_switchport_interface")
            names.add(name)
            if row.get("BAD_FIELD"):
                raise ValueError("unrecognized_switchport_field_or_value")
            fields = {}
            for value_name, key in SWITCHPORT_FIELDS.items():
                values = [value for value in row.get(value_name) or [] if value is not None]
                if len(values) > 1:
                    raise ValueError("duplicate_switchport_field")
                if values:
                    fields[key] = values[0]
            ports.append(_switchport_port(row["NAME"], fields, family))
            evidence[name].extend(lines)
        except ValueError as exc:
            vetoes.reject(lines, str(exc), name)
    return vetoes.finish(ports, evidence)


# OSPFv2 summaries (campus 20261003-015834 captures). The templates turn each meaningful
# line into a typed row (process header, label, area, table header, interface row, cost
# line); these functions replay the rows and the rejected lines in output order and
# apply the scope rules: a duplicate process, area or label vetoes the process, a damaged
# interface row vetoes that interface, and anything unattributable vetoes the output.
# Comware's brief table does not identify interfaces: its address observations must be
# checked against the full process output before attaching costs to an ifname. No RFC
# 8346 metric direction or missing interface cost is inferred here.

DEFAULT_COMWARE = "output:Multi-VPN-Instance is not enabled"


def _ipv4(value):
    return str(ipaddress.IPv4Address(value))


def _process_id(value):
    if not re.fullmatch(r"[0-9]+", value or "") or int(value) < 1:
        raise ValueError("invalid_ospf_process_id")
    return str(int(value))


def _area(value):
    if re.fullmatch(r"[0-9]+", value):
        if int(value) > 0xFFFFFFFF:
            raise ValueError("invalid_ospf_area")
    else:
        _ipv4(value)
    return value


def _cost(value):
    if not re.fullmatch(r"[0-9]+", value or "") or not 0 <= int(value) <= 65535:
        raise ValueError("invalid_ospf_cost")
    return int(value)


class _Ospf:
    """The summary being built, with the hand parsers' invalid markers."""

    def __init__(self, result):
        self.result, self.summary, self.sources = result, {}, []

    def reject(self, line, reason):
        self.result.rejected_count += 1
        self.result.warnings.append(f"line:{line}:{reason}")

    def invalid_interface(self, record):
        key = {k: record[k] for k in ("process_id", "address", "ifname") if k in record}
        if not key:
            self.summary["invalid_structure"] = True
        elif key not in self.summary.setdefault("invalid_interfaces", []):
            self.summary["invalid_interfaces"].append(key)

    def invalid_process(self, process_id):
        if process_id:
            if re.fullmatch(r"[0-9]+", process_id):
                process_id = str(int(process_id))
            invalid = self.summary.setdefault("invalid_processes", [])
            if process_id not in invalid:
                invalid.append(process_id)
        else:
            self.summary["invalid_structure"] = True

    def finish_interfaces(self, rows):
        invalid = self.summary.get("invalid_interfaces", [])
        invalid_processes = self.summary.get("invalid_processes", [])
        valid = []
        for row, lines in rows:
            if row["process_id"] in invalid_processes:
                continue
            if any(all(row.get(key) == value for key, value in bad.items()) for bad in invalid):
                continue
            valid.append(row)
            self.sources.append({"lines": sorted(set(lines)), "raw": {"fact": "interface"}})
        if valid:
            self.summary["interfaces"] = valid

    def done(self, incomplete, empty_verified):
        result = self.result
        result.summary, result.sources = self.summary, self.sources
        if self.summary:
            result.status = "partial" if incomplete or result.rejected_count else "complete"
        elif empty_verified and not incomplete and not result.rejected_count:
            result.status = "complete"
        else:
            result.warnings.append("unparsed_output")
        return result


def _events(parsed):
    """Rows and rejected lines in output order: (line, row or None, reason)."""
    events = [
        (lines[-1] if lines else 0, row, None) for row, lines in zip(parsed.rows, parsed.lines)
    ]
    reasons = parsed.rejected_reasons or ("template_rejected",) * len(parsed.rejected)
    events += [(number, None, reason) for number, reason in zip(parsed.rejected, reasons)]
    return sorted(events, key=lambda event: event[0])


def _nothing_read(result, parsed, incomplete):
    """An empty output: verified empty, or (cut off) no observation at all."""
    if parsed.rows or parsed.rejected or parsed.problems:
        return None
    result.status = "failed" if incomplete else "complete"
    return result


def ospf_interfaces(result, parsed, family, incomplete):
    """Brief interface costs; the IOS header and Comware sections are audited."""
    if (empty := _nothing_read(result, parsed, incomplete)) is not None:
        return empty
    ospf = _Ospf(result)
    comware = family == "comware"
    process_id = router_id = area = None
    header_lines, headers, seen, rows, areas, labels = [], set(), set(), [], set(), set()
    table = False
    for number, row, reason in _events(parsed):
        if row is None:
            ospf.reject(number, reason)
            ospf.summary["invalid_structure"] = True  # unknown syntax has no reliable key
            continue
        if comware and row.get("PROCESS_ID"):
            try:
                process_id, router_id = _process_id(row["PROCESS_ID"]), _ipv4(row["ROUTER_ID"])
                if process_id in headers:
                    ospf.invalid_process(process_id)
                    raise ValueError("duplicate_ospf_process")
                headers.add(process_id)
                area, table, header_lines = None, False, [number]
            except ValueError as exc:
                ospf.invalid_process(row["PROCESS_ID"])
                process_id = router_id = area = None
                table = False
                ospf.reject(number, str(exc))
            continue
        if comware and row.get("LABEL") and process_id:
            if process_id in labels:
                ospf.reject(number, "duplicate_ospf_interface_label")
                ospf.invalid_process(process_id)
            labels.add(process_id)
            continue
        if comware and row.get("AREA") and not row.get("ADDRESS") and process_id:
            try:
                area, table = _area(row["AREA"]), False
                if (process_id, area) in areas:
                    raise ValueError("duplicate_ospf_area")
                areas.add((process_id, area))
                header_lines = [header_lines[0], number]
            except ValueError as exc:
                ospf.invalid_process(process_id)
                ospf.reject(number, str(exc))
            continue
        if row.get("TABLE"):
            if table or (comware and (not process_id or area is None)):
                ospf.reject(number, "duplicate_or_unscoped_ospf_table")
                ospf.summary["invalid_structure"] = True
            table = True
            continue
        record = {}
        try:
            if not table or not (row.get("ADDRESS") or row.get("INTERFACE")):
                raise ValueError("unscoped_ospf_interface_row")
            if comware:
                record.update(process_id=process_id, router_id=router_id, area=area)
                record["address"] = _ipv4(row["ADDRESS"])
                if row.get("BAD_ROW"):
                    raise ValueError("invalid_ospf_interface_columns")
                record.update(cost=_cost(row["COST"]), state=row["STATE"])
                if not row["PRIORITY"].isdigit():
                    raise ValueError("invalid_ospf_priority")
                _ipv4(row["DR"])
                _ipv4(row["BDR"])
                lines = [*header_lines, number]
            else:
                record.update(
                    ifname=interface(row["INTERFACE"], family).name, raw_name=row["INTERFACE"]
                )
                if row.get("BAD_ROW"):
                    raise ValueError("invalid_ospf_interface_columns")
                record["process_id"] = _process_id(row["PROCESS_ID"])
                if not re.fullmatch(r"[0-9.]+/[0-9]+", row["ADDRESS"]):
                    raise ValueError("invalid_ospf_address_mask")
                address = ipaddress.IPv4Interface(row["ADDRESS"])
                record.update(
                    area=_area(row["AREA"]),
                    address=str(address.ip),
                    prefix_length=address.network.prefixlen,
                )
                record.update(cost=_cost(row["COST"]), state=row["STATE"])
                if not re.fullmatch(r"\d+/\d+", row["NEIGHBORS"]):
                    raise ValueError("invalid_ospf_neighbor_counts")
                lines = [number]
            key = (record["process_id"], record["address"])
            if key in seen:
                ospf.invalid_interface({"process_id": key[0], "address": key[1]})
                raise ValueError("duplicate_ospf_interface")
            seen.add(key)
            rows.append((record, lines))
        except ValueError as exc:
            if record.get("address") or record.get("ifname"):
                ospf.invalid_interface(record)
            else:
                ospf.summary["invalid_structure"] = True
            ospf.reject(number, str(exc) or "invalid_ospf_interface_row")
    if not table:
        ospf.reject(1, "ospf_table_missing")
        ospf.summary["invalid_structure"] = True
    ospf.finish_interfaces(rows)
    empty_verified = table and not rows and not result.rejected_count
    return ospf.done(incomplete, empty_verified)


def ospf_process(result, parsed, family, incomplete):
    """Router IDs, explicit scope evidence, and Comware address/ifname mappings."""
    if (empty := _nothing_read(result, parsed, incomplete)) is not None:
        return empty
    ospf = _Ospf(result)
    processes, rows, seen, areas = {}, [], set(), set()
    current = area = block = None

    def close_block():
        nonlocal block
        if block is None:
            return
        record, lines = block
        if "cost" not in record:
            ospf.reject(lines[0], "ospf_interface_cost_missing")
            ospf.invalid_interface(record)
        else:
            key = (record["process_id"], record["address"])
            if key in seen:
                ospf.reject(lines[0], "duplicate_ospf_interface")
                ospf.invalid_interface({"process_id": key[0], "address": key[1]})
            seen.add(key)
            rows.append((record, lines))
        block = None

    for number, row, reason in _events(parsed):
        if row is not None and row.get("PROCESS_ID"):
            close_block()
            try:
                pid, rid = _process_id(row["PROCESS_ID"]), _ipv4(row["ROUTER_ID"])
                if pid in processes:
                    ospf.invalid_process(pid)
                    raise ValueError("duplicate_ospf_process")
                current = {"process_id": pid, "router_id": rid}
                processes[pid] = (current, [number])
                area = None
            except ValueError as exc:
                ospf.invalid_process(row["PROCESS_ID"])
                current = None
                ospf.reject(number, str(exc))
            continue
        if current is None:
            ospf.reject(number, "unscoped_ospf_process_line")
            ospf.summary["invalid_structure"] = True
            continue
        pid = current["process_id"]
        if row is None:
            ospf.reject(number, reason)
            # Unknown text could introduce a new process or VPN scope. Keeping the
            # current block does not prove that an unfamiliar line belongs to it.
            ospf.summary["invalid_structure"] = True
            if block is not None:
                ospf.invalid_interface(block[0])
            continue
        if row.get("VRF_EVIDENCE"):
            if "vrf" in current:
                ospf.invalid_process(pid)
                ospf.reject(number, "duplicate_ospf_vrf_evidence")
            current.update(vrf="default", vrf_evidence=DEFAULT_COMWARE)
            processes[pid][1].append(number)
            continue
        if row.get("CONFIRMED_ROUTER_ID"):
            try:
                if "router_id_confirmed" in current or (
                    _ipv4(row["CONFIRMED_ROUTER_ID"]) != current["router_id"]
                ):
                    raise ValueError("duplicate_or_conflicting_ospf_router_id")
                current["router_id_confirmed"] = True
                processes[pid][1].append(number)
            except ValueError as exc:
                ospf.invalid_process(pid)
                ospf.reject(number, str(exc))
            continue
        if row.get("AREA"):
            close_block()
            try:
                area = _area(row["AREA"])
                if (pid, area) in areas:
                    raise ValueError("duplicate_ospf_area")
                areas.add((pid, area))
            except ValueError as exc:
                ospf.invalid_process(pid)
                ospf.reject(number, str(exc))
            continue
        if row.get("ADDRESS"):
            close_block()
            record = {"process_id": pid, "router_id": current["router_id"]}
            try:
                record["address"] = _ipv4(row["ADDRESS"])
                record.update(
                    ifname=interface(row["INTERFACE"], family).name, raw_name=row["INTERFACE"]
                )
                if area is None:
                    raise ValueError("ospf_interface_area_missing")
                record["area"] = area
                block = (record, [number])
            except ValueError as exc:
                ospf.invalid_interface(record)
                ospf.reject(number, str(exc))
            continue
        if row.get("COST") and block is not None:
            try:
                if "cost" in block[0]:
                    raise ValueError("duplicate_ospf_cost")
                block[0].update(cost=_cost(row["COST"]), state=row["STATE"])
                block[1].append(number)
            except ValueError as exc:
                ospf.invalid_interface(block[0])
                ospf.reject(number, str(exc))
            continue
        # A cost line outside an interface block.
        ospf.reject(number, "unrecognized_ospf_process_line")
        ospf.summary["invalid_structure"] = True
    close_block()
    if not processes:
        ospf.reject(1, "ospf_process_header_missing")
        ospf.summary["invalid_structure"] = True
    good = []
    for pid, (record, lines) in processes.items():
        record.pop("router_id_confirmed", None)
        if pid in ospf.summary.get("invalid_processes", []):
            continue
        good.append(record)
        ospf.sources.append({"lines": sorted(set(lines)), "raw": {"fact": "process"}})
    if good:
        ospf.summary["processes"] = good
    ospf.finish_interfaces(rows)
    return ospf.done(incomplete, False)


ASSEMBLE = {
    "trunk": trunk,
    "switchport": switchport,
    "ospf_interfaces": ospf_interfaces,
    "ospf_process": ospf_process,
}
