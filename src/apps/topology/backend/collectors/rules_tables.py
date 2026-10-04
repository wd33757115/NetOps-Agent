"""Whole-table facts with vetoes, assembled from rule-governance rows.

Trunk and switchport facts are summaries: the ports that are fully understood, the
ports whose observation is damaged (``invalid_ports``: a local veto) and whether
anything in the output could not be attributed to a port (``invalid_structure``: a
global veto). A veto is itself a fact, so such a result is partial, never failed.
Cut-off output is a global veto. These functions work on the check items' same-name
Values; the templates own the CLI text.
"""

from __future__ import annotations

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


ASSEMBLE = {"trunk": trunk, "switchport": switchport}
