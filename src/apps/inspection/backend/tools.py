"""Agent uses the same fixed inspection admission and durable read model."""

from pydantic import BaseModel, ConfigDict

from src.core.patrol.batch_manifest import PatrolBatchManifestRepository
from src.platform.inventory.api import DevicePatrolLaunchRequest
from src.platform.tools.registry import Tool

from .config_baseline import ReadBaseline, read_baseline
from .diagnostics import TOOLS as DIAGNOSTIC_TOOLS
from .launch import launch_patrol
from .patrol_batch_service import build_patrol_batch_summary


class ReadBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    batch_id: str


def read_batch(user, body):
    runs = PatrolBatchManifestRepository().workflow_runs(body.batch_id)
    if runs is None:
        raise KeyError("巡检批次不存在")
    return build_patrol_batch_summary(body.batch_id, runs)


TOOLS = [*DIAGNOSTIC_TOOLS,
    Tool(
        "read_baseline", "内部读取经目标资产校验的未解析基线原文",
        ReadBaseline, read_baseline,
    ),
    Tool(
        "launch",
        "按已发布检查项和设备范围启动固定巡检，返回批次编号",
        DevicePatrolLaunchRequest,
        lambda user, body: launch_patrol(body, actor=user.user_id),
        True,
    ),
    Tool("read_batch", "读取巡检批次、阶段、证据与结果", ReadBatch, read_batch),
]
