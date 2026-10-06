"""ACL, NAT and policy-routing definitions against BR1's replies and the CLI of the same
moment (tests/fixtures/netconf/h3c-comware7/br1-policy), compared as topology facts."""

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from src.apps.netconf_api.backend import mapping
from src.apps.topology.backend.collectors import rules
from src.apps.topology.backend.collectors.identify import Identity
from src.apps.topology.backend.collectors.netconf import compare
from src.apps.topology.backend.collectors.parse import parse
from src.platform.device_access.netconf import check_filter

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "tests/fixtures/netconf/h3c-comware7/br1-policy"
BR1 = ROOT / "tests/fixtures/netconf/h3c-comware7/br1"
VRF = ROOT / "tests/fixtures/netconf/h3c-comware7/br2-acl-vrf"
SEEDS = ROOT / "src/apps/netconf_api/backend/seeds/h3c_comware7.yaml"
COMWARE = Identity(vendor="H3C", os_family="comware")
CASES = {
    "acl_rules": ("h3c-comware7.acl-rules", "cli-display-acl-all.txt"),
    "acl_bindings": ("h3c-comware7.acl-bindings", "cli-display-packet-filter-interface.txt"),
    "nat_rules": ("h3c-comware7.nat-rules", "cli-display-nat-all.txt"),
    "nat_bindings": ("h3c-comware7.nat-bindings", "cli-display-nat-all.txt"),
    "pbr_bindings": ("h3c-comware7.pbr-bindings", "cli-display-ip-policy-based-route-setup.txt"),
    "pbr_rules": ("h3c-comware7.pbr-rules", "cli-display-ip-policy-based-route.txt"),
}


@pytest.fixture(scope="module")
def definitions():
    return mapping.load_all(yaml.safe_load(SEEDS.read_text(encoding="utf-8"))["definitions"])


def replies(**changed):
    files = {
        "Ifmgr": BR1 / "ifmgr-interfaces.xml",
        "ACL": POLICY / "acl.xml",
        "NAT": POLICY / "nat.xml",
        "PBR": POLICY / "pbr.xml",
    }
    asked = []

    def fetch(definition, subtree):
        assert check_filter(subtree) == subtree
        asked.append(subtree)
        module = subtree.split("><", 1)[1].split(">", 1)[0].lstrip("<")
        return changed.get(module) or files[module].read_text(encoding="utf-8")

    fetch.asked = asked
    return fetch


def read(definitions, capability, fetch=None):
    chosen = rules.selection(COMWARE, capability)
    definition_id, _ = CASES[capability]
    mapped = mapping.collect(definitions, definition_id, fetch or replies(), fields=chosen.fields)
    return chosen, mapped


def facts(chosen, mapped, capability):
    data = SimpleNamespace(
        rows=mapped.rows,
        refs=mapped.refs,
        problems=[vars(problem) for problem in mapped.problems],
        read=mapped.read,
    )
    return rules.project_netconf(COMWARE, capability, chosen, data, CASES[capability][0])


@pytest.mark.parametrize("capability", sorted(CASES))
def test_netconf_facts_are_the_cli_facts(definitions, capability):
    chosen, mapped = read(definitions, capability)
    assert mapped.problems == []
    netconf = facts(chosen, mapped, capability)
    cli = parse(COMWARE, capability, (POLICY / CASES[capability][1]).read_text(encoding="utf-8"))
    assert (cli.status, netconf.status) == ("complete", "complete")
    comparison = compare(cli, netconf)
    if capability != "acl_rules":
        assert comparison["status"] == "same", comparison
        return
    # One rule differs only in how its unknown condition is written: both leave it out of
    # the computation (the CLI's "dscp ef", NETCONF's leaf).
    assert comparison["differing"] == 2
    [left], [right] = comparison["only_cli"], comparison["only_netconf"]
    assert left["unsupported"] == ["condition:dscp ef"]
    assert right["unsupported"] == ["condition:DSCP=46"]
    assert {**left, "unsupported": None} == {**right, "unsupported": None}


def test_an_auto_order_acl_is_listed_in_its_match_order(definitions):
    chosen, mapped = read(definitions, "acl_rules")
    rows = [
        (row["LINE_NUM"], row["MATCH_ORDER"]) for row in mapped.rows if row["ACL_NAME"] == "3991"
    ]
    assert rows == [("5", "auto"), ("0", "auto")]  # by MatchIndex, not RuleID
    cli = parse(COMWARE, "acl_rules", (POLICY / "cli-display-acl-all.txt").read_text("utf-8"))
    assert [r["seq"] for r in cli.records if r["acl"] == "3991"] == [5, 0]


