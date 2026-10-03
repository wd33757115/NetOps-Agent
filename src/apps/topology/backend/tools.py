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
        "complete 表示这套推导所需的观测证据齐备，不是数据面实测成功；"
        "未计算全部 ACL、策略路由和二层转发规则。"
        "现场不一致也可能源于覆盖或推导限制；只读，不登录设备。",
        Trace,
        lambda user, body: service.trace_current(user.user_id, body.src, body.dst),
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
