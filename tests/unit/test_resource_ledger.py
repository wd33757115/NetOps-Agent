"""Resource ledger: hierarchical IP planning and the application ledger, against the package's own schema."""
import io

import pytest
from fastapi import HTTPException
from openpyxl import Workbook, load_workbook
from sqlalchemy import create_engine

from src.apps.resource_ledger.backend import importer, inventory, netmath, service
from src.apps.resource_ledger.backend.schemas import (
    AllocateIn,
    HostIn,
    LevelIn,
    LevelsIn,
    PrefixFields,
    PrefixIn,
    RegionIn,
    ReservationIn,
    ServiceIn,
    SiteIn,
    SpaceIn,
    SubsystemIn,
    SystemIn,
    ZoneIn,
)
from src.auth.models import CurrentUser
from src.platform.kernel.migrations import migrate
from src.platform.kernel.registry import discover

ADMIN = CurrentUser(user_id="1", username="admin", role="admin")
OPERATOR = CurrentUser(user_id="2", username="ops", role="operator")
VIEWER = CurrentUser(user_id="3", username="viewer", role="viewer")
LV = {"region": "lv-region", "site": "lv-site", "zone": "lv-zone", "system": "lv-system", "subsystem": "lv-subsystem"}


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///" + str(tmp_path / "ledger.sqlite3"))
    assert migrate(discover()["resource-ledger"], engine) == "resource_ledger_0003"
    monkeypatch.setattr("src.platform.kernel.migrations.business_engine", lambda: engine)
    yield engine
    engine.dispose()


def objects(actor=ADMIN):
    region = service.save_region(actor, RegionIn(name="西安"))["id"]
    site = service.save_site(actor, SiteIn(region_id=region, name="西安机房"))["id"]
    zone = service.save_zone(actor, ZoneIn(site_id=site, name="region2", title="生产业务区",
                                           firewall="tra-FW1", security_domain="Untrust"))["id"]
    system = inventory.save_system(actor, SystemIn(name="风险评估系统", importance="核心"))["id"]
    sub1 = inventory.save_subsystem(actor, SubsystemIn(system_id=system, name="风险评估子系统1"))["id"]
    sub2 = inventory.save_subsystem(actor, SubsystemIn(system_id=system, name="风险评估子系统2"))["id"]
    return {"region": region, "site": site, "zone": zone, "system": system, "sub1": sub1, "sub2": sub2}


def block(network=None, actor=ADMIN, **fields):
    return service.create_prefix(actor, PrefixIn(network=network, **fields))["id"]


def carve(parent, kind, length, ident=None, **fields):
    key = {"subsystem": "subsystem_id"}.get(kind, f"{kind}_id")
    return block(parent_id=parent, length=length, level_id=LV[kind], **{key: ident}, **fields)


def planned(o):
    """10.1.0.0/16 carved down to two subsystems of the risk system."""
    root = block("10.1.0.0/16", label="西部生产地址")
    region = carve(root, "region", 17, o["region"])
    site = carve(region, "site", 18, o["site"])
    zone = carve(site, "zone", 20, o["zone"])
    system = carve(zone, "system", 22, o["system"])
    sub1 = carve(system, "subsystem", 24, o["sub1"], gateway="10.1.0.254")
    sub2 = carve(system, "subsystem", 24, o["sub2"])
    return {"root": root, "region": region, "site": site, "zone": zone, "system": system, "sub1": sub1, "sub2": sub2}


def test_network_parsing_is_strict_and_ranges_are_exact():
    assert netmath.parse_network("10.1.1.0/24")[3] == "10.1.1.0/24"
    with pytest.raises(ValueError, match="应写成 10.1.1.0/24"):
        netmath.parse_network("10.1.1.5/24")
    with pytest.raises(ValueError, match="缺少掩码"):
        netmath.parse_network("10.1.1.0")
    first, last, length, _ = netmath.parse_network("10.0.0.0/31")
    assert netmath.usable(first, last, length) == (first, last)
    first, last, length, _ = netmath.parse_network("10.0.0.0/24")
    assert netmath.usable(first, last, length) == (first + 1, last - 1)
    outer = netmath.parse_network("10.1.0.0/16")[:2]
    kids = [netmath.parse_network(n)[:2] for n in ("10.1.1.0/24", "10.1.2.0/24", "10.1.4.0/24")]
    gaps = netmath.gaps(outer, kids)
    assert [(netmath.ip_text(a), netmath.ip_text(b)) for a, b in gaps] == [
        ("10.1.0.0", "10.1.0.255"), ("10.1.3.0", "10.1.3.255"), ("10.1.5.0", "10.1.255.255")]
    assert netmath.aligned_blocks(gaps[-1], 24) == 251


