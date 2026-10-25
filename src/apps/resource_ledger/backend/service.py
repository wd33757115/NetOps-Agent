"""IP address management: planning levels, the block tree, allocation, reservations and search.

Address resources are the top blocks of a routing domain (``Space``). Planners carve them
down level by level (region, site, network zone, business system, subsystem, or levels of
their own) and each block binds the object of its level while inheriting the others from
the blocks above it. So every address can say where it sits and which business it serves,
and a host only takes addresses from its own system's blocks.
"""

from bisect import bisect_left, bisect_right

from fastapi import HTTPException
from sqlalchemy import func, or_, select

from . import netmath
from .common import (
    bad,
    can_edit,
    diff,
    missing,
    new_id,
    now,
    percent,
    record,
    require_editor,
    sessions,
)
from .models import (
    Address,
    Change,
    Circuit,
    Host,
    Level,
    Prefix,
    Region,
    Reservation,
    Site,
    Space,
    Subsystem,
    System,
    Termination,
    Zone,
)
from .schemas import (
    AddressFields,
    AllocateIn,
    LevelsIn,
    PrefixFields,
    PrefixIn,
    RegionIn,
    ReservationIn,
    SiteIn,
    SpaceIn,
    ZoneIn,
)

# Allocation stays out of prefixes kept aside or being returned.
CLOSED = {"保留", "待回收"}
FIELDS = {"region": "region_id", "site": "site_id", "zone": "zone_id", "system": "system_id", "subsystem": "subsystem_id"}
KINDS = {"region": "地域", "site": "机房", "zone": "网络区域", "system": "业务系统", "subsystem": "子系统", "custom": "自定义"}
# Objects that contain one another are planned outermost first.
CHAINS = (("region", "site", "zone"), ("system", "subsystem"))


# --------------------------------------------------------------------------- lookups


class Lookup:
    """Levels and the objects blocks bind, loaded once per request."""

    def __init__(self, session):
        self.levels = {lv.id: lv for lv in session.scalars(select(Level).order_by(Level.position)).all()}
        self.objects = {
            "region": {r.id: r for r in session.scalars(select(Region)).all()},
            "site": {s.id: s for s in session.scalars(select(Site)).all()},
            "zone": {z.id: z for z in session.scalars(select(Zone)).all()},
            "system": {s.id: s for s in session.scalars(select(System)).all()},
            "subsystem": {s.id: s for s in session.scalars(select(Subsystem)).all()},
        }

    def ordered_levels(self):
        return sorted(self.levels.values(), key=lambda lv: lv.position)

    def get(self, kind, ident):
        return self.objects[kind].get(ident)

    def name(self, kind, ident):
        if kind == "level":
            level = self.levels.get(ident)
            return level.title if level else ident
        row = self.get(kind, ident)
        return row.name if row else ident

    def implied(self, kind, ident):
        """The object and everything it belongs to, outermost first."""
        row = self.get(kind, ident)
        if row is None:
            raise missing(KINDS[kind])
        if kind == "site":
            return self.implied("region", row.region_id) + [("site", ident)]
        if kind == "zone":
            return self.implied("site", row.site_id) + [("zone", ident)]
        if kind == "subsystem":
            return self.implied("system", row.system_id) + [("subsystem", ident)]
        return [(kind, ident)]

    def zone_ref(self, zone_id, inherited=False):
        zone = self.get("zone", zone_id)
        if not zone:
            return None
        site = self.get("site", zone.site_id)
        region = self.get("region", site.region_id) if site else None
        return {
            "id": zone.id, "name": zone.name, "title": zone.title, "inherited": inherited,
            "site": site.name if site else None, "region": region.name if region else None,
        }

    def ref(self, kind, ident):
        return {"id": ident, "name": self.name(kind, ident)} if ident else None


def level_ref(level):
    return {"id": level.id, "position": level.position, "dimension": level.dimension,
            "title": level.title, "default_length": level.default_length}


# --------------------------------------------------------------------------- locations


def overview(actor):
    with sessions() as session:
        regions = session.scalars(select(Region).order_by(Region.name)).all()
        sites = session.scalars(select(Site).order_by(Site.name)).all()
        spaces = session.scalars(select(Space).order_by(Space.name)).all()
        zones = session.scalars(select(Zone).order_by(Zone.name)).all()
        systems = session.scalars(select(System).order_by(System.name)).all()
        subsystems = session.scalars(select(Subsystem).order_by(Subsystem.name)).all()
        levels = session.scalars(select(Level).order_by(Level.position)).all()
        return {
            "can_edit": can_edit(actor),
            "regions": [{"id": r.id, "name": r.name, "note": r.note} for r in regions],
            "sites": [
                {"id": s.id, "region_id": s.region_id, "name": s.name, "kind": s.kind,
                 "address": s.address, "note": s.note, "core": s.core}
                for s in sites
            ],
            "spaces": [space_view(s) for s in spaces],
            "zones": [zone_view(z) for z in zones],
            "systems": [{"id": s.id, "name": s.name, "short_name": s.short_name, "importance": s.importance}
                        for s in systems],
            "subsystems": [{"id": s.id, "system_id": s.system_id, "name": s.name} for s in subsystems],
            "levels": [level_ref(lv) for lv in levels],
        }


def space_view(space):
    return {"id": space.id, "name": space.name, "kind": space.kind, "note": space.note}


def zone_view(zone):
    return {key: getattr(zone, key) for key in (
        "id", "site_id", "name", "title", "kind", "security_level", "note")}


def bound(session, field, ident):
    return session.scalars(select(Prefix).where(getattr(Prefix, field) == ident)).first()


def save_region(actor, body: RegionIn, region_id=None):
    require_editor(actor)
    with sessions() as session:
        clash = session.scalars(select(Region).where(Region.name == body.name)).first()
        if clash and clash.id != region_id:
            raise bad(f"地域“{body.name}”已存在")
        if region_id is None:
            region = Region(id=new_id(), **body.model_dump())
            session.add(region)
            record(session, actor, "地域", region.id, f"地域 {region.name}", "新增")
        else:
            region = session.get(Region, region_id)
            if not region:
                raise missing("地域")
            changes = diff(region, body.model_dump(), {"name": "名称", "note": "备注"})
            if changes:
                record(session, actor, "地域", region.id, f"地域 {region.name}", "修改", changes)
        return {"id": region.id}


