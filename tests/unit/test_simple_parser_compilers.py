from __future__ import annotations

import pytest

from src.core.parser_generation import (
    FieldSpec,
    KeyValueCompiler,
    ParserSample,
    ParserSpec,
    RepeatedBlockCompiler,
    SampleExpectation,
    SampleRole,
    TableCompiler,
    TextFSMValidationBackend,
    validate_template_candidate,
)


def _sample(raw_text: str, keys: tuple[tuple[str, ...], ...]) -> ParserSample:
    return ParserSample(
        sample_id="sample-1",
        raw_text=raw_text,
        role=SampleRole.GENERATION,
        expectation=SampleExpectation(
            record_count=len(keys),
            primary_keys=frozenset(keys),
        ),
    )


@pytest.mark.parametrize(
    ("compiler", "spec", "raw_text", "keys"),
    [
        (
            TableCompiler(),
            ParserSpec(
                entity="interface",
                primary_keys=("interface",),
                fields=(
                    FieldSpec("interface", True, "interface_name", source_labels=("Interface",)),
                    FieldSpec(
                        "status",
                        True,
                        "enum",
                        values=("UP", "DOWN"),
                        source_labels=("Link",),
                    ),
                ),
            ),
            "Interface Link\nGE1/0/1 UP\nGE1/0/2 DOWN\n",
            (("GE1/0/1",), ("GE1/0/2",)),
        ),
        (
            KeyValueCompiler(),
            ParserSpec(
                entity="device",
                primary_keys=("name",),
                fields=(
                    FieldSpec("name", True, source_labels=("Device name",)),
                    FieldSpec("version", True, source_labels=("Software version",)),
                ),
            ),
            "Device name: SW1\nSoftware version: 7.1\n",
            (("SW1",),),
        ),
        (
            RepeatedBlockCompiler(),
            ParserSpec(
                entity="interface_optical",
                primary_keys=("interface",),
                fields=(
                    FieldSpec("interface", True, "interface_name", source_labels=("Port",)),
                    FieldSpec(
                        "rx_power_dbm",
                        True,
                        "number",
                        source_labels=("Rx Power",),
                    ),
                    FieldSpec(
                        "tx_power_dbm",
                        True,
                        "number",
                        source_labels=("Tx Power",),
                    ),
                ),
            ),
            (
                "Port GE1/0/1:\nRx Power: -10.5\nTx Power: -2.1\n"
                "Port GE1/0/2:\nRx Power: -18.0\nTx Power: -2.3\n"
            ),
            (("GE1/0/1",), ("GE1/0/2",)),
        ),
        (
            TableCompiler(),
            ParserSpec(
                entity="interface_l3",
                primary_keys=("interface",),
                fields=(
                    FieldSpec(
                        "interface",
                        True,
                        "interface_name",
                        source_labels=("Interface",),
                    ),
                    FieldSpec(
                        "ip_address",
                        True,
                        "ip_or_unassigned",
                        source_labels=("IP Address",),
                    ),
                ),
            ),
            "Interface  IP Address\nGE1/0/1   192.0.2.1\nGE1/0/2   --\n",
            (("GE1/0/1",), ("GE1/0/2",)),
        ),
    ],
)
def test_deterministic_compilers_emit_templates_that_pass_hard_gates(
    compiler,
    spec,
    raw_text,
    keys,
):
    sample = _sample(raw_text, keys)

    candidate = compiler.compile(spec, (sample,))

    assert candidate is not None
    report = validate_template_candidate(
        candidate.template_text,
        spec,
        (sample,),
        TextFSMValidationBackend(),
    )
    assert report.passed, report.diagnostics


def test_table_compiler_refuses_unbound_headers():
    spec = ParserSpec(
        entity="interface",
        primary_keys=("interface",),
        fields=(
            FieldSpec("interface", True, "interface_name"),
            FieldSpec("status", True),
        ),
    )

    assert TableCompiler().compile(
        spec,
        (_sample("Port State\nGE1/0/1 UP\n", (("GE1/0/1",),)),),
    ) is None


