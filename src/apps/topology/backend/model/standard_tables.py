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

from collections import defaultdict

from .device_state import INTERFACE_TYPES
from .standard_trees import IF, OC_LLDP, device_tree, openconfig_lldp_section

KINDS = {identity: kind for kind, identity in INTERFACE_TYPES.items()}
SUBTYPES = {"mac-address": "MAC address", "interface-name": "Interface name"}
def _mac(ieee):
    return ieee.replace("-", ":").lower()


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
        if leaf == "address":
            # The RFC 8343 interface tree cannot contain interfaces without an IANA
            # interface type (Huawei MEth is one example). Their addresses still identify
            # the collected device and must join LLDP management-address claims to it.
            # Keep these addresses as builder facts, but do not invent an interface in the
            # RFC 8343 tree. Any L3 point remains unbound unless its L2 attachment is observed.
            tree_interfaces = {entry["name"] for entry in device}
            instances = {
                record["name"]: record.get("network-instance", "default")
                for record in records.get("interface", [])
                if record.get("name")
            }
            seen = {
                (row["ifname"], row["vrf"], row["address"], row["prefix_len"])
                for row in found
            }
            for item in records.get("ipv4-address", []):
                if item.get("origin") == "other":
                    continue
                ifname = item.get("interface")
                if not ifname or ifname in tree_interfaces or ifname not in instances:
                    continue
                if "ip" not in item or "prefix-length" not in item:
                    continue
                row = (
                    ifname,
                    instances[ifname],
                    item["ip"],
                    item["prefix-length"],
                )
                if row in seen:
                    continue
                seen.add(row)
                found.append(
                    {
                        "ifname": ifname,
                        "vrf": row[1],
                        "address": row[2],
                        "prefix_len": row[3],
                    }
                )
        rows[key] = found
    if "lldp-neighbor" in records:
        found = []
        tree = openconfig_lldp_section(records, platform)["tree"][OC_LLDP]
        for interface in tree.get("interfaces", {}).get("interface", []):
            for neighbor in interface.get("neighbors", {}).get("neighbor", []):
                remote = neighbor.get("state", {})
                if "chassis-id" not in remote or "port-id" not in remote:
                    continue
                chassis = remote["chassis-id"]
                if remote.get("chassis-id-type") == "mac-address":
                    chassis = _mac(chassis)
                addresses = [
                    item["state"]["address"]
                    for item in neighbor.get("mgmt-addresses", {}).get("mgmt-address", [])
                    if item.get("state", {}).get("address")
                ]
                found.append(
                    {
                        "local_if": interface["name"],
                        "remote_chassis_id": chassis,
                        "remote_chassis_subtype": SUBTYPES.get(
                            remote.get("chassis-id-type"), remote.get("chassis-id-type")
                        ),
                        "remote_port_id": remote["port-id"],
                        "remote_port_subtype": SUBTYPES.get(
                            remote.get("port-id-type"), remote.get("port-id-type")
                        ),
                        "remote_sysname": remote.get("system-name"),
                        "remote_system_description": remote.get("system-description"),
                        "remote_mgmt_addresses": addresses,
                    }
                )
        rows["lldp"] = found
    rows.update(policy_rows(records))
    rows.update(layer2_rows(records, platform, rows.get("interfaces")))
    rows.update(zone_rows(records))
    return rows


def _vid_range(text):
    """The device's VLAN list ("1,10,20", "2-5") as IEEE 802.1Q vid-range text, or None."""
    from ..collectors.rules_tables import vid_range

    try:
        return vid_range(text or "")
    except ValueError:
        return None


