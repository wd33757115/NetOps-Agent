"""Carrier circuits in the resource ledger: records, interconnect addresses, resilience and impact."""
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine

from src.apps.resource_ledger.backend import circuits, resilience, service
from src.apps.resource_ledger.backend.schemas import (
    CircuitIn,
    CircuitTypeIn,
    NetworkIn,
    PopIn,
    PrefixIn,
    ProviderIn,
    RegionIn,
    RiskIn,
    SiteIn,
    TerminationIn,
)
from src.auth.models import CurrentUser
from src.platform.kernel.migrations import migrate
from src.platform.kernel.registry import discover

ADMIN = CurrentUser(user_id="1", username="admin", role="admin")
VIEWER = CurrentUser(user_id="3", username="viewer", role="viewer")


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///" + str(tmp_path / "ledger.sqlite3"))
    assert migrate(discover()["resource-ledger"], engine) == "resource_ledger_0003"
    monkeypatch.setattr("src.platform.kernel.migrations.business_engine", lambda: engine)
    yield engine
    engine.dispose()


def world():
    """The sites, providers and circuits of the design mockup."""
    xian = service.save_region(ADMIN, RegionIn(name="西安"))["id"]
    north = service.save_region(ADMIN, RegionIn(name="北京"))["id"]
    south = service.save_region(ADMIN, RegionIn(name="华南"))["id"]
    site = lambda name, region, **kw: service.save_site(ADMIN, SiteIn(region_id=region, name=name, **kw))["id"]
    s = {
        "xa": site("西安机房", xian, core=True),
        "bj": site("北京亦庄机房", north, core=True),
        "cd": site("成都门店012", south, kind="门店"),
        "sh": site("上海分公司", north, kind="分支"),
        "gz": site("广州门店031", south, kind="门店"),
        "xy": site("咸阳灾备中心", xian, kind="托管"),
    }
    provider = lambda name, phone: circuits.save_provider(ADMIN, ProviderIn(name=name, support_phone=phone))["id"]
    p = {"lt": provider("中国联通", "10010"), "dx": provider("中国电信", "10000"), "yd": provider("中国移动", "10086"),
         "cy": provider("城域网", None), "net": provider("公共互联网", None)}
    network = lambda pid, name, kind="MPLS VPN": circuits.save_network(ADMIN, NetworkIn(provider_id=pid, name=name, kind=kind))["id"]
    n = {"cn2": network(p["dx"], "电信 CN2 华北"), "ydm": network(p["yd"], "移动 MPLS 西南"),
         "inet": network(p["net"], "互联网 SD-WAN", "互联网")}
    pop = circuits.save_pop(ADMIN, PopIn(provider_id=p["dx"], name="西安高新局 PE-XA-03"))["id"]
    types = {t["name"]: t["id"] for t in circuits.types_page(ADMIN)["types"]}
    c = {}

    def line(cid, pid, kind, a_site, a_device, z_site=None, z_net=None, risks=(), a_pop=None):
        c[cid] = circuits.save_circuit(ADMIN, CircuitIn(
            provider_id=p[pid], cid=cid, type_id=types[kind], bandwidth=100,
            a=TerminationIn(site_id=s[a_site], device=a_device, pop_id=a_pop),
            z=TerminationIn(site_id=s[z_site] if z_site else None, network_id=n[z_net] if z_net else None),
            risks=list(risks)))["id"]

    line("XA-BJ-MSTP-0001", "lt", "MSTP/SDH", "xa", "XA-WAN-R1", z_site="bj", risks=["西安机房入户光缆 A"])
    line("CN2-0098812", "dx", "MPLS VPN", "xa", "XA-WAN-R1", z_net="cn2", risks=["西安机房入户光缆 A"], a_pop=pop)
    line("CN2-0098813", "dx", "MPLS VPN", "bj", "BJ-WAN-R1", z_net="cn2")
    line("XA-YD-0001", "yd", "MPLS VPN", "xa", "XA-WAN-R2", z_net="ydm", risks=["西安机房入户光缆 A"])
    line("CD-MPLS-2207", "yd", "MPLS VPN", "cd", "CD-R1", z_net="ydm")
    line("CD-4G-0012", "yd", "4G/5G 备份", "cd", "CD-4G", z_net="ydm")
    line("SH-MPLS-1101", "dx", "MPLS VPN", "sh", "SH-R1", z_net="cn2")
    line("SH-INET-3301", "dx", "互联网专线", "sh", "SH-R2", z_net="inet")
    line("XA-INET-0001", "lt", "互联网专线", "xa", "XA-INET-R1", z_net="inet", risks=["西安机房入户光缆 B"])
    line("GZ-INET-5508", "lt", "互联网专线", "gz", "GZ-R1", z_net="inet")
    line("XA-XY-DF-01", "cy", "裸光纤/波分", "xa", "XA-WAN-R3", z_site="xy")
    line("XA-XY-MSTP-02", "lt", "MSTP/SDH", "xa", "XA-WAN-R4", z_site="xy")
    return s, p, n, c, types


