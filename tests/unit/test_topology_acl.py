"""IPv4 ACLs on a path: read through rule governance, checked hop by hop.

Real outputs from the campus lab (tests/fixtures/topology/campus-wan):

- acl-syntax: test ACLs on CORE1 (IOS) and BR1 (Comware) covering what both platforms
  print (hosts, wildcards, port operators and ranges, ICMP names, established, fragments,
  dscp, object groups, time ranges, depth-first order, named ACLs), bound both ways;
- acl: DIST1 Vlan10 inbound denies PC1's ping to PC4 and telnet to 172.16.20.0/24, BR1
  GE2/0 outbound denies UDP 53 to 198.51.100.0/24. VPCS traffic was refused exactly
  where these paths say: the hit counters in the fixture (3 pings, 1 telnet, 2 DNS
  probes) are that traffic, and the web, echo-reply and NTP flows passed.
"""

from copy import deepcopy
from pathlib import Path

import pytest

from src.apps.rule_governance.backend.check_items import system_items
from src.apps.rule_governance.runtime import CheckItemSelectionReader, SelectionError
from src.apps.topology.backend.collectors.parse import parse
from src.apps.topology.backend.model.acl import entry_matches, evaluate
from src.apps.topology.backend.model.device_acl import project_device_acl
from src.apps.topology.backend.model.path import trace
from tests.unit.test_topology_policy_bindings import BASE, IDENTITY, INTERNET, PC1, PC4, observe
from tests.unit.test_topology_vrf_collection import ios  # noqa: F401  (fixture)

FIXTURES = Path(__file__).parents[1] / "fixtures/topology/campus-wan"
SYNTAX, SCENARIO = FIXTURES / "acl-syntax", FIXTURES / "acl"
FAMILY = {"br1": "comware", "core1": "ios", "dist1": "ios"}
ADDRESS = {"br1": "10.10.1.11", "core1": "10.10.1.13", "dist1": "10.10.1.15"}
BR1_LOOPBACK = "10.255.0.1"


def read(folder, device, cap, text=None):
    text = text if text is not None else (folder / device / f"{cap}.txt").read_text("utf-8")
    return parse(IDENTITY[FAMILY[device]], cap, text)


def entries(folder, device, acl):
    return [row for row in read(folder, device, "acl_rules").records if row["acl"] == acl]


def packet(src, dst, protocol=None, src_port=None, dst_port=None, **extra):
    return {
        "src": src,
        "dst": dst,
        "vrf": "default",
        "protocol": protocol,
        "src_port": src_port,
        "dst_port": dst_port,
        **extra,
    }


# --- what the devices print, as conditions ---------------------------------------------


def test_ios_entries_read_as_conditions():
    result = read(SYNTAX, "core1", "acl_rules")
    assert result.status == "complete" and result.rejected_count == 0
    rows = {(row["acl"], row["seq"]): row for row in result.records}
    assert rows["10", 10]["src"] == {"address": "10.1.1.1", "wildcard": "0.0.0.0"}  # bare host
    assert rows["10", 20]["src"] == {"address": "10.0.0.0", "wildcard": "0.255.0.255"}
    assert "src" not in rows["10", 30] and rows["10", 30]["action"] == "deny"  # any
    assert rows["T-ALL", 20]["src_ports"] == {"op": "range", "ports": [1000, 2000]}
    assert rows["T-ALL", 20]["dst_ports"] == {"op": "eq", "ports": [53]}  # "domain"
    assert rows["T-ALL", 30]["dst_ports"] == {"op": "eq", "ports": [80, 443]}  # "www 443"
    assert rows["T-ALL", 50]["icmp"] == [3, 4]  # "packet-too-big"
    assert rows["T-ALL", 60]["established"] and rows["T-ALL", 60]["matches"] == 24
    assert rows["T-ALL", 70]["fragments"]
    assert rows["T-ALL", 80]["src_ports"] == {"op": "neq", "ports": [80]}
    assert rows["T-ALL", 80]["dst_ports"] == {"op": "lt", "ports": [1024]}
    assert rows["T-ALL", 120]["unsupported"] == ["condition:dscp ef"]
    assert rows["T-ALL", 130]["unsupported"] == ["object_group:OG-T"]
    assert rows["T-ALL", 140]["time_range"] == {"name": "TR-DAY", "active": True}
    assert rows["T-ALL", 150]["protocol"] == 51  # "ahp"
    assert "vrf" not in rows["T-ALL", 160]  # an IOS entry has no instance condition


