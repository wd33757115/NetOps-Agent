"""Carrier circuits: providers with their networks and points of presence, circuit types,
shared physical risks, and the circuits themselves with their A/Z ends.

Only technical facts are kept. A circuit's interconnect block comes from the IP plan (an
existing leaf block, or the next free one carved under a block), and the addresses its two
ends use are allocated there and stay owned by the circuit until it is retired.
"""

from sqlalchemy import func, or_, select

from . import netmath
from .common import bad, can_edit, diff, missing, new_id, now, record, require_editor, sessions
from .models import (
    Address,
    Circuit,
    CircuitRisk,
    CircuitType,
    Pop,
    Prefix,
    Provider,
    ProviderNetwork,
    Region,
    RiskGroup,
    Site,
    Termination,
)
from .schemas import (
    CircuitIn,
    CircuitTypeIn,
    NetworkIn,
    PopIn,
    PrefixIn,
    ProviderIn,
    RiskIn,
    TerminationIn,
)
from .service import SpaceIndex, add_prefix, check_free, resolve_space

RETIRED = "已拆除"
PROVIDER_NAMES = {"name": "名称", "support_phone": "报障电话", "account": "客户编号", "contact": "客户经理", "note": "备注"}
NETWORK_NAMES = {"provider_id": "运营商", "name": "名称", "kind": "类型", "service_id": "服务标识",
                 "interconnects": "站点间互通", "note": "备注"}
CIRCUIT_NAMES = {
    "provider_id": "运营商", "cid": "电路编号", "type_id": "类型", "status": "状态", "bandwidth": "带宽",
    "install_date": "开通日期", "description": "说明", "prefix_id": "互联网段", "vlan": "VLAN",
    "routing": "路由方式", "local_as": "本端 AS", "remote_as": "对端 AS", "note": "备注",
}
END_NAMES = {"site_id": "站点", "network_id": "运营商网络", "device": "设备", "interface": "接口",
             "port_speed": "端口速率", "xconnect": "配线", "pop_id": "局端", "ip": "地址", "note": "备注"}


def save(session, actor, model, body, ident, entity, names, label, unique):
    """Create or update a simple row; ``unique`` finds a clashing row by its natural key."""
    clash = session.scalars(unique).first()
    if clash and clash.id != ident:
        raise bad(f"{entity}“{label}”已存在")
    if ident is None:
        row = model(id=new_id(), **body.model_dump())
        session.add(row)
        record(session, actor, entity, row.id, f"{entity} {label}", "新增")
        return row
    row = session.get(model, ident)
    if not row:
        raise missing(entity)
    changes = diff(row, body.model_dump(), names)
    if changes:
        record(session, actor, entity, row.id, f"{entity} {label}", "修改", changes)
    return row


def remove(session, actor, row, entity, label):
    session.delete(row)
    record(session, actor, entity, row.id, f"{entity} {label}", "删除")
    return {"deleted": True}


# --------------------------------------------------------------------------- providers


def used_by(session, column, ident):
    return session.scalar(select(func.count()).select_from(Termination).where(column == ident)) or 0


def providers_page(actor):
    with sessions() as session:
        providers = session.scalars(select(Provider).order_by(Provider.name)).all()
        networks = session.scalars(select(ProviderNetwork).order_by(ProviderNetwork.name)).all()
        pops = session.scalars(select(Pop).order_by(Pop.name)).all()
        circuits = dict(session.execute(
            select(Circuit.provider_id, func.count()).group_by(Circuit.provider_id)).all())
        net_use = dict(session.execute(select(Termination.network_id, func.count())
                                       .where(Termination.network_id.is_not(None)).group_by(Termination.network_id)).all())
        pop_use = dict(session.execute(select(Termination.pop_id, func.count())
                                       .where(Termination.pop_id.is_not(None)).group_by(Termination.pop_id)).all())
        return {
            "can_edit": can_edit(actor),
            "providers": [{
                **{key: getattr(p, key) for key in ("id", *PROVIDER_NAMES)},
                "circuits": circuits.get(p.id, 0),
                "networks": [{**network_view(n), "used": net_use.get(n.id, 0)} for n in networks if n.provider_id == p.id],
                "pops": [{"id": o.id, "name": o.name, "note": o.note, "used": pop_use.get(o.id, 0)}
                         for o in pops if o.provider_id == p.id],
            } for p in providers],
        }


