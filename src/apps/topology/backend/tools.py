from pydantic import BaseModel, ConfigDict, Field

from src.platform.tools.registry import Tool

from . import service


class Read(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str


class Locate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=64, description="IP 地址或 MAC 地址")


class Trace(BaseModel):
    model_config = ConfigDict(extra="forbid")
    src: str = Field(min_length=1, max_length=64, description="源 IP 地址")
    dst: str = Field(min_length=1, max_length=64, description="目的 IP 地址")
    src_vrf: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description=(
            "源所在的网络实例（VRF，在其第一跳设备上的本地名称）；地址在多个 VPN 中重复时指定"
        ),
    )
    dst_vrf: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description="目的所在的网络实例（VRF，在其第一跳设备上的本地名称），用于反方向",
    )
    protocol: str | None = Field(
        default=None,
        min_length=1,
        max_length=8,
        description="协议：tcp、udp、icmp 或协议号；不填时 ACL 中看协议的规则判断不了",
    )
    src_port: int | None = Field(
        default=None, ge=1, le=65535, description="源端口（仅 TCP/UDP）；不填时看源端口的规则判断不了"
    )
    dst_port: int | None = Field(
        default=None, ge=1, le=65535, description="目的端口（仅 TCP/UDP），如 80、443"
    )


class Services(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observation_id: str


TOOLS = [
    Tool(
        "locate",
        "端点定位：在本人各网络的当前快照中查询 IP 或 MAC 的设备身份候选、观察端口和 VLAN；"
        "端口观察不等于物理直连，同一地址跨设备/本地网络实例或 MAC 跨 VLAN 有冲突时保留候选，"
        "以及该网段在路由器上的三层接口（router_interfaces）；VRRP 虚拟地址返回各成员的主备状态；"
        "终端自己配置的网关、掩码看不到，"
        "不能当作已证实；附依据与观测时间；只读，不登录设备，查不到即返回空，不代表终端不存在",
        Locate,
        lambda user, body: service.locate_current(user.user_id, body.query),
        evidence=True,
    ),
    Tool(
        "trace_path",
        "候选转发路径推导：用当前快照的设备本地网络实例、路由、邻接和接口状态，"
        "推导 src→dst 与 dst→src 的候选设备、入/出接口和命中路由，附采集时间；"
        "VRRP 主设备仅作为第一跳候选依据，终端自身网关配置未观察。"
        "缺少下一跳邻接、终点 ARP、来源接入或唯一身份时返回 partial 和具体缺口；"
        "仅同前缀不能证明二层可达，接口不可用则终止该分支。"
        "经 MPLS VPN 或 VXLAN 三层 VNI 的路由按隧道端点在底层转发，"
        "在对端按 RT 或 L3VNI 进入接收实例；"
        "MPLS 每跳需观察到到端点的标签，VXLAN 入口需观察到隧道 Up。"
        "同一地址在多个 VPN 中重复时，用 src_vrf/dst_vrf 指定实例，否则各实例都作为候选。"
        "complete 表示这套推导所需的观测证据齐备，不是数据面实测成功；"
        "每跳按设备自己的 ACL 规则检查入接口和出接口上绑定的 IPv4 ACL：被拒绝时该路径以 "
        "denied_in/denied_out 结束并给出 ACL 名和规则序号（default 表示末尾的默认动作），"
        "所有路径都被拒绝时该方向为 blocked；规则要看的字段没给（如端口）或有判断不了的条件时"
        "记 filter_undetermined 并写明缺什么，此时补上 protocol/dst_port 再查；"
        "反方向按应答报文检查（TCP 带 ACK，ICMP 为 echo-reply）；接口在安全域里时记 "
        "security_policy_not_evaluated，域间策略需到设备上看；隧道内（MPLS/VXLAN）不检查核心 ACL；"
        "跳上 not_observed 列出该设备没完整采到的 ACL/绑定/安全域/策略路由/NAT 表，不等于没有。"
        "入接口绑定策略路由（policy_may_redirect）或入/出接口绑定 NAT（nat_may_translate）"
        "的跳带缺口、该方向为 partial，缺口给出设备、接口和策略名或 NAT 方式："
        "路径仍是路由表推导的，策略内容未计算，需到该设备查看该策略是否匹配这条流量、"
        "把它改到哪里或改写成什么地址；"
        "未检查二层设备上的 ACL 和二层转发规则。"
        "现场不一致也可能源于覆盖或推导限制；只读，不登录设备。",
        Trace,
        lambda user, body: service.trace_current(
            user.user_id,
            body.src,
            body.dst,
            body.src_vrf,
            body.dst_vrf,
            flow={"protocol": body.protocol, "src_port": body.src_port, "dst_port": body.dst_port},
        ),
        evidence=True,
    ),
    Tool(
        "vpn_services",
        "VPN 服务视图：从各网络当前快照读出三层 VPN（RFC 9182 L3NM）与 EVPN 二层服务"
        "（RFC 9291 L2NM）。导出同一 RT 的设备本地实例（VRF/EVI）归为一个服务；"
        "只靠导入关联的（共享服务泄漏、hub-spoke）是不同服务，在 relations 中列出谁导入了谁的 RT。"
        "每个成员给出设备、本地实例名、RD/RT、接入接口及其地址和状态；"
        "承载（LDP、VXLAN）只在所有成员都有证据时给出，未证明的写入 gaps。"
        "这是由设备配置和状态推出的网络层服务，不是客户订单或服务合同；"
        "vpn-id 由平台按最小导出 RT 分配；只读，不登录设备。",
        Services,
        lambda user, body: service.services_current(user.user_id),
        evidence=True,
    ),
    Tool(
        "read_run",
        "查询本人拓扑任务、带时间和候选身份标记的观测图；不是仿真或实时状态",
        Read,
        lambda user, body: service.read(user.user_id, body.run_id),
        evidence=True,
    ),
    Tool(
        "read_evidence",
        "查询本人拓扑观测的原始命令证据",
        Evidence,
        lambda user, body: service.evidence(user.user_id, body.observation_id),
        evidence=True,
    ),
]
