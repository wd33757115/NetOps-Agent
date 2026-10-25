"""Sites and carrier circuits in the ledger workbook: their sheets, import rows and export.

The rows go through the same checks as the pages: a circuit row is written by the circuit
service itself, so its ends, points of presence and interconnect addresses follow the same
rules. Providers, circuit types, points of presence and shared risks a row names are
created when missing and listed in the report; sites and networks must already exist.
"""

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select

from . import circuits
from .common import diff, new_id
from .models import (
    Circuit,
    CircuitType,
    Pop,
    Prefix,
    Provider,
    ProviderNetwork,
    Region,
    RiskGroup,
    Site,
    Space,
)
from .schemas import (
    CIRCUIT_STATES,
    NETWORK_KINDS,
    RISK_KINDS,
    ROUTING,
    SITE_KINDS,
    CircuitIn,
    TerminationIn,
)

END = ["站点", "设备", "接口", "端口速率(Mbps)", "配线", "局端", "地址"]
SITE_SHEET = {"站点": ["地域", "站点", "类型", "核心站点", "地址", "备注"]}
SHEETS = {
    "运营商": ["运营商", "报障电话", "客户编号", "客户经理", "备注"],
    "运营商网络": ["运营商", "名称", "类型", "服务标识", "站点间互通", "备注"],
    "局端": ["运营商", "局端", "备注"],
    "专线类型": ["名称", "备注"],
    "共享风险": ["名称", "类型", "备注"],
    "专线": ["运营商", "电路编号", "类型", "状态", "带宽(Mbps)", "开通日期", "说明",
           *(f"A端{c}" for c in END), "Z端站点", "Z端运营商网络", *(f"Z端{c}" for c in END[1:]),
           "互联网段", "VLAN", "路由方式", "本端AS", "对端AS", "共享风险", "备注", "路由域"],
}
REQUIRED = {
    "站点": ["地域", "站点"], "运营商": ["运营商"], "运营商网络": ["运营商", "名称"], "局端": ["运营商", "局端"],
    "专线类型": ["名称"], "共享风险": ["名称"], "专线": ["运营商", "电路编号", "类型", "A端站点"],
}
EXAMPLES = {
    "站点": [["西安", "西安机房", "自建", "是", "", ""],
           ["西安", "咸阳灾备中心", "托管", "否", "", ""]],
    "运营商": [["中国电信", "10000 转 5", "BJ-ENT-6620193", "王工", ""],
            ["中国联通", "10010 转 2", "", "", ""]],
    "运营商网络": [["中国电信", "电信 CN2 华北", "MPLS VPN", "VPN-ORG-0031", "是", ""]],
    "局端": [["中国电信", "西安高新局 PE-XA-03", ""]],
    "专线类型": [["MPLS VPN", "运营商 MPLS 网络接入"]],
    "共享风险": [["西安机房入户光缆 A", "光缆", "东侧管井"]],
    "专线": [["中国电信", "CN2-0098812", "MPLS VPN", "在用", 500, "2024-06-18", "",
            "西安机房", "XA-WAN-R1", "GE0/0/3", 1000, "ODF-02 第 14 口", "西安高新局 PE-XA-03", "",
            "", "电信 CN2 华北", "", "", "", "", "", "",
            "", 3001, "BGP", "65001", "4809", "西安机房入户光缆 A", "", ""],
           ["中国联通", "XA-XY-MSTP-02", "MSTP/SDH", "在用", 1000, "", "",
            "西安机房", "XA-WAN-R4", "GE0/0/5", "", "", "", "",
            "咸阳灾备中心", "", "XY-WAN-R1", "GE0/0/1", "", "", "", "",
            "", "", "静态", "", "", "", "", ""]],
}
NOTES = [
    "站点：核心站点填“是”或“否”；类型可填：" + "、".join(SITE_KINDS) + "。"
    "地域不存在时会新建。",
    "运营商、局端、专线类型、共享风险不存在时会新建；站点和运营商网络要先在本表或台账里建好。",
    "运营商网络：类型可填：" + "、".join(NETWORK_KINDS) + "；站点间互通填“是”表示站点之间能经它互访（MPLS VPN，或部署了 SD-WAN 的互联网）。",
    "共享风险：类型可填：" + "、".join(RISK_KINDS) + "。同一条光缆、管道、入户等写同一个名称，系统据此判断主备是否共路由。",
    "专线：电路编号在同一运营商内唯一，已存在的按本表更新。A 端是本方站点；Z 端填站点或运营商网络（只有一端的线路两栏都留空）。"
    "状态可填：" + "、".join(CIRCUIT_STATES) + "；路由方式可填：" + "、".join(ROUTING) + "。",
    "专线：互联网段填网段规划里已有的最末一级网段（如 10.255.0.4/30），两端地址会在其中分配；共享风险多个用顿号分隔。"
    "运营商网络重名时写成“运营商/网络名称”。",
]
YES, NO = {"是", "y", "yes", "true", "1", "√"}, {"否", "n", "no", "false", "0"}


