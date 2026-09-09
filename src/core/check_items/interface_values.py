"""Operator-approved interface state/configuration/counter Value contract.

References: supplied Huawei X2E display-interface documentation and local NTC
hp_comware/huawei_vrp display_interface templates. CLI values remain source-faithful.
"""

from src.core.check_items.catalog import build_check_item_definition, load_catalog_defaults

ITEM_ID = "interface.runtime_statistics"
FIELDS = {
    "line_status": ("state", None, "接口物理状态，保留管理关闭及故障原因等完整原文"),
    "protocol_status": ("state", None, "接口链路协议状态，保留括号中的状态说明"),
    "description": ("text", None, "接口描述，空字符串也是有效描述"),
    "hw_address": ("text", None, "接口硬件 MAC 地址"),
    "bandwidth_kbps": ("gauge", "kbps", "接口原文 Bandwidth 配置带宽，单位 kbps，不等于当前吞吐量"),
    "speed": ("text", None, "接口当前速度原文，保留原文单位或厂商显示格式"),
    "duplex": ("state", None, "接口双工模式原文"),
    "port_link_type": ("state", None, "接口链路类型，保留 configured/negotiated 等原文"),
    "vlan_native": ("gauge", None, "接口缺省 VLAN/PVID 编号"),
    "mtu": ("gauge", "bytes", "接口三层最大传输单元，不与二层最大帧长混用"),
    "l2mtu": ("gauge", "bytes", "接口二层最大帧长度，不与三层 MTU 混用"),
    "input_packets": ("counter", "count", "接口累计接收报文数"),
    "output_packets": ("counter", "count", "接口累计发送报文数"),
    "input_bytes": ("counter", "bytes", "接口累计接收字节数"),
    "output_bytes": ("counter", "bytes", "接口累计发送字节数"),
    "input_errors": (
        "counter",
        "count",
        "接口接收错误总数；Huawei Input 下的 Total Error；没有输出则缺失",
    ),
    "output_errors": ("counter", "count", "接口发送错误总数；只取输出方向明确的错误总数"),
    "input_crc_errors": ("counter", "count", "接收方向 CRC/FCS 错误计数，保留厂商计数口径"),
    "last_physical_up_time": ("text", None, "最近物理 Up 时间，原文 - 表示未记录"),
    "last_physical_down_time": ("text", None, "最近物理 Down 时间，原文 - 表示未记录"),
    "negotiation": ("state", None, "接口自协商状态原文"),
    "port_mode": ("state", None, "接口介质工作模式原文"),
    "loopback": ("state", None, "接口环回模式原文"),
}


def definition():
    observations = [
        {
            "canonical_field": field,
            "observation_type": f"{ITEM_ID}.{field}",
            "semantic_type": kind,
            "value_type": "integer" if kind in {"counter", "gauge"} else "text",
            "unit": unit,
            "description": description,
            "required": field in {"line_status", "protocol_status"},
        }
        for field, (kind, unit, description) in FIELDS.items()
    ]
    return build_check_item_definition(
        {
            "id": ITEM_ID,
            "version": 1,
            "name": "接口状态运行信息和统计信息",
            "description": ("接口状态、基本配置及收发统计；每个可用 Value 的变化均产生信息事件，"
                            "变化不等于故障。"),
            "category": "interface",
            "result_mode": "structured",
            "assessment_mode": "change",
            "status": "active",
            "report_projection": "auto@1",
            "canonical_contract": {
                "entity_type": "interface",
                "identity_dimensions": ["interface"],
                "value_contract": "same-name/v1",
                "change_events": True,
                "observations": observations,
            },
            "coverage_policy": {"model": "non_exhaustive", "require_rule_evaluation": True},
        },
        defaults=load_catalog_defaults()["check_item"],
    )
