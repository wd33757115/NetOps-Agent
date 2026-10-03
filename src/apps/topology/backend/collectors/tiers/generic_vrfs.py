"""Enumerate Cisco VRF names; this is device-local scope, never a global identity."""

import re

from .common import Parsed

IOS_HEADER = re.compile(r"\s*Name\s+Default RD\s+Protocols\s+Interfaces\s*", re.I)
IOS_ROW = re.compile(r"\s*(\S+)\s+(?:<not set>|\S+)\s+(ipv[46](?:,ipv[46])*)(?:\s+.*)?")
NXOS_HEADER = re.compile(r"\s*VRF-Name\s+VRF-ID\s+State\s+Reason\s*", re.I)
NXOS_ROW = re.compile(r"\s*(\S+)\s+\d+\s+(?:Up|Down)\s+.*", re.I)


def parse(text, family):
    result = Parsed()
    if family not in {"ios", "iosxe", "nxos"}:
        return result
    header = NXOS_HEADER if family == "nxos" else IOS_HEADER
    row = NXOS_ROW if family == "nxos" else IOS_ROW
    seen_header = False
    names = set()
    instances = []
    interfaces_column = None
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        if header.fullmatch(line):
            seen_header = True
            if family != "nxos":
                interfaces_column = line.lower().index("interfaces")
            continue
        match = row.fullmatch(line) if seen_header else None
        if match:
            result.candidates += 1
            name = match[1]
            if name in names:
                result.reject(number, "duplicate_vrf_name")
                continue
            names.add(name)
            instances.append(
                {
                    "name": name,
                    "address_families": match[2].split(",") if family != "nxos" else [],
                }
            )
            result.sources.append({"lines": [number], "raw": {"text": line}})
        elif instances and interfaces_column is not None and not line[:interfaces_column].strip():
            # IOS wraps only the interface column onto continuation lines.
            continue
        else:
            result.reject(number, "unrecognized_vrf_table_line")
    if seen_header:
        result.summary = {"instances": instances}
        result.empty_verified = not instances and not result.rejected
    return result
