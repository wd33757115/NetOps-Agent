"""The application ledger: business systems, their subsystems, hosts and service ports.

A host belongs to one system (and optionally one of its subsystems), carries its own
lifecycle status, and lists the ports it serves. Its addresses come from the IP ledger:
an address already allocated is linked, a new one is allocated on the spot, and either
way it must sit in a block planned for the host's system.
"""

import re

from fastapi import HTTPException
from sqlalchemy import func, or_, select

from . import netmath
from .common import bad, can_edit, diff, missing, new_id, now, record, require_editor, sessions
from .models import Address, Host, Prefix, Service, Subsystem, System
from .schemas import HostIn, ServiceIn, SubsystemIn, SystemIn
from .service import (
    CLOSED,
    Lookup,
    check_free,
    host_problem,
    locate,
    next_free,
    planners,
    validate_all,
)

SYSTEM_NAMES = {
    "name": "名称", "short_name": "简称", "importance": "重要性", "department": "主管部门",
    "business_owner": "业务负责人", "ops_owner": "运维负责人", "contact": "联系方式", "note": "备注",
}
SUBSYSTEM_NAMES = {"system_id": "业务系统", "name": "名称", "owner": "负责人", "note": "备注"}
HOST_NAMES = {
    "hostname": "主机名", "system_id": "业务系统", "subsystem_id": "子系统", "environment": "环境",
    "status": "状态", "kind": "类型", "role": "角色", "os": "操作系统", "owner": "负责人",
    "online_date": "上线日期", "offline_date": "下线日期", "tags": "标签", "note": "备注",
}
PORTS = re.compile(r"^(\d{1,5})(?:-(\d{1,5}))?$")


# --------------------------------------------------------------------------- systems


def systems_page(actor):
    """Systems with their subsystems, hosts by status, and the blocks planned for them."""
    with sessions() as session:
        lookup = Lookup(session)
        domains = planners(session, lookup)
        systems = session.scalars(select(System).order_by(System.name)).all()
        subsystems = session.scalars(select(Subsystem).order_by(Subsystem.name)).all()
        counts = {}
        for system_id, subsystem_id, status, total in session.execute(
            select(Host.system_id, Host.subsystem_id, Host.status, func.count()).group_by(
                Host.system_id, Host.subsystem_id, Host.status)
        ).all():
            for key in (system_id, (system_id, subsystem_id)):
                counts.setdefault(key, {})
                counts[key][status] = counts[key].get(status, 0) + total
        blocks = {}
        for prefix in session.scalars(select(Prefix).where(or_(
            Prefix.system_id.is_not(None), Prefix.subsystem_id.is_not(None))).order_by(Prefix.first)).all():
            space, index, planner = domains[prefix.space_id]
            effective = planner.effective(prefix)
            key = prefix.subsystem_id or prefix.system_id
            blocks.setdefault(key, []).append({
                "id": prefix.id, "network": prefix.network, "space": space.name,
                "where": [step["name"] for step in effective["path"] if step["dimension"] in ("region", "site", "zone")],
                "zone": lookup.zone_ref(effective.get("zone")),
                "usage": index.address_usage(prefix),
            })
        return {
            "can_edit": can_edit(actor),
            "systems": [{
                **system_view(system),
                "hosts": counts.get(system.id, {}),
                "blocks": blocks.get(system.id, []),
                "subsystems": [{
                    "id": sub.id, "name": sub.name, "owner": sub.owner, "note": sub.note,
                    "hosts": counts.get((system.id, sub.id), {}),
                    "blocks": blocks.get(sub.id, []),
                } for sub in subsystems if sub.system_id == system.id],
            } for system in systems],
        }


def system_view(system):
    return {key: getattr(system, key) for key in ("id", *SYSTEM_NAMES)}


