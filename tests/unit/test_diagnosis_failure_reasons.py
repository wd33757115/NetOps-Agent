import pytest
from fastapi import HTTPException
from pydantic import BaseModel, ValidationError

from src.apps.device_access.backend.diagnosis import why_not_run
from src.platform.agent_runtime.dsh import UNAVAILABLE, tool_failure
from src.platform.device_access.contracts import DeviceAccessError


@pytest.mark.parametrize(
    "platform,command,expected",
    [
        # The run that failed wrote the option after the target: correctable, not "approval".
        ("comware", "ping -c 3 192.168.1.2 -a 192.168.1.1", "ping -a 源地址 -c 3 目标地址"),
        ("ios", "ping source 10.0.0.1 10.0.0.2", "ping 目标地址 source 源地址 repeat 3"),
        ("comware", "ping -a Vlan-interface200 192.168.1.2", "不接受接口名"),
        ("comware", "ping -c 50 192.168.1.2", "次数不超过 5"),
    ],
)
def test_a_refused_probe_says_how_to_write_it(platform, command, expected):
    reason = why_not_run(platform, command, "L3")
    assert expected in reason and "人工审批" not in reason


def test_true_approvals_and_unknown_codes_still_say_so():
    assert "人工审批" in why_not_run("comware", "display frobnicate", "L3")
    assert why_not_run("comware", "reset counters interface", "L4") == "配置或变更类命令，不执行"
    # A code without its own wording falls back to the level, and names the code.
    assert why_not_run("comware", " ", "L4").endswith("（empty_command）")


def test_a_command_of_the_other_platform_names_the_device_platform():
    # A model once sent ten Comware "display" commands to IOS devices.
    assert "Cisco IOS，查看类命令用 show" in why_not_run("ios", "display ip interface brief", "L3")
    assert "H3C Comware，查看类命令用 display" in why_not_run("comware", "show ip route", "L3")


class Arguments(BaseModel):
    host: str


@pytest.mark.parametrize(
    "error,expected",
    [
        (PermissionError("本轮诊断命令预算已用完"), "被拒绝：本轮诊断命令预算已用完"),
        (TimeoutError(), "工具调用超时（75 秒）"),
        (HTTPException(404, "基线快照不存在"), "查询失败（404）：基线快照不存在"),
        (
            DeviceAccessError("authentication_failed", "login failed"),
            "设备访问失败：authentication_failed",
        ),
        (ValueError("Business evidence exceeds budget; narrow"), "缩小查询范围"),
    ],
)
def test_known_failures_reach_the_model_with_their_reason(error, expected):
    text = tool_failure(error, 75)
    assert expected in text and text.endswith("不要据此推断正常或故障")


def test_bad_arguments_name_the_field():
    with pytest.raises(ValidationError) as caught:
        Arguments.model_validate({})
    assert "host" in tool_failure(caught.value, 10)


def test_unknown_errors_stay_opaque():
    assert tool_failure(RuntimeError("postgresql://user:secret@db"), 10) == UNAVAILABLE
    assert tool_failure(ValueError("internal detail"), 10) == UNAVAILABLE
