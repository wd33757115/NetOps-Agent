from pydantic import BaseModel, ConfigDict, Field

from src.platform.tools.registry import Tool

from . import service


class Read(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str


class Locate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=64, description="IP 地址或 MAC 地址")


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observation_id: str


TOOLS = [
    Tool(
        "locate",
        "终端定位：在本人各网络的当前快照中查 IP 或 MAC 接在哪台设备哪个端口、VLAN 和网关，"
        "附依据与观测时间；只读，不登录设备，查不到即返回空，不代表终端不存在",
        Locate,
        lambda user, body: service.locate_current(user.user_id, body.query),
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
