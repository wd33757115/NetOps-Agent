"""Interface IP facts require an actual mask, never an invented /32 or /24."""

import re

from ..normalize import interface_address, vrf_name
from .common import IFNAME, Parsed, interface, valid_ip

# IOS "show ip interface": one block per interface, addresses and VRF inside it.
BLOCK = re.compile(r"(\S+) is (?:up|down|administratively down|deleted), line protocol is .+")
ADDRESS = re.compile(r"\s+(?:Internet address is|Secondary address) (\S+)\s*")
BLOCK_VRF = re.compile(r'\s+VPN Routing/Forwarding "([^"]+)"\s*')
# Other block lines that carry addresses but are not the interface's own.
OTHER_ADDRESSES = re.compile(
    r"\s+(?:Broadcast address is|Multicast reserved groups joined:|Helper address(?:es)? (?:is|are)"
    r"|Interface is unnumbered\.)\s.*"
)
ADDRESS_LIKE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")  # "show ip interface" is IPv4 only


def parse_blocks(text, family):
    """Every block line is read; an address line in an unknown phrasing rejects."""
    result = Parsed()
    name, own, vrf = None, [], "default"

    def close():
        for record, lines, raw in own:
            result.add({**record, "vrf": vrf}, lines, {"text": raw})

    for number, line in enumerate(text.splitlines(), 1):
        header = BLOCK.fullmatch(line)
        if header:
            close()
            own, vrf = [], "default"
            result.candidates += 1
            result.handled_lines.add(number)
            try:
                name = (interface(header[1], family).name, number)
            except ValueError as exc:
                name = None
                result.reject(number, str(exc))
            continue
        if name is None:
            continue
        result.handled_lines.add(number)
        address, block_vrf = ADDRESS.fullmatch(line), BLOCK_VRF.fullmatch(line)
        try:
            if address:
                record = {"ifname": name[0], **interface_address(address[1])}
                own.append((record, [name[1], number], line))
            elif block_vrf:
                vrf = vrf_name(block_vrf[1], family)
                for _, lines, _ in own:
                    lines.append(number)
            elif ADDRESS_LIKE.search(line) and not OTHER_ADDRESSES.fullmatch(line):
                raise ValueError("unrecognized_address_line")
        except ValueError as exc:
            result.reject(number, str(exc))
    close()
    result.empty_verified = result.candidates > 0 and not result.records and not result.rejected
    return result


def parse(text, family):
    if BLOCK.fullmatch(next((line for line in text.splitlines() if line.strip()), "")):
        return parse_blocks(text, family)
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