def test_carving_takes_the_next_free_block_and_inherits_bindings(ledger):
    o = objects()
    p = planned(o)
    tree = service.subtree(ADMIN, p["root"])
    rows = [r for r in tree["rows"] if r["type"] == "prefix"]
    assert [(r["network"], r["depth"], r["level"] and r["level"]["title"]) for r in rows] == [
        ("10.1.0.0/16", 0, None), ("10.1.0.0/17", 1, "地域"), ("10.1.0.0/18", 2, "机房"),
        ("10.1.0.0/20", 3, "网络区域"), ("10.1.0.0/22", 4, "业务系统"),
        ("10.1.0.0/24", 5, "子系统"), ("10.1.1.0/24", 5, "子系统")]
    leaf = rows[-1]
    assert leaf["title"] == "风险评估子系统2"
    assert [step["name"] for step in leaf["path"]] == ["西安", "西安机房", "region2", "风险评估系统", "风险评估子系统2"]
    assert leaf["zone"] == {**leaf["zone"], "name": "region2", "inherited": True, "firewall": "tra-FW1"}
    assert leaf["system"]["name"] == "风险评估系统" and leaf["subsystem"]["name"] == "风险评估子系统2"
    assert rows[0]["title"] == "西部生产地址"
    gap = next(r for r in tree["rows"] if r["type"] == "gap" and r["parent_id"] == p["system"])
    assert (gap["first"], gap["last"], gap["unit"], gap["blocks"]) == ("10.1.2.0", "10.1.3.255", 24, 2)
    options = service.carve_options(ADMIN, p["system"])
    assert [lv["title"] for lv in options["levels"]] == ["子系统"]
    assert (options["length"], options["count"], options["candidates"]) == (24, 2, ["10.1.2.0/24", "10.1.3.0/24"])
    region_options = service.carve_options(ADMIN, p["root"])
    assert region_options["levels"][0]["suggested_length"] == 17
    assert region_options["candidates"][0] == "10.1.128.0/17"
    resource = service.resources(ADMIN)["resources"][0]
    assert (resource["network"], resource["title"], resource["planned"]) == ("10.1.0.0/16", "西部生产地址", 50)


def test_levels_must_go_down_and_bindings_must_agree(ledger):
    o = objects()
    p = planned(o)
    with pytest.raises(HTTPException, match="不能划在“网络区域”层级"):
        carve(p["zone"], "region", 21, o["region"])
    beijing = service.save_region(ADMIN, RegionIn(name="北京"))["id"]
    yizhuang = service.save_site(ADMIN, SiteIn(region_id=beijing, name="亦庄机房"))["id"]
    with pytest.raises(HTTPException, match="亦庄机房.*属于地域“北京”.*上级网段归属地域“西安”"):
        carve(p["region"], "site", 18, yizhuang)
    with pytest.raises(HTTPException, match="请选择业务系统"):
        carve(p["zone"], "system", 22)
    other = inventory.save_system(ADMIN, SystemIn(name="信贷审批系统"))["id"]
    approval = inventory.save_subsystem(ADMIN, SubsystemIn(system_id=other, name="审批服务"))["id"]
    with pytest.raises(HTTPException, match="审批服务.*属于业务系统“信贷审批系统”"):
        carve(p["system"], "subsystem", 24, approval)
    # Skipping levels is fine: a system block straight under the region.
    assert carve(p["region"], "system", 22, other)
    with pytest.raises(HTTPException, match="已经没有空闲的 /17"):
        carve(p["root"], "region", 17, o["region"]) and carve(p["root"], "region", 17, o["region"])
    with pytest.raises(HTTPException, match="落在已有的 10.1.0.0/22 里"):
        block("10.1.2.0/24", parent_id=p["zone"])


