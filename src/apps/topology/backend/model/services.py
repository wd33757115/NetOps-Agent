"""Network service projections of collected VPN instances (plan §8 M5).

L3NM (RFC 9182) for IP VPN instances (VRFs), L2NM (RFC 9291) for EVPN instances (EVIs),
both with the vpn-common vocabulary (RFC 9181). The service layer is read from device
facts only; nothing a device does not show is filled in:

- A VPN service is the set of instances that export a common route target: the routes
  they originate carry the same target, so no import policy can tell them apart. Groups
  joined only by imports (a shared-services leak, a hub and its spokes) stay separate
  services; the import is in each instance's targets and listed in ``relations``.
- ``vpn-id`` and ``vpn-node-id`` are platform-assigned: the service's lowest exported
  target, and the SIMAP node-id of the device-local instance. ``ne-id`` is the device's
  SIMAP node-id. A ``vpn-name`` is given only when every member uses the same local name.
- Route targets and distinguishers are written in their RFC 8294 typed form: the CLI
  "ASN:nn" / "IPv4:nn" text is the type 0, 2 or 1 value of RFC 4360 / RFC 4364.
- An instance without any route target is local (e.g. a management VRF), not a VPN.
- ``vpn-service-topology`` is any-to-any when every member imports a target each other
  member exports, custom otherwise. ``underlay-transport`` names a protocol only when
  every member shows it: operational LDP sessions for an MPLS VPN, an L3 VNI bound to the
  instance for EVPN-VXLAN, VXLAN encapsulation for an EVI.
- Network accesses are the instance's interfaces (an L3 VNI interface is transport, not
  an access) with their observed address, admin and oper status.

An IP VRF carried over EVPN (symmetric IRB, RFC 9136) is an IP VPN service, so it is an
L3NM service whose underlay is VXLAN; its EVIs are L2NM services of their own. An EVI's RD
and targets sit under ``bgp-auto-discovery``, which RFC 9291 allows only with
``bgp-ad-enabled``: set when every member's device has an established BGP EVPN session.
The EVPN service-interface type (IOS "vlan-based") is not exported: the evpn-bgp
signaling case holds a ``pbb`` container whose when condition names ``'pbb-evpn'``
without a prefix, an identity ietf-l2vpn-ntw does not define, so no instance of that case
validates (M1 case ``l2nm-evpn-signaling-option``).
"""

from __future__ import annotations

import ipaddress
from collections import defaultdict

from .builder import _uri, device_node_ids
from .overlay import l3vni_interfaces, prepare

L3NM = "ietf-l3vpn-ntw:"
L2NM = "ietf-l2vpn-ntw:"
VPN = "ietf-vpn-common:"
FAMILIES = {"ipv4": VPN + "ipv4", "ipv6": VPN + "ipv6"}


def typed(value):
    """RFC 8294 route-target / route-distinguisher text of a CLI "admin:assigned" value."""
    admin, colon, assigned = str(value).rpartition(":")
    if not colon or not assigned.isdigit():
        return None
    number = int(assigned)
    try:
        ipaddress.IPv4Address(admin)
    except ValueError:
        pass
    else:
        return f"1:{admin}:{number}" if number <= 0xFFFF else None
    if not admin.isdigit():
        return None
    asn = int(admin)
    if asn <= 0xFFFF and number <= 0xFFFFFFFF:
        return f"0:{asn}:{number}"
    if asn <= 0xFFFFFFFF and number <= 0xFFFF:
        return f"2:{asn}:{number}"
    return None


def _gap(gaps, code, **fields):
    gap = {"code": code, **fields}
    if gap not in gaps:
        gaps.append(gap)


def _targets(imports, exports, gaps, where):
    """RFC 9182/9291 vpn-target entries: targets in both directions, then import, export."""
    typed_imports, typed_exports = set(), set()
    for raw, into in ((imports, typed_imports), (exports, typed_exports)):
        for value in raw:
            target = typed(value)
            if target is None:
                _gap(gaps, "route_target_not_typed", value=value, **where)
            else:
                into.add(target)
    groups = [
        ("both", typed_imports & typed_exports),
        ("import", typed_imports - typed_exports),
        ("export", typed_exports - typed_imports),
    ]
    return [
        {
            "id": index,
            "route-targets": [{"route-target": t} for t in sorted(targets)],
            "route-target-type": kind,
        }
        for index, (kind, targets) in enumerate([g for g in groups if g[1]], start=1)
    ]


