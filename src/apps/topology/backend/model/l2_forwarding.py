"""Which VLANs each observed L2 link actually forwards (evidence analysis, not YANG).

A link that is up does not prove that a VLAN crosses it (implementation plan §8 M3).
For every L2 link between two collected switch ports and every VLAN either end admits:

- admission: an access port admits its access VLAN untagged; an 802.1Q trunk admits
  the allowed (and, when listed, active) VLANs, untagged for its native VLAN;
- tagging: both ends must agree, so a native VLAN on one end and a tagged VLAN on the
  other is a mismatch rather than one VLAN;
- spanning tree: per-VLAN port state (PVST/rapid-PVST "show spanning-tree"), else the
  trunk table's "forwarding state and not pruned" list; a blocking end blocks the VLAN;
- aggregation: a member port is judged on its aggregate and must itself be bundled.

Per (link, VLAN) the result is forwarding, blocked (end, role), not-admitted (end),
tagging-mismatch, member-not-bundled or unknown (the missing fact). Link issues name
native VLAN mismatches and allowed lists with no common VLAN. Bridge domains are the
devices each VLAN connects through forwarding links. Per-VLAN STP state has no IEEE or
IETF YANG node (RFC 8944 and the IEEE bridge models describe one tree per bridge or
per MSTI), so this stays application analysis beside the SIMAP tree.
"""

from __future__ import annotations

from .builder import (
    L2T,
    NT,
    ST,
    _operational_trunk,
    _ports,
    _vlan_ids,
    _vlan_ranges,
    device_node_ids,
)

BLOCKING = {"blocking", "discarding", "listening", "learning", "broken", "disabled"}


def _lag_parents(facts):
    parent, bundled = {}, {}
    for record in facts.get("lag") or []:
        if not isinstance(record, dict) or not isinstance(record.get("ifname"), str):
            continue
        for member in record.get("members", []):
            parent[member] = record["ifname"]
            bundled[member] = member in record.get("bundled", [])
    return parent, bundled


def _port_mode(facts, port):
    """{'mode': access|trunk, ...} or None when the port's L2 role is not observed."""
    switchports = _ports(facts.get("switchport")) or {}
    trunks = _ports(facts.get("trunk")) or {}
    sp, tr = switchports.get(port), trunks.get(port)
    iface = next(
        (
            r
            for r in facts.get("interfaces") or []
            if isinstance(r, dict) and r.get("ifname") == port
        ),
        {},
    )
    if (tr and _operational_trunk(tr) and tr.get("l2_mode") == "trunk") or (
        sp and sp.get("l2_mode") == "trunk" and _operational_trunk(sp)
    ):
        source = tr or sp
        allowed = _vlan_ids(source.get("trunk_vlans"))
        if not allowed:
            return None
        active = _vlan_ids(tr.get("active_vlans")) if tr and tr.get("active_vlans") else None
        native = source.get("native_vlan")
        mode = {
            "mode": "trunk",
            "allowed": allowed & active if active is not None else allowed,
            "native": native if type(native) is int else None,
        }
        if tr and tr.get("forwarding_vlans") is not None:
            mode["forwarding"] = _vlan_ids(tr["forwarding_vlans"]) or set()
        return mode
    vlan = (sp or {}).get("access_vlan") if (sp or {}).get("l2_mode") == "access" else None
    if vlan is None and iface.get("l2_mode") == "access":
        vlan = iface.get("access_vlan")
    if type(vlan) is int:
        return {"mode": "access", "allowed": {vlan}, "native": vlan}
    return None


def _stp_state(facts, port, vid, mode):
    rows = [r for r in facts.get("stp") or [] if isinstance(r, dict)]
    match = [r for r in rows if r.get("vlan") == vid and r.get("ifname") == port]
    if match:
        return match[0]["state"], match[0].get("role")
    if "forwarding" in mode:
        return ("forwarding" if vid in mode["forwarding"] else "blocking"), None
    return None, None


def _end(devices, address, ifname):
    facts = devices[address]
    parents, bundled = _lag_parents(facts)
    port = parents.get(ifname, ifname)
    return {
        "device": address,
        "ifname": ifname,
        "port": port,
        "member_bundled": bundled.get(ifname),
        "mode": _port_mode(facts, port),
        "facts": facts,
    }