def test_table_compiler_parses_multiple_table_layouts_without_dropping_entities():
    spec = ParserSpec(
        entity="interface",
        primary_keys=("interface",),
        fields=(
            FieldSpec("interface", True, "interface_name", source_labels=("Interface",)),
            FieldSpec(
                "status",
                True,
                "enum",
                values=("UP", "DOWN"),
                source_labels=("Link",),
            ),
            FieldSpec(
                "protocol",
                False,
                "regex",
                pattern=r"(?:UP|DOWN)",
                source_labels=("Protocol",),
            ),
            FieldSpec(
                "ip_address",
                False,
                "regex",
                pattern=r"(?:\d{1,3}\.){3}\d{1,3}|--",
                source_labels=("Primary IP",),
            ),
            FieldSpec(
                "speed",
                False,
                "regex",
                pattern=r"(?:\d+[GM](?:\(a\))?|auto)",
                source_labels=("Speed",),
            ),
            FieldSpec(
                "duplex",
                False,
                "regex",
                pattern=r"(?:(?:F|H|A)(?:\(a\))?|auto)",
                source_labels=("Duplex",),
            ),
            FieldSpec(
                "type",
                False,
                "enum",
                values=("A", "T", "H"),
                source_labels=("Type",),
            ),
            FieldSpec("pvid", False, "number", source_labels=("PVID",)),
            FieldSpec("description", False, source_labels=("Description",)),
        ),
    )
    raw = """Brief information on interfaces in route mode:
Link: ADM - administratively down; Stby - standby
Protocol: (s) - spoofing
Interface            Link Protocol Primary IP      Description
MGE0/0/0             UP   UP       10.0.0.1        management
Vlan1                DOWN DOWN     --

Brief information on interfaces in bridge mode:
Link: ADM - administratively down; Stby - standby
Speed: (a) - auto
Duplex: (a)/A - auto; H - half; F - full
Type: A - access; T - trunk; H - hybrid
Interface            Link Speed   Duplex Type PVID Description
GE1/0/1              UP   1G(a)   F(a)   A    703  server one
GE1/0/2              DOWN auto    A      A    703
"""
    expected = (("MGE0/0/0",), ("Vlan1",), ("GE1/0/1",), ("GE1/0/2",))
    sample = _sample(raw, expected)

    candidate = TableCompiler().compile(spec, (sample,))

    assert candidate is not None
    report = validate_template_candidate(
        candidate.template_text,
        spec,
        (sample,),
        TextFSMValidationBackend(),
    )
    assert report.passed, report.diagnostics
    assert {row["interface"] for row in report.samples[0].records} == {
        key[0] for key in expected
    }
    route_row, _vlan_row, bridge_row, _empty_description_row = report.samples[0].records
    assert route_row["protocol"] == "UP"
    assert route_row["description"] == "management"
    assert bridge_row["speed"] == "1G(a)"
    assert bridge_row["description"] == "server one"


def test_table_compiler_keeps_adjacent_text_columns_semantically_aligned():
    spec = ParserSpec(
        entity="interface_status",
        primary_keys=("port",),
        fields=(
            FieldSpec("port", True, "interface_name", source_labels=("Port",)),
            FieldSpec("name", False, "text", source_labels=("Name",)),
            FieldSpec("status", True, "text", source_labels=("Status",)),
            FieldSpec("vlan", False, "text", source_labels=("Vlan",)),
            FieldSpec("duplex", False, "text", source_labels=("Duplex",)),
            FieldSpec("speed", False, "text", source_labels=("Speed",)),
            FieldSpec("type", False, "text", source_labels=("Type",)),
        ),
    )
    raw = """Port          Name               Status    Vlan      Duplex  Speed   Type
--------------------------------------------------------------------------------
mgmt0         --                 connected routed    full    1000    --
Eth1/1        Connect_to_XA-FOTI connected 952       full    1000    1000base-T
Eth1/11       To_LAB-DEVICE-WAN-TA connected 1         full    1000    1000base-T
Eth1/3        --                 xcvrAbsen routed    auto    auto    --
--------------------------------------------------------------------------------
"""
    sample = _sample(raw, (("mgmt0",), ("Eth1/1",), ("Eth1/11",), ("Eth1/3",)))

    candidate = TableCompiler().compile(spec, (sample,))

    assert candidate is not None
    report = validate_template_candidate(
        candidate.template_text,
        spec,
        (sample,),
        TextFSMValidationBackend(),
    )
    assert report.passed, report.diagnostics
    assert report.samples[0].records == (
        {
            "port": "mgmt0",
            "name": "--",
            "status": "connected",
            "vlan": "routed",
            "duplex": "full",
            "speed": "1000",
            "type": "--",
        },
        {
            "port": "Eth1/1",
            "name": "Connect_to_XA-FOTI",
            "status": "connected",
            "vlan": "952",
            "duplex": "full",
            "speed": "1000",
            "type": "1000base-T",
        },
        {
            "port": "Eth1/11",
            "name": "To_LAB-DEVICE-WAN-TA",
            "status": "connected",
            "vlan": "1",
            "duplex": "full",
            "speed": "1000",
            "type": "1000base-T",
        },
        {
            "port": "Eth1/3",
            "name": "--",
            "status": "xcvrAbsen",
            "vlan": "routed",
            "duplex": "auto",
            "speed": "auto",
            "type": "--",
        },
    )


