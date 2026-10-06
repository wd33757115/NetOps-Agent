"""The ACL query table, taken from the RFC 8519 / RFC 9899 tree (device_acl.py).

One row per interface and direction an IPv4 ACL is attached to: the ACL's entries in the
device's match order, as the tree has them (``name``, ``action``, ``matches`` with the
standard node names, ``matched-packets``), and the action for packets no entry matches
(application metadata of the tree: it is no RFC 8519 leaf). An entry the tree could not
express keeps its place as ``undetermined`` with the reasons, so a packet that reaches
it cannot be decided past it (owner decision 2026-10-06). An attachment the tree could
not express is a row without entries, ``complete`` false, with the reasons.

``entries`` turns a row's entries into the conditions model/acl.py checks a packet with.
"""

from __future__ import annotations

import ipaddress

from .device_acl import ACL, ENH, ESTABLISHED, project_device_acl

DIRECTIONS = {"ingress": "in", "egress": "out"}
FORWARDING = {ACL + "accept": "permit", ACL + "drop": "deny", ACL + "reject": "deny"}
NOT_ATTACHED = {
    "acl_attachment_not_expressible",
    "attached_acl_not_observed",
    "acl_interface_reference_not_proven",
    "acl_instance_condition_not_expressible",
}


def _entry(ace, sets):
    """An exported entry; a port set it references is resolved into its ports."""
    matches = ace.get("matches", {})
    for container in ("tcp", "udp"):
        for side in ("source", "destination"):
            leaf = f"{ENH}{side}-{container}-port-set"
            if leaf in matches.get(container, {}):
                matches = {**matches, container: {**matches[container], leaf: sets.get(matches[container][leaf], [])}}
    found = {"name": ace["name"], "matches": matches}
    forwarding = ace.get("actions", {}).get("forwarding")
    if forwarding in FORWARDING:
        found["action"] = FORWARDING[forwarding]
    if "statistics" in ace:
        found["matched-packets"] = int(ace["statistics"]["matched-packets"])
    return found


def query_table(projection):
    """[{interface-id, direction, acl, entries, default-action, complete, reasons}]."""
    tree, gaps = projection.get("tree") or {}, projection.get("gaps") or []
    acls = tree.get(ACL + "acls", {})
    held = {}
    for gap in gaps:
        if gap.get("code") == "acl_entry_not_expressible":
            held.setdefault(gap["acl"], {})[gap["position"]] = {
                "name": str(gap["entry"]),
                **({"action": gap["action"]} if "action" in gap else {}),
                "undetermined": gap["reasons"],
            }
    sets = {
        row["name"]: [port["port-range-or-operator"] for port in row["port"]]
        for row in acls.get(ENH + "defined-sets", {}).get("port-sets", {}).get("port-set", [])
    }
    ordered = {}
    for acl in acls.get("acl", []):
        aces = iter(acl.get("aces", {}).get("ace", []))
        places = held.get(acl["name"], {})
        total = len(acl.get("aces", {}).get("ace", [])) + len(places)
        ordered[acl["name"]] = [
            places[n] if n in places else _entry(next(aces), sets) for n in range(total)
        ]
    defaults = {
        (row["interface"], row["direction"], row["acl"]): row["default_action"]
        for row in (projection.get("metadata") or {}).get("default_actions", [])
    }
    rows = []
    for point in acls.get("attachment-points", {}).get("interface", []):
        for side, direction in DIRECTIONS.items():
            for attached in point.get(side, {}).get("acl-sets", {}).get("acl-set", []):
                key = (point["interface-id"], direction, attached["name"])
                rows.append(
                    {
                        "interface-id": point["interface-id"],
                        "direction": direction,
                        "acl": attached["name"],
                        "entries": ordered.get(attached["name"], []),
                        "default-action": defaults.get(key),
                        "complete": True,
                        "reasons": [],
                    }
                )
    for gap in gaps:
        if gap.get("code") in NOT_ATTACHED:
            reason = gap["code"] + (f":{gap['reason']}" if gap.get("reason") else "")
            rows.append(
                {
                    "interface-id": gap["interface"],
                    "direction": gap["direction"],
                    "acl": gap["acl"],
                    "entries": [],
                    "default-action": defaults.get((gap["interface"], gap["direction"], gap["acl"])),
                    "complete": False,
                    "reasons": [reason],
                }
            )
    return rows


def _address(prefix):
    network = ipaddress.ip_network(prefix)
    return {"address": str(network.network_address), "wildcard": str(network.hostmask)}


def _port(value):
    if "lower-port" in value:
        return {"op": "range", "ports": [value["lower-port"], value["upper-port"]]}
    return {"op": value["operator"], "ports": [value["port"]]}


def _seq(name):
    return int(name) if name.isdigit() else name


def entries(row):
    """A row's entries as the conditions model/acl.py evaluates (src/dst address and
    wildcard, protocol, ports (a port set as its ports), ICMP, established)."""
    found = []
    for entry in row["entries"]:
        record = {"seq": _seq(entry["name"])}
        if "action" in entry:
            record["action"] = entry["action"]
        if "undetermined" in entry:
            record["unsupported"] = list(entry["undetermined"])
            found.append(record)
            continue
        matches = entry["matches"]
        ipv4 = matches.get("ipv4", {})
        if "protocol" in ipv4:
            record["protocol"] = ipv4["protocol"]
        for leaf, key in (("source-ipv4-network", "src"), ("destination-ipv4-network", "dst")):
            if leaf in ipv4:
                record[key] = _address(ipv4[leaf])
        for container in ("tcp", "udp"):
            for side, key in (("source", "src_ports"), ("destination", "dst_ports")):
                fields = matches.get(container, {})
                if f"{side}-port" in fields:
                    record[key] = _port(fields[f"{side}-port"])
                members = fields.get(f"{ENH}{side}-{container}-port-set")
                if members is not None:
                    if members and all(m.get("operator") == "eq" for m in members):
                        record[key] = {"op": "eq", "ports": [m["port"] for m in members]}
                    else:
                        record.setdefault("unsupported", []).append(f"{key}_set")
        flags = matches.get("tcp", {}).get(ENH + "flags-bitmask")
        if flags == ESTABLISHED:
            record["established"] = True
        elif flags is not None:
            record.setdefault("unsupported", []).append("tcp_flags")
        if "icmp" in matches:
            record["icmp"] = [matches["icmp"]["type"], matches["icmp"].get("code")]
        if "matched-packets" in entry:
            record["matches"] = entry["matched-packets"]
        found.append(record)
    return found


def acl_tables(facts):
    """The device's ACL query table, from its standard tree."""
    return query_table(project_device_acl({"facts": facts}))