def _vlan_status(ends, vid):
    tagging = []
    for end in ends:
        mode = end["mode"]
        if mode is None:
            return {"status": "unknown", "missing": "l2_port_mode", "at": end["device"]}
        if vid not in mode["allowed"]:
            return {"status": "not-admitted", "at": end["device"], "port": end["port"]}
        tagging.append("untagged" if vid == mode["native"] else "tagged")
    if len(set(tagging)) > 1:
        return {"status": "tagging-mismatch", "tagging": tagging}
    for end in ends:
        if end["member_bundled"] is False:
            return {"status": "member-not-bundled", "at": end["device"], "port": end["ifname"]}
    unknown = None
    for end in ends:
        state, role = _stp_state(end["facts"], end["port"], vid, end["mode"])
        if state in BLOCKING:
            blocked = {
                "status": "blocked",
                "at": end["device"],
                "port": end["port"],
                "state": state,
            }
            if role:
                blocked["role"] = role
            return blocked
        if state is None:
            unknown = {"status": "unknown", "missing": "stp_state", "at": end["device"]}
    return unknown or {"status": "forwarding"}


def _issues(ends):
    modes = [end["mode"] for end in ends]
    if any(mode is None for mode in modes):
        return []
    issues = []
    if all(mode["mode"] == "trunk" for mode in modes):
        natives = [mode["native"] for mode in modes]
        if None not in natives and natives[0] != natives[1]:
            issues.append({"code": "native_vlan_mismatch", "native_vlans": natives})
        if not modes[0]["allowed"] & modes[1]["allowed"]:
            issues.append({"code": "allowed_vlans_disjoint"})
    elif (
        all(mode["mode"] == "access" for mode in modes) and modes[0]["native"] != modes[1]["native"]
    ):
        issues.append({"code": "access_vlan_mismatch", "vlans": [m["native"] for m in modes]})
    elif {mode["mode"] for mode in modes} == {"access", "trunk"}:
        issues.append({"code": "access_trunk_mismatch"})
    return issues


def analyse(devices, tree, base):
    """{links: {link-id: {...}}, bridge_domains: {vid: [[devices]]}} for the L2 network."""
    ids = device_node_ids(devices, base)
    owner = {node: address for address, node in ids.items()}
    networks = tree.get("ietf-network:networks", {}).get("network", [])
    l2 = next((n for n in networks if L2T + "l2-topology" in n.get("network-types", {})), None)
    result = {"links": {}, "bridge_domains": {}}
    if l2 is None:
        return result
    names = {
        (node["node-id"], tp["tp-id"]): tp.get(L2T + "l2-termination-point-attributes", {}).get(
            "interface-name"
        )
        for node in l2.get("node", [])
        for tp in node.get(NT + "termination-point", [])
    }
    edges = {}
    for link in l2.get(NT + "link", []):
        refs = [(e["node-ref"], e["tp-ref"]) for e in link.get(ST + "tp", [])]
        if len(refs) != 2 or not all(node in owner and names.get((node, tp)) for node, tp in refs):
            continue  # hosts, shared segments and unmanaged ends are not judged here
        ends = [_end(devices, owner[node], names[(node, tp)]) for node, tp in refs]
        if not any(end["mode"] for end in ends):
            continue  # routed or otherwise non-switching ports on both ends
        vlans = set().union(*(end["mode"]["allowed"] for end in ends if end["mode"]))
        statuses = {vid: _vlan_status(ends, vid) for vid in sorted(vlans)}
        result["links"][link["link-id"]] = {
            "ends": [
                {
                    "device": end["device"],
                    "interface": end["ifname"],
                    **({"aggregate": end["port"]} if end["port"] != end["ifname"] else {}),
                    **(
                        {
                            "mode": end["mode"]["mode"],
                            "vlans": _vlan_ranges(end["mode"]["allowed"]),
                            **(
                                {"native_vlan": end["mode"]["native"]}
                                if end["mode"]["mode"] == "trunk"
                                else {}
                            ),
                        }
                        if end["mode"]
                        else {"mode": None}
                    ),
                }
                for end in ends
            ],
            "vlans": {str(vid): status for vid, status in statuses.items()},
            "issues": _issues(ends),
        }
        for vid, status in statuses.items():
            if status["status"] == "forwarding":
                edges.setdefault(vid, []).append(tuple(end["device"] for end in ends))
    for vid, pairs in sorted(edges.items()):
        groups = []
        for a, b in pairs:
            joined = [g for g in groups if a in g or b in g]
            merged = {a, b}.union(*joined)
            groups = [g for g in groups if g not in joined] + [merged]
        result["bridge_domains"][str(vid)] = sorted(sorted(g) for g in groups)
    return result
