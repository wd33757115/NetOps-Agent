from __future__ import annotations

import io
from pathlib import Path

import pytest

from src.core.check_items.adapter_projection import (
    project_adapter_batches_to_observations,
)
from src.core.check_items.catalog import CheckItemDefinition, load_catalog
from src.core.check_items.command_adapter import execute_adapter
from src.core.parser.textfsm_input import prepare_textfsm_input
from src.core.parser_generation import (
    FieldSpec,
    ParserSample,
    ParserSpec,
    SampleExpectation,
    SampleRole,
    TextFSMValidationBackend,
    validate_template_candidate,
)

textfsm = pytest.importorskip("textfsm")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_ROOT = (
    PROJECT_ROOT / "src/skills/shared/textfsm-templates/H3C/Comware7"
)


def _parse(template_name: str, raw: str) -> list[dict[str, str]]:
    template_text = (TEMPLATE_ROOT / template_name).read_text(encoding="utf-8")
    parser = textfsm.TextFSM(io.StringIO(template_text))
    return [
        dict(zip(parser.header, row, strict=False))
        for row in parser.ParseText(prepare_textfsm_input(template_text, raw))
    ]


def _payload(item: CheckItemDefinition) -> dict[str, object]:
    return {
        "version_id": item.version_id,
        "canonical_contract": item.canonical_contract,
    }


def _optical_spec(item: CheckItemDefinition) -> dict[str, object]:
    dimensions = {"channel": {"field": "channel"}}
    return {
        "schema_version": "command-adapter/v2",
        "parser": {"template_id": "h3c-optics-v1.1"},
        "outputs": [
            {
                "check_item_id": item.id,
                "check_item_version_id": item.version_id,
                "source_record_set": "optics",
                "projection": {
                    "fields": {
                        "interface": {"source": "interface"},
                        "channel": {
                            "source": "channel_token",
                            "value_mapping": {
                                "": "aggregate",
                                "Current": "aggregate",
                                "1": "1",
                                "2": "2",
                                "3": "3",
                                "4": "4",
                            },
                            "on_unmapped": "reject",
                        },
                        "transceiver_present": {
                            "source": "transceiver_present",
                            "value_mapping": {"": "present", "absent": "absent"},
                            "on_unmapped": "reject",
                            "dimensions": dimensions,
                        },
                        "rx_power_dbm": {
                            "source": "rx_power_dbm",
                            "canonical_unit": "dbm",
                            "dimensions": dimensions,
                        },
                        "tx_power_dbm": {
                            "source": "tx_power_dbm",
                            "canonical_unit": "dbm",
                            "dimensions": dimensions,
                        },
                    }
                },
            }
        ],
    }


def test_h3c_interface_brief_template_discards_legend_rows_and_projects_status():
    item = load_catalog().check_item("interface.oper_status")
    assert item is not None
    rows = _parse(
        "display_interface_brief__1.0.0.textfsm",
        """
Brief information on interfaces in route mode:
Link: ADM - administratively down; Stby - standby
Protocol: (s) - spoofing
Interface            Link Protocol Primary IP      Description
InLoop0              UP   UP(s)    --
MGE0/0/0             DOWN DOWN     --

Brief information on interfaces in bridge mode:
Interface            Link Speed   Duplex Type PVID Description
XGE1/0/1             ADM  auto    A      A    1
FGE1/0/25            down  40G(a)  F(a)   --   --
""",
    )

    assert [row["interface"] for row in rows] == [
        "InLoop0",
        "MGE0/0/0",
        "XGE1/0/1",
        "FGE1/0/25",
    ]
    spec = {
        "schema_version": "command-adapter/v2",
        "parser": {"template_id": "h3c-interface-v1"},
        "outputs": [
            {
                "check_item_id": item.id,
                "check_item_version_id": item.version_id,
                "source_record_set": "interfaces",
                "projection": {
                    "fields": {
                        "interface": {"source": "interface"},
                        "oper_status": {
                            "source": "link",
                            "value_mapping": {
                                "UP": "up",
                                "DOWN": "down",
                                "ADM": "admin_down",
                                "Stby": "unknown",
                            },
                            "on_unmapped": "reject",
                        },
                    }
                },
            }
        ],
    }
    batch = execute_adapter(
        spec,
        record_sets={"interfaces": rows},
        check_items={item.id: _payload(item)},
        device_id="h3c-test",
        adapter_version_id="h3c-interface-v1",
        require_pinned_check_item_versions=True,
    )[0]

    assert batch.quality_status == "complete"
    assert [row["oper_status"] for row in batch.records] == [
        "up",
        "down",
        "admin_down",
        "down",
    ]


