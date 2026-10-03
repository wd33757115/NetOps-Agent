"""Snapshot interface observations use RFC 8343 without claiming completeness."""

import copy
import json
from pathlib import Path

import pytest

from src.apps.topology.backend.collectors.parse import parse_generic
from src.apps.topology.backend.model.device_state import (
    project_device_state,
    project_interfaces,
    read_interface_state,
)

ROOT = Path(__file__).resolve().parents[2]
CAPTURES = ROOT / "tests/fixtures/topology/campus-wan"
YANG = ROOT / "src/apps/topology/resources/yang"


def rows(projected):
    return projected["tree"]["ietf-interfaces:interfaces"]["interface"]


def gap_codes(projected, name=None):
    return {
        gap["code"] for gap in projected["gaps"] if name is None or gap.get("interface") == name
    }


def observed_interfaces(device):
    family = "comware" if device.startswith("br") else "ios"
    filename = "interfaces.txt" if family == "comware" else "interfaces_description.txt"
    parsed = parse_generic(
        {"os_family": family}, "interfaces", (CAPTURES / device / filename).read_text()
    )
    assert parsed.status == "complete"
    return parsed.records


@pytest.mark.parametrize(
    ("device", "count", "down_count"),
    [
        ("br1", 9, 0),
        ("br2", 9, 0),
        ("core1", 9, 2),
        ("core2", 9, 2),
        ("dist1", 20, 9),
        ("dist2", 20, 9),
        ("acc1", 16, 11),
        ("acc2", 16, 11),
    ],
)
def test_eight_real_campus_devices_keep_observed_interfaces_and_down_ports(
    device, count, down_count
):
    projected = project_interfaces(observed_interfaces(device))
    assert len(rows(projected)) == count
    assert sum(row["oper-status"] == "down" for row in rows(projected)) == down_count
    assert {row["oper-status"] for row in rows(projected)} <= {"up", "down"}
    assert all(set(row) <= {"name", "type", "oper-status"} for row in rows(projected))
    assert projected["metadata"]["completeness"] == "partial"
    assert projected["metadata"]["full_validation"] == "not_performed"
    assert (
        sum(gap["code"] == "mandatory_statistics_not_observed" for gap in projected["gaps"])
        == count
    )


@pytest.mark.parametrize("device", ["dist1", "dist2"])
def test_real_aggregate_vlan_and_loopback_types_come_from_facts(device):
    projected = project_interfaces(observed_interfaces(device))
    assert read_interface_state(projected, "Port-channel1") == {
        "name": "Port-channel1",
        "type": "iana-if-type:ieee8023adLag",
        "oper-status": "up",
    }
    assert read_interface_state(projected, "Vlan10")["type"] == "iana-if-type:l3ipvlan"
    assert read_interface_state(projected, "Loopback0")["type"] == "iana-if-type:softwareLoopback"
    assert read_interface_state(projected, "Ethernet0/3")["oper-status"] == "down"


@pytest.mark.parametrize("device", ["br1", "br2"])
def test_unmapped_real_internal_interface_types_do_not_become_other_or_ethernet(device):
    projected = project_interfaces(observed_interfaces(device))
    for name in ("NULL0", "REG0"):
        assert read_interface_state(projected, name) == {"name": name, "oper-status": "up"}
        assert "interface_type_unmapped" in gap_codes(projected, name)
    assert read_interface_state(projected, "GigabitEthernet1/0")["type"] == (
        "iana-if-type:ethernetCsmacd"
    )


@pytest.mark.parametrize(
    "status", ["up", "down", "testing", "unknown", "dormant", "not-present", "lower-layer-down"]
)
def test_all_seven_native_oper_status_values_are_preserved(status):
    projected = project_interfaces(
        [{"ifname": "Ethernet0/1", "type": "ethernet", "oper_status": status}]
    )
    assert read_interface_state(projected, "Ethernet0/1")["oper-status"] == status
    assert gap_codes(projected) == {"mandatory_statistics_not_observed"}


@pytest.mark.parametrize(
    "status", [None, "", "UP", "administratively down", "unreachable", True, 1, []]
)
def test_invalid_or_missing_status_is_a_gap_not_an_inferred_state(status):
    projected = project_interfaces(
        [{"ifname": "Ethernet0/1", "type": "ethernet", "oper_status": status}]
    )
    assert "oper-status" not in read_interface_state(projected, "Ethernet0/1")
    assert "oper_status_not_observed_or_invalid" in gap_codes(projected, "Ethernet0/1")