def test_resilience_finds_single_points_from_recorded_facts(ledger):
    world()
    report = resilience.resilience_page(ADMIN)
    rows = {row["site"]["name"]: row for row in report["sites"]}
    assert {c["name"] for c in report["core"]} == {"西安机房", "北京亦庄机房"}
    # Facts only: whether a site reaches the core, and what single points it has; no pass or fail.
    states = {name: row["state_name"] for name, row in rows.items()}
    assert states == {"西安机房": "有单点", "北京亦庄机房": "有单点", "成都门店012": "有单点",
                      "上海分公司": "有单点", "广州门店031": "有单点", "咸阳灾备中心": "没有发现单点"}
    assert report["counts"] == {"single": 5, "clear": 1} and report["all"] == report["total"] == 6
    assert "verdict" not in rows["西安机房"] and "wide" not in rows["上海分公司"]["single_points"][0]
    beijing = {(item["kind"], item["name"]) for item in rows["北京亦庄机房"]["single_points"]}
    assert beijing == {("device", "西安机房 XA-WAN-R1"), ("risk", "西安机房入户光缆 A")}
    chengdu = {(item["kind"], item["name"]) for item in rows["成都门店012"]["single_points"]}
    assert {("provider", "中国移动"), ("network", "移动 MPLS 西南"), ("circuit", "XA-YD-0001"),
            ("device", "西安机房 XA-WAN-R2"), ("risk", "西安机房入户光缆 A")} <= chengdu
    assert [(i["kind"], i["name"]) for i in rows["上海分公司"]["single_points"]] == [("provider", "中国电信")]
    # Paths read as hops: circuit, then where it leads.
    direct = rows["咸阳灾备中心"]["paths"][0]
    assert [hop.get("cid") or hop.get("name") for hop in direct] == ["XA-XY-DF-01", "西安机房"]
    via = [hop.get("cid") or hop.get("name") for hop in rows["成都门店012"]["paths"][0]]
    assert via == ["CD-4G-0012", "移动 MPLS 西南", "XA-YD-0001", "西安机房"]
    # A circuit that alone cuts sites off is flagged in the list.
    listed = {c["cid"]: c for c in circuits.circuits_page(ADMIN)["circuits"]}
    assert listed["XA-YD-0001"]["single_point_for"] == ["成都门店012"]
    assert listed["XA-XY-DF-01"]["single_point_for"] == []
    # Single-point circuits filter and page on the server.
    only = circuits.circuits_page(ADMIN, single_point=True, limit=2)
    singles = {cid for cid, c in listed.items() if c["single_point_for"]}
    assert only["total"] == only["single_points"] == len(singles) and len(only["circuits"]) == 2
    assert {c["cid"] for c in only["circuits"]} <= singles
    # The report pages too; counts follow the search but not the state picked.
    page = resilience.resilience_page(ADMIN, state="single", offset=1, limit=2)
    assert page["total"] == 5 and len(page["sites"]) == 2 and page["counts"] == {"single": 5, "clear": 1}
    shop = resilience.resilience_page(ADMIN, query="门店")
    assert {r["site"]["name"] for r in shop["sites"]} == {"成都门店012", "广州门店031"} and shop["all"] == 2