def test_table_compiler_binds_a_context_key_to_a_following_table():
    spec = ParserSpec(
        entity="memory_statistics",
        primary_keys=("slot",),
        fields=(
            FieldSpec("slot", True, source_labels=("Slot",)),
            FieldSpec("total", True, "number", source_labels=("Total",)),
            FieldSpec("used", True, "number", source_labels=("Used",)),
            FieldSpec("free", True, "number", source_labels=("Free",)),
            FieldSpec("shared", True, "number", source_labels=("Shared",)),
            FieldSpec("buffers", True, "number", source_labels=("Buffers",)),
            FieldSpec("cached", True, "number", source_labels=("Cached",)),
            FieldSpec("free_ratio", True, "percent", source_labels=("FreeRatio",)),
        ),
    )
    raw = """Memory statistics are measured in KB:
Slot 1:
             Total      Used      Free    Shared   Buffers    Cached   FreeRatio
Mem:       1946044   1034320    911724         0      4860    209388       47.4%
-/+ Buffers/Cache:    820072   1125972
Swap:            0         0         0
"""
    sample = _sample(raw, (("1",),))

    candidate = TableCompiler().compile(spec, (sample,))

    assert candidate is not None
    assert candidate.provider == "deterministic:table:context"
    report = validate_template_candidate(
        candidate.template_text,
        spec,
        (sample,),
        TextFSMValidationBackend(),
    )
    assert report.passed, report.diagnostics
    assert report.samples[0].records == (
        {
            "slot": "1",
            "total": "1946044",
            "used": "1034320",
            "free": "911724",
            "shared": "0",
            "buffers": "4860",
            "cached": "209388",
            "free_ratio": "47.4%",
        },
    )


def test_table_compiler_combines_summary_fields_with_a_wrapped_target_table():
    spec = ParserSpec(
        entity="redundancy_member",
        primary_keys=("member_id",),
        fields=(
            FieldSpec(
                "member_id",
                True,
                "number",
                source_labels=("Id",),
            ),
            FieldSpec(
                "port",
                True,
                "interface_name",
                source_labels=("Port",),
            ),
            FieldSpec("status", True, source_labels=("Status",)),
            FieldSpec(
                "consistency",
                True,
                source_labels=("Consistency",),
            ),
            FieldSpec(
                "reason",
                True,
                source_labels=("Reason",),
            ),
            FieldSpec(
                "active_ranges",
                True,
                source_labels=("Active ranges",),
            ),
            FieldSpec(
                "domain_id",
                True,
                "number",
                source_labels=("Domain id",),
            ),
            FieldSpec(
                "peer_status",
                True,
                source_labels=("Peer status",),
            ),
        ),
    )
    raw = """Domain id                        : 300
Peer status                      : peer formed

Peer-link status
------------------------------------------
id    Port   Status Active ranges
--    ----   ------ -----------------------
1     Po300  up     1-100

Member status
-----------------------------------------------------------------------
Id    Port          Status Consistency Reason       Active ranges
--    ------------  ------ ----------- ------------ -------------
400   Po400         up     success     success      1-20
                                                   ,30-40
401   Po401         up     success     success      50

402   Po402         up     success     success      60-70
"""
    expected = (("400",), ("401",), ("402",))
    sample = _sample(raw, expected)

    candidate = TableCompiler().compile(spec, (sample,))

    assert candidate is not None
    assert candidate.provider == "deterministic:table:summary_context"
    report = validate_template_candidate(
        candidate.template_text,
        spec,
        (sample,),
        TextFSMValidationBackend(),
    )
    assert report.passed, report.diagnostics
    assert [row["member_id"] for row in report.samples[0].records] == [
        "400",
        "401",
        "402",
    ]
    assert {row["domain_id"] for row in report.samples[0].records} == {"300"}
    assert {row["peer_status"] for row in report.samples[0].records} == {
        "peer formed"
    }
    assert all(row["member_id"] != "1" for row in report.samples[0].records)