def test_h3c_optical_projection_keeps_absence_valid_and_present_metrics_required():
    item = load_catalog().check_item("interface.optical_power")
    assert item is not None
    assert item.version == 11
    rows = _parse(
        "display_transceiver_diagnosis_interface__1.1.0.textfsm",
        """
FortyGigE1/0/25 transceiver diagnostic information:
  Current diagnostic parameters:
[module]  Temp.(C)   Voltage(V)
          24         3.32
[channel] Bias(mA)  RX power(dBm)  TX power(dBm)
    1     5.83      -0.67          -2.04
Ten-GigabitEthernet1/0/2 transceiver diagnostic information:
  Current diagnostic parameters:
    Temp.(C)   Voltage(V)  Bias(mA)  RX power(dBm)  TX power(dBm)
    21         3.31        6.14      -3.10          -2.68
Ten-GigabitEthernet1/0/5 transceiver diagnostic information:
The transceiver is absent.
""",
    )
    assert [(row["interface"], row["channel_token"]) for row in rows] == [
        ("FortyGigE1/0/25", "1"),
        ("Ten-GigabitEthernet1/0/2", "Current"),
        ("Ten-GigabitEthernet1/0/5", ""),
    ]

    spec = _optical_spec(item)
    batch = execute_adapter(
        spec,
        record_sets={"optics": rows},
        check_items={item.id: _payload(item)},
        device_id="h3c-test",
        adapter_version_id="h3c-optics-v1.1",
        require_pinned_check_item_versions=True,
    )[0]

    assert batch.quality_status == "complete"
    absent = batch.records[-1]
    assert absent == {
        "interface": "Ten-GigabitEthernet1/0/5",
        "channel": "aggregate",
        "transceiver_present": "absent",
        "rx_power_dbm": "",
        "tx_power_dbm": "",
    }
    observations = project_adapter_batches_to_observations(
        (batch,),
        check_items={item.id: item},
        snapshot={
            "id": "h3c-optical-snapshot",
            "run_id": "h3c-optical-run",
            "device_id": "h3c-test",
            "canonical_command": "display transceiver diagnosis interface",
            "observed_at": batch.observed_at,
            "metadata_json": {},
        },
        mode="parser_validation",
        source="test",
        dataset_id=None,
        command_adapter_id="h3c-optics",
        command_adapter_version_id="h3c-optics-v1.1",
    )
    absent_observations = [
        item
        for item in observations
        if item.entity_key.endswith('"aggregate"]') and item.value == "absent"
    ]
    assert len(observations) == 7
    assert len(absent_observations) == 1
    assert absent_observations[0].field_name == "transceiver_present"

    incomplete_present = [
        {
            "interface": "Ten-GigabitEthernet1/0/6",
            "channel_token": "Current",
            "transceiver_present": "",
            "rx_power_dbm": "",
            "tx_power_dbm": "",
        }
    ]
    failed = execute_adapter(
        spec,
        record_sets={"optics": incomplete_present},
        check_items={item.id: _payload(item)},
        device_id="h3c-test",
        adapter_version_id="h3c-optics-v1.1",
        require_pinned_check_item_versions=True,
    )[0]
    assert failed.quality_status == "unavailable"
    assert "required canonical field is empty: rx_power_dbm" in failed.errors[0]


def test_h3c_optical_template_uses_explicit_aggregate_key_for_sparse_channel_token():
    raw = """
FortyGigE1/0/25 transceiver diagnostic information:
  Current diagnostic parameters:
[module]  Temp.(C)   Voltage(V)
          24         3.32
[channel] Bias(mA)  RX power(dBm)  TX power(dBm)
    1     5.83      -0.67          -2.04
Ten-GigabitEthernet1/0/2 transceiver diagnostic information:
  Current diagnostic parameters:
    Temp.(C)   Voltage(V)  Bias(mA)  RX power(dBm)  TX power(dBm)
    21         3.31        6.14      -3.10          -2.68
Ten-GigabitEthernet1/0/5 transceiver diagnostic information:
The transceiver is absent.
"""
    spec = ParserSpec(
        entity="interface_optical",
        primary_keys=("interface", "channel_token"),
        fields=(
            FieldSpec("interface", True, "interface_name"),
            FieldSpec(
                "channel_token",
                False,
                "text",
                primary_key_default="aggregate",
            ),
            FieldSpec("transceiver_present", False, "enum", values=("absent",)),
            FieldSpec("rx_power_dbm", False, "number"),
            FieldSpec("tx_power_dbm", False, "number"),
        ),
    )
    template_text = (
        TEMPLATE_ROOT / "display_transceiver_diagnosis_interface__1.1.0.textfsm"
    ).read_text(encoding="utf-8")

    report = validate_template_candidate(
        template_text,
        spec,
        (
            ParserSample(
                sample_id="h3c-optical-mixed",
                raw_text=raw,
                role=SampleRole.GENERATION,
                expectation=SampleExpectation(record_count=3),
            ),
        ),
        TextFSMValidationBackend(),
    )

    assert report.passed is True
    assert report.samples[0].actual_primary_keys == (
        ("FortyGigE1/0/25", "1"),
        ("Ten-GigabitEthernet1/0/2", "Current"),
        ("Ten-GigabitEthernet1/0/5", "aggregate"),
    )


def test_h3c_optical_threshold_template_extracts_device_alarm_limits():
    rows = _parse(
        "display_transceiver_diagnosis_interface_thresholds__1.0.0.textfsm",
        """
Ten-GigabitEthernet1/0/2 transceiver diagnostic information:
  Current diagnostic parameters:
    Temp.(C)   Voltage(V)  Bias(mA)  RX power(dBm)  TX power(dBm)
    21         3.31        6.14      -3.10          -2.68
  Alarm thresholds:
          Temp.(C)   Voltage(V)  Bias(mA)  RX power(dBm)  TX power(dBm)
    High  73         3.60        12.00     1.00           0.00
    Low   -3         3.00        1.00      -11.90         -10.30
""",
    )

    assert rows == [{
        "interface": "Ten-GigabitEthernet1/0/2",
        "rx_high_alarm_dbm": "1.00",
        "tx_high_alarm_dbm": "0.00",
        "rx_low_alarm_dbm": "-11.90",
        "tx_low_alarm_dbm": "-10.30",
    }]
