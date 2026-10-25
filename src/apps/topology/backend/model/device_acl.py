"""The collected IPv4 ACLs as an RFC 8519 tree, with the RFC 9899 extensions.

An entry is exported only when RFC 8519 and its extensions (RFC 9899, ietf-acl-enh, owner
decision 2026-10-06) express it exactly: a contiguous wildcard is a prefix, "lt N"/"gt N"
are "lte N-1"/"gte N+1", "established" is the TCP flags ACK or RST (RFC 9899
flags-bitmask, operator "any"; the Cisco definition of established). Several ports
in one entry ("eq www 443") are an RFC 9899 port set named "<ACL>:<rule>:src|dst" from
the device's own keys (owner decision 2026-10-06, as PBR's sequence-id). An entry they
cannot express (fragments: "non-initial fragment" is not one fragment-type condition; an
object group whose members were not collected; a time range; a non-contiguous wildcard;
a condition the reading does not know) is left out with a gap that keeps its place and
action: dropping a condition would widen the entry, and a path computed from the tree
cannot decide past it. Exported entries keep the device's match order. The action for
packets no entry matches is not an RFC 8519 leaf and no entry is made up for it; it is
application metadata, like the evidence. Neither RFC 8519 nor RFC 9899 has a network
instance condition, while a Comware entry without vpn-instance matches only public packets
and one with vpn-instance only that instance's. So an ACL is exported as it acts in each
instance it is applied in (owner decision 2026-10-06): under its own name for public
interfaces, as "<ACL>@<instance>" for interfaces in a VPN instance, each with the entries
that can match there and without the instance condition; the entries left out are named
in metadata ``acl_views``.
"""

from __future__ import annotations

import ipaddress

from .device_state import project_device_state

ACL = "ietf-access-control-list:"
ENH = "ietf-acl-enh:"
ESTABLISHED = {"operator": "any", "explicit-tcp-flag": [ENH + "ack", ENH + "rst"]}
ACTIONS = {"permit": ACL + "accept", "deny": ACL + "drop"}
PROTOCOL_CONTAINERS = {6: "tcp", 17: "udp"}


def _prefix(address):
    """address/wildcard as an IPv4 prefix, or None when the wildcard is not contiguous."""
    wildcard = int(ipaddress.IPv4Address(address["wildcard"]))
    length = 32 - wildcard.bit_length()
    if wildcard != (1 << (32 - length)) - 1:
        return None
    return str(ipaddress.ip_network(f"{address['address']}/{length}", strict=False))


def _port(condition):
    """RFC 8519 port-range-or-operator, or None."""
    op, ports = condition["op"], condition["ports"]
    if op == "range":
        return {"lower-port": ports[0], "upper-port": ports[1]}
    if len(ports) != 1:
        return None  # "eq 80 443": one RFC 8519 entry holds one port or one range
    port = ports[0]
    if op in {"eq", "neq"}:
        return {"operator": op, "port": port}
    if op == "lt" and port > 0:
        return {"operator": "lte", "port": port - 1}
    if op == "gt" and port < 65535:
        return {"operator": "gte", "port": port + 1}
    return None


def _ace(entry):
    """(RFC 8519 ace, None, its RFC 9899 port sets) or (None, [why it cannot be
    expressed], [])."""
    why = list(entry.get("unsupported") or [])
    port_sets = []
    for flag, reason in (("fragments", "fragments"), ("time_range", "time_range")):
        if entry.get(flag):
            why.append(reason)
    if entry.get("vrf") not in {None, "default"}:
        why.append("vpn_instance")
    if entry.get("action") not in ACTIONS:
        why.append("action")
    ipv4 = {}
    if "protocol" in entry:
        ipv4["protocol"] = entry["protocol"]
    for key, leaf in (("src", "source-ipv4-network"), ("dst", "destination-ipv4-network")):
        if key in entry:
            prefix = _prefix(entry[key])
            if prefix is None:
                why.append(f"{key}_wildcard")
            else:
                ipv4[leaf] = prefix
    matches = {"ipv4": ipv4} if ipv4 else {}
    container = PROTOCOL_CONTAINERS.get(entry.get("protocol"))
    for key, leaf in (("src_ports", "source-port"), ("dst_ports", "destination-port")):
        if key not in entry:
            continue
        condition = entry[key]
        if container and condition["op"] == "eq" and len(condition["ports"]) > 1:
            if any(port is None for port in condition["ports"]):
                why.append(key)
                continue
            # Several ports: an RFC 9899 port set, named from the device's own keys.
            side = "source" if key == "src_ports" else "destination"
            name = f"{entry['acl']}:{entry['seq']}:{'src' if side == 'source' else 'dst'}"
            port_sets.append(
                {
                    "name": name,
                    "port": [
                        {"id": str(n), "port-range-or-operator": {"operator": "eq", "port": port}}
                        for n, port in enumerate(condition["ports"], 1)
                    ],
                }
            )
            matches.setdefault(container, {})[f"{ENH}{side}-{container}-port-set"] = name
            continue
        port = _port(condition) if container else None
        if port is None:
            why.append(key)
        else:
            matches.setdefault(container, {})[leaf] = port
    if entry.get("established"):
        if container != "tcp":
            why.append("established")
        else:
            matches.setdefault("tcp", {})[ENH + "flags-bitmask"] = dict(ESTABLISHED)
    if "icmp" in entry:
        kind, code = entry["icmp"]
        matches["icmp"] = {"type": kind, **({"code": code} if code is not None else {})}
    if why:
        return None, sorted(set(why)), []
    ace = {"name": str(entry["seq"]), "actions": {"forwarding": ACTIONS[entry["action"]]}}
    if matches:
        ace["matches"] = matches
    if "matches" in entry:
        ace["statistics"] = {"matched-packets": str(entry["matches"])}
    return ace, None, port_sets