def save_system(actor, body: SystemIn, system_id=None):
    require_editor(actor)
    with sessions() as session:
        clash = session.scalars(select(System).where(System.name == body.name)).first()
        if clash and clash.id != system_id:
            raise bad(f"业务系统“{body.name}”已存在")
        if system_id is None:
            system = System(id=new_id(), **body.model_dump())
            session.add(system)
            record(session, actor, "业务系统", system.id, f"业务系统 {system.name}", "新增")
        else:
            system = session.get(System, system_id)
            if not system:
                raise missing("业务系统")
            changes = diff(system, body.model_dump(), SYSTEM_NAMES)
            if changes:
                record(session, actor, "业务系统", system.id, f"业务系统 {system.name}", "修改", changes)
        return {"id": system.id, "name": system.name}


def delete_system(actor, system_id):
    require_editor(actor)
    with sessions() as session:
        system = session.get(System, system_id)
        if not system:
            raise missing("业务系统")
        if session.scalars(select(Subsystem).where(Subsystem.system_id == system_id)).first():
            raise bad("业务系统下还有子系统，请先删除子系统")
        if session.scalars(select(Host).where(Host.system_id == system_id)).first():
            raise bad("业务系统下还有主机，请先删除或移走主机")
        if session.scalars(select(Prefix).where(Prefix.system_id == system_id)).first():
            raise bad("还有网段划给了这个业务系统，请先修改这些网段")
        session.delete(system)
        record(session, actor, "业务系统", system.id, f"业务系统 {system.name}", "删除")
    return {"deleted": True}


def save_subsystem(actor, body: SubsystemIn, subsystem_id=None):
    require_editor(actor)
    with sessions() as session:
        system = session.get(System, body.system_id)
        if not system:
            raise missing("业务系统")
        clash = session.scalars(select(Subsystem).where(
            Subsystem.system_id == body.system_id, Subsystem.name == body.name)).first()
        if clash and clash.id != subsystem_id:
            raise bad(f"{system.name} 已有子系统“{body.name}”")
        if subsystem_id is None:
            subsystem = Subsystem(id=new_id(), **body.model_dump())
            session.add(subsystem)
            record(session, actor, "子系统", subsystem.id, f"子系统 {system.name} / {subsystem.name}", "新增")
        else:
            subsystem = session.get(Subsystem, subsystem_id)
            if not subsystem:
                raise missing("子系统")
            if subsystem.system_id != body.system_id and session.scalars(
                    select(Host).where(Host.subsystem_id == subsystem_id)).first():
                raise bad("子系统下还有主机，不能移到别的业务系统")
            changes = diff(subsystem, body.model_dump(), SUBSYSTEM_NAMES)
            if changes:
                # Blocks of this subsystem may sit under another system's block.
                validate_all(session)
                lookup = Lookup(session)
                for change in changes:
                    if change["field"] == "业务系统":
                        change["before"], change["after"] = (lookup.name("system", change["before"]),
                                                             lookup.name("system", change["after"]))
                record(session, actor, "子系统", subsystem.id, f"子系统 {system.name} / {subsystem.name}", "修改",
                       changes)
        return {"id": subsystem.id, "name": subsystem.name}


def delete_subsystem(actor, subsystem_id):
    require_editor(actor)
    with sessions() as session:
        subsystem = session.get(Subsystem, subsystem_id)
        if not subsystem:
            raise missing("子系统")
        if session.scalars(select(Host).where(Host.subsystem_id == subsystem_id)).first():
            raise bad("子系统下还有主机，请先删除或移走主机")
        if session.scalars(select(Prefix).where(Prefix.subsystem_id == subsystem_id)).first():
            raise bad("还有网段划给了这个子系统，请先修改这些网段")
        session.delete(subsystem)
        record(session, actor, "子系统", subsystem.id, f"子系统 {subsystem.name}", "删除")
    return {"deleted": True}


# --------------------------------------------------------------------------- hosts


def parse_ports(service: ServiceIn):
    if service.protocol == "ICMP":
        return None, None
    match = PORTS.match(service.ports or "")
    if not match:
        raise bad(f"{service.protocol} 端口“{service.ports or ''}”无效，请写成 80 或 8000-8010")
    first = int(match.group(1))
    last = int(match.group(2) or first)
    if not 1 <= first <= last <= 65535:
        raise bad(f"{service.protocol} 端口“{service.ports}”无效，端口在 1 到 65535 之间，且起始不大于结束")
    return first, last