def network_view(network):
    return {key: getattr(network, key) for key in ("id", *NETWORK_NAMES)}


def save_provider(actor, body: ProviderIn, ident=None):
    require_editor(actor)
    with sessions() as session:
        row = save(session, actor, Provider, body, ident, "运营商", PROVIDER_NAMES, body.name,
                   select(Provider).where(Provider.name == body.name))
        return {"id": row.id, "name": row.name}


def delete_provider(actor, ident):
    require_editor(actor)
    with sessions() as session:
        provider = session.get(Provider, ident)
        if not provider:
            raise missing("运营商")
        if session.scalars(select(Circuit).where(Circuit.provider_id == ident)).first():
            raise bad("这个运营商还有专线，请先删除或改到别的运营商")
        for network in session.scalars(select(ProviderNetwork).where(ProviderNetwork.provider_id == ident)).all():
            if used_by(session, Termination.network_id, network.id):
                raise bad(f"运营商网络“{network.name}”还有专线接入，请先修改这些专线")
            session.delete(network)
        for pop in session.scalars(select(Pop).where(Pop.provider_id == ident)).all():
            if used_by(session, Termination.pop_id, pop.id):
                raise bad(f"局端“{pop.name}”还有专线使用，请先修改这些专线")
            session.delete(pop)
        session.flush()
        return remove(session, actor, provider, "运营商", provider.name)


def save_network(actor, body: NetworkIn, ident=None):
    require_editor(actor)
    with sessions() as session:
        if not session.get(Provider, body.provider_id):
            raise missing("运营商")
        row = save(session, actor, ProviderNetwork, body, ident, "运营商网络", NETWORK_NAMES, body.name,
                   select(ProviderNetwork).where(ProviderNetwork.provider_id == body.provider_id,
                                                 ProviderNetwork.name == body.name))
        return {"id": row.id, "name": row.name}


def delete_network(actor, ident):
    require_editor(actor)
    with sessions() as session:
        network = session.get(ProviderNetwork, ident)
        if not network:
            raise missing("运营商网络")
        if used_by(session, Termination.network_id, ident):
            raise bad("还有专线接入这个运营商网络，请先修改这些专线")
        return remove(session, actor, network, "运营商网络", network.name)


def save_pop(actor, body: PopIn, ident=None):
    require_editor(actor)
    with sessions() as session:
        if not session.get(Provider, body.provider_id):
            raise missing("运营商")
        current = session.get(Pop, ident) if ident else None
        # A circuit only reaches its own provider's points of presence.
        if current and current.provider_id != body.provider_id and used_by(session, Termination.pop_id, ident):
            raise bad("还有专线使用这个局端，不能改到别的运营商")
        row = save(session, actor, Pop, body, ident, "局端", {"provider_id": "运营商", "name": "名称", "note": "备注"},
                   body.name, select(Pop).where(Pop.provider_id == body.provider_id, Pop.name == body.name))
        return {"id": row.id, "name": row.name}


def delete_pop(actor, ident):
    require_editor(actor)
    with sessions() as session:
        pop = session.get(Pop, ident)
        if not pop:
            raise missing("局端")
        if used_by(session, Termination.pop_id, ident):
            raise bad("还有专线使用这个局端，请先修改这些专线")
        return remove(session, actor, pop, "局端", pop.name)


# --------------------------------------------------------------------------- types and shared risks


def types_page(actor):
    with sessions() as session:
        use = dict(session.execute(select(Circuit.type_id, func.count()).group_by(Circuit.type_id)).all())
        return {"can_edit": can_edit(actor), "types": [
            {"id": t.id, "name": t.name, "note": t.note, "used": use.get(t.id, 0)}
            for t in session.scalars(select(CircuitType).order_by(CircuitType.name)).all()]}


def save_type(actor, body: CircuitTypeIn, ident=None):
    require_editor(actor)
    with sessions() as session:
        row = save(session, actor, CircuitType, body, ident, "专线类型", {"name": "名称", "note": "备注"}, body.name,
                   select(CircuitType).where(CircuitType.name == body.name))
        return {"id": row.id, "name": row.name}


