"""The netconf-api runtime contract: selection by check item and device family, module
announcement, and a read over a consumer's session."""

from pathlib import Path

import yaml

from src.apps.netconf_api.backend import mapping
from src.apps.netconf_api.runtime import DefinitionReader, announced
from src.apps.rule_governance.runtime import CheckItemSelectionReader

ROOT = Path(__file__).resolve().parents[2]
BR1 = ROOT / "tests/fixtures/netconf/h3c-comware7/br1"
SEEDS = ROOT / "src/apps/netconf_api/backend/seeds/h3c_comware7.yaml"
REPLIES = {"Ifmgr": "ifmgr-interfaces.xml", "ARP": "arp.xml", "Route": "route.xml"}


def published(extra=()):
    bodies = [*yaml.safe_load(SEEDS.read_text(encoding="utf-8"))["definitions"], *extra]
    loaded = mapping.load_all(bodies)
    return lambda: {key: (definition, 3, "a" * 64) for key, definition in loaded.items()}


class Session:
    capabilities = tuple((BR1 / "hello-capabilities.txt").read_text("utf-8").split())

    def __init__(self):
        self.asked = []

    def get(self, subtree):
        self.asked.append(subtree)
        module = subtree.split("><", 1)[1].split(">", 1)[0].lstrip("<")
        return (BR1 / REPLIES[module]).read_text("utf-8")


def test_a_check_item_is_selected_for_its_device_family_with_its_lookups():
    reader = DefinitionReader(loader=published())
    chosen = reader.select(
        ["topology.arp", "topology.routes", "topology.lldp_neighbors"],
        {"vendor": "H3C", "platform": "comware"},
    )
    assert set(chosen) == {"topology.arp", "topology.routes"}
    arp = chosen["topology.arp"]
    assert (arp.ref, arp.module) == ("h3c-comware7.arp@v3", "ARP")
    assert set(arp.definitions) == {"h3c-comware7.arp", "h3c-comware7.interfaces"}
    assert reader.select(["topology.arp"], {"vendor": "Cisco", "platform": "ios"}) == {}
    assert reader.select(["topology.arp"], {"vendor": "h3c", "platform": "comware"})  # case


def test_two_definitions_for_one_family_select_neither():
    raw = yaml.safe_load(SEEDS.read_text(encoding="utf-8"))["definitions"][1]
    reader = DefinitionReader(loader=published([{**raw, "id": "h3c-comware7.arp-copy"}]))
    chosen = reader.select(
        ["topology.arp", "topology.routes"], {"vendor": "H3C", "platform": "comware"}
    )
    assert set(chosen) == {"topology.routes"}


def test_a_read_maps_rows_and_keeps_every_reply():
    reader = DefinitionReader(loader=published())
    selection = reader.select(["topology.arp"], {"vendor": "H3C", "platform": "comware"})[
        "topology.arp"
    ]
    fields = (
        CheckItemSelectionReader(source="shipped")
        .select(["topology.arp"], {"vendor": "H3C", "platform": "comware"})["topology.arp"]
        .fields
    )
    session, guarded = Session(), []
    data = reader.read(selection, session, fields=fields, guard=lambda: guarded.append(1))
    assert (data.read, len(data.rows), data.problems) == (6, 6, [])
    assert [reply["definition"] for reply in data.replies] == [
        "h3c-comware7.interfaces",
        "h3c-comware7.arp",
    ]
    assert [reply["filter"] for reply in data.replies] == session.asked
    assert len(guarded) == 2  # checked before each RPC
    assert announced(selection, Session.capabilities)
    assert not announced(selection, [c for c in Session.capabilities if "module=ARP&" not in c])
