"""NETCONF → standard records → IETF/IEEE trees on BR1/BR2's real replies.

The records are checked against the CLI printed at the same moment, the trees against
the pinned YANG modules (src/apps/topology/resources/yang).
"""

import copy
from collections import Counter
from pathlib import Path

import pytest
import yaml

from src.apps.netconf_api.backend import standard
from src.apps.rule_governance.runtime import CheckItemSelectionReader
from src.apps.topology.backend.model.route_table import query_table
from src.apps.topology.backend.model.standard_trees import project

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/fixtures/netconf/h3c-comware7"
SEED = ROOT / "src/apps/netconf_api/backend/mappings/h3c_comware7.yaml"
YANG = ROOT / "src/apps/topology/resources/yang"
NS = "{http://www.h3c.com/netconf/data:1.0}"
# The CLI's protocol and the standard identity (with the RFC 9129 route type) for it.
CLI_PROTOCOLS = {
    "Direct": ("ietf-routing:direct", None),
    "Static": ("ietf-routing:static", None),
    "O_INTRA": ("ietf-ospf:ospfv2", "intra-area"),
    "O_INTER": ("ietf-ospf:ospfv2", "inter-area"),
    "BGP": ("ietf-bgp:bgp", None),
}


@pytest.fixture(scope="module")
def mapping():
    return standard.load(yaml.safe_load(SEED.read_text(encoding="utf-8")))


def replies(device):
    return [p.read_text(encoding="utf-8") for p in sorted((FIXTURES / device).glob("*.xml"))]


@pytest.fixture(scope="module")
def br1(mapping):
    return standard.convert(mapping, replies("br1"))


def test_br1_tables_and_the_only_unlisted_code(br1):
    records, problems = br1
    assert {name: len(rows) for name, rows in records.items()} == {
        "interface": 9,
        "ipv4-address": 7,
        "ipv4-neighbor": 6,
        "route": 50,  # 47 IPv4 and 3 IPv6
        "ospf-instance": 1,
        "ospf-area": 1,
        "ospf-interface": 4,
        "ospf-neighbor": 3,
        "lldp-neighbor": 3,
        "lldp-management-address": 3,
    }
    # LoopBack0's OSPF network type 3 was seen next to nothing that names it.
    assert problems == [
        {
            "table": "ospf-interface",
            "row": 3,
            "field": "interface-type",
            "problem": "unmapped_code",
            "device_value": "3",
        }
    ]
    vip = {"interface": "GigabitEthernet1/0", "ip": "10.10.1.10", "prefix-length": 32}
    assert {**vip, "origin": "other"} in records["ipv4-address"]  # the VRRP address


def test_a_table_not_in_any_reply_is_absent_not_empty(mapping):
    records, problems = standard.convert(mapping, replies("br2"))
    assert set(records) == {"interface", "route"} and problems == []


def test_an_unlisted_code_is_reported_with_the_device_text(mapping):
    reply = (FIXTURES / "br1/ifmgr-interfaces.xml").read_text(encoding="utf-8")
    reply = reply.replace("<AdminStatus>1</AdminStatus>", "<AdminStatus>2</AdminStatus>", 1)
    records, problems = standard.convert(mapping, [reply])
    assert "enabled" not in records["interface"][0]  # not guessed as false
    assert problems == [
        {
            "table": "interface",
            "row": 0,
            "field": "enabled",
            "problem": "unmapped_code",
            "device_value": "2",
        }
    ]


def test_a_bad_mapping_is_refused_when_loaded():
    with pytest.raises(standard.MappingError):
        standard.load(
            {
                "namespace": "urn:x",
                "tables": {
                    "ipv4-address": {
                        "path": "top/A/B",
                        "fields": {
                            "interface": {
                                "leaf": "I",
                                "lookup": {
                                    "table": "interface",
                                    "match": "if-index",
                                    "take": "name",
                                },
                            }
                        },
                    }
                },
            }
        )
    with pytest.raises(standard.MappingError):
        standard.load(
            {
                "namespace": "urn:x",
                "tables": {
                    "t": {"path": "a/b", "fields": {"x": {"leaf": "L", "convert": "guess"}}}
                },
            }
        )


