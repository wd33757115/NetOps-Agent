"""Network address translation of one traced packet, from the device's own NAT rules.

Rules come one record each (collectors/rules.py ``_nat_rule``): an address-pool range, a
static mapping of one address or one protocol port, or source NAT by ACL to an interface
address or a pool. A rule bound to an interface applies there. A rule of no interface of
its own applies at the interfaces whose NAT role is "outside"; when the device has
"inside" interfaces, source NAT then only applies to packets that came in on one.

Destination NAT (a static mapping's global address or port) rewrites a packet arriving on
such an interface, before its route lookup; source NAT rewrites one leaving by it, after
the lookup: the order IOS and Comware both use, with the inbound ACL before and the
outbound ACL after. A translation is exact or not made: the address a pool gives and a
translated port are left open (None). A rule this reading cannot apply, or a match that
depends on what the caller left open, is "undetermined" with what would settle it.

A firewall's NAT policy (rules of type "policy", standard_tables.zone_rows) is matched in
the device's order on the zones of the interface the packet came in by (``local`` for
what the device sends itself) and the one it leaves by, and on its addresses; the first
rule that matches decides, and none means no translation (the policy's default no-nat).
Only source NAT to the leaving interface's address (easy-ip) and no-nat are applied.
"""

from __future__ import annotations

import ipaddress

from .acl import evaluate


def roles(facts):
    return {
        row["ifname"]: row["role"]
        for row in facts.get("nat_bindings") or []
        if row.get("role")
    }


def interface_address(facts, ifname):
    """The interface's one IPv4 address, or None when it has none or several."""
    found = {
        row["address"]
        for row in facts.get("ip") or []
        if row.get("ifname") == ifname and ipaddress.ip_address(row["address"]).version == 4
    }
    return found.pop() if len(found) == 1 else None


def _applies(rule, ifname, interface_roles):
    if "interface" in rule:
        return rule["interface"] == ifname
    return interface_roles.get(ifname) == "outside"


def _global(facts, rule):
    if "global" in rule:
        return rule["global"]
    return interface_address(facts, rule.get("global_interface"))


def _undetermined(rule, needs):
    return {"undetermined": {"rule": rule["kind"], "needs": sorted(set(needs))}}


def destination(facts, ifname, packet):
    """{"packet", "translation"} / {"undetermined": {...}} / None for a packet arriving."""
    interface_roles = roles(facts)
    for rule in facts.get("nat_rules") or []:
        if rule.get("type") != "static" or not _applies(rule, ifname, interface_roles):
            continue
        address = _global(facts, rule)
        if address is None:
            return _undetermined(rule, ["global_address"])
        if packet["dst"] != address:
            continue
        if rule.get("unsupported"):
            return _undetermined(rule, rule["unsupported"])
        needs = []
        if "protocol" in rule:
            if packet.get("protocol") is None:
                needs.append("protocol")
            elif packet["protocol"] != rule["protocol"]:
                continue
        if "global_port" in rule:
            if packet.get("dst_port") is None:
                needs.append("dst_port")
            elif packet["dst_port"] != rule["global_port"]:
                continue
        if needs:
            return _undetermined(rule, needs)
        after = {**packet, "dst": rule["local"]}
        if "local_port" in rule:
            after["dst_port"] = rule["local_port"]
        return {"packet": after, "translation": _record("destination", ifname, rule, packet, after)}
    return None


def _zone(facts, ifname):
    if ifname is None:
        return "local"
    found = {row["zone"] for row in facts.get("security_zones") or [] if row.get("ifname") == ifname}
    return found.pop() if len(found) == 1 else None


def _covered(address, prefixes):
    if not prefixes:
        return True  # no address condition
    if address is None:
        return None
    return any(ipaddress.ip_address(address) in ipaddress.ip_network(p) for p in prefixes)


