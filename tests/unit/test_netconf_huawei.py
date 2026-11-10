"""Huawei YunShan NETCONF → standard records, on the eNSP Pro lab's real replies.

The lab (AR6700, USG6306F, CE6800, YunShan OS 1.25.1.1) announces no IETF data model:
every table reads Huawei's own modules, each its own XML namespace, with lists nested in
an interface. The records are checked against the CLI the devices printed.
"""

import re
from pathlib import Path

import pytest
import yaml

from src.apps.netconf_api.backend import standard

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/fixtures/netconf/huawei-yunshan"
MAPPING = ROOT / "src/apps/netconf_api/backend/mappings/huawei_yunshan.yaml"
IFM = "urn:huawei:yang:huawei-ifm"


@pytest.fixture(scope="module")
def mapping():
    return standard.load(yaml.safe_load(MAPPING.read_text(encoding="utf-8")))


def replies(device):
    """{table: [reply]}, each table read from the reply to its own filter."""
    return {p.stem: [p.read_text(encoding="utf-8")] for p in sorted((FIXTURES / device).glob("*.xml"))}


@pytest.fixture(scope="module")
def converted(mapping):
    return {device: standard.convert(mapping, replies(device)) for device in ("usg-1", "ar-1", "ce-1")}


def cli_routes(device):
    """(instance, prefix, protocol, next hop, interface) of the CLI's routing tables."""
    found = set()
    for path in sorted((FIXTURES / device).glob("cli-display_ip_routing_table*.txt")):
        instance = "default"
        for line in path.read_text(encoding="utf-8").splitlines():
            if match := re.match(r"Routing Table\s*:\s*(\S+)", line):
                instance = "default" if match[1] == "_public_" else match[1]
            pattern = r"\s*(\d+\.\d+\.\d+\.\d+/\d+)\s+(\S+)\s+\d+\s+\d+\s+\S+\s+(\S+)\s+(\S+)\s*$"
            if match := re.match(pattern, line):
                found.add((instance, match[1], match[2].lower(), match[3], match[4]))
    return found


PROTOCOLS = {"ietf-routing:direct": "direct", "ietf-ospf:ospfv2": "ospf"}


def test_the_lab_converts_without_a_problem(converted):
    for device, (records, problems) in converted.items():
        assert problems == [], device
    usg = converted["usg-1"][0]
    assert {name: len(rows) for name, rows in usg.items()} == {
        "interface": 17,
        "ipv4-address": 5,
        "ipv4-neighbor": 4,
        "route": 20,
        "ospf-instance": 1,
        "ospf-area": 1,
        "ospf-neighbor": 1,
        "lldp-neighbor": 4,
        "lldp-management-address": 3,
        "lag": 1,
        "lag-member": 2,
        "security-zone": 4,
        "nat-policy-rule": 1,
        "nat-policy-source": 1,
    }


@pytest.mark.parametrize("device", ["usg-1", "ar-1", "ce-1"])
def test_routes_equal_the_cli_route_for_route(converted, device):
    records = converted[device][0]["route"]
    netconf = {
        (
            r["network-instance"],
            r["destination-prefix"],
            PROTOCOLS[r["source-protocol"]],
            r["next-hop-address"],
            r["outgoing-interface"],
        )
        for r in records
    }
    assert netconf == cli_routes(device)


def test_a_route_not_in_use_is_left_out(converted):
    # USG lists its OSPF route to its own connected 10.0.12.0/30 as invalid (active false).
    routes = converted["usg-1"][0]["route"]
    connected = [r for r in routes if r["destination-prefix"] == "10.0.12.0/30"]
    assert [r["source-protocol"] for r in connected] == ["ietf-routing:direct"]
    assert all("receive" == r["special-next-hop"] for r in routes if r["outgoing-interface"] == "InLoopBack0")


def test_interfaces_carry_their_instance_sub_interface_trunk_and_zone(converted):
    rows = {r["name"]: r for r in converted["usg-1"][0]["interface"]}
    assert rows["MEth0/0/0"]["network-instance"] == "_management_vpn_"
    assert rows["GE0/0/0"]["network-instance"] == "default"  # the device's "_public_"
    assert rows["GE0/0/0"]["security-zone"] == "untrust"
    assert rows["GE0/0/1"]["aggregate-interface"] == "Eth-Trunk1"
    sub = rows["Eth-Trunk1.10"]
    assert (sub["parent-interface"], sub["outer-vlan-id"], sub["security-zone"]) == ("Eth-Trunk1", 10, "trust")


def test_nested_lists_name_their_interface(converted):
    usg = converted["usg-1"][0]
    addresses = {(r["interface"], r["ip"], r["prefix-length"]) for r in usg["ipv4-address"]}
    assert ("Eth-Trunk1.20", "10.1.20.1", 24) in addresses
    assert ("GE0/0/0", "10.0.12.2", 30) in addresses
    lldp = {(r["interface"], r["system-name"], r["port-id"]) for r in usg["lldp-neighbor"]}
    assert lldp >= {("GE0/0/0", "AR-1", "GE0/0/0"), ("GE0/0/1", "CE-1", "GE1/0/1"), ("GE0/0/2", "CE-1", "GE1/0/2")}
    # CE-1 advertises its management address, as IEEE 802.1AB octets.
    management = {(r["interface"], r["address"]) for r in usg["lldp-management-address"]}
    assert ("GE0/0/1", "C0A84F0D") in management  # 192.168.79.13