def layer2_rows(records, platform, interfaces=None):
    """{"lag", "trunk", "vlan", "vlan_members", "mac"} and each access port's VLAN on its
    interface row, from standard records: the inputs of builder.py and l2_forwarding.py.

    IEEE 802.1Q and 802.1AX are not yet built as trees from these records; the rows carry
    the device's values as read (the plan's non-standard query-table part).
    """
    rows = {}
    if "lag" in records:
        members = defaultdict(list)
        for record in records.get("lag-member", []):
            if "aggregate-interface" in record and "name" in record:
                members[record["aggregate-interface"]].append(record)
        found = []
        for record in records["lag"]:
            if "name" not in record:
                continue
            ports = members.get(record["name"], [])
            names = [m["name"] for m in ports]
            found.append(
                {
                    "ifname": record["name"],
                    "members": names,
                    "bundled": [m["name"] for m in ports if m.get("selected") is True],
                    "member_flags": {
                        m["name"]: "selected" if m.get("selected") is True else "not-selected"
                        for m in ports
                    },
                    **({"protocol": record["mode"]} if "mode" in record else {}),
                }
            )
        rows["lag"] = found
    if "bridge-port" in records:
        trunks, invalid, access = [], [], {}
        for record in records["bridge-port"]:
            name = record.get("name")
            if not name or record.get("problem_fields"):
                if name:
                    invalid.append(name)
                continue
            if record.get("link-type") == "trunk":
                allowed = _vid_range(record.get("trunk-vlans"))
                if allowed is None or "pvid" not in record:
                    invalid.append(name)
                    continue
                trunks.append(
                    {
                        "ifname": name,
                        "raw_name": name,
                        "l2_mode": "trunk",
                        "encapsulation": "dot1q",
                        "native_vlan": record["pvid"],
                        "trunk_vlans": allowed,
                    }
                )
            elif record.get("link-type") == "access" and "pvid" in record:
                access[name] = record["pvid"]
        summary = {"ports": trunks}
        if invalid:
            summary["invalid_ports"] = sorted(invalid)
        rows["trunk"] = summary
        for row in interfaces or []:
            if row["ifname"] in access:
                row["l2_mode"] = "access"
                row["access_vlan"] = access[row["ifname"]]
    if "vlan" in records:
        rows["vlan"] = [
            {"vlan_id": r["vid"], "name": r.get("name") or str(r["vid"])}
            for r in records["vlan"]
            if "vid" in r
        ]
        ports = defaultdict(list)
        for record in records.get("vlan-member", []):
            if "vid" in record and "interface" in record:
                ports[record["vid"]].append(record["interface"])
        rows["vlan_members"] = [
            {"vlan_id": r["vlan_id"], "ports": ports.get(r["vlan_id"], [])} for r in rows["vlan"]
        ]
    if "mac-entry" in records:
        rows["mac"] = [
            {
                "vlan_or_bd": str(r["vid"]),
                "mac": r["address"],
                "port": r["interface"],
                "type": r.get("type"),
            }
            for r in records["mac-entry"]
            if {"vid", "address", "interface"} <= set(r)
        ]
    return rows


# NAT policy actions this reading applies; any other (or none read) is not evaluated.
NAT_ACTIONS = {"source-nat-easy-ip": "source", "no-nat": "none"}


def zone_rows(records):
    """{"security_zones", "nat_rules"}: interface zones and zone-based NAT policy rules.

    RFC 8512 has no security zone: a policy rule is kept with its zones as read, marked
    non-standard, and model/nat.py matches its zones from the interfaces' zones.
    """
    rows = {}
    if "interface" in records:
        zones = [
            {"zone": r["security-zone"], "ifname": r["name"]}
            for r in records["interface"]
            if r.get("security-zone") and r.get("name")
        ]
        if zones or "security-zone" in records:
            rows["security_zones"] = zones
    if "nat-policy-rule" in records:
        sources, destinations = defaultdict(list), defaultdict(list)
        for record in records.get("nat-policy-source", []):
            if "rule" in record and "prefix" in record:
                sources[record["rule"]].append(record["prefix"])
        for record in records.get("nat-policy-destination", []):
            if "rule" in record and "prefix" in record:
                destinations[record["rule"]].append(record["prefix"])
        found = []
        for index, record in enumerate(records["nat-policy-rule"]):
            if "name" not in record:
                continue
            rule = {
                "kind": "nat-policy",
                "type": "policy",
                "name": record["name"],
                "order": index,
                "enabled": record.get("enabled", True),
                "source_zones": record.get("source-zone", []),
                "destination_zones": record.get("destination-zone", []),
                "sources": sources.get(record["name"], []),
                "destinations": destinations.get(record["name"], []),
                "non_standard": "zone-based NAT policy: RFC 8512 has no security zone",
            }
            action = NAT_ACTIONS.get(record.get("action"))
            unsupported = _flagged(record)
            if record.get("egress-interface"):
                unsupported.append("egress-interface")
            if action is None:
                unsupported.append(f"action:{record.get('action', 'not read')}")
            else:
                rule["action"] = action
            if unsupported:
                rule["unsupported"] = unsupported
            found.append(rule)
        rows["nat_rules"] = found
        # Policy rules apply by zone: there is no interface binding to observe.
        rows["nat_bindings"] = []
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