def test_inserting_a_block_must_fit_the_blocks_below_it(ledger):
    o = objects()
    root = block("10.2.0.0/16")
    sub = carve(root, "subsystem", 24, o["sub1"])
    other = inventory.save_system(ADMIN, SystemIn(name="信贷审批系统"))["id"]
    with pytest.raises(HTTPException, match="属于业务系统“风险评估系统”，但上级网段归属业务系统“信贷审批系统”"):
        block("10.2.0.0/22", level_id=LV["system"], system_id=other)
    middle = block("10.2.0.0/22", level_id=LV["system"], system_id=o["system"])
    assert service.prefix_detail(ADMIN, sub)["parent_id"] == middle
    with pytest.raises(HTTPException, match="不能划在“子系统”层级"):
        service.update_prefix(ADMIN, middle, PrefixFields(level_id=LV["subsystem"], subsystem_id=o["sub2"]))


def test_planning_levels_can_change_while_blocks_still_fit(ledger):
    o = objects()
    planned(o)
    levels = service.overview(ADMIN)["levels"]
    body = [LevelIn(**{k: lv[k] for k in ("id", "dimension", "title", "default_length")}) for lv in levels]
    body[0].title = "大区"
    custom = LevelIn(dimension="custom", title="安全分区")
    result = service.save_levels(ADMIN, LevelsIn(levels=[*body, custom]))
    assert [lv["title"] for lv in result["levels"]] == ["大区", "机房", "网络区域", "业务系统", "子系统", "安全分区"]
    with pytest.raises(HTTPException, match="还有网段在用"):
        service.save_levels(ADMIN, LevelsIn(levels=body[1:]))
    with pytest.raises(HTTPException, match="要按从大到小的顺序排列"):
        service.save_levels(ADMIN, LevelsIn(levels=[body[1], body[0], *body[2:]]))
    # Putting systems above zones is allowed as a hierarchy, but the planned blocks no longer fit.
    with pytest.raises(HTTPException, match="不能划在"):
        service.save_levels(ADMIN, LevelsIn(levels=[body[3], body[0], body[1], body[2], body[4]]))
    entry = service.changes(ADMIN, entity="规划层级")["items"][0]
    assert entry["detail"][0]["after"].startswith("大区 › 机房")


def test_allocation_skips_gateway_reservations_and_taken_addresses(ledger):
    leaf = block("10.1.1.0/29", label="小网段", gateway="10.1.1.1")
    service.reserve(ADMIN, leaf, ReservationIn(first="10.1.1.2", last="10.1.1.3", purpose="预留"))
    first = service.allocate(OPERATOR, leaf, AllocateIn(hostname="a"))
    assert first["ip"] == "10.1.1.4"
    assert service.allocate(OPERATOR, leaf, AllocateIn(ip="10.1.1.6", kind="VIP"))["ip"] == "10.1.1.6"
    for ip, message in [("10.1.1.1", "网关"), ("10.1.1.2", "保留范围"), ("10.1.1.4", "已分配"),
                        ("10.1.1.7", "可用地址范围"), ("10.1.2.1", "可用地址范围")]:
        with pytest.raises(HTTPException, match=message):
            service.allocate(OPERATOR, leaf, AllocateIn(ip=ip))
    assert service.allocate(OPERATOR, leaf, AllocateIn())["ip"] == "10.1.1.5"
    with pytest.raises(HTTPException, match="没有空闲地址"):
        service.allocate(OPERATOR, leaf, AllocateIn())
    detail = service.prefix_detail(ADMIN, leaf)
    assert detail["stats"] == {"usable": 6, "allocated": 4, "reserved": 2, "free": 0}
    assert detail["usage"]["percent"] == 100


def test_allocation_rules_for_summaries_and_closed_prefixes(ledger):
    summary = block("10.1.0.0/16")
    block("10.1.1.0/24")
    with pytest.raises(HTTPException, match="已划分子网段"):
        service.allocate(ADMIN, summary, AllocateIn())
    held = block("10.2.0.0/24", status="保留")
    with pytest.raises(HTTPException, match="不能分配"):
        service.allocate(ADMIN, held, AllocateIn())


