"""Read-only answers other features ask the ledger for: systems, hosts, where an IP sits,
and who should reach whom on which ports when a firewall request is drafted.

Everything here only reads. An address the ledger does not know is still a valid party
to a request (an outside source, say); it is reported as unknown rather than refused.
Which firewalls and security zones a policy lands on is computed from the topology when
the policy is generated, so these answers never name firewalls.
"""

import ipaddress
import re

from sqlalchemy import select

from . import netmath
from .common import bad, sessions
from .inventory import port_text, related
from .models import Host, Subsystem, System
from .service import Lookup, locate, planners

# Hosts already taken out of service take no part in new access.
RETIRED = {"已下线"}
ITEM = re.compile(r"[\s,，、;；]+")


def system_ref(system):
    return {"name": system.name, "short_name": system.short_name, "importance": system.importance}


def where(lookup, effective):
    return {
        "region": lookup.name("region", effective["region"]) if effective.get("region") else None,
        "site": lookup.name("site", effective["site"]) if effective.get("site") else None,
        "zone": lookup.name("zone", effective["zone"]) if effective.get("zone") else None,
    }


def find_systems(actor, query):
    """Systems and subsystems whose name or short name contains the words asked for."""
    text = (query or "").strip().lower()
    with sessions() as session:
        lookup = Lookup(session)
        domains = planners(session, lookup)
        systems = session.scalars(select(System).order_by(System.name)).all()
        subsystems = session.scalars(select(Subsystem).order_by(Subsystem.name)).all()
        hosts = session.scalars(select(Host)).all()
        found = []
        for system in systems:
            subs = [s for s in subsystems if s.system_id == system.id]
            names = [system.name, system.short_name or "", *(s.name for s in subs)]
            if text and not any(text in name.lower() or name and name.lower() in text for name in names):
                continue
            blocks = []
            for space, index, planner in domains.values():
                for prefix in index.prefixes:
                    if prefix.system_id == system.id or prefix.subsystem_id in {s.id for s in subs}:
                        effective = planner.effective(prefix)
                        blocks.append({"network": prefix.network,
                                       "subsystem": lookup.name("subsystem", prefix.subsystem_id) if prefix.subsystem_id else None,
                                       **where(lookup, effective)})
            mine = [h for h in hosts if h.system_id == system.id]
            found.append({
                **system_ref(system),
                "department": system.department, "business_owner": system.business_owner, "ops_owner": system.ops_owner,
                "subsystems": [{"name": s.name, "hosts": sum(1 for h in mine if h.subsystem_id == s.id)} for s in subs],
                "blocks": blocks,
                "hosts": {status: sum(1 for h in mine if h.status == status) for status in sorted({h.status for h in mine})},
            })
            if len(found) >= 10:
                break
        return {"query": query, "systems": found}


def resolve_system(session, name, subsystem=None):
    """One system by its name, short name or an unambiguous part of either."""
    text = (name or "").strip()
    systems = session.scalars(select(System)).all()
    exact = [s for s in systems if text in (s.name, s.short_name)]
    # "西安风险评估系统" names the system with a place in front of it.
    loose = exact or [s for s in systems if text in s.name or s.name in text
                      or (s.short_name and (text in s.short_name or s.short_name in text))]
    if not loose:
        raise bad(f"台账里没有业务系统“{text}”")
    if len(loose) > 1:
        raise bad(f"“{text}”对应多个业务系统：{'、'.join(s.name for s in loose)}，请说明是哪一个")
    system = loose[0]
    sub = None
    if subsystem:
        subs = session.scalars(select(Subsystem).where(Subsystem.system_id == system.id)).all()
        matched = [s for s in subs if s.name == subsystem.strip()] or [s for s in subs if subsystem.strip() in s.name]
        if len(matched) != 1:
            raise bad(f"{system.name} 没有子系统“{subsystem}”" if not matched
                      else f"“{subsystem}”对应 {system.name} 的多个子系统：{'、'.join(s.name for s in matched)}")
        sub = matched[0]
    return system, sub


