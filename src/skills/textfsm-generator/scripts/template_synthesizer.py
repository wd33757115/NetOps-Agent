# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501 - TextFSM rules are intentionally one physical regex line.

"""Deterministic TextFSM candidates for common CLI output structures."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from config_loader import CategorySpec


class HasOutput(Protocol):
    output: str


@dataclass(frozen=True)
class SynthesizedTemplate:
    template_text: str
    strategy: str


def _sample_text(samples: tuple[HasOutput, ...], limit: int = 3) -> str:
    return "\n".join(str(sample.output or "") for sample in samples[:limit])


def expected_min_records(sample_text: str, spec: CategorySpec) -> int | None:
    entity = (spec.entity_type or spec.name or "").lower()
    text = sample_text or ""
    if entity == "interface_optical":
        count = len(
            re.findall(
                r"(?mi)^\s*\S+\s+transceiver\s+"
                r"(?:diagnosis|diagnostic)\s+information\s*:",
                text,
            )
        )
        if not count:
            count = len(
                re.findall(
                    r"(?mi)^\s*\S+\s+transceiver\s+is\s+"
                    r"(?:present|absent)\b",
                    text,
                )
            )
        return count or None
    if entity == "fan":
        env_count = len(
            re.findall(
                r"(?mi)^\s*(?:Fan\d\S*|Fan_in_PS\S+)\s+\S+\s+\S+\s+\S+\s+"
                r"(?:Ok|Fail|Failed|Absent|Present|Fault|Normal)\s*$",
                text,
            )
        )
        if env_count:
            return env_count
        block_count = len(re.findall(r"(?mi)^\s*Fan\s+\S+\s*:", text))
        if block_count:
            return block_count
        table_count = len(
            re.findall(r"(?mi)^\s*\S+\s+(?:Normal|Fault|Absent|Present)\s+\S+", text)
        )
        return table_count or None
    if entity == "inventory_component":
        count = len(re.findall(r'(?mi)^\s*NAME:\s+"[^"]+"\s*,\s*DESCR:', text))
        if not count:
            count = len(re.findall(r"(?mi)^\s*DEVICE_NAME\s+:", text))
        return count or None
    if entity == "memory":
        count = len(re.findall(r"(?mi)^\s*Slot\s+\S+\s*:", text))
        return count or None
    if entity == "interface_l2":
        count = len(
            re.findall(
                r"(?mi)^\s*(?:[A-Za-z]+[A-Za-z0-9/._:-]*|mgmt\d*)\s+"
                r".*?\s+"
                r"(?:connected|notconnect|notconnec|disabled|xcvrAbsen|"
                r"err-disabled|inactive|sfpAbsent)\s+"
                r"\S+\s+\S+\s+\S+\s+\S+",
                text,
            )
        )
        return count or None
    if entity == "power_supply":
        count = len(
            re.findall(r"(?mi)^\s*(?:Power|Power\s+Supply|PWR|PSU)\s+\S+\s*:", text)
        )
        if not count:
            count = len(
                re.findall(
                    r"(?mi)^\s*\d+\s+\S+\s+\d+(?:\.\d+)?\s+W\s+"
                    r"\d+(?:\.\d+)?\s+W\s+\d+(?:\.\d+)?\s+W\s+\S+\s*$",
                    text,
                )
            )
        if not count:
            count = len(
                re.findall(
                    r"(?mi)^\s*\d+\s+(?:Normal|Abnormal|Fault|Absent|Present|normal|fault)\b",
                    text,
                )
            )
        if not count:
            count = len(
                re.findall(
                    r"(?mi)^\s*PWR\S+\s+(?:normal|abnormal|fault|absent|present)\b",
                    text,
                )
            )
        return count or None
    if entity == "environment_sensor":
        count = len(
            re.findall(
                r"(?mi)^\s*\S+\s+\S+\s+\d+(?:\.\d+)?\s+\d+(?:\.\d+)?\s+"
                r"-?\d+(?:\.\d+)?\s+\S+\s*$",
                text,
            )
        )
        if not count:
            count = len(
                re.findall(
                    r"(?mi)^\s*\d+\s+\S+(?:\s+\d+)?\s+-?\d+\s+-?\d+\s+-?\d+\s+-?\d+",
                    text,
                )
            )
        if not count:
            count = len(
                re.findall(
                    r"(?mi)^\s*\d+\s+\d+\s+\S+\s+\d+\s+-?\d+\s+-?\d+\s+-?\d+\s+-?\d+",
                    text,
                )
            )
        return count or None
    if entity == "system_resource":
        if re.search(r"(?mi)^\s*CPU states\s+:", text) and re.search(
            r"(?mi)^\s*Memory usage\s+:", text
        ):
            return 1
        return None
    if entity == "redundancy_member":
        if re.search(r"(?mi)^\s*vPC\s+domain\s+id\s+:", text):
            count = len(
                re.findall(
                    r"(?mi)^\s*\d+\s+Po\S+\s+"
                    r"(?:up|down|suspended|failed|disabled|inactive)\b",
                    text,
                )
            )
            return count or None
        if re.search(r"(?mi)^\s*MemberID\s+Role\s+Priority\s+CPU-Mac", text):
            count = len(
                re.findall(
                    r"(?mi)^\s*[*+]*\d+\s+\S+\s+\d+\s+[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}",
                    text,
                )
            )
            return count or None
        count = len(re.findall(r"(?mi)^\s*(?:MemberID|Member|Peer|vPC)\b", text))
        return count if count > 1 else None
    if entity == "vrrp":
        return None
    return None


def expected_primary_key_tuples(
    sample_text: str,
    spec: CategorySpec,
) -> set[tuple[str, ...]]:
    """Extract deterministic entity keys when the CLI exposes stable headers."""
    entity = (spec.entity_type or spec.name or "").lower()
    if entity != "interface_optical" or tuple(spec.primary_keys) != ("interface",):
        return set()
    return {
        (match.group("interface"),)
        for match in re.finditer(
            r"(?mi)^\s*(?P<interface>\S+)\s+transceiver\s+"
            r"(?:diagnosis|diagnostic)\s+information\s*:",
            sample_text or "",
        )
    }


def _h3c_transceiver_diagnostics_template(
    sample_text: str,
) -> SynthesizedTemplate | None:
    if not re.search(
        r"(?mi)^\s*\S+\s+transceiver\s+(?:diagnosis|diagnostic)\s+information\s*:",
        sample_text,
    ):
        return None
    if not re.search(
        r"(?mi)^\s*(?:Current diagnostic parameters:|The transceiver is absent\.|"
        r"The transceiver does not support this function\.)",
        sample_text,
    ):
        return None
    return SynthesizedTemplate(
        r"""Value interface (\S+)
