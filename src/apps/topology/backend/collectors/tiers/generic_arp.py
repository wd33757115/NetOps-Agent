"""IP/MAC row shapes; units and VLANs are never inferred from arbitrary numbers."""

import re

from ..normalize import age_seconds, vrf_name
from .common import IFNAME, Parsed, interface, valid_ip, valid_mac, vlan, vrf_header


def parse(text, family):
    result = Parsed()
    age_unit = None
    comware_header = False
    vsi_column = None
    vrf = "default"
    for number, line in enumerate(text.splitlines(), 1):
        if header := vrf_header(line):
            vrf = vrf_name(header, family)
        if match := re.search(r"IP address\s+MAC address\s+(VLAN/VSI name)", line, re.I):
            comware_header = True
            vsi_column = match.start(1)
        if re.search(r"Age\s*\(min(?:utes)?\)", line, re.I):
            age_unit = "min"
        tokens = line.split()
        if (
            vsi_column is not None
            and len(tokens) == 1
            and len(line) - len(line.lstrip()) == vsi_column
            and result.records
            and "vsi" in result.records[-1]
            and result.sources[-1]["lines"][-1] == number - 1
        ):
            # A long VSI name wraps onto the next line in its own column.
            result.records[-1]["vsi"] += tokens[0]
            result.sources[-1]["lines"].append(number)
            result.handled_lines.add(number)
            continue
        ips = [(i, valid_ip(t)) for i, t in enumerate(tokens) if valid_ip(t)]
        macs = [(i, valid_mac(t)) for i, t in enumerate(tokens) if valid_mac(t)]
        ports = [t for t in tokens if IFNAME.fullmatch(t)]
        if not macs and not (ips and (ports or re.search(r"incomplete", line, re.I))):
            continue
        result.candidates += 1
        result.handled_lines.add(number)
        if len(ips) != 1 or len(macs) != 1 or len(ports) > 1:
            result.reject(number, "incomplete_or_ambiguous_arp_row")
            continue
        mac_index, mac = macs[0]
        record = {"vrf": vrf, "ip": ips[0][1], "mac": mac}
        if ports:
            record["ifname"] = interface(ports[0], family).name
        if comware_header:
            try:
                column = tokens[mac_index + 1]
                if column.isdigit():
                    record["vlan"] = vlan(column)
                elif column != "--":
                    record["vsi"] = column  # learned in a VSI (VXLAN), not a VLAN
                record["type"] = {"D": "dynamic", "S": "static"}.get(tokens[-1], tokens[-1])
            except (ValueError, IndexError):
                result.reject(number, "invalid_arp_columns")
                continue
            # H3C captured header says 'Aging' without a unit. Keep raw age only.
            result.warnings.append(f"line:{number}:age_unit_unknown")
        elif age_unit and mac_index > 0:
            try:
                record["age_s"] = age_seconds(tokens[mac_index - 1], unit=age_unit)
            except ValueError:
                result.warnings.append(f"line:{number}:age_unparsed")
        result.add(record, [number], {"text": line})
    return result