def test_reservations_cannot_overlap_or_cover_allocations(ledger):
    leaf = block("10.1.1.0/24")
    service.allocate(ADMIN, leaf, AllocateIn(ip="10.1.1.15"))
    service.reserve(ADMIN, leaf, ReservationIn(first="10.1.1.20", last="10.1.1.30", purpose="扩容"))
    with pytest.raises(HTTPException, match="重叠"):
        service.reserve(ADMIN, leaf, ReservationIn(first="10.1.1.25", last="10.1.1.40", purpose="x"))
    with pytest.raises(HTTPException, match="已分配地址"):
        service.reserve(ADMIN, leaf, ReservationIn(first="10.1.1.10", last="10.1.1.19", purpose="x"))
    with pytest.raises(HTTPException, match="之内"):
        service.reserve(ADMIN, leaf, ReservationIn(first="10.1.1.250", last="10.1.1.255", purpose="x"))


def test_deleting_keeps_allocations_safe_and_summaries_removable(ledger):
    summary = block("10.1.0.0/16")
    leaf = block("10.1.1.0/24")
    address = service.allocate(ADMIN, leaf, AllocateIn())
    with pytest.raises(HTTPException, match="请先释放"):
        service.delete_prefix(ADMIN, leaf)
    # Removing the summary leaves its child as a resource of its own.
    service.delete_prefix(ADMIN, summary)
    assert [r["network"] for r in service.resources(ADMIN)["resources"]] == ["10.1.1.0/24"]
    service.release_address(ADMIN, address["id"])
    service.delete_prefix(ADMIN, leaf)
    block("10.1.1.0/24")
    with pytest.raises(HTTPException, match="已有网段 10.1.1.0/24"):
        block("10.1.1.0/24")


def test_viewer_reads_but_cannot_change(ledger):
    leaf = block("10.1.1.0/24")
    assert service.prefix_detail(VIEWER, leaf)["can_edit"] is False
    with pytest.raises(HTTPException) as denied:
        service.allocate(VIEWER, leaf, AllocateIn())
    assert denied.value.status_code == 403
    with pytest.raises(HTTPException) as denied:
        inventory.save_system(VIEWER, SystemIn(name="x"))
    assert denied.value.status_code == 403


def test_gateway_must_be_usable_and_updates_are_logged_by_name(ledger):
    o = objects()
    with pytest.raises(HTTPException, match="网关"):
        block("10.1.1.0/24", gateway="10.1.1.255")
    leaf = block("10.1.1.0/24", purpose="旧用途")
    service.update_prefix(OPERATOR, leaf, PrefixFields(purpose="新用途", level_id=LV["zone"], zone_id=o["zone"],
                                                     vlan=101, gateway="10.1.1.254", ticket="CHG-1"))
    entry = service.changes(ADMIN, entity="网段")["items"][0]
    assert (entry["action"], entry["actor_name"], entry["label"]) == ("修改", "ops", "网段 10.1.1.0/24")
    fields = {c["field"]: (c["before"], c["after"]) for c in entry["detail"]}
    assert fields["用途"] == ("旧用途", "新用途") and fields["申请工单号"] == (None, "CHG-1")
    assert fields["层级"] == (None, "网络区域") and fields["网络区域"] == (None, "region2")


def test_search_finds_an_ip_in_every_routing_domain_and_blocks_by_owner(ledger):
    o = objects()
    p = planned(o)
    other = service.save_space(ADMIN, SpaceIn(name="灾备网"))["id"]
    block("10.1.0.0/16", space_id=other)
    dr = block("10.1.1.0/24", space_id=other, purpose="灾备风险评估")
    space = service.resources(ADMIN)["spaces"]
    assert {s["name"] for s in space} == {"全局", "灾备网"}
    service.allocate(ADMIN, dr, AllocateIn(ip="10.1.1.9", hostname="dr-app"))
    found = service.search(ADMIN, "10.1.1.9")
    by_space = {r["space"]["name"]: r for r in found["results"]}
    assert set(by_space) == {"全局", "灾备网"}
    assert by_space["全局"]["prefix"]["title"] == "风险评估子系统2" and by_space["全局"]["address"] is None
    assert by_space["灾备网"]["address"]["hostname"] == "dr-app"
    assert service.search(ADMIN, "dr-app")["results"][0]["ip"] == "10.1.1.9"
    owned = service.search(ADMIN, "子系统1")["results"]
    assert [r["id"] for r in owned] == [p["sub1"]]
    with pytest.raises(HTTPException, match="有多个路由域"):
        block("10.9.0.0/16")


