"""MAC forwarding entries; require VLAN evidence before writing a typed row."""

import re

from .common import IFNAME, Parsed, interface, valid_mac, vlan

HEADER = re.compile(r"^\s*MAC Address\s+VLAN ID\s+State\s+Port/Nickname\s+Aging\s*$", re.I)


def parse(text, family):
    result = Parsed()
    meaningful = [
        line
        for line in text.splitlines()
        if line.strip() and not re.fullmatch(r"\s*[-=]+\s*", line)
    ]
    result.empty_verified = len(meaningful) == 1 and bool(HEADER.fullmatch(meaningful[0]))
    for number, line in enumerate(text.splitlines(), 1):
        tokens = line.split()
        macs = [(i, valid_mac(t)) for i, t in enumerate(tokens) if valid_mac(t)]
        ports = [t for t in tokens if IFNAME.fullmatch(t)]
        if not macs:
            if ports:
                result.candidates += 1
                result.handled_lines.add(number)
                result.reject(number, "mac_missing")
            continue
        result.candidates += 1
        result.handled_lines.add(number)
        if len(macs) != 1 or len(ports) != 1:
            result.reject(number, "incomplete_or_ambiguous_mac_row")
            continue
        index, mac = macs[0]
        # Recognized table forms place VLAN directly before or after the MAC.
        adjacent = [
            tokens[i]
            for i in (index - 1, index + 1)
            if 0 <= i < len(tokens) and tokens[i].isdigit()
        ]
        try:
            if len(adjacent) != 1:
                raise ValueError("vlan_missing_or_ambiguous")
            vid = str(vlan(adjacent[0]))
        except ValueError as exc:
            result.reject(number, str(exc))
            continue
        entry_type = next(
            (
                t.lower()
                for t in tokens
                if t.lower() in {"dynamic", "static", "learned", "blackhole"}
            ),
            None,
        )
        result.add(
            {
                "vlan_or_bd": vid,
                "mac": mac,
                "port": interface(ports[0], family).name,
                "type": entry_type,
            },
            [number],
            {"text": line},
        )
    return result
