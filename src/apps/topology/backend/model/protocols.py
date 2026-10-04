"""Native RFC 8349 protocol instances, and protocol adjacencies between collected devices.

Instances, one RFC 8349 control-plane-protocol each, in the routing tree of their scope:

- OSPFv2 (RFC 9129): named by the OSPF process ID the device shows. Only processes the
  OSPF projection (``ospf.py``) binds to one network-instance scope are exported, with
  the router ID, the areas of their usable interfaces (cost from the OSPF interface
  facts) and the neighbours seen on each interface (router ID, address, state).
- BGP (draft-ietf-idr-bgp-model-21): named by the local AS, by which the CLI identifies
  its one BGP process; global AS and identifier; neighbours keyed by their address with
  peer AS, peer type (internal when the peer AS is the local AS) and session state. Each
  address-family summary the device shows (IPv4 unicast, VPNv4, EVPN) adds its AFI-SAFI
  to the global list and to every neighbour it lists, active when that row is
  established. Prefix counts are not exported: the CLI columns do not say whether they
  are counted before or after inbound policy, which the model distinguishes.

- LDP (RFC 9070): one instance (the CLI shows one LDP process per router, named "ldp"
  here); the local LSR ID when every session row shows the same one; peers keyed by
  LSR ID and label space with their session state; per interface, the hello
  adjacencies the device lists as discovery sources (IOS), each pointing at its peer.
- MPLS (RFC 8960): the interfaces MPLS is proven enabled on: an LDP discovery source,
  or the egress of a labelled LFIB entry (an outgoing label is pushed there). The LFIB
  itself is not a RIB here: RFC 8960 keeps it in an MPLS RIB the App does not name.

Interface names in an OSPF or LDP instance and in the MPLS interface list reference the
interface list, so the caller copies those interfaces into the same tree. Nothing
unobserved (timers, LSDB, statistics) is filled in.

Adjacencies are analysis beside the trees, never merged into the L2/L3 graph: an OSPF
neighbour row is matched to the collected device owning that address in the same scope
and compared with that device's own view; a BGP peer is matched to the collected device
owning the peer address, or reported as a peer outside the collected network. A BGP
session is one entry over all its address families (e.g. the EVPN sessions between leaves
and route reflectors), listing the families either end shows.
"""

from __future__ import annotations

from collections import defaultdict

from .ospf import project_ospf

OSPF = "ietf-ospf:"
BGP = "ietf-bgp:"
AFI_SAFI = "iana-bgp-afi-safi-types:"
LDP = "ietf-mpls-ldp:"
MPLS = "ietf-mpls:"
LDP_STATES = {"non-existent", "initialized", "openrec", "opensent", "operational"}
# Address-family summary facts -> RFC 4760 AFI/SAFI (IANA BGP AFI-SAFI identities).
BGP_FAMILIES = (
    ("bgp_peers", "ipv4-unicast"),
    ("bgp_vpnv4", "l3vpn-ipv4-unicast"),
    ("bgp_evpn", "l2vpn-evpn"),
)


def _gap(gaps, code, **fields):
    gap = {"code": code, **fields}
    if gap not in gaps:
        gaps.append(gap)