Value transceiver_present (absent|present)
Value dom_supported (does not support)
Value temperature_celsius (-?\d+(?:\.\d+)?)
Value voltage_v (-?\d+(?:\.\d+)?)
Value bias_ma (-?\d+(?:\.\d+)?)
Value rx_power_dbm (-?\d+(?:\.\d+)?)
Value tx_power_dbm (-?\d+(?:\.\d+)?)
Value rx_low_warning_dbm (-?\d+(?:\.\d+)?)
Value rx_high_warning_dbm (-?\d+(?:\.\d+)?)
Value tx_low_warning_dbm (-?\d+(?:\.\d+)?)
Value tx_high_warning_dbm (-?\d+(?:\.\d+)?)
Value rx_low_alarm_dbm (-?\d+(?:\.\d+)?)
Value tx_low_alarm_dbm (-?\d+(?:\.\d+)?)
Value rx_power_channel_1_dbm (-?\d+(?:\.\d+)?)
Value rx_power_channel_2_dbm (-?\d+(?:\.\d+)?)
Value rx_power_channel_3_dbm (-?\d+(?:\.\d+)?)
Value rx_power_channel_4_dbm (-?\d+(?:\.\d+)?)
Value tx_power_channel_1_dbm (-?\d+(?:\.\d+)?)
Value tx_power_channel_2_dbm (-?\d+(?:\.\d+)?)
Value tx_power_channel_3_dbm (-?\d+(?:\.\d+)?)
Value tx_power_channel_4_dbm (-?\d+(?:\.\d+)?)

Start
  ^\s*${interface}\s+transceiver\s+(?:diagnosis|diagnostic)\s+information\s*: -> Block

