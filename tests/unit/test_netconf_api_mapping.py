"""NETCONF definitions against BR1's replies and the CLI printed at the same moment
(docs/netconf-api-handoff.md §5.3)."""

from collections import Counter
from pathlib import Path

import pytest
import yaml

from src.apps.netconf_api.backend import mapping
from src.apps.rule_governance.runtime import CheckItemSelectionReader
from src.platform.device_access.netconf import check_filter

ROOT = Path(__file__).resolve().parents[2]
BR1 = ROOT / "tests/fixtures/netconf/h3c-comware7/br1"
SEEDS = ROOT / "src/apps/netconf_api/backend/seeds/h3c_comware7.yaml"
NS = "http://www.h3c.com/netconf/data:1.0"
REPLIES = {
    "h3c-comware7.interfaces": "ifmgr-interfaces.xml",
    "h3c-comware7.arp": "arp.xml",
    "h3c-comware7.routes": "route.xml",
}


@pytest.fixture(scope="module")
def definitions():
    return mapping.load_all(yaml.safe_load(SEEDS.read_text(encoding="utf-8"))["definitions"])


@pytest.fixture(scope="module")
def selections():
    return CheckItemSelectionReader(source="shipped").select(
        ["topology.arp", "topology.routes"], {"vendor": "H3C", "platform": "comware"}
    )


def reply(name):
    return (BR1 / name).read_text(encoding="utf-8")


def replay(changed=None):
    """A fetch answering from the fixtures, recording the filters asked for."""
    asked = []

    def fetch(definition, subtree):
        asked.append((definition.id, subtree))
        return (changed or {}).get(definition.id) or reply(REPLIES[definition.id])

    fetch.asked = asked
    return fetch


def test_arp_matches_the_cli(definitions, selections):
    fetch = replay()
    mapped = mapping.collect(
        definitions, "h3c-comware7.arp", fetch, fields=selections["topology.arp"].fields
    )
    # The interface table is read first, for the lookup.
    assert [name for name, _ in fetch.asked] == ["h3c-comware7.interfaces", "h3c-comware7.arp"]
    cli = []
    for line in reply("cli-display-arp.txt").splitlines()[2:]:
        ip, mac, vlan, interface, _age, kind = line.split()
        cli.append({"IP_ADDRESS": ip, "MAC_ADDRESS": mac, "INTERFACE": interface, "TYPE": kind})
    assert vlan == "--"
    assert mapped.problems == [] and mapped.read == 6
    assert sorted(mapped.rows, key=lambda row: row["IP_ADDRESS"]) == sorted(
        ({**row, "VRF": "--"} for row in cli), key=lambda row: row["IP_ADDRESS"]
    )


def test_routes_match_the_published_cli_template(definitions, selections):
    selection = selections["topology.routes"]
    mapped = mapping.collect(definitions, "h3c-comware7.routes", replay(), fields=selection.fields)
    assert mapped.problems == [] and mapped.read == len(mapped.rows) == 47
    assert {row["VRF"] for row in mapped.rows} == {""}  # the public table
    parsed = CheckItemSelectionReader(source="shipped").parse(
        selection, reply("cli-display-ip-routing-table.txt")
    )
    assert parsed.status == "complete" and len(parsed.rows) == 47
    # NETCONF names the interface in full; the interface table gives the CLI's name.
    short = {}
    for element in mapping._elements(
        definitions["h3c-comware7.interfaces"], reply("ifmgr-interfaces.xml")
    ):
        short[element.findtext(f"{{{NS}}}Name")] = element.findtext(f"{{{NS}}}AbbreviatedName")
    names = ("NETWORK", "PREFIX_LENGTH", "PROTOCOL", "DISTANCE", "METRIC", "NEXTHOP_IP")
    netconf = Counter(
        (*(row[name] for name in names), short[row["NEXTHOP_IF"]]) for row in mapped.rows
    )
    cli = Counter((*(row[name] for name in names), row["NEXTHOP_IF"]) for row in parsed.rows)
    assert netconf == cli


