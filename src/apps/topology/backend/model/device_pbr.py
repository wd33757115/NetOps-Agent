"""The collected IPv4 policy routes as an OpenConfig policy-forwarding tree.

Neither IETF nor IEEE has a policy-routing model, so the owner chose the public
openconfig-policy-forwarding (the pinned OpenConfig release, with its whole
dependency closure). It lives only under openconfig-network-instance, and an interface
binding is a leafref to /oc-if:interfaces/interface/name, so the tree is the minimal host
tree that carries it: each network instance a bound interface is in (its name and type),
each bound interface (its name and type, the leaves the module requires) and
policy-forwarding. Names are those of the IETF trees: the interface names of RFC 8343, a
VRF's RFC 8529 instance name, and "DEFAULT" (OpenConfig's DEFAULT_INSTANCE) for the
global table, which RFC 8529 has no entry for. Interfaces, addresses and routes stay in
the IETF trees; nothing of them is repeated here.

A node is evaluated in node-number order, the first that matches decides (Comware). An
OpenConfig rule matches packet fields, not an ACL, so a node's ACL is expanded into one
rule per entry, numbered ``node * 65536 + n`` in the ACL's match order, all with the
node's action; the description names node, ACL and entry. The expansion is exact only
where every entry is a permit entry OpenConfig can express (a prefix wildcard, a protocol
number, one port or a range): H3C documents both that PBR ignores the ACL's permit/deny
and, for other versions, that a deny entry fails the match, and a time range likewise, so
an ACL with a deny entry or a time range is not expanded. An entry of another VPN instance
than the interface's never matches there and is left out (Comware: an entry without
vpn-instance matches only public packets).

Actions: a permit node with one next hop sets ``action/config/next-hop`` (forward to it,
bypassing the lookup); a deny node, and a permit node without an action, send the packet
by the routing table and end the evaluation: ``forwarding-action FORWARD`` ("Send the
packet"), since OpenConfig treats a rule without action as an error.

What cannot be expressed is left out with a gap, and the tree is partial: a node that
cannot be (its policy's rules stop before it; later nodes are not exported, since
dropping one node would change which node packets reach), a binding whose interface,
type or instance is not proven, and a policy for locally generated packets (OpenConfig
has no place for it; it is listed in the metadata).
"""

from __future__ import annotations

import ipaddress

from .device_state import project_device_state

NI = "openconfig-network-instance:"
IF = "openconfig-interfaces:"
NI_TYPES = "openconfig-network-instance-types:"
FORWARD = "openconfig-policy-forwarding:FORWARD"  # send the packet by the routing table
DEFAULT = "DEFAULT"  # OpenConfig's name for the global table's instance
STEP = 65536  # sequence-id = node * STEP + entry number in the expansion
MODULES = "openconfig/public 8c3a6cf65ac8a21ad029d252352c6319745b06c2"


def _prefix(address):
    wildcard = int(ipaddress.IPv4Address(address["wildcard"]))
    length = 32 - wildcard.bit_length()
    if wildcard != (1 << (32 - length)) - 1:
        return None
    return str(ipaddress.ip_network(f"{address['address']}/{length}", strict=False))


def _port(condition):
    """OpenConfig port-num-range: a port, "low..high", or None."""
    op, ports = condition["op"], condition["ports"]
    if any(port is None for port in ports):
        return None
    if op == "range":
        return f"{ports[0]}..{ports[1]}"
    if len(ports) != 1:
        return None
    port = ports[0]
    if op == "eq":
        return port
    if op == "lt" and port > 0:
        return f"0..{port - 1}"
    if op == "gt" and port < 65535:
        return f"{port + 1}..65535"
    return None  # neq: not one range


def _match(entry):
    """({"ipv4": ..., "transport": ...}, None) or (None, [why])."""
    why = list(entry.get("unsupported") or [])
    if entry.get("action") != "permit":
        why.append("acl_entry_not_permit")
    for flag in ("established", "fragments", "time_range", "icmp"):
        if entry.get(flag):
            why.append(flag)
    ipv4, transport = {}, {}
    if "protocol" in entry:
        if not 0 <= entry["protocol"] <= 254:
            why.append("protocol")
        ipv4["protocol"] = entry["protocol"]
    for key, leaf in (("src", "source-address"), ("dst", "destination-address")):
        if key in entry:
            prefix = _prefix(entry[key])
            if prefix is None:
                why.append(f"{key}_wildcard")
            ipv4[leaf] = prefix
    for key, leaf in (("src_ports", "source-port"), ("dst_ports", "destination-port")):
        if key in entry:
            port = _port(entry[key])
            if port is None:
                why.append(key)
            transport[leaf] = port
    if why:
        return None, sorted(set(why))
    match = {}
    if ipv4:
        match["ipv4"] = {"config": ipv4}
    if transport:
        match["transport"] = {"config": transport}
    return match, None


def _node_rules(node, entries, vrf):
    """(OpenConfig rules, None) or (None, [why]) for one node in one instance."""
    why = list(node.get("unsupported") or [])
    if "acl" not in node:
        why.append("no_acl_match")  # what a node without if-match matches is not verified
    applicable = [
        entry
        for entry in entries
        if entry.get("family", "ipv4") == "ipv4" and entry.get("vrf", vrf) == vrf
    ]
    if "acl" in node and not entries:
        why.append("acl_not_observed")
    if len(applicable) >= STEP:
        why.append("acl_too_long")
    if node["mode"] == "permit" and "next_hop" in node:
        action = {"config": {"next-hop": node["next_hop"]}}
    else:
        action = {"forwarding": {"config": {"forwarding-action": FORWARD}}}
    rules = []
    for number, entry in enumerate(applicable, 1):
        match, reasons = _match(entry)
        if match is None:
            why += [f"entry {entry['seq']}:{reason}" for reason in reasons]
            continue
        sequence = node["node"] * STEP + number
        config = {
            "sequence-id": sequence,
            "description": f"node {node['node']} acl {node['acl']} rule {entry['seq']}",
        }
        rules.append({"sequence-id": sequence, "config": config, **match, "action": action})
    if why:
        return None, why
    return rules, None