def _ospf_instances(facts, gaps):
    """{scope: [instance]} and {scope: {ifname}} referenced."""
    projection = project_ospf({"device": facts})["devices"]["device"]
    gaps.extend(projection["gaps"])
    processes = {
        str(p.get("process_id")): p for p in projection["processes"] if isinstance(p, dict)
    }
    usable = [row for row in projection["interfaces"] if row.get("usable")]
    neighbours = [row for row in facts.get("ospf_neighbors") or [] if isinstance(row, dict)]
    by_ifname = defaultdict(list)
    for row in usable:
        by_ifname[row["ifname"]].append(row)
    seen = defaultdict(list)
    for index, row in enumerate(neighbours):
        owners = by_ifname.get(row.get("ifname"), [])
        if len(owners) != 1:
            _gap(
                gaps,
                "ospf_neighbor_interface_unbound",
                record_index=index,
                interface=row.get("ifname"),
            )
            continue
        owner = owners[0]
        if ("process" in row and row["process"] != str(owner["process_id"])) or (
            "area" in row and "area" in owner and row["area"] != owner["area"]
        ):
            _gap(gaps, "ospf_neighbor_process_or_area_conflict", record_index=index)
            continue
        seen[(str(owner["process_id"]), owner["ifname"])].append(row)
    instances, referenced = defaultdict(list), defaultdict(set)
    for pid in sorted({str(row["process_id"]) for row in usable}):
        rows = [row for row in usable if str(row["process_id"]) == pid]
        scopes = {row["vrf"] for row in rows}
        process = processes.get(pid)
        if len(scopes) != 1 or process is None:
            _gap(gaps, "ospf_instance_not_exported", process_id=pid)
            continue
        scope = next(iter(scopes))
        areas = defaultdict(list)
        for row in sorted(rows, key=lambda r: r["ifname"]):
            if "area" not in row:
                _gap(
                    gaps,
                    "ospf_interface_area_not_observed",
                    process_id=pid,
                    interface=row["ifname"],
                )
                continue
            entry = {"name": row["ifname"], "cost": row["cost"]}
            found = {}
            for nb in seen.get((pid, row["ifname"]), []):
                found.setdefault(nb["neighbor_router_id"], []).append(nb)
            neighbours_out = []
            for rid, rows_for in sorted(found.items()):
                if len(rows_for) != 1:
                    _gap(gaps, "duplicate_ospf_neighbor", process_id=pid, router_id=rid)
                    continue
                nb = rows_for[0]
                neighbours_out.append(
                    {"neighbor-router-id": rid, "address": nb["address"], "state": nb["state"]}
                )
            if neighbours_out:
                entry["neighbors"] = {"neighbor": neighbours_out}
            areas[row["area"]].append(entry)
            referenced[scope].add(row["ifname"])
        instance = {
            "type": OSPF + "ospfv2",
            "name": pid,
            OSPF + "ospf": {
                "router-id": process["router_id"],
                **(
                    {
                        "areas": {
                            "area": [
                                {"area-id": area, "interfaces": {"interface": entries}}
                                for area, entries in sorted(areas.items())
                            ]
                        }
                    }
                    if areas
                    else {}
                ),
            },
        }
        instances[scope].append(instance)
    return instances, referenced


def bgp_tables(facts):
    """[(family, summary)] of the BGP address-family summaries the device showed."""
    return [
        (family, facts[key])
        for key, family in BGP_FAMILIES
        if isinstance(facts.get(key), dict) and "local_as" in facts[key]
    ]


def _bgp_instance(facts, gaps):
    tables = bgp_tables(facts)
    if not tables:
        return None  # BGP not running, or not observed
    ases = {data["local_as"] for _, data in tables}
    ids = {data["router_id"] for _, data in tables if data.get("router_id")}
    if len(ases) > 1 or len(ids) > 1:
        _gap(gaps, "bgp_tables_disagree")  # one process shows one AS and one ID
        return None
    local_as = ases.pop()
    neighbours, families = {}, []
    for family, data in tables:
        rows = data.get("peers") or []
        if len({row["peer"] for row in rows}) != len(rows):
            _gap(gaps, "duplicate_bgp_peer")
            return None
        if rows:
            families.append(family)
        for row in rows:
            neighbour = neighbours.setdefault(
                row["peer"],
                {
                    "neighbor-key": row["peer"],
                    "remote-address": row["peer"],
                    "peer-as": row["peer_as"],
                    "peer-type": "internal" if row["peer_as"] == local_as else "external",
                    "session-state": row["state"],
                    "afi-safis": {"afi-safi": []},
                },
            )
            if (neighbour["peer-as"], neighbour.get("session-state")) != (
                row["peer_as"],
                row["state"],
            ):
                # One session per neighbour: the summaries must agree on it.
                _gap(gaps, "bgp_session_rows_disagree", peer=row["peer"])
                neighbour.pop("session-state", None)
                if neighbour["peer-as"] != row["peer_as"]:
                    return None
            neighbour["afi-safis"]["afi-safi"].append(
                {"name": AFI_SAFI + family, "active": row["state"] == "established"}
            )
    bgp = {"global": {"as": local_as}}
    if ids:
        bgp["global"]["identifier"] = ids.pop()
    if families:
        bgp["global"]["afi-safis"] = {"afi-safi": [{"name": AFI_SAFI + f} for f in families]}
    if neighbours:
        bgp["neighbors"] = {"neighbor": list(neighbours.values())}
    return {"type": BGP + "bgp", "name": str(local_as), BGP + "bgp": bgp}