def test_ibgp_routes_on_br2_match_the_cli(definitions, selections):
    """BR2 learns its default and Internet routes over IBGP (code 6/1), BR1 over EBGP."""
    selection = selections["topology.routes"]
    br2 = BR1.parent / "br2"
    data = (br2 / "route.xml").read_text(encoding="utf-8")
    mapped = mapping.map_rows(definitions["h3c-comware7.routes"], data, fields=selection.fields)
    assert mapped.problems == [] and mapped.read == len(mapped.rows) == 46
    parsed = CheckItemSelectionReader(source="shipped").parse(
        selection, (br2 / "cli-display-ip-routing-table.txt").read_text(encoding="utf-8")
    )
    assert parsed.status == "complete" and len(parsed.rows) == 46
    interfaces = mapping._elements(
        definitions["h3c-comware7.interfaces"],
        (br2 / "ifmgr-interfaces.xml").read_text(encoding="utf-8"),
    )
    short = {
        e.findtext(f"{{{NS}}}Name"): e.findtext(f"{{{NS}}}AbbreviatedName") for e in interfaces
    }
    names = ("NETWORK", "PREFIX_LENGTH", "PROTOCOL", "DISTANCE", "METRIC", "NEXTHOP_IP")
    netconf = Counter(
        (*(row[name] for name in names), short[row["NEXTHOP_IF"]]) for row in mapped.rows
    )
    cli = Counter((*(row[name] for name in names), row["NEXTHOP_IF"]) for row in parsed.rows)
    assert netconf == cli
    assert Counter(row["PROTOCOL"] for row in mapped.rows)["BGP"] == 3


def test_an_unlisted_code_is_reported_not_guessed(definitions, selections):
    data = reply("route.xml").replace(
        "<ProtocolID>6</ProtocolID><SubProtocolID>6</SubProtocolID>",
        "<ProtocolID>4</ProtocolID><SubProtocolID>9</SubProtocolID>",
        1,
    )
    routes = definitions["h3c-comware7.routes"]
    loose = mapping.map_rows(routes, data)
    assert len(loose.rows) == 47 and loose.rows[0]["PROTOCOL"] == ""
    assert [(p.row, p.value, p.kind, p.raw) for p in loose.problems] == [
        (1, "PROTOCOL", "unmapped_code", "4/9")
    ]
    assert loose.problems[0].key.startswith(
        "VRF=,Ipv4/Ipv4Address=0.0.0.0,Ipv4/Ipv4PrefixLength=0,"
    )
    assert loose.rows[1:] == mapping.map_rows(routes, reply("route.xml")).rows[1:]
    # PROTOCOL is required by topology.routes: the row is not output, and says why.
    strict = mapping.map_rows(routes, data, fields=selections["topology.routes"].fields)
    assert len(strict.rows) == 46 and strict.refs[0] == 2
    assert [(p.row, p.value, p.kind) for p in strict.problems] == [
        (1, "PROTOCOL", "unmapped_code"),
        (1, "PROTOCOL", "required_missing"),
    ]


def test_filters_select_only_the_leaves_read(definitions):
    fetch = replay()
    mapping.collect(definitions, "h3c-comware7.arp", fetch)
    interfaces = dict(fetch.asked)["h3c-comware7.interfaces"]
    assert interfaces == (
        f'<top xmlns="{NS}"><Ifmgr><Interfaces><Interface>'
        "<IfIndex /><AbbreviatedName /></Interface></Interfaces></Ifmgr></top>"
    )
    for _, subtree in fetch.asked:
        assert check_filter(subtree) == subtree  # the platform takes it as it is
    routes = mapping.subtree_filter(definitions["h3c-comware7.routes"])
    assert "<Protocol><ProtocolID /><SubProtocolID /></Protocol>" in routes
    assert "<Ipv4><Ipv4Address /><Ipv4PrefixLength /></Ipv4>" in routes
    # A selected leaf already brings what is under it.
    wide = mapping.subtree_filter(definitions["h3c-comware7.routes"], ["Ipv4"])
    assert "<Ipv4 />" in wide and "<Ipv4Address />" not in wide


