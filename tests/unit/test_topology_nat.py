"""NAT on a traced path: rules read through rule governance, applied hop by hop.

Real outputs from the campus lab (tests/fixtures/topology/campus-wan/nat): BR1 (Comware)
translates PC1's subnet to its GE2/0 address and maps 100.64.1.2:8080 to PC4:80; CORE1
(IOS) translates PC1's traffic to BR1's loopback to its Ethernet0/1 address and maps PC2
to 10.99.1.12. VPCS traffic through them matched the device NAT tables (BR1 sessions
172.16.10.11 -> 100.64.1.2; CORE1 translations 172.16.10.11 -> 10.0.13.2 and
10.99.1.12 <-> 172.16.20.12), and INET-PC reached PC4:80 through 100.64.1.2:8080.
"""

from pathlib import Path

import pytest

from src.apps.topology.backend.collectors.parse import parse
from src.apps.topology.backend.model.path import trace
from tests.unit.test_topology_policy_bindings import BASE, IDENTITY, INTERNET, PC1, observe

NAT = Path(__file__).parents[1] / "fixtures/topology/campus-wan/nat"
FAMILY = {"br1": "comware", "core1": "ios"}
ADDRESS = {"br1": "10.10.1.11", "core1": "10.10.1.13"}
PC2, PC4 = "172.16.20.12", "172.16.20.14"
BR1_WAN, BR1_LOOPBACK, BR1_TO_CORE1 = "100.64.1.2", "10.255.0.1", "10.0.13.1"
CORE1_TO_BR1 = "10.0.13.2"


def read(device, cap, text=None):
    text = text if text is not None else (NAT / device / f"{cap}.txt").read_text("utf-8")
    return parse(IDENTITY[FAMILY[device]], cap, text)


@pytest.fixture(scope="module")
def lab():
    return observe(
        {
            ADDRESS[device]: {
                cap: read(device, cap)
                for cap in ("nat_rules", "nat_bindings", "acl_rules", "acl_bindings", "routes")
                if (NAT / device / f"{cap}.txt").exists()
            }
            for device in ("br1", "core1")
        }
    )


def names(path):
    return [hop["device"]["name"].split(".")[0] for hop in path["hops"]]


def translations(path):
    return [
        (hop["device"]["name"].split(".")[0], step["nat"], step["interface"], step["before"], step["after"])
        for hop in path["hops"]
        for step in hop.get("translations", [])
    ]


# --- the rules each platform prints ---------------------------------------------------


def test_ios_nat_statements_read_as_rules():
    result = read("core1", "nat_rules")
    assert result.status == "complete"
    assert result.records == [
        {"kind": "pool", "pool": "P1", "type": "pool", "start": "10.99.0.1", "end": "10.99.0.6"},
        {"kind": "inside source static", "type": "static", "local": PC2, "global": "10.99.1.12"},
        {"kind": "inside source list", "pool": "P1", "type": "source", "acl": "NAT-P", "port_translation": False},
        {
            "kind": "inside source list",
            "type": "source",
            "acl": "NAT-T",
            "to_interface": "Ethernet0/1",
            "port_translation": True,  # overload
        },
        {
            "kind": "inside source static",
            "type": "static",
            "local": "172.16.10.15",
            "global_interface": "Ethernet0/1",
            "protocol": 6,
            "local_port": 80,
            "global_port": 8081,
        },
    ]
    # Interface roles come with the bindings: rules of no interface apply between them.
    assert {(row["ifname"], row["role"]) for row in read("core1", "nat_bindings").records} == {
        ("Ethernet0/1", "outside"),
        ("Ethernet1/0", "inside"),
        ("Ethernet1/1", "inside"),
    }


def test_comware_nat_rules_read_from_display_nat_all():
    result = read("br1", "nat_rules")
    assert result.status == "complete"
    assert result.records == [
        {"kind": "address group", "pool": "1", "type": "pool", "start": "100.64.9.1", "end": "100.64.9.6"},
        {"kind": "outbound", "interface": "GigabitEthernet2/0", "type": "source", "acl": "2999", "port_translation": True},
        {
            "kind": "outbound",
            "interface": "GigabitEthernet2/0",
            "pool": "1",
            "type": "source",
            "acl": "2998",
            "port_translation": True,
        },
        {
            "kind": "internal server",
            "interface": "GigabitEthernet2/0",
            "type": "static",
            "local": PC4,
            "global": BR1_WAN,
            "protocol": 6,
            "local_port": 80,
            "global_port": 8080,
        },
        {"kind": "Static", "type": "static", "local": "192.0.2.20", "global": "100.64.9.20"},
    ]
    # Two outbound rules on one interface: both bindings are read (the table is complete).
    bindings = read("br1", "nat_bindings")
    assert bindings.status == "complete"
    assert [row.get("rule") for row in bindings.records if row["kind"] == "outbound"] == ["2999", "2998"]