def _ldp_instance(facts, gaps):
    rows = facts.get("ldp_peers")
    if not isinstance(rows, list) or not rows:
        return None  # LDP not running, or not observed
    peers, discovery = {}, defaultdict(list)
    for row in rows:
        key = (row["peer_lsr_id"], row.get("label_space", 0))
        if key in peers:
            _gap(gaps, "duplicate_ldp_peer", lsr_id=key[0])
            return None
        peer = {"lsr-id": key[0], "label-space-id": key[1]}
        if row.get("state") in LDP_STATES:
            peer["session-state"] = row["state"]
        peers[key] = peer
        for source in row.get("discovery") or []:
            discovery[source["ifname"]].append(
                {
                    "adjacent-address": source["address"],
                    "peer": {"lsr-id": key[0], "label-space-id": key[1]},
                }
            )
    ldp = {}
    local = {row["local_lsr_id"] for row in rows if row.get("local_lsr_id")}
    if len(local) == 1:
        ldp["global"] = {"lsr-id": local.pop()}
    if discovery:
        ldp["discovery"] = {
            "interfaces": {
                "interface": [
                    {
                        "name": name,
                        "address-families": {
                            "ipv4": {"hello-adjacencies": {"hello-adjacency": adjacencies}}
                        },
                    }
                    for name, adjacencies in sorted(discovery.items())
                ]
            }
        }
    ldp["peers"] = {"peer": list(peers.values())}
    return {"type": LDP + "mpls-ldp", "name": "ldp", LDP + "mpls-ldp": ldp}


def _mpls_interfaces(facts, ldp):
    names = set()
    if ldp:
        for row in (
            ldp[LDP + "mpls-ldp"].get("discovery", {}).get("interfaces", {}).get("interface", [])
        ):
            names.add(row["name"])
    for row in facts.get("mpls_lfib") or []:
        if row.get("vrf", "default") == "default" and type(row.get("out_label")) is int:
            if row.get("ifname"):
                names.add(row["ifname"])
    return sorted(names)


def project_protocols(facts):
    """{instances: {scope: [control-plane-protocol]}, interfaces: {scope: [name]},
    mpls: {scope: [interface name]}, gaps}."""
    gaps = []
    if not isinstance(facts, dict):
        return {
            "instances": {},
            "interfaces": {},
            "mpls": {},
            "gaps": [{"code": "invalid_facts"}],
        }
    instances, referenced = _ospf_instances(facts, gaps)
    bgp = _bgp_instance(facts, gaps)
    if bgp is not None:
        instances["default"].append(bgp)
    ldp = _ldp_instance(facts, gaps)
    if ldp is not None:
        instances["default"].append(ldp)
    mpls = _mpls_interfaces(facts, ldp)
    interfaces = {scope: sorted(names) for scope, names in referenced.items()}
    return {
        "instances": dict(instances),
        "interfaces": interfaces,
        "mpls": {"default": mpls} if mpls else {},
        "gaps": gaps,
    }


def _owners(devices):
    """address -> [(device, interface, scope)] over every collected IP fact."""
    owners = defaultdict(list)
    for key, facts in devices.items():
        for row in facts.get("ip") or []:
            owners[row["address"]].append((key, row["ifname"], row.get("vrf", "default")))
    return owners