Block
  ^\s*\S+\s+transceiver\s+(?:diagnosis|diagnostic)\s+information\s*: -> Continue.Record
  ^\s*${interface}\s+transceiver\s+(?:diagnosis|diagnostic)\s+information\s*:
  ^The transceiver is ${transceiver_present}\. -> Record Start
  ^The transceiver ${dom_supported} this function\. -> Record Start
  ^\s*\[module\]\s+Temp\.\(C\)\s+Voltage\(V\)
  ^\s*\[channel\]\s+Bias\(mA\)\s+RX power\(dBm\)\s+TX power\(dBm\)
  ^\s*1\s+-?\d+(?:\.\d+)?\s+${rx_power_channel_1_dbm}\s+${tx_power_channel_1_dbm}\s*$$
  ^\s*2\s+-?\d+(?:\.\d+)?\s+${rx_power_channel_2_dbm}\s+${tx_power_channel_2_dbm}\s*$$
  ^\s*3\s+-?\d+(?:\.\d+)?\s+${rx_power_channel_3_dbm}\s+${tx_power_channel_3_dbm}\s*$$
  ^\s*4\s+-?\d+(?:\.\d+)?\s+${rx_power_channel_4_dbm}\s+${tx_power_channel_4_dbm}\s*$$ -> Record Start
  ^\s*${temperature_celsius}\s+${voltage_v}\s+${bias_ma}\s+${rx_power_dbm}\s+${tx_power_dbm}\s*$$
  ^\s*High\s+-?\d+(?:\.\d+)?\s+-?\d+(?:\.\d+)?\s+-?\d+(?:\.\d+)?\s+${rx_high_warning_dbm}\s+${tx_high_warning_dbm}\s*$$
  ^\s*Low\s+-?\d+(?:\.\d+)?\s+-?\d+(?:\.\d+)?\s+-?\d+(?:\.\d+)?\s+${rx_low_alarm_dbm}\s+${tx_low_alarm_dbm}\s*$$ -> Record Start

EOF
""",
        "section_block:h3c_transceiver_diagnostics",
    )


def _slot_fan_block_template(sample_text: str) -> SynthesizedTemplate | None:
    if not re.search(r"(?mi)^\s*Slot\s+\S+\s*:", sample_text):
        return None
    if not re.search(r"(?mi)^\s*Fan\s+\S+\s*:", sample_text):
        return None
    has_airflow = "Airflow Direction" in sample_text
    if has_airflow:
        return SynthesizedTemplate(
            """Value Filldown slot (\\d+)
Value fan (\\S+)
Value state (\\S+)
Value direction (.+)
Value speed (\\S+)
Value model (.+)

Start
  ^\\s*Slot\\s+${slot}: -> Slot

Slot
  ^\\s*Slot\\s+${slot}: -> Slot
  ^\\s*Fan\\s+${fan}: -> Fan
  ^\\s*$$

Fan
  ^\\s*State\\s+:\\s+${state}
  ^\\s*Airflow Direction:\\s+${direction}
  ^\\s*Prefer Airflow Direction:.* -> Record Slot
  ^\\s*Slot\\s+${slot}: -> Slot

EOF
""",
            "section_block:slot_fan_airflow",
        )
    return SynthesizedTemplate(
        """Value Filldown slot (\\d+)
Value fan (\\S+)
Value state (\\S+)
Value direction (.+)
Value speed (\\S+)
Value model (.+)

Start
  ^\\s*Slot\\s+${slot}: -> Slot

Slot
  ^\\s*Slot\\s+${slot}: -> Slot
  ^\\s*Fan\\s+${fan}: -> Fan
  ^\\s*$$

Fan
  ^\\s*State\\s+:\\s+${state} -> Record Slot
  ^\\s*Slot\\s+${slot}: -> Slot

EOF
""",
        "section_block:slot_fan_state",
    )


def _fan_index_table_template(sample_text: str) -> SynthesizedTemplate | None:
    if not re.search(r"(?mi)^\s*Index\s+Status\s+Speedlevel\b", sample_text):
        return None
    return SynthesizedTemplate(
        """Value fan (\\S+)
Value state (\\S+)
Value speed (\\S+)
Value slot (\\S+)
Value direction (.+)
Value model (.+)

Start
  ^\\s*Index\\s+Status\\s+Speedlevel\\s*$$
  ^\\s*-+\\s*$$
  ^\\s*${fan}\\s+${state}\\s+${speed}\\s*$$ -> Record