def host_rows(session, lookup, domains, hosts):
    addresses, services = related(session, [h.id for h in hosts])
    rows = []
    for host in hosts:
        ips = []
        for address in sorted(addresses.get(host.id, []), key=lambda a: a.value):
            _, index, planner = domains[address.space_id]
            block = index.deepest(address.value)
            ips.append({"ip": address.ip, "network": block.network if block else None,
                        **where(lookup, planner.effective(block))})
        rows.append({
            "hostname": host.hostname, "subsystem": lookup.name("subsystem", host.subsystem_id) if host.subsystem_id else None,
            "status": host.status, "environment": host.environment, "role": host.role,
            "ips": ips,
            "services": [port_text(s) for s in sorted(services.get(host.id, []), key=lambda s: (s.protocol, s.port_first or 0))],
        })
    return rows


def system_hosts(actor, system, subsystem=None, status=None):
    with sessions() as session:
        found, sub = resolve_system(session, system, subsystem)
        statement = select(Host).where(Host.system_id == found.id).order_by(Host.hostname)
        if sub:
            statement = statement.where(Host.subsystem_id == sub.id)
        if status:
            statement = statement.where(Host.status == status)
        hosts = session.scalars(statement.limit(201)).all()
        lookup = Lookup(session)
        rows = host_rows(session, lookup, planners(session, lookup), hosts[:200])
        return {"system": system_ref(found), "subsystem": sub.name if sub else None,
                "hosts": rows, "truncated": len(hosts) > 200}


def locate_ip(actor, ip):
    """Where an address sits in the plan, and the host using it."""
    try:
        value = netmath.ip_value(ip)
    except ValueError as exc:
        raise bad(str(exc)) from None
    with sessions() as session:
        lookup = Lookup(session)
        found = []
        for hit in locate(session, value):
            block, address = hit["block"], hit["address"]
            effective = hit["planner"].effective(block)
            entry = {
                "routing_domain": hit["space"].name,
                "network": block.network if block else None,
                "path": [f"{step['level']} {step['name']}" for step in effective["path"]],
                "system": lookup.name("system", effective["system"]) if effective.get("system") else None,
                "subsystem": lookup.name("subsystem", effective["subsystem"]) if effective.get("subsystem") else None,
                **where(lookup, effective),
                "allocated": bool(address),
                "host": None,
            }
            if address and address.host_id:
                host = session.get(Host, address.host_id)
                entry["host"] = host_rows(session, lookup, {hit["space"].id: (hit["space"], hit["index"], hit["planner"])}, [host])[0]
            elif address:
                entry["name"] = address.hostname
                entry["kind"] = address.kind
            found.append(entry)
        return {"ip": netmath.ip_text(value), "found": bool(found), "places": found}


def parse_parties(text):
    """Addresses written as IPs, CIDRs or ranges (10.6.145.180-182 allowed); None when it is a name."""
    items = [item for item in ITEM.split(text or "") if item]
    if not items:
        return None
    parsed = []
    for item in items:
        try:
            if "/" in item:
                ipaddress.IPv4Network(item, strict=False)
            elif "-" in item:
                first, last = item.split("-", 1)
                ipaddress.IPv4Address(first)
                if "." not in last:
                    last = first.rsplit(".", 1)[0] + "." + last
                ipaddress.IPv4Address(last)
            else:
                ipaddress.IPv4Address(item)
        except ValueError:
            return None
        parsed.append(item)
    return parsed


PORT = re.compile(r"^(tcp|udp|icmp)(?:/(\d{1,5}(?:-\d{1,5})?))?$", re.IGNORECASE)


def parse_ports(text):
    result = []
    for item in [i for i in ITEM.split(text or "") if i]:
        match = PORT.match(item)
        if not match or (match.group(1).lower() != "icmp" and not match.group(2)):
            raise bad(f"端口“{item}”无效，请写成 tcp/80、udp/53、tcp/8000-8010 或 icmp")
        result.append((match.group(1).lower(), match.group(2) or ""))
    return result