def delete_region(actor, region_id):
    require_editor(actor)
    with sessions() as session:
        region = session.get(Region, region_id)
        if not region:
            raise missing("地域")
        if session.scalars(select(Site).where(Site.region_id == region_id)).first():
            raise bad("地域下还有站点，请先删除或移走站点")
        if bound(session, "region_id", region_id):
            raise bad("还有网段划给了这个地域，请先修改这些网段")
        session.delete(region)
        record(session, actor, "地域", region.id, f"地域 {region.name}", "删除")
    return {"deleted": True}


SITE_NAMES = {"region_id": "地域", "name": "名称", "kind": "类型", "address": "地址", "note": "备注", "core": "核心站点"}


def save_site(actor, body: SiteIn, site_id=None):
    require_editor(actor)
    with sessions() as session:
        if not session.get(Region, body.region_id):
            raise missing("地域")
        clash = session.scalars(select(Site).where(Site.name == body.name)).first()
        if clash and clash.id != site_id:
            raise bad(f"站点“{body.name}”已存在")
        if site_id is None:
            site = Site(id=new_id(), **body.model_dump())
            session.add(site)
            record(session, actor, "站点", site.id, f"站点 {site.name}", "新增")
        else:
            site = session.get(Site, site_id)
            if not site:
                raise missing("站点")
            moved = site.region_id != body.region_id
            changes = diff(site, body.model_dump(), SITE_NAMES)
            if moved:
                # Blocks of this site may sit under another region's block.
                validate_all(session)
            if changes:
                record(session, actor, "站点", site.id, f"站点 {site.name}", "修改", changes)
        return {"id": site.id, "name": site.name}


def delete_site(actor, site_id):
    require_editor(actor)
    with sessions() as session:
        site = session.get(Site, site_id)
        if not site:
            raise missing("站点")
        if session.scalars(select(Zone).where(Zone.site_id == site_id)).first():
            raise bad("站点下还有网络区域，请先删除或移走网络区域")
        if bound(session, "site_id", site_id):
            raise bad("还有网段划给了这个站点，请先修改这些网段")
        if session.scalars(select(Termination).where(Termination.site_id == site_id)).first():
            raise bad("还有专线接在这个站点上，请先修改这些专线")
        session.delete(site)
        record(session, actor, "站点", site.id, f"站点 {site.name}", "删除")
    return {"deleted": True}


def resolve_space(session, space_id=None):
    """The routing domain to plan in: the one asked for, or the only one there is."""
    if space_id:
        space = session.get(Space, space_id)
        if not space:
            raise missing("路由域")
        return space.id
    spaces = session.scalars(select(Space).order_by(Space.name).limit(2)).all()
    if len(spaces) == 1:
        return spaces[0].id
    if spaces:
        raise bad("有多个路由域，请选择网段所在的路由域")
    space = Space(id=new_id(), name="全局", kind="其他", note="默认路由域")
    session.add(space)
    session.flush()
    return space.id


def save_space(actor, body: SpaceIn, space_id=None):
    require_editor(actor)
    with sessions() as session:
        clash = session.scalars(select(Space).where(Space.name == body.name)).first()
        if clash and clash.id != space_id:
            raise bad(f"路由域“{body.name}”已存在")
        if space_id is None:
            space = Space(id=new_id(), **body.model_dump())
            session.add(space)
            record(session, actor, "路由域", space.id, f"路由域 {space.name}", "新增")
        else:
            space = session.get(Space, space_id)
            if not space:
                raise missing("路由域")
            changes = diff(space, body.model_dump(), {"name": "名称", "kind": "类型", "note": "说明"})
            if changes:
                record(session, actor, "路由域", space.id, f"路由域 {space.name}", "修改", changes)
        return {"id": space.id}


def delete_space(actor, space_id):
    require_editor(actor)
    with sessions() as session:
        space = session.get(Space, space_id)
        if not space:
            raise missing("路由域")
        if session.scalars(select(Prefix).where(Prefix.space_id == space_id)).first():
            raise bad("路由域里还有网段，请先删除网段")
        session.delete(space)
        record(session, actor, "路由域", space.id, f"路由域 {space.name}", "删除")
    return {"deleted": True}


ZONE_NAMES = {
    "site_id": "机房", "name": "名称", "title": "说明名", "kind": "类型", "security_level": "安全等级",
    "note": "备注",
}


def save_zone(actor, body: ZoneIn, zone_id=None):
    require_editor(actor)
    with sessions() as session:
        if not session.get(Site, body.site_id):
            raise missing("站点")
        clash = session.scalars(
            select(Zone).where(Zone.site_id == body.site_id, Zone.name == body.name)
        ).first()
        if clash and clash.id != zone_id:
            raise bad(f"这个机房已有网络区域“{body.name}”")
        if zone_id is None:
            zone = Zone(id=new_id(), **body.model_dump())
            session.add(zone)
            record(session, actor, "网络区域", zone.id, f"网络区域 {zone.name}", "新增")
        else:
            zone = session.get(Zone, zone_id)
            if not zone:
                raise missing("网络区域")
            moved = zone.site_id != body.site_id
            changes = diff(zone, body.model_dump(), ZONE_NAMES)
            if moved:
                validate_all(session)
            if changes:
                record(session, actor, "网络区域", zone.id, f"网络区域 {zone.name}", "修改", changes)
        return {"id": zone.id}


def delete_zone(actor, zone_id):
    require_editor(actor)
    with sessions() as session:
        zone = session.get(Zone, zone_id)
        if not zone:
            raise missing("网络区域")
        if bound(session, "zone_id", zone_id):
            raise bad("还有网段划给了这个网络区域，请先修改这些网段")
        session.delete(zone)
        record(session, actor, "网络区域", zone.id, f"网络区域 {zone.name}", "删除")
    return {"deleted": True}


# --------------------------------------------------------------------------- planning levels


