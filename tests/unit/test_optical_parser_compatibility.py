from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.core.parser.policy import CategorySpec
from src.core.parser.textfsm_input import TEXTFSM_END_OF_INPUT, prepare_textfsm_input

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GENERATOR_SCRIPTS = PROJECT_ROOT / "src" / "skills" / "textfsm-generator" / "scripts"
if str(GENERATOR_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(GENERATOR_SCRIPTS))

from template_synthesizer import synthesize_template  # noqa: E402
from template_validator import validate_template  # noqa: E402

OPTICAL_SPEC = CategorySpec(
    name="interface_optical",
    entity_type="interface_optical",
    primary_keys=("interface",),
    required_fields=("interface",),
    optional_fields=(
        "transceiver_present",
        "dom_supported",
        "rx_power_dbm",
        "tx_power_dbm",
        "rx_low_warning_dbm",
        "rx_high_warning_dbm",
        "tx_low_warning_dbm",
        "tx_high_warning_dbm",
        "rx_low_alarm_dbm",
        "rx_high_alarm_dbm",
        "tx_low_alarm_dbm",
        "tx_high_alarm_dbm",
    ),
    validators={"interface": {"type": "interface_name"}},
)


H3C_MIXED_THRESHOLD_SAMPLE = """\
Ten-GigabitEthernet1/0/49 transceiver diagnostic information:
  Current diagnostic parameters:
    Temp.(C)   Voltage(V)  Bias(mA)  RX power(dBm)  TX power(dBm)
    30         3.32        2.82      -5.34          -6.32
  Alarm thresholds:
          Temp.(C)   Voltage(V)  Bias(mA)  RX power(dBm)  TX power(dBm)
    High  73         3.80        33.00     0.00           6.00
    Low   -3         2.80        1.00      -18.01         -12.50
  Warning thresholds:
          Temp.(C)   Voltage(V)  Bias(mA)  RX power(dBm)  TX power(dBm)
    High  70         3.50        30.00     -3.00          0.00
    Low   0          3.10        1.00      -15.00         -9.50
Ten-GigabitEthernet1/0/50 transceiver diagnostic information:
  Current diagnostic parameters:
    Temp.(C) Voltage(V)  Bias(mA)  RX power(dBm)  TX power(dBm)
    32         3.36        5.44      -15.07         -5.11
  Alarm thresholds:
          Temp.(C) Voltage(V)  Bias(mA)  RX power(dBm)  TX power(dBm)
    High  73         3.80        12.00     0.00           0.00
    Low   -3         2.81        1.00      -16.99         -12.50
Ten-GigabitEthernet1/0/51 transceiver diagnostic information:
The transceiver is absent.
"""


def test_h3c_parser_keeps_warning_and_alarm_thresholds_in_the_same_entity():
    sample = SimpleNamespace(
        output=H3C_MIXED_THRESHOLD_SAMPLE,
        file_path="sample.txt",
        device_id="switch-1",
    )
    synthesized = synthesize_template(
        command="display transceiver diagnosis interface",
        samples=(sample,),
        spec=OPTICAL_SPEC,
    )

    assert synthesized is not None
    result = validate_template(
        synthesized.template_text,
        sample.output,
        list(OPTICAL_SPEC.required_fields),
        OPTICAL_SPEC.validators,
        primary_keys=OPTICAL_SPEC.primary_keys,
        expected_record_count=3,
        expected_primary_keys={
            ("Ten-GigabitEthernet1/0/49",),
            ("Ten-GigabitEthernet1/0/50",),
            ("Ten-GigabitEthernet1/0/51",),
        },
    )

    assert result.is_valid, result.errors
    warning_and_alarm = result.records[0]
    assert warning_and_alarm["rx_low_warning_dbm"] == "-15.00"
    assert warning_and_alarm["rx_high_warning_dbm"] == "-3.00"
    assert warning_and_alarm["rx_low_alarm_dbm"] == "-18.01"
    assert warning_and_alarm["rx_high_alarm_dbm"] == "0.00"
    alarm_only = result.records[1]
    assert alarm_only["rx_low_warning_dbm"] == ""
    assert alarm_only["rx_low_alarm_dbm"] == "-16.99"
    assert result.records[2]["transceiver_present"] == "absent"


def test_textfsm_end_marker_is_opt_in_and_rejects_input_collision():
    ordinary = "Value field (\\S+)\n\nStart\n  ^${field} -> Record\n"
    assert prepare_textfsm_input(ordinary, "value") == "value"

    marker_template = ordinary + f"  ^{TEXTFSM_END_OF_INPUT} -> Record\n"
    assert prepare_textfsm_input(marker_template, "value").endswith(
        f"{TEXTFSM_END_OF_INPUT}\n"
    )
    with pytest.raises(ValueError, match="reserved TextFSM end marker"):
        prepare_textfsm_input(marker_template, TEXTFSM_END_OF_INPUT)
