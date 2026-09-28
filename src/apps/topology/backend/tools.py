from pydantic import BaseModel, ConfigDict

from src.platform.tools.registry import Tool

from . import service


class Read(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observation_id: str


TOOLS = [
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