def project_device_acl(observation):
    """{"tree", "metadata", "gaps"}: the device's IPv4 ACLs and where they are applied."""
    data = observation.get("data", observation) if isinstance(observation, dict) else {}
    facts = data.get("facts", data) if isinstance(data, dict) else {}
    if not isinstance(facts, dict):
        facts = {}
    metadata = {
        "standard": "RFC 8519, RFC 9899",
        "content": "operational",
        "completeness": "partial",
        "full_validation": "not_performed",
    }
    for source, target in (("id", "observation_id"), ("observed_at", "observed_at")):
        if isinstance(observation, dict) and isinstance(observation.get(source), str):
            metadata[target] = observation[source]
    result = {"tree": {}, "metadata": metadata, "gaps": []}
    rules, bindings = facts.get("acl_rules"), facts.get("acl_bindings")
    if not isinstance(rules, list):
        result["gaps"].append({"code": "acl_rules_not_observed"})
        rules = []
    if not isinstance(bindings, list):
        result["gaps"].append({"code": "acl_bindings_not_observed"})
        bindings = []
    entries = {}
    for entry in rules:
        if entry.get("family", "ipv4") == "ipv4":
            entries.setdefault(entry["acl"], []).append(entry)
    acls, port_sets, views = {}, [], []

    def view(name, scope):
        """The ACL as it acts on packets of one network instance: its entries with no
        instance condition or with this instance's, in the device's order; an entry of
        another instance never matches there and is left out."""
        key = name if scope == "default" else f"{name}@{scope}"
        if key in acls:
            return key
        acl = acls[key] = {"name": key, "type": ACL + "ipv4-acl-type"}
        omitted, position = [], -1
        for entry in entries[name]:
            if entry.get("vrf") not in {None, scope}:
                omitted.append(entry["seq"])
                continue
            position += 1
            ace, why, sets = _ace({k: v for k, v in entry.items() if k != "vrf"} | {"acl": key})
            port_sets.extend(sets)
            if ace is None:
                result["gaps"].append(
                    {
                        "code": "acl_entry_not_expressible",
                        "acl": key,
                        "entry": entry["seq"],
                        "position": position,
                        **({"action": entry["action"]} if entry.get("action") in ACTIONS else {}),
                        "reasons": why,
                    }
                )
                continue
            acl.setdefault("aces", {"ace": []})["ace"].append(ace)
        if key != name or omitted:
            views.append(
                {"acl": key, "of": name, "network_instance": scope, "omitted_entries": omitted}
            )
        return key

    for name in entries:
        view(name, "default")
    rows = (
        project_device_state(observation)["tree"]
        .get("ietf-interfaces:interfaces", {})
        .get("interface", [])
    )
    names = {row["name"] for row in rows}
    instance = {row["ifname"]: row.get("vrf", "default") for row in facts.get("ip") or []}
    points, defaults, used = {}, [], set()
    for binding in bindings:
        if binding.get("family") not in {None, "ipv4"} or "acl" not in binding:
            continue
        where = {"interface": binding["ifname"], "direction": binding["direction"], "acl": binding["acl"]}
        if binding.get("scope") or binding.get("status"):
            if binding.get("default_action"):
                defaults.append({**where, "default_action": binding["default_action"]})
            result["gaps"].append(
                {"code": "acl_attachment_not_expressible", **where,
                 "reason": binding.get("scope") or binding.get("status")}
            )
            continue
        if binding["acl"] not in entries:
            if binding.get("default_action"):
                defaults.append({**where, "default_action": binding["default_action"]})
            result["gaps"].append({"code": "attached_acl_not_observed", **where})
            continue
        if binding["ifname"] not in names:
            if binding.get("default_action"):
                defaults.append({**where, "default_action": binding["default_action"]})
            result["gaps"].append({"code": "acl_interface_reference_not_proven", **where})
            continue
        # The interface's instance decides which entries can match there.
        attached = view(binding["acl"], instance.get(binding["ifname"], "default"))
        if binding.get("default_action"):
            defaults.append({**where, "acl": attached, "default_action": binding["default_action"]})
        side = "ingress" if binding["direction"] == "in" else "egress"
        point = points.setdefault(binding["ifname"], {"interface-id": binding["ifname"]})
        point.setdefault(side, {"acl-sets": {"acl-set": []}})["acl-sets"]["acl-set"].append(
            {"name": attached}
        )
        used.add(binding["ifname"])
    if views:
        metadata["acl_views"] = views
    if defaults:
        metadata["default_actions"] = defaults
    if acls:
        tree = {"acl": list(acls.values())}
        if port_sets:
            tree[ENH + "defined-sets"] = {"port-sets": {"port-set": port_sets}}
        if points:
            tree["attachment-points"] = {"interface": [points[name] for name in sorted(points)]}
        result["tree"][ACL + "acls"] = tree
    if used:
        # An attachment point references the interface (RFC 8343), so it is included.
        result["tree"]["ietf-interfaces:interfaces"] = {
            "interface": [row for row in rows if row["name"] in used]
        }
    return result