def test_zones_page_lists_zone_blocks_and_usage(ledger):
    o = objects()
    p = planned(o)
    service.allocate(ADMIN, p["sub2"], AllocateIn())
    row = service.zones_page(ADMIN)["zones"][0]
    assert [b["network"] for b in row["prefixes"]] == ["10.1.0.0/20"]
    assert (row["subnets"], row["usage"]) == (2, 0)
    assert row["where"]["site"] == "西安机房"
    with pytest.raises(HTTPException, match="还有网段划给了这个网络区域"):
        service.delete_zone(ADMIN, o["zone"])


def host(o, name="risk-app-01", ips=(), sub="sub1", **fields):
    return HostIn(hostname=name, system_id=o["system"], subsystem_id=o[sub] if sub else None, ips=list(ips), **fields)


def test_hosts_take_addresses_only_from_their_own_blocks(ledger):
    o = objects()
    p = planned(o)
    suggestion = inventory.suggest_ip(ADMIN, o["system"], o["sub1"])
    assert suggestion == {"ip": "10.1.0.1", "prefix": {"id": p["sub1"], "network": "10.1.0.0/24"}, "space": "全局"}
    vip = service.allocate(ADMIN, p["sub1"], AllocateIn(ip="10.1.0.10", kind="VIP"))
    created = inventory.save_host(OPERATOR, host(o, ips=["10.1.0.1", "10.1.0.10"], services=[
        ServiceIn(protocol="tcp", ports="80"), ServiceIn(protocol="TCP", ports="8000-8010"), ServiceIn(protocol="ICMP")]))
    view = inventory.host_detail(ADMIN, created["id"])
    assert [ip["ip"] for ip in view["ips"]] == ["10.1.0.1", "10.1.0.10"]
    assert view["ips"][0]["zone"]["firewall"] == "tra-FW1"
    assert [s["text"] for s in view["services"]] == ["ICMP", "TCP/80", "TCP/8000-8010"]
    detail = service.prefix_detail(ADMIN, p["sub1"])
    linked = {a["ip"]: a["host"] and a["host"]["hostname"] for a in detail["addresses"]}
    assert linked == {"10.1.0.1": "risk-app-01", "10.1.0.10": "risk-app-01"}
    assert vip["ip"] == "10.1.0.10"
    for ips, message in [(["10.1.1.5"], "属于子系统“风险评估子系统2”"), (["10.9.9.9"], "不在任何已规划的网段里"),
                         (["10.1.0.254"], "网关"), (["10.1.0.0"], "可用地址范围")]:
        with pytest.raises(HTTPException, match=message):
            inventory.save_host(ADMIN, host(o, "x", ips=ips))
    with pytest.raises(HTTPException, match="已是主机 risk-app-01 的地址"):
        inventory.save_host(ADMIN, host(o, "y", ips=["10.1.0.1"]))
    with pytest.raises(HTTPException, match="端口"):
        inventory.save_host(ADMIN, host(o, "z", services=[ServiceIn(protocol="UDP", ports="70000")]))
    # A host without a subsystem may use any block of its system.
    inventory.save_host(ADMIN, host(o, "shared", ips=["10.1.1.7"], sub=None))
    assert inventory.suggest_ip(ADMIN, o["system"], o["sub1"], ["10.1.0.2"])["ip"] == "10.1.0.3"


