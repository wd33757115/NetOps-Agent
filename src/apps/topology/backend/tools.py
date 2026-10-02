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
        "终端定位：在本人各网络的当前快照中查 IP 或 MAC 接在哪台设备哪个端口、VLAN，"
        "以及该网段在路由器上的三层接口（router_interfaces）；VRRP 虚拟地址返回各成员的主备状态；"
        "终端自己配置的网关、掩码看不到，"
        "不能当作已证实；附依据与观测时间；只读，不登录设备，查不到即返回空，不代表终端不存在",
        Locate,
        lambda user, body: service.locate_current(user.user_id, body.query),
        evidence=True,
    ),
    Tool(
        "trace_path",
        "之前的流量路径：用最近一次拓扑发现时采集的路由表，算出 src→dst 与 dst→src "
        "当时经过哪些设备、入/出接口和命中的路由，附采集时间；网段有 VRRP 时第一跳取当时的主设备；"
        "数据缺口处停止并注明原因"
        "（无路由、黑洞、下一跳未纳管、下一跳无法解析、未采集路由表、环路）；只读，不登录设备。"
        "它不是现场转发状态：现场与它不同，说明之后发生了变化",
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