def test_comware_entries_read_as_conditions():
    result = read(SYNTAX, "br1", "acl_rules")
    assert result.status == "complete" and result.rejected_count == 0
    rows = {(row["acl"], row["seq"]): row for row in result.records}
    assert rows["3998", 5]["src"] == {"address": "192.0.2.1", "wildcard": "0.0.0.0"}  # "... 0"
    assert rows["3998", 10]["dst_ports"] == {"op": "eq", "ports": [53]}  # "dns"
    assert rows["3998", 25]["icmp"] == [3, 4]  # "fragmentneed-DFset"
    assert rows["3998", 75]["protocol"] == 51
    assert rows["3998", 60]["unsupported"] == ["condition:dscp ef"]
    assert rows["T-NAMED", 5]["dst"] == {"address": "192.0.2.0", "wildcard": "0.0.0.255"}
    # Without vpn-instance a Comware rule matches only non-VPN packets (declared default).
    assert {row.get("vrf") for row in result.records} == {"default"}


def test_a_depth_first_acl_keeps_the_order_the_device_matches_in():
    assert [row["seq"] for row in entries(SYNTAX, "br1", "3997")] == [10, 5, 0]


def test_bindings_carry_the_action_for_unmatched_packets():
    ios_bindings = read(SYNTAX, "core1", "acl_bindings").records
    assert sorted(ios_bindings, key=lambda row: row["direction"]) == [
        # IOS prints no default: its ACLs end with an implicit deny (declared).
        {"ifname": "Ethernet1/0", "direction": "in", "family": "ipv4", "acl": "T-IN", "default_action": "deny"},
        {"ifname": "Ethernet1/0", "direction": "out", "family": "ipv4", "acl": "T-ALL", "default_action": "deny"},
    ]
    comware = read(SYNTAX, "br1", "acl_bindings")
    assert comware.status == "complete"
    # Comware prints the default action per direction; its two rows make one binding.
    assert sorted(comware.records, key=lambda row: row["direction"]) == [
        {"ifname": "GigabitEthernet3/0", "direction": "in", "family": "ipv4", "acl": "T-NAMED", "default_action": "permit"},
        {"ifname": "GigabitEthernet3/0", "direction": "out", "family": "ipv4", "acl": "3998", "default_action": "permit"},
    ]


def test_security_zone_members():
    assert read(SYNTAX, "br1", "security_zones").records == [
        {"zone": "ZTEST", "ifname": "LoopBack99"}
    ]
    # Once used, Comware keeps its built-in zones; without members they bind nothing.
    builtin = read(SCENARIO, "br1", "security_zones")
    assert (builtin.status, builtin.records) == ("complete", [])


@pytest.mark.parametrize(
    "device,cap,text",
    [
        ("core1", "acl_rules", ""),
        ("br1", "acl_rules", ""),
        ("br1", "acl_bindings", ""),
        ("br1", "security_zones", "There is no zone.\n"),
    ],
)
def test_nothing_configured_is_an_empty_table(device, cap, text):
    result = read(SYNTAX, device, cap, text)
    assert (result.status, result.records) == ("complete", [])