def test_host_addresses_guard_later_changes(ledger):
    o = objects()
    p = planned(o)
    created = inventory.save_host(ADMIN, host(o, ips=["10.1.0.5"]))["id"]
    address = service.prefix_detail(ADMIN, p["sub1"])["addresses"][0]
    with pytest.raises(HTTPException, match="请先在主机台账里移除"):
        service.release_address(ADMIN, address["id"])
    with pytest.raises(HTTPException, match="10.1.0.5 是主机 risk-app-01 的地址"):
        service.update_prefix(ADMIN, p["sub1"], PrefixFields(level_id=LV["subsystem"], subsystem_id=o["sub2"]))
    # Still inside the system's block, so a host of the subsystem may keep it.
    service.update_prefix(ADMIN, p["sub1"], PrefixFields(label="公共"))
    with pytest.raises(HTTPException, match="10.1.0.5 是主机 risk-app-01 的地址.*没有划给业务系统"):
        service.update_prefix(ADMIN, p["system"], PrefixFields(label="公共区"))
    service.update_prefix(ADMIN, p["sub1"], PrefixFields(level_id=LV["subsystem"], subsystem_id=o["sub1"]))
    with pytest.raises(HTTPException, match="不能给主机"):
        inventory.save_host(ADMIN, host(o, ips=["10.1.0.5"], sub="sub2"), created)
    # Dropping the IP unlinks it; the address stays allocated until someone releases it.
    inventory.save_host(ADMIN, host(o, ips=[]), created)
    kept = service.prefix_detail(ADMIN, p["sub1"])["addresses"][0]
    assert (kept["ip"], kept["host"]) == ("10.1.0.5", None)
    service.release_address(ADMIN, kept["id"])
    inventory.save_host(ADMIN, host(o, ips=["10.1.0.6"]), created)
    assert inventory.delete_host(ADMIN, created)["kept_addresses"] == ["10.1.0.6"]
    page = inventory.systems_page(ADMIN)["systems"][0]
    assert [b["network"] for b in page["blocks"]] == ["10.1.0.0/22"]
    assert page["blocks"][0]["where"] == ["西安", "西安机房", "region2"]
    assert [b["network"] for b in page["subsystems"][0]["blocks"]] == ["10.1.0.0/24"]


def test_hosts_page_filters_and_counts(ledger):
    o = objects()
    planned(o)
    inventory.save_host(ADMIN, host(o, "risk-app-01", ips=["10.1.0.11"]))
    inventory.save_host(ADMIN, host(o, "risk-db-01", ips=["10.1.1.11"], sub="sub2", role="数据库", status="待投产"))
    page = inventory.hosts_page(ADMIN, system_id=o["system"])
    assert page["total"] == 2
    assert inventory.hosts_page(ADMIN, status="待投产")["hosts"][0]["hostname"] == "risk-db-01"
    assert inventory.hosts_page(ADMIN, query="10.1.0.")["hosts"][0]["hostname"] == "risk-app-01"
    counts = inventory.systems_page(ADMIN)["systems"][0]
    assert counts["hosts"] == {"已投产": 1, "待投产": 1}
    assert counts["subsystems"][1]["hosts"] == {"待投产": 1}
    with pytest.raises(HTTPException, match="还有子系统"):
        inventory.delete_system(ADMIN, o["system"])


def workbook(**sheets):
    book = Workbook()
    book.remove(book.active)
    for name, rows in sheets.items():
        sheet = book.create_sheet(name)
        sheet.append(importer.SHEETS[name])
        for row in rows:
            sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def test_template_imports_its_own_examples_and_exports_round_trip(ledger):
    data = importer.template()
    assert set(load_workbook(io.BytesIO(data)).sheetnames) == {*importer.SHEETS, "说明"}
    preview = importer.run(ADMIN, data, commit=False)
    assert preview["errors"] == 0 and preview["committed"] is False, preview["rows"]
    assert service.resources(ADMIN)["resources"] == []  # preview writes nothing
    done = importer.run(ADMIN, data, commit=True)
    assert done["committed"] and done["summary"]["网段"]["新增"] == 6
    assert done["created"]["地域"] == ["西安"] and done["summary"]["站点"]["新增"] == 2
    assert done["summary"]["专线"]["新增"] == 2 and done["summary"]["运营商网络"]["新增"] == 1
    leaf = service.search(ADMIN, "10.1.1.11")["results"][0]
    assert leaf["prefix"]["title"] == "风险评估子系统1" and leaf["address"]["host"]["hostname"] == "risk-app-01"
    assert leaf["prefix"]["zone"]["firewall"] == "tra-FW1"
    again = importer.run(ADMIN, importer.export(ADMIN), commit=False)
    assert again["errors"] == 0
    assert {r["result"] for r in again["rows"]} == {"无变化"}, [r for r in again["rows"] if r["result"] != "无变化"]