def adjacencies(devices):
    """OSPF adjacencies and BGP sessions between the collected devices.

    An OSPF router ID identifies a router only inside its OSPF scope: it is looked up in
    the scope of the interface the neighbour was seen on, and an ID two devices claim is
    reported as ambiguous. A neighbour address is matched in any scope, so adjacent
    network instances with different local names still pair.
    """
    owners = _owners(devices)
    ospf, bgp = [], []
    rid_owners, own_rids, scope_of = defaultdict(set), defaultdict(set), {}
    for key, facts in devices.items():
        projection = project_ospf({key: facts})["devices"][key]
        for scope, ids in projection["router_ids"].items():
            for rid in ids:
                rid_owners[(scope, rid)].add(key)
                own_rids[key].add(rid)
        for row in projection["interfaces"]:
            if row.get("usable"):
                scope_of[(key, row["ifname"])] = row["vrf"]
    views = {}
    for key, facts in sorted(devices.items()):
        for row in facts.get("ospf_neighbors") or []:
            views[(key, row["ifname"], row["neighbor_router_id"])] = row
    done = set()
    for (key, ifname, rid), row in sorted(views.items()):
        if (key, ifname, rid) in done:
            continue
        lone = {
            "a": {"device": key, "interface": ifname},
            "neighbor_router_id": rid,
            "address": row["address"],
            "state": row["state"],
        }
        candidates = [o for o in owners.get(row["address"], []) if o[0] != key]
        matched = [o for o in candidates if o[0] in rid_owners.get((o[2], rid), set())]
        claimed = {k for o in candidates for k in rid_owners.get((o[2], rid), set())}
        if len(matched) > 1 or len(claimed) > 1:
            ospf.append({**lone, "status": "router_id_ambiguous"})
            continue
        if not matched:
            ospf.append({**lone, "status": "neighbor_outside_collection"})
            continue
        peer, peer_if, _ = matched[0]
        back_rid = next((r for r in sorted(own_rids[key]) if (peer, peer_if, r) in views), None)
        back = views.get((peer, peer_if, back_rid)) if back_rid else None
        done.add((key, ifname, rid))
        if back_rid:
            done.add((peer, peer_if, back_rid))
        states = [row["state"], back["state"] if back else None]
        ospf.append(
            {
                "a": {
                    "device": key,
                    "interface": ifname,
                    "router_id": back_rid,
                    "scope": scope_of.get((key, ifname)),
                },
                "b": {
                    "device": peer,
                    "interface": peer_if,
                    "router_id": rid,
                    "scope": scope_of.get((peer, peer_if)),
                },
                "states": states,
                "status": "full"
                if states == ["full", "full"]
                else "one_sided"
                if back is None
                else "not_full",
            }
        )
    sessions, families = {}, defaultdict(list)
    for key, facts in sorted(devices.items()):
        for family, data in bgp_tables(facts):
            for row in data.get("peers") or []:
                sessions.setdefault((key, row["peer"]), (data.get("local_as"), row))
                families[(key, row["peer"])].append(family)
    done = set()
    for (key, address), (local_as, row) in sorted(sessions.items()):
        if (key, address) in done:
            continue
        peer = [o for o in owners.get(address, []) if o[0] != key]
        kind = "internal" if row["peer_as"] == local_as else "external"
        if len(peer) != 1:
            bgp.append(
                {
                    "a": {"device": key},
                    "peer": address,
                    "peer_as": row["peer_as"],
                    "type": kind,
                    "afi_safis": families[(key, address)],
                    "state": row["state"],
                    "status": "peer_outside_collection" if not peer else "peer_ambiguous",
                }
            )
            continue
        peer_key = peer[0][0]
        back = next(
            (
                (k, a)
                for (k, a) in sessions
                if k == peer_key and any(o[0] == key for o in owners.get(a, []))
            ),
            None,
        )
        done.add((key, address))
        if back:
            done.add(back)
        states = [row["state"], sessions[back][1]["state"] if back else None]
        bgp.append(
            {
                "a": {"device": key, "address": back[1] if back else None},
                "b": {"device": peer_key, "address": address},
                "type": kind,
                "afi_safis": sorted(set(families[(key, address)]) | set(families.get(back, []))),
                "states": states,
                "status": "established"
                if states == ["established", "established"]
                else "one_sided"
                if back is None
                else "not_established",
            }
        )
    return {"ospf": ospf, "bgp": bgp}