def flag(row, column, default):
    value = (row.get(column) or "").strip().lower()
    if not value:
        return default
    if value in YES:
        return True
    if value in NO:
        return False
    raise ValueError(f"{column}“{row[column]}”无效，请填“是”或“否”")


def integer(row, column, low=0, high=10_000_000):
    value = row.get(column)
    if not value:
        return None
    if not str(value).isdigit() or not low <= int(value) <= high:
        raise ValueError(f"{column}“{value}”无效，应为 {low}–{high} 的整数")
    return int(value)


def pick(row, column, options, default):
    value = row.get(column) or default
    if value is not None and value not in options:
        raise ValueError(f"{column}“{value}”无效，可填：{'、'.join(options)}")
    return value


def readable(error: ValidationError):
    first = error.errors()[0]
    where = ".".join(str(part) for part in first.get("loc", ()))
    return f"字段 {where} 格式不对：{first.get('msg', '')}"


class CircuitRows:
    """Import rows for sites and circuits; mixed into the ledger import run."""

    def provider(self, name):
        return self.first(select(Provider).where(Provider.name == name)) or self.made(
            "运营商", Provider(id=new_id(), name=name), name, f"运营商 {name}")

    def circuit_type(self, name):
        return self.first(select(CircuitType).where(CircuitType.name == name)) or self.made(
            "专线类型", CircuitType(id=new_id(), name=name), name, f"专线类型 {name}")

    def pop(self, provider, name):
        return self.first(select(Pop).where(Pop.provider_id == provider.id, Pop.name == name)) or self.made(
            "局端", Pop(id=new_id(), provider_id=provider.id, name=name), f"{provider.name} / {name}", f"局端 {name}")

    def existing_site(self, name):
        site = self.first(select(Site).where(Site.name == name))
        if not site:
            raise ValueError(f"站点“{name}”不存在，请在“站点”表里填写")
        return site

    def network_named(self, value):
        provider, _, name = value.rpartition("/")
        statement = select(ProviderNetwork).where(ProviderNetwork.name == (name if provider else value))
        found = self.session.scalars(statement).all()
        if provider:
            found = [n for n in found if self.session.get(Provider, n.provider_id).name == provider]
        if not found:
            raise ValueError(f"运营商网络“{value}”不存在，请在“运营商网络”表里填写")
        if len(found) > 1:
            raise ValueError(f"有多个运营商都有网络“{value}”，请写成“运营商/网络名称”")
        return found[0]

    def simple(self, sheet, number, label, model, existing, values, names, entity):
        if existing:
            changes = diff(existing, values, names)
            if not changes:
                return self.result(sheet, number, label, "无变化")
            self.log(entity, existing.id, f"{entity} {label}", "导入", changes)
            return self.result(sheet, number, label, "更新", changes=changes)
        row = model(id=new_id(), **values)
        self.session.add(row)
        self.session.flush()
        self.log(entity, row.id, f"{entity} {label}", "导入")
        self.result(sheet, number, label, "新增")

    def site_row(self, number, row):
        label = row["站点"]
        try:
            values = {"kind": pick(row, "类型", SITE_KINDS, "自建"), "core": flag(row, "核心站点", False),
                      "address": row.get("地址") or None, "note": row.get("备注") or None}
            site = self.first(select(Site).where(Site.name == label))
            if site:
                region = self.session.get(Region, site.region_id)
                if region.name != row["地域"]:
                    raise ValueError(f"站点“{label}”属于地域“{region.name}”，与填写的“{row['地域']}”不一致")
            else:
                values.update(region_id=self.region(row["地域"]).id, name=label)
        except ValueError as exc:
            return self.result("站点", number, label, "错误", str(exc))
        from .service import SITE_NAMES

        self.simple("站点", number, label, Site, site, values, SITE_NAMES, "机房")

    def provider_row(self, number, row):
        label = row["运营商"]
        values = {"support_phone": row.get("报障电话") or None, "account": row.get("客户编号") or None,
                  "contact": row.get("客户经理") or None, "note": row.get("备注") or None}
        existing = self.first(select(Provider).where(Provider.name == label))
        self.simple("运营商", number, label, Provider, existing, {**values, **({} if existing else {"name": label})},
                    circuits.PROVIDER_NAMES, "运营商")

    def network_row(self, number, row):
        label = f"{row['运营商']} / {row['名称']}"
        try:
            values = {"kind": pick(row, "类型", NETWORK_KINDS, "MPLS VPN"), "service_id": row.get("服务标识") or None,
                      "interconnects": flag(row, "站点间互通", True), "note": row.get("备注") or None}
        except ValueError as exc:
            return self.result("运营商网络", number, label, "错误", str(exc))
        provider = self.provider(row["运营商"])
        existing = self.first(select(ProviderNetwork).where(
            ProviderNetwork.provider_id == provider.id, ProviderNetwork.name == row["名称"]))
        self.simple("运营商网络", number, label, ProviderNetwork, existing,
                    {**values, **({} if existing else {"provider_id": provider.id, "name": row["名称"]})},
                    circuits.NETWORK_NAMES, "运营商网络")

    def pop_row(self, number, row):
        label = f"{row['运营商']} / {row['局端']}"
        provider = self.provider(row["运营商"])
        existing = self.first(select(Pop).where(Pop.provider_id == provider.id, Pop.name == row["局端"]))
        self.simple("局端", number, label, Pop, existing,
                    {"note": row.get("备注") or None, **({} if existing else {"provider_id": provider.id, "name": row["局端"]})},
                    {"note": "备注"}, "局端")

    def type_row(self, number, row):
        label = row["名称"]
        existing = self.first(select(CircuitType).where(CircuitType.name == label))
        self.simple("专线类型", number, label, CircuitType, existing,
                    {"note": row.get("备注") or None, **({} if existing else {"name": label})}, {"note": "备注"}, "专线类型")

    def risk_row(self, number, row):
        label = row["名称"]
        try:
            values = {"kind": pick(row, "类型", RISK_KINDS, "光缆"), "note": row.get("备注") or None}
        except ValueError as exc:
            return self.result("共享风险", number, label, "错误", str(exc))
        existing = self.first(select(RiskGroup).where(RiskGroup.name == label))
        self.simple("共享风险", number, label, RiskGroup, existing, {**values, **({} if existing else {"name": label})},
                    {"kind": "类型", "note": "备注"}, "共享风险")

    def end(self, row, side, provider):
        site = row.get(f"{side}端站点")
        network = row.get("Z端运营商网络") if side == "Z" else None
        rest = [row.get(f"{side}端{c}") for c in END[1:]]
        if not site and not network and not any(rest):
            return None
        if site and network:
            raise ValueError("Z 端只能填站点或运营商网络中的一个")
        pop = row.get(f"{side}端局端")
        return TerminationIn(
            site_id=self.existing_site(site).id if site else None,
            network_id=self.network_named(network).id if network else None,
            device=row.get(f"{side}端设备") or None, interface=row.get(f"{side}端接口") or None,
            port_speed=integer(row, f"{side}端端口速率(Mbps)", 1), xconnect=row.get(f"{side}端配线") or None,
            pop_id=self.pop(provider, pop).id if pop else None, ip=row.get(f"{side}端地址") or None)

    def circuit_row(self, number, row):
        label = row["电路编号"]
        try:
            provider = self.provider(row["运营商"])
            space = self.space(row.get("路由域")) if row.get("路由域") else None
            body = CircuitIn(
                provider_id=provider.id, cid=label, type_id=self.circuit_type(row["类型"]).id,
                status=pick(row, "状态", CIRCUIT_STATES, "在用"), bandwidth=integer(row, "带宽(Mbps)"),
                install_date=row.get("开通日期") or None, description=row.get("说明") or None,
                a=self.end(row, "A", provider), z=self.end(row, "Z", provider),
                space_id=space.id if space else None, interconnect=row.get("互联网段") or None,
                vlan=integer(row, "VLAN", 1, 4094), routing=pick(row, "路由方式", ROUTING, None),
                local_as=row.get("本端AS") or None, remote_as=row.get("对端AS") or None,
                risks=[part for part in row.get("共享风险", "").replace("，", "、").split("、") if part.strip()],
                note=row.get("备注") or None)
        except ValidationError as exc:
            return self.result("专线", number, label, "错误", readable(exc))
        except ValueError as exc:
            return self.result("专线", number, label, "错误", str(exc))
        existing = self.first(select(Circuit).where(Circuit.provider_id == provider.id, Circuit.cid == label))
        try:
            circuit, changes, action = circuits.write_circuit(self.session, self.actor, body, existing.id if existing else None)
        except HTTPException as exc:
            return self.result("专线", number, label, "错误", exc.detail)
        if existing and not changes:
            return self.result("专线", number, label, "无变化")
        self.log("专线", circuit.id, f"专线 {label}", "导入", changes or None)
        self.result("专线", number, label, "更新" if existing else "新增", changes=changes if existing else [])


