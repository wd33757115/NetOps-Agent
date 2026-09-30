from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict

from src.platform.tools.registry import Tool

from . import config_compare, diagnosis, saved, service
from .schemas import DiagnosisRun, Read


class Empty(BaseModel):
    model_config = ConfigDict(extra="forbid")


def run(user, body):
    if not user.can_execute_skills():
        raise HTTPException(403, "当前账号不能登录设备诊断")
    return diagnosis.run(
        user.user_id, body.host, body.platform, body.commands, body.purpose, body.turn_id
    )


def compare_config(user, body):
    if not user.can_execute_skills():
        raise HTTPException(403, "当前账号不能登录设备比较配置")
    return config_compare.compare(user.user_id, body)


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
        "compare_config",
        "将巡检保存的一份未解析基线原文与现场同一条命令的输出逐行文本对比；"
        "先验证基线设备和完整性，再在设备上原样执行该快照的命令一次，不接受任意命令；"
        "只返回差异行和证据缺口，不回传整份输出，不自动重试",
        config_compare.CompareConfig,
        compare_config,
        mutates=True,
    ),
    Tool(
        "run",
        "登录本轮路径计算或终端定位结果中出现的设备，执行只读 display/show 或有限 ping/tracert，"
        "一次最多 5 条；配置导出、需审批和配置命令不会执行；每步写入审计，登录失败不自动重试",
        DiagnosisRun,
        run,
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