def test_repeated_block_compiler_absorbs_sectioned_optical_tables():
    spec = ParserSpec(
        entity="interface_optical",
        primary_keys=("interface",),
        fields=(
            FieldSpec("interface", True, "interface_name"),
            FieldSpec(
                "transceiver_status",
                False,
                "enum",
                values=("absent",),
                source_labels=("The transceiver is/absent",),
            ),
            FieldSpec(
                "rx_power_dbm",
                False,
                "number",
                source_labels=("Current diagnostic parameters/RX power(dBm)",),
            ),
            FieldSpec(
                "tx_power_dbm",
                False,
                "number",
                source_labels=("Current diagnostic parameters/TX power(dBm)",),
            ),
            FieldSpec(
                "rx_low_alarm_dbm",
                False,
                "number",
                source_labels=("Alarm thresholds/Low/RX power(dBm)",),
            ),
            FieldSpec(
                "rx_low_warning_dbm",
                False,
                "number",
                source_labels=("Warning thresholds/Low/RX power(dBm)",),
            ),
        ),
    )
    raw = """Ten-GigabitEthernet1/0/49 transceiver diagnostic information:
  Current diagnostic parameters:
    Temp.(C)  Voltage(V)  Bias(mA)  RX power(dBm)  TX power(dBm)
    30        3.32        2.82      -5.34          -6.32
  Alarm thresholds:
          Temp.(C)  Voltage(V)  Bias(mA)  RX power(dBm)  TX power(dBm)
    High  73        3.80        33.00     0.00           6.00
    Low   -3        2.80        1.00      -18.01         -12.50
  Warning thresholds:
          Temp.(C)  Voltage(V)  Bias(mA)  RX power(dBm)  TX power(dBm)
    High  70        3.50        30.00     -3.00          0.00
    Low   0         3.10        1.00      -15.00         -9.50
Ten-GigabitEthernet1/0/50 transceiver diagnostic information:
The transceiver is absent.
Ten-GigabitEthernet1/0/51 transceiver diagnostic information:
  Current diagnostic parameters:
    Temp.(C)  Voltage(V)  Bias(mA)  RX power(dBm)  TX power(dBm)
    31        3.33        5.65      -8.07          -4.45
  Alarm thresholds:
          Temp.(C)  Voltage(V)  Bias(mA)  RX power(dBm)  TX power(dBm)
    High  73        3.80        12.00     0.00           0.00
    Low   -3        2.81        1.00      -16.99         -12.50
"""
    sample = _sample(
        raw,
        (
            ("Ten-GigabitEthernet1/0/49",),
            ("Ten-GigabitEthernet1/0/50",),
            ("Ten-GigabitEthernet1/0/51",),
        ),
    )

    candidate = RepeatedBlockCompiler().compile(spec, (sample,))

    assert candidate is not None
    report = validate_template_candidate(
        candidate.template_text,
        spec,
        (sample,),
        TextFSMValidationBackend(),
    )
    assert report.passed, report.diagnostics
    rows = report.samples[0].records
    assert rows[0]["rx_power_dbm"] == "-5.34"
    assert rows[0]["rx_low_warning_dbm"] == "-15.00"
    assert rows[1]["transceiver_status"] == "absent"
    assert rows[2]["rx_low_alarm_dbm"] == "-16.99"