def export_rows(session, several):
    """Rows of every site and circuit sheet, in the import layout."""
    names = circuits.Names(session)
    rows = {name: [] for name in [*SITE_SHEET, *SHEETS]}
    yes = lambda value: "是" if value else "否"
    for site in sorted(names.sites.values(), key=lambda s: s.name):
        region = names.regions.get(site.region_id)
        rows["站点"].append([region.name if region else None, site.name, site.kind, yes(site.core), site.address, site.note])
    for provider in sorted(names.providers.values(), key=lambda p: p.name):
        rows["运营商"].append([provider.name, provider.support_phone, provider.account, provider.contact, provider.note])
    counts = {}
    for network in names.networks.values():
        counts[network.name] = counts.get(network.name, 0) + 1
    network_label = {n.id: (f"{names.providers[n.provider_id].name}/{n.name}" if counts[n.name] > 1 else n.name)
                     for n in names.networks.values()}
    for network in sorted(names.networks.values(), key=lambda n: (names.providers[n.provider_id].name, n.name)):
        rows["运营商网络"].append([names.providers[network.provider_id].name, network.name, network.kind,
                              network.service_id, yes(network.interconnects), network.note])
    for pop in sorted(names.pops.values(), key=lambda o: (names.providers[o.provider_id].name, o.name)):
        rows["局端"].append([names.providers[pop.provider_id].name, pop.name, pop.note])
    for kind in sorted(names.types.values(), key=lambda t: t.name):
        rows["专线类型"].append([kind.name, kind.note])
    for risk in sorted(names.risks.values(), key=lambda r: r.name):
        rows["共享风险"].append([risk.name, risk.kind, risk.note])
    all_circuits = session.scalars(select(Circuit).order_by(Circuit.cid)).all()
    ids = [c.id for c in all_circuits]
    ends, risks = circuits.ends_of(session, ids), circuits.risks_of(session, ids)
    prefixes = {p.id: p for p in session.scalars(select(Prefix).where(
        Prefix.id.in_({c.prefix_id for c in all_circuits if c.prefix_id}))).all()}
    spaces = {s.id: s.name for s in session.scalars(select(Space)).all()}

    def end(termination, with_network):
        if termination is None:
            return [None] * (len(END) + (1 if with_network else 0))
        site = names.sites.get(termination.site_id)
        pop = names.pops.get(termination.pop_id)
        place = [site.name if site else None]
        if with_network:
            place.append(network_label.get(termination.network_id))
        return [*place, termination.device, termination.interface, termination.port_speed, termination.xconnect,
                pop.name if pop else None, termination.ip]

    for circuit in all_circuits:
        prefix = prefixes.get(circuit.prefix_id)
        a, z = ends.get(circuit.id, {}).get("A"), ends.get(circuit.id, {}).get("Z")
        rows["专线"].append([
            names.providers[circuit.provider_id].name, circuit.cid, names.types[circuit.type_id].name, circuit.status,
            circuit.bandwidth, circuit.install_date, circuit.description, *end(a, False), *end(z, True),
            prefix.network if prefix else None, circuit.vlan, circuit.routing, circuit.local_as, circuit.remote_as,
            "、".join(names.risks[r].name for r in risks.get(circuit.id, []) if r in names.risks), circuit.note,
            spaces.get(prefix.space_id) if prefix and several else None,
        ])
    return rows

