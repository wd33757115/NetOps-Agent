from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict

from src.platform.tools.registry import Tool

from . import service
from .schemas import Collect, Read


class Empty(BaseModel):
    model_config = ConfigDict(extra="forbid")


def collect(user, body):
    if not user.can_execute_skills():
        raise HTTPException(403, "当前账号不能采集设备")
    return tool_view(service.collect(user.user_id, body))


def tool_view(result):
    import copy
    import hashlib

    result = copy.deepcopy(result)
    for command in result.get("result", {}).get("commands", []):
        raw = command.get("output", "").encode()
        command["output_sha256"] = hashlib.sha256(raw).hexdigest()
        command["output_excerpt_complete"] = len(raw) <= 6000
        command["output"] = raw[:6000].decode(errors="ignore")
    result["observed_at"] = result.get("result", {}).get("observed_at")
    return result


TOOLS = [
    Tool(
        "collect",
        "主动采集：只能使用人工创建、尚未过期的连接和固定诊断命令集；失败需人工处理，不自动重试",
        Collect,
        collect,
        mutates=True,
    ),
    Tool(
        "read_operation",
        "查询本人设备采集原始证据、时间和失败状态",
        Read,
        lambda user, body: tool_view(service.read(user.user_id, body.operation_id)),
        evidence=True,
    ),
    Tool(
        "list_connections",
        "列出人工授权的有效诊断连接、设备地址和允许的命令集，不返回凭据",
        Empty,
        lambda user, body: service.list_connections(user.user_id),
        evidence=True,
    ),
]
