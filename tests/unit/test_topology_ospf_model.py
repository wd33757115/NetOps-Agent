"""Meaningful OSPF scope/cost binding cases, without invented direction slots."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from src.apps.topology.backend.collectors.parse import parse_generic
from src.apps.topology.backend.collectors.tiers import generic_ospf
from src.apps.topology.backend.model.ospf import link_metric, project_ospf

CAPTURES = Path(__file__).resolve().parents[2] / "tests/fixtures/topology/campus-wan"


def interface(name="Ethernet0/1", address="10.0.0.1", cost=10, pid="1", **extra):
    return {
        "ifname": name,
        "address": address,
        "cost": cost,
        "process_id": pid,
        "area": "0",
        **extra,
    }


def facts(cost=10, pid="1", vrf="default", address="10.0.0.1", name="Ethernet0/1"):
    return {
        "ospf_interfaces": {"interfaces": [interface(name, address, cost, pid)]},
        "ospf_process": {"processes": [{"process_id": pid, "router_id": "10.255.0.1"}]},
        "ip": [{"ifname": name, "address": address, "vrf": vrf, "prefix_len": 30}],
    }


def ref(device="a", vrf="default", name="Ethernet0/1", ip="10.0.0.1"):
    return {"address": device, "vrf": vrf, "ifname": name, "address_ip": ip}


def device(projected, key="a"):
    return projected["devices"][key]


def codes(result):
    return {gap["code"] for gap in result["gaps"]}


def pair(a=10, b=10):
    return project_ospf({"a": facts(a), "b": facts(b, pid="99", address="10.0.0.2")})


def pair_refs():
    return [ref(), ref("b", ip="10.0.0.2")]


def test_exact_ip_fact_binds_ios_process_rid_without_an_ospf_vrf_default():
    original = {"a": facts(vrf="BLUE")}
    prior = deepcopy(original)
    result = project_ospf(original)
    assert device(result)["router_ids"] == {"BLUE": ["10.255.0.1"]}
    assert device(result)["interfaces"][0]["vrf"] == "BLUE"
    assert "vrf" not in device(result)["processes"][0]
    assert original == prior
    assert json.loads(json.dumps(result)) == result


@pytest.mark.parametrize("vrf", [None, "", " BLUE "])
def test_missing_or_invalid_ip_scope_does_not_become_default(vrf):
    rows = facts()
    if vrf is None:
        del rows["ip"][0]["vrf"]
    else:
        rows["ip"][0]["vrf"] = vrf
    result = project_ospf({"a": rows})
    assert device(result)["router_ids"] == {}
    assert not device(result)["interfaces"][0]["usable"]
    assert "vrf" not in device(result)["interfaces"][0]
    assert "ospf_ip_scope_unproven_or_ambiguous" in codes(device(result))


@pytest.mark.parametrize("change", ["wrong_name", "wrong_address", "same_subnet", "ipv6"])
def test_scope_requires_exact_canonical_name_and_ipv4_address(change):
    rows = facts()
    if change == "wrong_name":
        rows["ip"][0]["ifname"] = "Et0/1"
    else:
        rows["ip"][0]["address"] = {
            "wrong_address": "10.0.1.1",
            "same_subnet": "10.0.0.2",
            "ipv6": "2001:db8::1",
        }[change]
    result = device(project_ospf({"a": rows}))
    assert result["router_ids"] == {}
    assert not result["interfaces"][0]["usable"]


@pytest.mark.parametrize("vrf", ["default", "OTHER", None])
def test_duplicate_matching_ip_rows_never_choose_a_scope(vrf):
    rows = facts()
    other = deepcopy(rows["ip"][0])
    if vrf is None:
        del other["vrf"]
    else:
        other["vrf"] = vrf
    rows["ip"].append(other)
    result = device(project_ospf({"a": rows}))
    assert result["router_ids"] == {}
    assert not result["interfaces"][0]["usable"]


def test_multi_scope_process_keeps_per_interface_costs_but_no_router_id():
    rows = facts()
    rows["ospf_interfaces"]["interfaces"].append(interface("Ethernet0/2", "10.0.1.1", 20))
    rows["ip"].append({"ifname": "Ethernet0/2", "address": "10.0.1.1", "vrf": "BLUE"})
    result = device(project_ospf({"a": rows}))
    assert result["router_ids"] == {}
    assert {(row["vrf"], row["cost"]) for row in result["interfaces"]} == {
        ("default", 10),
        ("BLUE", 20),
    }
    assert all(row["usable"] for row in result["interfaces"])
    assert "ospf_process_scope_unproven_or_ambiguous" in codes(result)


def test_explicit_process_scope_conflict_blocks_all_costs_of_that_process():
    rows = facts(vrf="BLUE")
    rows["ospf_process"]["processes"][0]["vrf"] = "default"
    result = device(project_ospf({"a": rows}))
    assert result["router_ids"] == {}
    assert not result["interfaces"][0]["usable"]
    assert "conflicting_ospf_scope" in codes(result)


def test_synthetic_comware_parser_outputs_join_brief_to_full_interface_proof():
    brief = """OSPF Process 1 with Router ID 10.255.0.1
 Interfaces
 Area: 0.0.0.0
 IP Address Type State Cost Pri DR BDR
 10.0.0.1 Broadcast DR 1 1 10.0.0.1 0.0.0.0
