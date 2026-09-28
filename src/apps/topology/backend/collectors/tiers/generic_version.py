"""Conservative identity candidates; no vendor/model inference from hostnames."""

import re

from .common import Parsed

PATTERNS = {
    "model": (
        r"^\s*(?:H3C|HPE|HP)\s+(\S+)\s+uptime is\b",
        r"^\s*(?:Model(?: name| number)?|Device model)\s*[:=]\s*(\S.*?)\s*$",
    ),
    "os_version": (
        r"^.*?\b(?:Comware|IOS|VRP|NX-OS|RGOS)\b.*?\bVersion\s+(.+?)\s*$",
        r"^\s*(?:NXOS|system)\s*:\s*version\s+(\S+)",
    ),
    "serial": (
        r"^\s*(?:Serial(?: number)?|Processor board ID|System serial number)\s*[:=]?\s+(\S+)\s*$",
    ),
    "hostname": (r"^\s*(\S+)\s+uptime is\b", r"^\s*(?:Hostname|System name)\s*:\s*(\S+)\s*$"),
}


def parse(text, family):
    result = Parsed()
    for number, line in enumerate(text.splitlines(), 1):
        for field, patterns in PATTERNS.items():
            for pattern in patterns:
                match = re.search(pattern, line, re.I)
                if match:
                    value = match.group(1)
                    result.candidates += 1
                    if field in result.summary and result.summary[field] != value:
                        result.reject(number, "conflicting_version_identity")
                    else:
                        result.summary[field] = value
                        result.sources.append({"field": field, "lines": [number], "raw": value})
                    break
    return result
