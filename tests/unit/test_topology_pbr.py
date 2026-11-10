"""Policy routing as a standard tree, a query table and a step of the path.

BR1's sample policy (tests/fixtures/netconf/h3c-comware7/br1-policy): TEST-PBR node 10
permit, if-match acl 3992 (192.0.2.0/24 -> 203.0.113.0/24), apply next-hop 10.0.13.2;
node 20 deny, if-match acl 3993 (from 192.0.2.64/26); applied on GigabitEthernet5/0 and
to locally generated packets. The path cases add 192.0.2.0/24 to that interface of the
campus BR1.
"""

import copy
import json
from pathlib import Path

import pytest

from src.apps.topology.backend.collectors.identify import Identity
from src.apps.topology.backend.collectors.parse import parse
from src.apps.topology.backend.model.device_pbr import STEP, project_device_pbr
from src.apps.topology.backend.model.path import trace
from src.apps.topology.backend.model.pbr import evaluate, query_table
from tests.unit.test_topology_path import without_policies
from tests.unit.test_topology_policy_bindings import BASE, observe

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "tests/fixtures/netconf/h3c-comware7/br1-policy"
OPENCONFIG = ROOT / "models/network-twin/openconfig"
COMWARE = Identity(vendor="H3C", os_family="comware")
BR1 = "10.10.1.11"
IFNAME = "GigabitEthernet5/0"
TO_CORE1 = "10.0.13.2"


def read(cap, file):
    result = parse(COMWARE, cap, (POLICY / file).read_text("utf-8"))
    assert result.status == "complete", result.warnings
    return result.records


@pytest.fixture(scope="module")
def policy():
    return {
        "pbr_rules": read("pbr_rules", "cli-display-ip-policy-based-route.txt"),
        "pbr_bindings": read("pbr_bindings", "cli-display-ip-policy-based-route-setup.txt"),
        "acl_rules": read("acl_rules", "cli-display-acl-all.txt"),
    }


def facts(policy, **changed):
    return {
        "interfaces": [{"ifname": IFNAME, "type": "ethernet"}],
        "ip": [{"ifname": IFNAME, "vrf": "default", "address": "192.0.2.1", "prefix_len": 24}],
        **copy.deepcopy(policy),
        **changed,
    }


@pytest.fixture(scope="module")
def model():
    yangson = pytest.importorskip("yangson", reason="Install tests/topology-requirements.txt")
    return yangson.DataModel(
        (OPENCONFIG / "library.json").read_text("utf-8"),
        [str(OPENCONFIG / "modules"), str(ROOT / "src/apps/topology/resources/yang")],
    )


def validate(model, tree):
    content = pytest.importorskip("yangson.enumerations").ContentType.config
    model.from_raw(tree).validate(ctype=content)


def test_a_clause_the_template_does_not_know_is_kept_with_its_node():
    text = (
        "Policy name: P\n"
        "  node 5 permit:\n"
        "    if-match acl 3000\n"
        "    if-match acl 3001\n"
        "    apply next-hop 1.1.1.1 2.2.2.2\n"
        "  node 6 permit:\n"
        "    apply next-hop 3.3.3.3\n"
    )
    result = parse(COMWARE, "pbr_rules", text)
    assert result.status == "complete"
    assert result.records == [
        {
            "policy": "P",
            "node": 5,
            "mode": "permit",
            "acl": "3000",
            "unsupported": ["clause:if-match acl 3001 | apply next-hop 1.1.1.1 2.2.2.2"],
        },
        {"policy": "P", "node": 6, "mode": "permit", "next_hop": "3.3.3.3"},
    ]


def test_an_unknown_line_fails_the_output():
    assert parse(COMWARE, "pbr_rules", "PBR summary\n").status == "failed"


# --- the standard tree -----------------------------------------------------------------