def test_impact_of_one_shared_cable(ledger):
    world()
    risk = next(r for r in circuits.risks_page(ADMIN)["risks"] if r["name"] == "西安机房入户光缆 A")
    page = resilience.impact_page(ADMIN, "risk", risk["id"])
    assert [o["name"] for o in page["options"]["risk"]] == ["西安机房入户光缆 A", "西安机房入户光缆 B"]
    impact = page["impact"]
    assert [c["cid"] for c in impact["circuits"]] == ["CN2-0098812", "XA-BJ-MSTP-0001", "XA-YD-0001"]
    assert {row["site"]["name"]: row["state"] for row in impact["sites"]} == {
        "北京亦庄机房": "down", "西安机房": "down", "成都门店012": "down"}
    # Shanghai still reaches Beijing over CN2 and Xi'an over the internet overlay.
    assert impact["counts"]["fine"] == 3
    hit = [hop for path in impact["sites"][0]["paths"] for hop in path if hop.get("hit")]
    assert hit and all(hop["kind"] == "circuit" for hop in hit)
    line = circuits.circuits_page(ADMIN, query="SH-MPLS")["circuits"][0]
    states = {row["site"]["name"]: row["state"] for row in resilience.impact_page(ADMIN, "circuit", line["id"])["impact"]["sites"]}
    assert states == {"上海分公司": "single"}
    # Both of Shanghai's lines are China Telecom's: losing the provider cuts it off.
    telecom = resilience.impact_page(ADMIN, "provider", line["provider"]["id"])["impact"]
    assert {row["site"]["name"]: row["state"] for row in telecom["sites"]}["上海分公司"] == "down"


def test_circuit_detail_shows_support_domains_and_impact(ledger):
    s, p, n, c, _ = world()
    detail = circuits.circuit_detail(ADMIN, c["CN2-0098812"])
    assert detail["support"]["support_phone"] == "10000"
    assert detail["a"]["place"]["name"] == "西安机房" and detail["a"]["pop"]["name"] == "西安高新局 PE-XA-03"
    assert detail["z"]["place"] == {"kind": "network", "id": n["cn2"], "name": "电信 CN2 华北",
                                    "detail": "中国电信 · MPLS VPN"}
    domains = {d["kind"]: d for d in detail["domains"]}
    assert domains["device"]["shared_with"] == ["XA-BJ-MSTP-0001"]
    assert domains["risk"]["shared_with"] == ["XA-BJ-MSTP-0001", "XA-YD-0001"] and not domains["risk"]["auto"]
    # Losing it leaves Beijing and Xi'an one line between them; Shanghai keeps two ways in.
    assert {row["site"]["name"]: row["state"] for row in detail["impact"]["sites"]} == {
        "北京亦庄机房": "single", "西安机房": "single"}
    found = circuits.circuits_page(ADMIN, query="XA-WAN-R1")
    assert {row["cid"] for row in found["circuits"]} == {"XA-BJ-MSTP-0001", "CN2-0098812"}
    assert circuits.circuits_page(ADMIN, site_id=s["cd"])["total"] == 2