def test_an_entry_this_reading_does_not_know_is_kept_undecidable():
    ios_text = "Extended IP access list X\n    10 evaluate REFLECTED\n    20 permit tcp any any eq foo-service\n"
    [unknown, port] = read(SYNTAX, "core1", "acl_rules", ios_text).records
    assert "action" not in unknown and unknown["unsupported"] == [
        "unknown_entry",
        "condition:evaluate REFLECTED",
    ]
    assert port["unsupported"] == ["port_name:foo-service"]
    comware_text = (
        "Advanced IPv4 ACL 3000, 1 rule,\nACL's step is 5\n"
        " rule 5 permit ip source 10.0.0.0 0.255.255.255 user-group G1\n"
    )
    [row] = read(SYNTAX, "br1", "acl_rules", comware_text).records
    assert row["unsupported"] == ["condition:user-group G1"]
    # Every unknown entry is undecided, never read as if the condition were absent.
    assert entry_matches(unknown, packet("10.0.0.1", "10.0.0.2", 6, 1000, 80))[0] is None
    assert entry_matches(port, packet("10.0.0.1", "10.0.0.2", 6, 1000, 80))[0] is None
    assert entry_matches(row, packet("10.0.0.1", "10.0.0.2"))[0] is None


def test_text_that_is_not_acl_output_is_rejected():
    result = read(SYNTAX, "core1", "acl_rules", "Extended IP access list X\nnonsense\n")
    assert result.status in {"partial", "failed"} and result.rejected_count == 1


# --- one packet against the entries ----------------------------------------------------


def test_the_first_matching_entry_decides():
    t_all = entries(SYNTAX, "core1", "T-ALL")
    web = packet("10.9.9.9", "192.0.2.5", 6, 40000, 80)
    assert evaluate(t_all, web, "deny") == {"result": "permit", "rule": 30}


def test_undecided_entries_that_agree_do_not_leave_the_result_open():
    # No protocol given: T-ALL's earlier entries cannot be decided, but all of them permit.
    t_all = entries(SYNTAX, "core1", "T-ALL")
    result = evaluate(t_all, packet("10.9.9.9", "10.8.8.8"), "deny")
    assert result["result"] == "permit" and result["rule"] == 160
    assert result["undecided_before"] == [30, 40, 50, 60, 100, 110, 130, 150]


def test_undecided_entries_that_disagree_say_what_would_settle_them():
    pc_in = entries(SCENARIO, "dist1", "PC-IN")
    assert evaluate(pc_in, packet(PC1, PC4), "deny") == {
        "result": "undetermined",
        "rules": [10, 20, 30],
        "needs": ["dst_port", "icmp", "protocol"],
    }


def test_established_matches_only_a_reply():
    t_all = entries(SYNTAX, "core1", "T-ALL")
    first = packet("198.51.100.1", "172.16.10.11", 6, 443, 50000)
    assert evaluate(t_all, first, "deny")["rule"] == 160
    assert evaluate(t_all, {**first, "reply": True}, "deny") == {"result": "permit", "rule": 60}


def test_an_echo_request_and_its_reply_are_different_packets():
    pc_in = entries(SCENARIO, "dist1", "PC-IN")
    request = packet(PC1, PC4, 1, icmp=(8, 0))
    reply = packet(PC1, PC4, 1, icmp=(0, 0), reply=True)  # PC1 answering PC4's ping
    assert evaluate(pc_in, request, "deny") == {"result": "deny", "rule": 10}
    assert evaluate(pc_in, reply, "deny") == {"result": "permit", "rule": 30}


def test_a_comware_rule_without_vpn_instance_leaves_vpn_packets_alone():
    named = entries(SYNTAX, "br1", "T-NAMED")
    public = packet("10.1.1.1", "192.0.2.9")
    assert evaluate(named, public, "permit") == {"result": "deny", "rule": 5}
    in_vpn = {**public, "vrf": "RED"}
    assert evaluate(named, in_vpn, "permit") == {"result": "permit", "rule": "default"}


def test_fragment_and_inactive_time_range_entries():
    rows = {row["seq"]: row for row in entries(SYNTAX, "core1", "T-ALL")}
    from_test_net = packet("192.0.2.7", "10.8.8.8", 6, 1000, 2000)
    assert entry_matches(rows[70], from_test_net) == (False, [])  # traced packets are whole
    assert entry_matches(rows[140], from_test_net) == (True, [])  # active when collected
    inactive = {**rows[140], "time_range": {"name": "TR-DAY", "active": False}}
    assert entry_matches(inactive, from_test_net) == (False, [])


# --- paths through the lab's ACLs ------------------------------------------------------