def _abbreviations(device):
    from defusedxml import ElementTree

    root = ElementTree.fromstring(
        (FIXTURES / device / "ifmgr-interfaces.xml").read_text(encoding="utf-8")
    )
    return {
        e.findtext(f"{NS}Name"): e.findtext(f"{NS}AbbreviatedName")
        for e in root.iter(f"{NS}Interface")
    }


@pytest.mark.parametrize("device", ["br1", "br2", "br1-policy"])
def test_ipv4_routes_match_the_cli(mapping, device):
    """Every IPv4 route row equals a row of "display ip routing-table" taken with it."""
    folder = FIXTURES / device
    texts = [p.read_text(encoding="utf-8") for p in sorted(folder.glob("*.xml"))]
    if not (folder / "ifmgr-interfaces.xml").exists():
        texts.append((FIXTURES / "br1/ifmgr-interfaces.xml").read_text(encoding="utf-8"))
    records, _ = standard.convert(mapping, texts)
    short = _abbreviations(device if (folder / "ifmgr-interfaces.xml").exists() else "br1")
    selection = CheckItemSelectionReader(source="shipped").select(
        ["topology.routes"], {"vendor": "H3C", "platform": "comware"}
    )["topology.routes"]
    parsed = CheckItemSelectionReader(source="shipped").parse(
        selection, (folder / "cli-display-ip-routing-table.txt").read_text(encoding="utf-8")
    )
    cli = Counter(
        (
            f"{row['NETWORK']}/{row['PREFIX_LENGTH']}",
            *CLI_PROTOCOLS[row["PROTOCOL"]],
            int(row["DISTANCE"]),
            row["NEXTHOP_IP"],
            row["NEXTHOP_IF"],
        )
        for row in parsed.rows
    )
    netconf = Counter(
        (
            row["destination-prefix"],
            row["source-protocol"],
            row.get("ospf-route-type"),
            row["route-preference"],
            row["next-hop-address"],
            short[row["outgoing-interface"]],
        )
        for row in records["route"]
        if ":" not in row["destination-prefix"]
    )
    assert netconf == cli and sum(cli.values()) == len(parsed.rows)


def _validate(tree):
    yangson = pytest.importorskip("yangson", reason="Install tests/topology-requirements.txt")
    content = pytest.importorskip("yangson.enumerations").ContentType.all
    model = yangson.DataModel((YANG / "device-library.json").read_text(), [str(YANG)])
    tree = copy.deepcopy(tree)
    # Test-only completion of the counters' mandatory time, which NETCONF did not read.
    for interface in tree.get("ietf-interfaces:interfaces", {}).get("interface", []):
        interface["statistics"] = {"discontinuity-time": "2000-01-01T00:00:00Z"}
    # yangson cannot load the BGP draft; BGP routes are checked with libyang (M4 README).
    for rib in tree.get("ietf-routing:routing", {}).get("ribs", {}).get("rib", []):
        rib["routes"]["route"] = [
            r for r in rib["routes"]["route"] if r["source-protocol"] != "ietf-bgp:bgp"
        ]
    model.from_raw(tree).validate(ctype=content)


@pytest.mark.parametrize("device", ["br1", "br2"])
def test_trees_validate_against_the_pinned_modules(mapping, device):
    records, _ = standard.convert(mapping, replies(device))
    trees = project(records, "comware")
    _validate(trees["device"]["tree"])
    for section in trees["control"]["routing"]:
        _validate(section["tree"])
    _validate(trees["control"]["lldp"]["tree"])


