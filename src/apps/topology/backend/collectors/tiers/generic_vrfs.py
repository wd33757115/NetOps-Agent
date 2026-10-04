"""Enumerate NX-OS VRF names; this is device-local scope, never a global identity.

IOS and Comware VRF lists are read through the rule-governance check item topology.vrfs.
"""

import re

from .common import Parsed

NXOS_HEADER = re.compile(r"\s*VRF-Name\s+VRF-ID\s+State\s+Reason\s*", re.I)
NXOS_ROW = re.compile(r"\s*(\S+)\s+\d+\s+(?:Up|Down)\s+.*", re.I)


def parse(text, family):
    result = Parsed()
    if family != "nxos":
        return result
    seen_header = False
    names = set()
    instances = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        if NXOS_HEADER.fullmatch(line):
            seen_header = True
            continue
        match = NXOS_ROW.fullmatch(line) if seen_header else None
        if not match:
            result.reject(number, "unrecognized_vrf_table_line")
            continue
        result.candidates += 1
        if match[1] in names:
            result.reject(number, "duplicate_vrf_name")
            continue
        names.add(match[1])
        instances.append({"name": match[1], "address_families": []})
        result.sources.append({"lines": [number], "raw": {"text": line}})
    if seen_header:
        result.summary = {"instances": instances}
        result.empty_verified = not instances and not result.rejected
    return result
