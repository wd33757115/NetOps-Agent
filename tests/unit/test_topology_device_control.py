"""Native routing/LLDP trees require their observed keys and explicit scope."""

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.apps.topology.backend.collectors.identify import Identity
from src.apps.topology.backend.collectors.parse import parse
from src.apps.topology.backend.model.device_control import project_device_control
from src.apps.topology.backend.service import topology_observation

ROOT = Path(__file__).resolve().parents[2]
CAPTURES = ROOT / "tests/fixtures/topology/campus-wan"
YANG = ROOT / "src/apps/topology/resources/yang"
IDENTITIES = {
    "comware": Identity("H3C", "comware", "h3c-comware7", "display", "high", "identified"),
    "ios": Identity("Cisco", "ios", "cisco-ios", "show", "high", "identified"),
}


def codes(section):
    return {gap["code"] for gap in section["gaps"]}


def route(scope="blue", rib="actual-v4-rib", prefix="192.0.2.0/24", protocol="O_INTRA", hops=None):
    return {
        "vrf": scope,
        "rib_name": rib,
        "prefix": prefix,
        "protocol": protocol,
        "preference": 110,
        "metric": 45,
        "next_hops": hops if hops is not None else [{"ip": "10.0.0.2", "vrf": scope}],
    }


def ribs(section):
    return section["tree"]["ietf-routing:routing"].get("ribs", {}).get("rib", [])


def routes(section):
    return [row for rib in ribs(section) for row in rib["routes"]["route"]]


def local(section):
    return section["tree"]["ieee802-dot1ab-lldp:lldp"].get("local-system-data", {})


def observe(device):
    family = "comware" if device.startswith("br") else "ios"
    files = {"routes": "routes", "lldp": "lldp_verbose", "lldp_local": "lldp_local"}
    parsed = {
        cap: parse(
            IDENTITIES[family], cap, (CAPTURES / device / f"{filename}.txt").read_text("utf-8")
        )
        for cap, filename in files.items()
        if (CAPTURES / device / f"{filename}.txt").exists()
    }
    data = topology_observation(
        SimpleNamespace(
            parsed=parsed,
            identity=IDENTITIES[family],
            profile_id=IDENTITIES[family].profile_id,
            mode="topology",
            driver="paramiko",
        )
    )
    assert data["capabilities"]["routes"] == "complete"
    return {"id": f"observed-{device}", "observed_at": "2026-10-03T02:00:00Z", "data": data}


@pytest.mark.parametrize(
    "device", ["br1", "br2", "core1", "core2", "dist1", "dist2", "acc1", "acc2"]
)
def test_real_routes_enter_a_rib_named_from_instance_and_family(device):
    # The device prints no RIB name: one is derived from instance and address family
    # (owner decision 2026-10-06) and metadata says so.
    observation = observe(device)
    before = copy.deepcopy(observation)
    projected = project_device_control(observation)
    raw = observation["data"]["facts"]["routes"]
    assert all("rib_name" not in row for row in raw)
    assert len(projected["routing"]) == 1
    section = projected["routing"][0]
    assert section["vrf"] == ("default" if raw else None)
    if raw:
        assert [rib["name"] for rib in ribs(section)] == ["ipv4"]
        assert section["metadata"]["derived_rib_names"]["ipv4"]["network_instance"] == "default"
        # Without interface facts in this observation, a route with an outgoing
        # interface cannot reference it: such routes are gaps, kept by prefix.
        held = {row["ietf-ipv4-unicast-routing:destination-prefix"] for row in routes(section)}
        left = {
            raw[gap["record_index"]]["prefix"]
            for gap in section["gaps"]
            if gap["code"] == "route_next_hop_not_projected"
        }
        assert held | left == {row["prefix"] for row in raw} and not held & left
        assert "native_rib_name_not_observed" not in codes(section)
    else:
        assert section["tree"] == {"ietf-routing:routing": {}}
    assert section["metadata"]["records"] == raw
    assert section["metadata"]["completeness"] == "partial"
    assert section["metadata"]["full_validation"] == "not_performed"
    assert section["metadata"]["observation_id"] == observation["id"]
    assert observation == before