def party(session, lookup, domains, text, subsystem, label):
    """One side of an access: given addresses, or the in-service hosts of a system."""
    addresses = parse_parties(text)
    if addresses is not None:
        known = []
        for item in addresses:
            entry = {"address": item, "in_ledger": False}
            if "/" not in item and "-" not in item:
                places = [hit for hit in locate(session, netmath.ip_value(item), domains) if hit["block"]]
                if places:
                    hit = places[0]
                    effective = hit["planner"].effective(hit["block"])
                    entry.update(in_ledger=True, network=hit["block"].network,
                                 system=lookup.name("system", effective["system"]) if effective.get("system") else None,
                                 **where(lookup, effective))
            known.append(entry)
        return {"kind": "addresses", "label": text.strip(), "system": None, "addresses": known, "hosts": []}
    system, sub = resolve_system(session, text, subsystem)
    statement = select(Host).where(Host.system_id == system.id, Host.status.not_in(RETIRED)).order_by(Host.hostname)
    if sub:
        statement = statement.where(Host.subsystem_id == sub.id)
    hosts = host_rows(session, lookup, domains, session.scalars(statement).all())
    name = f"{system.name}/{sub.name}" if sub else system.name
    if not hosts:
        raise bad(f"{label}“{name}”在台账里没有在用的主机")
    return {"kind": "system", "label": name, "system": system.name, "addresses": [], "hosts": hosts}


def addresses_of(side):
    if side["kind"] == "addresses":
        return [entry["address"] for entry in side["addresses"]]
    return [ip["ip"] for host in side["hosts"] for ip in host["ips"]]


def plan_access(actor, source, destination, source_subsystem=None, destination_subsystem=None, ports=None):
    """Firewall request rows for source → destination, from the ledger's hosts and ports."""
    with sessions() as session:
        lookup = Lookup(session)
        domains = planners(session, lookup)
        src = party(session, lookup, domains, source, source_subsystem, "源")
        dst = party(session, lookup, domains, destination, destination_subsystem, "目的")
        notes = []
        for entry in src["addresses"] + dst["addresses"]:
            if not entry["in_ledger"]:
                notes.append(f"{entry['address']} 不在台账的已规划网段里，按申请照常放行")
        src_ips = addresses_of(src)
        if not src_ips:
            raise bad(f"源“{src['label']}”的主机在台账里都没有 IP")
        wanted = parse_ports(ports) if ports else None
        groups = {}
        if wanted is not None:
            dst_ips = addresses_of(dst)
            for proto, port in wanted:
                groups.setdefault((proto, port), []).extend(dst_ips)
        elif dst["kind"] == "addresses":
            raise bad("目的是 IP 地址时，请说明要开通的端口，例如 tcp/443")
        else:
            for host in dst["hosts"]:
                if not host["services"]:
                    notes.append(f"主机 {host['hostname']} 没有登记服务端口，没有为它生成策略")
                    continue
                if not host["ips"]:
                    notes.append(f"主机 {host['hostname']} 没有 IP，没有为它生成策略")
                    continue
                for service in host["services"]:
                    proto, _, port = service.partition("/")
                    groups.setdefault((proto.lower(), port), []).extend(ip["ip"] for ip in host["ips"])
        if not dst["addresses"] and not any(host["ips"] for host in dst["hosts"]):
            raise bad(f"目的“{dst['label']}”的主机在台账里都没有 IP")
        rows = [{
            "source_system": src["system"] or "", "src_ip": ",".join(dict.fromkeys(src_ips)),
            "destination_system": dst["system"] or "", "dst_ip": ",".join(dict.fromkeys(ips)),
            "port": port, "proto": proto,
        } for (proto, port), ips in sorted(groups.items(), key=lambda item: (item[0][0], int(item[0][1].split("-")[0] or 0)))]
        return {"source": src, "destination": dst, "rows": rows, "notes": notes}