def _policy(facts, in_if, out_if, packet):
    """The NAT policy's outcome for a packet leaving by out_if, or None (no translation)."""
    zones = (_zone(facts, in_if), _zone(facts, out_if))
    for rule in sorted(
        (r for r in facts.get("nat_rules") or [] if r.get("type") == "policy"),
        key=lambda r: r.get("order", 0),
    ):
        if rule.get("enabled") is False:
            continue
        needs = []
        for zone, allowed, name in zip(zones, (rule["source_zones"], rule["destination_zones"]), ("in", "out")):
            if not allowed:
                continue
            if zone is None:
                needs.append(f"zone_of_{name}_interface")
            elif zone not in allowed:
                break
        else:
            matched = [_covered(packet.get("src"), rule["sources"]), _covered(packet.get("dst"), rule["destinations"])]
            if False in matched:
                continue
            needs += [field for field, ok in zip(("src", "dst"), matched) if ok is None]
            if needs or rule.get("unsupported"):
                return _undetermined(rule, needs + rule.get("unsupported", []))
            if rule["action"] == "none":
                return None
            after = {**packet, "src": interface_address(facts, out_if), "src_port": None}
            return {"packet": after, "translation": _record("source", out_if, rule, packet, after)}
    return None


def source(facts, in_if, out_if, packet, vrf="default"):
    """{"packet", "translation"} / {"undetermined": {...}} / None for a packet leaving
    (``vrf``: the network instance it is in, for the ACL that selects it)."""
    if any(rule.get("type") == "policy" for rule in facts.get("nat_rules") or []):
        return _policy(facts, in_if, out_if, packet)
    interface_roles = roles(facts)
    if "inside" in interface_roles.values() and interface_roles.get(in_if) != "inside":
        return None  # rules of no interface translate only from inside to outside
    rules = [
        rule
        for rule in facts.get("nat_rules") or []
        if rule.get("type") in {"static", "source"} and _applies(rule, out_if, interface_roles)
    ]
    for rule in (rule for rule in rules if rule["type"] == "static"):
        if packet["src"] != rule["local"]:
            continue
        if rule.get("unsupported"):
            return _undetermined(rule, rule["unsupported"])
        if "local_port" in rule:
            # A port mapping translates what leaves from that local port only.
            if packet.get("protocol") is None or packet.get("src_port") is None:
                return _undetermined(rule, ["protocol", "src_port"])
            if (packet["protocol"], packet["src_port"]) != (rule["protocol"], rule["local_port"]):
                continue
        address = _global(facts, rule)
        if address is None:
            return _undetermined(rule, ["global_address"])
        after = {**packet, "src": address}
        if "global_port" in rule:
            after["src_port"] = rule["global_port"]
        return {"packet": after, "translation": _record("source", out_if, rule, packet, after)}
    for rule in (rule for rule in rules if rule["type"] == "source"):
        if rule.get("unsupported"):
            return _undetermined(rule, rule["unsupported"])
        if "acl" in rule:
            entries = [row for row in facts.get("acl_rules") or [] if row["acl"] == rule["acl"]]
            if not entries:
                return _undetermined(rule, ["acl_entries"])
            # An entry that denies, or none that matches, leaves the packet untranslated.
            outcome = evaluate(entries, {**packet, "vrf": vrf}, "deny")
            if outcome["result"] == "deny":
                continue
            if outcome["result"] == "undetermined":
                return _undetermined(rule, outcome["needs"])
        after = {**packet}
        if "pool" in rule:
            after["src"] = None  # an address of the pool, which one is not known
            pool = [
                [row["start"], row["end"]]
                for row in facts.get("nat_rules") or []
                if row.get("type") == "pool" and row.get("pool") == rule["pool"]
            ]
        else:
            after["src"] = interface_address(facts, rule.get("to_interface") or out_if)
            pool = None
        if rule.get("port_translation"):
            after["src_port"] = None
        translation = _record("source", out_if, rule, packet, after)
        if pool is not None:
            translation["pool"] = pool
        return {"packet": after, "translation": translation}
    return None


def _record(kind, ifname, rule, before, after):
    changed = [field for field in ("src", "src_port", "dst", "dst_port") if before.get(field) != after.get(field)]
    return {
        "nat": kind,
        "interface": ifname,
        "rule": {
            key: rule[key]
            for key in ("kind", "name", "acl", "pool", "local", "global", "global_interface", "local_port", "global_port")
            if key in rule
        },
        "before": {field: before.get(field) for field in changed},
        "after": {field: after.get(field) for field in changed},
    }