def delete_type(actor, ident):
    require_editor(actor)
    with sessions() as session:
        row = session.get(CircuitType, ident)
        if not row:
            raise missing("专线类型")
        if session.scalars(select(Circuit).where(Circuit.type_id == ident)).first():
            raise bad("还有专线是这个类型，请先修改这些专线")
        return remove(session, actor, row, "专线类型", row.name)


def risks_page(actor):
    with sessions() as session:
        use = {}
        for risk_id, cid in session.execute(select(CircuitRisk.risk_id, Circuit.cid)
                                            .join(Circuit, Circuit.id == CircuitRisk.circuit_id)).all():
            use.setdefault(risk_id, []).append(cid)
        return {"can_edit": can_edit(actor), "risks": [
            {"id": r.id, "name": r.name, "kind": r.kind, "note": r.note, "circuits": sorted(use.get(r.id, []))}
            for r in session.scalars(select(RiskGroup).order_by(RiskGroup.name)).all()]}


def save_risk(actor, body: RiskIn, ident=None):
    require_editor(actor)
    with sessions() as session:
        row = save(session, actor, RiskGroup, body, ident, "共享风险", {"name": "名称", "kind": "类型", "note": "备注"},
                   body.name, select(RiskGroup).where(RiskGroup.name == body.name))
        return {"id": row.id, "name": row.name}


def delete_risk(actor, ident):
    """A risk is a label: deleting it takes it off the circuits it was on."""
    require_editor(actor)
    with sessions() as session:
        row = session.get(RiskGroup, ident)
        if not row:
            raise missing("共享风险")
        links = session.scalars(select(CircuitRisk).where(CircuitRisk.risk_id == ident)).all()
        for link in links:
            session.delete(link)
        session.flush()
        result = remove(session, actor, row, "共享风险", row.name)
        return {**result, "unlinked": len(links)}


# --------------------------------------------------------------------------- circuits


class Names:
    """Names of what circuits refer to, loaded once per request."""

    def __init__(self, session):
        self.providers = {p.id: p for p in session.scalars(select(Provider)).all()}
        self.types = {t.id: t for t in session.scalars(select(CircuitType)).all()}
        self.sites = {s.id: s for s in session.scalars(select(Site)).all()}
        self.regions = {r.id: r for r in session.scalars(select(Region)).all()}
        self.networks = {n.id: n for n in session.scalars(select(ProviderNetwork)).all()}
        self.pops = {o.id: o for o in session.scalars(select(Pop)).all()}
        self.risks = {r.id: r for r in session.scalars(select(RiskGroup)).all()}

    def end(self, termination):
        if termination is None:
            return None
        site = self.sites.get(termination.site_id)
        network = self.networks.get(termination.network_id)
        pop = self.pops.get(termination.pop_id)
        if site:
            region = self.regions.get(site.region_id)
            place = {"kind": "site", "id": site.id, "name": site.name, "detail": " · ".join(
                part for part in (region.name if region else None, site.kind, "核心站点" if site.core else None) if part)}
        elif network:
            provider = self.providers.get(network.provider_id)
            place = {"kind": "network", "id": network.id, "name": network.name, "detail": " · ".join(
                part for part in (provider.name if provider else None, network.kind, network.service_id) if part)}
        else:
            place = None
        return {
            "place": place, "site_id": termination.site_id, "network_id": termination.network_id,
            "device": termination.device, "interface": termination.interface, "port_speed": termination.port_speed,
            "xconnect": termination.xconnect, "pop": {"id": pop.id, "name": pop.name} if pop else None,
            "pop_id": termination.pop_id, "ip": termination.ip, "allocated": bool(termination.address_id),
            "note": termination.note,
        }

    def label(self, termination):
        end = self.end(termination)
        return end["place"]["name"] if end and end["place"] else "—"


def ends_of(session, circuit_ids):
    found = {}
    if circuit_ids:
        for termination in session.scalars(select(Termination).where(Termination.circuit_id.in_(circuit_ids))).all():
            found.setdefault(termination.circuit_id, {})[termination.side] = termination
    return found


