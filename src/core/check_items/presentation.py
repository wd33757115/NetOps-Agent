"""Human-facing labels for stable check-item identities.

Catalog names remain immutable asset metadata. These labels are presentation
projections only and must never participate in catalog or runtime digests.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import NamedTuple


class CheckItemPresentation(NamedTuple):
    name: str
    description: str


_ZH_CN_PRESENTATIONS = MappingProxyType(
    {
        "device.version": CheckItemPresentation(
            "设备版本与资产信息",
            "保留软件版本、平台型号和机框身份，供审计与周期对比。",
        ),
        "system.cpu_usage": CheckItemPresentation(
            "CPU 使用率",
            "评估设备、槽位或处理器的 CPU 使用率。",
        ),
        "system.memory_usage": CheckItemPresentation(
            "内存使用率",
            "评估设备或槽位的内存容量与使用率。",
        ),
        "interface.runtime_statistics": CheckItemPresentation(
            "接口状态运行信息和统计信息",
            "检查接口状态、配置及收发统计，每个可用字段的变化产生信息事件。",
        ),
        "interface.optical_power": CheckItemPresentation(
            "接口光功率",
            "评估光模块在位状态及收发光功率测量值。",
        ),
        "environment.fan": CheckItemPresentation(
            "风扇状态",
            "评估风扇在位情况及运行状态。",
        ),
        "environment.power": CheckItemPresentation(
            "电源状态",
            "评估电源模块在位情况及运行状态。",
        ),
        "configuration.running": CheckItemPresentation(
            "运行配置变化",
            "保存并比较标准化后的运行配置证据。",
        ),
        "device.inventory": CheckItemPresentation(
            "硬件组件清单",
            "保留组件型号、序列号和厂商信息。",
        ),
        "environment.temperature": CheckItemPresentation(
            "温度传感器状态",
            "根据设备上报阈值评估温度传感器状态。",
        ),
        "interface.ip_assignment": CheckItemPresentation(
            "接口 IP 配置",
            "以厂商无关字段保留接口 IP 地址。",
        ),
        "redundancy.vrrp": CheckItemPresentation(
            "VRRP 冗余状态",
            "评估各 VRRP 实例的当前运行状态。",
        ),
        "redundancy.stack": CheckItemPresentation(
            "堆叠与 IRF 成员状态",
            "评估堆叠或 IRF 成员关系与运行状态。",
        ),
        "redundancy.mlag": CheckItemPresentation(
            "跨设备链路聚合状态",
            "评估 M-LAG 或 vPC 域的当前状态。",
        ),
        "network.vrf_inventory": CheckItemPresentation(
            "VRF 清单",
            "保留 VRF 实例清单和路由域身份。",
        ),
        "network.arp_table": CheckItemPresentation(
            "ARP 绑定清单",
            "保留 IP 与 MAC 绑定，供变化分析和证据报告使用。",
        ),
        "network.mac_table": CheckItemPresentation(
            "MAC 转发表清单",
            "保留 MAC 地址对应的 VLAN 与接口位置。",
        ),
        "configuration.startup": CheckItemPresentation(
            "启动配置选择",
            "比较启动配置文件选择和引导引用。",
        ),
        "operations.critical_logs": CheckItemPresentation(
            "关键运行日志变化",
            "比较脱敏后的关键日志窗口，不直接推断设备健康状态。",
        ),
        **{
            check_item_id: CheckItemPresentation(
                name, "保存未解析的命令原文作为诊断基线，诊断时与现场逐行对比；不解析、不判断健康。"
            )
            for check_item_id, name in (
                ("interface.brief_text", "接口简要状态原文"),
                ("interface.ip_brief_text", "三层接口简要状态原文"),
                ("network.routing_table_text", "路由表原文"),
                ("network.ospf_peer_text", "OSPF 邻居原文"),
                ("network.arp_text", "ARP 表原文"),
                ("network.mac_text", "MAC 地址表原文"),
                ("network.vlan_text", "VLAN 原文"),
                ("network.lldp_neighbor_text", "LLDP 邻居原文"),
                ("network.routing_statistics_text", "路由统计原文"),
                ("network.arp_count_text", "ARP 条目数原文"),
                ("network.mac_count_text", "MAC 条目数原文"),
            )
        },
    }
)


def check_item_display_name(
    check_item_id: str | None,
    fallback: str | None = None,
) -> str:
    """Return the localized name without changing the stable catalog identity."""

    normalized_id = str(check_item_id or "").strip()
    presentation = _ZH_CN_PRESENTATIONS.get(normalized_id)
    if presentation is not None:
        return presentation.name
    return str(fallback or normalized_id).strip()


def check_item_display_description(
    check_item_id: str | None,
    fallback: str | None = None,
) -> str:
    """Return the localized description with a catalog fallback."""

    normalized_id = str(check_item_id or "").strip()
    presentation = _ZH_CN_PRESENTATIONS.get(normalized_id)
    if presentation is not None:
        return presentation.description
    return str(fallback or "").strip()


def supported_check_item_presentations() -> tuple[str, ...]:
    """Expose covered stable IDs for catalog-governance tests."""

    return tuple(sorted(_ZH_CN_PRESENTATIONS))