def test_rule_conditions_and_states(definitions):
    chosen, mapped = read(definitions, "acl_rules")
    rule = {(row["ACL_NAME"], row["LINE_NUM"]): row for row in mapped.rows}
    assert (rule["3990", "60"]["TIME"], rule["3990", "60"]["STATE"]) == ("TR-DAY", "Active")
    assert (rule["3990", "70"]["TIME"], rule["3990", "70"]["STATE"]) == ("TR-OFF", "Inactive")
    assert rule["3990", "5"]["STATE"] == ""  # no time range, no state
    port = rule["3990", "10"]
    assert (port["SRC_PORT_MATCH"], port["SRC_PORT"]) == ("", "")
    assert (port["SRC_PORT_RANGE_START"], port["SRC_PORT_RANGE_END"]) == ("1000", "2000")
    assert rule["3990", "55"]["SRC_NETWORK_OBJECT_GROUP_NAME"] == "OG-T"
    assert rule["2990", "5"]["MATCHES"] == ""  # the CLI prints no zero count
    assert {row["VRF"] for row in mapped.rows} == {"--"}


def test_a_vpn_instance_condition(definitions):
    chosen = rules.selection(COMWARE, "acl_rules")
    reply = (VRF / "acl.xml").read_text(encoding="utf-8")
    mapped = mapping.collect(
        definitions, "h3c-comware7.acl-rules", replies(ACL=reply), fields=chosen.fields
    )
    assert {(row["ACL_NAME"], row["VRF"]) for row in mapped.rows} == {
        ("2999", "TESTVRF"),
        ("3999", "TESTVRF"),
    }
    cli = parse(COMWARE, "acl_rules", (VRF / "cli-display-acl.txt").read_text(encoding="utf-8"))
    assert compare(cli, facts(chosen, mapped, "acl_rules"))["status"] == "same"


def test_a_condition_no_definition_reads_keeps_the_rule_out(definitions):
    reply = (
        (POLICY / "acl.xml")
        .read_text("utf-8")
        .replace("<RuleID>45</RuleID>", "<RuleID>45</RuleID><Precedence>5</Precedence>")
    )
    chosen, mapped = read(definitions, "acl_rules", replies(ACL=reply))
    rule = next(r for r in mapped.rows if (r["ACL_NAME"], r["LINE_NUM"]) == ("3990", "45"))
    assert rule["OTHER"] == "Precedence=5"
    record = next(r for r in facts(chosen, mapped, "acl_rules").records if r["seq"] == 45)
    assert record["unsupported"] == ["condition:Precedence=5"]


def test_an_unlisted_protocol_is_a_problem_and_the_table_is_not_used(definitions):
    reply = (
        (POLICY / "acl.xml")
        .read_text("utf-8")
        .replace("<ProtocolType>47</ProtocolType>", "<ProtocolType>89</ProtocolType>")
    )
    chosen, mapped = read(definitions, "acl_rules", replies(ACL=reply))
    [problem] = mapped.problems
    assert (problem.table, problem.value, problem.kind, problem.raw) == (
        "ACL/IPv4NamedAdvanceRules/Rule",
        "PROTOCOL",
        "unmapped_code",
        "89",
    )
    assert facts(chosen, mapped, "acl_rules").status == "partial"


def test_a_net_static_mapping_is_reported_not_read_as_one_address(definitions):
    reply = (
        (POLICY / "nat.xml")
        .read_text("utf-8")
        .replace(
            "<Ipv4PrefixLength>32</Ipv4PrefixLength>", "<Ipv4PrefixLength>24</Ipv4PrefixLength>"
        )
    )
    chosen, mapped = read(definitions, "nat_rules", replies(NAT=reply))
    [problem] = mapped.problems
    assert (problem.kind, problem.value, problem.raw) == (
        "unexpected_value",
        "GlobalInfo/Ipv4PrefixLength",
        "24",
    )
    assert "Static" not in {row["KIND"] for row in mapped.rows}


