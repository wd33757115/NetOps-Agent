"""Whether an IPv4 ACL lets one packet through, from the device's own entries.

A packet is one concrete flow: its addresses, the network instance it is in, and as far as
the caller knows them the protocol, ports and ICMP type/code, and whether it is a reply
(``reply``: a TCP segment with ACK set, which "established" matches, or an echo reply).

Entries are taken in the device's match order (as it lists them). Each is checked
condition by condition: a condition the packet fails rules the entry out; one that cannot
be decided (the caller left the field open, or the entry has a condition this module does
not evaluate) leaves the entry undecided. The result is the action every possible outcome
agrees on, or ``undetermined`` with the entries that decide it and what would settle them.
Traced packets are whole (no fragment), so an entry for non-initial fragments never
matches them; a time range counts as it was when the ACL was collected.
"""

from __future__ import annotations

import ipaddress

TCP = 6
UDP = 17
ICMP = 1
ACTIONS = frozenset({"permit", "deny"})


def _covers(address, entry_address):
    value = int(ipaddress.IPv4Address(address))
    base = int(ipaddress.IPv4Address(entry_address["address"]))
    wildcard = int(ipaddress.IPv4Address(entry_address["wildcard"]))
    return value & ~wildcard == base & ~wildcard


def _port(condition, value):
    op, ports = condition["op"], condition["ports"]
    if any(port is None for port in ports):
        return None  # a port name the dialect table lacks (listed as unsupported)
    if op == "eq":
        return value in ports
    if op == "neq":
        return value != ports[0]
    if op == "lt":
        return value < ports[0]
    if op == "gt":
        return value > ports[0]
    if op == "lte":  # RFC 8519's operators, as the query table carries them
        return value <= ports[0]
    if op == "gte":
        return value >= ports[0]
    return ports[0] <= value <= ports[1]


def entry_matches(entry, packet):
    """(True, []) / (False, []) / (None, [what would decide it])."""
    if entry.get("family", "ipv4") != "ipv4":
        return False, []
    open_ = list(entry.get("unsupported") or [])
    if "vrf" in entry and entry["vrf"] != packet["vrf"]:
        return False, []
    if "time_range" in entry and not entry["time_range"]["active"]:
        return False, []
    if entry.get("fragments"):
        return False, []
    protocol = packet.get("protocol")
    if "protocol" in entry:
        if protocol is None:
            open_.append("protocol")
        elif protocol != entry["protocol"]:
            return False, []
    for key in ("src", "dst"):
        if key not in entry:
            continue
        if packet.get(key) is None:
            open_.append(key)  # an address NAT left open (an address of a pool)
        elif not _covers(packet[key], entry[key]):
            return False, []
    for key in ("src_port", "dst_port"):
        if f"{key}s" not in entry:
            continue
        if packet.get(key) is None:
            open_.append(key)
        elif _port(entry[f"{key}s"], packet[key]) is False:
            return False, []
    if "icmp" in entry:
        kind, code = entry["icmp"]
        if packet.get("icmp") is None:
            open_.append("icmp")
        elif packet["icmp"][0] != kind or (code is not None and packet["icmp"][1] != code):
            return False, []
    if entry.get("established"):
        if protocol is None:
            open_.append("protocol")
        elif protocol != TCP or not packet.get("reply"):
            return False, []
    if entry.get("action") not in ACTIONS:
        open_.append("unknown_entry")
    return (None, sorted(set(open_))) if open_ else (True, [])


def evaluate(entries, packet, default_action):
    """{"result": "permit"|"deny", "rule": seq|"default"} when every outcome agrees
    (``undecided_before``: earlier entries that may match first, with the same action), or
    {"result": "undetermined", "rules": [seq...], "needs": [...]}."""
    possible, undecided, needs = set(), [], []
    for entry in entries:
        matched, open_ = entry_matches(entry, packet)
        if matched is False:
            continue
        if matched is None:
            possible |= {entry["action"]} if entry.get("action") in ACTIONS else set(ACTIONS)
            undecided.append(entry["seq"])
            needs += open_
            continue
        possible.add(entry["action"])
        if len(possible) == 1:
            return {
                "result": entry["action"],
                "rule": entry["seq"],
                **({"undecided_before": undecided} if undecided else {}),
            }
        return {
            "result": "undetermined",
            "rules": [*undecided, entry["seq"]],
            "needs": sorted(set(needs)),
        }
    if default_action in ACTIONS:
        possible.add(default_action)
    else:
        possible |= set(ACTIONS)
        needs.append("default_action")
    if len(possible) == 1:
        return {
            "result": default_action,
            "rule": "default",
            **({"undecided_before": undecided} if undecided else {}),
        }
    return {"result": "undetermined", "rules": undecided, "needs": sorted(set(needs))}