def save_levels(actor, body: LevelsIn):
    """Replace the hierarchy in one go; blocks already planned must still fit it."""
    require_editor(actor)
    with sessions() as session:
        current = {lv.id: lv for lv in session.scalars(select(Level)).all()}
        positions = {}
        for position, item in enumerate(body.levels, 1):
            if item.id and item.id not in current:
                raise missing("规划层级")
            if item.dimension != "custom":
                if item.dimension in positions:
                    raise bad(f"“{KINDS[item.dimension]}”只能对应一个层级")
                positions[item.dimension] = position
        for chain in CHAINS:
            placed = [positions[kind] for kind in chain if kind in positions]
            if placed != sorted(placed):
                raise bad("、".join(KINDS[k] for k in chain) + " 要按从大到小的顺序排列")
        kept = {item.id for item in body.levels if item.id}
        for level in current.values():
            in_use = session.scalars(select(Prefix).where(Prefix.level_id == level.id).limit(1)).first()
            if level.id not in kept and in_use:
                raise bad(f"层级“{level.title}”还有网段在用，不能删除")
            change = next((i for i in body.levels if i.id == level.id), None)
            if change and change.dimension != level.dimension and in_use:
                raise bad(f"层级“{level.title}”已有网段在用，不能改变它对应的对象")
        before = [lv.title for lv in sorted(current.values(), key=lambda lv: lv.position)]
        # Positions are unique: park the current ones out of the way first.
        for level in current.values():
            level.position = -level.position
        session.flush()
        for level in current.values():
            if level.id not in kept:
                session.delete(level)
        session.flush()
        for position, item in enumerate(body.levels, 1):
            values = {"position": position, "dimension": item.dimension, "title": item.title,
                      "default_length": item.default_length}
            if item.id:
                for key, value in values.items():
                    setattr(current[item.id], key, value)
            else:
                session.add(Level(id=new_id(), **values))
        session.flush()
        validate_all(session)
        after = [item.title for item in body.levels]
        record(session, actor, "规划层级", "levels", "规划层级", "修改",
               [{"field": "层级", "before": " › ".join(before) or None, "after": " › ".join(after) or None}])
        return {"levels": [level_ref(lv) for lv in session.scalars(select(Level).order_by(Level.position)).all()]}


# --------------------------------------------------------------------------- the tree


class SpaceIndex:
    """One routing domain in memory: nesting, allocations and per-block usage."""

    def __init__(self, session, space_id):
        self.prefixes = session.scalars(
            select(Prefix).where(Prefix.space_id == space_id).order_by(Prefix.first, Prefix.length)
        ).all()
        self.reservations = session.scalars(
            select(Reservation).where(Reservation.space_id == space_id).order_by(Reservation.first)
        ).all()
        self.addresses = session.scalars(
            select(Address).where(Address.space_id == space_id).order_by(Address.value)
        ).all()
        self.values = [a.value for a in self.addresses]
        self.firsts = [p.first for p in self.prefixes]
        self.parent, self.children, self.roots, stack = {}, {p.id: [] for p in self.prefixes}, [], []
        for prefix in self.prefixes:
            while stack and stack[-1].last < prefix.first:
                stack.pop()
            self.parent[prefix.id] = stack[-1] if stack else None
            (self.children[stack[-1].id] if stack else self.roots).append(prefix)
            stack.append(prefix)
        self.by_id = {p.id: p for p in self.prefixes}

    def depth(self, prefix):
        depth, node = 0, self.parent[prefix.id]
        while node:
            depth, node = depth + 1, self.parent[node.id]
        return depth

    def ancestors(self, prefix):
        result, node = [], self.parent[prefix.id]
        while node:
            result.append(node)
            node = self.parent[node.id]
        return result[::-1]

    def descendants(self, prefix):
        start = bisect_left(self.firsts, prefix.first)
        end = bisect_right(self.firsts, prefix.last)
        return [p for p in self.prefixes[start:end] if p.length > prefix.length]

    def leaves(self, prefix):
        return [p for p in [prefix, *self.descendants(prefix)] if not self.children[p.id]]

    def deepest(self, value):
        """The smallest block holding an address, found by walking down the tree."""
        found, level = None, self.roots
        while level:
            i = bisect_right([p.first for p in level], value) - 1
            if i < 0 or level[i].last < value:
                break
            found = level[i]
            level = self.children[found.id]
        return found

    def addresses_in(self, first, last):
        return self.addresses[bisect_left(self.values, first): bisect_right(self.values, last)]

    def add(self, address):
        """Keep the index current while one request allocates several addresses."""
        position = bisect_left(self.values, address.value)
        self.values.insert(position, address.value)
        self.addresses.insert(position, address)

    def reservations_in(self, first, last):
        return [r for r in self.reservations if r.first <= last and r.last >= first]

    def usage(self, prefix):
        """Leaf: addresses in use of those that can be handed out. Summary: share planned below it."""
        kids = self.children[prefix.id]
        if kids:
            planned = netmath.covered([(k.first, k.last) for k in kids], (prefix.first, prefix.last))
            total = netmath.size(prefix.first, prefix.last)
            return {"kind": "planned", "used": planned, "total": total, "percent": percent(planned, total)}
        lo, hi = netmath.usable(prefix.first, prefix.last, prefix.length)
        ranges = [(r.first, r.last) for r in self.reservations_in(lo, hi)]
        ranges += [(a.value, a.value) for a in self.addresses_in(lo, hi)]
        if prefix.gateway:
            gateway = netmath.ip_value(prefix.gateway)
            ranges.append((gateway, gateway))
        used = netmath.covered(ranges, (lo, hi))
        total = max(0, hi - lo + 1)
        return {"kind": "addresses", "used": used, "total": total, "percent": percent(used, total)}

    def address_usage(self, prefix):
        used = total = 0
        for leaf in self.leaves(prefix):
            usage = self.usage(leaf)
            used, total = used + usage["used"], total + usage["total"]
        return percent(used, total) if total else None

    def gaps(self, prefix):
        kids = self.children[prefix.id]
        if not kids:
            return []
        unit = netmath.common_length([k.length for k in kids])
        rows = []
        for gap in netmath.gaps((prefix.first, prefix.last), [(k.first, k.last) for k in kids]):
            rows.append({
                "first": netmath.ip_text(gap[0]),
                "last": netmath.ip_text(gap[1]),
                "size": netmath.size(*gap),
                "unit": unit,
                "blocks": netmath.aligned_blocks(gap, unit),
                "largest": netmath.largest_blocks(gap)[:3],
            })
        return rows


TOP = {"position": 0, "level": None, "block": None, "path": []}