def risks_of(session, circuit_ids):
    found = {}
    if circuit_ids:
        for circuit_id, risk_id in session.execute(
                select(CircuitRisk.circuit_id, CircuitRisk.risk_id).where(CircuitRisk.circuit_id.in_(circuit_ids))).all():
            found.setdefault(circuit_id, []).append(risk_id)
    return found


def circuit_row(circuit, names, ends, risk_ids, prefixes):
    provider = names.providers.get(circuit.provider_id)
    kind = names.types.get(circuit.type_id)
    prefix = prefixes.get(circuit.prefix_id)
    return {
        "id": circuit.id, "cid": circuit.cid, "status": circuit.status,
        "provider": {"id": provider.id, "name": provider.name} if provider else None,
        "type": {"id": kind.id, "name": kind.name} if kind else None,
        "bandwidth": circuit.bandwidth, "install_date": circuit.install_date, "description": circuit.description,
        "a": names.end(ends.get("A")), "z": names.end(ends.get("Z")),
        "interconnect": {"id": prefix.id, "network": prefix.network} if prefix else None,
        "vlan": circuit.vlan, "routing": circuit.routing, "local_as": circuit.local_as, "remote_as": circuit.remote_as,
        "risks": [{"id": r, "name": names.risks[r].name, "kind": names.risks[r].kind} for r in risk_ids if r in names.risks],
        "note": circuit.note, "updated_at": circuit.updated_at, "updated_by": circuit.updated_by,
    }


def circuits_page(actor, query=None, provider_id=None, type_id=None, status=None, site_id=None, offset=0, limit=100,
                  single_point=False):
    from .resilience import Analysis

    with sessions() as session:
        single = Analysis(session).single_points_by_circuit()
        statement = select(Circuit)
        if single_point:
            statement = statement.where(Circuit.id.in_(list(single)))
        if provider_id:
            statement = statement.where(Circuit.provider_id == provider_id)
        if type_id:
            statement = statement.where(Circuit.type_id == type_id)
        if status:
            statement = statement.where(Circuit.status == status)
        if site_id:
            statement = statement.where(Circuit.id.in_(select(Termination.circuit_id).where(Termination.site_id == site_id)))
        text = (query or "").strip()
        if text:
            like = f"%{text}%"
            ends = select(Termination.circuit_id).where(or_(
                Termination.device.ilike(like), Termination.interface.ilike(like), Termination.ip.ilike(like),
                Termination.xconnect.ilike(like),
                Termination.site_id.in_(select(Site.id).where(Site.name.ilike(like))),
                Termination.network_id.in_(select(ProviderNetwork.id).where(ProviderNetwork.name.ilike(like))),
                Termination.pop_id.in_(select(Pop.id).where(Pop.name.ilike(like)))))
            statement = statement.where(or_(
                Circuit.cid.ilike(like), Circuit.description.ilike(like), Circuit.note.ilike(like),
                Circuit.local_as.ilike(like), Circuit.remote_as.ilike(like), Circuit.id.in_(ends),
                Circuit.prefix_id.in_(select(Prefix.id).where(Prefix.network.ilike(like)))))
        total = session.scalar(select(func.count()).select_from(statement.subquery()))
        circuits = session.scalars(statement.order_by(Circuit.cid).offset(offset).limit(limit)).all()
        names = Names(session)
        ids = [c.id for c in circuits]
        ends, risks = ends_of(session, ids), risks_of(session, ids)
        prefixes = {p.id: p for p in session.scalars(select(Prefix).where(
            Prefix.id.in_({c.prefix_id for c in circuits if c.prefix_id}))).all()}
        counts = dict(session.execute(select(Circuit.status, func.count()).group_by(Circuit.status)).all())
        return {
            "can_edit": can_edit(actor), "total": total, "counts": counts, "single_points": len(single),
            "circuits": [{**circuit_row(c, names, ends.get(c.id, {}), risks.get(c.id, []), prefixes),
                          "single_point_for": single.get(c.id, [])} for c in circuits],
        }


