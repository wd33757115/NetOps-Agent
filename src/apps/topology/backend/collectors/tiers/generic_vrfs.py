"""Enumerate VRF / VPN-instance names; this is device-local scope, never a global identity."""

import re

from .common import Parsed

IOS_HEADER = re.compile(r"\s*Name\s+Default RD\s+Protocols\s+Interfaces\s*", re.I)
IOS_ROW = re.compile(r"\s*(\S+)\s+(?:<not set>|\S+)\s+(ipv[46](?:,ipv[46])*)(?:\s+.*)?")
NXOS_HEADER = re.compile(r"\s*VRF-Name\s+VRF-ID\s+State\s+Reason\s*", re.I)
NXOS_ROW = re.compile(r"\s*(\S+)\s+\d+\s+(?:Up|Down)\s+.*", re.I)
# Comware "display ip vpn-instance" (vpn-fabric PE2/LEAF3): counts, then name, RD, create time.
COMWARE_HEADER = re.compile(r"\s*VPN-Instance Name\s+RD\s+Create time\s*", re.I)
COMWARE_ROW = re.compile(r"\s*(\S+)\s+\S+\s+\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2}\s*")
COMWARE_PREFACE = re.compile(
    r"\s*(?:Total VPN-Instances configured|License limit|"
    r"Max VPN instances permitted by license)\s*:\s*\S+\s*",
    re.I,
)


def parse(text, family):
    result = Parsed()
    if family not in {"ios", "iosxe", "nxos", "comware"}:
        return result
    header, row = {
        "nxos": (NXOS_HEADER, NXOS_ROW),
        "comware": (COMWARE_HEADER, COMWARE_ROW),
    }.get(family, (IOS_HEADER, IOS_ROW))
    seen_header = False
    names = set()
    instances = []
    interfaces_column = None
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        if family == "comware" and not seen_header and COMWARE_PREFACE.fullmatch(line):
            continue
        if header.fullmatch(line):
            seen_header = True
            if family in {"ios", "iosxe"}:
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
                    "address_families": match[2].split(",") if family in {"ios", "iosxe"} else [],
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