def port_text(service):
    if service.port_first is None:
        return service.protocol
    ports = str(service.port_first) if service.port_first == service.port_last else f"{service.port_first}-{service.port_last}"
    return f"{service.protocol}/{ports}"


def service_view(service):
    return {
        "id": service.id, "protocol": service.protocol, "port_first": service.port_first,
        "port_last": service.port_last, "text": port_text(service),
        "ports": None if service.port_first is None else port_text(service).split("/", 1)[1],
        "name": service.name, "note": service.note,
    }


def host_view(host, lookup, addresses, services, domains=None):
    ips = []
    for address in sorted(addresses, key=lambda a: a.value):
        entry = {"id": address.id, "ip": address.ip, "space_id": address.space_id}
        if domains and address.space_id in domains:
            space, index, planner = domains[address.space_id]
            block = index.deepest(address.value)
            if block:
                effective = planner.effective(block)
                entry.update(prefix={"id": block.id, "network": block.network},
                             zone=lookup.zone_ref(effective.get("zone")),
                             space=space.name)
        ips.append(entry)
    return {
        "id": host.id, "hostname": host.hostname,
        "system": lookup.ref("system", host.system_id),
        "subsystem": lookup.ref("subsystem", host.subsystem_id),
        **{key: getattr(host, key) for key in (
            "environment", "status", "kind", "role", "os", "owner", "online_date", "offline_date",
            "tags", "note", "updated_at", "updated_by")},
        "ips": ips,
        "services": [service_view(s) for s in sorted(services, key=lambda s: (s.protocol, s.port_first or 0))],
    }


def hosts_page(actor, system_id=None, subsystem_id=None, status=None, environment=None, query=None,
               offset=0, limit=50):
    with sessions() as session:
        statement = select(Host)
        if system_id:
            statement = statement.where(Host.system_id == system_id)
        if subsystem_id:
            statement = statement.where(Host.subsystem_id == subsystem_id)
        if status:
            statement = statement.where(Host.status == status)
        if environment:
            statement = statement.where(Host.environment == environment)
        text = (query or "").strip()
        if text:
            like = f"%{text}%"
            by_ip = select(Address.host_id).where(Address.ip.like(f"{text}%"), Address.host_id.is_not(None))
            statement = statement.where(or_(
                Host.hostname.ilike(like), Host.owner.ilike(like), Host.tags.ilike(like),
                Host.note.ilike(like), Host.id.in_(by_ip)))
        total = session.scalar(select(func.count()).select_from(statement.subquery()))
        hosts = session.scalars(statement.order_by(Host.hostname).offset(offset).limit(limit)).all()
        lookup = Lookup(session)
        domains = planners(session, lookup)
        addresses, services = related(session, [h.id for h in hosts])
        return {
            "can_edit": can_edit(actor),
            "total": total,
            "hosts": [host_view(h, lookup, addresses.get(h.id, []), services.get(h.id, []), domains) for h in hosts],
        }


def related(session, host_ids):
    addresses, services = {}, {}
    if host_ids:
        for address in session.scalars(select(Address).where(Address.host_id.in_(host_ids))).all():
            addresses.setdefault(address.host_id, []).append(address)
        for service in session.scalars(select(Service).where(Service.host_id.in_(host_ids))).all():
            services.setdefault(service.host_id, []).append(service)
    return addresses, services


def host_detail(actor, host_id):
    with sessions() as session:
        host = session.get(Host, host_id)
        if not host:
            raise missing("主机")
        lookup = Lookup(session)
        addresses, services = related(session, [host.id])
        view = host_view(host, lookup, addresses.get(host.id, []), services.get(host.id, []), planners(session, lookup))
        view["can_edit"] = can_edit(actor)
        return view