@pytest.mark.parametrize("device", ["br1", "br2"])
def test_real_local_lldp_keeps_names_but_never_guesses_chassis_encoding(device):
    observation = observe(device)
    section = project_device_control(observation)["lldp"]
    raw = observation["data"]["facts"]["lldp_local"]
    assert local(section) == {
        "system-name": raw["hostname"],
        "system-description": raw["system_description"],
    }
    assert "local_chassis_subtype_not_observed_or_invalid" in codes(section)
    assert "native_lldp_keys_not_observed" in codes(section)
    assert section["metadata"]["local_facts"] == raw
    assert section["metadata"]["neighbor_records"] == observation["data"]["facts"]["lldp"]
    assert "port" not in section["tree"]["ieee802-dot1ab-lldp:lldp"]


def test_missing_scope_does_not_become_default_or_a_native_rib_name():
    record = route(scope=None, rib=None)
    section = project_device_control({"routes": [record]})["routing"][0]
    assert section["vrf"] is None and ribs(section) == []
    assert codes(section) == {"route_scope_not_observed"}
    assert section["metadata"]["records"] == [record]


def test_two_vrfs_with_same_native_rib_name_remain_separate_instances():
    a, b = route(), route(scope="red", prefix="198.51.100.0/24")
    result = project_device_control({"routes": [a, b]})["routing"]
    assert [section["vrf"] for section in result] == ["blue", "red"]
    assert [ribs(section)[0]["name"] for section in result] == ["actual-v4-rib"] * 2
    assert [len(routes(section)) for section in result] == [1, 1]
    assert "vrf" not in json.dumps([section["tree"] for section in result])


def test_explicit_rib_with_conflicting_address_families_is_not_split_using_invented_keys():
    result = project_device_control({"routes": [route(), route(prefix="2001:db8::/64")]})[
        "routing"
    ][0]
    assert ribs(result) == []
    assert "native_rib_address_family_conflict" in codes(result)


@pytest.mark.parametrize("protocol", ["D", "RIP", "O_FAKE", "ietf-routing:invented", [], None])
def test_unfrozen_or_unknown_protocol_identity_is_preserved_as_a_gap(protocol):
    record = route(protocol=protocol)
    result = project_device_control({"routes": [record]})["routing"][0]
    assert routes(result) == []
    assert codes(result) == {"source_protocol_identity_unmapped"}
    assert result["metadata"]["records"][0]["protocol"] == protocol


@pytest.mark.parametrize(
    "protocol,identity",
    [
        ("C", "ietf-routing:direct"),
        ("L", "ietf-routing:direct"),
        ("Direct", "ietf-routing:direct"),
        ("S", "ietf-routing:static"),
        ("Static", "ietf-routing:static"),
        ("O IA", "ietf-ospf:ospf"),
        ("O E2", "ietf-ospf:ospf"),
        ("O_INTRA", "ietf-ospf:ospf"),
        # draft-ietf-idr-bgp-model (fixed draft, M4): validated with libyang only.
        ("B", "ietf-bgp:bgp"),
        ("BGP", "ietf-bgp:bgp"),
    ],
)
def test_supported_protocols_use_actual_frozen_native_identities(protocol, identity):
    result = project_device_control({"routes": [route(protocol=protocol)]})["routing"][0]
    entry = routes(result)[0]
    assert entry["source-protocol"] == identity
    assert entry["route-preference"] == 110
    assert set(entry) == {
        "ietf-ipv4-unicast-routing:destination-prefix",
        "source-protocol",
        "route-preference",
        "next-hop",
    }


@pytest.mark.parametrize("preference", [-1, 2**32, True, "110", []])
def test_invalid_preference_is_not_coerced(preference):
    record = route()
    record["preference"] = preference
    section = project_device_control({"routes": [record]})["routing"][0]
    assert "route-preference" not in routes(section)[0]
    assert "invalid_route_preference" in codes(section)


UNPROVEN = [{"prefix": "192.0.2.0/24", "address": "10.0.0.2", "interface": "Ethernet0/1"}]