def test_the_sample_policy_is_an_openconfig_tree(policy, model):
    result = project_device_pbr({"facts": facts(policy)})
    validate(model, result["tree"])
    # The local policy has no place in OpenConfig: a gap, and listed in the metadata.
    assert result["gaps"] == [{"code": "local_policy_not_expressible", "policy": "TEST-PBR"}]
    assert result["metadata"]["local_policies"] == ["TEST-PBR"]
    assert result["metadata"]["completeness"] == "partial"
    tree = result["tree"]
    assert tree["openconfig-interfaces:interfaces"]["interface"] == [
        {"name": IFNAME, "config": {"name": IFNAME, "type": "iana-if-type:ethernetCsmacd"}}
    ]
    [instance] = tree["openconfig-network-instance:network-instances"]["network-instance"]
    assert instance["config"] == {
        "name": "DEFAULT",
        "type": "openconfig-network-instance-types:DEFAULT_INSTANCE",
    }
    forwarding = instance["policy-forwarding"]
    [point] = forwarding["interfaces"]["interface"]
    assert point["config"] == {"interface-id": IFNAME, "apply-forwarding-policy": "TEST-PBR"}
    [entry] = forwarding["policies"]["policy"]
    assert entry["rules"]["rule"] == [
        {
            "sequence-id": 10 * STEP + 1,
            "config": {"sequence-id": 10 * STEP + 1, "description": "node 10 acl 3992 rule 5"},
            "ipv4": {
                "config": {
                    "source-address": "192.0.2.0/24",
                    "destination-address": "203.0.113.0/24",
                }
            },
            "action": {"config": {"next-hop": TO_CORE1}},
        },
        {
            "sequence-id": 20 * STEP + 1,
            "config": {"sequence-id": 20 * STEP + 1, "description": "node 20 acl 3993 rule 5"},
            "ipv4": {"config": {"source-address": "192.0.2.64/26"}},
            # deny: not policy-routed, sent by the routing table
            "action": {
                "forwarding": {
                    "config": {"forwarding-action": "openconfig-policy-forwarding:FORWARD"}
                }
            },
        },
    ]


def test_a_binding_to_an_unknown_interface_fails_the_leafref(policy, model):
    tree = project_device_pbr({"facts": facts(policy)})["tree"]
    broken = json.loads(json.dumps(tree).replace(f'"interface": "{IFNAME}"', '"interface": "GE9/9"'))
    with pytest.raises(Exception, match="instance-required"):
        validate(model, broken)


def test_an_acl_with_a_deny_entry_stops_the_policy_there(policy, model):
    # 3990 has deny entries: H3C documents both "ignored" and "fails the match".
    nodes = [{**policy["pbr_rules"][0], "acl": "3990"}, policy["pbr_rules"][1]]
    result = project_device_pbr({"facts": facts(policy, pbr_rules=nodes)})
    validate(model, result["tree"])
    [gap] = [g for g in result["gaps"] if g["code"] == "pbr_node_not_expressible"]
    assert (gap["policy"], gap["node"]) == ("TEST-PBR", 10)
    assert any(reason.endswith("acl_entry_not_permit") for reason in gap["reasons"])
    [instance] = result["tree"]["openconfig-network-instance:network-instances"]["network-instance"]
    [entry] = instance["policy-forwarding"]["policies"]["policy"]
    assert "rules" not in entry  # node 20 is left out too: only packets node 10 leaves reach it
    [row] = query_table(result)
    assert row["complete"] is False
    assert evaluate(row, {"src": "192.0.2.70", "dst": "198.51.100.1"})["result"] == "undetermined"


@pytest.mark.parametrize(
    "changed,code",
    [
        ({"interfaces": []}, "pbr_interface_reference_not_proven"),
        ({"interfaces": [{"ifname": IFNAME}]}, "pbr_interface_type_not_observed"),
        ({"pbr_rules": []}, "bound_policy_not_observed"),
    ],
)
def test_a_binding_that_cannot_be_exported_is_a_gap_and_an_open_row(policy, changed, code):
    result = project_device_pbr({"facts": facts(policy, **changed)})
    assert code in {gap["code"] for gap in result["gaps"]}
    assert result["tree"] == {}
    [row] = query_table(result)
    assert (row["interface-id"], row["complete"], row["rules"]) == (IFNAME, False, [])
    assert evaluate(row, {"src": "192.0.2.10", "dst": "203.0.113.5"})["result"] == "undetermined"