@pytest.fixture(scope="module")
def scenario():
    return observe(
        {
            ADDRESS[device]: {
                cap: read(SCENARIO, device, cap)
                for cap in ("acl_rules", "acl_bindings", "security_zones")
                if (SCENARIO / device / f"{cap}.txt").exists()
            }
            for device in ("br1", "dist1")
        }
    )


def names(path):
    return [hop["device"]["name"].split(".")[0] for hop in path["hops"]]


def hits(device, acl, seq):
    [row] = [row for row in entries(SCENARIO, device, acl) if row["seq"] == seq]
    return row.get("matches")


def test_pc1_pinging_pc4_is_refused_where_dist1_refused_it(scenario):
    forward = trace(scenario, BASE, PC1, PC4, protocol="icmp")["forward"]
    assert forward["status"] == "blocked"
    [path] = forward["paths"]
    assert names(path) == ["DIST1"]
    assert path["end"] == {
        "status": "denied_in",
        "interface": "Vlan10",
        "direction": "in",
        "acl": "PC-IN",
        "rule": 10,
    }
    assert hits("dist1", "PC-IN", 10) == 3  # the three VPCS pings


def test_telnet_is_refused_and_web_is_delivered(scenario):
    telnet = trace(scenario, BASE, PC1, PC4, protocol="tcp", dst_port=23)["forward"]
    assert telnet["status"] == "blocked"
    assert [p["end"]["rule"] for p in telnet["paths"]] == [20]
    assert hits("dist1", "PC-IN", 20) == 1
    web = trace(scenario, BASE, PC1, PC4, protocol="tcp", dst_port=80)["forward"]
    assert web["status"] == "complete"
    [path] = web["paths"]
    assert path["end"]["status"] == "delivered"
    assert path["hops"][0]["filters"] == [
        {"interface": "Vlan10", "direction": "in", "acl": "PC-IN", "result": "permit", "rule": 30}
    ]


def test_without_a_protocol_the_hop_says_what_it_needs(scenario):
    forward = trace(scenario, BASE, PC1, PC4)["forward"]
    assert forward["status"] == "partial"
    [path] = forward["paths"]
    assert path["end"]["status"] == "delivered"  # the walk goes on past an open filter
    assert path["hops"][0]["gaps"] == [
        {
            "status": "filter_undetermined",
            "interface": "Vlan10",
            "direction": "in",
            "acl": "PC-IN",
            "rules": [10, 20, 30],
            "needs": ["dst_port", "icmp", "protocol"],
        }
    ]


def test_dns_to_the_internet_is_refused_on_every_path(scenario):
    dns = trace(scenario, BASE, PC1, INTERNET, protocol="udp", dst_port=53)["forward"]
    assert dns["status"] == "blocked"
    assert sorted(map(names, dns["paths"])) == [["DIST1", "CORE1", "BR1"], ["DIST1", "CORE2", "BR1"]]
    for path in dns["paths"]:
        assert path["end"] == {
            "status": "denied_out",
            "interface": "GigabitEthernet2/0",
            "direction": "out",
            "acl": "3990",
            "rule": 5,
        }
    assert hits("br1", "3990", 5) == 2  # the two VPCS probes
    ntp = trace(scenario, BASE, PC1, INTERNET, protocol="udp", dst_port=123)["forward"]
    for path in ntp["paths"]:
        assert path["end"]["status"] == "unmanaged_next_hop"
        assert path["hops"][-1]["filters"][-1]["rule"] == "default"


def test_what_a_device_sends_itself_is_not_filtered_on_its_way_out(scenario):
    own = trace(scenario, BASE, BR1_LOOPBACK, INTERNET, protocol="udp", dst_port=53)["forward"]
    [path] = own["paths"]
    assert path["end"]["status"] == "unmanaged_next_hop" and "filters" not in path["hops"][0]