def _scopes(facts):
    """{interface: instance} for interfaces whose addresses are all in one instance."""
    found = {}
    for row in facts.get("ip") or []:
        found.setdefault(row["ifname"], set()).add(row.get("vrf", "default"))
    return found


def project_device_pbr(observation):
    """{"tree", "metadata", "gaps"}: the device's IPv4 policy routes where they are applied."""
    data = observation.get("data", observation) if isinstance(observation, dict) else {}
    facts = data.get("facts", data) if isinstance(data, dict) else {}
    if not isinstance(facts, dict):
        facts = {}
    metadata = {
        "standard": "openconfig-policy-forwarding",
        "modules": MODULES,
        "content": "operational",
        "completeness": "complete",
        "full_validation": "not_performed",
        "sequence_id": f"node * {STEP} + entry number in the node's ACL",
    }
    for source, target in (("id", "observation_id"), ("observed_at", "observed_at")):
        if isinstance(observation, dict) and isinstance(observation.get(source), str):
            metadata[target] = observation[source]
    result = {"tree": {}, "metadata": metadata, "gaps": []}
    nodes, bindings = facts.get("pbr_rules"), facts.get("pbr_bindings")
    for name, table in (("pbr_rules", nodes), ("pbr_bindings", bindings)):
        if not isinstance(table, list):
            result["gaps"].append({"code": f"{name}_not_observed"})
    nodes = nodes if isinstance(nodes, list) else []
    bindings = bindings if isinstance(bindings, list) else []
    policies = {}
    for node in sorted(nodes, key=lambda row: row["node"]):
        policies.setdefault(node["policy"], []).append(node)
    entries = {}
    for entry in facts.get("acl_rules") or []:
        entries.setdefault(entry["acl"], []).append(entry)
    rows = (
        project_device_state(observation)["tree"]
        .get("ietf-interfaces:interfaces", {})
        .get("interface", [])
    )
    types = {row["name"]: row.get("type") for row in rows}
    scopes = _scopes(facts)
    local = sorted({row["policy"] for row in bindings if row.get("local")})
    for policy in local:
        result["gaps"].append({"code": "local_policy_not_expressible", "policy": policy})
    if local:
        metadata["local_policies"] = local
    instances, interfaces, bound = {}, {}, {}
    for binding in bindings:
        if binding.get("local"):
            continue
        ifname, policy = binding["ifname"], binding["policy"]
        where = {"interface": ifname, "policy": policy}
        scope = scopes.get(ifname, {"default"})
        if policy not in policies:
            result["gaps"].append({"code": "bound_policy_not_observed", **where})
            continue
        if ifname not in types:
            result["gaps"].append({"code": "pbr_interface_reference_not_proven", **where})
            continue
        if not types[ifname]:
            # oc-if requires the interface type; it is not made up.
            result["gaps"].append({"code": "pbr_interface_type_not_observed", **where})
            continue
        if len(scope) != 1 or ifname in bound:
            result["gaps"].append({"code": "pbr_binding_not_expressible", **where})
            continue
        vrf = next(iter(scope))
        bound[ifname] = policy
        interfaces[ifname] = types[ifname]
        instances.setdefault(vrf, {}).setdefault(policy, []).append(ifname)
    if not instances:
        if result["gaps"]:
            metadata["completeness"] = "partial"
        return result
    network_instances = []
    for vrf in sorted(instances, key=lambda name: (name != "default", name)):
        name = DEFAULT if vrf == "default" else vrf
        kind = "DEFAULT_INSTANCE" if vrf == "default" else "L3VRF"
        policy_list, points = [], []
        for policy in sorted(instances[vrf]):
            rules = []
            for node in policies[policy]:
                found, why = _node_rules(node, entries.get(node.get("acl"), []), vrf)
                if found is None:
                    result["gaps"].append(
                        {
                            "code": "pbr_node_not_expressible",
                            "network_instance": name,
                            "policy": policy,
                            "node": node["node"],
                            "reasons": why,
                        }
                    )
                    break  # later nodes are reached only by packets this one leaves
                rules += found
            entry = {
                "policy-id": policy,
                "config": {"policy-id": policy, "type": "PBR_POLICY"},
            }
            if rules:
                entry["rules"] = {"rule": rules}
            policy_list.append(entry)
            for ifname in sorted(instances[vrf][policy]):
                points.append(
                    {
                        "interface-id": ifname,
                        "config": {"interface-id": ifname, "apply-forwarding-policy": policy},
                        "interface-ref": {"config": {"interface": ifname}},
                    }
                )
        network_instances.append(
            {
                "name": name,
                "config": {"name": name, "type": NI_TYPES + kind},
                "policy-forwarding": {
                    "policies": {"policy": policy_list},
                    "interfaces": {"interface": sorted(points, key=lambda p: p["interface-id"])},
                },
            }
        )
    result["tree"] = {
        IF + "interfaces": {
            "interface": [
                {"name": ifname, "config": {"name": ifname, "type": interfaces[ifname]}}
                for ifname in sorted(interfaces)
            ]
        },
        NI + "network-instances": {"network-instance": network_instances},
    }
    if result["gaps"]:
        metadata["completeness"] = "partial"
    return result