def kept_without_interface(section):
    """The route stays, with its next-hop address; metadata names the interface the tree
    does not prove, and the interface tree is not built from it."""
    assert routes(section)[0]["next-hop"] == {
        "ietf-ipv4-unicast-routing:next-hop-address": "10.0.0.2"
    }
    assert section["metadata"]["next_hop_interfaces_unproven"] == UNPROVEN
    assert "ietf-interfaces:interfaces" not in section["tree"]
    assert "route_next_hop_not_projected" not in codes(section)


def test_interface_refs_require_current_identity_and_explicit_same_vrf_ownership():
    record = route(hops=[{"ip": "10.0.0.2", "ifname": "Ethernet0/1", "vrf": "blue"}])
    facts = {
        "routes": [record],
        "interfaces": [{"ifname": "Ethernet0/1", "type": "ethernet", "oper_status": "up"}],
        "ip": [{"ifname": "Ethernet0/1", "vrf": "blue", "address": "10.0.0.1", "prefix_len": 24}],
    }
    result = project_device_control(facts)["routing"][0]
    assert routes(result)[0]["next-hop"]["outgoing-interface"] == "Ethernet0/1"
    assert result["tree"]["ietf-interfaces:interfaces"]["interface"] == [
        {"name": "Ethernet0/1", "type": "iana-if-type:ethernetCsmacd", "oper-status": "up"}
    ]
    assert codes(result) == {"mandatory_statistics_not_observed", "admin_state_not_observed"}
    # The interface proven in another instance: the tree keeps the next hop's address,
    # metadata names the interface (a VPN route over the underlay).
    changed = copy.deepcopy(facts)
    changed["ip"][0]["vrf"] = "red"
    outside = project_device_control(changed)["routing"][0]
    assert routes(outside)[0]["next-hop"] == {
        "ietf-ipv4-unicast-routing:next-hop-address": "10.0.0.2"
    }
    assert outside["metadata"]["next_hops_outside_instance"] == [
        {
            "prefix": "192.0.2.0/24",
            "address": "10.0.0.2",
            "interface": "Ethernet0/1",
            "interface_instance": "red",
        }
    ]
    assert "ietf-interfaces:interfaces" not in outside["tree"]
    # Its instance not proven: the address stays in the tree, the interface in metadata.
    changed["ip"][0]["vrf"] = None
    kept_without_interface(project_device_control(changed)["routing"][0])
    # Without an address there is nothing to keep: the route is a gap.
    changed["routes"][0]["next_hops"][0]["ip"] = None
    rejected = project_device_control(changed)["routing"][0]
    assert routes(rejected) == []
    assert {"outgoing_interface_reference_not_proven", "route_next_hop_not_projected"} <= codes(
        rejected
    )


def test_duplicate_or_failed_interfaces_cannot_supply_native_references():
    records = [{"ifname": "Ethernet0/1", "type": "ethernet", "oper_status": "up"}] * 2
    facts = {
        "routes": [route(hops=[{"ip": "10.0.0.2", "ifname": "Ethernet0/1", "vrf": "blue"}])],
        "interfaces": records,
        "ip": [{"ifname": "Ethernet0/1", "vrf": "blue"}],
    }
    for observation in (
        facts,
        {
            "facts": facts,
            "capabilities": {"routes": "complete", "ip": "complete", "interfaces": "failed"},
        },
    ):
        kept_without_interface(project_device_control(observation)["routing"][0])


def test_missing_or_conflicting_current_interface_scope_does_not_supply_native_ref():
    record = route(hops=[{"ip": "10.0.0.2", "ifname": "Ethernet0/1", "vrf": "blue"}])
    facts = {
        "routes": [record],
        "interfaces": [{"ifname": "Ethernet0/1", "type": "ethernet", "oper_status": "up"}],
        "ip": [{"ifname": "Ethernet0/1", "vrf": "blue"}, {"ifname": "Ethernet0/1"}],
    }
    kept_without_interface(project_device_control(facts)["routing"][0])
    facts["ip"][1]["vrf"] = "red"
    kept_without_interface(project_device_control(facts)["routing"][0])