def test_bindings_are_read_from_their_tables(definitions):
    _, acl = read(definitions, "acl_bindings")
    assert [
        (r["INTERFACE"], r["DIRECTION"], r["ACL_NAME"], r["DEFAULT_ACTION"]) for r in acl.rows
    ] == [
        ("GigabitEthernet5/0", "Inbound", "3990", ""),
        ("GigabitEthernet5/0", "Outbound", "T-NAMED", ""),
        ("GigabitEthernet5/0", "Inbound", "", "Permit"),
        ("GigabitEthernet5/0", "Outbound", "", "Permit"),
    ]
    _, nat = read(definitions, "nat_bindings")
    assert [(r["KIND"], r["RULE"]) for r in nat.rows] == [
        ("outbound", "2992"),
        ("outbound", "2991"),
        ("internal server", ""),
        ("internal server", ""),
        ("static", ""),  # only the interface with static NAT enabled
    ]
    _, pbr = read(definitions, "pbr_bindings")
    assert [(r["POLICY"], r["TYPE"], r["INTERFACE"]) for r in pbr.rows] == [
        ("TEST-PBR", "Forward", "GigabitEthernet5/0"),
        ("TEST-PBR", "Local", "N/A"),
    ]


def test_a_binding_that_is_not_in_effect_is_reported(definitions):
    reply = (
        (POLICY / "acl.xml")
        .read_text("utf-8")
        .replace("<AclGroupStatus>1</AclGroupStatus>", "<AclGroupStatus>2</AclGroupStatus>", 1)
    )
    _, mapped = read(definitions, "acl_bindings", replies(ACL=reply))
    [problem] = mapped.problems
    assert (problem.value, problem.kind, problem.raw) == ("STATUS", "unmapped_code", "2")


def test_one_filter_per_definition_with_whole_rows_where_every_leaf_counts(definitions):
    fetch = replies()
    read(definitions, "acl_rules", fetch)
    groups, rules_filter = fetch.asked
    assert "<NamedGroups><Group><GroupIndex /><MatchOrder /></Group></NamedGroups>" in groups
    # Both rule tables in one RPC; their rows whole, so no condition is missed.
    assert "<IPv4NamedBasicRules><Rule /></IPv4NamedBasicRules>" in rules_filter
    assert "<IPv4NamedAdvanceRules><Rule /></IPv4NamedAdvanceRules>" in rules_filter
    fetch = replies()
    read(definitions, "acl_bindings", fetch)
    assert any("<PfilterDefAction><DefaultAction /></PfilterDefAction>" in f for f in fetch.asked)
    fetch = replies()
    read(definitions, "pbr_bindings", fetch)
    assert "<PBRLocalPolicy><Ipv4PolicyName /></PBRLocalPolicy>" in fetch.asked[-1]


def test_an_unkeyed_lookup_needs_exactly_one_row(definitions):
    reply = (
        (POLICY / "acl.xml")
        .read_text("utf-8")
        .replace(
            "<DefaultAction>1</DefaultAction>",
            "<DefaultAction>1</DefaultAction><DefaultAction>2</DefaultAction>",
        )
    )
    _, mapped = read(definitions, "acl_bindings", replies(ACL=reply))
    assert {p.kind for p in mapped.problems} == {"lookup_ambiguous"}


def test_policy_nodes_are_the_cli_rows(definitions):
    chosen, mapped = read(definitions, "pbr_rules")
    cli = rules.reader().parse(
        chosen, (POLICY / "cli-display-ip-policy-based-route.txt").read_text("utf-8")
    )
    names = ("POLICY", "NODE", "MODE", "MATCH_ACL", "NEXT_HOP")
    # The CLI leaves out an empty Value, NETCONF writes it as "".
    assert [{n: row[n] for n in names} for row in mapped.rows] == [
        {n: row.get(n) or "" for n in names} for row in cli.rows
    ]
    assert [row["NODE"] for row in mapped.rows] == ["10", "20"]
    assert [row["NEXT_HOP"] for row in mapped.rows] == ["10.0.13.2", ""]
    assert not any(row.get("OTHER") for row in (*mapped.rows, *cli.rows))


def pbr_reply(old, new):
    return replies(PBR=(POLICY / "pbr.xml").read_text("utf-8").replace(old, new, 1))


NEXT_HOP = "<IpAddress>10.0.13.2</IpAddress>"


def test_a_node_with_two_next_hops_is_not_read_as_one(definitions):
    second = (
        "<ApplyNexthop><AddressType>0</AddressType><PolicyName>TEST-PBR</PolicyName>"
        "<NodeID>10</NodeID><Mode>0</Mode><VrfIndex>0</VrfIndex>"
        "<IpAddress>10.0.23.2</IpAddress><Priority>2</Priority>"
        "<NexthopDirect>false</NexthopDirect></ApplyNexthop></PBRApplyNexthop>"
    )
    chosen, mapped = read(definitions, "pbr_rules", pbr_reply("</PBRApplyNexthop>", second))
    [problem] = mapped.problems
    assert (problem.value, problem.kind) == ("NEXT_HOP", "lookup_ambiguous")
    assert facts(chosen, mapped, "pbr_rules").status == "partial"


