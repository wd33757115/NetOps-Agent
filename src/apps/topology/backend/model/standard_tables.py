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
    rows.update(policy_rows(records))
    return rows


def _flagged(record):
    """Conditions the device gave that the mapping could not read: never left out."""
    return [f"condition:{leaf}" for leaf in record.get("unread", [])] + [
        f"condition:{field}" for field in record.get("problem_fields", [])
    ]


def _acl_entry(record):
    """One ACL entry as the conditions device_acl.py exports (its input form)."""
    unsupported = _flagged(record)
    entry = {"acl": record["acl"], "seq": record["sequence"], "family": "ipv4"}
    if record.get("action") in {"permit", "deny"}:
        entry["action"] = record["action"]
    else:
        unsupported.append("unknown_entry")
    if "protocol" in record:
        entry["protocol"] = record["protocol"]
    for side, key in (("source", "src"), ("destination", "dst")):
        if group := record.get(f"{side}-object-group"):
            unsupported.append(f"object_group:{group}")
        elif f"{side}-address" in record and f"{side}-wildcard" in record:
            entry[key] = {
                "address": record[f"{side}-address"],
                "wildcard": record[f"{side}-wildcard"],
            }
        op = record.get(f"{side}-port-operator")
        if op is not None:
            low, high = record.get(f"{side}-port"), record.get(f"{side}-upper-port")
            ports = [low, high] if op == "range" else [low]
            if None in ports:
                unsupported.append(f"{key}_ports")
            else:
                entry[f"{key}_ports"] = {"op": op, "ports": ports}
    if "icmp-type" in record:
        entry["icmp"] = [record["icmp-type"], record.get("icmp-code")]
    if record.get("established"):
        entry["established"] = True
    if record.get("fragment"):
        entry["fragments"] = True
    if name := record.get("time-range"):
        if "time-range-active" not in record:
            unsupported.append(f"time_range:{name}")
        entry["time_range"] = {"name": name, "active": bool(record.get("time-range-active"))}
    entry["vrf"] = record.get("network-instance", "default")
    if "matched-packets" in record:
        entry["matches"] = record["matched-packets"]
    if unsupported:
        entry["unsupported"] = unsupported
    return entry


def policy_rows(records):
    """{"acl_rules", "acl_bindings", "pbr_rules", "pbr_bindings"}: the ACL and
    policy-routing inputs of device_acl.py and device_pbr.py, from standard records."""
    rows = {}
    if "acl-entry" in records:
        order = {g["acl"]: g.get("match-order") for g in records.get("acl-group", [])}
        by_acl = {}
        for record in records["acl-entry"]:
            if "acl" in record and "sequence" in record:
                by_acl.setdefault(record["acl"], []).append(record)
        entries = []
        for acl, found in by_acl.items():
            if order.get(acl) == "auto":  # depth first: the device numbers the match order
                found.sort(key=lambda r: (r.get("match-index") is None, r.get("match-index", 0)))
            else:
                found.sort(key=lambda r: r["sequence"])
            entries.extend(_acl_entry(r) for r in found)
        rows["acl_rules"] = entries
    if "acl-attachment" in records or "acl-default-attachment" in records:
        running = {
            (r.get("if-index"), r.get("direction"), r.get("acl")): r.get("in-effect")
            for r in records.get("acl-run", [])
        }
        defaults = records.get("acl-default-action", [])
        default = defaults[0].get("default-action") if len(defaults) == 1 else None
        bindings = {}
        for record in records.get("acl-attachment", []):
            if record.get("object-type") != "interface" or "interface" not in record:
                continue  # not an interface's filter
            key = (record["interface"], record.get("direction"))
            binding = bindings.setdefault(
                key, {"ifname": key[0], "direction": key[1], "family": "ipv4"}
            )
            binding["acl"] = record["acl"]
            state = running.get((record.get("if-index"), record.get("direction"), record["acl"]))
            if state is not True or record.get("problem_fields"):
                binding["status"] = "not_in_effect" if state is False else "state_not_observed"
        for record in records.get("acl-default-attachment", []):
            if record.get("object-type") != "interface" or "interface" not in record:
                continue
            key = (record["interface"], record.get("direction"))
            binding = bindings.setdefault(
                key, {"ifname": key[0], "direction": key[1], "family": "ipv4"}
            )
            if default:
                binding["default_action"] = default
        rows["acl_bindings"] = list(bindings.values())
    if "pbr-node" in records:
        hops = {}
        for record in records.get("pbr-next-hop", []):
            hops.setdefault((record.get("policy"), record.get("node")), []).append(record)
        nodes = []
        for record in sorted(
            records["pbr-node"], key=lambda r: (r.get("policy", ""), r.get("node", 0))
        ):
            if "policy" not in record or "node" not in record or "mode" not in record:
                continue
            node = {"policy": record["policy"], "node": record["node"], "mode": record["mode"]}
            if "acl" in record:
                node["acl"] = record["acl"]
            unsupported = _flagged(record) + [
                f"clause:{field}"
                for field, value in record.items()
                if field.startswith("apply-") and value is not False
            ]
            found = hops.get((record["policy"], record["node"]), [])
            if len(found) == 1 and not found[0].get("problem_fields") and "address" in found[0]:
                node["next_hop"] = found[0]["address"]
            elif found:
                unsupported.append("next_hop_form")
            if unsupported:
                node["unsupported"] = unsupported
            nodes.append(node)
        rows["pbr_rules"] = nodes
    if "pbr-interface" in records or "pbr-local" in records:
        rows["pbr_bindings"] = [
            {"ifname": r["interface"], "policy": r["policy"]}
            for r in records.get("pbr-interface", [])
            if "interface" in r and "policy" in r
        ] + [
            {"policy": r["policy"], "local": True}
            for r in records.get("pbr-local", [])
            if "policy" in r
        ]
    return rows
