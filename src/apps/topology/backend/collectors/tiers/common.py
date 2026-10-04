"""Generic parser contracts and evidence in normalized-text line coordinates."""

import re
from dataclasses import dataclass, field

from ..families import cli
from ..ifname import normalize_ifname
from ..normalize import ip_address, mac_address


@dataclass
class Parsed:
    records: list[dict] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    candidates: int = 0
    rejected: int = 0
    empty_verified: bool = False
    handled_lines: set[int] = field(default_factory=set)

    def add(self, record, lines, raw):
        self.records.append(record)
        self.sources.append({"lines": sorted(set(lines)), "raw": raw})

    def reject(self, line, reason):
        self.rejected += 1
        self.warnings.append(f"line:{line}:{reason}")


TABLE_NOISE = {
    "arp": (
        r"Type:.*",
        r"IP address\s+MAC address\s+.*",
        r"Protocol\s+Address\s+.*",
        r"IP ADDRESS\s+MAC ADDRESS\s+.*",
        r"Address\s+Age.*",
        r"IPv6 Address\s+.*",
        r"IP ARP Table for context\s+\S+",
    ),
    "mac": (
        r"MAC Address\s+VLAN ID\s+.*",
        r"Vlan\s+Mac Address\s+.*",
        r"MAC Address\s+VLAN/VSI.*",
        r"Mac Address Table",
        r"Legend:.*",
    ),
    "interfaces": (
        r"Brief information on interfaces in (?:route|bridge) mode:",
        r"(?:Link|Protocol|Speed|Duplex|Type):.*",
        r"Interface\s+.*",
        r"Port\s+Name\s+Status\s+.*",
    ),
    "ip": (r"Interface\s+.*", r"\*down:.*", r"\(s\):.*"),
}


def audit_table(text, capability, result):
    """A good row followed by unfamiliar data is partial, never silently complete."""
    handled = result.handled_lines | {n for source in result.sources for n in source["lines"]}
    patterns = (*TABLE_NOISE[capability], r"[-=\s]+", r"(?:VPN instance|VRF)\s*:\s*\S+")
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip() or number in handled or table_count(line):
            continue
        if any(re.fullmatch(pattern, line.strip(), re.I) for pattern in patterns):
            continue
        result.reject(number, "unrecognized_table_line")


def vrf_header(line):
    match = re.fullmatch(
        r"\s*(?:(?:VPN instance|VRF)\s*:\s*|IP ARP Table for context\s+)(\S+)\s*", line, re.I
    )
    return match.group(1) if match else None


# Shape recognition is broader than canonicalization. Unknown family preserves spelling.
IFNAME = re.compile(
    r"(?:GigabitEthernet|Ten-GigabitEthernet|TenGigabitEthernet|XGigabitEthernet|"
    r"FastEthernet|Ethernet|Gi|GE|XGE|Te|Fa|Eth|Et|"
    r"Bridge-Aggregation|Route-Aggregation|BAGG|RAGG|Port-channel|Po|Eth-Trunk|"
    r"Vlan-interface|Vlanif|Vlan|Vl|InLoopBack|InLoop|LoopBack|Loop|Lo|NULL|REG|mgmt|"
    r"Tunnel|Tun|Tu|Vsi-interface|Vsi|nve|nv)"
    r"[0-9]+(?:/[0-9]+)*(?:\.[0-9]+)?",
    re.I | re.ASCII,
)


def interface(value, family):
    if not IFNAME.fullmatch(value):
        raise ValueError("unrecognized_interface_shape")
    return normalize_ifname(value, cli(family) or family)


def valid_ip(value):
    try:
        return ip_address(value)
    except ValueError:
        return None


def valid_mac(value):
    try:
        return mac_address(value)
    except ValueError:
        return None


def vlan(value):
    if not re.fullmatch(r"[0-9]+", value) or not 1 <= int(value) <= 4094:
        raise ValueError("invalid_vlan")
    return int(value)


def table_count(text):
    """Only explicit total-entry counts, never capacity or per-interface counters."""
    patterns = (
        r"^\s*Total (?:number of )?(?:entries|neighbors|neighbours|MAC addresses)"
        r"\s*[:=]\s*(\d+)\s*$",
        r"^\s*Total (?:entries|neighbors|neighbours) (?:displayed|are)\s*:\s*(\d+)\s*$",
        r"^\s*Total Mac Addresses for this criterion\s*:\s*(\d+)\s*$",  # IOS
    )
    return [int(m.group(1)) for p in patterns for m in re.finditer(p, text, re.I | re.M)]