def test_interconnect_block_and_addresses_follow_the_circuit(ledger):
    s, p, n, c, types = world()
    service.create_prefix(ADMIN, PrefixIn(network="10.255.0.0/16", label="互联地址"))
    body = dict(provider_id=p["dx"], cid="CN2-TEST-1", type_id=types["MPLS VPN"],
                a=TerminationIn(site_id=s["xa"], device="XA-WAN-R9", ip="10.255.0.1"),
                z=TerminationIn(network_id=n["cn2"], ip="10.255.0.2"), carve_from="10.255.0.0/16", vlan=3001,
                routing="BGP", local_as="65001", remote_as="4809")
    created = circuits.save_circuit(ADMIN, CircuitIn(**body))
    detail = circuits.circuit_detail(ADMIN, created["id"])
    assert detail["interconnect"]["network"] == "10.255.0.0/30"
    assert detail["a"]["allocated"] and detail["z"]["allocated"]
    hit = service.search(ADMIN, "10.255.0.2")["results"][0]
    assert hit["address"]["circuit"] == {"id": created["id"], "cid": "CN2-TEST-1", "side": "Z"}
    with pytest.raises(HTTPException, match="是专线 CN2-TEST-1 的互联地址"):
        service.release_address(ADMIN, hit["address"]["id"])
    with pytest.raises(HTTPException, match="是专线 CN2-TEST-1 的互联网段"):
        service.delete_prefix(ADMIN, detail["interconnect"]["id"])
    with pytest.raises(HTTPException, match="不在互联网段 10.255.0.0/30 里"):
        circuits.save_circuit(ADMIN, CircuitIn(**{**body, "interconnect": "10.255.0.0/30", "carve_from": None,
                                                  "a": TerminationIn(site_id=s["xa"], ip="10.255.9.1")}), created["id"])
    # Another circuit cannot take the same block.
    with pytest.raises(HTTPException, match="已是专线 CN2-TEST-1 的互联网段"):
        circuits.save_circuit(ADMIN, CircuitIn(**{**body, "cid": "CN2-TEST-2", "carve_from": None,
                                                  "interconnect": "10.255.0.0/30"}))
    with pytest.raises(HTTPException, match="可用地址范围"):
        circuits.save_circuit(ADMIN, CircuitIn(**{**body, "carve_from": None, "interconnect": "10.255.0.0/30",
                                                  "z": TerminationIn(network_id=n["cn2"], ip="10.255.0.3")}), created["id"])
    # Clearing an end's address frees it.
    circuits.save_circuit(ADMIN, CircuitIn(**{**body, "carve_from": None, "interconnect": "10.255.0.0/30",
                                              "a": TerminationIn(site_id=s["xa"], device="XA-WAN-R9")}), created["id"])
    assert service.search(ADMIN, "10.255.0.1")["results"][0]["address"] is None
    assert service.search(ADMIN, "10.255.0.2")["results"][0]["address"]["circuit"]["side"] == "Z"
    # Retiring releases both addresses and hands the block back for reuse.
    circuits.save_circuit(ADMIN, CircuitIn(**{**body, "status": "已拆除", "carve_from": None,
                                              "interconnect": "10.255.0.0/30"}), created["id"])
    block = service.prefix_detail(ADMIN, detail["interconnect"]["id"])
    assert block["status"] == "待回收" and block["addresses"] == []
    log = service.changes(ADMIN, entity="专线")["items"][0]
    assert any(change["field"] == "互联网段状态" for change in log["detail"])
    circuits.delete_circuit(ADMIN, created["id"])
    service.delete_prefix(ADMIN, detail["interconnect"]["id"])
    # A carved block fills the ends left blank with its free usable addresses, A before Z.
    blank = circuits.save_circuit(ADMIN, CircuitIn(**{**body, "cid": "CN2-TEST-3", "a": TerminationIn(site_id=s["xa"]),
                                                      "z": TerminationIn(network_id=n["cn2"])}))
    view = circuits.circuit_detail(ADMIN, blank["id"])
    assert (view["interconnect"]["network"], view["a"]["ip"], view["z"]["ip"]) == ("10.255.0.0/30", "10.255.0.1", "10.255.0.2")
    typed = circuits.save_circuit(ADMIN, CircuitIn(**{**body, "cid": "CN2-TEST-4", "carve_length": 31,
                                                      "a": TerminationIn(site_id=s["xa"]),
                                                      "z": TerminationIn(network_id=n["cn2"], ip="10.255.0.4")}))
    view = circuits.circuit_detail(ADMIN, typed["id"])
    assert (view["interconnect"]["network"], view["a"]["ip"], view["z"]["ip"]) == ("10.255.0.4/31", "10.255.0.5", "10.255.0.4")