def test_a_lookup_that_finds_nothing_leaves_the_value_empty(definitions, selections):
    interfaces = reply("ifmgr-interfaces.xml").replace(
        "<IfIndex>49</IfIndex>", "<IfIndex>4900</IfIndex>"
    )
    mapped = mapping.collect(
        definitions,
        "h3c-comware7.arp",
        replay({"h3c-comware7.interfaces": interfaces}),
        fields=selections["topology.arp"].fields,
    )
    assert len(mapped.rows) == 6  # INTERFACE is not required
    [problem] = mapped.problems
    assert (problem.value, problem.kind, problem.raw) == ("INTERFACE", "lookup_miss", "49")
    assert problem.key == "Ipv4Address=10.0.13.2,VrfIndex=0"
    row = mapped.rows[mapped.refs.index(problem.row)]
    assert row["IP_ADDRESS"] == "10.0.13.2" and row["INTERFACE"] == ""


def test_a_lookup_table_that_disagrees_with_itself_is_not_used(definitions):
    table = mapping.Table(
        "h3c-comware7.interfaces",
        [
            {"IfIndex": "49", "AbbreviatedName": "GE3/0"},
            {"IfIndex": "49", "AbbreviatedName": "GE9/0"},
            {"IfIndex": "17", "AbbreviatedName": "GE1/0"},
            {"IfIndex": "17", "AbbreviatedName": "GE1/0"},
        ],
    )
    mapped = mapping.map_rows(
        definitions["h3c-comware7.arp"], reply("arp.xml"), tables={table.definition: table}
    )
    kinds = Counter((p.kind, p.raw) for p in mapped.problems)
    assert kinds == {
        ("lookup_ambiguous", "49"): 1,
        ("lookup_miss", "33"): 1,
        ("lookup_miss", "65"): 1,
        ("lookup_miss", "81"): 1,
    }
    assert {
        row["INTERFACE"] for row in mapped.rows if row["IP_ADDRESS"].startswith("10.10.1.")
    } == {"GE1/0"}
    with pytest.raises(mapping.MappingError):
        mapping.map_rows(definitions["h3c-comware7.arp"], reply("arp.xml"))  # table not read


def test_values_of_the_wrong_type(definitions, selections):
    data = reply("arp.xml").replace("AA-BB-CC-00-02-10", "AA-BB-CC-00-02", 1)
    arp = mapping.map_rows(
        definitions["h3c-comware7.arp"],
        data,
        fields=selections["topology.arp"].fields,
        tables={"h3c-comware7.interfaces": mapping.Table("h3c-comware7.interfaces", [])},
    )
    mac = [p for p in arp.problems if p.value == "MAC_ADDRESS"]
    assert [(p.kind, p.raw) for p in mac] == [
        ("type_mismatch", "AA-BB-CC-00-02"),
        ("required_missing", None),
    ]
    assert len(arp.rows) == 5
    data = reply("route.xml").replace(
        "<Preference>255</Preference>", "<Preference>high</Preference>", 1
    )
    routes = mapping.map_rows(
        definitions["h3c-comware7.routes"], data, fields=selections["topology.routes"].fields
    )
    assert len(routes.rows) == 47 and routes.rows[0]["DISTANCE"] == ""  # DISTANCE is optional
    assert [(p.value, p.kind, p.raw) for p in routes.problems] == [
        ("DISTANCE", "type_mismatch", "high")
    ]


def test_contract_gaps(definitions, selections):
    fields = selections["topology.routes"].fields
    assert mapping.contract_gaps(definitions["h3c-comware7.routes"], fields) == []
    raw = yaml.safe_load(SEEDS.read_text(encoding="utf-8"))["definitions"][2]
    del raw["values"]["PROTOCOL"]
    raw["values"]["COLOUR"] = "Tag"
    narrowed = mapping.load(raw)
    assert mapping.contract_gaps(narrowed, fields) == [
        "value_outside_contract:COLOUR",
        "required_value_unmapped:PROTOCOL",
    ]
    with pytest.raises(mapping.DefinitionError, match="outside"):
        mapping.map_rows(narrowed, reply("route.xml"), fields=fields)