def check_owner(session, body: HostIn):
    system = session.get(System, body.system_id)
    if not system:
        raise missing("业务系统")
    if body.subsystem_id:
        subsystem = session.get(Subsystem, body.subsystem_id)
        if not subsystem:
            raise missing("子系统")
        if subsystem.system_id != system.id:
            raise bad(f"子系统“{subsystem.name}”不属于业务系统“{system.name}”")


def link_address(session, actor, host, value, lookup, domains):
    """Link an allocated address to the host, or allocate it from the host's own block."""
    text = netmath.ip_text(value)
    hits = [hit for hit in locate(session, value, domains) if hit["block"] or hit["address"]]
    fitting = [hit for hit in hits
               if not host_problem(lookup, host, hit["planner"].effective(hit["block"]))]
    if not fitting:
        if not hits:
            raise bad(f"{text} 不在任何已规划的网段里，请先在网段规划里为业务系统划分网段")
        hit = hits[0]
        problem = host_problem(lookup, host, hit["planner"].effective(hit["block"]))
        raise bad(f"{text} 不能给主机 {host.hostname}：{problem}")
    if len(fitting) > 1:
        raise bad(f"{text} 在多个路由域里都属于这个业务系统，请在网段规划里核对")
    hit = fitting[0]
    address = hit["address"]
    if address:
        if address.host_id and address.host_id != host.id:
            other = session.get(Host, address.host_id)
            raise bad(f"{text} 已是主机 {other.hostname if other else ''} 的地址")
        address.host_id, address.hostname = host.id, host.hostname
        return address, "关联"
    check_free(hit["index"], hit["block"], value)
    address = Address(
        id=new_id(), space_id=hit["space"].id, value=value, ip=text, kind="主机", hostname=host.hostname,
        assigned_at=now(), assigned_by=actor.username, host_id=host.id,
    )
    session.add(address)
    hit["index"].add(address)
    return address, "分配"


def wanted_ips(texts):
    wanted = []
    for text in texts:
        try:
            value = netmath.ip_value(text)
        except ValueError as exc:
            raise bad(str(exc)) from None
        if value not in wanted:
            wanted.append(value)
    return wanted


def wanted_ports(services):
    ports = [(service, *parse_ports(service)) for service in services]
    keys = [(s.protocol, first, last) for s, first, last in ports]
    if len(set(keys)) != len(keys):
        raise bad("服务端口有重复")
    return ports


def sync_host(session, actor, host, wanted, ports, lookup, domains):
    """Make the host's addresses and ports exactly these; returns the change-log entries."""
    changes = []
    current = session.scalars(select(Address).where(Address.host_id == host.id)).all()
    before_ips = sorted(a.value for a in current)
    for address in current:
        if address.value in wanted:
            address.hostname = host.hostname
        else:
            # The address stays allocated; it can be released from its block.
            address.host_id = None
    for value in wanted:
        if value not in before_ips:
            address, how = link_address(session, actor, host, value, lookup, domains)
            session.flush()
            record(session, actor, "IP 地址", address.id, f"IP {address.ip}", how,
                   [{"field": "主机", "before": None, "after": host.hostname}])
    session.flush()
    # The host's system may have changed: every address it keeps must still fit.
    for address in session.scalars(select(Address).where(Address.host_id == host.id)).all():
        _, index, planner = domains[address.space_id]
        problem = host_problem(lookup, host, planner.effective(index.deepest(address.value)))
        if problem:
            raise bad(f"{address.ip} 不能给主机 {host.hostname}：{problem}")
    existing = session.scalars(select(Service).where(Service.host_id == host.id)).all()
    before_ports = sorted(port_text(s) for s in existing)
    for service in existing:
        session.delete(service)
    for service, first, last in ports:
        session.add(Service(id=new_id(), host_id=host.id, protocol=service.protocol, port_first=first,
                            port_last=last, name=service.name, note=service.note))
    session.flush()
    after_ports = sorted(port_text(s) for s in session.scalars(select(Service).where(Service.host_id == host.id)))
    if before_ports != after_ports:
        changes.append({"field": "服务端口", "before": "、".join(before_ports) or None,
                        "after": "、".join(after_ports) or None})
    if sorted(wanted) != before_ips:
        changes.append({"field": "IP 地址", "before": "、".join(netmath.ip_text(v) for v in before_ips) or None,
                        "after": "、".join(netmath.ip_text(v) for v in sorted(wanted)) or None})
    return changes


