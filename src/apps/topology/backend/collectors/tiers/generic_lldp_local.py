"""LLDP local identity candidates, including per-port management addresses."""

from .common import Parsed, interface, valid_ip, valid_mac
from .generic_lldp import fields


def parse(text, family):
    result = Parsed()
    summary = {"management_addresses": []}
    evidence = []
    local = None
    for key, value, lines in fields(text):
        if key == "local_if":
            try:
                local = interface(value, family).name
            except ValueError:
                result.reject(lines[0], "invalid_local_interface")
                local = None
        elif key == "mgmt" and valid_mac(value) and not valid_ip(value):
            # IEEE 802.1AB address subtype all802: a port without an IP advertises its MAC
            # (vpn-fabric LEAF3). Only IP management addresses are modeled.
            result.handled_lines.update(lines)
        elif key == "mgmt":
            result.candidates += 1
            address = valid_ip(value)
            if not address or not local:
                result.reject(lines[0], "invalid_local_management_address")
                continue
            item = {"ifname": local, "address": address}
            if item not in summary["management_addresses"]:
                summary["management_addresses"].append(item)
            evidence.append({"field": "management_addresses", "lines": lines, "raw": value})
        elif (
            key in {"chassis", "sysname", "description", "serial", "model", "manufacturer"}
            and not local
        ):
            result.candidates += 1
            target = {
                "chassis": "chassis_id",
                "sysname": "hostname",
                "description": "system_description",
                "serial": "serial",
                "manufacturer": "vendor",
            }.get(key, key)
            if target in summary and summary[target] != value:
                result.reject(lines[0], "conflicting_local_identity")
            summary[target] = (valid_mac(value) or value) if key == "chassis" else value
            evidence.append({"field": target, "lines": lines, "raw": value})
    if summary.get("chassis_id"):
        result.summary = summary
        result.sources = evidence
    else:
        result.reject(1, "local_chassis_missing")
    return result
