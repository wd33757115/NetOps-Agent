"""Whether each site can still reach the core when one thing fails, from the circuits in use.

Circuits in use join sites and provider networks into a graph. Traffic passes through a
provider network only when that network carries site-to-site traffic (an MPLS VPN, or the
internet with an overlay), and through a site only when it is a core site. A site's target
is the set of core sites (for a core site, the other core sites).

A single point of failure is anything whose loss disconnects a site from its target: one
circuit, a provider, a provider network, a point of presence, a device at a site, or a
shared physical risk recorded on circuits. The first five come from what is already
recorded on the circuits; only physical risks are labelled by hand. This reads the ledger:
routing actually configured on devices is outside what it can see, so it reports what the
ledger shows and leaves the judgement to the people who run the network.
"""

from collections import Counter, defaultdict, deque

from sqlalchemy import select

from .circuits import Names, ends_of, risks_of
from .models import Address, Circuit, Host, System, Termination

ACTIVE = "在用"
KINDS = {"circuit": "专线", "provider": "运营商", "network": "运营商网络", "pop": "局端", "device": "设备", "risk": "共享风险"}
# What the ledger shows about a site: facts, not a pass or fail.
STATES = {"cut": "到不了核心站点", "single": "有单点", "clear": "没有发现单点", "idle": "没有在用专线", "none": "没有检查目标"}
MAX_PATHS, MAX_HOPS = 8, 6