def test_br1_trees_hold_what_the_cli_could_not(br1):
    trees = project(br1[0], "comware")
    (section,) = trees["control"]["routing"]
    assert section["vrf"] == "default"
    ribs = {rib["name"]: rib for rib in section["tree"]["ietf-routing:routing"]["ribs"]["rib"]}
    assert set(ribs) == {"ipv4", "ipv6"}
    assert section["metadata"]["derived_rib_names"]["ipv4"]["network_instance"] == "default"
    routes = {
        r["ietf-ipv4-unicast-routing:destination-prefix"]: r
        for r in ribs["ipv4"]["routes"]["route"]
    }
    # OSPF routes carry RFC 9129's metric and route type (OSPFv2 is known from the code).
    ospf = [r for r in routes.values() if r["source-protocol"] == "ietf-ospf:ospfv2"]
    assert ospf and all("ietf-ospf:metric" in r and "ietf-ospf:route-type" in r for r in ospf)
    assert routes["172.16.0.0/16"]["next-hop"] == {"special-next-hop": "blackhole"}  # NULL0
    assert routes["10.0.12.1/32"]["next-hop"] == {"special-next-hop": "receive"}  # InLoop0
    assert any("next-hop-list" in r["next-hop"] for r in routes.values())  # ECMP
    # RFC 9129 instance with neighbours, and LLDP with its native keys.
    (instance,) = section["tree"]["ietf-routing:routing"]["control-plane-protocols"][
        "control-plane-protocol"
    ]
    area = instance["ietf-ospf:ospf"]["areas"]["area"][0]
    states = {
        n["neighbor-router-id"]: n["state"]
        for i in area["interfaces"]["interface"]
        for n in i.get("neighbors", {}).get("neighbor", [])
    }
    assert states == {"10.255.0.2": "full", "10.255.0.3": "full", "10.255.0.4": "full"}
    lldp = trees["control"]["lldp"]
    remote = {
        p["name"]: p["remote-systems-data"][0]
        for p in lldp["tree"]["ieee802-dot1ab-lldp:lldp"]["port"]
    }
    assert remote["GigabitEthernet5/0"]["chassis-id"] == "74-25-8A-E3-CF-E7"
    assert (
        remote["GigabitEthernet5/0"]["time-mark"],
        remote["GigabitEthernet5/0"]["remote-index"],
    ) == (20561, 1)
    # CORE1/CORE2's 258-character description exceeds IEEE's 255 octets: kept aside.
    assert "system-description" not in remote["GigabitEthernet3/0"]
    assert {v["field"] for v in lldp["metadata"]["values_not_exported"]} == {"system-description"}


def test_the_route_query_table_reads_netconf_sections(br1):
    trees = project(br1[0], "comware")
    table = query_table(trees["control"]["routing"])
    rows = {(r["rib"], r["destination-prefix"]): r for r in table["routes"]}
    default = rows[("ipv4", "0.0.0.0/0")]
    assert default["source-protocol"] == "ietf-bgp:bgp" and default["route-preference"] == 255
    assert default["next-hops"] == [{"ip": "100.64.1.1", "ifname": "GigabitEthernet2/0"}]
    assert table["missing"] == {}


def _br1_policy(mapping):
    texts = replies("br1-policy") + [
        (FIXTURES / "br1/ifmgr-interfaces.xml").read_text(encoding="utf-8")
    ]
    return standard.convert(mapping, texts)