def test_special_and_simple_next_hop_evidence_conflict_is_not_arbitrarily_selected():
    record = route()
    record["special_next_hop"] = "blackhole"
    section = project_device_control({"routes": [record]})["routing"][0]
    assert routes(section) == []  # RFC 8349 next-hop is the route's forwarding
    assert "conflicting_route_next_hop" in codes(section)


def test_recursive_next_hop_address_needs_no_invented_interface_or_index():
    result = project_device_control({"routes": [route()]})["routing"][0]
    assert routes(result)[0]["next-hop"] == {
        "ietf-ipv4-unicast-routing:next-hop-address": "10.0.0.2"
    }
    assert "ietf-interfaces:interfaces" not in result["tree"]
    assert not {"index", "active", "last-updated", "metric"} & set(routes(result)[0])


def test_cross_vrf_next_hop_keeps_its_instance_in_metadata():
    # RFC 8349 has no instance for a next hop: a leaked route keeps the address in the
    # tree, and metadata says where it resolves.
    section = project_device_control({"routes": [route(hops=[{"ip": "10.0.0.2", "vrf": "red"}])]})[
        "routing"
    ][0]
    assert routes(section)[0]["next-hop"] == {
        "ietf-ipv4-unicast-routing:next-hop-address": "10.0.0.2"
    }
    assert section["metadata"]["next_hops_outside_instance"] == [
        {"prefix": "192.0.2.0/24", "address": "10.0.0.2", "network_instance": "red"}
    ]
    # Without an address there is nothing to resolve there: a gap.
    blind = project_device_control({"routes": [route(hops=[{"ifname": "X1", "vrf": "red"}])]})[
        "routing"
    ][0]
    assert routes(blind) == []
    assert codes(blind) == {"next_hop_scope_not_proven", "route_next_hop_not_projected"}


@pytest.mark.parametrize("address", ["2001:db8::1", "malformed", True, 25])
def test_invalid_or_cross_family_address_cannot_become_interface_only_next_hop(address):
    facts = {
        "routes": [route(hops=[{"ip": address, "ifname": "Ethernet0/1", "vrf": "blue"}])],
        "interfaces": [{"ifname": "Ethernet0/1", "type": "ethernet", "oper_status": "up"}],
        "ip": [{"ifname": "Ethernet0/1", "vrf": "blue", "address": "10.0.0.1", "prefix_len": 24}],
    }
    section = project_device_control(facts)["routing"][0]
    assert routes(section) == []
    assert "ietf-interfaces:interfaces" not in section["tree"]
    assert codes(section) == {"invalid_next_hop_address", "route_next_hop_not_projected"}


def test_ecmp_bad_hop_is_omitted_without_becoming_a_different_interface_hop():
    facts = {
        "routes": [
            route(
                hops=[
                    {"ip": "10.0.0.2", "vrf": "blue"},
                    {"ip": "2001:db8::1", "ifname": "Ethernet0/1", "vrf": "blue"},
                ]
            )
        ],
        "interfaces": [{"ifname": "Ethernet0/1", "type": "ethernet", "oper_status": "up"}],
        "ip": [{"ifname": "Ethernet0/1", "vrf": "blue", "address": "10.0.0.1", "prefix_len": 24}],
    }
    section = project_device_control(facts)["routing"][0]
    # One ECMP hop left out would send traffic over the others only: no half route.
    assert routes(section) == []
    assert "ietf-interfaces:interfaces" not in section["tree"]
    assert codes(section) == {"invalid_next_hop_address", "route_next_hop_not_projected"}


@pytest.mark.parametrize("status", ["failed", "unsupported", "unknown", None, [], True])
def test_failed_capabilities_cannot_project_carried_control_facts(status):
    observation = {
        "data": {
            "facts": {"routes": [route()], "lldp_local": {"hostname": "stale"}, "lldp": [{}]},
            "capabilities": {"routes": status, "lldp_local": status, "lldp": status},
        }
    }
    result = project_device_control(observation)
    assert ribs(result["routing"][0]) == [] and local(result["lldp"]) == {}
    assert "capability_unusable" in codes(result["routing"][0])
    assert "capability_unusable" in codes(result["lldp"])