def test_an_interface_in_a_security_zone_is_not_settled_by_its_acl(scenario):
    zoned = {
        key: {**facts, "security_zones": [{"zone": "Untrust", "ifname": "GigabitEthernet2/0"}]}
        if key == ADDRESS["br1"]
        else facts
        for key, facts in scenario.items()
    }
    ntp = trace(zoned, BASE, PC1, INTERNET, protocol="udp", dst_port=123)["forward"]
    assert ntp["status"] == "partial"
    for path in ntp["paths"]:
        assert {
            "status": "security_policy_not_evaluated",
            "interface": "GigabitEthernet2/0",
            "zones": ["Untrust"],
        } in path["hops"][-1]["gaps"]


def test_a_bound_acl_whose_entries_were_not_read_stays_open(scenario):
    unread = {
        key: {k: v for k, v in facts.items() if k != "acl_rules"} if key == ADDRESS["dist1"] else facts
        for key, facts in scenario.items()
    }
    forward = trace(unread, BASE, PC1, PC4, protocol="icmp")["forward"]
    [path] = forward["paths"]
    assert forward["status"] == "partial"
    gap = path["hops"][0]["gaps"][0]
    assert (gap["status"], gap["needs"]) == ("filter_undetermined", ["acl_entries"])
    assert "acl_rules" in path["hops"][0]["not_observed"]


@pytest.mark.parametrize(
    "flow,message",
    [
        ({"dst_port": 80}, "请同时指定协议"),
        ({"protocol": "icmp", "dst_port": 80}, "只适用于 TCP 或 UDP"),
        ({"protocol": "bogus"}, "协议应为"),
        ({"protocol": "tcp", "dst_port": 0}, "1-65535"),
    ],
)
def test_a_flow_is_checked_before_tracing(scenario, flow, message):
    with pytest.raises(ValueError, match=message):
        trace(scenario, BASE, PC1, PC4, **flow)


# --- collection and the rule-governance contract ---------------------------------------


def test_a_command_two_capabilities_publish_runs_once(ios):  # noqa: F811
    result = ios.collect()
    assert ios.commands.count("show ip interface") == 1
    assert result.parsed["ip"].status == "complete"
    assert (result.parsed["acl_bindings"].status, result.parsed["acl_bindings"].records) == (
        "complete",
        [],
    )


def test_defaults_for_a_value_the_check_item_does_not_declare_are_refused(monkeypatch):
    original = system_items.templates

    def changed():
        bound = original()
        key = "topology.acl_bindings.ios"
        return {**bound, key: {**bound[key], "defaults": {"NOT_A_VALUE": "x"}}}

    monkeypatch.setattr(system_items, "templates", changed)
    with pytest.raises(SelectionError, match="defaults for Values"):
        CheckItemSelectionReader(source="shipped").select(
            ["topology.acl_bindings"], {"vendor": "Cisco", "platform": "ios"}
        )


# --- RFC 8519 ------------------------------------------------------------------------


@pytest.fixture(scope="module")
def device_model():
    yangson = pytest.importorskip("yangson", reason="Install tests/topology-requirements.txt")
    root = Path(__file__).parents[2] / "src/apps/topology/resources/yang"
    return yangson.DataModel((root / "device-library.json").read_text("utf-8"), [str(root)])


def rfc8519(devices, device):
    result = project_device_acl({"facts": devices[ADDRESS[device]]})
    return result, result["tree"].get("ietf-access-control-list:acls", {})


def validate(model, tree):
    content = pytest.importorskip("yangson.enumerations").ContentType.all
    tree = deepcopy(tree)
    # Test-only completion of the interface counters the CLI never gives.
    for interface in tree.get("ietf-interfaces:interfaces", {}).get("interface", []):
        interface["statistics"] = {"discontinuity-time": "2000-01-01T00:00:00Z"}
    model.from_raw(tree).validate(ctype=content)