class Planner:
    """What each block of a routing domain belongs to, checked against the hierarchy."""

    def __init__(self, session, index, lookup):
        self.session, self.index, self.lookup, self.cache = session, index, lookup, {}

    def level(self, prefix):
        return self.lookup.levels.get(prefix.level_id)

    def effective(self, prefix):
        """Bindings from the top down to this block; raises when a block does not fit."""
        if prefix is None:
            return TOP
        if prefix.id in self.cache:
            return self.cache[prefix.id]
        above = self.effective(self.index.parent[prefix.id])
        result = {**above, "path": list(above["path"])}
        level = self.level(prefix)
        if level:
            if level.position <= above["position"]:
                raise bad(f"{prefix.network} 是“{level.title}”层级，不能划在“{above['level']}”层级的 "
                          f"{above['block']} 之下")
            result.update(position=level.position, level=level.title, block=prefix.network)
            self.bind(prefix, level, result)
        self.cache[prefix.id] = result
        return result

    def bind(self, prefix, level, result):
        kind = level.dimension
        if kind == "custom":
            if not prefix.label:
                raise bad(f"{prefix.network} 是“{level.title}”层级，请填写名称")
            result["path"].append({"level": level.title, "dimension": kind, "id": None,
                                   "name": prefix.label, "prefix_id": prefix.id})
            return
        ident = getattr(prefix, FIELDS[kind])
        if not ident:
            raise bad(f"{prefix.network} 是“{level.title}”层级，请选择{KINDS[kind]}")
        name = self.lookup.name
        for outer, value in self.lookup.implied(kind, ident):
            have = result.get(outer)
            if have and have != value:
                raise bad(f"{prefix.network} 的{KINDS[kind]}“{name(kind, ident)}”属于{KINDS[outer]}"
                          f"“{name(outer, value)}”，但上级网段归属{KINDS[outer]}“{name(outer, have)}”")
            result[outer] = value
        result["path"].append({"level": level.title, "dimension": kind, "id": ident,
                               "name": name(kind, ident), "prefix_id": prefix.id})

    def validate(self, prefix):
        """Check a block and everything below it, and the hosts using its addresses."""
        for node in [prefix, *self.index.descendants(prefix)]:
            self.effective(node)
        self.check_hosts(prefix.first, prefix.last)

    def check_hosts(self, first, last):
        linked = [a for a in self.index.addresses_in(first, last) if a.host_id]
        if not linked:
            return
        hosts = {h.id: h for h in self.session.scalars(
            select(Host).where(Host.id.in_({a.host_id for a in linked}))).all()}
        for address in linked:
            host = hosts.get(address.host_id)
            problem = host and host_problem(self.lookup, host, self.effective(self.index.deepest(address.value)))
            if problem:
                raise bad(f"{address.ip} 是主机 {host.hostname} 的地址，{problem}")

    def title(self, prefix):
        level = self.level(prefix)
        if level and level.dimension != "custom":
            return self.lookup.name(level.dimension, getattr(prefix, FIELDS[level.dimension]))
        return prefix.label or prefix.purpose

    def row(self, prefix, base_depth=0):
        index, lookup = self.index, self.lookup
        effective = self.effective(prefix)
        level = self.level(prefix)
        zone_id = effective.get("zone")
        return {
            "id": prefix.id,
            "space_id": prefix.space_id,
            "network": prefix.network,
            "length": prefix.length,
            "parent_id": index.parent[prefix.id].id if index.parent[prefix.id] else None,
            "depth": index.depth(prefix) - base_depth,
            "level": level_ref(level) if level else None,
            "title": self.title(prefix),
            "label": prefix.label,
            "purpose": prefix.purpose,
            # Every binding from the top; the last one is this block's own when it has a level.
            "path": effective["path"],
            **{field: getattr(prefix, field) for field in FIELDS.values()},
            "zone": lookup.zone_ref(zone_id, inherited=prefix.zone_id != zone_id) if zone_id else None,
            "system": lookup.ref("system", effective.get("system")),
            "subsystem": lookup.ref("subsystem", effective.get("subsystem")),
            "vlan": prefix.vlan,
            "gateway": prefix.gateway,
            "status": prefix.status,
            "owner": prefix.owner,
            "ticket": prefix.ticket,
            "note": prefix.note,
            "child_count": len(index.children[prefix.id]),
            "usage": index.usage(prefix),
            "updated_at": prefix.updated_at,
            "updated_by": prefix.updated_by,
        }


def host_problem(lookup, host, effective):
    """Why a host may not use an address of a block with these bindings, or None."""
    system = effective.get("system")
    if system != host.system_id:
        where = f"属于业务系统“{lookup.name('system', system)}”" if system else "没有划给业务系统"
        return f"它所在的网段{where}，与主机的业务系统“{lookup.name('system', host.system_id)}”不一致"
    subsystem = effective.get("subsystem")
    if host.subsystem_id and subsystem and subsystem != host.subsystem_id:
        return (f"它所在的网段属于子系统“{lookup.name('subsystem', subsystem)}”，"
                f"与主机的子系统“{lookup.name('subsystem', host.subsystem_id)}”不一致")
    return None


def planners(session, lookup=None):
    lookup = lookup or Lookup(session)
    result = {}
    for space in session.scalars(select(Space).order_by(Space.name)).all():
        index = SpaceIndex(session, space.id)
        result[space.id] = (space, index, Planner(session, index, lookup))
    return result


def validate_all(session):
    session.flush()
    for _, index, planner in planners(session).values():
        for root in index.roots:
            planner.validate(root)


def resources(actor):
    """The top blocks of every routing domain, with how far they are planned and used."""
    with sessions() as session:
        lookup = Lookup(session)
        domains = planners(session, lookup)
        items = []
        for space, index, planner in domains.values():
            for root in index.roots:
                items.append({
                    "id": root.id, "network": root.network, "title": planner.title(root),
                    "purpose": root.purpose, "status": root.status,
                    "level": level_ref(planner.level(root)) if planner.level(root) else None,
                    "space": {"id": space.id, "name": space.name},
                    "planned": index.usage(root)["percent"] if index.children[root.id] else None,
                    "usage": index.address_usage(root),
                })
        return {
            "can_edit": can_edit(actor),
            "spaces": [space_view(space) for space, _, _ in domains.values()],
            "levels": [level_ref(lv) for lv in lookup.ordered_levels()],
            "resources": items,
        }


def subtree(actor, prefix_id):
    """A block and everything carved from it, with the unplanned gaps in between."""
    with sessions() as session:
        root = session.get(Prefix, prefix_id)
        if not root:
            raise missing("网段")
        space = session.get(Space, root.space_id)
        lookup = Lookup(session)
        index = SpaceIndex(session, root.space_id)
        planner = Planner(session, index, lookup)
        base = index.depth(root)
        rows = []
        for prefix in [root, *index.descendants(root)]:
            row = planner.row(prefix, base)
            rows.append({"type": "prefix", **row})
            rows.extend({"type": "gap", "parent_id": prefix.id, "depth": row["depth"] + 1, **gap}
                        for gap in index.gaps(prefix))
        return {
            "space": space_view(space),
            "ancestors": [{"id": a.id, "network": a.network, "title": planner.title(a)}
                          for a in index.ancestors(root)],
            "usage": index.address_usage(root),
            "rows": ordered(rows),
            "can_edit": can_edit(actor),
        }