def test_a_node_clause_the_reading_does_not_know_is_not_expressed(policy):
    nodes = [{**policy["pbr_rules"][0], "unsupported": ["clause:apply precedence 5"]}]
    result = project_device_pbr({"facts": facts(policy, pbr_rules=nodes)})
    [gap] = [g for g in result["gaps"] if g["code"] == "pbr_node_not_expressible"]
    assert gap["reasons"] == ["clause:apply precedence 5"]


# --- the query table -----------------------------------------------------------------------


@pytest.fixture(scope="module")
def row(policy):
    [found] = query_table(project_device_pbr({"facts": facts(policy)}))
    return found


def test_the_query_table_keeps_the_tree_names(row):
    assert (row["network-instance"], row["interface-id"], row["policy-id"]) == (
        "DEFAULT",
        IFNAME,
        "TEST-PBR",
    )
    assert [(r["sequence-id"], r["match"], r["action"]) for r in row["rules"]] == [
        (
            10 * STEP + 1,
            {"source-address": "192.0.2.0/24", "destination-address": "203.0.113.0/24"},
            {"next-hop": TO_CORE1},
        ),
        (
            20 * STEP + 1,
            {"source-address": "192.0.2.64/26"},
            {"forwarding-action": "openconfig-policy-forwarding:FORWARD"},
        ),
    ]


@pytest.mark.parametrize(
    "src,dst,expected",
    [
        ("192.0.2.10", "203.0.113.5", {"result": "next-hop", "next-hop": TO_CORE1, "node": 10}),
        # In both ACLs: node 10 comes first.
        ("192.0.2.70", "203.0.113.5", {"result": "next-hop", "next-hop": TO_CORE1, "node": 10}),
        # Only in 3993: the deny node, routed normally.
        ("192.0.2.70", "198.51.100.1", {"result": "forward", "node": 20}),
        ("198.51.100.7", "203.0.113.5", {"result": "no-match"}),
    ],
)
def test_the_first_matching_node_decides(row, src, dst, expected):
    found = evaluate(row, {"src": src, "dst": dst})
    assert {key: found.get(key) for key in expected} == expected


def test_an_open_field_leaves_the_policy_undetermined(row):
    tcp_only = {**row, "rules": [{**row["rules"][0], "match": {**row["rules"][0]["match"], "protocol": 6}}]}
    found = evaluate(tcp_only, {"src": "192.0.2.10", "dst": "203.0.113.5"})
    assert (found["result"], found["needs"]) == ("undetermined", ["protocol"])


# --- the path --------------------------------------------------------------------------


def _clone(rows, match, **changed):
    return {**copy.deepcopy(next(r for r in rows if match(r))), **changed}


@pytest.fixture(scope="module")
def campus():
    observed = observe({})
    return {address: without_policies(facts) for address, facts in observed.items()}


def with_policy(campus, policy, **tables):
    """BR1 with 192.0.2.0/24 also on GigabitEthernet5/0 (its link to BR2) and the sample
    policy (or ``tables``)."""
    devices = copy.deepcopy(campus)
    br1 = devices[BR1]
    br1["ip"].append(
        _clone(
            br1["ip"],
            lambda r: r["ifname"] == "GigabitEthernet3/0",
            ifname=IFNAME,
            address="192.0.2.1",
            prefix_len=24,
        )
    )
    route = _clone(br1["routes"], lambda r: r.get("prefix") == "10.0.13.0/30", prefix="192.0.2.0/24")
    route["next_hops"] = [{**hop, "ifname": IFNAME} for hop in route["next_hops"]]
    br1["routes"].append(route)
    for host, mac in (("192.0.2.10", "00:00:5e:00:53:0a"), ("192.0.2.70", "00:00:5e:00:53:46")):
        br1["arp"].append(
            _clone(br1["arp"], lambda r: True, ip=host, mac=mac, ifname=IFNAME, vrf="default")
        )
    br1.update(copy.deepcopy(policy))
    br1.update(tables)
    return devices