class Analysis:
    def __init__(self, session):
        self.session = session
        self.names = names = Names(session)
        circuits = session.scalars(select(Circuit).where(Circuit.status == ACTIVE)).all()
        self.circuits = {c.id: c for c in circuits}
        ends = ends_of(session, list(self.circuits))
        risks = risks_of(session, list(self.circuits))
        self.ends = ends
        self.edges, self.adjacent = {}, defaultdict(list)
        self.elements = defaultdict(set)
        self.labels = {}
        for circuit in circuits:
            nodes = []
            for side in ("A", "Z"):
                end = ends.get(circuit.id, {}).get(side)
                if end and end.site_id:
                    nodes.append(("site", end.site_id))
                elif end and end.network_id:
                    nodes.append(("net", end.network_id))
                if end and end.pop_id:
                    self.add("pop", end.pop_id, circuit.id, names.pops[end.pop_id].name if end.pop_id in names.pops else end.pop_id)
                if end and end.site_id and end.device:
                    site = names.sites.get(end.site_id)
                    self.add("device", f"{end.site_id}|{end.device.strip().lower()}", circuit.id,
                             f"{site.name if site else ''} {end.device.strip()}".strip())
                if end and end.network_id and end.network_id in names.networks:
                    self.add("network", end.network_id, circuit.id, names.networks[end.network_id].name)
            provider = names.providers.get(circuit.provider_id)
            self.add("circuit", circuit.id, circuit.id, circuit.cid)
            self.add("provider", circuit.provider_id, circuit.id, provider.name if provider else circuit.provider_id)
            for risk_id in risks.get(circuit.id, []):
                if risk_id in names.risks:
                    self.add("risk", risk_id, circuit.id, names.risks[risk_id].name)
            if len(nodes) == 2 and nodes[0] != nodes[1]:
                self.edges[circuit.id] = tuple(nodes)
                self.adjacent[nodes[0]].append((circuit.id, nodes[1]))
                self.adjacent[nodes[1]].append((circuit.id, nodes[0]))
        self.core = {s.id for s in names.sites.values() if s.core}

    def add(self, kind, ident, circuit_id, label):
        self.elements[(kind, ident)].add(circuit_id)
        self.labels[(kind, ident)] = label

    # ------------------------------------------------------------------ graph

    def transit(self, node):
        kind, ident = node
        if kind == "net":
            network = self.names.networks.get(ident)
            return bool(network and network.interconnects)
        return ident in self.core

    def targets(self, site_id):
        return {("site", s) for s in self.core if s != site_id}

    def reachable(self, start, targets, removed=frozenset()):
        seen, queue = {start}, deque([start])
        while queue:
            node = queue.popleft()
            for circuit_id, other in self.adjacent[node]:
                if circuit_id in removed or other in seen:
                    continue
                if other in targets:
                    return True
                seen.add(other)
                if self.transit(other):
                    queue.append(other)
        return False

    def paths(self, start, targets, removed=frozenset()):
        found = []

        def walk(node, trail, visited):
            if len(found) >= MAX_PATHS or len(trail) >= MAX_HOPS:
                return
            for circuit_id, other in sorted(self.adjacent[node], key=lambda item: self.circuits[item[0]].cid):
                if circuit_id in removed or other in visited:
                    continue
                step = trail + [(circuit_id, other)]
                if other in targets:
                    found.append(step)
                elif self.transit(other):
                    walk(other, step, visited | {other})

        walk(start, [], {start})
        return found

    def show(self, path, hit=frozenset()):
        """A path as hops: each circuit, then the node it reaches."""
        hops = []
        for circuit_id, node in path:
            circuit = self.circuits[circuit_id]
            provider = self.names.providers.get(circuit.provider_id)
            hops.append({"kind": "circuit", "id": circuit_id, "cid": circuit.cid, "hit": circuit_id in hit,
                         "provider": provider.name if provider else ""})
            kind, ident = node
            if kind == "site":
                site = self.names.sites.get(ident)
                hops.append({"kind": "site", "id": ident, "name": site.name if site else ident})
            else:
                network = self.names.networks.get(ident)
                hops.append({"kind": "network", "id": ident, "name": network.name if network else ident})
        return hops

    # ------------------------------------------------------------------ per site

    def scope(self):
        """Sites worth reporting: core sites, and sites any circuit ends at (in use or not)."""
        if not hasattr(self, "_scope"):
            ends = set(self.session.scalars(select(Termination.site_id).where(Termination.site_id.is_not(None))).all())
            self._scope = [s for s in sorted(self.names.sites.values(), key=lambda s: (not s.core, s.name))
                           if s.core or s.id in ends]
        return self._scope

    def single_points(self, site_id, paths):
        start, targets = ("site", site_id), self.targets(site_id)
        if not paths:
            return []
        first = {circuit_id for circuit_id, _ in paths[0]}
        found = []
        for key, circuits in self.elements.items():
            if not circuits & first:
                continue
            if not self.reachable(start, targets, frozenset(circuits)):
                found.append({"kind": key[0], "id": key[1], "kind_name": KINDS[key[0]], "name": self.labels[key]})
        order = list(KINDS)
        return sorted(found, key=lambda item: (order.index(item["kind"]), item["name"]))

    def site_report(self, site):
        start, targets = ("site", site.id), self.targets(site.id)
        active = any(end.site_id == site.id for ends in self.ends.values() for end in ends.values())
        paths = self.paths(start, targets) if targets else []
        spofs = self.single_points(site.id, paths)
        if not targets:
            state, note = "none", "它是唯一的核心站点" if site.core else "还没有标注核心站点"
        elif not active:
            state, note = "idle", None
        elif not paths:
            state, note = "cut", "在用专线连不到任何核心站点"
        else:
            state, note = ("single" if spofs else "clear"), None
        region = self.names.regions.get(site.region_id)
        return {
            "site": {"id": site.id, "name": site.name, "kind": site.kind, "core": site.core,
                     "region": region.name if region else None},
            "paths": [self.show(path) for path in paths],
            "single_points": spofs,
            "state": state, "state_name": STATES[state], "note": note,
        }

    def report(self):
        return {
            "core": [{"id": s.id, "name": s.name} for s in self.names.sites.values() if s.core],
            "sites": [self.site_report(site) for site in self.scope()],
        }

    def single_points_by_circuit(self):
        """Circuit id -> names of the sites for which losing that circuit alone cuts them off."""
        found = defaultdict(list)
        for site in self.scope():
            targets = self.targets(site.id)
            paths = self.paths(("site", site.id), targets) if targets else []
            for item in self.single_points(site.id, paths):
                if item["kind"] == "circuit":
                    found[item["id"]].append(site.name)
        return dict(found)

    # ------------------------------------------------------------------ what if

    def options(self):
        grouped = defaultdict(list)
        for (kind, ident), circuits in self.elements.items():
            grouped[kind].append({"id": ident, "name": self.labels[(kind, ident)], "circuits": len(circuits)})
        return {kind: sorted(grouped.get(kind, []), key=lambda item: item["name"]) for kind in KINDS}

    def impact(self, kind, ident):
        key = (kind, ident)
        removed = frozenset(self.elements.get(key, set()))
        rows, counts = [], defaultdict(int)
        for site in self.scope():
            start, targets = ("site", site.id), self.targets(site.id)
            if not targets:
                continue
            before = self.paths(start, targets)
            if not before or not any(set(c for c, _ in path) & removed for path in before):
                counts["fine"] += 1
                continue
            after = self.paths(start, targets, removed)
            if not after:
                state = "down"
            elif len(after) == 1 and len(before) > 1:
                state = "single"
            else:
                counts["fine"] += 1
                continue
            counts[state] += 1
            rows.append({"site": {"id": site.id, "name": site.name, "kind": site.kind, "core": site.core},
                         "state": state, "paths": [self.show(path, removed) for path in before]})
        rows.sort(key=lambda row: (row["state"] != "down", row["site"]["name"]))
        circuits = []
        for circuit_id in sorted(removed, key=lambda c: self.circuits[c].cid):
            circuit = self.circuits[circuit_id]
            provider = self.names.providers.get(circuit.provider_id)
            circuits.append({"id": circuit_id, "cid": circuit.cid, "provider": provider.name if provider else "",
                             "support_phone": provider.support_phone if provider else None})
        affected = {row["site"]["id"] for row in rows}
        return {
            "element": {"kind": kind, "kind_name": KINDS.get(kind, kind), "id": ident, "name": self.labels.get(key, ident)},
            "circuits": circuits, "sites": rows, "counts": dict(counts),
            "systems": self.systems(affected),
        }

    def systems(self, site_ids):
        """Business systems with hosts at these sites, by where their addresses sit in the plan."""
        if not site_ids:
            return []
        from .service import Lookup, planners

        lookup = Lookup(self.session)
        domains = planners(self.session, lookup)
        hosts = {h.id: h for h in self.session.scalars(select(Host)).all()}
        systems = {s.id: s.name for s in self.session.scalars(select(System)).all()}
        found = defaultdict(lambda: defaultdict(set))
        for address in self.session.scalars(select(Address).where(Address.host_id.is_not(None))).all():
            if address.space_id not in domains or address.host_id not in hosts:
                continue
            _, index, planner = domains[address.space_id]
            site_id = planner.effective(index.deepest(address.value)).get("site")
            if site_id in site_ids:
                host = hosts[address.host_id]
                found[systems.get(host.system_id, "")][site_id].add(host.id)
        return [{"system": name, "sites": [{"name": self.names.sites[s].name, "hosts": len(h)} for s, h in by_site.items()]}
                for name, by_site in sorted(found.items())]

    # ------------------------------------------------------------------ one circuit

    def domains(self, circuit_id):
        """What this circuit shares with other circuits in use, kind by kind."""
        rows = []
        for (kind, ident), circuits in sorted(self.elements.items(), key=lambda item: list(KINDS).index(item[0][0])):
            if kind == "circuit" or circuit_id not in circuits:
                continue
            others = sorted(self.circuits[c].cid for c in circuits if c != circuit_id)
            rows.append({"kind": kind, "kind_name": KINDS[kind], "name": self.labels[(kind, ident)],
                         "auto": kind != "risk", "shared_with": others})
        return rows


def resilience_page(actor, query=None, kind=None, state=None, offset=0, limit=50):
    """One page of site reports. Counts by state cover the search and kind, not the state picked."""
    from .common import can_edit, sessions

    with sessions() as session:
        report = Analysis(session).report()
    text = (query or "").strip().lower()
    rows = [r for r in report["sites"] if (not text or text in r["site"]["name"].lower()) and (not kind or r["site"]["kind"] == kind)]
    counts = Counter(r["state"] for r in rows)
    picked = [r for r in rows if not state or r["state"] == state]
    return {"can_edit": can_edit(actor), "core": report["core"], "states": STATES, "counts": dict(counts),
            "all": len(rows), "total": len(picked), "sites": picked[offset:offset + limit]}


def impact_page(actor, kind=None, ident=None):
    from .common import sessions

    with sessions() as session:
        analysis = Analysis(session)
        result = {"options": analysis.options()}
        if kind and ident:
            if (kind, ident) not in analysis.elements:
                result["impact"] = {"element": {"kind": kind, "kind_name": KINDS.get(kind, kind), "id": ident,
                                                "name": ident}, "circuits": [], "sites": [], "counts": {}, "systems": []}
            else:
                result["impact"] = analysis.impact(kind, ident)
        return result