def test_records_guard_what_circuits_use(ledger):
    s, p, n, c, types = world()
    with pytest.raises(HTTPException, match="还有专线"):
        circuits.delete_provider(ADMIN, p["yd"])
    with pytest.raises(HTTPException, match="还有专线接入"):
        circuits.delete_network(ADMIN, n["cn2"])
    with pytest.raises(HTTPException, match="还有专线是这个类型"):
        circuits.delete_type(ADMIN, types["MPLS VPN"])
    with pytest.raises(HTTPException, match="还有专线接在这个站点上"):
        service.delete_site(ADMIN, s["gz"])
    with pytest.raises(HTTPException, match="不属于这条专线的运营商"):
        pop = circuits.providers_page(ADMIN)["providers"]
        dx_pop = next(pr for pr in pop if pr["name"] == "中国电信")["pops"][0]["id"]
        circuits.save_circuit(ADMIN, CircuitIn(provider_id=p["lt"], cid="X1", type_id=types["其他"],
                                               a=TerminationIn(site_id=s["xa"], pop_id=dx_pop)))
    with pytest.raises(HTTPException, match="不能改到别的运营商"):
        circuits.save_pop(ADMIN, PopIn(provider_id=p["lt"], name="西安高新局 PE-XA-03"), dx_pop)
    with pytest.raises(HTTPException, match="A 端要选本方站点"):
        circuits.save_circuit(ADMIN, CircuitIn(provider_id=p["lt"], cid="X2", type_id=types["其他"],
                                               a=TerminationIn(network_id=n["inet"])))
    with pytest.raises(HTTPException, match="已有电路编号 GZ-INET-5508"):
        circuits.save_circuit(ADMIN, CircuitIn(provider_id=p["lt"], cid="GZ-INET-5508", type_id=types["其他"],
                                               a=TerminationIn(site_id=s["gz"])))
    # Deleting a shared risk takes it off its circuits.
    risk = next(r for r in circuits.risks_page(ADMIN)["risks"] if r["name"] == "西安机房入户光缆 A")
    assert circuits.delete_risk(ADMIN, risk["id"])["unlinked"] == 3
    assert not circuits.circuit_detail(ADMIN, c["CN2-0098812"])["risks"]
    # Simple records round-trip through create, update and delete.
    kind = circuits.save_type(ADMIN, CircuitTypeIn(name="卫星"))["id"]
    circuits.save_type(ADMIN, CircuitTypeIn(name="卫星链路"), kind)
    assert "卫星链路" in [t["name"] for t in circuits.types_page(ADMIN)["types"]]
    circuits.delete_type(ADMIN, kind)
    cable = circuits.save_risk(ADMIN, RiskIn(name="跨江管道", kind="管道"))["id"]
    circuits.delete_risk(ADMIN, cable)
    spare = circuits.save_provider(ADMIN, ProviderIn(name="临时运营商"))["id"]
    circuits.save_network(ADMIN, NetworkIn(provider_id=spare, name="临时网络"))
    circuits.delete_provider(ADMIN, spare)
    with pytest.raises(HTTPException) as denied:
        circuits.save_provider(VIEWER, ProviderIn(name="x"))
    assert denied.value.status_code == 403
    gone = circuits.delete_circuit(ADMIN, c["GZ-INET-5508"])
    assert gone == {"deleted": True}
    service.delete_site(ADMIN, s["gz"])
    # The change log pages with a total: six sites made, one deleted.
    log = service.changes(ADMIN, entity="站点", limit=5)
    assert log["total"] == 7 and len(log["items"]) == 5 and log["more"]