def test_import_settles_sites_zones_and_subsystems_under_inherited_blocks(ledger):
    data = workbook(**{
        "网段": [["10.3.0.0/16", "地址资源", "东部生产", "", "", "", "", "", "", "", ""],
                ["10.3.0.0/17", "地域", "北京", "", "", "", "", "", "", "", ""],
                ["10.3.0.0/18", "机房", "亦庄机房", "", "", "", "", "", "", "", ""],
                ["10.3.0.0/20", "网络区域", "DMZ", "", "", "", "", "", "", "", ""],
                ["10.3.0.0/22", "业务系统", "网银系统", "", "", "", "", "", "", "", ""],
                ["10.3.0.0/24", "子系统", "网银前置", "", "", "", "", "", "", "", ""],
                ["10.3.8.0/24", "子系统", "孤儿子系统", "", "", "", "", "", "", "", ""],
                ["10.3.9.0/24", "不存在的层级", "x", "", "", "", "", "", "", "", ""]],
    })
    report = importer.run(ADMIN, data, commit=True)
    errors = {r["row"]: r["message"] for r in report["rows"] if r["result"] == "错误"}
    assert set(errors) == {8, 9}
    assert "需要上级网段先划到业务系统" in errors[8] and "层级“不存在的层级”不存在" in errors[9]
    assert report["committed"] is False
    fixed = workbook(**{"网段": [row for row in [
        ["10.3.0.0/16", "地址资源", "东部生产", "", "", "", "", "", "", "", ""],
        ["10.3.0.0/17", "地域", "北京", "", "", "", "", "", "", "", ""],
        ["10.3.0.0/18", "机房", "亦庄机房", "", "", "", "", "", "", "", ""],
        ["10.3.0.0/20", "网络区域", "DMZ", "", "", "", "", "", "", "", ""],
        ["10.3.0.0/22", "业务系统", "网银系统", "", "", "", "", "", "", "", ""],
        ["10.3.0.0/24", "子系统", "网银前置", "", "", "", "", "", "", "", ""]]]})
    done = importer.run(ADMIN, fixed, commit=True)
    assert done["committed"], done["rows"]
    assert done["created"] == {"地域": ["北京"], "机房": ["亦庄机房"], "网络区域": ["亦庄机房 / DMZ"],
                               "业务系统": ["网银系统"], "子系统": ["网银系统 / 网银前置"]}


def test_import_errors_block_the_commit_and_name_the_row(ledger):
    data = workbook(**{
        "网段": [["10.1.1.0/24", "", "Web", "", "", "", "使用中", "", "", "", ""],
                ["10.1.2.5/24", "", "坏网段", "", "", "", "", "", "", "", ""]],
        "IP 地址": [["10.1.1.7", "主机", "web-1", "", "", ""],
                   ["10.9.9.9", "主机", "x", "", "", ""]],
        "主机": [["app-1", "没有的系统", "", "10.1.1.8", "TCP/80", "", "", "", "", "", "", "", "", "", ""]],
    })
    report = importer.run(ADMIN, data, commit=True)
    assert report["committed"] is False and report["errors"] == 3
    errors = {(r["sheet"], r["row"]): r["message"] for r in report["rows"] if r["result"] == "错误"}
    assert "应写成 10.1.2.0/24" in errors[("网段", 3)]
    assert "不在任何已规划的网段内" in errors[("IP 地址", 3)]
    assert "业务系统“没有的系统”不存在" in errors[("主机", 2)]
    assert service.resources(ADMIN)["resources"] == []


def test_import_is_admin_only(ledger):
    with pytest.raises(HTTPException) as denied:
        importer.run(OPERATOR, importer.template(), commit=False)
    assert denied.value.status_code == 403