def _bgp(facts):
    """(local AS, router ID) of the device's one BGP process, when its tables agree."""
    tables = [facts.get(k) for k in ("bgp_peers", "bgp_vpnv4", "bgp_evpn")]
    tables = [t for t in tables if isinstance(t, dict)]
    ases = {t["local_as"] for t in tables if t.get("local_as") is not None}
    ids = {t["router_id"] for t in tables if t.get("router_id")}
    return (
        ases.pop() if len(ases) == 1 else None,
        ids.pop() if len(ids) == 1 else None,
    )


def _status(facts, ifname):
    row = next((r for r in facts.get("interfaces") or [] if r.get("ifname") == ifname), None)
    status = {}
    if row and row.get("admin_status") in ("up", "down"):
        status["admin-status"] = {"status": VPN + "admin-" + row["admin_status"]}
    if row and row.get("oper_status") in ("up", "down"):
        status["oper-status"] = {"status": VPN + "op-" + row["oper_status"]}
    return status


def _group(members):
    """Union of members that export a common target; members: [{"export": set, ...}]."""
    parent = list(range(len(members)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    owner = {}
    for index, member in enumerate(members):
        for target in member["export"]:
            if target in owner:
                parent[root(index)] = root(owner[target])
            else:
                owner[target] = index
    groups = defaultdict(list)
    for index in range(len(members)):
        groups[root(index)].append(members[index])
    return list(groups.values())


def _service_frame(base, kind, group):
    exported = sorted({t for m in group for t in m["export_typed"]})
    first = exported[0] if exported else group[0]["node_id"]
    service = {"vpn-id": f"{base}{kind}:{first}"}
    names = {m["name"] for m in group}
    if len(names) == 1:
        service["vpn-name"] = names.pop()
    # A route carries every target its exporter sets: one imported target is enough.
    meshed = all(m["import_typed"] & o["export_typed"] for m in group for o in group if o is not m)
    service["vpn-service-topology"] = VPN + ("any-to-any" if meshed else "custom")
    return service


def _relations(services):
    """Imports of a target another service exports (extranet, shared services, hub-spoke)."""
    exporter = {}
    for service_id, group in services:
        for member in group:
            for target in member["export_typed"]:
                exporter.setdefault(target, service_id)
    found = []
    for service_id, group in services:
        for member in group:
            for target in sorted(member["import_typed"]):
                source = exporter.get(target)
                if source and source != service_id:
                    found.append(
                        {
                            "service": service_id,
                            "vpn-node-id": member["node_id"],
                            "imports_from": source,
                            "route_target": target,
                        }
                    )
    return found


def _typed_set(values):
    return {t for t in (typed(v) for v in values) if t}


# --------------------------------------------------------------------------- L3NM


def _vrf_members(devices, ids, gaps):
    members = []
    for key, facts in sorted(devices.items()):
        if not isinstance(facts, dict) or key not in ids:
            continue
        transport = set(l3vni_interfaces(facts))
        l3vnis = {row["vrf"] for row in l3vni_interfaces(facts).values()}
        ldp = any(p.get("state") == "operational" for p in facts.get("ldp_peers") or [])
        vpnv4 = facts.get("bgp_vpnv4") if isinstance(facts.get("bgp_vpnv4"), dict) else {}
        for row in facts.get("vrf_detail") or []:
            families = {
                family: targets
                for family, targets in (row.get("route_targets") or {}).items()
                if family in FAMILIES and (targets.get("import") or targets.get("export"))
            }
            if not families:
                continue  # a local instance, not part of any VPN
            imports = {v for t in families.values() for v in t.get("import", [])}
            exports = {v for t in families.values() for v in t.get("export", [])}
            where = {"device": key, "vrf": row["vrf"]}
            members.append(
                {
                    "key": key,
                    "facts": facts,
                    "name": row["vrf"],
                    "row": row,
                    "families": families,
                    "where": where,
                    "node_id": f"{ids[key]}:ni:{_uri(row['vrf'])}",
                    "export": exports,
                    "export_typed": _typed_set(exports),
                    "import_typed": _typed_set(imports),
                    "accesses": [i for i in row.get("interfaces") or [] if i not in transport],
                    "transport": {
                        "vxlan": row["vrf"] in l3vnis,
                        "ldp": ldp
                        and any(p.get("state") == "established" for p in vpnv4.get("peers") or []),
                    },
                }
            )
    return members


def _l3_access(member, ifname, profile):
    facts = member["facts"]
    access = {"id": ifname, "interface-id": ifname, "vpn-instance-profile": profile}
    gateways = {r.get("gateway") for r in facts.get("evpn_instances") or []}
    gateways |= {r.get("gateway") for r in facts.get("vxlan_vnis") or [] if r.get("type") == "l2"}
    if ifname in gateways:
        access["vpn-network-access-type"] = VPN + "irb"
    status = _status(facts, ifname)
    if status:
        access["status"] = status
    addresses = [
        r
        for r in facts.get("ip") or []
        if r.get("ifname") == ifname and r.get("vrf", "default") == member["name"]
    ]
    for version, family in ((4, "ipv4"), (6, "ipv6")):
        rows = [r for r in addresses if ipaddress.ip_address(r["address"]).version == version]
        if len(rows) == 1:  # a primary address; secondaries have no single-valued place
            access.setdefault("ip-connection", {})[family] = {
                "local-address": rows[0]["address"],
                "prefix-length": rows[0]["prefix_len"],
            }
    return access


def _l3_node(member, gaps):
    facts, row, name = member["facts"], member["row"], member["name"]
    profile = {"profile-id": name}
    rd = row.get("rd")
    if rd:
        if typed(rd):
            profile["rd"] = typed(rd)
        else:
            _gap(gaps, "route_distinguisher_not_typed", value=rd, **member["where"])
    families = []
    for family, targets in sorted(member["families"].items()):
        entry = {"address-family": FAMILIES[family]}
        vpn_targets = _targets(
            targets.get("import", []), targets.get("export", []), gaps, member["where"]
        )
        if vpn_targets:
            entry["vpn-targets"] = {"vpn-target": vpn_targets}
        families.append(entry)
    profile["address-family"] = families
    node = {"vpn-node-id": member["node_id"], "ne-id": member["node_id"].rsplit(":ni:", 1)[0]}
    local_as, router_id = _bgp(facts)
    if local_as is not None:
        node["local-as"] = local_as
    if router_id:
        node["router-id"] = router_id
    node["active-vpn-instance-profiles"] = {"vpn-instance-profile": [profile]}
    accesses = [_l3_access(member, ifname, name) for ifname in member["accesses"]]
    if accesses:
        node["vpn-network-accesses"] = {"vpn-network-access": accesses}
    return node


def _underlay(group):
    protocols = []
    if all(m["transport"].get("ldp") for m in group):
        protocols.append(VPN + "ldp")
    if all(m["transport"].get("vxlan") for m in group):
        protocols.append(VPN + "vxlan-trans")
    return {"protocol": protocols} if protocols else None


def project_l3nm(devices, base, ids=None):
    """{"tree": RFC 9182 l3vpn-ntw, "relations": [...], "gaps": [...]}."""
    ids = ids or device_node_ids(devices, base)
    gaps = []
    services, grouped = [], []
    for group in _group(_vrf_members(devices, ids, gaps)):
        service = _service_frame(base, "l3vpn", group)
        service["vpn-type"] = VPN + "l3vpn"
        underlay = _underlay(group)
        if underlay:
            service["underlay-transport"] = underlay
        service["vpn-instance-profiles"] = {
            "vpn-instance-profile": [{"profile-id": n} for n in sorted({m["name"] for m in group})]
        }
        nodes = sorted((_l3_node(m, gaps) for m in group), key=lambda n: n["vpn-node-id"])
        service["vpn-nodes"] = {"vpn-node": nodes}
        services.append(service)
        grouped.append((service["vpn-id"], group))
    services.sort(key=lambda s: s["vpn-id"])
    tree = {L3NM + "l3vpn-ntw": {"vpn-services": {"vpn-service": services}}} if services else {}
    return {"tree": tree, "relations": _relations(grouped), "gaps": gaps}


# --------------------------------------------------------------------------- L2NM


def _evi_members(devices, ids):
    members = []
    for key, facts in sorted(devices.items()):
        if not isinstance(facts, dict) or key not in ids:
            continue
        segments = {
            row["instance"]: row
            for row in facts.get("vxlan_vnis") or []
            if row.get("instance") and row.get("type") == "l2"
        }
        for row in facts.get("evpn_instances") or []:
            if not (row.get("rt_import") or row.get("rt_export")):
                continue
            segment = segments.get(row["instance"], {})
            ports = row.get("ports") or segment.get("ports") or []
            members.append(
                {
                    "key": key,
                    "facts": facts,
                    "name": row["instance"],
                    "row": row,
                    "where": {"device": key, "instance": row["instance"]},
                    "node_id": f"{ids[key]}:evi:{_uri(row['instance'])}",
                    "export": set(row.get("rt_export") or []),
                    "export_typed": _typed_set(row.get("rt_export") or []),
                    "import_typed": _typed_set(row.get("rt_import") or []),
                    "ports": [p["ifname"] for p in ports if p.get("ifname")],
                    "vxlan": row.get("encapsulation") == "vxlan",
                    "bgp": any(
                        p.get("state") == "established"
                        for p in (facts.get("bgp_evpn") or {}).get("peers") or []
                    ),
                }
            )
    return members


def _l2_node(member, gaps, discovered):
    facts, row = member["facts"], member["row"]
    node = {"vpn-node-id": member["node_id"], "ne-id": member["node_id"].rsplit(":evi:", 1)[0]}
    _, router_id = _bgp(facts)
    if router_id:
        node["router-id"] = router_id
    discovery = {}
    if row.get("rd"):
        if typed(row["rd"]):
            discovery["rd"] = typed(row["rd"])
        else:
            _gap(gaps, "route_distinguisher_not_typed", value=row["rd"], **member["where"])
    targets = _targets(
        row.get("rt_import") or [], row.get("rt_export") or [], gaps, member["where"]
    )
    if targets:
        discovery["vpn-target"] = targets
    if discovery and discovered:
        node["bgp-auto-discovery"] = discovery
    elif discovery:
        _gap(gaps, "bgp_auto_discovery_not_proven", **member["where"])
    accesses = []
    for ifname in member["ports"]:
        access = {"id": ifname, "interface-id": ifname}
        status = _status(facts, ifname)
        if status:
            access["status"] = status
        accesses.append(access)
    if accesses:
        node["vpn-network-accesses"] = {"vpn-network-access": accesses}
    return node


def project_l2nm(devices, base, ids=None):
    """{"tree": RFC 9291 l2vpn-ntw, "relations": [...], "gaps": [...]} for EVPN instances."""
    ids = ids or device_node_ids(devices, base)
    gaps = []
    services, grouped = [], []
    for group in _group(_evi_members(devices, ids)):
        service = _service_frame(base, "l2vpn", group)
        if all(m["vxlan"] for m in group):
            service["vpn-type"] = VPN + "vxlan-evpn"
            service["underlay-transport"] = {"protocol": [VPN + "vxlan-trans"]}
        discovered = all(m["bgp"] for m in group)
        if discovered:
            service["bgp-ad-enabled"] = True
            service["signaling-type"] = VPN + "bgp-signaling"
        nodes = sorted(
            (_l2_node(m, gaps, discovered) for m in group), key=lambda n: n["vpn-node-id"]
        )
        service["vpn-nodes"] = {"vpn-node": nodes}
        services.append(service)
        grouped.append((service["vpn-id"], group))
    services.sort(key=lambda s: s["vpn-id"])
    tree = {L2NM + "l2vpn-ntw": {"vpn-services": {"vpn-service": services}}} if services else {}
    return {"tree": tree, "relations": _relations(grouped), "gaps": gaps}


def project_services(devices, base):
    """L3NM and L2NM projections of collected device facts, sharing one identity rule."""
    ids = device_node_ids(devices, base)
    prepared = prepare(devices)
    return {
        "l3nm": project_l3nm(prepared, base, ids),
        "l2nm": project_l2nm(prepared, base, ids),
    }
