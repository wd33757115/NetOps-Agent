from dataclasses import asdict, replace
from io import StringIO

import pytest
import textfsm

from src.core.check_items.adapter_projection import project_adapter_batches_to_observations
from src.core.check_items.canonical import validate_check_item_contract
from src.core.check_items.catalog import load_catalog
from src.core.check_items.command_adapter import execute_adapter
from src.core.check_items.hardware_values import TARGETS, template_path
from src.core.check_items.value_contract import same_name_fields

CPU = """Slot 1 CPU 0 CPU usage:
       9% in last 5 seconds
       4% in last 1 minute
       3% in last 5 minutes
"""
MEMORY = """Memory statistics are measured in KB:
Slot 1:
             Total      Used      Free    Shared   Buffers    Cached   FreeRatio
Mem:       3879320   1533404   2345916         0      3676    268064       61.1%
-/+ Buffers/Cache:   1261664   2617656
Swap:            0         0         0
"""
FAN = """Slot 1:
Fan 1:
 State: Normal
 Airflow Direction: Port-to-power
 Prefer Airflow Direction: Port-to-power
Fan 2:
 State: Faulty
Slot 2:
Fan 1:
 State: Absent
"""
POWER = """Slot 1:
 Input Power: 69(W)
 PowerID State Mode Current(A) Voltage(V) Power(W) FanDirection
 1 Normal AC 2.65 11.84 31 PowerToPort
 2 Faulty -- -- -- --
Slot 2:
 1 Normal AC 3.24 11.87 38 PowerToPort
"""
SAMPLES = dict(zip(TARGETS, (CPU, MEMORY, FAN, POWER)))


def parse(item_id, raw):
    parser = textfsm.TextFSM(StringIO(template_path(item_id).read_text(encoding="utf8")))
    return parser.header, [dict(zip(parser.header, row)) for row in parser.ParseText(raw)]


def run(item_id, raw):
    item = next(i for i in load_catalog().check_items if i.id == item_id)
    headers, records = parse(item_id, raw)
    spec = {
        "schema_version": "command-adapter/v2",
        "parser": {"template_id": "test"},
        "outputs": [
            {
                "check_item_id": item.id,
                "check_item_version_id": item.version_id,
                "source_record_set": "default",
                "empty_status": "unavailable",
                "projection": {"fields": same_name_fields(item.canonical_contract, headers)},
            }
        ],
    }
    batches = execute_adapter(
        spec,
        record_sets={"default": records},
        check_items={item.id: asdict(item)},
        device_id="test",
        adapter_version_id="test",
    )
    obs = project_adapter_batches_to_observations(
        batches,
        check_items={item.id: item},
        snapshot={"id": "snap", "device_id": "test"},
        mode="offline",
        source="test",
        dataset_id="test",
        command_adapter_id="test",
        command_adapter_version_id="test",
    )
    return item, batches[0], obs


@pytest.mark.parametrize("item_id", TARGETS)
def test_values_exactly_match_the_checkitem_without_transformations(item_id):
    item, batch, _ = run(item_id, SAMPLES[item_id])
    assert not validate_check_item_contract(item)
    assert batch.quality_status == "complete", batch.errors
    headers, _ = parse(item_id, SAMPLES[item_id])
    declared = set(item.canonical_contract["identity_dimensions"]) | {
        r["canonical_field"] for r in item.canonical_contract["observations"]
    }
    assert set(headers) == declared
    assert same_name_fields(item.canonical_contract, headers) == {h: {"source": h} for h in headers}
    assert not any("projection_recipes" in r for r in item.canonical_contract["observations"])


def test_cpu_multiple_processors_and_fixed_windows_are_not_cli_columns():
    _, batch, obs = run("system.cpu_usage", CPU + CPU.replace("CPU 0", "CPU 1").replace("3%", "7%"))
    assert len(batch.records) == 2
    assert [r["cpu_usage_percent"] for r in batch.records] == [3, 7]
    assert all("window" not in r for r in batch.records)
    metrics = {o.field_name: o for o in obs}
    assert metrics["cpu_usage_percent"].dimensions == {"window": "5m"}
    assert metrics["cpu_5s_percent"].dimensions == {"window": "5s"}
    assert metrics["cpu_1m_percent"].dimensions == {"window": "1m"}
    # Incomplete processor data cannot accidentally inherit the previous 5m.
    _, bad, observations = run(
        "system.cpu_usage", CPU + "Slot 1 CPU 1 CPU usage:\n 2% in last 5 seconds\n"
    )
    assert bad.quality_status == "partial"
    assert not observations


def test_memory_preserves_kb_and_device_free_ratio_without_inventing_five_minute_usage():
    _, batch, obs = run("system.memory_usage", MEMORY)
    row = batch.records[0]
    assert row["memory_total_kb"] == 3879320
    assert row["memory_used_kb"] == 1533404
    assert row["memory_free_percent"] == 61.1
    assert row["memory_usage_percent"] in (None, "")
    assert not any(o.observation_type == "device.memory.utilization" for o in obs)
    assert not parse("system.memory_usage", MEMORY.replace("in KB:", "in MB:"))[1]


def test_fan_fault_without_airflow_and_multiple_slots_is_not_lost():
    _, batch, _ = run("environment.fan", FAN)
    assert [(r["slot"], r["fan"], r["fan_status"]) for r in batch.records] == [
        ("1", "1", "Normal"),
        ("1", "2", "Faulty"),
        ("2", "1", "Absent"),
    ]
    assert not batch.records[1]["fan_airflow_direction"]


def test_power_missing_metrics_are_empty_not_zero_and_slot_power_does_not_leak():
    _, batch, _ = run("environment.power", POWER)
    assert len(batch.records) == 3
    assert batch.records[0]["current_amperes"] == 2.65
    assert batch.records[1]["power_supply_status"] == "Faulty"
    assert not batch.records[1]["current_amperes"]
    assert batch.records[1]["slot_input_watts"] == 69
    assert not batch.records[2]["slot_input_watts"]


def test_fixed_dimension_rejects_unapproved_window():
    item, _, _ = run("system.cpu_usage", CPU)
    from copy import deepcopy

    contract = deepcopy(item.canonical_contract)
    contract["observations"][0]["fixed_dimensions"] = {"window": "invented"}
    assert validate_check_item_contract(replace(item, canonical_contract=contract))


@pytest.mark.parametrize(
    "item_id,field",
    [
        ("environment.fan", "fan_status"),
        ("environment.power", "power_supply_status"),
    ],
)
def test_original_h3c_state_is_assessed_without_rewriting_values(item_id, field):
    from src.core.check_items.canonical import rule_semantics_catalog
    from src.core.patrol.event_rules import current_state_condition_matches

    item, _, _ = run(item_id, SAMPLES[item_id])
    target = next(
        r for r in item.canonical_contract["observations"] if r["canonical_field"] == field
    )
    semantics = rule_semantics_catalog(item.canonical_contract).get(target["observation_type"])
    condition = {
        "kind": "entity_status_down",
        "fields": [field],
        "values": list(semantics.state_groups["unhealthy"]),
    }
    assert current_state_condition_matches(condition, {"field": field, "value": "Faulty"})
    assert not current_state_condition_matches(condition, {"field": field, "value": "Normal"})