def test_repeated_block_compiler_handles_labeled_metric_blocks():
    spec = ParserSpec(
        entity="processor",
        primary_keys=("slot", "cpu"),
        fields=(
            FieldSpec("slot", True, "number", source_labels=("Slot",)),
            FieldSpec("cpu", True, "number", source_labels=("CPU",)),
            FieldSpec(
                "utilization_5s",
                True,
                "percent",
                source_labels=("in last 5 seconds",),
            ),
            FieldSpec(
                "utilization_1m",
                True,
                "percent",
                source_labels=("in last 1 minute",),
            ),
            FieldSpec(
                "utilization_5m",
                True,
                "percent",
                source_labels=("in last 5 minutes",),
            ),
        ),
    )
    raw = """Slot 0 CPU 0 CPU usage:
       4% in last 5 seconds
       0% in last 1 minute
       1% in last 5 minutes

Slot 2 CPU 0 CPU usage:
      24% in last 5 seconds
      26% in last 1 minute
      15% in last 5 minutes
"""
    sample = _sample(raw, (("0", "0"), ("2", "0")))

    candidate = RepeatedBlockCompiler().compile(spec, (sample,))

    assert candidate is not None
    assert candidate.provider == "deterministic:repeated_block:labeled_metrics"
    report = validate_template_candidate(
        candidate.template_text,
        spec,
        (sample,),
        TextFSMValidationBackend(),
    )
    assert report.passed, (report.diagnostics, candidate.template_text)
    assert report.samples[0].records == (
        {
            "slot": "0",
            "cpu": "0",
            "utilization_5s": "4%",
            "utilization_1m": "0%",
            "utilization_5m": "1%",
        },
        {
            "slot": "2",
            "cpu": "0",
            "utilization_5s": "24%",
            "utilization_1m": "26%",
            "utilization_5m": "15%",
        },
    )


def test_repeated_block_compiler_handles_nested_parent_child_measurements():
    spec = ParserSpec(
        entity="interface_transceiver",
        primary_keys=("interface", "channel"),
        fields=(
            FieldSpec(
                "interface",
                True,
                "interface_name",
                source_labels=("Ethernet1/1",),
            ),
            FieldSpec(
                "present",
                True,
                "text",
                source_labels=(
                    "transceiver is present",
                    "transceiver is not present",
                ),
            ),
            FieldSpec(
                "dom_supported",
                False,
                "text",
                source_labels=("DOM is not supported",),
            ),
            FieldSpec(
                "rx_power_current",
                False,
                "number",
                source_labels=("Rx Power",),
            ),
            FieldSpec(
                "rx_power_alarm_high",
                False,
                "number",
                source_labels=("Rx Power High Alarm",),
            ),
            FieldSpec(
                "rx_power_alarm_low",
                False,
                "number",
                source_labels=("Rx Power Low Alarm",),
            ),
            FieldSpec(
                "rx_power_warning_high",
                False,
                "number",
                source_labels=("Rx Power High Warning",),
            ),
            FieldSpec(
                "rx_power_warning_low",
                False,
                "number",
                source_labels=("Rx Power Low Warning",),
            ),
            FieldSpec(
                "channel",
                True,
                "enum",
                values=("1", "2", "DOM", "SFP", "transceiver"),
                source_labels=(
                    "Lane Number:",
                    "transceiver is not present",
                    "DOM is not supported",
                    "SFP Detail Diagnostics Information",
                ),
            ),
        ),
    )
    raw = """Ethernet1/1
    transceiver is not present
Ethernet1/2
    transceiver is present
DOM is not supported
Ethernet1/3
    transceiver is present
Lane Number:1 Network Lane
    SFP Detail Diagnostics Information
                Current              Alarms                  Warnings
                Measurement     High        Low         High          Low
    Rx Power    -2.10 dBm       -1.00 dBm   -15.00 dBm  -2.00 dBm   -13.00 dBm
Lane Number:2 Network Lane
    SFP Detail Diagnostics Information
                Current              Alarms                  Warnings
                Measurement     High        Low         High          Low
    Rx Power    -2.20 dBm       -1.00 dBm   -15.00 dBm  -2.00 dBm   -13.00 dBm
"""
    expected = (
        ("Ethernet1/1", "transceiver"),
        ("Ethernet1/2", "DOM"),
        ("Ethernet1/3", "1"),
        ("Ethernet1/3", "2"),
    )
    sample = _sample(raw, expected)

    candidate = RepeatedBlockCompiler().compile(spec, (sample,))

    assert candidate is not None
    assert candidate.provider == "deterministic:repeated_block:nested_records"
    report = validate_template_candidate(
        candidate.template_text,
        spec,
        (sample,),
        TextFSMValidationBackend(),
    )
    assert report.passed, report.diagnostics
    assert report.samples[0].records[0]["present"] == "not present"
    assert (
        report.samples[0].records[1]["dom_supported"] == "not supported"
    ), candidate.template_text
    assert report.samples[0].records[2]["rx_power_current"] == "-2.10"
    assert report.samples[0].records[3]["rx_power_warning_low"] == "-13.00"