def ordered(rows):
    """Prefix rows in tree order with each gap after the last child it follows."""
    def key(row):
        first = netmath.ip_value(row["first"]) if row["type"] == "gap" else netmath.parse_network(row["network"])[0]
        return (first, row["depth"])
    return sorted(rows, key=key)


# --------------------------------------------------------------------------- carving


def suggested_length(index, parent, level):
    if level.default_length and level.default_length > parent.length:
        return level.default_length
    same = [k.length for k in index.children[parent.id] if k.level_id == level.id]
    return netmath.common_length(same) if same else min(parent.length + 1, 32)


def free_blocks(index, parent, length, limit=64):
    """How many /length blocks are still free directly under a block, and the first few."""
    step = 1 << (32 - length)
    count, candidates = 0, []
    for gap in netmath.gaps((parent.first, parent.last), [(k.first, k.last) for k in index.children[parent.id]]):
        count += netmath.aligned_blocks(gap, length)
        start = -(-gap[0] // step) * step
        while start + step - 1 <= gap[1] and len(candidates) < limit:
            candidates.append(start)
            start += step
    return count, candidates


def carve_options(actor, prefix_id, level_id=None, length=None):
    """What can be carved from a block: the levels below it and the free blocks of a size."""
    with sessions() as session:
        parent = session.get(Prefix, prefix_id)
        if not parent:
            raise missing("网段")
        lookup = Lookup(session)
        index = SpaceIndex(session, parent.space_id)
        planner = Planner(session, index, lookup)
        position = planner.effective(parent)["position"]
        below = [lv for lv in lookup.ordered_levels() if lv.position > position]
        if level_id and level_id not in {lv.id for lv in below}:
            raise bad("这个层级不能划在当前网段之下")
        chosen = next((lv for lv in below if lv.id == level_id), below[0] if below and not level_id else None)
        size = length or (suggested_length(index, parent, chosen) if chosen else min(parent.length + 1, 32))
        if not parent.length < size <= 32:
            raise bad(f"新网段要比 {parent.network} 小，请选择 /{parent.length + 1} 到 /32")
        count, candidates = free_blocks(index, parent, size)
        return {
            "parent": planner.row(parent),
            "levels": [{**level_ref(lv), "suggested_length": suggested_length(index, parent, lv)} for lv in below],
            "level_id": chosen.id if chosen else None,
            "length": size,
            "count": count,
            "candidates": [f"{netmath.ip_text(c)}/{size}" for c in candidates],
        }


# --------------------------------------------------------------------------- prefixes


PREFIX_NAMES = {
    "level_id": "层级", "region_id": "地域", "site_id": "机房", "zone_id": "网络区域",
    "system_id": "业务系统", "subsystem_id": "子系统", "label": "名称", "purpose": "用途",
    "vlan": "VLAN", "gateway": "网关", "status": "状态", "owner": "负责人",
    "ticket": "申请工单号", "note": "备注",
}
REFS = {"层级": "level", "地域": "region", "机房": "site", "网络区域": "zone", "业务系统": "system", "子系统": "subsystem"}


def readable(lookup, changes):
    """Show bound objects by name in the change log."""
    for change in changes:
        kind = REFS.get(change["field"])
        if kind:
            for key in ("before", "after"):
                if change[key]:
                    change[key] = lookup.name(kind, change[key])
    return changes


def prefix_values(session, body: PrefixFields, first, last, length):
    """Stored fields: only the binding of the block's own level is kept."""
    level = None
    if body.level_id:
        level = session.get(Level, body.level_id)
        if not level:
            raise missing("规划层级")
    values = body.model_dump(include=set(PREFIX_NAMES))
    for kind, field in FIELDS.items():
        if not level or level.dimension != kind:
            values[field] = None
    values["gateway"] = None
    if body.gateway:
        try:
            gateway = netmath.ip_value(body.gateway)
        except ValueError as exc:
            raise bad(str(exc)) from None
        lo, hi = netmath.usable(first, last, length)
        if not lo <= gateway <= hi:
            raise bad(f"网关 {body.gateway} 不在这个网段可用地址范围内")
        values["gateway"] = netmath.ip_text(gateway)
    return values


def create_prefix(actor, body: PrefixIn):
    require_editor(actor)
    with sessions() as session:
        prefix = add_prefix(session, actor, body)
        return {"id": prefix.id, "network": prefix.network}


def add_prefix(session, actor, body: PrefixIn):
    """Create a block inside an open session, checked against the tree and logged."""
    parent = None
    if body.parent_id:
        parent = session.get(Prefix, body.parent_id)
        if not parent:
            raise missing("上级网段")
        space_id = parent.space_id
    else:
        space_id = resolve_space(session, body.space_id)
    if body.network:
        try:
            first, last, length, network = netmath.parse_network(body.network)
        except ValueError as exc:
            raise bad(str(exc)) from None
        if parent and not (parent.first <= first and last <= parent.last and length > parent.length):
            raise bad(f"{network} 不在 {parent.network} 之内")
    else:
        if body.length <= parent.length:
            raise bad(f"新网段要比 {parent.network} 小，请选择 /{parent.length + 1} 到 /32")
        _, candidates = free_blocks(SpaceIndex(session, space_id), parent, body.length, limit=1)
        if not candidates:
            raise bad(f"{parent.network} 里已经没有空闲的 /{body.length}")
        first, length = candidates[0], body.length
        last = first + (1 << (32 - length)) - 1
        network = f"{netmath.ip_text(first)}/{length}"
    if session.scalars(select(Prefix).where(
        Prefix.space_id == space_id, Prefix.first == first, Prefix.length == length
    )).first():
        raise bad(f"已有网段 {network}")
    prefix = Prefix(
        id=new_id(), space_id=space_id, network=network, first=first, last=last, length=length,
        updated_at=now(), updated_by=actor.username, **prefix_values(session, body, first, last, length),
    )
    session.add(prefix)
    session.flush()
    lookup = Lookup(session)
    index = SpaceIndex(session, space_id)
    planner = Planner(session, index, lookup)
    if parent and index.parent[prefix.id] is not parent:
        raise bad(f"{network} 落在已有的 {index.parent[prefix.id].network} 里，请在那一级划分")
    planner.validate(prefix)
    detail = [{"field": "层级", "before": None, "after": planner.level(prefix).title}] if prefix.level_id else []
    title = planner.title(prefix)
    if title:
        detail.append({"field": "归属" if prefix.level_id else "名称", "before": None, "after": title})
    record(session, actor, "网段", prefix.id, f"网段 {network}", "新增", detail)
    return prefix


def update_prefix(actor, prefix_id, body: PrefixFields):
    require_editor(actor)
    with sessions() as session:
        prefix = session.get(Prefix, prefix_id)
        if not prefix:
            raise missing("网段")
        changes = diff(prefix, prefix_values(session, body, prefix.first, prefix.last, prefix.length), PREFIX_NAMES)
        if changes:
            session.flush()
            lookup = Lookup(session)
            Planner(session, SpaceIndex(session, prefix.space_id), lookup).validate(prefix)
            prefix.updated_at, prefix.updated_by = now(), actor.username
            record(session, actor, "网段", prefix.id, f"网段 {prefix.network}", "修改", readable(lookup, changes))
        return {"id": prefix.id}


def delete_prefix(actor, prefix_id):
    require_editor(actor)
    with sessions() as session:
        prefix = session.get(Prefix, prefix_id)
        if not prefix:
            raise missing("网段")
        user = session.scalars(select(Circuit).where(Circuit.prefix_id == prefix_id)).all()
        live = [c for c in user if c.status != "已拆除"]
        if live:
            raise bad(f"{prefix.network} 是专线 {live[0].cid} 的互联网段，请先在专线里修改")
        index = SpaceIndex(session, prefix.space_id)
        if not index.children[prefix.id]:
            # A leaf's allocations would fall to its parent or nowhere; release them first.
            addresses = len(index.addresses_in(prefix.first, prefix.last))
            reserved = len(index.reservations_in(prefix.first, prefix.last))
            if addresses or reserved:
                raise bad(f"网段内还有 {addresses} 个已分配地址、{reserved} 段保留范围，请先释放")
        for circuit in user:
            circuit.prefix_id = None
        lookup = Lookup(session)
        title = Planner(session, index, lookup).title(prefix)
        session.delete(prefix)
        session.flush()
        # Blocks below move up a level and lose this block's binding; their hosts must still fit.
        Planner(session, SpaceIndex(session, prefix.space_id), lookup).check_hosts(prefix.first, prefix.last)
        record(session, actor, "网段", prefix.id, f"网段 {prefix.network}", "删除",
               [{"field": "归属" if prefix.level_id else "名称", "before": title, "after": None}] if title else None)
    return {"deleted": True}


def prefix_detail(actor, prefix_id):
    with sessions() as session:
        prefix = session.get(Prefix, prefix_id)
        if not prefix:
            raise missing("网段")
        space = session.get(Space, prefix.space_id)
        lookup = Lookup(session)
        index = SpaceIndex(session, prefix.space_id)
        planner = Planner(session, index, lookup)
        view = planner.row(prefix)
        view["space"] = {"id": space.id, "name": space.name}
        view["ancestors"] = [{"id": a.id, "network": a.network, "title": planner.title(a),
                              "level": planner.level(a).title if planner.level(a) else None}
                             for a in index.ancestors(prefix)]
        view["can_edit"] = can_edit(actor)
        addresses = index.addresses_in(prefix.first, prefix.last)
        hosts, circuits = owners(session, addresses)
        kids = index.children[prefix.id]
        if kids:
            view["child_rows"] = ordered(
                [{"type": "prefix", **planner.row(k)} for k in kids]
                + [{"type": "gap", "parent_id": prefix.id, "depth": view["depth"] + 1, **g}
                   for g in index.gaps(prefix)]
            )
            # Addresses recorded on the summary itself, outside every child prefix.
            stray = [a for a in addresses if not any(k.first <= a.value <= k.last for k in kids)]
            view["stray_addresses"] = [address_view(a, hosts, circuits) for a in stray[:200]]
            return view
        lo, hi = netmath.usable(prefix.first, prefix.last, prefix.length)
        view["usable"] = {"first": netmath.ip_text(lo), "last": netmath.ip_text(hi)} if lo <= hi else None
        view["first_ip"], view["last_ip"] = netmath.ip_text(prefix.first), netmath.ip_text(prefix.last)
        view["addresses"] = [address_view(a, hosts, circuits) for a in addresses[:5000]]
        view["addresses_truncated"] = len(addresses) > 5000
        view["reservations"] = [reservation_view(r) for r in index.reservations_in(prefix.first, prefix.last)]
        reserved = netmath.covered([(r.first, r.last) for r in index.reservations_in(lo, hi)], (lo, hi))
        allocated = sum(1 for a in addresses if lo <= a.value <= hi)
        gateway = 1 if prefix.gateway else 0
        view["stats"] = {
            "usable": max(0, hi - lo + 1),
            "allocated": allocated + gateway,
            "reserved": reserved,
            "free": max(0, hi - lo + 1 - view["usage"]["used"]),
        }
        try:
            view["next_free"] = netmath.ip_text(next_free(index, prefix))
        except HTTPException:
            view["next_free"] = None
        return view


def hosts_of(session, addresses):
    ids = {a.host_id for a in addresses if a.host_id}
    return {h.id: h for h in session.scalars(select(Host).where(Host.id.in_(ids))).all()} if ids else {}


def circuits_of(session, addresses):
    """The circuit end each address is the interconnect address of: address id -> circuit ref."""
    ids = [a.id for a in addresses]
    if not ids:
        return {}
    rows = session.execute(
        select(Termination.address_id, Termination.side, Circuit.id, Circuit.cid)
        .join(Circuit, Circuit.id == Termination.circuit_id).where(Termination.address_id.in_(ids))
    ).all()
    return {address_id: {"id": circuit_id, "cid": cid, "side": side} for address_id, side, circuit_id, cid in rows}


def owners(session, addresses):
    return hosts_of(session, addresses), circuits_of(session, addresses)


def address_view(address, hosts=None, circuits=None):
    host = (hosts or {}).get(address.host_id)
    return {
        **{key: getattr(address, key) for key in (
            "id", "ip", "kind", "hostname", "dns_name", "note", "assigned_at", "assigned_by")},
        "host": {"id": host.id, "hostname": host.hostname, "status": host.status} if host else None,
        "circuit": (circuits or {}).get(address.id),
    }


def reservation_view(reservation):
    return {
        "id": reservation.id,
        "first": netmath.ip_text(reservation.first),
        "last": netmath.ip_text(reservation.last),
        "size": netmath.size(reservation.first, reservation.last),
        "purpose": reservation.purpose,
        "created_at": reservation.created_at,
        "created_by": reservation.created_by,
    }


# --------------------------------------------------------------------------- allocation


def taken(index, prefix):
    """Why an address of a leaf prefix cannot be handed out: value -> reason."""
    reasons = {a.value: f"已分配给 {a.hostname or a.kind}" for a in index.addresses_in(prefix.first, prefix.last)}
    if prefix.gateway:
        reasons[netmath.ip_value(prefix.gateway)] = "是网关地址"
    return reasons


def next_free(index, prefix, skip=()):
    if index.children[prefix.id]:
        raise bad("这个网段已划分子网段，请在子网段里分配地址")
    lo, hi = netmath.usable(prefix.first, prefix.last, prefix.length)
    used = taken(index, prefix)
    reserved = netmath.merge([(r.first, r.last) for r in index.reservations_in(lo, hi)])
    value = lo
    while value <= hi:
        block = next((r for r in reserved if r[0] <= value <= r[1]), None)
        if block:
            value = block[1] + 1
            continue
        if value not in used and value not in skip:
            return value
        value += 1
    raise bad("这个网段已经没有空闲地址")


def check_free(index, prefix, value):
    """Raise unless an address of a leaf prefix can be handed out."""
    if prefix.status in CLOSED:
        raise bad(f"网段 {prefix.network} 状态为“{prefix.status}”，不能分配地址")
    if index.children[prefix.id]:
        raise bad(f"{netmath.ip_text(value)} 不在 {prefix.network} 的任何子网段里，请在子网段里分配地址")
    lo, hi = netmath.usable(prefix.first, prefix.last, prefix.length)
    if not lo <= value <= hi:
        raise bad(f"{netmath.ip_text(value)} 不在 {prefix.network} 的可用地址范围内")
    reason = taken(index, prefix).get(value)
    if reason:
        raise bad(f"{netmath.ip_text(value)} {reason}")
    hold = next(iter(index.reservations_in(value, value)), None)
    if hold:
        raise bad(f"{netmath.ip_text(value)} 在保留范围内（{hold.purpose}）")


def allocate(actor, prefix_id, body: AllocateIn):
    require_editor(actor)
    with sessions() as session:
        prefix = session.get(Prefix, prefix_id)
        if not prefix:
            raise missing("网段")
        if prefix.status in CLOSED:
            raise bad(f"网段状态为“{prefix.status}”，不能分配地址")
        index = SpaceIndex(session, prefix.space_id)
        if body.ip:
            try:
                value = netmath.ip_value(body.ip)
            except ValueError as exc:
                raise bad(str(exc)) from None
            if index.children[prefix.id]:
                raise bad("这个网段已划分子网段，请在子网段里分配地址")
            check_free(index, prefix, value)
        else:
            value = next_free(index, prefix)
        address = Address(
            id=new_id(), space_id=prefix.space_id, value=value, ip=netmath.ip_text(value),
            assigned_at=now(), assigned_by=actor.username, **body.model_dump(exclude={"ip"}),
        )
        session.add(address)
        record(session, actor, "IP 地址", address.id, f"IP {address.ip}", "分配",
               [{"field": "网段", "before": None, "after": prefix.network},
                {"field": "用途", "before": None, "after": address.kind}]
               + ([{"field": "主机名", "before": None, "after": address.hostname}] if address.hostname else []))
        return address_view(address)


ADDRESS_NAMES = {"kind": "用途", "hostname": "主机名", "dns_name": "DNS 名", "note": "备注"}


def update_address(actor, address_id, body: AddressFields):
    require_editor(actor)
    with sessions() as session:
        address = session.get(Address, address_id)
        if not address:
            raise missing("IP 地址")
        values = body.model_dump()
        hosts, circuits = owners(session, [address])
        if address.host_id or address.id in circuits:
            # A host's or circuit's address is named after its owner.
            values["hostname"], values["kind"] = address.hostname, address.kind
        changes = diff(address, values, ADDRESS_NAMES)
        if changes:
            record(session, actor, "IP 地址", address.id, f"IP {address.ip}", "修改", changes)
        return address_view(address, hosts, circuits)


def release_address(actor, address_id):
    require_editor(actor)
    with sessions() as session:
        address = session.get(Address, address_id)
        if not address:
            raise missing("IP 地址")
        if address.host_id:
            host = session.get(Host, address.host_id)
            raise bad(f"{address.ip} 是主机 {host.hostname if host else ''} 的地址，请先在主机台账里移除")
        circuit = circuits_of(session, [address]).get(address.id)
        if circuit:
            raise bad(f"{address.ip} 是专线 {circuit['cid']} 的互联地址，请在专线里修改")
        session.delete(address)
        record(session, actor, "IP 地址", address.id, f"IP {address.ip}", "释放",
               [{"field": "主机名", "before": address.hostname, "after": None}] if address.hostname else None)
    return {"released": True}


def reserve(actor, prefix_id, body: ReservationIn):
    require_editor(actor)
    with sessions() as session:
        prefix = session.get(Prefix, prefix_id)
        if not prefix:
            raise missing("网段")
        index = SpaceIndex(session, prefix.space_id)
        if index.children[prefix.id]:
            raise bad("这个网段已划分子网段，请在子网段里保留地址")
        try:
            first, last = netmath.ip_value(body.first), netmath.ip_value(body.last)
        except ValueError as exc:
            raise bad(str(exc)) from None
        if first > last:
            raise bad("起始地址不能大于结束地址")
        lo, hi = netmath.usable(prefix.first, prefix.last, prefix.length)
        if first < lo or last > hi:
            raise bad(f"保留范围必须在 {netmath.ip_text(lo)} – {netmath.ip_text(hi)} 之内")
        overlap = index.reservations_in(first, last)
        if overlap:
            raise bad(f"与已有保留范围重叠（{overlap[0].purpose}）")
        busy = index.addresses_in(first, last)
        if busy:
            raise bad(f"范围内已有 {len(busy)} 个已分配地址，例如 {busy[0].ip}")
        reservation = Reservation(
            id=new_id(), space_id=prefix.space_id, first=first, last=last, purpose=body.purpose,
            created_at=now(), created_by=actor.username,
        )
        session.add(reservation)
        label = f"保留 {netmath.ip_text(first)} – {netmath.ip_text(last)}"
        record(session, actor, "保留范围", reservation.id, label, "新增",
               [{"field": "用途", "before": None, "after": body.purpose}])
        return reservation_view(reservation)


def unreserve(actor, reservation_id):
    require_editor(actor)
    with sessions() as session:
        reservation = session.get(Reservation, reservation_id)
        if not reservation:
            raise missing("保留范围")
        session.delete(reservation)
        label = f"保留 {netmath.ip_text(reservation.first)} – {netmath.ip_text(reservation.last)}"
        record(session, actor, "保留范围", reservation.id, label, "删除",
               [{"field": "用途", "before": reservation.purpose, "after": None}])
    return {"deleted": True}


# --------------------------------------------------------------------------- zones page


def zones_page(actor):
    with sessions() as session:
        lookup = Lookup(session)
        zones = session.scalars(select(Zone).order_by(Zone.name)).all()
        by_zone = {z.id: {"prefixes": [], "used": 0, "total": 0, "leaves": 0} for z in zones}
        for space, index, planner in planners(session, lookup).values():
            for prefix in index.prefixes:
                zone_id = planner.effective(prefix).get("zone")
                if zone_id not in by_zone:
                    continue
                entry = by_zone[zone_id]
                if prefix.zone_id == zone_id:
                    entry["prefixes"].append({"id": prefix.id, "network": prefix.network, "space": space.name})
                if not index.children[prefix.id]:
                    usage = index.usage(prefix)
                    entry["used"] += usage["used"]
                    entry["total"] += usage["total"]
                    entry["leaves"] += 1
        return {
            "can_edit": can_edit(actor),
            "zones": [{
                **zone_view(z),
                "where": lookup.zone_ref(z.id),
                "prefixes": by_zone[z.id]["prefixes"],
                "subnets": by_zone[z.id]["leaves"],
                "usage": percent(by_zone[z.id]["used"], by_zone[z.id]["total"]) if by_zone[z.id]["total"] else None,
            } for z in zones],
        }


# --------------------------------------------------------------------------- locating and search


def locate(session, value, domains=None):
    """Where an address sits in every routing domain that plans it."""
    found = []
    for space, index, planner in (domains or planners(session)).values():
        block = index.deepest(value)
        address = next(iter(index.addresses_in(value, value)), None)
        if block or address:
            found.append({"space": space, "index": index, "planner": planner, "block": block, "address": address})
    return found


def search(actor, query):
    text = (query or "").strip()
    if not text:
        return {"query": text, "results": []}
    with sessions() as session:
        lookup = Lookup(session)
        domains = planners(session, lookup)
        names = {space.id: space.name for space, _, _ in domains.values()}
        try:
            value = netmath.ip_value(text)
        except ValueError:
            value = None
        if value is not None:
            results = []
            for hit in locate(session, value, domains):
                index, planner, block, address = hit["index"], hit["planner"], hit["block"], hit["address"]
                hold = next(iter(index.reservations_in(value, value)), None)
                results.append({
                    "kind": "ip", "space": {"id": hit["space"].id, "name": hit["space"].name},
                    "prefix": planner.row(block) if block else None,
                    "address": address_view(address, *owners(session, [address])) if address else None,
                    "reservation": reservation_view(hold) if hold else None,
                })
            return {"query": text, "type": "ip", "results": results}
        try:
            first, last, _, network = netmath.parse_network(text)
        except ValueError:
            first = None
        if first is not None:
            rows = session.scalars(select(Prefix).where(Prefix.first <= last, Prefix.last >= first)
                                   .order_by(Prefix.first, Prefix.length).limit(100)).all()
            return {"query": network, "type": "network", "results": [
                {"kind": "prefix", "space": {"id": p.space_id, "name": names.get(p.space_id)},
                 "id": p.id, "network": p.network, "title": domains[p.space_id][2].title(p), "status": p.status,
                 "relation": "相同" if (p.first, p.last) == (first, last)
                 else ("包含它" if p.first <= first and last <= p.last else "在它之内")}
                for p in rows]}
        like = f"%{text}%"
        prefixes = session.scalars(select(Prefix).where(or_(
            Prefix.label.ilike(like), Prefix.purpose.ilike(like), Prefix.ticket.ilike(like),
            Prefix.owner.ilike(like), Prefix.note.ilike(like))).order_by(Prefix.first).limit(50)).all()
        # Blocks named after a bound object are found by that object's name.
        matched = {kind: {i for i, row in rows.items() if text.lower() in row.name.lower()}
                   for kind, rows in lookup.objects.items()}
        bound_rows = session.scalars(select(Prefix).where(or_(*(
            getattr(Prefix, FIELDS[kind]).in_(ids) for kind, ids in matched.items() if ids
        ))).order_by(Prefix.first).limit(50)).all() if any(matched.values()) else []
        seen, blocks = set(), []
        for p in [*bound_rows, *prefixes]:
            if p.id not in seen:
                seen.add(p.id)
                blocks.append(p)
        addresses = session.scalars(select(Address).where(or_(
            Address.hostname.ilike(like), Address.dns_name.ilike(like), Address.note.ilike(like)))
            .order_by(Address.value).limit(50)).all()
        hosts, circuits = owners(session, addresses)
        found = session.scalars(select(Circuit).where(Circuit.cid.ilike(like)).order_by(Circuit.cid).limit(30)).all()
        return {"query": text, "type": "text", "results": [
            *({"kind": "circuit", "space": None, "id": c.id, "cid": c.cid, "status": c.status} for c in found),
            *({"kind": "prefix", "space": {"id": p.space_id, "name": names.get(p.space_id)}, "id": p.id,
               "network": p.network, "title": domains[p.space_id][2].title(p), "status": p.status,
               "level": lookup.name("level", p.level_id) if p.level_id else None} for p in blocks),
            *({"kind": "address", "space": {"id": a.space_id, "name": names.get(a.space_id)},
               **address_view(a, hosts, circuits)} for a in addresses),
        ]}


# --------------------------------------------------------------------------- change log


def changes(actor, entity=None, query=None, limit=50, offset=0):
    with sessions() as session:
        statement = select(Change).order_by(Change.at.desc())
        if entity:
            # Sites were called 机房 before circuits came; their older entries stay under that name.
            statement = statement.where(Change.entity.in_(("站点", "机房") if entity == "站点" else (entity,)))
        if query:
            like = f"%{query.strip()}%"
            statement = statement.where(or_(Change.label.ilike(like), Change.actor_name.ilike(like)))
        total = session.scalar(select(func.count()).select_from(statement.order_by(None).subquery()))
        rows = session.scalars(statement.offset(offset).limit(min(limit, 200) + 1)).all()
        return {
            "items": [{key: getattr(c, key) for key in (
                "id", "at", "actor_name", "entity", "entity_id", "label", "action", "detail")}
                for c in rows[:limit]],
            "more": len(rows) > limit, "total": total,
        }