@pytest.mark.parametrize("kind", [None, "unknown", "mgmt", "iana-if-type:invented", [], True])
def test_an_ethernet_shaped_name_does_not_supply_missing_type_evidence(kind):
    projected = project_interfaces([{"ifname": "Ethernet0/1", "type": kind, "oper_status": "down"}])
    assert read_interface_state(projected, "Ethernet0/1") == {
        "name": "Ethernet0/1",
        "oper-status": "down",
    }
    assert "interface_type_unmapped" in gap_codes(projected, "Ethernet0/1")


def test_duplicate_interface_records_are_not_selected_arbitrarily():
    projected = project_interfaces(
        [
            {"ifname": "Ethernet0/1", "type": "ethernet", "oper_status": "up"},
            {"ifname": "Ethernet0/1", "type": "ethernet", "oper_status": "down"},
            {"ifname": "Ethernet0/2", "type": "ethernet", "oper_status": "down"},
        ]
    )
    assert read_interface_state(projected, "Ethernet0/1") is None
    assert read_interface_state(projected, "Ethernet0/2")["oper-status"] == "down"
    assert gap_codes(projected, "Ethernet0/1") == {"duplicate_interface"}


@pytest.mark.parametrize(
    "record", [None, [], {}, {"ifname": ""}, {"ifname": " "}, {"ifname": "Et0\n1"}]
)
def test_bad_records_cannot_create_standard_interface_entries(record):
    projected = project_interfaces([record])
    assert rows(projected) == []
    assert gap_codes(projected) == {"invalid_interface_record"}


def test_snapshot_metadata_stays_outside_the_standard_tree_and_lldp_is_not_required():
    observation = {
        "id": "observation-1",
        "observed_at": "2026-10-03T01:40:10.302006+00:00",
        "data": {
            "complete": False,
            "facts": {"interfaces": observed_interfaces("core1")},
            "capabilities": {"lldp": "failed", "interfaces": "complete"},
        },
    }
    before = copy.deepcopy(observation)
    projected = project_device_state(observation)
    assert read_interface_state(projected, "Ethernet1/2")["oper-status"] == "down"
    assert projected["metadata"]["observation_id"] == observation["id"]
    assert projected["metadata"]["observed_at"] == observation["observed_at"]
    assert set(projected["tree"]) == {"ietf-interfaces:interfaces"}
    assert observation == before
    read = read_interface_state(projected, "Ethernet1/2")
    read["oper-status"] = "up"
    assert read_interface_state(projected, "Ethernet1/2")["oper-status"] == "down"


def test_valid_rows_from_partial_interface_collection_remain_readable_with_a_gap():
    observation = {
        "facts": {
            "interfaces": [{"ifname": "Ethernet0/1", "type": "ethernet", "oper_status": "down"}]
        },
        "capabilities": {"interfaces": "partial"},
    }
    projected = project_device_state(observation)
    assert read_interface_state(projected, "Ethernet0/1")["oper-status"] == "down"
    assert "interfaces_collection_partial" in gap_codes(projected)


def test_flat_transport_metadata_refuses_current_interface_state_and_preserves_evidence():
    records = observed_interfaces("dist1")
    metadata = {"tier": "generic", "warnings": ["collection_incomplete"], "sources": []}
    observation = {
        "id": "truncated-interfaces",
        "data": {
            "facts": {"interfaces": records},
            "capabilities": {"interfaces": "partial"},
            "fact_metadata": {"interfaces": metadata},
        },
    }
    before = copy.deepcopy(observation)
    result = project_device_state(observation)
    assert rows(result) == []
    assert "interfaces_transport_incomplete" in gap_codes(result)
    assert result["metadata"]["interface_records"] == records
    assert result["metadata"]["fact_metadata"] == {"interfaces": metadata}
    assert result["metadata"]["observation_id"] == "truncated-interfaces"
    assert observation == before


@pytest.mark.parametrize(
    "warnings",
    [[], ["line:1:bad_row"], ["advertised_count_mismatch"], ["line:1:collection_incomplete"]],
)
def test_local_partial_interface_parse_remains_current_with_explicit_gaps(warnings):
    records = observed_interfaces("dist1")
    result = project_device_state(
        {
            "data": {
                "complete": False,
                "facts": {"interfaces": records},
                "capabilities": {"interfaces": "partial", "lldp": "failed"},
                "fact_metadata": {"interfaces": {"warnings": warnings}},
            }
        }
    )
    assert read_interface_state(result, "Ethernet0/1")["oper-status"] == "up"
    assert "interfaces_collection_partial" in gap_codes(result)
    assert "interfaces_transport_incomplete" not in gap_codes(result)


