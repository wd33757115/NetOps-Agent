"""Interface IP facts require an actual mask, never an invented /32 or /24."""

import re

from ..normalize import interface_address, vrf_name
from .common import IFNAME, Parsed, interface, valid_ip


def parse(text, family):
    result = Parsed()
    recognized_table = bool(
        re.search(r"IP address/Mask|IP Address/Mask|IP-Address.*Mask", text, re.I)
    )
    vpn_column = bool(re.search(r"VPN instance", text, re.I))
    no_address = 0
    address_column = None
    for number, line in enumerate(text.splitlines(), 1):
        if re.match(r"\s*Interface\s+", line, re.I):
            header = re.sub(r"IP address/Mask|IP-Address", "ip_address", line, flags=re.I)
            columns = header.lower().split()
            address_column = columns.index("ip_address") if "ip_address" in columns else None
        tokens = line.split()
        if not tokens or not IFNAME.fullmatch(tokens[0]):
            continue
        result.candidates += 1
        result.handled_lines.add(number)
        candidates = []
        ip_index = None
        for index, token in enumerate(tokens[1:], 1):
            if "/" in token and ("." in token or ":" in token):
                candidates.append(token)
                ip_index = index
            elif valid_ip(token):
                if index + 1 < len(tokens) and valid_ip(tokens[index + 1]):
                    candidates.append(token + "/" + tokens[index + 1])
                    ip_index = index + 1
                    break
                candidates.append(token)  # A bare IP must fail the explicit-mask check.
                ip_index = index
        if (
            not candidates
            and recognized_table
            and address_column is not None
            and len(tokens) > address_column
            and tokens[address_column].lower() in {"--", "unassigned", "none"}
        ):
            no_address += 1
            continue
        try:
            if len(candidates) != 1:
                raise ValueError("missing_or_ambiguous_ip_prefix")
            address = interface_address(candidates[0])
            if vpn_column and ip_index + 1 >= len(tokens):
                raise ValueError("missing_vpn_column")
            vrf = vrf_name(tokens[ip_index + 1], family) if vpn_column else "default"
        except ValueError as exc:
            result.reject(number, str(exc))
            continue
        result.add(
            {"ifname": interface(tokens[0], family).name, "vrf": vrf, **address},
            [number],
            {"text": line},
        )
    result.empty_verified = recognized_table and no_address > 0 and no_address == result.candidates
    return result