"""
    full = """OSPF Process 1 with Router ID 10.255.0.1
 Multi-VPN-Instance is not enabled
 Area: 0.0.0.0
 Interface: 10.0.0.1 (GigabitEthernet3/0)
 Cost: 1 State: DR Type: Broadcast MTU: 1500
"""
    b, p = (
        generic_ospf.parse_interfaces(brief, "comware"),
        generic_ospf.parse_process(full, "comware"),
    )
    assert not b.rejected and not p.rejected
    rows = facts(1, name="GigabitEthernet3/0")
    rows.update(ospf_interfaces={"summary": b.summary}, ospf_process={"summary": p.summary})
    result = device(project_ospf({"a": rows}))
    assert result["interfaces"] == [
        {
            "process_id": "1",
            "address": "10.0.0.1",
            "ifname": "GigabitEthernet3/0",
            "cost": 1,
            "area": "0.0.0.0",
            "router_id": "10.255.0.1",
            "vrf": "default",
            "sources": ["ospf_interfaces", "ospf_process"],
            "usable": True,
        }
    ]
    assert result["router_ids"] == {"default": ["10.255.0.1"]}


def test_comware_address_only_cannot_get_ifname_from_ip_facts():
    rows = facts(1)
    del rows["ospf_interfaces"]["interfaces"][0]["ifname"]
    result = device(project_ospf({"a": rows}))
    assert "ifname" not in result["interfaces"][0]
    assert result["router_ids"] == {}
    assert not result["interfaces"][0]["usable"]
    assert "ospf_interface_name_unproven" in codes(result)


@pytest.mark.parametrize(
    "field,value",
    [("cost", 1), ("ifname", "Ethernet0/2"), ("area", "10"), ("router_id", "10.255.0.2")],
)
def test_conflicting_brief_and_full_facts_never_select_one_cost(field, value):
    rows = facts()
    original = deepcopy(rows["ospf_interfaces"]["interfaces"][0])
    other = deepcopy(original)
    if field == "router_id":
        original["router_id"] = "10.255.0.1"
        rows["ospf_interfaces"]["interfaces"][0] = original
    other[field] = value
    rows["ospf_process"]["interfaces"] = [other]
    result = device(project_ospf({"a": rows}))
    assert result["interfaces"] == [] and result["router_ids"] == {}
    assert "conflicting_ospf_interface" in codes(result)
    assert {row["record"]["cost"] for row in result["observations"]} == {10, other["cost"]}


@pytest.mark.parametrize("capability", ["ospf_interfaces", "ospf_process"])
def test_global_parser_veto_blocks_both_sources_but_preserves_raw_observations(capability):
    rows = facts()
    rows["ospf_process"]["interfaces"] = deepcopy(rows["ospf_interfaces"]["interfaces"])
    rows[capability]["invalid_structure"] = True
    result = device(project_ospf({"a": rows}))
    assert result["interfaces"] == [] and result["router_ids"] == {}
    assert len(result["observations"]) == 2
    assert result["processes"] == rows["ospf_process"]["processes"]
    assert "ospf_device_projection_blocked" in codes(result)


@pytest.mark.parametrize(
    "selector",
    [
        {"ifname": "Ethernet0/1"},
        {"address": "10.0.0.1"},
        {"process_id": "1", "address": "10.0.0.1"},
    ],
)
def test_per_interface_veto_from_dropped_source_blocks_other_source_only_at_that_port(selector):
    rows = facts()
    good = interface("Ethernet0/2", "10.0.1.1", 20)
    rows["ospf_process"]["interfaces"] = [*rows["ospf_interfaces"]["interfaces"], good]
    rows["ospf_interfaces"] = {"invalid_interfaces": [selector]}
    rows["ip"].append({"ifname": "Ethernet0/2", "address": "10.0.1.1", "vrf": "default"})
    result = device(project_ospf({"a": rows}))
    assert [(row["ifname"], row["cost"]) for row in result["interfaces"]] == [("Ethernet0/2", 20)]
    assert "ospf_interface_vetoed" in codes(result)


@pytest.mark.parametrize("capability", ["ospf_interfaces", "ospf_process"])
def test_process_veto_blocks_interfaces_from_all_sources(capability):
    rows = facts()
    rows[capability]["invalid_processes"] = ["01"]
    result = device(project_ospf({"a": rows}))
    assert result["interfaces"] == [] and result["router_ids"] == {}
    assert "ospf_process_vetoed" in codes(result)


@pytest.mark.parametrize(
    "markers",
    [
        {"invalid_interfaces": [{}]},
        {"invalid_interfaces": None},
        {"invalid_processes": [None]},
        {"invalid_structure": "false"},
    ],
)
def test_malformed_veto_markers_fail_closed(markers):
    rows = facts()
    rows["ospf_interfaces"].update(markers)
    result = device(project_ospf({"a": rows}))
    assert result["interfaces"] == [] and result["router_ids"] == {}
    assert "ospf_device_projection_blocked" in codes(result)


@pytest.mark.parametrize("cost", [True, None, -1, 65536, "10"])
def test_damaged_cost_in_one_source_cannot_be_replaced_by_a_good_other_source(cost):
    rows = facts()
    rows["ospf_process"]["interfaces"] = deepcopy(rows["ospf_interfaces"]["interfaces"])
    rows["ospf_interfaces"]["interfaces"][0]["cost"] = cost
    result = device(project_ospf({"a": rows}))
    assert result["interfaces"] == []
    assert "invalid_ospf_interface" in codes(result)
    assert len(result["observations"]) == 2


def test_duplicate_interface_in_single_source_invalidates_entire_group():
    rows = facts()
    rows["ospf_interfaces"]["interfaces"] *= 2
    result = device(project_ospf({"a": rows}))
    assert result["interfaces"] == [] and result["router_ids"] == {}
    assert "duplicate_ospf_interface" in codes(result)


def test_duplicate_process_invalidates_process_costs_and_router_id():
    rows = facts()
    rows["ospf_process"]["processes"] *= 2
    result = device(project_ospf({"a": rows}))
    assert not any(row["usable"] for row in result["interfaces"])
    assert result["router_ids"] == {}
    assert "duplicate_ospf_process" in codes(result)


def test_missing_full_process_keeps_precisely_bound_ios_cost_without_router_id():
    rows = facts()
    del rows["ospf_process"]
    result = device(project_ospf({"a": rows}))
    assert result["interfaces"][0]["usable"]
    assert result["interfaces"][0]["cost"] == 10
    assert result["router_ids"] == {}


@pytest.mark.parametrize("status", ["failed", "unsupported", "unknown", None, [], {}])
def test_unusable_wrapped_capability_does_not_consume_carried_observations(status):
    rows = facts()
    rows["ospf_interfaces"] = {"status": status, "summary": rows["ospf_interfaces"]}
    result = device(project_ospf({"a": rows}))
    assert result["interfaces"] == []
    assert "ospf_capability_unusable" in codes(result)


def test_empty_verified_device_has_no_invented_rid_or_cost():
    result = device(project_ospf({"a": {"ospf_interfaces": {}, "ospf_process": {}, "ip": []}}))
    assert result["router_ids"] == {} and result["interfaces"] == [] and result["gaps"] == []


def test_common_cost_is_a_uint64_json_string_with_no_reverse_metric():
    projected = pair()
    result = link_metric(projected, pair_refs())
    assert result["metric1"] == "10"
    assert "metric2" not in result and not result["gaps"]
    assert [row["process_id"] for row in result["evidence"]] == ["1", "99"]


def test_link_cost_binds_each_device_local_instance_without_requiring_equal_names():
    projected = project_ospf({"a": facts(vrf="BLUE"), "b": facts(vrf="MGMT", address="10.0.0.2")})
    result = link_metric(projected, [ref(vrf="BLUE"), ref("b", vrf="MGMT", ip="10.0.0.2")])
    assert result["metric1"] == "10" and not result["gaps"]
    assert [row["vrf"] for row in result["evidence"]] == ["BLUE", "MGMT"]


def test_actual_asymmetric_shape_one_versus_ten_stays_per_interface():
    projected = pair(1, 10)
    result = link_metric(projected, pair_refs())
    assert "metric1" not in result and "metric2" not in result
    assert "asymmetric_ospf_cost" in codes(result)
    assert [row["cost"] for row in result["evidence"]] == [1, 10]
    assert device(projected)["interfaces"][0]["cost"] == 1
    assert device(projected, "b")["interfaces"][0]["cost"] == 10


def test_all_mp2mp_terminations_must_have_cost_evidence():
    projected = pair()
    projected["devices"]["c"] = device(project_ospf({"a": facts(address="10.0.0.3")}))
    refs = [*pair_refs(), ref("c", ip="10.0.0.3")]
    assert link_metric(projected, refs)["metric1"] == "10"
    refs.append(ref("host", name="nic", ip="10.0.0.4"))
    result = link_metric(projected, refs)
    assert "metric1" not in result
    assert "ospf_interface_unavailable_or_ambiguous" in codes(result)
    assert len(result["evidence"]) == 3


def test_area_is_retained_as_fact_and_not_required_to_match_across_endpoints():
    projected = pair()
    device(projected, "b")["interfaces"][0]["area"] = "0.0.0.10"
    result = link_metric(projected, pair_refs())
    assert result["metric1"] == "10"
    assert {row["area"] for row in result["evidence"]} == {"0.0.0.0", "0.0.0.10"}


def test_multiple_processes_on_same_interface_address_are_not_collapsed_even_for_same_cost():
    rows = facts()
    rows["ospf_interfaces"]["interfaces"].append(interface(pid="2"))
    projected = project_ospf({"a": rows, "b": facts(address="10.0.0.2")})
    result = link_metric(projected, pair_refs())
    assert "metric1" not in result
    assert "ospf_interface_unavailable_or_ambiguous" in codes(result)


@pytest.mark.parametrize("change", ["duplicate", "host", "missing_address", "wrong_scope", "ipv6"])
def test_missing_damaged_or_duplicate_termination_never_uses_remaining_costs(change):
    refs = pair_refs()
    if change == "duplicate":
        refs.append(deepcopy(refs[0]))
    elif change == "host":
        refs[1]["address"] = "host"
    elif change == "missing_address":
        del refs[1]["address_ip"]
    elif change == "wrong_scope":
        refs[1]["vrf"] = "BLUE"
    elif change == "ipv6":
        refs[1]["address_ip"] = "2001:db8::1"
    result = link_metric(pair(), refs)
    assert "metric1" not in result and result["gaps"]


def test_zero_loopback_cost_is_kept_as_fact_but_not_a_transmitting_link_cost():
    projected = pair(0, 0)
    assert device(projected)["interfaces"][0]["cost"] == 0
    result = link_metric(projected, pair_refs())
    assert "metric1" not in result
    assert "invalid_ospf_link_cost" in codes(result)


@pytest.mark.parametrize("bad", [None, [], "bad", True])
def test_malformed_public_inputs_are_reported_instead_of_crashing(bad):
    assert "invalid_devices" in codes(project_ospf(bad))
    assert link_metric(bad, pair_refs())["gaps"]
    assert link_metric(pair(), bad)["gaps"]


def test_invalid_device_or_interface_container_blocks_projection():
    rows = facts()
    rows["ospf_interfaces"]["interfaces"] = None
    result = project_ospf({"a": rows, "b": None})
    assert "ospf_device_projection_blocked" in codes(device(result))
    assert "invalid_device_facts" in codes(device(result, "b"))


def test_malformed_projection_device_interfaces_do_not_crash_link_lookup():
    projected = pair()
    device(projected)["interfaces"] = None
    assert "metric1" not in link_metric(projected, pair_refs())


@pytest.mark.parametrize(
    "name,expected_costs,raw_count",
    [
        ("br1", [0, 1, 1, 1], 8),
        ("br2", [0, 1, 1, 1], 8),
        ("core1", [1, 10, 10, 10, 10, 10], 6),
        ("core2", [1, 10, 10, 10, 10, 10], 6),
        ("dist1", [1, 1, 1, 10, 10], 5),
        ("dist2", [1, 1, 1, 10, 10], 5),
        ("acc1", [], 0),
        ("acc2", [], 0),
    ],
)
def test_real_campus_helper_keeps_costs_and_original_observations(name, expected_costs, raw_count):
    family = "comware" if name.startswith("br") else "ios"
    ip_file = "ip_interfaces.txt" if family == "comware" else "ip_interface.txt"
    ip = parse_generic({"os_family": family}, "ip", (CAPTURES / name / ip_file).read_text("utf-8"))
    brief = generic_ospf.parse_interfaces(
        (CAPTURES / "ospf" / name / "ospf_interfaces.txt").read_text("utf-8"), family
    )
    full = generic_ospf.parse_process(
        (CAPTURES / "ospf" / name / "ospf_process.txt").read_text("utf-8"), family
    )
    assert ip.status == "complete" and not brief.rejected and not full.rejected
    facts = {"ip": ip.records, "ospf_interfaces": brief.summary, "ospf_process": full.summary}
    original = deepcopy(facts)
    projected = device(project_ospf({"a": facts}))
    assert sorted(row["cost"] for row in projected["interfaces"]) == expected_costs
    assert all(row["usable"] and row["vrf"] == "default" for row in projected["interfaces"])
    assert len(projected["observations"]) == raw_count
    assert projected["processes"] == full.summary.get("processes", [])
    assert not projected["gaps"] and facts == original
    if family == "comware":
        assert all("ifname" not in row for row in brief.summary["interfaces"])
        assert all(
            row["sources"] == ["ospf_interfaces", "ospf_process"] for row in projected["interfaces"]
        )
    elif expected_costs:
        assert all("vrf" not in row for row in full.summary["processes"])


@pytest.mark.parametrize("capability", ["ospf_interfaces", "ospf_process"])
@pytest.mark.parametrize("source", ["metadata", "wrapper", "marker"])
def test_incomplete_collection_is_not_unique_scope_or_cost_evidence(capability, source):
    rows = facts()
    if source == "metadata":
        rows[capability]["parse_metadata"] = {"warnings": ["collection_incomplete"]}
    elif source == "wrapper":
        rows[capability] = {
            "summary": rows[capability],
            "status": "partial",
            "warnings": ["collection_incomplete"],
        }
    else:
        rows[capability]["collection_incomplete"] = True
    result = device(project_ospf({"a": rows}))
    assert result["interfaces"] == [] and result["router_ids"] == {}
    assert "ospf_collection_incomplete" in codes(result)
    assert result["observations"][0]["record"]["cost"] == 10


def test_public_incomplete_transport_veto_survives_fact_summary(monkeypatch):
    from src.apps.topology.backend.collectors.parse import PARSERS

    monkeypatch.setitem(PARSERS, "ospf_interfaces", generic_ospf.parse_interfaces)
    captured = """Interface PID Area IP Address/Mask Cost State Nbrs F/C
Et0/1 1 0 10.0.0.1/30 10 DR 1/1
"""
    parsed = parse_generic({"os_family": "ios"}, "ospf_interfaces", captured, complete=False)
    assert parsed.status == "partial" and parsed.summary["interfaces"]
    rows = facts()
    rows["ospf_interfaces"] = parsed.fact_summary()
    result = device(project_ospf({"a": rows}))
    assert result["interfaces"] == [] and result["router_ids"] == {}
    assert "ospf_collection_incomplete" in codes(result)


@pytest.mark.parametrize("metadata", [None, "bad", {"warnings": None}])
def test_damaged_parse_metadata_cannot_hide_collection_completeness(metadata):
    rows = facts()
    rows["ospf_interfaces"]["parse_metadata"] = metadata
    result = device(project_ospf({"a": rows}))
    assert result["interfaces"] == [] and result["router_ids"] == {}
    assert "invalid_ospf_parse_metadata" in codes(result)