def test_real_truncated_interfaces_cannot_survive_typed_list_and_metadata_storage():
    text = (CAPTURES / "dist1/interfaces_description.txt").read_text("utf-8")
    parsed = parse_generic({"os_family": "ios"}, "interfaces", text, complete=False)
    assert parsed.status == "partial" and parsed.records
    result = project_device_state(
        {
            "data": {
                "facts": {"interfaces": parsed.records},
                "capabilities": {"interfaces": parsed.status},
                "fact_metadata": {"interfaces": parsed.fact_summary()["parse_metadata"]},
            }
        }
    )
    assert rows(result) == []
    assert "interfaces_transport_incomplete" in gap_codes(result)


@pytest.mark.parametrize("status", ["complete", "partial", "failed"])
def test_wrapped_interface_facts_keep_transport_veto_and_do_not_ignore_failed_status(status):
    records = observed_interfaces("dist1")
    result = project_device_state(
        {
            "interfaces": {
                "records": records,
                "status": status,
                "parse_metadata": {"warnings": ["collection_incomplete"]},
            }
        }
    )
    assert rows(result) == []
    assert "interfaces_transport_incomplete" in gap_codes(result)
    assert result["metadata"]["interface_records"] == records
    if status == "failed":
        assert "interfaces_capability_unusable" in gap_codes(result)


@pytest.mark.parametrize("status", ["failed", "unsupported", "invented", None, False, []])
def test_failed_or_invalid_interface_capability_cannot_reuse_carried_up_records(status):
    projected = project_device_state(
        {
            "facts": {
                "interfaces": [{"ifname": "Ethernet0/1", "type": "ethernet", "oper_status": "up"}]
            },
            "capabilities": {"interfaces": status},
        }
    )
    assert rows(projected) == []
    assert "interfaces_capability_unusable" in gap_codes(projected)


def test_declared_capability_map_without_interface_success_does_not_reuse_carried_records():
    projected = project_device_state(
        {
            "facts": {
                "interfaces": [{"ifname": "Ethernet0/1", "type": "ethernet", "oper_status": "up"}]
            },
            "capabilities": {"lldp": "complete"},
        }
    )
    assert rows(projected) == []
    assert "interfaces_capability_unusable" in gap_codes(projected)


@pytest.mark.parametrize(
    "value", [None, [], {}, {"data": None}, {"facts": None}, {"capabilities": []}]
)
def test_missing_or_malformed_observation_does_not_assume_a_device_state(value):
    projected = project_device_state(value)
    assert rows(projected) == []
    assert projected["gaps"]
    assert read_interface_state(projected, "Ethernet0/1") is None


def native_model():
    yangson = pytest.importorskip("yangson", reason="Install tests/topology-requirements.txt")
    return yangson.DataModel((YANG / "interfaces-library.json").read_text(), [str(YANG)])


def all_content():
    return pytest.importorskip("yangson.enumerations").ContentType.all


def test_independent_interface_library_keeps_native_mandatory_validation():
    library = json.loads((YANG / "interfaces-library.json").read_text())
    modules = library["ietf-yang-library:modules-state"]["module"]
    assert {m["name"] for m in modules if m["conformance-type"] == "implement"} == {
        "ietf-interfaces",
        "iana-if-type",
    }
    assert not any(m.get("feature") for m in modules)
    projected = project_interfaces(
        [{"ifname": "Ethernet0/1", "type": "ethernet", "oper_status": "down"}]
    )
    assert set(rows(projected)[0]) == {"name", "type", "oper-status"}
    # Partial parsing proves type/hierarchy only, never full completeness.
    instance = native_model().from_raw(projected["tree"])
    with pytest.raises(Exception, match="statistics|discontinuity-time"):
        instance.validate(ctype=all_content())


@pytest.mark.parametrize(
    "status", ["up", "down", "testing", "unknown", "dormant", "not-present", "lower-layer-down"]
)
def test_native_schema_accepts_projected_leaves_with_explicit_synthetic_completion(status):
    projected = project_interfaces(
        [{"ifname": "Ethernet0/1", "type": "ethernet", "oper_status": status}]
    )
    completed = copy.deepcopy(projected["tree"])
    # Synthetic validation control only; the production projector never adds this timestamp.
    completed["ietf-interfaces:interfaces"]["interface"][0]["statistics"] = {
        "discontinuity-time": "2026-01-01T00:00:00Z"
    }
    native_model().from_raw(completed).validate(ctype=all_content())
    assert "statistics" not in rows(projected)[0]