@pytest.mark.parametrize(
    "old,new",
    [
        ("<NexthopDirect>false</NexthopDirect>", "<NexthopDirect>true</NexthopDirect>"),
        ("<VrfIndex>0</VrfIndex>", "<VrfIndex>3</VrfIndex>"),
    ],
)
def test_a_next_hop_of_another_form_is_reported(definitions, old, new):
    chosen, mapped = read(definitions, "pbr_rules", pbr_reply(old, new))
    [problem] = mapped.problems
    assert (problem.value, problem.kind, problem.raw) == (
        "NEXT_HOP",
        "lookup_unexpected",
        "TEST-PBR/10",
    )
    assert facts(chosen, mapped, "pbr_rules").status == "partial"


def test_an_ipv6_next_hop_is_not_an_ipv4_nodes(definitions):
    reply = pbr_reply(
        "<ApplyNexthop><AddressType>0</AddressType>", "<ApplyNexthop><AddressType>1</AddressType>"
    )
    _, mapped = read(definitions, "pbr_rules", reply)
    assert mapped.problems == []  # no IPv4 next hop: the node sets none
    assert [row["NEXT_HOP"] for row in mapped.rows] == ["", ""]


def test_a_node_clause_no_definition_reads_keeps_the_node_out(definitions):
    reply = pbr_reply("<NodeID>20</NodeID>", "<NodeID>20</NodeID><ApplyPrecedence>5</ApplyPrecedence>")
    chosen, mapped = read(definitions, "pbr_rules", reply)
    assert mapped.rows[1]["OTHER"] == "ApplyPrecedence=5"
    record = facts(chosen, mapped, "pbr_rules").records[1]
    assert record["unsupported"] == ["clause:ApplyPrecedence=5"]


def test_a_lookup_marked_optional_needs_a_match():
    raw = {
        **BASE,
        "parts": [
            {
                **PART,
                "values": {
                    "ACL_NAME": {"lookup": {"definition": "y", "take": "B", "optional": True}}
                },
            }
        ],
    }
    with pytest.raises(mapping.DefinitionError):
        mapping.load(raw)


BASE = {
    "id": "x.rules",
    "vendor": "H3C",
    "platforms": ["comware"],
    "check_item": "topology.acl_rules",
    "namespace": "http://www.h3c.com/netconf/data:1.0",
    "datastore": "state",
}
PART = {"path": "top/ACL/IPv4NamedBasicRules/Rule", "keys": ["GroupIndex"]}


@pytest.mark.parametrize(
    "raw",
    [
        {**BASE, **PART, "parts": [PART]},  # one-part form and parts together
        {**BASE, "parts": []},
        {**BASE, "parts": [{**PART, "values": {"ACL_TYPE": {"const": "Basic", "leaf": "X"}}}]},
        {
            **BASE,
            "parts": [{**PART, "values": {"ACL_NAME": {"leaf": "A", "when": {"B": ["1", "!2"]}}}}],
        },
        {**BASE, "parts": [{**PART, "order": {"group": "GroupIndex"}}]},
        {**BASE, "parts": [{**PART, "values": {"OTHER": "X"}, "rest": {"value": "OTHER"}}]},
        {**BASE, "parts": [{**PART, "rest": {"ignore": ["A"]}}]},
        {
            **BASE,
            "parts": [
                {
                    **PART,
                    "values": {
                        "ACL_NAME": {
                            "leaf": ["A", "B"],
                            "lookup": {"definition": "y", "match": "A", "take": "B"},
                        }
                    },
                }
            ],
        },
        {
            **BASE,
            "parts": [
                {
                    **PART,
                    "values": {
                        "ACL_NAME": {"lookup": {"definition": "y", "match": "A", "take": "B"}}
                    },
                }
            ],
        },
        {**BASE, "parts": [{**PART, "values": {"ACL_NAME": "A"}, "filter": "<top/>"}]},
        {**BASE, "parts": [{**PART, "where": {"A<": "1"}, "values": {"ACL_NAME": "A"}}]},
        {**{k: v for k, v in BASE.items() if k != "check_item"}, "parts": [PART, PART]},
    ],
)
def test_a_part_with_invalid_shape_is_refused(raw):
    with pytest.raises(mapping.DefinitionError):
        mapping.load(raw)
