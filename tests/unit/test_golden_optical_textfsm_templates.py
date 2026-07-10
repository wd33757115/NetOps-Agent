from __future__ import annotations

import io
from pathlib import Path

import pytest


textfsm = pytest.importorskip("textfsm")

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _parse_template(template_path: Path, raw_text: str) -> list[dict[str, str]]:
    parser = textfsm.TextFSM(io.StringIO(template_path.read_text(encoding="utf-8")))
    return [dict(zip(parser.header, row, strict=False)) for row in parser.ParseText(raw_text)]


def test_cisco_transceiver_detail_template_parses_power_and_dom_unsupported():
    rows = _parse_template(
        PROJECT_ROOT
        / "src/skills/shared/textfsm-templates/Cisco/IOS-C2960/show_interface_transceiver_detail__1.0.0.textfsm",
        """
Gi1/0/27 transceiver is present
  Tx Power      -6.55 dBm       0.99 dBm  -13.56 dBm   -3.00 dBm     -9.50 dBm
  Rx Power      -18.10 dBm       3.95 dBm  -21.54 dBm    0.00 dBm    -17.21 dBm
Gi1/0/28 transceiver is present
  DOM is not supported
""",
    )

    assert rows[0]["interface"] == "Gi1/0/27"
    assert rows[0]["rx_power_dbm"] == "-18.10"
    assert rows[0]["tx_power_dbm"] == "-6.55"
    assert rows[0]["rx_low_warning_dbm"] == "-17.21"
    assert rows[1]["interface"] == "Gi1/0/28"
    assert rows[1]["dom_supported"] == "not supported"
    assert rows[1]["rx_power_dbm"] == ""


def test_h3c_transceiver_diagnosis_template_parses_power_and_absent():
    rows = _parse_template(
        PROJECT_ROOT
        / "src/skills/shared/textfsm-templates/H3C/Comware7/display_transceiver_diagnosis_interface__1.0.0.textfsm",
        """
Ten-GigabitEthernet1/0/1 transceiver diagnosis information:
  Rx power: -18.2 dBm
  Tx power: -5.1 dBm
Ten-GigabitEthernet1/0/2 transceiver is absent
""",
    )

    assert rows == [
        {
            "interface": "Ten-GigabitEthernet1/0/1",
            "transceiver_present": "",
            "rx_power_dbm": "-18.2",
            "tx_power_dbm": "-5.1",
            "rx_low_warning_dbm": "",
            "rx_high_warning_dbm": "",
            "tx_low_warning_dbm": "",
            "tx_high_warning_dbm": "",
            "rx_low_alarm_dbm": "",
            "tx_low_alarm_dbm": "",
        },
        {
            "interface": "Ten-GigabitEthernet1/0/2",
            "transceiver_present": "absent",
            "rx_power_dbm": "",
            "tx_power_dbm": "",
            "rx_low_warning_dbm": "",
            "rx_high_warning_dbm": "",
            "tx_low_warning_dbm": "",
            "tx_high_warning_dbm": "",
            "rx_low_alarm_dbm": "",
            "tx_low_alarm_dbm": "",
        },
    ]