@pytest.mark.parametrize(
    "device,text,expected",
    [
        ("core1", "ip nat inside source route-map RM interface Ethernet0/1 overload\n", "rule:RM interface Ethernet0/1 overload"),
        ("core1", "ip nat outside source static 198.51.100.9 10.1.1.9\n", "rule:198.51.100.9 10.1.1.9"),
        ("core1", "ip nat inside source list NAT-T interface Ethernet0/1 vrf BLUE overload\n", "vrf:BLUE"),
    ],
)
def test_a_rule_this_reading_does_not_apply_is_kept_and_marked(device, text, expected):
    [record] = read(device, "nat_rules", text).records
    assert expected in record["unsupported"]


def test_comware_rules_it_does_not_read_fail_the_output():
    text = (NAT / "br1/nat_rules.txt").read_text("utf-8")
    inbound = text.replace(
        "NAT logging:",
        "NAT inbound information:\n  Totally 1 NAT inbound rules.\n  Interface: GigabitEthernet2/0\n    ACL: 2000\n\nNAT logging:",
    )
    assert read("br1", "nat_rules", inbound).status == "partial"
    excluded = text.replace(
        "      100.64.9.1            100.64.9.6       \n",
        "      100.64.9.1            100.64.9.6       \n    Exclude address information:\n",
    )
    assert read("br1", "nat_rules", excluded).status == "partial"


@pytest.mark.parametrize(
    "device,text",
    [("core1", ""), ("br1", "NAT is not configured.\n")],
)
def test_no_nat_is_an_empty_table(device, text):
    result = read(device, "nat_rules", text)
    assert (result.status, result.records) == ("complete", [])


# --- the lab's flows ------------------------------------------------------------------


def test_pc1_to_the_internet_is_translated_at_br1_and_the_reply_undone_there(lab):
    result = trace(lab, BASE, PC1, INTERNET, protocol="icmp")
    forward, reverse = result["forward"], result["reverse"]
    assert len(forward["paths"]) == 2
    for path in forward["paths"]:
        assert translations(path) == [
            ("BR1", "source", "GigabitEthernet2/0", {"src": PC1}, {"src": BR1_WAN})
        ]
    # The reply is sent to the translated source; it comes in where BR1 sends to the
    # internet and BR1 undoes its translation there.
    assert (reverse["from"], reverse["to"], reverse["first_hop"]) == (INTERNET, BR1_WAN, "external")
    for path in reverse["paths"]:
        assert names(path)[0] == "BR1"
        assert path["hops"][0]["gaps"][0]["status"] == "external_entry_assumed"
        assert translations(path)[0] == ("BR1", "session", "GigabitEthernet2/0", {"dst": BR1_WAN}, {"dst": PC1})
    assert {path["end"]["status"] for path in reverse["paths"]} >= {"delivered"}


def test_the_port_mapping_carries_internet_traffic_to_pc4(lab):
    result = trace(lab, BASE, INTERNET, BR1_WAN, protocol="tcp", src_port=40000, dst_port=8080)
    forward, reverse = result["forward"], result["reverse"]
    assert forward["first_hop"] == "external"
    for path in forward["paths"]:
        assert translations(path) == [
            (
                "BR1",
                "destination",
                "GigabitEthernet2/0",
                {"dst": BR1_WAN, "dst_port": 8080},
                {"dst": PC4, "dst_port": 80},
            )
        ]
        assert path["end"] == {"status": "delivered", "to": "subnet", "interface": "Vlan20"}
    # PC4 answers from 172.16.20.14:80; BR1 sends it out as 100.64.1.2:8080.
    assert reverse["from"] == PC4
    for path in reverse["paths"]:
        assert translations(path) == [
            (
                "BR1",
                "session",
                "GigabitEthernet2/0",
                {"src": PC4, "src_port": 80},
                {"src": BR1_WAN, "src_port": 8080},
            )
        ]


def test_a_port_mapping_needs_the_port_to_decide(lab):
    forward = trace(lab, BASE, INTERNET, BR1_WAN, protocol="tcp")["forward"]
    gaps = [gap for path in forward["paths"] for hop in path["hops"] for gap in hop.get("gaps", [])]
    assert {"status": "nat_undetermined", "interface": "GigabitEthernet2/0", "rule": "internal server", "needs": ["dst_port"]} in gaps


