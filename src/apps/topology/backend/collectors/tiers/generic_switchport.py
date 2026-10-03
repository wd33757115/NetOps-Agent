"""IOS switchport blocks, verified against the campus IOL 17.15 captures.

Preserve administrative/operational mode observations separately.  An idle dynamic
port's default access VLAN does not establish access mode or a PVID.  The ordinary
native-tagging field is matched exactly, separately from the private-VLAN field.
"""

import re

from .common import Parsed, interface, vlan
from .generic_trunk import vid_range

REQUIRED = {
    "Switchport",
    "Administrative Mode",
    "Operational Mode",
    "Access Mode VLAN",
    "Trunking Native Mode VLAN",
    "Trunking VLANs Enabled",
}
OBSERVED = REQUIRED | {
    "Administrative Native VLAN tagging",
    "Operational Native VLAN tagging",
    "Administrative Trunking Encapsulation",
    "Operational Trunking Encapsulation",
}
# These fields were observed alongside the VLAN fields.  Their exact benign
# values keep private VLAN/voice features from silently becoming ordinary VLANs.
OTHER = {
    "Negotiation of Trunking": r"On|Off",
    "Voice VLAN": r"none",
    "Administrative private-vlan host-association": r"none",
    "Administrative private-vlan mapping": r"none",
    "Administrative private-vlan trunk native VLAN": r"none",
    "Administrative private-vlan trunk Native VLAN tagging": r"enabled|disabled",
    "Administrative private-vlan trunk encapsulation": r"dot1q",
    "Administrative private-vlan trunk normal VLANs": r"none",
    "Administrative private-vlan trunk associations": r"none",
    "Administrative private-vlan trunk mappings": r"none",
    "Operational private-vlan": r"none",
    "Pruning VLANs Enabled": r"[0-9,-]+|none",
    "Capture VLANs Allowed": r"ALL|[0-9,-]+|none",
    "Protected": r"false",
    "Appliance trust": r"none",
}


def _vid(value):
    match = re.fullmatch(r"([0-9]+)(?: \([^()]+\))?", value)
    if not match:
        raise ValueError("invalid_switchport_vlan")
    return vlan(match[1])


def _project(name, fields, family):
    if not REQUIRED <= fields.keys():
        raise ValueError("switchport_fields_missing")
    port = interface(name, family)
    if port.kind not in {"ethernet", "lag"} or fields["Switchport"] != "Enabled":
        raise ValueError("switchport_format_not_verified")
    admin, oper = fields["Administrative Mode"], fields["Operational Mode"]
    if admin not in {"dynamic auto", "dynamic desirable", "static access", "access", "trunk"}:
        raise ValueError("switchport_admin_mode_not_verified")
    trunk = re.fullmatch(r"trunk(?: \(member of bundle ([^()]+)\))?", oper)
    if not trunk and oper not in {"down", "static access", "access"}:
        raise ValueError("switchport_oper_mode_not_verified")
    access, native = _vid(fields["Access Mode VLAN"]), _vid(fields["Trunking Native Mode VLAN"])
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
    if trunk:
        if fields.get("Operational Trunking Encapsulation") != "dot1q":
            raise ValueError("switchport_encapsulation_not_verified")
        record.update(l2_mode="trunk", encapsulation="dot1q", pvid=native)
        if trunk[1]:
            parent = interface(trunk[1], family)
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


def parse(text, family):
    result = Parsed()
    if family not in {"ios", "iosxe"}:
        result.warnings.append("switchport_format_not_verified")
        return result
    blocks, current, invalid_ports = [], None, set()
    invalid_structure = False
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        name = re.fullmatch(r"Name: (\S+)\s*", line)
        if name:
            current = {
                "name": name[1],
                "fields": {},
                "lines": [number],
                "raw": [line],
                "valid": True,
            }
            blocks.append(current)
            result.candidates += 1
            continue
        if line.startswith("Name:"):
            # A malformed next block could belong to any port, not the previous one.
            current = None
            invalid_structure = True
            result.reject(number, "switchport_block_header_invalid")
            continue
        if current is None:
            invalid_structure = True
            result.reject(number, "switchport_block_header_missing")
            continue
        current["lines"].append(number)
        current["raw"].append(line)
        if line == "Capture Mode Disabled":
            result.handled_lines.add(number)
            continue
        field = re.fullmatch(r"([^:]+):\s*(.*?)\s*", line)
        if not field:
            current["valid"] = False
            result.reject(number, "unrecognized_switchport_line")
            continue
        key, value = field[1], field[2]
        if key in current["fields"]:
            current["valid"] = False
            result.reject(number, "duplicate_switchport_field")
            continue
        current["fields"][key] = value
        if key not in OBSERVED and (key not in OTHER or not re.fullmatch(OTHER[key], value)):
            current["valid"] = False
            result.reject(number, "unrecognized_switchport_field_or_value")
        else:
            result.handled_lines.add(number)
    records, names = [], set()
    for block in blocks:
        normalized = None
        try:
            identity = interface(block["name"], family)
            if identity.kind not in {"ethernet", "lag"}:
                raise ValueError("switchport_interface_not_verified")
            normalized = identity.name
            if normalized in names:
                invalid_ports.add(normalized)
                raise ValueError("duplicate_switchport_interface")
            names.add(normalized)
            if not block["valid"]:
                invalid_ports.add(normalized)
                continue
            record = _project(block["name"], block["fields"], family)
            records.append((record, block))
        except ValueError as exc:
            if normalized is None:
                invalid_structure = True
            else:
                invalid_ports.add(normalized)
            result.reject(block["lines"][0], str(exc))
    ports = []
    for record, block in records:
        if record["ifname"] in invalid_ports:
            continue
        ports.append(record)
        result.sources.append({"lines": block["lines"], "raw": {"text": "\n".join(block["raw"])}})
    if ports:
        result.summary["ports"] = ports
    if invalid_ports:
        result.summary["invalid_ports"] = sorted(invalid_ports)
    if invalid_structure:
        result.summary["invalid_structure"] = True
    return result
