"""Explicit platform aliases; preserve unknown names instead of guessing a port type."""

import re
from dataclasses import dataclass

# canonical prefix, kind, accepted aliases. Prefix matching is case-insensitive.
PLATFORMS = {
    "comware": (
        ("GigabitEthernet", "ethernet", ("GigabitEthernet", "GE")),
        ("Ten-GigabitEthernet", "ethernet", ("Ten-GigabitEthernet", "XGE")),
        ("Bridge-Aggregation", "lag", ("Bridge-Aggregation", "BAGG")),
        ("Route-Aggregation", "lag", ("Route-Aggregation", "RAGG")),
        ("Vlan-interface", "vlanif", ("Vlan-interface", "Vlan")),
        ("LoopBack", "loopback", ("LoopBack", "Loop")),
        ("InLoopBack", "loopback", ("InLoopBack", "InLoop")),
        ("NULL", "null", ("NULL",)),
        ("REG", "register", ("REG",)),
    ),
    "nxos": (
        ("Ethernet", "ethernet", ("Ethernet", "Eth")),
        ("port-channel", "lag", ("port-channel", "Po")),
        ("Vlan", "vlanif", ("Vlan",)),
        ("loopback", "loopback", ("loopback", "Lo")),
        ("mgmt", "mgmt", ("mgmt",)),
        ("Null", "null", ("Null",)),
    ),
    "ios": (
        ("GigabitEthernet", "ethernet", ("GigabitEthernet", "Gi")),
        ("TenGigabitEthernet", "ethernet", ("TenGigabitEthernet", "Te")),
        ("FastEthernet", "ethernet", ("FastEthernet", "Fa")),
        # IOL and some platforms name ports Ethernet0/1; LLDP and MAC tables shorten to Et0/1.
        ("Ethernet", "ethernet", ("Ethernet", "Et")),
        ("Port-channel", "lag", ("Port-channel", "Po")),
        ("Vlan", "vlanif", ("Vlan", "Vl")),
        ("Loopback", "loopback", ("Loopback", "Lo")),
        ("Null", "null", ("Null",)),
    ),
    "vrp": (
        ("GigabitEthernet", "ethernet", ("GigabitEthernet", "GE")),
        ("XGigabitEthernet", "ethernet", ("XGigabitEthernet", "XGE")),
        ("Eth-Trunk", "lag", ("Eth-Trunk",)),
        ("Vlanif", "vlanif", ("Vlanif",)),
        ("LoopBack", "loopback", ("LoopBack",)),
        ("NULL", "null", ("NULL",)),
    ),
}


@dataclass(frozen=True)
class InterfaceName:
    raw_name: str
    name: str
    kind: str
    recognized: bool

    @property
    def physical(self):
        return self.recognized and self.kind in {"ethernet", "mgmt"} and "." not in self.name


def normalize_ifname(raw, platform):
    if not isinstance(raw, str) or not raw.strip() or len(raw) > 256:
        raise ValueError("invalid interface name")
    if any(ord(char) < 32 or ord(char) == 127 for char in raw):
        raise ValueError("control character in interface name")
    text = raw.strip(" ")
    for canonical, kind, aliases in PLATFORMS.get("ios" if platform == "iosxe" else platform, ()):
        for alias in aliases:
            match = re.fullmatch(
                re.escape(alias) + r"([0-9]+(?:/[0-9]+)*(?:\.[0-9]+)?)", text, re.I | re.ASCII
            )
            if match:
                # Do not erase leading zeroes or lane identifiers; no evidence they are equivalent.
                return InterfaceName(raw, canonical + match.group(1), kind, True)
    return InterfaceName(raw, text, "unknown", False)
