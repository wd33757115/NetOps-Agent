"""The policy-routing query table and what a policy does to one packet.

The table is taken from the standard tree (device_pbr.py), not from the collected rows:
one row per interface a policy is applied to, with the policy's rules in order. Field
names are the tree's own (network-instance, interface-id, policy-id, sequence-id,
source-address, next-hop, forwarding-action). Where the tree is partial for a binding
(gaps of device_pbr), the row says so: ``complete`` is false when the rules stop before a
node that could not be expressed, and an interface whose binding could not be exported
at all has no rules and ``reasons``.

A packet is checked against the rules in sequence-id order and the first that matches
decides (Comware evaluates nodes in order; a node's entries are expanded in order). A
condition the packet leaves open (a protocol or port the caller did not give, an address
NAT left open) leaves a rule undecided; the result is the action every outcome agrees
on, as for an ACL (model/acl.py).
"""

from __future__ import annotations

import ipaddress

from .device_pbr import NI, STEP

NOT_EXPORTED = {
    "bound_policy_not_observed",
    "pbr_interface_reference_not_proven",
    "pbr_interface_type_not_observed",
    "pbr_binding_not_expressible",
}


def query_table(projection):
    """[{network-instance, interface-id, policy-id, rules, complete, reasons}]."""
    tree, gaps = projection.get("tree") or {}, projection.get("gaps") or []
    stopped = {
        (gap["network_instance"], gap["policy"]): gap
        for gap in gaps
        if gap.get("code") == "pbr_node_not_expressible"
    }
    rows = []
    for instance in tree.get(NI + "network-instances", {}).get("network-instance", []):
        forwarding = instance.get("policy-forwarding", {})
        policies = {
            policy["policy-id"]: policy
            for policy in forwarding.get("policies", {}).get("policy", [])
        }
        for point in forwarding.get("interfaces", {}).get("interface", []):
            policy_id = point["config"]["apply-forwarding-policy"]
            rules = []
            for rule in policies[policy_id].get("rules", {}).get("rule", []):
                match = {
                    **rule.get("ipv4", {}).get("config", {}),
                    **rule.get("transport", {}).get("config", {}),
                }
                action = rule["action"]
                rules.append(
                    {
                        "sequence-id": rule["sequence-id"],
                        "description": rule["config"].get("description"),
                        "match": match,
                        "action": action.get("config")
                        or action.get("forwarding", {}).get("config"),
                    }
                )
            stop = stopped.get((instance["name"], policy_id))
            rows.append(
                {
                    "network-instance": instance["name"],
                    "interface-id": point["interface-id"],
                    "policy-id": policy_id,
                    "rules": sorted(rules, key=lambda rule: rule["sequence-id"]),
                    "complete": stop is None,
                    "reasons": [
                        {"node": stop["node"], "reasons": stop["reasons"]}
                    ] if stop else [],
                }
            )
    exported = {row["interface-id"] for row in rows}
    for gap in gaps:
        if gap.get("code") in NOT_EXPORTED and gap["interface"] not in exported:
            rows.append(
                {
                    "network-instance": None,
                    "interface-id": gap["interface"],
                    "policy-id": gap["policy"],
                    "rules": [],
                    "complete": False,
                    "reasons": [{"binding": gap["code"]}],
                }
            )
    return rows


def _covers(value, prefix):
    return ipaddress.IPv4Address(value) in ipaddress.IPv4Network(prefix)


def _port(value, condition):
    if condition == "ANY":
        return True
    if isinstance(condition, int):
        return value == condition
    low, high = (int(part) for part in condition.split(".."))
    return low <= value <= high


def rule_matches(rule, packet):
    """(True, []) / (False, []) / (None, [what would decide it])."""
    match, open_ = rule["match"], []
    if "protocol" in match:
        if packet.get("protocol") is None:
            open_.append("protocol")
        elif packet["protocol"] != match["protocol"]:
            return False, []
    for key, leaf in (("src", "source-address"), ("dst", "destination-address")):
        if leaf not in match:
            continue
        if packet.get(key) is None:
            open_.append(key)
        elif not _covers(packet[key], match[leaf]):
            return False, []
    for key, leaf in (("src_port", "source-port"), ("dst_port", "destination-port")):
        if leaf not in match:
            continue
        if packet.get(key) is None:
            open_.append(key)
        elif not _port(packet[key], match[leaf]):
            return False, []
    return (None, sorted(set(open_))) if open_ else (True, [])


def node(rule):
    return rule["sequence-id"] // STEP


def evaluate(row, packet):
    """What the policy applied at one interface does to the packet:

    {"result": "next-hop", "next-hop": ip, "rule": sequence-id, "node": n}: forwarded to
    that next hop; {"result": "forward", "rule", "node"}: a matching node sends it by the
    routing table; {"result": "no-match"}: no node matches, the routing table decides;
    {"result": "undetermined", "rules": [...], "needs": [...]}.
    """
    possible, undecided, needs = [], [], []

    def outcome(rule):
        action = rule["action"]
        if "next-hop" in action:
            return {"result": "next-hop", "next-hop": action["next-hop"]}
        return {"result": "forward"}

    for rule in row["rules"]:
        matched, open_ = rule_matches(rule, packet)
        if matched is False:
            continue
        found = outcome(rule)
        if found not in possible:
            possible.append(found)
        if matched is None:
            undecided.append(rule["sequence-id"])
            needs += open_
            continue
        if len(possible) == 1:
            return {
                **found,
                "rule": rule["sequence-id"],
                "node": node(rule),
                "description": rule["description"],
                **({"undecided_before": undecided} if undecided else {}),
            }
        return {
            "result": "undetermined",
            "rules": [*undecided, rule["sequence-id"]],
            "needs": sorted(set(needs)),
        }
    if not row["complete"]:
        return {
            "result": "undetermined",
            "rules": undecided,
            "needs": sorted({*needs, "policy_not_expressible"}),
            "reasons": row["reasons"],
        }
    if undecided:
        return {"result": "undetermined", "rules": undecided, "needs": sorted(set(needs))}
    return {"result": "no-match"}