""",
        "table:index_status_speedlevel",
    )


def _inventory_name_pid_template(sample_text: str) -> SynthesizedTemplate | None:
    if not re.search(r'(?mi)^\s*NAME:\s+"[^"]+"\s*,\s*DESCR:', sample_text):
        return None
    if not re.search(r"(?mi)^\s*PID:\s+", sample_text):
        return None
    return SynthesizedTemplate(
        """Value component ([^"]+)
Value name ([^"]*)
Value pid (\\S+)
Value serial (.*)
Value vendor (\\S+)

Start
  ^\\s*NAME:\\s+"${component}"\\s*,\\s*DESCR:\\s+"${name}" -> Item

Item
  ^\\s*PID:\\s+${pid}\\s*,\\s*VID:\\s+\\S*\\s*,\\s*SN:\\s*${serial}\\s*$$ -> Record Start

EOF
""",
        "paired_lines:name_descr_pid_sn",
    )


def _h3c_manuinfo_template(sample_text: str) -> SynthesizedTemplate | None:
    if not re.search(r"(?mi)^\s*DEVICE_NAME\s+:", sample_text):
        return None
    if not re.search(r"(?mi)^\s*DEVICE_SERIAL_NUMBER\s+:", sample_text):
        return None
    return SynthesizedTemplate(
        """Value component (Chassis self|Slot\\s+\\d+\\s+CPU\\s+\\d+|Subslot\\s+\\d+|Fan\\s+\\d+|Power\\s+\\d+)
Value name (.+)
Value serial (\\S+)
Value mac_address (\\S+)
Value manufacturing_date (\\S+)
Value vendor (\\S+)
Value pid (.+)

Start
  ^\\s*${component}:\\s*$$ -> Item

Item
  ^\\s*${component}:\\s*$$ -> Item
  ^\\s*DEVICE_NAME\\s+:\\s+${name}\\s*$$
  ^\\s*DEVICE_SERIAL_NUMBER\\s+:\\s+${serial}\\s*$$
  ^\\s*MAC_ADDRESS\\s+:\\s+${mac_address}\\s*$$
  ^\\s*MANUFACTURING_DATE\\s+:\\s+${manufacturing_date}\\s*$$
  ^\\s*VENDOR_NAME\\s+:\\s+${vendor}\\s*$$
  ^\\s*PRODUCT\\s+ID\\s+:\\s+${pid}\\s*$$ -> Record Start

EOF
""",
        "section_block:h3c_manuinfo",
    )


def _interface_status_template(sample_text: str) -> SynthesizedTemplate | None:
    if not re.search(
        r"(?mi)^\s*Port\s+Name\s+Status\s+Vlan\s+Duplex\s+Speed\s+Type\s*$",
        sample_text,
    ):
        return None
    return SynthesizedTemplate(
        """Value interface (\\S+)
Value description (.{0,18}?)
Value status (\\S+)
Value vlan (\\S+)
Value duplex (\\S+)
Value speed (\\S+)
Value type (.+?)

Start
  ^\\s*-+\\s*$$
  ^\\s*Port\\s+Name\\s+Status\\s+Vlan\\s+Duplex\\s+Speed\\s+Type\\s*$$
  ^\\s*${interface}\\s+${description}\\s+${status}\\s+${vlan}\\s+${duplex}\\s+${speed}\\s+${type}\\s*$$ -> Record
""",
        "fixed_width_table:interface_status",
    )


def _environment_fan_template(sample_text: str) -> SynthesizedTemplate | None:
    if not re.search(
        r"(?mi)^\s*Fan\s+Model\s+Hw\s+Direction\s+Status\s*$",
        sample_text,
    ):
        return None
    return SynthesizedTemplate(
        """Value fan (\\S+)
Value model (\\S+)
Value direction (\\S+)
Value state (\\S+)
Value slot (\\S+)
Value speed (\\S+)

Start
  ^\\s*-+\\s*$$
  ^\\s*Fan\\s+Model\\s+Hw\\s+Direction\\s+Status\\s*$$
  ^\\s*Fan\\s+Zone\\s+Speed:.*$$
  ^\\s*Fan\\s+Air\\s+Filter\\s+:.*$$
  ^\\s*${fan}\\s+${model}\\s+\\S+\\s+${direction}\\s+${state}\\s*$$ -> Record
""",
        "fixed_width_table:environment_fan",
    )


def _environment_power_template(sample_text: str) -> SynthesizedTemplate | None:
    if not re.search(
        r"(?mi)^\s*Supply\s+Model\s+Output\s+Input\s+Capacity\s+Status\s*$",
        sample_text,
    ) and "Power Supply:" not in sample_text:
        return None
    return SynthesizedTemplate(
        """Value slot (\\S+)
Value power_supply (\\S+)
Value model (\\S+)
Value output_watts (\\d+(?:\\.\\d+)?)
Value input_watts (\\d+(?:\\.\\d+)?)
Value capacity_watts (\\d+(?:\\.\\d+)?)
Value state (\\S+)

Start
  ^\\s*Power\\s+Supply:.*$$
  ^\\s*Voltage:.*$$
  ^\\s*Power\\s+Actual\\s+Actual\\s+Total\\s*$$
  ^\\s*Supply\\s+Model\\s+Output\\s+Input\\s+Capacity\\s+Status\\s*$$
  ^\\s*\\(Watts\\s*\\)\\s+\\(Watts\\s*\\)\\s+\\(Watts\\s*\\)\\s*$$
  ^\\s*-+.*$$
  ^\\s*${power_supply}\\s+${model}\\s+${output_watts}\\s+W\\s+${input_watts}\\s+W\\s+${capacity_watts}\\s+W\\s+${state}\\s*$$ -> Record
""",
        "fixed_width_table:environment_power",
    )


def _environment_temperature_template(sample_text: str) -> SynthesizedTemplate | None:
    if not re.search(
        r"(?mi)^\s*Module\s+Sensor\s+MajorThresh\s+MinorThres\s+CurTemp\s+Status\s*$",
        sample_text,
    ):
        return None
    return SynthesizedTemplate(
        """Value component (\\S+)
Value location (\\S+)
Value warning_threshold (\\d+(?:\\.\\d+)?)
Value minor_threshold (\\d+(?:\\.\\d+)?)
Value reading (-?\\d+(?:\\.\\d+)?)
Value state (\\S+)
Value unit (Celsius)

Start
  ^\\s*Temperature:.*$$
  ^\\s*-+\\s*$$
  ^\\s*Module\\s+Sensor\\s+MajorThresh\\s+MinorThres\\s+CurTemp\\s+Status\\s*$$
  ^\\s*\\(Celsius\\)\\s+\\(Celsius\\)\\s+\\(Celsius\\)\\s*$$
  ^\\s*${component}\\s+${location}\\s+${warning_threshold}\\s+${minor_threshold}\\s+${reading}\\s+${state}\\s*$$ -> Record
""",
        "fixed_width_table:environment_temperature",
    )


def _h3c_memory_template(sample_text: str) -> SynthesizedTemplate | None:
    if not re.search(r"(?mi)^\s*Memory statistics are measured in KB:", sample_text):
        return None
    return SynthesizedTemplate(
        """Value Filldown slot (\\S+)
Value total (\\d+)
Value used (\\d+)
Value free (\\d+)
Value shared (\\d+)
Value buffers (\\d+)
Value cached (\\d+)
Value free_ratio (\\d+(?:\\.\\d+)?)

Start
  ^\\s*Slot\\s+${slot}:\\s*$$
  ^\\s*Total\\s+Used\\s+Free\\s+Shared\\s+Buffers\\s+Cached\\s+FreeRatio\\s*$$
  ^\\s*Mem:\\s+${total}\\s+${used}\\s+${free}\\s+${shared}\\s+${buffers}\\s+${cached}\\s+${free_ratio}%\\s*$$ -> Record
  ^\\s*-\\/\\+\\s+Buffers\\/Cache:.*$$
  ^\\s*Swap:.*$$

EOF
""",
        "section_table:h3c_memory",
    )


def _h3c_power_slot_template(sample_text: str) -> SynthesizedTemplate | None:
    if not re.search(r"(?mi)^\s*PowerID\s+State\s+Mode\s+Current", sample_text):
        return None
    return SynthesizedTemplate(
        """Value Filldown slot (\\S+)
Value power_supply (\\S+)
Value state (\\S+)
Value mode (\\S+)
Value current (\\S+)
Value voltage (\\S+)
Value power (\\S+)
Value fan_direction (\\S+)

Start
  ^\\s*Slot\\s+${slot}:\\s*$$
  ^\\s*Input\\s+Power:.*$$
  ^\\s*PowerID\\s+State\\s+Mode\\s+Current\\(A\\)\\s+Voltage\\(V\\)\\s+Power\\(W\\)\\s+FanDirection\\s*$$
  ^\\s*${power_supply}\\s+${state}\\s+${mode}\\s+${current}\\s+${voltage}\\s+${power}\\s+${fan_direction}\\s*$$ -> Record

EOF
""",
        "section_table:h3c_power_slot",
    )


def _h3c_power_index_template(sample_text: str) -> SynthesizedTemplate | None:
    if not re.search(r"(?mi)^\s*Index\s+Status\s*$", sample_text):
        return None
    return SynthesizedTemplate(
        """Value power_supply (\\S+)
Value state (\\S+)
Value slot (\\S+)
Value mode (\\S+)
Value current (\\S+)
Value voltage (\\S+)
Value power (\\S+)
Value fan_direction (\\S+)

Start
  ^\\s*Index\\s+Status\\s*$$
  ^\\s*-+\\s*$$
  ^\\s*${power_supply}\\s+${state}\\s*$$ -> Record
""",
        "simple_table:h3c_power_index",
    )


def _h3c_environment_slot_template(sample_text: str) -> SynthesizedTemplate | None:
    if not re.search(r"(?mi)^\s*Slot\s+Sensor\s+Temperature\s+Lower\s+Warning", sample_text):
        return None
    return SynthesizedTemplate(
        """Value component (\\S+)
Value location (.+?)
Value reading (-?\\d+)
Value lower_threshold (-?\\d+)
Value warning_threshold (-?\\d+)
Value alarm_threshold (-?\\d+)
Value shutdown_threshold (\\S+)
Value unit (C)
Value state (\\S+)

Start
  ^\\s*System\\s+temperature\\s+information.*$$
  ^\\s*-+\\s*$$
  ^\\s*Slot\\s+Sensor\\s+Temperature\\s+Lower\\s+Warning\\s+Alarm\\s+Shutdown\\s*$$
  ^\\s*${component}\\s+${location}\\s+${reading}\\s+${lower_threshold}\\s+${warning_threshold}\\s+${alarm_threshold}\\s+${shutdown_threshold}\\s*$$ -> Record
""",
        "fixed_width_table:h3c_environment_slot",
    )


def _h3c_environment_subslot_template(sample_text: str) -> SynthesizedTemplate | None:
    if not re.search(r"(?mi)^\s*Slot\s+Subslot\s+Sensor\s+ID\s+Temperature", sample_text):
        return None
    return SynthesizedTemplate(
        """Value component (\\S+)
Value subslot (\\S+)
Value location (\\S+)
Value sensor_id (\\S+)
Value reading (-?\\d+)
Value lower_threshold (-?\\d+)
Value warning_threshold (-?\\d+)
Value alarm_threshold (-?\\d+)
Value unit (C)
Value state (\\S+)

Start
  ^\\s*Slot\\s+Subslot\\s+Sensor\\s+ID\\s+Temperature\\s+LowerLimit\\s+WarningLimit\\s+AlarmLimit\\s*$$
  ^\\s*-+\\s*$$
  ^\\s*${component}\\s+${subslot}\\s+${location}\\s+${sensor_id}\\s+${reading}\\s+${lower_threshold}\\s+${warning_threshold}\\s+${alarm_threshold}\\s*$$ -> Record
""",
        "fixed_width_table:h3c_environment_subslot",
    )


def _h3c_irf_template(sample_text: str) -> SynthesizedTemplate | None:
    if not re.search(r"(?mi)^\s*MemberID\s+Role\s+Priority\s+CPU-Mac", sample_text):
        return None
    return SynthesizedTemplate(
        """Value member (\\d+)
Value role (\\S+)
Value priority (\\d+)
Value cpu_mac (\\S+)
Value description (.+)
Value state (\\S+)

Start
  ^\\s*MemberID\\s+Role\\s+Priority\\s+CPU-Mac\\s+Description\\s*$$
  ^\\s*[*+]*${member}\\s+${role}\\s+${priority}\\s+${cpu_mac}\\s+${description}\\s*$$ -> Record
  ^\\s*-+\\s*$$
""",
        "fixed_width_table:h3c_irf",
    )


def _empty_allowed_template(sample_text: str) -> SynthesizedTemplate | None:
    if not re.search(r"(?i)not configured", sample_text):
        return None
    return SynthesizedTemplate(
        """Value member (\\S+)
Value state (\\S+)

Start
  ^__NETOPS_NEVER_MATCH__\\s+${member}\\s+${state}\\s*$$ -> Record
""",
        "empty_allowed:not_configured",
    )


def _system_resources_template(sample_text: str) -> SynthesizedTemplate | None:
    if not re.search(r"(?mi)^\s*Load average:", sample_text):
        return None
    if not re.search(r"(?mi)^\s*CPU states\s+:", sample_text):
        return None
    if not re.search(r"(?mi)^\s*Memory usage:", sample_text):
        return None
    return SynthesizedTemplate(
        """Value load_1m (\\d+(?:\\.\\d+)?)
Value load_5m (\\d+(?:\\.\\d+)?)
Value load_15m (\\d+(?:\\.\\d+)?)
Value process_total (\\d+)
Value process_running (\\d+)
Value cpu_user (\\d+(?:\\.\\d+)?)
Value cpu_kernel (\\d+(?:\\.\\d+)?)
Value cpu_idle (\\d+(?:\\.\\d+)?)
Value total (\\d+)
Value used (\\d+)
Value free (\\d+)
Value memory_status (\\S+)

Start
  ^\\s*Load\\s+average:\\s+1\\s+minute:\\s+${load_1m}\\s+5\\s+minutes:\\s+${load_5m}\\s+15\\s+minutes:\\s+${load_15m}\\s*$$
  ^\\s*Processes\\s+:\\s+${process_total}\\s+total,\\s+${process_running}\\s+running\\s*$$
  ^\\s*CPU\\s+states\\s+:\\s+${cpu_user}%\\s+user,\\s+${cpu_kernel}%\\s+kernel,\\s+${cpu_idle}%\\s+idle\\s*$$
  ^\\s*CPU\\d+\\s+states\\s+:.*$$
  ^\\s*Memory\\s+usage:\\s+${total}K\\s+total,\\s+${used}K\\s+used,\\s+${free}K\\s+free\\s*$$
  ^\\s*Current\\s+memory\\s+status:\\s+${memory_status}\\s*$$ -> Record
""",
        "key_value_block:system_resources",
    )


def _vpc_status_template(sample_text: str) -> SynthesizedTemplate | None:
    if not re.search(r"(?mi)^\s*vPC\s+domain\s+id\s+:", sample_text):
        return None
    if not re.search(r"(?mi)^\s*vPC\s+status\s*$", sample_text):
        return None
    return SynthesizedTemplate(
        """Value Filldown role (\\S+)
Value member (\\d+)
Value peer (Po\\S+)
Value state (\\S+)
Value consistency (success|failed|not-applicable|notapplicable|inconsistent|-)
Value reason (\\S+)
Value description (.*)

Start
  ^\\s*vPC\\s+role\\s+:\\s+${role}\\s*$$
  ^\\s*Id\\s+Port\\s+Status\\s+Consistency\\s+Reason\\s+Active\\s+vlans\\s*$$
  ^\\s*id\\s+Port\\s+Status\\s+Active\\s+vlans\\s*$$
  ^\\s*-+.*$$
  ^\\s*${member}\\s+${peer}\\s+${state}\\s+${consistency}\\s+${reason}\\s+${description}\\s*$$ -> Record
  ^\\s*${member}\\s+${peer}\\s+${state}\\s+${description}\\s*$$ -> Record

EOF
""",
        "fixed_width_table:vpc_status",
    )


def synthesize_template(
    *,
    command: str,
    samples: tuple[HasOutput, ...],
    spec: CategorySpec,
) -> SynthesizedTemplate | None:
    text = _sample_text(samples)
    entity = (spec.entity_type or spec.name or "").lower()
    if entity == "fan":
        return (
            _environment_fan_template(text)
            or _slot_fan_block_template(text)
            or _fan_index_table_template(text)
        )
    if entity == "inventory_component":
        return _inventory_name_pid_template(text) or _h3c_manuinfo_template(text)
    if entity == "memory":
        return _h3c_memory_template(text)
    if entity == "interface_l2":
        return _interface_status_template(text)
    if entity == "interface_optical":
        return _h3c_transceiver_diagnostics_template(text)
    if entity == "power_supply":
        return (
            _environment_power_template(text)
            or _h3c_power_slot_template(text)
            or _h3c_power_index_template(text)
        )
    if entity == "environment_sensor":
        return (
            _environment_temperature_template(text)
            or _h3c_environment_slot_template(text)
            or _h3c_environment_subslot_template(text)
        )
    if entity == "system_resource":
        return _system_resources_template(text)
    if entity == "vrrp":
        return _empty_allowed_template(text)
    if entity == "redundancy_member":
        return _vpc_status_template(text) or _h3c_irf_template(text) or _empty_allowed_template(text)
    return None
