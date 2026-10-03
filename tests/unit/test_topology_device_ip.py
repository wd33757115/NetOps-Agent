"""Native IP, ARP and VRRP fields require explicit scope and keep mandatory gaps."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from src.apps.topology.backend.collectors.parse import parse_generic
from src.apps.topology.backend.model.device_ip import enrich_device_ip
from src.apps.topology.backend.model.device_state import project_device_state

ROOT = Path(__file__).resolve().parents[2]
CAPTURES = ROOT / "tests/fixtures/topology/campus-wan"


def observation(ip=None, arp=None, vrrp=None, interfaces=None):
    return {
        "id": "capture",
        "data": {
            "facts": {
                "interfaces": [{"ifname": "Vlan20", "type": "vlanif", "oper_status": "up"}]
                if interfaces is None
                else interfaces,
                "ip": [
                    {"ifname": "Vlan20", "address": "192.0.2.2", "prefix_len": 24, "vrf": "BLUE"}
                ]
                if ip is None
                else ip,
                "arp": [] if arp is None else arp,
                "vrrp": [] if vrrp is None else vrrp,
            },
            "capabilities": {
                "interfaces": "complete",
                "ip": "complete",
                "arp": "complete",
                "vrrp": "complete",
            },
        },
    }


def export(obs):
    return enrich_device_ip(project_device_state(obs), obs)


def entries(result):
    return result["tree"]["ietf-interfaces:interfaces"]["interface"]


def iface(result, name="Vlan20"):
    return next(row for row in entries(result) if row["name"] == name)


def codes(result):
    return {gap["code"] for gap in result["gaps"]}


def neighbor(**extra):
    return {
        "ifname": "Vlan20",
        "ip": "192.0.2.10",
        "mac": "02:00:00:00:00:10",
        "vrf": "BLUE",
        **extra,
    }


def virtual(**extra):
    return {
        "ifname": "Vlan20",
        "vrid": 20,
        "state": "master",
        "priority": 255,
        "virtual_addresses": ["192.0.2.1"],
        "last_adv_source": "192.0.2.2",
        **extra,
    }


def test_ip_extends_a_copy_with_only_observed_native_fields_and_scope_sidecar():
    obs = observation()
    projected = project_device_state(obs)
    original, base = deepcopy(obs), deepcopy(projected)
    result = enrich_device_ip(projected, obs)
    assert iface(result)["ietf-ip:ipv4"] == {"address": [{"ip": "192.0.2.2", "prefix-length": 24}]}
    assert result["metadata"]["interface_scopes"] == {"Vlan20": "BLUE"}
    assert obs == original and projected == base
    assert json.loads(json.dumps(result)) == result
    assert result["metadata"]["completeness"] == "partial"
    assert result["metadata"]["full_validation"] == "not_performed"
    assert "mandatory_statistics_not_observed" in codes(result)
    assert "vrf" not in iface(result)


def test_ipv4_and_ipv6_use_distinct_native_af_containers_on_same_explicit_scope():
    obs = observation(
        ip=[
            {"ifname": "Vlan20", "address": "192.0.2.2", "prefix_len": 24, "vrf": "BLUE"},
            {"ifname": "Vlan20", "address": "2001:0db8::2", "prefix_len": 64, "vrf": "BLUE"},
        ]
    )
    result = export(obs)
    assert iface(result)["ietf-ip:ipv6"] == {
        "address": [{"ip": "2001:db8::2", "prefix-length": 64}]
    }
    assert iface(result)["ietf-ip:ipv4"]["address"][0]["prefix-length"] == 24


@pytest.mark.parametrize(
    "field,value",
    [
        ("vrf", None),
        ("vrf", ""),
        ("prefix_len", None),
        ("prefix_len", True),
        ("prefix_len", -1),
        ("prefix_len", 33),
        ("address", "invalid"),
        ("address", 1),
        ("address", "fe80::1%3"),
        ("ifname", ""),
    ],
)
def test_invalid_ip_fact_is_not_repaired_or_guessed(field, value):
    obs = observation()
    obs["data"]["facts"]["ip"][0][field] = value
    result = export(obs)
    assert "ietf-ip:ipv4" not in iface(result)
    assert "ietf-ip:ipv6" not in iface(result)
    assert "invalid_ip_address_fact" in codes(result)


def test_valid_ipv6_prefix_can_reach_128_without_ipv4_limits():
    obs = observation(
        ip=[{"ifname": "Vlan20", "address": "2001:db8::2", "prefix_len": 128, "vrf": "BLUE"}]
    )
    assert iface(export(obs))["ietf-ip:ipv6"]["address"] == [
        {"ip": "2001:db8::2", "prefix-length": 128}
    ]


def test_same_interface_in_two_vrfs_is_not_folded_into_top_level_interfaces():
    obs = observation(arp=[neighbor()], vrrp=[virtual()])
    other = deepcopy(obs["data"]["facts"]["ip"][0])
    other.update(vrf="RED", address="198.51.100.2")
    obs["data"]["facts"]["ip"].append(other)
    result = export(obs)
    assert "ietf-ip:ipv4" not in iface(result)
    assert result["metadata"]["interface_scopes"] == {}
    assert "interface_multiple_vrf_scopes" in codes(result)


def test_duplicate_ip_key_is_not_selected_even_with_matching_rows():
    obs = observation()
    obs["data"]["facts"]["ip"] *= 2
    result = export(obs)
    assert "ietf-ip:ipv4" not in iface(result)
    assert "duplicate_ip_address_fact" in codes(result)


def test_missing_interface_observations_create_name_only_with_mandatory_gaps():
    obs = observation(interfaces=[])
    obs["data"]["capabilities"]["interfaces"] = "failed"
    result = export(obs)
    assert set(iface(result)) == {"name", "ietf-ip:ipv4"}
    assert {
        "interface_type_unmapped",
        "oper_status_not_observed_or_invalid",
        "mandatory_statistics_not_observed",
        "interface_state_not_observed",
    } <= codes(result)


def test_known_duplicate_interface_observations_cannot_be_recreated_from_ip():
    obs = observation(
        interfaces=[
            {"ifname": "Vlan20", "oper_status": "up"},
            {"ifname": "Vlan20", "oper_status": "down"},
        ]
    )
    result = export(obs)
    assert entries(result) == [] and "duplicate_interface" in codes(result)


@pytest.mark.parametrize("capability", ["ip", "arp", "vrrp"])
@pytest.mark.parametrize("status", ["failed", "unsupported", "unknown", None])
def test_unusable_capabilities_never_export_carried_records(capability, status):
    obs = observation(arp=[neighbor(type="dynamic")], vrrp=[virtual(version=3)])
    obs["data"]["capabilities"][capability] = status
    result = export(obs)
    target = iface(result).get("ietf-ip:ipv4", {})
    assert {"ip": "address", "arp": "neighbor", "vrrp": "ietf-vrrp:vrrp"}[capability] not in target
    assert "device_ip_capability_unusable" in codes(result)
    if capability == "ip":
        assert target == {}


def test_new_failed_ip_collection_removes_prior_ip_owned_subtrees_from_copy():
    prior = export(observation(arp=[neighbor()], vrrp=[virtual()]))
    obs = observation()
    obs["data"]["capabilities"]["ip"] = "failed"
    result = enrich_device_ip(prior, obs)
    assert "ietf-ip:ipv4" not in iface(result)
    assert "ietf-ip:ipv4" in iface(prior)


@pytest.mark.parametrize("origin", ["dynamic", "static"])
def test_arp_explicit_type_becomes_native_neighbor_origin(origin):
    result = export(observation(arp=[neighbor(type=origin)]))
    assert iface(result)["ietf-ip:ipv4"]["neighbor"] == [
        {"ip": "192.0.2.10", "link-layer-address": "02:00:00:00:00:10", "origin": origin}
    ]


@pytest.mark.parametrize("origin", [None, "D", "other", {}, []])
def test_ios_age_and_unknown_origin_do_not_become_dynamic(origin):
    result = export(observation(arp=[neighbor(type=origin, age_s=60)]))
    entry = iface(result)["ietf-ip:ipv4"]["neighbor"][0]
    assert "origin" not in entry
    assert "neighbor_origin_not_observed" in codes(result)


def test_physical_arp_attachment_resolves_to_unique_same_scope_l3_subnet():
    result = export(observation(arp=[neighbor(ifname="Ethernet0/1", type="dynamic")]))
    assert iface(result)["ietf-ip:ipv4"]["neighbor"][0]["origin"] == "dynamic"
    assert not any(row["name"] == "Ethernet0/1" for row in entries(result))


def test_explicit_real_l3_arp_interface_can_contain_an_out_of_subnet_neighbor():
    result = export(observation(arp=[neighbor(ip="203.0.113.10")]))
    assert iface(result)["ietf-ip:ipv4"]["neighbor"][0]["ip"] == "203.0.113.10"


@pytest.mark.parametrize("change", ["wrong_vrf", "overlap", "outside", "wrong_l3_name"])
def test_arp_scope_or_subnet_ambiguity_does_not_pick_an_interface(change):
    obs = observation(arp=[neighbor(ifname="Ethernet0/1")])
    if change == "wrong_vrf":
        obs["data"]["facts"]["arp"][0]["vrf"] = "RED"
    elif change == "overlap":
        obs["data"]["facts"]["ip"].append(
            {"ifname": "Vlan30", "address": "192.0.2.3", "prefix_len": 24, "vrf": "BLUE"}
        )
    elif change == "outside":
        obs["data"]["facts"]["arp"][0]["ip"] = "203.0.113.10"
    else:
        obs["data"]["facts"]["arp"][0]["ifname"] = "Vlan30"
        obs["data"]["facts"]["ip"].append(
            {"ifname": "Vlan30", "address": "198.51.100.3", "prefix_len": 24, "vrf": "RED"}
        )
    result = export(obs)
    assert all("neighbor" not in row.get("ietf-ip:ipv4", {}) for row in entries(result))
    assert "neighbor_interface_scope_unproven_or_ambiguous" in codes(result)


@pytest.mark.parametrize(
    "duplicate", [neighbor(), neighbor(mac="02:00:00:00:00:11"), neighbor(mac="bad")]
)
def test_duplicate_or_damaged_neighbor_key_never_selects_good_record(duplicate):
    result = export(observation(arp=[neighbor(), duplicate]))
    assert "neighbor" not in iface(result)["ietf-ip:ipv4"]
    assert "duplicate_or_invalid_neighbor_fact" in codes(result)


def test_vrrp_native_position_retains_observed_state_and_vips_but_missing_version():
    result = export(observation(vrrp=[virtual()]))
    vrrp = iface(result)["ietf-ip:ipv4"]["ietf-vrrp:vrrp"]["vrrp-instance"]
    assert vrrp == [
        {
            "vrid": 20,
            "state": "ietf-vrrp:master",
            "last-adv-source": "192.0.2.2",
            "virtual-ipv4-addresses": {"virtual-ipv4-address": [{"ipv4-address": "192.0.2.1"}]},
        }
    ]
    assert "mandatory_vrrp_version_not_observed" in codes(result)
    assert "priority" not in vrrp[0] and "version" not in vrrp[0]


@pytest.mark.parametrize("state", ["initialize", "backup", "master"])
@pytest.mark.parametrize("version", [2, 3, "2", "vrrp-v3", "ietf-vrrp:vrrp-v2"])
def test_only_observed_supported_vrrp_version_and_state_become_identities(state, version):
    result = export(observation(vrrp=[virtual(state=state, version=version)]))
    entry = iface(result)["ietf-ip:ipv4"]["ietf-vrrp:vrrp"]["vrrp-instance"][0]
    assert entry["state"] == "ietf-vrrp:" + state
    assert entry["version"] in {"ietf-vrrp:vrrp-v2", "ietf-vrrp:vrrp-v3"}
    assert "mandatory_vrrp_version_not_observed" not in codes(result)


def test_vrrp_ipv6_uses_ipv6_virtual_address_fields_and_observed_v3():
    obs = observation(
        ip=[{"ifname": "Vlan20", "address": "2001:db8::2", "prefix_len": 64, "vrf": "BLUE"}],
        vrrp=[
            virtual(
                virtual_addresses=["fe80::1", "2001:db8::1"], last_adv_source="fe80::2", version=3
            )
        ],
    )
    entry = iface(export(obs))["ietf-ip:ipv6"]["ietf-vrrp:vrrp"]["vrrp-instance"][0]
    assert entry["virtual-ipv6-addresses"] == {
        "virtual-ipv6-address": [{"ipv6-address": "fe80::1"}, {"ipv6-address": "2001:db8::1"}]
    }
    assert entry["version"] == "ietf-vrrp:vrrp-v3"


@pytest.mark.parametrize(
    "change",
    [
        "vrid_zero",
        "vrid_256",
        "vrid_bool",
        "duplicate_vips",
        "mixed_af",
        "wrong_scope",
        "missing_interface",
        "ipv6_v2",
    ],
)
def test_vrrp_invalid_key_af_or_scope_is_not_exported(change):
    row = virtual(version=3)
    obs = observation(vrrp=[row])
    if change.startswith("vrid"):
        row["vrid"] = {"vrid_zero": 0, "vrid_256": 256, "vrid_bool": True}[change]
    elif change == "duplicate_vips":
        row["virtual_addresses"] *= 2
    elif change == "mixed_af":
        row["virtual_addresses"].append("2001:db8::1")
    elif change == "wrong_scope":
        row["vrf"] = "RED"
    elif change == "missing_interface":
        row["ifname"] = "Vlan30"
    else:
        row.update(virtual_addresses=["2001:db8::1"], version=2)
        obs["data"]["facts"]["ip"] = [
            {"ifname": "Vlan20", "address": "2001:db8::2", "prefix_len": 64, "vrf": "BLUE"}
        ]
    result = export(obs)
    assert all(
        "ietf-vrrp:vrrp" not in row.get("ietf-ip:ipv4", {})
        and "ietf-vrrp:vrrp" not in row.get("ietf-ip:ipv6", {})
        for row in entries(result)
    )


def test_duplicate_vrrp_instance_does_not_choose_a_state():
    result = export(observation(vrrp=[virtual(), virtual(state="backup")]))
    assert "ietf-vrrp:vrrp" not in iface(result)["ietf-ip:ipv4"]
    assert "duplicate_or_invalid_vrrp_instance" in codes(result)


@pytest.mark.parametrize("name", ["br1", "br2", "core1", "core2", "dist1", "dist2", "acc1", "acc2"])
def test_real_campus_partial_device_export_has_only_observed_ip_neighbor_vrrp_fields(
    name, native_model
):
    family = "comware" if name.startswith("br") else "ios"
    files = {
        "interfaces": "interfaces" if family == "comware" else "interfaces_description",
        "ip": "ip_interfaces" if family == "comware" else "ip_interface",
        "arp": "arp",
        "vrrp": "vrrp",
    }
    facts, capabilities = {}, {}
    for cap, filename in files.items():
        path = CAPTURES / name / f"{filename}.txt"
        if not path.exists():
            capabilities[cap] = "unsupported"
            continue
        parsed = parse_generic({"os_family": family}, cap, path.read_text("utf-8"))
        facts[cap], capabilities[cap] = parsed.records, parsed.status
    obs = {"data": {"facts": facts, "capabilities": capabilities}}
    result = export(obs)
    assert result["metadata"]["completeness"] == "partial"
    assert sum(
        len(row.get("ietf-ip:ipv4", {}).get("address", [])) for row in entries(result)
    ) == len(facts["ip"])
    assert result["metadata"]["interface_scopes"] == {
        row["ifname"]: row["vrf"] for row in facts["ip"]
    }
    native_model.from_raw(result["tree"])
    with pytest.raises(Exception, match="statistics|discontinuity-time|version|type"):
        validate_native(native_model, result["tree"])
    for row in entries(result):
        for af in ("ietf-ip:ipv4", "ietf-ip:ipv6"):
            target = row.get(af, {})
            assert set(target) <= {"address", "neighbor", "ietf-vrrp:vrrp"}
            for entry in target.get("neighbor", []):
                assert set(entry) <= {"ip", "link-layer-address", "origin"}
                if family == "ios":
                    assert "origin" not in entry
            for instance in target.get("ietf-vrrp:vrrp", {}).get("vrrp-instance", []):
                assert "version" not in instance and "priority" not in instance
                assert "mandatory_vrrp_version_not_observed" in codes(result)


def test_invalid_observation_or_projection_returns_gaps_without_guessing_fields():
    assert "invalid_observation" in codes(enrich_device_ip(project_device_state(None), None))
    assert "invalid_device_projection" in codes(enrich_device_ip({}, observation()))


@pytest.fixture(scope="module")
def native_model():
    from yangson import DataModel

    yang = ROOT / "src/apps/topology/resources/yang"
    return DataModel((yang / "device-library.json").read_text("utf-8"), [str(yang)])


def validate_native(model, tree):
    from yangson.enumerations import ContentType

    model.from_raw(tree).validate(ctype=ContentType.all)


def explicit_synthetic_control(ipv6=False):
    """Test-only supplied counter time; never added by production projection."""
    obs = observation(arp=[neighbor(type="dynamic")], vrrp=[virtual(version=3)])
    if ipv6:
        obs = observation(
            ip=[{"ifname": "Vlan20", "address": "2001:db8::2", "prefix_len": 64, "vrf": "BLUE"}],
            vrrp=[
                virtual(
                    virtual_addresses=["fe80::1", "2001:db8::1"],
                    last_adv_source="fe80::2",
                    version=3,
                )
            ],
        )
    tree = export(obs)["tree"]
    tree["ietf-interfaces:interfaces"]["interface"][0]["statistics"] = {
        "discontinuity-time": "2000-01-01T00:00:00Z"
    }
    return tree


def test_production_partial_parses_but_full_native_validation_reports_missing_statistics(
    native_model,
):
    result = export(observation(arp=[neighbor(type="dynamic")]))
    native_model.from_raw(result["tree"])
    with pytest.raises(Exception, match="statistics|discontinuity-time"):
        validate_native(native_model, result["tree"])
    assert "statistics" not in iface(result)


def test_native_vrrp_version_mandatory_is_not_hidden_by_partial_label(native_model):
    result = export(observation(vrrp=[virtual()]))
    iface(result)["statistics"] = {"discontinuity-time": "2000-01-01T00:00:00Z"}
    native_model.from_raw(result["tree"])
    with pytest.raises(Exception, match="version"):
        validate_native(native_model, result["tree"])
    assert "mandatory_vrrp_version_not_observed" in codes(result)


@pytest.mark.parametrize("ipv6", [False, True])
def test_explicit_synthetic_complete_controls_validate_with_frozen_native_schema(
    native_model, ipv6
):
    validate_native(native_model, explicit_synthetic_control(ipv6))


@pytest.mark.parametrize(
    "change", ["wrong_ip_af", "neighbor_enum", "version_identity", "state_identity", "custom_vrf"]
)
def test_native_schema_rejects_bad_af_enum_identity_and_custom_field(native_model, change):
    tree = explicit_synthetic_control()
    validate_native(native_model, tree)
    row = tree["ietf-interfaces:interfaces"]["interface"][0]
    ipv4 = row["ietf-ip:ipv4"]
    instance = ipv4["ietf-vrrp:vrrp"]["vrrp-instance"][0]
    if change == "wrong_ip_af":
        ipv4["address"][0]["ip"] = "2001:db8::2"
    elif change == "neighbor_enum":
        ipv4["neighbor"][0]["origin"] = "permanent"
    elif change == "version_identity":
        instance["version"] = "ietf-vrrp:master"
    elif change == "state_identity":
        instance["state"] = "ietf-vrrp:vrrp-v3"
    else:
        row["vrf"] = "BLUE"
    with pytest.raises(Exception):
        validate_native(native_model, tree)


@pytest.mark.parametrize("change", ["v2", "third_virtual"])
def test_native_ipv6_vrrp_constraints_are_kept_intact(native_model, change):
    tree = explicit_synthetic_control(ipv6=True)
    validate_native(native_model, tree)
    instance = tree["ietf-interfaces:interfaces"]["interface"][0]["ietf-ip:ipv6"]["ietf-vrrp:vrrp"][
        "vrrp-instance"
    ][0]
    if change == "v2":
        instance["version"] = "ietf-vrrp:vrrp-v2"
    else:
        instance["virtual-ipv6-addresses"]["virtual-ipv6-address"].append(
            {"ipv6-address": "2001:db8::3"}
        )
    with pytest.raises(Exception):
        validate_native(native_model, tree)


@pytest.mark.parametrize("change", ["global_first", "third_virtual"])
def test_ipv6_vrrp_export_respects_description_order_and_native_cardinality(change):
    values = ["fe80::1", "2001:db8::1"]
    if change == "global_first":
        values.reverse()
    else:
        values.append("2001:db8::3")
    obs = observation(
        ip=[{"ifname": "Vlan20", "address": "2001:db8::2", "prefix_len": 64, "vrf": "BLUE"}],
        vrrp=[virtual(virtual_addresses=values, version=3)],
    )
    result = export(obs)
    assert "ietf-vrrp:vrrp" not in iface(result)["ietf-ip:ipv6"]
    assert "invalid_vrrp_fact" in codes(result)


@pytest.mark.parametrize("capability", ["ip", "arp", "vrrp"])
def test_known_transport_incompleteness_in_record_wrapper_blocks_owned_data(capability):
    obs = observation(arp=[neighbor()], vrrp=[virtual()])
    obs["data"]["facts"][capability] = {
        "records": obs["data"]["facts"][capability],
        "status": "partial",
        "parse_metadata": {"warnings": ["collection_incomplete"]},
    }
    result = export(obs)
    assert {"ip": "address", "arp": "neighbor", "vrrp": "ietf-vrrp:vrrp"}[capability] not in iface(
        result
    ).get("ietf-ip:ipv4", {})
    assert "device_ip_collection_unusable" in codes(result)


def test_explicit_null_capability_metadata_is_not_treated_as_permission_to_use_facts():
    obs = observation(arp=[neighbor()], vrrp=[virtual()])
    obs["data"]["capabilities"] = None
    assert "ietf-ip:ipv4" not in iface(export(obs))


@pytest.mark.parametrize("version", [True, 4, "unknown", [], {}])
def test_invalid_observed_version_refuses_vrrp_instance_instead_of_becoming_default(version):
    result = export(observation(vrrp=[virtual(version=version)]))
    assert "ietf-vrrp:vrrp" not in iface(result)["ietf-ip:ipv4"]
    assert "invalid_vrrp_fact" in codes(result)