def test_queries_answer_where_things_are_and_who_reaches_whom(ledger):
    from src.apps.resource_ledger.backend import queries

    o = objects()
    planned(o)
    inventory.save_host(ADMIN, host(o, "risk-web-01", ips=["10.1.0.11"], services=[
        ServiceIn(protocol="TCP", ports="80"), ServiceIn(protocol="TCP", ports="443")]))
    inventory.save_host(ADMIN, host(o, "risk-web-02", ips=["10.1.0.12"], services=[ServiceIn(protocol="TCP", ports="443")]))
    inventory.save_host(ADMIN, host(o, "risk-db-01", ips=["10.1.1.11"], sub="sub2", services=[
        ServiceIn(protocol="TCP", ports="3306")]))
    inventory.save_host(ADMIN, host(o, "risk-old", ips=["10.1.1.12"], sub="sub2", status="已下线", services=[
        ServiceIn(protocol="TCP", ports="22")]))
    found = queries.find_systems(ADMIN, "风险评估")["systems"]
    assert [s["name"] for s in found] == ["风险评估系统"]
    assert {b["network"] for b in found[0]["blocks"]} == {"10.1.0.0/22", "10.1.0.0/24", "10.1.1.0/24"}
    assert found[0]["blocks"][0]["zone"] == "region2"
    # Firewalls come from the topology when the policy is generated, never from the ledger.
    assert "firewall" not in found[0]["blocks"][0]
    where = queries.locate_ip(ADMIN, "10.1.0.11")
    place = where["places"][0]
    assert (place["system"], place["subsystem"], place["zone"], place["site"]) == (
        "风险评估系统", "风险评估子系统1", "region2", "西安机房")
    assert place["host"]["hostname"] == "risk-web-01" and place["host"]["services"] == ["TCP/80", "TCP/443"]
    assert queries.locate_ip(ADMIN, "8.8.8.8") == {"ip": "8.8.8.8", "found": False, "places": []}

    plan = queries.plan_access(ADMIN, "10.248.255.11", "西安风险评估系统")
    assert plan["rows"] == [
        {"source_system": "", "src_ip": "10.248.255.11", "destination_system": "风险评估系统",
         "dst_ip": "10.1.0.11", "port": "80", "proto": "tcp"},
        {"source_system": "", "src_ip": "10.248.255.11", "destination_system": "风险评估系统",
         "dst_ip": "10.1.0.11,10.1.0.12", "port": "443", "proto": "tcp"},
        {"source_system": "", "src_ip": "10.248.255.11", "destination_system": "风险评估系统",
         "dst_ip": "10.1.1.11", "port": "3306", "proto": "tcp"},
    ]
    assert "firewalls" not in plan
    assert plan["notes"] == ["10.248.255.11 不在台账的已规划网段里，按申请照常放行"]
    narrowed = queries.plan_access(ADMIN, "风险评估系统", "10.6.145.180-182", source_subsystem="风险评估子系统1", ports="tcp/5672")
    assert narrowed["rows"] == [{"source_system": "风险评估系统", "src_ip": "10.1.0.11,10.1.0.12",
                                 "destination_system": "", "dst_ip": "10.6.145.180-182", "port": "5672", "proto": "tcp"}]
    for args, message in [(("10.1.1.1", "10.2.2.2"), "请说明要开通的端口"), (("10.1.1.1", "没有的系统"), "没有业务系统"),
                          (("10.1.1.1", "风险评估系统", None, None, "http"), "端口“http”无效")]:
        with pytest.raises(HTTPException, match=message):
            queries.plan_access(ADMIN, *args)


def test_ledger_tools_are_registered_read_only():
    from src.platform.tools.registry import catalog

    tools = {name: tool for name, (app, tool) in catalog().items() if app == "resource-ledger"}
    assert set(tools) == {"resource_ledger__find_systems", "resource_ledger__list_hosts",
                          "resource_ledger__locate_ip", "resource_ledger__plan_access",
                          "resource_ledger__find_circuits", "resource_ledger__site_resilience",
                          "resource_ledger__failure_impact"}
    assert not any(tool.mutates for tool in tools.values())