def test_arp_has_neighbours_only_with_their_instance(converted):
    rows = converted["usg-1"][0]["ipv4-neighbor"]
    assert {(r["interface"], r["ip"], r["network-instance"]) for r in rows} == {
        ("MEth0/0/0", "192.168.79.1", "_management_vpn_"),
        ("GE0/0/0", "10.0.12.1", "default"),
        ("Eth-Trunk1.10", "10.1.10.10", "default"),
        ("Eth-Trunk1.20", "10.1.20.10", "default"),
    }


def test_layer_two_on_the_switch(converted):
    ce = converted["ce-1"][0]
    ports = {r["name"]: r for r in ce["bridge-port"]}
    assert (ports["Eth-Trunk1"]["link-type"], ports["Eth-Trunk1"]["trunk-vlans"]) == ("trunk", "1,10,20")
    assert (ports["GE1/0/3"]["link-type"], ports["GE1/0/3"]["pvid"]) == ("access", 10)
    members = {(r["vid"], r["interface"], r["tagged"]) for r in ce["vlan-member"]}
    assert {(10, "Eth-Trunk1", True), (10, "GE1/0/3", False), (20, "GE1/0/4", False)} <= members
    assert {(r["name"], r["selected"]) for r in ce["lag-member"]} == {("GE1/0/1", True), ("GE1/0/2", True)}


def test_nat_policy_rule_with_its_zones_and_sources(converted):
    usg = converted["usg-1"][0]
    (rule,) = usg["nat-policy-rule"]
    assert rule == {
        "name": "client-easy-ip",
        "enabled": True,
        "nat-type": "nat",
        "source-zone": ["trust"],
        "destination-zone": ["untrust"],
        "action": "source-nat-easy-ip",
    }
    assert usg["nat-policy-source"] == [{"rule": "client-easy-ip", "prefix": "10.1.10.0/24"}]


def test_an_unknown_code_is_reported_not_dropped(mapping):
    texts = replies("usg-1")
    texts["interface"] = [texts["interface"][0].replace(">down</ns1:oper-status>", ">testing</ns1:oper-status>", 1)]
    records, problems = standard.convert(mapping, texts)
    assert {(p["table"], p["field"], p["problem"], p["device_value"]) for p in problems} == {
        ("interface", "oper-status", "unmapped_code", "testing")
    }
    assert any("oper-status" in r.get("problem_fields", []) for r in records["interface"])


def test_an_unread_nat_condition_is_listed(mapping):
    texts = replies("usg-1")
    texts["nat-policy-rule"] = [
        texts["nat-policy-rule"][0].replace(
            "</ns1:source-zone>",
            "</ns1:source-zone><ns1:service><ns1:service-items>http</ns1:service-items></ns1:service>",
            1,
        )
    ]
    records, _ = standard.convert(mapping, texts)
    (rule,) = records["nat-policy-rule"]
    assert rule["unread"] == ["service/service-items=http"]


def test_filters_switch_namespace_and_bring_the_owning_interface(mapping):
    tables = {table.name: table for table in mapping.tables}
    assert standard.subtree_filter(mapping, tables["ipv4-address"]) == (
        f'<ifm xmlns="{IFM}"><interfaces><interface><ipv4 xmlns="urn:huawei:yang:huawei-ip">'
        "<state><addresses><address /></addresses></state></ipv4><name /></interface></interfaces></ifm>"
    )
    route = standard.subtree_filter(mapping, tables["route"])
    assert route.startswith('<network-instance xmlns="urn:huawei:yang:huawei-network-instance"><instances><instance>')
    assert '<afs xmlns="urn:huawei:yang:huawei-l3vpn">' in route and route.endswith("<name /></instance></instances></network-instance>")


def test_namespaces_parents_lists_and_presence_in_a_small_mapping():
    mapping = standard.load(
        {
            "vendor": "Test",
            "namespace": "urn:a",
            "namespaces": {"b": "urn:b"},
            "tables": {
                "port": {
                    "path": "top/ports/port/b:extra/item",
                    "fields": {
                        "owner": "../../name",
                        "value": "value",
                        "tags": {"leaf": "tag", "list": True},
                        "flag": {"leaf": "on", "exists": True},
                        "hex": {"leaf": "address", "convert": "address-hex"},
                    },
                }
            },
        }
    )
    reply = (
        '<top xmlns="urn:a"><ports><port><name>p1</name><extra xmlns="urn:b">'
        "<item><value>1</value><tag>x</tag><tag>y</tag><on/><address>10.0.0.1</address></item>"
        "<item><value>2</value></item></extra></port></ports></top>"
    )
    records, problems = standard.convert(mapping, [reply])
    assert problems == []
    assert records["port"] == [
        {"owner": "p1", "value": "1", "tags": ["x", "y"], "flag": True, "hex": "0A000001"},
        {"owner": "p1", "value": "2"},
    ]


def test_an_undeclared_prefix_or_a_parent_step_in_a_table_path_is_refused():
    with pytest.raises(standard.MappingError, match="undeclared namespace prefix"):
        standard.load({"vendor": "T", "namespace": "urn:a", "tables": {"t": {"path": "top/x:row", "fields": {"a": "a"}}}})
    with pytest.raises(standard.MappingError, match="not a leaf path"):
        standard.load({"vendor": "T", "namespace": "urn:a", "tables": {"t": {"path": "top/../row", "fields": {"a": "a"}}}})
