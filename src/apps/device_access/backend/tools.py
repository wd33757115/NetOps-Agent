from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict

from src.platform.tools.registry import Tool

from . import baseline_compare, diagnosis, saved, service
from .schemas import DiagnosisRun, Read


class Empty(BaseModel):
    model_config = ConfigDict(extra="forbid")


def run(user, body):
    if not user.can_execute_skills():
        raise HTTPException(403, "当前账号不能登录设备诊断")
    return diagnosis.run(
        user.user_id, body.host, body.platform, body.commands, body.purpose, body.turn_id
    )


def compare_baseline(user, body):
    if not user.can_execute_skills():
        raise HTTPException(403, "当前账号不能登录设备对比基线")
    return baseline_compare.compare(user.user_id, body)


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
        "compare_baseline",
        "看设备和之前比变了什么：每台设备登录一次，把巡检为它保存的每条基线命令"
        "（配置、接口、路由表、OSPF 邻居、ARP、MAC、VLAN、LLDP 等，以实际保存的为准）"
        "在现场原样执行一次，与基线原文逐行对比；返回每台设备的汇总和每条命令的差异行，"
        "不回传整份输出，不接受任意命令，不自动重试",
        baseline_compare.CompareBaseline,
        compare_baseline,
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
