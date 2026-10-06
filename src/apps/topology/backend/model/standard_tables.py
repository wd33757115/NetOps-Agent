"""Builder rows from the standard trees of a device read over NETCONF.

The SIMAP builder (builder.py) reads interface, address, neighbour and LLDP rows. For a
device whose tables came through netconf-api's conversion layer, those rows are taken
from its standard trees (standard_trees.py), not from the CLI's facts:

- interfaces: RFC 8343 name, type, oper-status and enabled;
- ip: RFC 8344 addresses with their RFC 8529 instance; a VRRP virtual address the master
  lists (origin "other") is RFC 8347's, not the interface's, and is left out as the CLI
  does;
- arp: RFC 8344 neighbours;
- lldp: IEEE 802.1AB remote systems with their management addresses.

Only the tables the device was read for are given; the others stay with the CLI.
"""

from __future__ import annotations

import ipaddress

from .device_state import INTERFACE_TYPES
from .standard_trees import IF, LLDP, device_tree, lldp_section

KINDS = {identity: kind for kind, identity in INTERFACE_TYPES.items()}
SUBTYPES = {"mac-address": "MAC address", "interface-name": "Interface name"}
FAMILIES = {"ietf-routing:ipv4": 4, "ietf-routing:ipv6": 6}


def _mac(ieee):
    return ieee.replace("-", ":").lower()


def _address(entry):
    family = FAMILIES.get(entry.get("address-subtype"))
    try:
        raw = bytes.fromhex(entry["address"])
        return str(ipaddress.ip_address(raw)) if family else None
    except (KeyError, ValueError):
        return None


def builder_rows(records, platform):
    """{"interfaces", "ip", "arp", "lldp"}: those the device's standard records cover."""
    rows = {}
    device = device_tree(records, platform)["tree"][IF]["interface"]
    if "interface" in records:
        rows["interfaces"] = [
            {
                "ifname": entry["name"],
                "type": KINDS.get(entry.get("type")),
                **({"oper_status": entry["oper-status"]} if "oper-status" in entry else {}),
                **(
                    {"admin_status": "up" if entry["enabled"] else "down"}
                    if "enabled" in entry
                    else {}
                ),
                **({"description": entry["description"]} if "description" in entry else {}),
            }
            for entry in device
        ]
    for table, key, leaf in (
        ("ipv4-address", "ip", "address"),
        ("ipv4-neighbor", "arp", "neighbor"),
    ):
        if table not in records:
            continue
        found = []
        for entry in device:
            vrf = entry.get("ietf-network-instance:bind-ni-name", "default")
            for family in ("ietf-ip:ipv4", "ietf-ip:ipv6"):
                for item in entry.get(family, {}).get(leaf, []):
                    if leaf == "address":
                        if item.get("origin") == "other":
                            continue
                        found.append(
                            {
                                "ifname": entry["name"],
                                "vrf": vrf,
                                "address": item["ip"],
                                "prefix_len": item["prefix-length"],
                            }
                        )
                    else:
                        found.append(
                            {
                                "vrf": vrf,
                                "ip": item["ip"],
                                "mac": item["link-layer-address"],
                                "ifname": entry["name"],
                                **(
                                    {"type": item["origin"]}
                                    if item.get("origin") in {"dynamic", "static"}
                                    else {}
                                ),
                            }
                        )
        rows[key] = found
    if "lldp-neighbor" in records:
        found = []
        tree = lldp_section(records, platform)["tree"][LLDP]
        for port in tree.get("port", []):
            for remote in port.get("remote-systems-data", []):
                if "chassis-id" not in remote or "port-id" not in remote:
                    continue
                chassis = remote["chassis-id"]
                if remote.get("chassis-id-subtype") == "mac-address":
                    chassis = _mac(chassis)
                addresses = [a for a in map(_address, remote.get("management-address", [])) if a]
                found.append(
                    {
                        "local_if": port["name"],
                        "remote_chassis_id": chassis,
                        "remote_chassis_subtype": SUBTYPES.get(
                            remote.get("chassis-id-subtype"), remote.get("chassis-id-subtype")
                        ),
                        "remote_port_id": remote["port-id"],
                        "remote_port_subtype": SUBTYPES.get(
                            remote.get("port-id-subtype"), remote.get("port-id-subtype")
                        ),
                        "remote_sysname": remote.get("system-name"),
                        "remote_system_description": remote.get("system-description"),
                        "remote_mgmt_addresses": addresses,
                    }
                )
        rows["lldp"] = found
    return rows