def test_lldp_failure_does_not_block_a_current_complete_routes_capability():
    observation = {
        "data": {
            "complete": False,
            "facts": {"routes": [route()]},
            "capabilities": {"routes": "complete", "lldp": "failed", "lldp_local": "unsupported"},
        }
    }
    result = project_device_control(observation)
    assert len(routes(result["routing"][0])) == 1
    assert "collection_partial" not in codes(result["routing"][0])


def test_partial_routes_are_explicitly_partial_without_filling_absent_evidence():
    result = project_device_control(
        {"facts": {"routes": [route()]}, "capabilities": {"routes": "partial"}}
    )["routing"][0]
    assert len(routes(result)) == 1
    assert "collection_partial" in codes(result)
    assert "observed_at" not in result["metadata"]


@pytest.mark.parametrize("cap", ["routes", "lldp_local", "lldp"])
def test_flat_transport_metadata_blocks_current_control_projection_but_keeps_raw_facts(cap):
    facts = {
        "routes": [route()],
        "lldp_local": {"hostname": "node"},
        "lldp": [{"local_if": "Ethernet0/1", "remote_port_id": "Ethernet0/2"}],
    }
    metadata = {"tier": "generic", "warnings": ["collection_incomplete"], "sources": []}
    observation = {
        "data": {
            "facts": facts,
            "capabilities": {name: "partial" for name in facts},
            "fact_metadata": {cap: metadata},
        }
    }
    before = copy.deepcopy(observation)
    result = project_device_control(observation)
    section = result["routing"][0] if cap == "routes" else result["lldp"]
    assert {"code": "transport_collection_incomplete", "capability": cap} in section["gaps"]
    assert section["metadata"]["fact_metadata"][cap] == metadata
    if cap == "routes":
        assert ribs(section) == []
        assert section["metadata"]["records"] == facts[cap]
    elif cap == "lldp_local":
        assert local(section) == {}
        assert section["metadata"]["local_facts"] == facts[cap]
    else:
        assert "native_lldp_keys_not_observed" not in codes(section)
        assert section["metadata"]["neighbor_records"] == facts[cap]
    assert observation == before


@pytest.mark.parametrize("cap", ["ip", "interfaces"])
def test_transport_incomplete_reference_capability_cannot_close_current_route_interface(cap):
    facts = {
        "routes": [route(hops=[{"ip": "10.0.0.2", "ifname": "Ethernet0/1", "vrf": "blue"}])],
        "interfaces": [{"ifname": "Ethernet0/1", "type": "ethernet", "oper_status": "up"}],
        "ip": [{"ifname": "Ethernet0/1", "vrf": "blue", "address": "10.0.0.1", "prefix_len": 24}],
    }
    metadata = {"warnings": ["collection_incomplete"]}
    result = project_device_control(
        {
            "data": {
                "facts": facts,
                "capabilities": {name: "complete" for name in facts},
                "fact_metadata": {cap: metadata},
            }
        }
    )["routing"][0]
    kept_without_interface(result)  # its interface cannot be referenced
    assert {"code": "transport_collection_incomplete", "capability": cap} in result["gaps"]
    assert result["metadata"]["reference_facts"][cap] == facts[cap]
    assert result["metadata"]["fact_metadata"][cap] == metadata


@pytest.mark.parametrize(
    "warnings",
    [[], ["line:1:bad_row"], ["advertised_count_mismatch"], ["line:1:collection_incomplete"]],
)
def test_local_partial_parse_warnings_do_not_become_transport_incompleteness(warnings):
    facts = {"routes": [route()], "lldp_local": {"hostname": "node"}, "lldp": []}
    result = project_device_control(
        {
            "data": {
                "complete": False,
                "facts": facts,
                "capabilities": {cap: "partial" for cap in facts},
                "fact_metadata": {cap: {"warnings": warnings} for cap in facts},
            }
        }
    )
    assert len(routes(result["routing"][0])) == 1 and local(result["lldp"])["system-name"] == "node"
    assert "collection_partial" in codes(result["routing"][0])
    assert "transport_collection_incomplete" not in codes(result["routing"][0])
    assert "transport_collection_incomplete" not in codes(result["lldp"])