def circuit_detail(actor, ident):
    from .resilience import Analysis

    with sessions() as session:
        circuit = session.get(Circuit, ident)
        if not circuit:
            raise missing("专线")
        names = Names(session)
        ends = ends_of(session, [ident]).get(ident, {})
        prefixes = {circuit.prefix_id: session.get(Prefix, circuit.prefix_id)} if circuit.prefix_id else {}
        view = circuit_row(circuit, names, ends, risks_of(session, [ident]).get(ident, []), prefixes)
        provider = names.providers.get(circuit.provider_id)
        view["support"] = {key: getattr(provider, key) for key in ("support_phone", "account", "contact")} if provider else {}
        analysis = Analysis(session)
        view["impact"] = analysis.impact("circuit", ident) if circuit.status == "在用" else None
        view["domains"] = analysis.domains(ident)
        view["can_edit"] = can_edit(actor)
        return view


def interconnect(session, actor, body: CircuitIn, circuit, label):
    """The block the circuit's interconnect addresses come from, or None."""
    if body.carve_from:
        space_id = resolve_space(session, body.space_id)
        try:
            first, _, length, network = netmath.parse_network(body.carve_from)
        except ValueError as exc:
            raise bad(str(exc)) from None
        parent = session.scalars(select(Prefix).where(
            Prefix.space_id == space_id, Prefix.first == first, Prefix.length == length)).first()
        if not parent:
            raise bad(f"网段规划里没有 {network}，请先建好互联地址的网段")
        return add_prefix(session, actor, PrefixIn(
            parent_id=parent.id, length=body.carve_length, label=label[:64], purpose="专线互联", status="使用中"))
    if not body.interconnect:
        return None
    space_id = resolve_space(session, body.space_id)
    try:
        first, _, length, network = netmath.parse_network(body.interconnect)
    except ValueError as exc:
        raise bad(str(exc)) from None
    prefix = session.scalars(select(Prefix).where(
        Prefix.space_id == space_id, Prefix.first == first, Prefix.length == length)).first()
    if not prefix:
        raise bad(f"网段规划里没有 {network}；可以改为从上级网段自动划出")
    if session.scalars(select(Prefix).where(Prefix.space_id == space_id, Prefix.first >= prefix.first,
                                            Prefix.last <= prefix.last, Prefix.length > prefix.length)).first():
        raise bad(f"{network} 已划分下一级，请选最末一级的网段")
    other = session.scalars(select(Circuit).where(Circuit.prefix_id == prefix.id, Circuit.status != RETIRED)).first()
    if other and other is not circuit:
        raise bad(f"{network} 已是专线 {other.cid} 的互联网段")
    return prefix


def release(session, termination):
    if termination.address_id:
        address = session.get(Address, termination.address_id)
        termination.address_id = None
        session.flush()
        if address:
            session.delete(address)
            session.flush()


def place_address(session, actor, circuit, termination, prefix, side, cid):
    """Allocate the end's address in the interconnect block, or keep it as text when there is none."""
    if not termination.ip:
        release(session, termination)
        return
    try:
        value = netmath.ip_value(termination.ip)
    except ValueError as exc:
        raise bad(str(exc)) from None
    termination.ip = netmath.ip_text(value)
    current = session.get(Address, termination.address_id) if termination.address_id else None
    if prefix is None or circuit.status == RETIRED:
        release(session, termination)
        return
    if current and current.value == value and current.space_id == prefix.space_id:
        current.hostname = f"{cid} {side} 端"
        return
    release(session, termination)
    if not prefix.first <= value <= prefix.last:
        raise bad(f"{side} 端地址 {termination.ip} 不在互联网段 {prefix.network} 里")
    index = SpaceIndex(session, prefix.space_id)
    taken = next(iter(index.addresses_in(value, value)), None)
    if taken:
        raise bad(f"{termination.ip} 已分配给 {taken.hostname or taken.kind}")
    check_free(index, prefix, value)
    address = Address(id=new_id(), space_id=prefix.space_id, value=value, ip=termination.ip, kind="专线互联",
                      hostname=f"{cid} {side} 端", assigned_at=now(), assigned_by=actor.username)
    session.add(address)
    session.flush()
    termination.address_id = address.id


