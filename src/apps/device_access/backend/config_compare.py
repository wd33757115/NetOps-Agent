"""Compare one retained, unparsed baseline with the same command read live now.

The baseline comes from inspection (a raw CommandSnapshot). The live read runs
exactly that command once through the platform session, so both sides are the
same command's output. Only the line diff reaches the model, never the whole
output: a configuration dump stays inside this App, redacted.
"""

import hashlib
import json
from ipaddress import ip_address

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.platform.device_access import AccessContext, AccessGrant
from src.platform.device_access.command_policy import classify
from src.platform.device_access.contracts import DeviceAccessError
from src.platform.device_access.sessions import SessionBudget
from src.platform.device_access.text_compare import compare_text, unusable
from src.platform.tools import registry

from . import diagnosis, service
from .models import now
from .sessions import write_audit

MAX_RESULT_BYTES = 24000


class CompareConfig(BaseModel):
    # The command comes from the baseline, never from the model.
    model_config = ConfigDict(extra="forbid")
    snapshot_id: str = Field(
        min_length=1, max_length=64, description="从巡检命令目录（list_snapshots）选定的基线快照 ID"
    )
    host: str = Field(description="本轮路径或定位结果中的设备管理地址")
    purpose: str = Field(min_length=1, max_length=512, description="本次对比的目的，写入审计")
    # Supplied by DiagnosticWorkspace from the turn's topology answers.
    platform: str = Field(min_length=1, max_length=32)
    turn_id: str = Field(min_length=1, max_length=64)

    @field_validator("host")
    @classmethod
    def literal_address(cls, value):
        return str(ip_address(value))


def _size(view):
    return len(json.dumps(view, ensure_ascii=False, sort_keys=True, default=str, indent=2).encode())


def _bounded(view):
    comparison = view.get("comparison") or {}
    while comparison.get("diff") and _size(view) >= MAX_RESULT_BYTES:
        # Keep the head of the diff; say so, never call the rest unchanged.
        comparison["diff"] = comparison["diff"][: len(comparison["diff"]) * 3 // 4]
        comparison["diff_truncated"] = True
    if _size(view) >= MAX_RESULT_BYTES:
        raise ValueError("Comparison metadata exceeds evidence budget")
    return view


def compare(owner, body: CompareConfig):
    baseline = registry.invoke(
        "inspection__read_baseline",
        {"snapshot_id": body.snapshot_id, "host": body.host},
        actor_id=owner,
        read_only=True,
    )
    # Validate the internal provider's identity before resolving credentials or logging in.
    if (
        baseline["baseline"]["host"] != body.host
        or baseline["baseline"]["snapshot_id"] != body.snapshot_id
    ):
        raise ValueError("Baseline snapshot does not match the requested device")
    command = baseline["baseline"]["command"]
    view = {
        "host": body.host,
        "command": command,
        "observed_at": now().isoformat(),
        "status": "unavailable",
        "baseline": baseline["baseline"],
        "current": None,
        "comparison": None,
        "quality_gaps": list(baseline["quality_gaps"]),
        "meaning": "同一条命令的基线原文与现场输出逐行对比；基线不等于健康，差异不证明根因。",
    }
    if not baseline["complete"]:
        view["quality_gaps"].append("baseline_unavailable_no_live_read")
        return _bounded(view)
    if classify(body.platform, command).level not in {"L1", "L2"}:
        view["quality_gaps"].append("baseline_command_not_readonly")
        return _bounded(view)
    ref = f"diagnosis-compare:{body.turn_id}:{body.host}"
    guard = service._guard(owner, "账号不能执行现场对比")
    handle = None
    try:
        guard()
        port, username, password, device_name = diagnosis.login_for(owner, body.host)
        access = service.access(ref, body.host, port, username, password, device_name=device_name)
        handle = access.open_session(
            AccessContext(
                owner,
                service.APP_ID,
                (AccessGrant(ref, ref, action="device.session"),),
                body.turn_id,
            ),
            ref,
            platform=body.platform,
            purpose=body.purpose,
            budget=SessionBudget(total_seconds=70, idle_seconds=30, max_commands=1, max_probes=1),
            audit=write_audit,
            should_continue=guard,
        )
        result = handle.run(command, read_timeout=30)
        complete = (
            result.execution_status == "succeeded"
            and result.policy_level in {"L1", "L2"}
            and result.output_complete
            and result.failure_code is None
        )
        view["observed_at"] = result.finished_at
        view["current"] = {
            "command": command,
            "policy_level": result.policy_level,
            "status": result.execution_status,
            "failure_code": result.failure_code,
            "session_id": result.execution_id,
            "command_id": result.command_id,
            "evidence_ref": result.evidence_ref,
            "observed_at": result.finished_at,
            "output_complete": result.output_complete,
            "output_sha256": hashlib.sha256(result.output.encode()).hexdigest(),
        }
        if not complete:
            view["quality_gaps"].append("live_output_incomplete_not_compared")
            return _bounded(view)
        for side, text in (("baseline", baseline["text"]), ("live", result.output)):
            if reason := unusable(text, command):
                view["quality_gaps"].append(f"{side}_{reason}_not_compared")
        if view["quality_gaps"]:
            return _bounded(view)
        view["comparison"] = compare_text(baseline["text"], result.output, command=command)
        view["status"] = view["comparison"]["status"]
        view["quality_gaps"] += view["comparison"]["quality_gaps"]
    except DeviceAccessError as exc:
        view["failure_code"] = exc.failure_code
        view["quality_gaps"].append("live_read_failed:" + exc.failure_code)
        view["notice"] = "现场读取未完成，不自动重试；请核对 SSH 会话、权限和命令白名单。"
    finally:
        if handle is not None:
            handle.close()
    return _bounded(view)
