"""VRRP instances as RFC 8347 operational state: one record per interface and VRID.

Fields follow ietf-vrrp: vrid, state (initialize/backup/master), running priority,
virtual addresses and last-adv-source (the master's address; a master reports its
own). Two layouts from lab samples (campus-wan 20261003-010038):

- Comware "display vrrp verbose": an "Interface X" line, then "Key : Value" pairs,
  two per line, and "Total number of virtual routers : N".
- IOS "show vrrp": a "Vlan10 - Group 10" header, then "State is Master" style lines.

A block missing its VRID, state or virtual address is rejected, never guessed.
"""

import re

from .common import Parsed, interface, valid_ip

STATES = {"initialize": "initialize", "init": "initialize", "backup": "backup", "master": "master"}
COUNT = re.compile(r"\s*Total number of virtual routers\s*:\s*(\d+)\s*")
HEADERS = {
    "comware": re.compile(r"\s*Interface (\S+)\s*"),
    "ios": re.compile(r"(\S+) - Group (\d+)\s*"),
}
# Comware puts two pairs on a line, so these are searched; IOS lines are matched whole.
FIELDS = {
    "comware": {
        "vrid": re.compile(r"\bVRID\s*:\s*(\d+)"),
        "state": re.compile(r"\bState\s*:\s*(\w+)"),
        "priority": re.compile(r"\bRunning Pri\s*:\s*(\d+)"),
        "virtual": re.compile(r"^\s*Virtual IP\s*:\s*(\S+)\s*$"),
        "master": re.compile(r"^\s*Master IP\s*:\s*(\S+)\s*$"),
    },
    "ios": {
        "state": re.compile(r"^\s*State is (\w+)\s*$"),
        "virtual": re.compile(r"^\s*(?:Secondary )?Virtual IP address is (\S+)\s*$"),
        "priority": re.compile(r"^\s*Priority is (\d+)\s*$"),
        "master": re.compile(r"^\s*Master Router is (\S+)(?: \(local\))?, priority is \d+\s*$"),
    },
}


def _block(layout, ifname, vrid, number):
    record = {
        "ifname": ifname,
        "vrid": vrid,
        "state": None,
        "priority": None,
        "virtual_addresses": [],
        "last_adv_source": None,
    }
    return {"layout": layout, "record": record, "lines": [number]}


def _close(result, block):
    if block is None:
        return
    record, lines = block["record"], block["lines"]
    if record["vrid"] is None or record["state"] is None:
        result.reject(lines[0], "vrrp_block_without_vrid_or_state")
    elif not record["virtual_addresses"]:
        result.reject(lines[0], "vrrp_block_without_virtual_address")
    else:
        result.add(
            record, sorted(set(lines)), {"text": f"{record['ifname']} vrid {record['vrid']}"}
        )


def _apply(record, key, value):
    if key in {"vrid", "priority"}:
        record[key] = int(value)
    elif key == "state":
        record["state"] = STATES.get(value.lower(), value.lower())
    elif key == "master":
        record["last_adv_source"] = valid_ip(value)
    else:
        # Comware prints the virtual address with a host mask ("10.10.1.10/32").
        address = valid_ip(value.split("/", 1)[0])
        if address is None:
            raise ValueError("invalid_virtual_address")
        if address not in record["virtual_addresses"]:
            record["virtual_addresses"].append(address)


def parse(text, family):
    result = Parsed()
    block, advertised = None, None
    for number, line in enumerate(text.splitlines(), 1):
        count = COUNT.fullmatch(line)
        if count:
            advertised = int(count[1])
            result.handled_lines.add(number)
            continue
        layout, header = next(
            ((name, p.fullmatch(line)) for name, p in HEADERS.items() if p.fullmatch(line)),
            (None, None),
        )
        if header:
            _close(result, block)
            block = None
            result.candidates += 1
            result.handled_lines.add(number)
            try:
                vrid = int(header[2]) if layout == "ios" else None
                block = _block(layout, interface(header[1], family).name, vrid, number)
            except ValueError as exc:
                result.reject(number, str(exc))
            continue
        if block is None:
            continue
        result.handled_lines.add(number)
        vrid = FIELDS["comware"]["vrid"].search(line) if block["layout"] == "comware" else None
        if vrid and block["record"]["vrid"] is not None:
            # Another VRID on the same interface starts its own instance.
            _close(result, block)
            result.candidates += 1
            block = _block("comware", block["record"]["ifname"], None, number)
        for key, pattern in FIELDS[block["layout"]].items():
            match = pattern.search(line)
            if match:
                try:
                    _apply(block["record"], key, match[1])
                except ValueError as exc:
                    result.reject(number, str(exc))
                block["lines"].append(number)
    _close(result, block)
    if advertised is not None and advertised != len(result.records):
        result.warnings.append("advertised_count_mismatch")
        result.rejected += 1
    return result