def check_end(session, end: TerminationIn, side, provider_id):
    if end.site_id and end.network_id:
        raise bad(f"{side} 端只能接一个站点或一个运营商网络")
    if side == "A" and not end.site_id:
        raise bad("A 端要选本方站点")
    if end.site_id and not session.get(Site, end.site_id):
        raise missing("站点")
    if end.network_id and not session.get(ProviderNetwork, end.network_id):
        raise missing("运营商网络")
    if end.pop_id:
        pop = session.get(Pop, end.pop_id)
        if not pop:
            raise missing("局端")
        if pop.provider_id != provider_id:
            raise bad(f"局端“{pop.name}”不属于这条专线的运营商")


def risk_ids(session, actor, names):
    """Shared risks by name; ones not seen before are created."""
    found = []
    for name in dict.fromkeys(n.strip() for n in names if n and n.strip()):
        risk = session.scalars(select(RiskGroup).where(RiskGroup.name == name[:80])).first()
        if not risk:
            risk = RiskGroup(id=new_id(), name=name[:80], kind="其他")
            session.add(risk)
            session.flush()
            record(session, actor, "共享风险", risk.id, f"共享风险 {risk.name}", "新增")
        found.append(risk.id)
    return found


def write_circuit(session, actor, body: CircuitIn, ident=None):
    """Create or update a circuit inside an open session; returns the circuit and its change entries."""
    if not session.get(Provider, body.provider_id):
        raise missing("运营商")
    if not session.get(CircuitType, body.type_id):
        raise missing("专线类型")
    clash = session.scalars(select(Circuit).where(Circuit.provider_id == body.provider_id, Circuit.cid == body.cid)).first()
    if clash and clash.id != ident:
        raise bad(f"这个运营商已有电路编号 {body.cid}")
    check_end(session, body.a, "A", body.provider_id)
    if body.z:
        check_end(session, body.z, "Z", body.provider_id)
    if body.z and body.z.ip and body.a.ip and body.z.ip == body.a.ip:
        raise bad("A 端和 Z 端地址不能相同")
    values = body.model_dump(include=set(CIRCUIT_NAMES) - {"prefix_id"})
    if ident is None:
        circuit = Circuit(id=new_id(), updated_at=now(), updated_by=actor.username, **values)
        session.add(circuit)
        session.flush()
        changes, action = [], "新增"
    else:
        circuit = session.get(Circuit, ident)
        if not circuit:
            raise missing("专线")
        changes, action = diff(circuit, values, CIRCUIT_NAMES), "修改"
    current = session.get(Prefix, circuit.prefix_id) if circuit.prefix_id else None
    try:
        wanted = netmath.parse_network(body.interconnect)[3] if body.interconnect else None
    except ValueError as exc:
        raise bad(str(exc)) from None
    keep = current and not body.carve_from and wanted == current.network
    prefix = current if keep else interconnect(session, actor, body, circuit, body.cid)
    if (prefix.id if prefix else None) != circuit.prefix_id:
        changes.append({"field": "互联网段", "before": current.network if current else None,
                        "after": prefix.network if prefix else None})
        circuit.prefix_id = prefix.id if prefix else None
    if prefix is not None and body.carve_from:
        # Nobody knows a freshly carved block in advance: ends left blank take its free usable addresses, A first.
        lo, hi = netmath.usable(prefix.first, prefix.last, prefix.length)
        typed = {end.ip.strip() for end in (body.a, body.z) if end is not None and end.ip}
        spare = (text for text in map(netmath.ip_text, range(lo, hi + 1)) if text not in typed)
        for end in (body.a, body.z):
            if end is not None and not end.ip:
                end.ip = next(spare, None)
    ends = ends_of(session, [circuit.id]).get(circuit.id, {})
    for side, end in (("A", body.a), ("Z", body.z)):
        termination = ends.get(side)
        if end is None:
            if termination:
                release(session, termination)
                session.delete(termination)
                changes.append({"field": f"{side} 端", "before": "有", "after": None})
            continue
        if termination is None:
            termination = Termination(id=new_id(), circuit_id=circuit.id, side=side)
            session.add(termination)
        for change in diff(termination, end.model_dump(), END_NAMES):
            changes.append({**change, "field": f"{side} 端{change['field']}"})
        place_address(session, actor, circuit, termination, prefix, side, body.cid)
    wanted = risk_ids(session, actor, body.risks)
    links = session.scalars(select(CircuitRisk).where(CircuitRisk.circuit_id == circuit.id)).all()
    before = sorted(link.risk_id for link in links)
    if before != sorted(wanted):
        for link in links:
            session.delete(link)
        session.flush()
        for risk_id in wanted:
            session.add(CircuitRisk(circuit_id=circuit.id, risk_id=risk_id))
        risks = {r.id: r.name for r in session.scalars(select(RiskGroup)).all()}
        changes.append({"field": "共享风险", "before": "、".join(risks.get(r, r) for r in before) or None,
                        "after": "、".join(risks.get(r, r) for r in wanted) or None})
    if circuit.status == RETIRED and prefix and prefix.status == "使用中":
        # A retired circuit hands its block back for reuse.
        prefix.status = "待回收"
        changes.append({"field": "互联网段状态", "before": "使用中", "after": "待回收"})
    if changes or action == "新增":
        circuit.updated_at, circuit.updated_by = now(), actor.username
    session.flush()
    return circuit, readable(session, changes), action


