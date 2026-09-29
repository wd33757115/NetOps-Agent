"""AI diagnosis logins: one platform-controlled, audited session per call.

The caller (the assistant's diagnostic workspace) decides which devices a turn may
reach; this module only logs in and runs commands through the platform session, so
every command is classified (L1/L2 run, L3/L4 never) and every step is audited.
Configuration dumps are not sent at all: their output can carry secrets.
"""

from sqlalchemy import select

from src.platform.device_access import AccessContext, AccessGrant
from src.platform.device_access.command_policy import classify
from src.platform.device_access.contracts import DeviceAccessError
from src.platform.device_access.sessions import SessionBudget

from . import saved, service
from .models import SavedSession, now
from .sessions import write_audit

# Normalized command prefixes whose output is a configuration dump.
CONFIG_DUMPS = {
    "comware": ("display current-configuration", "display saved-configuration", "display this"),
    "vrp": ("display current-configuration", "display saved-configuration", "display this"),
    "ios": ("show running-config", "show startup-config"),
    "nxos": ("show running-config", "show startup-config"),
}
# Five excerpts must fit the assistant's 24000-byte evidence snapshot, CJK included.
EXCERPT_BYTES = 3000
NOT_RUN = {
    "L3": "需要人工审批，本轮不执行",
    "L4": "不允许的命令（配置或危险操作），不执行",
    "config": "配置导出可能包含密文，不执行；请用针对性的 display/show 命令",
}


def login_for(owner, host):
    """The saved session for this host, else the default login. Secrets stay here."""
    with service.sessions() as session:
        rows = list(
            session.scalars(
                select(SavedSession)
                .where(SavedSession.owner_id == str(owner), SavedSession.host == host)
                .order_by(SavedSession.port != 22, SavedSession.username.is_(None))
            )
        )
        chosen = rows[0] if rows else None
        session_id, port = (chosen.id, chosen.port) if chosen else (None, 22)
    username, password = saved.login(owner, session_id)
    return port, username, password


def _dump(platform, normalized):
    return any(
        normalized == prefix or normalized.startswith(prefix + " ")
        for prefix in CONFIG_DUMPS.get(platform, ())
    )


def run(owner, host, platform, commands, purpose, turn_id):
    """Log in once, run up to five commands, close. Never retries a failed login."""
    ref = f"diagnosis:{turn_id}:{host}"
    view = {"host": host, "platform": platform, "observed_at": now().isoformat(), "commands": []}
    planned = []
    for text in commands:
        decision = classify(platform, text)
        if decision.level in {"L1", "L2"} and _dump(platform, decision.normalized):
            view["commands"].append(
                {
                    "command": text,
                    "policy_level": decision.level,
                    "status": "not_executed",
                    "reason": NOT_RUN["config"],
                }
            )
        else:
            planned.append(text)
    if not planned:
        view["login"] = "not_needed"
        return view
    guard = service._guard(owner, "账号不能执行诊断登录")
    try:
        guard()
        port, username, password = login_for(owner, host)
        access = service.access(ref, host, port, username, password)
        handle = access.open_session(
            AccessContext(
                owner, service.APP_ID, (AccessGrant(ref, ref, action="device.session"),), turn_id
            ),
            ref,
            platform=platform,
            purpose=purpose,
            budget=SessionBudget(total_seconds=70, idle_seconds=30, max_commands=5, max_probes=5),
            audit=write_audit,
            should_continue=guard,
        )
    except DeviceAccessError as exc:
        view.update(
            login="failed",
            failure_code=exc.failure_code,
            notice="登录失败，不自动重试；请在 SSH 终端检查该设备的会话或默认用户名密码。",
        )
        return view
    view["login"] = "succeeded"
    try:
        for text in planned:
            try:
                result = handle.run(text, read_timeout=20)
            except DeviceAccessError as exc:
                view["commands"].append(
                    {"command": text, "status": "failed", "failure_code": exc.failure_code}
                )
                break
            item = {
                "command": text,
                "normalized_command": result.normalized_command,
                "policy_level": result.policy_level,
                "status": result.execution_status,
                "evidence_ref": result.evidence_ref,
            }
            if result.policy_level not in {"L1", "L2"}:
                item.update(
                    status="not_executed", reason=NOT_RUN.get(result.policy_level, NOT_RUN["L4"])
                )
            else:
                item.update(
                    output=result.output.encode()[:EXCERPT_BYTES].decode(errors="ignore"),
                    output_truncated=len(result.output.encode()) > EXCERPT_BYTES,
                    output_complete=result.output_complete,
                    failure_code=result.failure_code,
                )
            view["commands"].append(item)
            if result.execution_status == "failed":
                break
    finally:
        handle.close()
    return view
