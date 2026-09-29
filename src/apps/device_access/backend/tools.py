from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict

from src.platform.tools.registry import Tool

from . import saved, service
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
        "主动采集：只能使用人工选定的已保存会话和固定诊断命令集；失败需人工处理，不自动重试",
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
        "list_sessions",
        "列出本人已保存的 SSH 会话名称和设备地址，不返回用户名密码",
        Empty,
        lambda user, body: [
            {key: row[key] for key in ("id", "name", "host", "port")}
            for row in saved.list_sessions(user.user_id)
        ],
        evidence=True,
    ),
]