def readable(session, changes):
    names = Names(session)
    lookups = {"运营商": names.providers, "类型": names.types, "站点": names.sites, "运营商网络": names.networks,
               "局端": names.pops}
    for change in changes:
        field = change["field"]
        key = field[3:] if field[:2] in ("A ", "Z ") else field
        table = lookups.get(key)
        if table:
            for side in ("before", "after"):
                row = table.get(change[side])
                if row:
                    change[side] = row.name
    return changes


def save_circuit(actor, body: CircuitIn, ident=None):
    require_editor(actor)
    with sessions() as session:
        circuit, changes, action = write_circuit(session, actor, body, ident)
        if action == "新增" or changes:
            record(session, actor, "专线", circuit.id, f"专线 {circuit.cid}", action, changes or None)
        return {"id": circuit.id, "cid": circuit.cid}


def delete_circuit(actor, ident):
    require_editor(actor)
    with sessions() as session:
        circuit = session.get(Circuit, ident)
        if not circuit:
            raise missing("专线")
        for termination in session.scalars(select(Termination).where(Termination.circuit_id == ident)).all():
            release(session, termination)
            session.delete(termination)
        for link in session.scalars(select(CircuitRisk).where(CircuitRisk.circuit_id == ident)).all():
            session.delete(link)
        detail = None
        if circuit.prefix_id:
            prefix = session.get(Prefix, circuit.prefix_id)
            if prefix and prefix.status == "使用中":
                prefix.status = "待回收"
                detail = [{"field": "互联网段", "before": f"{prefix.network} 使用中", "after": "待回收"}]
        session.flush()
        session.delete(circuit)
        record(session, actor, "专线", circuit.id, f"专线 {circuit.cid}", "删除", detail)
    return {"deleted": True}


def options(actor):
    """What the circuit form chooses from."""
    with sessions() as session:
        names = Names(session)
        return {
            "can_edit": can_edit(actor),
            "providers": [{"id": p.id, "name": p.name} for p in sorted(names.providers.values(), key=lambda p: p.name)],
            "networks": [{"id": n.id, "name": n.name, "provider_id": n.provider_id, "kind": n.kind}
                         for n in sorted(names.networks.values(), key=lambda n: n.name)],
            "pops": [{"id": o.id, "name": o.name, "provider_id": o.provider_id}
                     for o in sorted(names.pops.values(), key=lambda o: o.name)],
            "types": [{"id": t.id, "name": t.name} for t in sorted(names.types.values(), key=lambda t: t.name)],
            "risks": [{"id": r.id, "name": r.name, "kind": r.kind} for r in sorted(names.risks.values(), key=lambda r: r.name)],
            "sites": [{"id": s.id, "name": s.name, "kind": s.kind, "core": s.core,
                       "region": names.regions[s.region_id].name if s.region_id in names.regions else None}
                      for s in sorted(names.sites.values(), key=lambda s: s.name)],
        }