def test_core1_maps_pc2_and_the_reply_comes_back_as_the_global_address(lab):
    result = trace(lab, BASE, BR1_TO_CORE1, "10.99.1.12", protocol="icmp")
    for path in result["forward"]["paths"]:
        assert translations(path) == [
            ("CORE1", "destination", "Ethernet0/1", {"dst": "10.99.1.12"}, {"dst": PC2})
        ]
    [back] = result["reverse"]["paths"]
    assert result["reverse"]["status"] == "complete"
    # The reply reaches BR1 itself (10.0.13.1, proven on CORE1's Ethernet0/1 subnet).
    assert names(back) == ["DIST2", "CORE1", "BR1"]
    assert back["end"]["status"] == "delivered" and back["end"]["to"] == "device"
    assert translations(back) == [("CORE1", "session", "Ethernet0/1", {"src": PC2}, {"src": "10.99.1.12"})]


def test_only_the_path_through_core1_is_translated_and_each_reply_follows_its_own(lab):
    result = trace(lab, BASE, PC1, BR1_LOOPBACK, protocol="icmp")
    by_core = {names(path)[1]: path for path in result["forward"]["paths"]}
    assert translations(by_core["CORE1"]) == [
        ("CORE1", "source", "Ethernet0/1", {"src": PC1}, {"src": CORE1_TO_BR1})
    ]
    assert translations(by_core["CORE2"]) == []
    # The translated reply goes to 10.0.13.2, into CORE1, which undoes it.
    undone = [path for path in result["reverse"]["paths"] if translations(path)]
    assert undone and all(
        translations(path) == [("CORE1", "session", "Ethernet0/1", {"dst": CORE1_TO_BR1}, {"dst": PC1})]
        for path in undone
    )
    assert any(not translations(path) for path in result["reverse"]["paths"])


def test_inside_to_inside_traffic_is_not_translated(lab):
    # PC1 to PC4 never leaves by an outside interface.
    result = trace(lab, BASE, PC1, PC4, protocol="icmp")
    assert not any(translations(path) for path in result["forward"]["paths"])


def test_an_address_a_pool_gives_is_left_open(lab):
    rules = [{**row} for row in lab[ADDRESS["br1"]]["nat_rules"]]
    # Translate PC1's subnet to the address group instead of the interface.
    rules[1] = {**rules[1], "pool": "1"}
    pooled = {**lab, ADDRESS["br1"]: {**lab[ADDRESS["br1"]], "nat_rules": rules}}
    result = trace(pooled, BASE, PC1, INTERNET, protocol="icmp")
    for path in result["forward"]["paths"]:
        [hop] = [hop for hop in path["hops"] if hop.get("translations")]
        assert hop["translations"][0]["after"] == {"src": None}
        assert hop["translations"][0]["pool"] == [["100.64.9.1", "100.64.9.6"]]
        assert {"status": "nat_address_unknown", "interface": "GigabitEthernet2/0", "pool": [["100.64.9.1", "100.64.9.6"]]} in hop["gaps"]
    assert result["reverse"]["gaps"] == [{"status": "reverse_not_derived", "reason": "nat_address_unknown"}]


def test_a_device_without_nat_rules_keeps_the_may_translate_flag(lab):
    unread = {
        key: {k: v for k, v in facts.items() if k != "nat_rules"} if key == ADDRESS["br1"] else facts
        for key, facts in lab.items()
    }
    forward = trace(unread, BASE, PC1, INTERNET, protocol="icmp")["forward"]
    for path in forward["paths"]:
        assert not translations(path)
        assert any(gap["status"] == "nat_may_translate" for gap in path["hops"][-1]["gaps"])


def test_hidden_fact_tables_are_an_explicit_evaluation_control(monkeypatch):
    from src.apps.topology.backend import service

    monkeypatch.delenv("NETOPS_TOPOLOGY_HIDE_FACTS", raising=False)
    assert service._hidden_facts() == frozenset()
    monkeypatch.setenv("NETOPS_TOPOLOGY_HIDE_FACTS", " acl_rules, nat_rules ,")
    assert service._hidden_facts() == {"acl_rules", "nat_rules"}


def test_an_arp_entry_tied_to_no_interface_does_not_stop_the_analysis(lab):
    # LEAF3 (vpn-fabric) printed a VSI entry learned over a tunnel link with no interface.
    from src.apps.topology.backend.model.locate import _Locator, answer

    devices = {key: dict(facts) for key, facts in lab.items()}
    br1 = devices[ADDRESS["br1"]]
    br1["arp"] = [*br1["arp"], {"vrf": "default", "ip": "192.168.20.14", "mac": "00:50:79:66:68:00", "vsi": "BLUE-20", "type": "dynamic"}]
    assert trace(devices, BASE, PC1, INTERNET, protocol="icmp")["forward"]["paths"]
    assert answer(_Locator(devices, BASE, {}), "ip", PC1)["status"] != "not_found"