def first(result):
    [path] = result["forward"]["paths"][:1]
    return path["hops"][0]


def test_a_packet_node_10_matches_goes_to_its_next_hop(campus, policy):
    plain = first(trace(with_policy(campus, policy, pbr_rules=[], pbr_bindings=[]), BASE, "192.0.2.10", "203.0.113.5"))
    hop = first(trace(with_policy(campus, policy), BASE, "192.0.2.10", "203.0.113.5"))
    assert hop["in_interface"] == IFNAME
    assert hop["policy_route"] == {
        "interface": IFNAME,
        "policy": "TEST-PBR",
        "node": 10,
        "rule": 10 * STEP + 1,
        "description": "node 10 acl 3992 rule 5",
        "result": "next-hop",
        "next-hop": TO_CORE1,
    }
    assert hop["route"]["next_hop"] == TO_CORE1 and hop["route"]["protocol"] == "policy-forwarding"
    assert hop["out_interface"] == "GigabitEthernet3/0"
    assert plain["out_interface"] != "GigabitEthernet3/0"  # the route table goes elsewhere
    assert not any(g["status"] == "policy_may_redirect" for g in hop.get("gaps", []))


def test_the_first_node_wins_where_both_acls_match(campus, policy):
    hop = first(trace(with_policy(campus, policy), BASE, "192.0.2.70", "203.0.113.5"))
    assert (hop["policy_route"]["node"], hop["out_interface"]) == (10, "GigabitEthernet3/0")


def test_the_deny_node_and_no_node_leave_the_route_table_to_decide(campus, policy):
    devices = with_policy(campus, policy)
    plain = with_policy(campus, policy, pbr_rules=[], pbr_bindings=[])
    for src, dst, node in (("192.0.2.70", "198.51.100.1", 20), ("192.0.2.10", "198.51.100.1", None)):
        hop, reference = first(trace(devices, BASE, src, dst)), first(trace(plain, BASE, src, dst))
        assert hop["policy_route"].get("node") == node
        assert hop["route"] == reference["route"]
        assert hop["out_interface"] == reference["out_interface"]


def test_a_device_without_policy_content_keeps_the_flag(campus, policy):
    devices = with_policy(campus, policy)
    del devices[BR1]["pbr_rules"]
    hop = first(trace(devices, BASE, "192.0.2.10", "203.0.113.5"))
    assert "policy_route" not in hop
    assert {"status": "policy_may_redirect", "interface": IFNAME, "policy": "TEST-PBR"} in hop[
        "gaps"
    ]


def test_an_unresolvable_next_hop_falls_back_to_the_route_table(campus, policy):
    # Without BR1's default route nothing resolves 192.168.250.1 (Comware then routes the
    # packet by the table, which has no route to 203.0.113.9 either).
    nodes = [{**policy["pbr_rules"][0], "next_hop": "192.168.250.1"}, policy["pbr_rules"][1]]
    results = []
    for tables in ({"pbr_rules": nodes}, {"pbr_rules": [], "pbr_bindings": []}):
        devices = with_policy(campus, policy, **tables)
        devices[BR1]["routes"] = [r for r in devices[BR1]["routes"] if r["prefix"] != "0.0.0.0/0"]
        [path] = trace(devices, BASE, "192.0.2.10", "203.0.113.9")["forward"]["paths"]
        results.append(path)
    computed, plain = results
    assert computed["end"] == plain["end"] == {"status": "no_route"}
    assert {
        "status": "pbr_next_hop_unresolved",
        "interface": IFNAME,
        "policy": "TEST-PBR",
        "node": 10,
        "next_hop": "192.168.250.1",
    } in computed["hops"][0]["gaps"]