def test_embedded_summary_transport_metadata_is_also_an_explicit_veto():
    metadata = {"warnings": ["collection_incomplete"]}
    result = project_device_control(
        {"lldp_local": {"hostname": "node", "parse_metadata": metadata}}
    )["lldp"]
    assert local(result) == {}
    assert result["metadata"]["embedded_fact_metadata"]["lldp_local"] == metadata
    assert {"code": "transport_collection_incomplete", "capability": "lldp_local"} in result["gaps"]


def test_real_partial_route_capture_metadata_remains_a_transport_veto_after_typed_list_storage():
    parsed = parse(
        IDENTITIES["ios"],
        "routes",
        (CAPTURES / "dist1/routes.txt").read_text("utf-8"),
        complete=False,
    )
    assert parsed.status == "partial" and "collection_incomplete" in parsed.warnings
    records = copy.deepcopy(parsed.records)
    for record in records:
        record["rib_name"] = "explicit-synthetic-control-rib"
    metadata = parsed.fact_summary()["parse_metadata"]
    observation = {
        "data": {
            "facts": {"routes": records},
            "capabilities": {"routes": parsed.status},
            "fact_metadata": {"routes": metadata},
        }
    }
    section = project_device_control(observation)["routing"][0]
    assert ribs(section) == [] and section["metadata"]["records"] == records
    assert {"code": "transport_collection_incomplete", "capability": "routes"} in section["gaps"]
    # A local parse warning alone keeps individually validated typed rows.
    observation["data"]["fact_metadata"]["routes"]["warnings"] = ["line:1:bad_row"]
    assert routes(project_device_control(observation)["routing"][0])


@pytest.mark.parametrize("subtype", [None, "mac", "unknown", [], "invented"])
def test_mac_shaped_local_chassis_needs_explicit_native_subtype(subtype):
    result = project_device_control(
        {
            "lldp_local": {
                "hostname": "node",
                "chassis_id": "00:11:22:33:44:55",
                "chassis_subtype": subtype,
            }
        }
    )["lldp"]
    assert local(result) == {"system-name": "node"}
    assert "local_chassis_subtype_not_observed_or_invalid" in codes(result)


def test_lldp_native_remote_table_keys_are_not_invented_from_neighbor_order_or_time():
    record = {
        "local_if": "Ethernet0/1",
        "remote_chassis_id": "00:11:22:33:44:55",
        "remote_port_id": "Ethernet0/2",
        "remote_sysname": "peer",
    }
    result = project_device_control(
        {"observed_at": "2026-10-03T02:00:00Z", "data": {"facts": {"lldp": [record]}}}
    )["lldp"]
    assert result["tree"] == {"ieee802-dot1ab-lldp:lldp": {}}
    assert result["metadata"]["neighbor_records"] == [record]
    assert result["gaps"][-1] == {
        "code": "native_lldp_keys_not_observed",
        "record_index": 0,
        "missing_keys": ["dest_mac_address", "time_mark", "remote_index"],
    }
    assert "index" not in json.dumps(result["tree"])


def test_lldp_subtype_conflict_does_not_choose_one_encoding():
    section = project_device_control(
        {
            "lldp_local": {
                "hostname": "node",
                "chassis_id": "00:11:22:33:44:55",
                "chassis_subtype": "local",
                "chassis_id_subtype": "mac-address",
            }
        }
    )["lldp"]
    assert local(section) == {"system-name": "node"}
    assert "conflicting_local_chassis_subtype" in codes(section)


@pytest.mark.parametrize("chassis", ["01:11:22:33:44:55", "broadcast", "00:11:22:33:44:55\n"])
def test_native_mac_chassis_requires_unicast_source_encoding(chassis):
    section = project_device_control(
        {"lldp_local": {"chassis_id": chassis, "chassis_subtype": "mac-address"}}
    )["lldp"]
    assert local(section) == {}
    assert "invalid_local_chassis_id" in codes(section)


def test_network_chassis_id_does_not_fabricate_native_af_octet_encoding():
    section = project_device_control(
        {"lldp_local": {"chassis_id": "192.0.2.1", "chassis_subtype": "network-address"}}
    )["lldp"]
    assert local(section) == {}
    assert "local_chassis_network_encoding_not_observed" in codes(section)
    assert section["metadata"]["local_facts"]["chassis_id"] == "192.0.2.1"


