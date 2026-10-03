"""VLAN inventory and the port column observed in IOS/Comware brief tables.

Only ID/name are typed VLAN rows.  ``summary.members`` retains the printed
membership column; it is not a complete tagged/untagged bridge membership set.
"""

import re

from .common import Parsed, interface, vlan


def _ports(value, family):
    if not value.strip():
        return []
    parsed = [interface(part.strip(), family) for part in value.strip().split(",")]
    if any(port.kind not in {"ethernet", "lag"} for port in parsed):
        raise ValueError("invalid_vlan_member_interface")
    names = [port.name for port in parsed]
    if len(names) != len(set(names)):
        raise ValueError("duplicate_vlan_port")
    return names


def parse(text, family):
    result = Parsed()
    if family not in {"ios", "iosxe", "comware"}:
        result.warnings.append("vlan_format_not_verified")
        return result
    columns, rows, invalid, current = None, {}, set(), None
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip() or re.fullmatch(r"[-\s]+", line):
            continue
        ios_header = re.fullmatch(r"VLAN\s+Name\s+Status\s+Ports\s*", line)
        comware_header = re.fullmatch(r"VLAN ID\s+Name\s+Port\s*", line)
        if (ios_header and family in {"ios", "iosxe"}) or (comware_header and family == "comware"):
            if columns is not None:
                result.reject(number, "repeated_vlan_header")
                current = None
                continue
            columns = {
                "name": line.index("Name"),
                "ports": line.index("Ports" if ios_header else "Port"),
            }
            if ios_header:
                columns["status"] = line.index("Status")
            result.handled_lines.add(number)
            continue
        if (
            family == "comware"
            and columns is None
            and (
                line == "Brief information about all VLANs:"
                or re.fullmatch(
                    r"(?:Supported Minimum|Supported Maximum|Default) VLAN ID:\s*\d+", line
                )
            )
        ):
            result.handled_lines.add(number)
            continue
        if columns is None:
            result.reject(number, "unrecognized_vlan_header")
            continue
        name_start, ports_start = columns["name"], columns["ports"]
        identifier = line[:name_start].strip()
        if not identifier and current is not None and not line[:ports_start].strip():
            try:
                ports = _ports(line[ports_start:], family)
                if not ports or set(ports) & set(rows[current]["member"]["ports"]):
                    raise ValueError("invalid_vlan_continuation")
                rows[current]["member"]["ports"].extend(ports)
                rows[current]["lines"].append(number)
                rows[current]["raw"].append(line)
                result.handled_lines.add(number)
            except ValueError as exc:
                invalid.add(current)
                result.reject(number, str(exc))
            continue
        current = None
        result.candidates += 1
        try:
            vid = vlan(identifier)
            if vid in rows:
                invalid.add(vid)
                raise ValueError("duplicate_vlan_id")
            name_end = columns.get("status", ports_start)
            name = line[name_start:name_end].strip()
            if not name:
                raise ValueError("vlan_name_missing")
            member = {"vlan_id": vid, "ports": _ports(line[ports_start:], family)}
            if "status" in columns:
                status = line[columns["status"] : ports_start].strip()
                if not status or len(status.split()) != 1:
                    raise ValueError("vlan_status_missing_or_ambiguous")
                member["status"] = status
            rows[vid] = {
                "record": {"vlan_id": vid, "name": name},
                "member": member,
                "lines": [number],
                "raw": [line],
            }
            current = vid
            result.handled_lines.add(number)
        except ValueError as exc:
            result.reject(number, str(exc))
    members = []
    for vid, row in rows.items():
        if vid in invalid:
            continue
        result.add(row["record"], row["lines"], {"text": "\n".join(row["raw"])})
        members.append(row["member"])
    if members:
        result.summary["members"] = members
    return result