def test_the_scenario_acls_are_rfc8519_trees(scenario, device_model):
    result, acls = rfc8519(scenario, "dist1")
    validate(device_model, result["tree"])
    assert result["gaps"] == []
    [pc_in] = [acl for acl in acls["acl"] if acl["name"] == "PC-IN"]
    assert pc_in["type"] == "ietf-access-control-list:ipv4-acl-type"
    assert pc_in["aces"]["ace"][0] == {
        "name": "10",
        "actions": {"forwarding": "ietf-access-control-list:drop"},
        "matches": {
            "ipv4": {
                "protocol": 1,
                "source-ipv4-network": "172.16.10.11/32",
                "destination-ipv4-network": "172.16.20.14/32",
            },
            "icmp": {"type": 8},
        },
        "statistics": {"matched-packets": "3"},
    }
    assert acls["attachment-points"]["interface"] == [
        {"interface-id": "Vlan10", "ingress": {"acl-sets": {"acl-set": [{"name": "PC-IN"}]}}}
    ]
    # The attachment references the interface, which comes with it (RFC 8343).
    assert [row["name"] for row in result["tree"]["ietf-interfaces:interfaces"]["interface"]] == [
        "Vlan10"
    ]
    # The action for unmatched packets is metadata; no entry is made up for it.
    assert result["metadata"]["default_actions"] == [
        {"interface": "Vlan10", "direction": "in", "acl": "PC-IN", "default_action": "deny"}
    ]
    result, acls = rfc8519(scenario, "br1")
    validate(device_model, result["tree"])
    assert acls["attachment-points"]["interface"] == [
        {
            "interface-id": "GigabitEthernet2/0",
            "egress": {"acl-sets": {"acl-set": [{"name": "3990"}]}},
        }
    ]


@pytest.fixture(scope="module")
def syntax():
    return observe(
        {
            ADDRESS[device]: {cap: read(SYNTAX, device, cap) for cap in ("acl_rules", "acl_bindings")}
            for device in ("br1", "core1")
        }
    )


def test_what_rfc8519_cannot_express_is_left_out_with_a_gap(syntax, device_model):
    result, acls = rfc8519(syntax, "core1")
    validate(device_model, result["tree"])
    assert [(g["acl"], g["entry"], g["reasons"]) for g in result["gaps"]] == [
        ("10", 20, ["src_wildcard"]),  # 0.255.0.255 is not a prefix
        ("T-ALL", 30, ["dst_ports"]),  # "eq www 443": one entry holds one port or range
        ("T-ALL", 60, ["established"]),
        ("T-ALL", 70, ["fragments"]),
        ("T-ALL", 120, ["condition:dscp ef"]),
        ("T-ALL", 130, ["object_group:OG-T"]),
        ("T-ALL", 140, ["time_range"]),
    ]
    aces = {
        ace["name"]: ace for acl in acls["acl"] if acl["name"] == "T-ALL" for ace in acl["aces"]["ace"]
    }
    # "lt 1024" and "gt 1023" are the same ports as "lte 1023" and "gte 1024".
    assert aces["80"]["matches"]["tcp"] == {
        "source-port": {"operator": "neq", "port": 80},
        "destination-port": {"operator": "lte", "port": 1023},
    }
    assert aces["90"]["matches"]["tcp"] == {"source-port": {"operator": "gte", "port": 1024}}
    assert aces["20"]["matches"]["udp"]["source-port"] == {"lower-port": 1000, "upper-port": 2000}
    # Exported entries keep the device's match order.
    assert list(aces) == ["10", "20", "40", "50", "80", "90", "100", "110", "150", "160"]
    result, acls = rfc8519(syntax, "br1")
    validate(device_model, result["tree"])
    [auto] = [acl for acl in acls["acl"] if acl["name"] == "3997"]
    assert [ace["name"] for ace in auto["aces"]["ace"]] == ["10", "5", "0"]


def test_a_public_only_rule_is_not_attached_to_a_vpn_interface(scenario, device_model):
    facts = dict(scenario[ADDRESS["br1"]])
    facts["ip"] = [
        {**row, "vrf": "RED"} if row["ifname"] == "GigabitEthernet2/0" else row
        for row in facts["ip"]
    ]
    result = project_device_acl({"facts": facts})
    validate(device_model, result["tree"])
    assert "attachment-points" not in result["tree"]["ietf-access-control-list:acls"]
    assert result["gaps"] == [
        {
            "code": "acl_instance_condition_not_expressible",
            "interface": "GigabitEthernet2/0",
            "direction": "out",
            "acl": "3990",
        }
    ]