def test_lldp_independent_capability_results_keep_both_status_sources():
    section = project_device_control(
        {
            "facts": {"lldp": [], "lldp_local": {"hostname": "stale"}},
            "capabilities": {"lldp": "complete", "lldp_local": "failed"},
        }
    )["lldp"]
    assert local(section) == {}
    assert section["metadata"]["capabilities"] == {"lldp": "complete", "lldp_local": "failed"}
    assert "capability_unusable" in codes(section)


@pytest.mark.parametrize("value", [None, [], {"data": None}, {"facts": None}])
def test_invalid_observation_stays_explicitly_partial(value):
    result = project_device_control(value)
    assert result["gaps"]
    assert ribs(result["routing"][0]) == [] and local(result["lldp"]) == {}


@pytest.fixture(scope="module")
def model():
    yangson = pytest.importorskip("yangson", reason="Install tests/topology-requirements.txt")
    return yangson.DataModel((YANG / "device-library.json").read_text("utf-8"), [str(YANG)])


def validate(model, tree):
    model.from_raw(tree).validate(ctype=pytest.importorskip("yangson.enumerations").ContentType.all)


@pytest.mark.parametrize("version", [4, 6])
def test_native_ecmp_has_correct_af_address_leaves_and_no_operational_index(model, version):
    ips = ["10.0.0.2", "10.0.0.3"] if version == 4 else ["2001:db8:1::2", "2001:db8:1::3"]
    prefix = "192.0.2.0/24" if version == 4 else "2001:db8:2::/64"
    result = project_device_control(
        {"routes": [route(prefix=prefix, hops=[{"ip": ip, "vrf": "blue"} for ip in ips])]}
    )["routing"][0]
    entries = routes(result)[0]["next-hop"]["next-hop-list"]["next-hop"]
    namespace = f"ietf-ipv{version}-unicast-routing"
    assert entries == [{f"{namespace}:address": ip} for ip in ips]
    assert all("index" not in hop for hop in entries)
    validate(model, result["tree"])
    # The static configuration index leaf is not legal in operational routes.
    invalid = copy.deepcopy(result["tree"])
    invalid["ietf-routing:routing"]["ribs"]["rib"][0]["routes"]["route"][0]["next-hop"][
        "next-hop-list"
    ]["next-hop"][0]["index"] = "fabricated"
    with pytest.raises(Exception, match="index"):
        validate(model, invalid)


@pytest.mark.parametrize("protocol", ["Direct", "Static", "O_INTRA"])
def test_protocol_identities_validate_against_complete_frozen_dependency_closure(model, protocol):
    result = project_device_control({"routes": [route(protocol=protocol)]})["routing"][0]
    validate(model, result["tree"])
    assert result["metadata"]["completeness"] == "partial"
    assert result["metadata"]["full_validation"] == "not_performed"


@pytest.mark.parametrize("special", ["blackhole", "unreachable", "prohibit", "receive"])
def test_only_explicit_special_next_hop_semantics_project_and_validate(model, special):
    record = route(hops=[])
    record["special_next_hop"] = special
    section = project_device_control({"routes": [record]})["routing"][0]
    assert routes(section)[0]["next-hop"] == {"special-next-hop": special}
    validate(model, section["tree"])


def test_native_partial_interfaces_remain_subject_to_unmodified_mandatory_rules(model):
    facts = {
        "routes": [route(hops=[{"ifname": "Ethernet0/1", "vrf": "blue"}])],
        "interfaces": [{"ifname": "Ethernet0/1", "type": "ethernet", "oper_status": "up"}],
        "ip": [{"ifname": "Ethernet0/1", "vrf": "blue", "address": "10.0.0.1", "prefix_len": 24}],
    }
    result = project_device_control(facts)["routing"][0]
    with pytest.raises(Exception, match="statistics|discontinuity-time"):
        validate(model, result["tree"])
    completed = copy.deepcopy(result["tree"])
    completed["ietf-interfaces:interfaces"]["interface"][0]["statistics"] = {
        "discontinuity-time": "2026-01-01T00:00:00Z"
    }
    validate(model, completed)
    assert "statistics" not in result["tree"]["ietf-interfaces:interfaces"]["interface"][0]


