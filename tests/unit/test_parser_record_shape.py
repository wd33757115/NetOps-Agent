import pytest

from src.core.parser.record_shape import RecordShapeError, expand_list_primary_records


def test_expands_textfsm_list_primary_key_rows_with_scalar_thresholds():
    records = expand_list_primary_records(
        [
            {
                "interface": "HundredGigE1/0/25",
                "channel": ["1", "2"],
                "rx_power_dbm": ["-2.1", "-2.2"],
                "tx_power_dbm": ["-1.1", "-1.2"],
                "rx_low_alarm_dbm": "-10.5",
            }
        ],
        ("interface", "channel"),
    )

    assert records == [
        {
            "interface": "HundredGigE1/0/25",
            "channel": "1",
            "rx_power_dbm": "-2.1",
            "tx_power_dbm": "-1.1",
            "rx_low_alarm_dbm": "-10.5",
        },
        {
            "interface": "HundredGigE1/0/25",
            "channel": "2",
            "rx_power_dbm": "-2.2",
            "tx_power_dbm": "-1.2",
            "rx_low_alarm_dbm": "-10.5",
        },
    ]


def test_rejects_misaligned_list_values_instead_of_guessing_pairs():
    with pytest.raises(RecordShapeError, match="do not align"):
        expand_list_primary_records(
            [{"channel": ["1", "2"], "rx_power_dbm": ["-2.1"]}],
            ("channel",),
        )
