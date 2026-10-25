"""Project canonical StateObservation facts into human-facing domain tables."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

ValueFormatter = Callable[[Any], Any]


def _identity(value: Any) -> Any:
    return value


def _bytes_to_gib(value: Any) -> Any:
    try:
        return round(float(value) / 1_073_741_824, 3)
    except (TypeError, ValueError):
        return value


@dataclass(frozen=True)
class ReportColumn:
    key: str
    label: str
    observation_types: tuple[str, ...]
    dimensions: tuple[tuple[str, Any], ...] = ()
    absent_dimensions: tuple[str, ...] = ()
    formatter: ValueFormatter = _identity

    def matches(self, observation: dict[str, Any]) -> bool:
        if str(observation.get("observation_type") or "") not in self.observation_types:
            return False
        actual = _dimensions(observation)
        return all(actual.get(name) == value for name, value in self.dimensions) and all(
            name not in actual for name in self.absent_dimensions
        )


@dataclass(frozen=True)
class ReportView:
    key: str
    sheet_name: str
    columns: tuple[ReportColumn, ...]
    entity_prefixes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReportTable:
    key: str
    sheet_name: str
    headers: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]


REPORT_VIEWS = (
    ReportView(
        key="device_version",
        sheet_name="版本",
        entity_prefixes=("device",),
        columns=(
            ReportColumn(
                "model",
                "设备型号",
                ("device.hardware.model", "device.model"),
            ),
            ReportColumn(
                "software_version",
                "软件版本",
                ("device.software.version",),
            ),
            ReportColumn(
                "software_release",
                "发布版本",
                ("device.software.release",),
            ),
            ReportColumn(
                "reboot_reason",
                "最近重启原因",
                ("device.reboot.reason",),
            ),
            ReportColumn(
                "uptime",
                "运行时长",
                ("device.uptime",),
            ),
        ),
    ),
    ReportView(
        key="slot_inventory",
        sheet_name="板卡与子卡",
        entity_prefixes=("device_slot", "device_subslot", "slot", "subslot"),
        columns=(
            ReportColumn(
                "board_type",
                "板卡类型",
                ("device.slot.board_type",),
            ),
            ReportColumn(
                "hardware_version",
                "硬件版本",
                (
                    "device.slot.hardware_version",
                    "device.subslot.hardware_version",
                ),
            ),
            ReportColumn(
                "driver_version",
                "驱动版本",
                ("device.subslot.driver_version",),
            ),
            ReportColumn(
                "pcb_version",
                "PCB版本",
                ("device.slot.pcb_version",),
            ),
            ReportColumn(
                "cpld_version",
                "CPLD版本",
                (
                    "device.slot.cpld_version",
                    "device.subslot.cpld_version",
                ),
            ),
            ReportColumn(
                "memory_gib",
                "内存(GiB)",
                ("device.slot.memory_bytes",),
                formatter=_bytes_to_gib,
            ),
            ReportColumn(
                "flash_gib",
                "Flash(GiB)",
                ("device.slot.flash_bytes",),
                formatter=_bytes_to_gib,
            ),
        ),
    ),
    ReportView(
        key="cpu",
        sheet_name="CPU",
        columns=(
            ReportColumn(
                "cpu_5s",
                "CPU 5秒(%)",
                ("device.cpu.utilization",),
                dimensions=(("window", "5s"),),
            ),
            ReportColumn(
                "cpu_1m",
                "CPU 1分钟(%)",
                ("device.cpu.utilization",),
                dimensions=(("window", "1m"),),
            ),
            ReportColumn(
                "cpu_5m",
                "CPU 5分钟(%)",
                ("device.cpu.utilization",),
                dimensions=(("window", "5m"),),
            ),
            ReportColumn(
                "cpu_current",
                "CPU利用率(%)",
                ("device.cpu.utilization",),
                absent_dimensions=("window",),
            ),
            ReportColumn(
                "cpu_idle",
                "CPU空闲率(%)",
                ("device.cpu.idle",),
            ),
            ReportColumn(
                "cpu_high_warning_percent",
                "CPU预警阈值(%)",
                ("device.cpu.high_warning",),
            ),
            ReportColumn(
                "cpu_high_alarm_percent",
                "CPU严重阈值(%)",
                ("device.cpu.high_alarm",),
            ),
        ),
    ),
    ReportView(
        key="memory",
        sheet_name="内存",
        columns=(
            ReportColumn(
                "total_gib",
                "总内存(GiB)",
                ("device.memory.total_bytes", "device.memory_size"),
                formatter=_bytes_to_gib,
            ),
            ReportColumn(
                "used_gib",
                "已用内存(GiB)",
                ("device.memory.used_bytes", "device.memory.used"),
                formatter=_bytes_to_gib,
            ),
            ReportColumn(
                "free_gib",
                "可用内存(GiB)",
                ("device.memory.free_bytes",),
                formatter=_bytes_to_gib,
            ),
            ReportColumn(
                "utilization",
                "内存利用率(%)",
                ("device.memory.utilization",),
            ),
            ReportColumn(
                "free_ratio",
                "内存空闲率(%)",
                ("device.memory.free_ratio",),
            ),
            ReportColumn(
                "memory_high_warning_percent",
                "内存预警阈值(%)",
                ("device.memory.high_warning",),
            ),
            ReportColumn(
                "memory_high_alarm_percent",
                "内存严重阈值(%)",
                ("device.memory.high_alarm",),
            ),
        ),
    ),
    ReportView(
        key="fan",
        sheet_name="风扇",
        entity_prefixes=("environment_fan",),
        columns=(
            ReportColumn(
                "fan_state",
                "风扇状态",
                ("environment.fan.state",),
            ),
        ),
    ),
    ReportView(
        key="power",
        sheet_name="电源",
        entity_prefixes=("environment_power",),
        columns=(
            ReportColumn(
                "power_state",
                "电源状态",
                ("environment.power.state",),
            ),
        ),
    ),
    ReportView(
        key="temperature",
        sheet_name="温度",
        entity_prefixes=("environment_sensor", "temperature_sensor"),
        columns=(
            ReportColumn(
                "temperature_current_c",
                "当前温度(°C)",
                ("environment.temperature.current",),
            ),
            ReportColumn(
                "temperature_warning_c",
                "预警阈值(°C)",
                ("environment.temperature.warning",),
            ),
            ReportColumn(
                "temperature_alarm_c",
                "严重阈值(°C)",
                ("environment.temperature.alarm",),
            ),
        ),
    ),
    ReportView(
        key="interface",
        sheet_name="接口状态",
        entity_prefixes=("interface",),
        columns=(
            ReportColumn(
                "oper_status",
                "链路状态",
                ("interface.runtime_statistics.line_status",),
            ),
            ReportColumn(
                "protocol_status",
                "协议状态",
                ("interface.runtime_statistics.protocol_status",),
            ),
            ReportColumn(
                "ip_address",
                "IP地址",
                ("interface.ip_address",),
            ),
            ReportColumn(
                "speed",
                "速率",
                ("interface.runtime_statistics.speed",),
            ),
            ReportColumn(
                "duplex",
                "双工",
                ("interface.runtime_statistics.duplex",),
            ),
            ReportColumn(
                "description",
                "描述",
                ("interface.runtime_statistics.description",),
            ),
        ),
    ),
    ReportView(
        key="optical",
        sheet_name="光模块健康",
        entity_prefixes=("interface_optical", "interface"),
        columns=(
            ReportColumn(
                "part_number",
                "模块料号",
                ("interface.optical.part_number",),
            ),
            ReportColumn(
                "serial_number",
                "模块序列号",
                ("interface.optical.serial_number",),
            ),
            ReportColumn(
                "presence",
                "光模块状态",
                ("interface.optical.presence",),
            ),
            ReportColumn(
                "temperature",
                "温度(°C)",
                ("interface.optical.temperature",),
            ),
            ReportColumn(
                "rx_power",
                "接收功率(dBm)",
                ("interface.optical.rx_power",),
            ),
            ReportColumn(
                "tx_power",
                "发送功率(dBm)",
                ("interface.optical.tx_power",),
            ),
            ReportColumn(
                "rx_low_warning",
                "接收低告警阈值(dBm)",
                ("interface.optical.rx_low_warning",),
            ),
            ReportColumn(
                "tx_low_warning",
                "发送低告警阈值(dBm)",
                ("interface.optical.tx_low_warning",),
            ),
            ReportColumn(
                "rx_low_alarm",
                "接收低严重阈值(dBm)",
                ("interface.optical.rx_low_alarm",),
            ),
            ReportColumn(
                "rx_high_warning",
                "接收高告警阈值(dBm)",
                ("interface.optical.rx_high_warning",),
            ),
            ReportColumn(
                "rx_high_alarm",
                "接收高严重阈值(dBm)",
                ("interface.optical.rx_high_alarm",),
            ),
            ReportColumn(
                "tx_low_alarm",
                "发送低严重阈值(dBm)",
                ("interface.optical.tx_low_alarm",),
            ),
            ReportColumn(
                "tx_high_warning",
                "发送高告警阈值(dBm)",
                ("interface.optical.tx_high_warning",),
            ),
            ReportColumn(
                "tx_high_alarm",
                "发送高严重阈值(dBm)",
                ("interface.optical.tx_high_alarm",),
            ),
        ),
    ),
)


def _dimensions(observation: dict[str, Any]) -> dict[str, Any]:
    value = observation.get("dimensions_json")
    if value is None:
        value = observation.get("dimensions")
    return dict(value or {}) if isinstance(value or {}, dict) else {}


def _observation_value(observation: dict[str, Any]) -> Any:
    if observation.get("value_number") is not None:
        return observation["value_number"]
    value = observation.get("value_json")
    if value is None:
        value = observation.get("value")
    if value is not None and not isinstance(value, (dict, list)):
        return value
    if observation.get("value_text") is not None:
        return observation["value_text"]
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _observation_identity(observation: dict[str, Any]) -> str:
    return str(
        observation.get("id") or observation.get("observation_id") or f"object:{id(observation)}"
    )


def _sort_token(observation: dict[str, Any]) -> tuple[str, str]:
    observed_at = observation.get("observed_at")
    if isinstance(observed_at, datetime):
        observed = observed_at.isoformat()
    else:
        observed = str(observed_at or "")
    return observed, str(observation.get("fact_order_key") or observation.get("id") or "")


def _entity_matches(view: ReportView, observation: dict[str, Any]) -> bool:
    if not view.entity_prefixes:
        return True
    entity_type = str(observation.get("entity_type") or "")
    return any(
        entity_type == prefix or entity_type.startswith(f"{prefix}_")
        for prefix in view.entity_prefixes
    )


def _base_key(observation: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(observation.get("device_id") or ""),
        str(observation.get("entity_type") or ""),
        str(observation.get("entity_key") or "__singleton__"),
    )


def project_observation_view(
    observations: list[dict[str, Any]],
    view: ReportView,
) -> tuple[ReportTable, set[str]]:
    """Pivot one canonical view and report consumed observation IDs."""

    matching_columns = {column.key: column for column in view.columns}
    rows: dict[tuple[str, str, str], dict[str, Any]] = {}
    selected: dict[tuple[tuple[str, str, str], str], dict[str, Any]] = {}
    consumed: set[str] = set()
    for observation in observations:
        if not _entity_matches(view, observation):
            continue
        matched = [column for column in matching_columns.values() if column.matches(observation)]
        if not matched:
            continue
        key = _base_key(observation)
        row = rows.setdefault(
            key,
            {
                "device_id": key[0],
                "vendor": str(observation.get("vendor") or ""),
                "model": str(observation.get("model") or ""),
                "entity_type": key[1],
                "entity_key": key[2],
                "observed_at": observation.get("observed_at"),
                "_sort_token": _sort_token(observation),
            },
        )
        if _sort_token(observation) >= row["_sort_token"]:
            row["vendor"] = str(observation.get("vendor") or row["vendor"])
            row["model"] = str(observation.get("model") or row["model"])
            row["observed_at"] = observation.get("observed_at")
            row["_sort_token"] = _sort_token(observation)
        for column in matched:
            cell_key = (key, column.key)
            previous = selected.get(cell_key)
            if previous is None or _sort_token(observation) >= _sort_token(previous):
                selected[cell_key] = observation
                row[column.key] = column.formatter(_observation_value(observation))
            consumed.add(_observation_identity(observation))

    headers = (
        "设备ID",
        "厂商",
        "型号",
        "实体类型",
        "实体",
        *(column.label for column in view.columns),
        "采集时间",
    )
    output_rows = tuple(
        (
            row["device_id"],
            row["vendor"],
            row["model"],
            row["entity_type"],
            row["entity_key"],
            *(row.get(column.key) for column in view.columns),
            row["observed_at"],
        )
        for _, row in sorted(rows.items())
    )
    return (
        ReportTable(
            key=view.key,
            sheet_name=view.sheet_name,
            headers=headers,
            rows=output_rows,
        ),
        {item for item in consumed if item},
    )


def build_observation_report_tables(
    observations: list[dict[str, Any]],
) -> tuple[ReportTable, ...]:
    """Build stable domain sheets plus complete long-form evidence coverage."""

    tables: list[ReportTable] = []
    consumed: set[str] = set()
    for view in REPORT_VIEWS:
        table, view_consumed = project_observation_view(observations, view)
        # A commercial appendix needs a stable table of contents: an empty
        # domain means "not collected or not applicable", not a silently
        # omitted check.  The data-quality sheet explains the distinction.
        tables.append(table)
        consumed.update(view_consumed)

    other_rows: list[tuple[Any, ...]] = []
    evidence_rows: list[tuple[Any, ...]] = []
    for observation in sorted(
        observations,
        key=lambda item: (
            str(item.get("device_id") or ""),
            str(item.get("entity_type") or ""),
            str(item.get("entity_key") or ""),
            str(item.get("observation_type") or ""),
            json.dumps(_dimensions(item), ensure_ascii=False, sort_keys=True),
        ),
    ):
        observation_id = _observation_identity(observation)
        row = (
            observation.get("device_id"),
            observation.get("vendor"),
            observation.get("model"),
            observation.get("entity_type"),
            observation.get("entity_key"),
            observation.get("observation_type"),
            json.dumps(_dimensions(observation), ensure_ascii=False, sort_keys=True),
            _observation_value(observation),
            observation.get("unit"),
            observation.get("canonical_command"),
            observation.get("field_name"),
            observation.get("parser_template_id"),
            observation.get("evidence_id"),
            observation.get("observed_at"),
        )
        evidence_rows.append(row)
        if observation_id not in consumed:
            other_rows.append(row)

    detail_headers = (
        "设备ID",
        "厂商",
        "型号",
        "实体类型",
        "实体",
        "标准观测类型",
        "维度",
        "值",
        "单位",
        "来源命令",
        "来源字段",
        "Parser模板ID",
        "证据ID",
        "采集时间",
    )
    if other_rows:
        tables.append(
            ReportTable(
                key="other_structured",
                sheet_name="其他结构化数据",
                headers=detail_headers,
                rows=tuple(other_rows),
            )
        )
    tables.append(
        ReportTable(
            key="evidence_index",
            sheet_name="证据索引",
            headers=detail_headers,
            rows=tuple(evidence_rows),
        )
    )
    field_rows = tuple(
        (
            view.sheet_name,
            column.label,
            column.key,
            ", ".join(column.observation_types),
            ", ".join(f"{name}={value}" for name, value in column.dimensions) or "无",
        )
        for view in REPORT_VIEWS
        for column in view.columns
    )
    tables.append(
        ReportTable(
            key="canonical_field_dictionary",
            sheet_name="B字段字典",
            headers=("工作表", "报告列名", "B字段标识", "标准观测类型", "维度条件"),
            rows=field_rows,
        )
    )
    return tuple(tables)