def named(lookup, changes):
    """Show the host's system and subsystem by name in the change log."""
    for change in changes:
        kind = {"业务系统": "system", "子系统": "subsystem"}.get(change["field"])
        if kind:
            change["before"] = lookup.name(kind, change["before"]) if change["before"] else None
            change["after"] = lookup.name(kind, change["after"]) if change["after"] else None
    return changes


def save_host(actor, body: HostIn, host_id=None):
    require_editor(actor)
    wanted, ports = wanted_ips(body.ips), wanted_ports(body.services)
    with sessions() as session:
        check_owner(session, body)
        values = body.model_dump(exclude={"ips", "services"})
        if host_id is None:
            host = Host(id=new_id(), updated_at=now(), updated_by=actor.username, **values)
            session.add(host)
            changes, action = [], "新增"
        else:
            host = session.get(Host, host_id)
            if not host:
                raise missing("主机")
            changes, action = diff(host, values, HOST_NAMES), "修改"
            host.updated_at, host.updated_by = now(), actor.username
        session.flush()
        lookup = Lookup(session)
        changes += sync_host(session, actor, host, wanted, ports, lookup, planners(session, lookup))
        if action == "新增" or changes:
            record(session, actor, "主机", host.id, f"主机 {host.hostname}", action, named(lookup, changes) or None)
        return {"id": host.id}


def delete_host(actor, host_id):
    require_editor(actor)
    with sessions() as session:
        host = session.get(Host, host_id)
        if not host:
            raise missing("主机")
        kept = []
        for address in session.scalars(select(Address).where(Address.host_id == host_id).order_by(Address.value)):
            # Addresses stay allocated under the host name until someone releases them.
            address.host_id = None
            kept.append(address.ip)
        for service in session.scalars(select(Service).where(Service.host_id == host_id)).all():
            session.delete(service)
        session.flush()
        session.delete(host)
        record(session, actor, "主机", host.id, f"主机 {host.hostname}", "删除",
               [{"field": "IP 地址", "before": "、".join(kept), "after": None}] if kept else None)
    return {"deleted": True, "kept_addresses": kept}


def suggest_ip(actor, system_id, subsystem_id=None, taken=()):
    """The next free address in a block planned for this system or subsystem."""
    with sessions() as session:
        lookup = Lookup(session)
        if not lookup.get("system", system_id):
            raise missing("业务系统")
        skip = set()
        for text in taken:
            try:
                skip.add(netmath.ip_value(text))
            except ValueError:
                continue
        leaves = []
        for space, index, planner in planners(session, lookup).values():
            for prefix in index.prefixes:
                if index.children[prefix.id] or prefix.status in CLOSED:
                    continue
                effective = planner.effective(prefix)
                if effective.get("system") == system_id and (
                        subsystem_id is None or effective.get("subsystem") in (None, subsystem_id)):
                    leaves.append((space, index, prefix, effective.get("subsystem")))
        # A subsystem's own blocks come first; a host without one starts with the shared blocks.
        leaves.sort(key=lambda leaf: (leaf[3] is None) == bool(subsystem_id))
        for space, index, prefix, _ in leaves:
            try:
                value = next_free(index, prefix, skip)
            except HTTPException:
                continue
            return {"ip": netmath.ip_text(value), "prefix": {"id": prefix.id, "network": prefix.network},
                    "space": space.name}
        owner = lookup.name("subsystem", subsystem_id) if subsystem_id else lookup.name("system", system_id)
        raise bad(f"“{owner}”还没有可分配地址的网段，请先在网段规划里为它划分网段")