def test_circuits_export_and_import_back_unchanged(ledger):
    from src.apps.resource_ledger.backend import importer

    s, p, n, c, types = world()
    service.create_prefix(ADMIN, PrefixIn(network="10.255.0.0/16", label="互联地址"))
    circuits.save_circuit(ADMIN, CircuitIn(
        provider_id=p["dx"], cid="CN2-TEST-9", type_id=types["MPLS VPN"], status="在用", bandwidth=500,
        a=TerminationIn(site_id=s["xa"], device="XA-WAN-R9", interface="GE0/0/9", port_speed=1000, ip="10.255.0.1"),
        z=TerminationIn(network_id=n["cn2"], ip="10.255.0.2"), carve_from="10.255.0.0/16", vlan=3009,
        routing="BGP", local_as="65001", remote_as="4809", risks=["西安机房入户光缆 A"]))
    data = importer.export(ADMIN)
    report = importer.run(ADMIN, data, commit=False)
    assert report["errors"] == 0
    changed = [r for r in report["rows"] if r["result"] != "无变化"]
    assert not changed, changed
    assert report["summary"]["专线"]["无变化"] == 13 and report["summary"]["站点"]["无变化"] == 6


def test_circuit_rows_report_their_problems(ledger):
    from openpyxl import Workbook

    from src.apps.resource_ledger.backend import importer

    world()
    book = Workbook()
    book.remove(book.active)
    sheet = book.create_sheet("专线")
    sheet.append(importer.SHEETS["专线"])
    blank = [""] * len(importer.SHEETS["专线"])
    head = importer.SHEETS["专线"]

    def row(**cells):
        values = list(blank)
        for key, value in cells.items():
            values[head.index(key)] = value
        return values

    sheet.append(row(运营商="中国联通", 电路编号="NEW-1", 类型="MSTP/SDH", A端站点="不存在的站点"))
    sheet.append(row(运营商="中国联通", 电路编号="NEW-2", 类型="MSTP/SDH", A端站点="西安机房", Z端站点="咸阳灾备中心",
                     Z端运营商网络="电信 CN2 华北"))
    sheet.append(row(运营商="中国联通", 电路编号="NEW-3", 类型="MSTP/SDH", A端站点="西安机房", 路由方式="RIP"))
    sheet.append(row(运营商="新运营商", 电路编号="NEW-4", 类型="卫星链路", A端站点="西安机房", A端局端="某局",
                     共享风险="新管道、西安机房入户光缆 A"))
    import io
    buffer = io.BytesIO()
    book.save(buffer)
    report = importer.run(ADMIN, buffer.getvalue(), commit=True)
    errors = {r["row"]: r["message"] for r in report["rows"] if r["result"] == "错误"}
    assert "站点“不存在的站点”不存在" in errors[2]
    assert "只能填站点或运营商网络中的一个" in errors[3]
    assert "路由方式“RIP”无效" in errors[4]
    assert 5 not in errors and report["committed"] is False
    assert report["created"]["运营商"] == ["新运营商"] and report["created"]["专线类型"] == ["卫星链路"]
    assert report["created"]["局端"] == ["新运营商 / 某局"]


def test_chat_tools_answer_in_words(ledger):
    from src.apps.resource_ledger.backend import circuit_tools
    from src.platform.tools.registry import catalog

    world()
    found = circuit_tools.find_circuits(ADMIN, "CN2-0098812")["circuits"][0]
    assert found["a"] == "西安机房 · XA-WAN-R1 · 西安高新局 PE-XA-03" and found["z"] == "电信 CN2 华北"
    chengdu = circuit_tools.site_resilience(ADMIN, "成都")["sites"][0]
    assert chengdu["state"] == "有单点" and "运营商 中国移动" in chengdu["single_points"] and "verdict" not in chengdu
    assert chengdu["paths"][0] == "CD-4G-0012 → 移动 MPLS 西南 → XA-YD-0001 → 西安机房"
    impact = circuit_tools.failure_impact(ADMIN, "共享风险", "入户光缆 A")
    assert {s["site"] for s in impact["sites"]} == {"北京亦庄机房", "西安机房", "成都门店012"}
    assert impact["circuits"][0].startswith("CN2-0098812（中国电信，报障 10000）")
    with pytest.raises(HTTPException, match="对应多个共享风险"):
        circuit_tools.failure_impact(ADMIN, "共享风险", "入户光缆")
    names = {name for name, (app, _) in catalog().items() if app == "resource-ledger"}
    assert {"resource_ledger__find_circuits", "resource_ledger__site_resilience", "resource_ledger__failure_impact"} <= names
