"""Read policy workbooks by their headers, never by a fixed row offset."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
from policy_sets import address_text, addresses, port_text, ports
from policy_window import display, long_link, window

ALIASES = {
    "src_ip": {"src_ip", "源ip", "源地址", "源ip地址"},
    "dst_ip": {"dst_ip", "目的ip", "目的地址", "目的ip地址", "目标地址"},
    "port": {"port", "端口", "目的端口"},
    "proto": {"proto", "protocol", "协议", "协议类型"},
    "action": {"action", "动作", "策略动作"},
    "source_system": {"source_system", "src_system", "源业务系统"},
    "destination_system": {"destination_system", "dst_system", "目的业务系统", "目标业务系统"},
    "start_time": {"start_time", "开始时间", "生效时间"},
    "end_time": {"end_time", "结束时间", "失效时间"},
    "long_link": {"long_link", "长连接"},
}


def _text(value) -> str:
    return "" if pd.isna(value) else str(value).strip()


def _addresses(value: str) -> str:
    return ",".join(address_text(addresses(value)))


def read_policy_workbook(path: str, now: datetime | None = None) -> pd.DataFrame:
    now = now or datetime.now()
    sheet = pd.read_excel(path, header=None, engine="openpyxl", dtype=str)
    header = None
    columns = {}
    for index, row in sheet.iterrows():
        cells = [_text(value).lower().replace(" ", "") for value in row]
        found = {
            key: cells.index(alias)
            for key, aliases in ALIASES.items()
            for alias in aliases
            if alias in cells
        }
        if {"src_ip", "dst_ip", "port", "proto"} <= found.keys():
            header, columns = index, found
            break
    if header is None:
        raise ValueError(
            "上传的 Excel 不是策略申请表，缺少必要表头：源地址(src_ip)、"
            "目的地址(dst_ip)、端口(port)、协议(proto)。"
            "设备清单不能用于生成策略，请上传策略申请表。"
        )

    rows = []
    for index, row in sheet.loc[header + 1 :].iterrows():
        if any("策略规则说明" in _text(value) for value in row):
            break
        values = {key: _text(row.iloc[column]) for key, column in columns.items()}
        original = dict(values)
        if not any(values.values()):
            continue
        try:
            for column, name in enumerate(sheet.loc[header]):
                if _text(name) == "有效期" and _text(row.iloc[column]):
                    raise ValueError("有效期请分别填写 start_time 和 end_time")
            for key in ("src_ip", "dst_ip", "proto"):
                if not values[key]:
                    raise ValueError(f"{key} 不能为空")
            for key in ("src_ip", "dst_ip"):
                values[key] = _addresses(values[key])
                if not values[key]:
                    raise ValueError(f"{key} 不能为空")
            values["proto"] = values["proto"].lower()
            values["action"] = (values.get("action") or "permit").lower()
            if values["action"] != "permit":
                raise ValueError("本功能只生成 permit 放行策略，不支持拒绝策略")
            if values["proto"] not in {"tcp", "udp", "tcp/udp", "icmp"}:
                raise ValueError("协议暂只支持 tcp、udp、tcp/udp、icmp")
            if values["proto"] in {"tcp", "udp", "tcp/udp"} and not values["port"]:
                raise ValueError("TCP/UDP 策略必须明确填写端口")
            if values["proto"] == "icmp" and values["port"]:
                raise ValueError("ICMP 不支持端口条件，请留空端口")
            values["port"] = ",".join(port_text(ports(values["port"])))
            values["window"] = window(values.get("start_time", ""), values.get("end_time", ""), now)
            hours = long_link(values.get("long_link", ""))
            if hours is not None and "tcp" not in values["proto"].split("/"):
                raise ValueError("长连接只适用于 TCP")
            # Text, so a column of hours never turns into floats with NaN.
            values["long_link_hours"] = "" if hours is None else str(hours)
        except ValueError as exc:
            raise ValueError(f"策略申请表第 {index + 1} 行无效：{exc}") from exc
        values["_row"] = index + 1
        values["_original"] = original
        rows.append(values)
    if not rows:
        raise ValueError("策略申请表没有有效策略行，请填写源地址、目的地址、端口、协议和动作。")
    windows = {}
    for row in rows:
        if row["window"]:
            windows.setdefault(row["window"], []).append(str(row["_row"]))
    if len(windows) > 1:
        raise ValueError(
            "同一张申请表只能有一个有效期（设备上的时间段以工单号命名）："
            + "；".join(f"第 {'、'.join(lines)} 行为 {display(w)}" for w, lines in windows.items())
        )
    return pd.DataFrame(rows)