BASE = {
    "id": "x.table",
    "vendor": "H3C",
    "platforms": ["comware"],
    "check_item": "topology.arp",
    "namespace": NS,
    "datastore": "state",
    "path": "top/ARP/ArpTable/ArpEntry",
    "keys": ["Ipv4Address"],
    "values": {"IP_ADDRESS": "Ipv4Address"},
}


@pytest.mark.parametrize(
    "change",
    [
        {"path": "top/ARP/Arp<Table/ArpEntry"},
        {"path": "top"},
        {"path": "top/x:ARP/ArpEntry"},
        {"keys": ["Ipv4 Address"]},
        {"namespace": 'urn:x" xmlns:y="urn:y'},
        {"namespace": "not a uri"},
        {"datastore": "candidate"},
        {"values": {"ip": "Ipv4Address"}},
        {"values": {"IP_ADDRESS": "../Ipv4Address"}},
        {"values": {"IP_ADDRESS": {"leaf": "A", "format": "upper"}}},
        {"values": {"IP_ADDRESS": {"leaf": ["A", "B"]}}},
        {"values": {"IP_ADDRESS": {"leaf": ["A", "B"], "codes": {"1": "x"}}}},
        {"values": {"IP_ADDRESS": {"leaf": "A", "codes": {"1": "x"}, "format": "mac_dash4"}}},
        {
            "values": {
                "IP_ADDRESS": {
                    "leaf": "A",
                    "lookup": {"definition": "x.table", "match": "A", "take": "B"},
                }
            }
        },
        {"values": {"IP_ADDRESS": {"leaf": "A", "xpath": "//*"}}},
        {"values": {}},
        {"check_item": None},
        {"filter": "<top/>"},
    ],
)
def test_a_definition_with_invalid_names_or_shape_is_refused(change):
    with pytest.raises(mapping.DefinitionError):
        mapping.load({**BASE, **change})


def test_lookups_must_name_a_loaded_definition():
    lookup = {"definition": "x.interfaces", "match": "IfIndex", "take": "AbbreviatedName"}
    raw = {
        **BASE,
        "values": {"IP_ADDRESS": "Ipv4Address", "INTERFACE": {"leaf": "IfIndex", "lookup": lookup}},
    }
    with pytest.raises(mapping.DefinitionError, match="unknown"):
        mapping.load_all([raw])
    with pytest.raises(mapping.DefinitionError, match="twice"):
        mapping.load_all([BASE, BASE])


def test_an_empty_leaf_is_no_value_and_the_reply_must_be_xml(definitions):
    routes = definitions["h3c-comware7.routes"]
    data = reply("route.xml").replace("<Nexthop>100.64.1.1</Nexthop>", "<Nexthop> </Nexthop>", 1)
    assert mapping.map_rows(routes, data).rows[0]["NEXTHOP_IP"] == ""
    # The <data> element alone (what the platform session returns) reads the same.
    inner = reply("route.xml").split("<data>", 1)[1].rsplit("</data>", 1)[0]
    assert (
        len(
            mapping.map_rows(
                routes, f'<data xmlns="urn:ietf:params:xml:ns:netconf:base:1.0">{inner}</data>'
            ).rows
        )
        == 47
    )
    with pytest.raises(mapping.MappingError):
        mapping.map_rows(routes, "show ip route")


def test_fetcher_reads_state_or_running():
    calls = []

    class Session:
        def get(self, subtree):
            calls.append(("get", subtree))
            return "<data/>"

        def get_config(self, subtree, *, source):
            calls.append(("get-config", source, subtree))
            return "<data/>"

    fetch = mapping.fetcher(Session())
    state = mapping.load(BASE)
    running = mapping.load({**BASE, "datastore": "running"})
    fetch(state, "<a/>")
    fetch(running, "<b/>")
    assert calls == [("get", "<a/>"), ("get-config", "running", "<b/>")]
