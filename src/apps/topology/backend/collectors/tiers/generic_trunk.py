"""IOS ``show interfaces trunk``: keep the four printed tables separate.

Per-port observations are summary facts.  Allowed, active and forwarding VID sets
have different meanings; none implies how the native VLAN is transmitted.
"""

import re

from .common import Parsed, interface, vlan

HEADERS = (
    ("base", re.compile(r"Port\s+Mode\s+Encapsulation\s+Status\s+Native vlan\s*")),
    ("trunk_vlans", re.compile(r"Port\s+Vlans allowed on trunk\s*")),
    (
        "active_vlans",
        re.compile(r"Port\s+Vlans allowed and active in management domain\s*"),
    ),
    (
        "forwarding_vlans",
        re.compile(r"Port\s+Vlans in spanning tree forwarding state and not pruned\s*"),
    ),
)


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


def parse(text, family):
    result = Parsed()
    if family == "comware" and not text.strip():
        # Both campus BR captures of display port trunk print no table or rows.
        result.empty_verified = True
        return result
    if family not in {"ios", "iosxe"}:
        result.warnings.append("trunk_format_not_verified")
        result.reject(1, "trunk_structure_not_verified")
        result.summary["invalid_structure"] = True
        return result
    section, headers, tables, sources, invalid = None, set(), {}, {}, set()
    invalid_structure = False
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        header = next((key for key, pattern in HEADERS if pattern.fullmatch(line)), None)
        if header is not None:
            if header in headers:
                invalid_structure = True
                result.reject(number, "repeated_trunk_header")
            headers.add(header)
            section = header
            tables.setdefault(header, {})
            result.handled_lines.add(number)
            continue
        if section is None:
            invalid_structure = True
            result.reject(number, "unrecognized_trunk_header")
            continue
        tokens = line.split()
        name = None
        try:
            normalized = interface(tokens[0], family)
            if normalized.kind not in {"ethernet", "lag"}:
                raise ValueError("invalid_trunk_interface")
            name = normalized.name
            if name in tables[section]:
                raise ValueError("duplicate_trunk_port")
            if section == "base":
                result.candidates += 1
                if len(tokens) != 5 or tokens[2] != "802.1q" or tokens[3] != "trunking":
                    raise ValueError("unrecognized_trunk_port_row")
                record = {
                    "ifname": name,
                    "raw_name": tokens[0],
                    "l2_mode": "trunk",
                    "encapsulation": "dot1q",
                    "native_vlan": vlan(tokens[4]),
                }
            else:
                if len(tokens) != 2:
                    raise ValueError("invalid_trunk_vlan_row")
                record = vid_range(tokens[1])
            tables[section][name] = record
            sources.setdefault(name, []).append((number, line))
            result.handled_lines.add(number)
        except ValueError as exc:
            if name:
                invalid.add(name)
            else:
                invalid_structure = True
            result.reject(number, str(exc))
    required = {key for key, _ in HEADERS}
    if headers != required:
        result.reject(1, "trunk_sections_missing")
        result.summary["invalid_structure"] = True
        if invalid:
            result.summary["invalid_ports"] = sorted(invalid)
        return result
    ports = []
    base = tables["base"]
    for key in required - {"base"}:
        for name in set(base) ^ set(tables[key]):
            invalid.add(name)
            result.reject(1, f"trunk_port_set_mismatch:{key}:{name}")
    for name, record in base.items():
        if name in invalid:
            continue
        ports.append({**record, **{key: tables[key][name] for key in required - {"base"}}})
        evidence = sources[name]
        result.sources.append(
            {
                "lines": [number for number, _ in evidence],
                "raw": {"text": "\n".join(t for _, t in evidence)},
            }
        )
    if ports:
        result.summary["ports"] = ports
    if invalid:
        result.summary["invalid_ports"] = sorted(invalid)
    if invalid_structure:
        result.summary["invalid_structure"] = True
    result.empty_verified = not base and not result.rejected
    return result