def test_nat_tree_holds_what_display_nat_all_shows(mapping):
    records, problems = _br1_policy(mapping)
    assert problems == [] and not any("unread" in r for t in records.values() for r in t)
    nat = project(records, "comware")["control"]["nat"]
    assert [g["code"] for g in nat["gaps"]] == ["mandatory_statistics_not_observed"]
    (instance,) = nat["tree"]["ietf-nat:nat"]["instances"]["instance"]
    policies = {p["id"]: p for p in instance["policy"]}
    # "ACL: 2991  Address group ID: 9  NO-PAT: Y" with group 9 = 203.0.113.100-103.
    assert policies[2991] == {
        "id": 2991,
        "external-ip-address-pool": [{"pool-id": 9, "external-ip-pool": "203.0.113.100/30"}],
        "subscriber-match": [{"match-id": 5, "subnet": "192.0.2.0/25"}],
        "external-realm": {"external-interface": "GigabitEthernet2/0"},
    }
    # "ACL: 2992  Address group ID: ---": the interface's own address (Easy IP).
    assert "external-ip-address-pool" not in policies[2992]
    assert nat["metadata"]["translated_to_interface_address"] == [2992]
    assert nat["metadata"]["policy_types"] == {"2991": "basic-nat44", "2992": "napt44"}
    entries = {e["index"]: e for e in instance["mapping-table"]["mapping-entry"]}
    assert entries[1] == {
        "index": 1,
        "type": "static",
        "internal-src-address": "192.0.2.20/32",
        "external-src-address": "203.0.113.20/32",
    }
    servers = {
        (
            e["transport-protocol"],
            e["external-dst-address"],
            e["external-dst-port"]["start-port-number"],
        ): (
            e["internal-dst-address"],
            e["internal-dst-port"]["start-port-number"],
        )
        for e in entries.values()
        if "transport-protocol" in e
    }
    assert servers == {
        (6, "100.64.1.2/32", 8080): ("192.0.2.80/32", 80),
        (17, "203.0.113.53/32", 53): ("192.0.2.53/32", 53),
    }
    tree = copy.deepcopy(nat["tree"])
    instance = tree["ietf-nat:nat"]["instances"]["instance"][0]
    instance["statistics"] = {"discontinuity-time": "2000-01-01T00:00:00Z"}  # test-only
    _validate(tree)


def test_an_acl_with_an_exception_is_not_a_nat_subnet(mapping):
    """RFC 8512 selects subnets; a deny rule in the ACL cannot be expressed."""
    records, _ = _br1_policy(mapping)
    records = copy.deepcopy(records)
    deny = {
        "acl": "2991",
        "rule": 1,
        "action": "deny",
        "source-address": "192.0.2.1",
        "source-wildcard": "0.0.0.0",
    }
    records["acl-basic-rule"].append(deny)
    nat = project(records, "comware")["control"]["nat"]
    policies = nat["tree"]["ietf-nat:nat"]["instances"]["instance"][0]["policy"]
    assert [p["id"] for p in policies] == [2992]  # 2991 left out whole
    assert {"code": "acl_deny_rule", "record_index": 1, "acl": "2991"} in nat["gaps"]


def test_builder_rows_from_the_trees_equal_the_cli_facts(br1):
    """BR1's interfaces, addresses, ARP and LLDP for the SIMAP builder, from the trees,
    against the facts its CLI gave (campus-wan capture)."""
    import json

    from test_topology_protocols import observation

    from src.apps.topology.backend.model.standard_tables import builder_rows

    cli = observation("br1")["facts"]
    rows = builder_rows(br1[0], "comware")

    def same(key, drop=()):
        def text(row):
            return json.dumps({k: v for k, v in row.items() if k not in drop}, sort_keys=True)

        return sorted(map(text, cli[key])), sorted(map(text, rows[key]))

    assert same("ip")[0] == same("ip")[1]
    assert same("arp", drop={"age_s"})[0] == same("arp", drop={"age_s"})[1]  # NETCONF: no age
    # The 258-character description is not in the IEEE tree (255 octets at most).
    left, right = same("lldp", drop={"remote_system_description"})
    assert left == right and len(right) == 3
    # NULL0 and REG0 have no IANA type and stay out of the tree; the device's own
    # descriptions of its loopbacks are kept.
    names = {row["ifname"] for row in cli["interfaces"]} - {"NULL0", "REG0"}
    assert {row["ifname"] for row in rows["interfaces"]} == names