def test_known_native_chassis_encoding_and_local_fields_validate(model):
    facts = {
        "lldp_local": {
            "hostname": "node",
            "system_description": "observed\nmodel",
            "chassis_id": "00:11:22:33:44:55",
            "chassis_subtype": "mac-address",
        },
        "lldp": [],
    }
    result = project_device_control(facts)["lldp"]
    assert local(result)["chassis-id"] == "00-11-22-33-44-55"
    assert local(result)["chassis-id-subtype"] == "mac-address"
    validate(model, result["tree"])


def test_empty_native_instances_are_not_claimed_as_complete_device_exports(model):
    result = project_device_control({"routes": [], "lldp": [], "lldp_local": {}})
    validate(model, result["routing"][0]["tree"])
    validate(model, result["lldp"]["tree"])
    assert all(
        section["metadata"]["completeness"] == "partial"
        for section in [*result["routing"], result["lldp"]]
    )


def test_device_library_has_complete_sources_and_no_trimming_or_unobserved_features():
    sources = json.loads((YANG / "sources.json").read_text("utf-8"))
    library = json.loads((YANG / "device-library.json").read_text("utf-8"))
    modules = library["ietf-yang-library:modules-state"]["module"]
    assert len(modules) == 28  # 24, and RFC 9899 with the IANA modules it imports
    expected = {
        "ietf-acl-enh",
        "iana-icmpv4-types",
        "iana-icmpv6-types",
        "iana-ipv6-ext-types",
        "ietf-ip",
        "ietf-routing",
        "ietf-ipv4-unicast-routing",
        "ietf-ipv6-unicast-routing",
        "ietf-ospf",
        "ietf-key-chain",
        "ietf-bfd-types",
        "iana-bfd-types",
        "ietf-netconf-acm",
        "ietf-vrrp",
        "ieee802-dot1ab-lldp",
        "ieee802-types",
        "ieee802-dot1ab-types",
        "ietf-network-instance",
        "ietf-yang-schema-mount",
    }
    assert expected <= {entry["name"] for entry in modules}
    # Only features the collected devices show: IPv4 ACL entries matching IPv4, TCP,
    # UDP and ICMP fields, applied to interfaces, with per-entry hit counters.
    features = {entry["name"]: entry["feature"] for entry in modules if entry.get("feature")}
    assert features == {
        "ietf-access-control-list": [
            "match-on-ipv4",
            "match-on-tcp",
            "match-on-udp",
            "match-on-icmp",
            "ipv4",
            "interface-attachment",
            "acl-aggregate-stats",
        ]
    }
    entries = [(entry["name"], entry["revision"]) for entry in modules]
    entries.extend(
        (sub["name"], sub["revision"]) for entry in modules for sub in entry.get("submodule", [])
    )
    assert ("ietf-ipv6-router-advertisements", "2018-03-13") in entries
    for name, revision in entries:
        filename = f"{name}@{revision}.yang"
        assert (
            hashlib.sha256((YANG / filename).read_bytes()).hexdigest()
            == sources[filename]["sha256"]
        )
    assert sources["ietf-vrrp@2018-03-13.yang"] == {
        "source": "https://www.iana.org/assignments/yang-parameters/ietf-vrrp@2018-03-13.yang",
        "sha256": "242358c009481e6708ec3a5b7b8d162056e34521134953dbd07862d6861ec708",
        "reference": "https://www.rfc-editor.org/rfc/rfc8347.html#section-4",
    }
    # Existing native modules keep their already frozen bytes.
    assert (
        sources["ietf-interfaces@2018-02-20.yang"]["sha256"]
        == "f6faea9938f0341ed48fda93dba9a69aa32ee7142c463342efec3d38f4eb3621"
    )
    assert (
        sources["ietf-inet-types@2025-12-22.yang"]["sha256"]
        == "2fb39c45cd28d95f60838e5f74ac509d780d18f3c5bf78db251049a4d022e266"
    )
