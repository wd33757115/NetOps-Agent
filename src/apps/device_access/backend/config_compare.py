"""Compare one retained configuration block with one controlled, audited live read."""

import hashlib
import json
from ipaddress import ip_address

from pydantic import Field, field_validator

from src.platform.device_access import AccessContext, AccessGrant
from src.platform.device_access.command_policy import classify
from src.platform.device_access.config_sections import (
    Scope,
    build_command,
    compare_sections,
    extract_section,
)
from src.platform.device_access.contracts import DeviceAccessError
from src.platform.device_access.sessions import SessionBudget
from src.platform.tools import registry

from . import diagnosis, service
from .models import now
from .sessions import write_audit

MAX_RESULT_BYTES = 24000


class CompareConfig(Scope):
    snapshot_id: str = Field(
        min_length=1, max_length=64, description="从巡检命令目录选定的基线原始配置快照 ID"
    )
    host: str = Field(description="本轮路径或定位结果中的设备管理地址")
    purpose: str = Field(min_length=1, max_length=512, description="本次配置块比较的目的，写入审计")
    # Supplied by DiagnosticWorkspace, alongside the topology-derived platform.
    turn_id: str = Field(min_length=1, max_length=64)

    @field_validator("host")
    @classmethod
    def literal_address(cls, value):
        return str(ip_address(value))


def _bounded(view):
    def size():
        return len(
            json.dumps(view, ensure_ascii=False, sort_keys=True, default=str, indent=2).encode()
        )

    if size() >= MAX_RESULT_BYTES:
        # Never call omitted data an unchanged configuration. Keep source identities
        # and hashes so the full retained snapshot and audited command remain traceable.
        view["status"] = view["comparison"]["status"] = "unavailable"
        view["quality_gaps"].append("comparison_evidence_exceeds_output_budget")
        for side in (view["baseline"], view["current"]):
            if side and side.get("fragment"):
                fragment = side["fragment"]
                fragment.update(text="", lines=[], complete=False, truncated=True)
                fragment["quality_gaps"].append("comparison_evidence_exceeds_output_budget")
        view["comparison"].update(added=[], removed=[])
        view["comparison"]["quality_gaps"].append("comparison_evidence_exceeds_output_budget")
    if size() >= MAX_RESULT_BYTES:
        raise ValueError("Configuration comparison metadata exceeds evidence budget")
    return view


def compare(owner, body: CompareConfig):
    scope = Scope.model_validate(
        {key: getattr(body, key) for key in ("platform", "section", "target")}
    )
    baseline = registry.invoke(
        "inspection__read_config_section",
        {**scope.model_dump(), "snapshot_id": body.snapshot_id, "host": body.host},
        actor_id=owner,
        read_only=True,
    )
    # Validate the internal provider's identity before resolving credentials or logging in.
    if (
        baseline["baseline"]["host"] != body.host
        or baseline["baseline"]["snapshot_id"] != body.snapshot_id
        or baseline["scope"] != scope.model_dump()
        or any(baseline["fragment"].get(key) != value for key, value in scope.model_dump().items())
    ):
        raise ValueError("Baseline configuration scope does not match the requested device")
    previous = baseline["fragment"]
    view = {
        "host": body.host,
        "platform": body.platform,
        "scope": scope.model_dump(),
        "observed_at": now().isoformat(),
        "status": "unavailable",
        "baseline": {**baseline["baseline"], "fragment": previous},
        "current": None,
        "comparison": {"scope": scope.model_dump(), "status": "unavailable", "quality_gaps": []},
        "quality_gaps": list(previous.get("quality_gaps", [])),
        "meaning": (
            "仅比较指定基线与现场同一配置块的可见文本；历史基线不等于健康，配置差异不证明故障根因。"
        ),
    }
    if not previous.get("complete") or previous.get("truncated") or not previous.get("text"):
        view["quality_gaps"].append("baseline_section_unavailable_no_live_read")
        return _bounded(view)
    command = build_command(scope)
    if classify(body.platform, command).level not in {"L1", "L2"}:
        view["quality_gaps"].append("configuration_scope_command_not_readonly")
        return _bounded(view)
    ref = f"diagnosis-config:{body.turn_id}:{body.host}"
    guard = service._guard(owner, "账号不能执行现场配置块比较")
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
        result = handle.run(command, read_timeout=20)
        complete = (
            result.execution_status == "succeeded"
            and result.policy_level in {"L1", "L2"}
            and result.output_complete
            and result.failure_code is None
        )
        current = extract_section(result.output, scope, source_complete=complete, command=command)
        view["observed_at"] = result.finished_at
        view["current"] = {
            "host": body.host,
            "command": command,
            "normalized_command": result.normalized_command,
            "policy_level": result.policy_level,
            "status": result.execution_status,
            "failure_code": result.failure_code,
            "session_id": result.execution_id,
            "command_id": result.command_id,
            "evidence_ref": result.evidence_ref,
            "started_at": result.started_at,
            "observed_at": result.finished_at,
            "output_complete": result.output_complete,
            "output_sha256": hashlib.sha256(result.output.encode()).hexdigest(),
            "fragment": current,
        }
        view["comparison"] = compare_sections(previous, current)
        view["status"] = view["comparison"]["status"]
        view["quality_gaps"] = list(
            dict.fromkeys(
                view["quality_gaps"] + current["quality_gaps"] + view["comparison"]["quality_gaps"]
            )
        )
    except DeviceAccessError as exc:
        view["failure_code"] = exc.failure_code
        view["quality_gaps"].append("live_read_failed:" + exc.failure_code)
        view["notice"] = "现场读取未完成，不自动重试；请核对 SSH 会话、权限和命令白名单。"
    finally:
        if handle is not None:
            handle.close()
    return _bounded(view)
