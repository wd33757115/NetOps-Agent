"""AI diagnosis logins: one platform-controlled, audited session per call.

The caller (the assistant's diagnostic workspace) decides which devices a turn may
reach; this module only logs in and runs commands through the platform session, so
every command is classified (L1/L2 run, L3/L4 never) and every step is audited.
Each full output stays for this turn (live_outputs): the result carries an excerpt,
and compare_rows finds the rows or configuration sections the model asks for.
"""

from sqlalchemy import select

from src.platform.device_access import AccessContext, AccessGrant
from src.platform.device_access.command_policy import classify
from src.platform.device_access.contracts import DeviceAccessError
from src.platform.device_access.sessions import SessionBudget

from . import live_outputs, saved, service
from .models import SavedSession, now
from .sessions import write_audit

# Five excerpts must fit the assistant's 24000-byte evidence snapshot, CJK included.
EXCERPT_BYTES = 3000
NOT_RUN = {
    "L3": "需要人工审批，本轮不执行",
    "L4": "不允许的命令（配置或危险操作），不执行",
}
EXCERPT_NOTE = (
    "输出较长，这里是开头部分；完整输出已保留到本轮，可用 device_access__compare_rows 按关键词查"
)
# How each platform writes a bounded probe with a source address.
PROBE_EXAMPLE = {
    "comware": "ping -a 源地址 -c 3 目标地址（选项写在目标地址之前）",
    "vrp": "ping -a 源地址 -c 3 目标地址（选项写在目标地址之前）",
    "ios": "ping 目标地址 source 源地址 repeat 3（选项写在目标地址之后）",
    "nxos": "ping 目标地址 source 源地址 count 3（选项写在目标地址之后）",
}
# What a show command starts with on each platform; a model once sent ten Comware
# "display" commands to IOS devices before reading their platform.
PLATFORM_NAMES = {"comware": "H3C Comware", "vrp": "Huawei VRP", "ios": "Cisco IOS", "nxos": "Cisco NX-OS"}
SHOW_VERBS = {"comware": "display", "vrp": "display", "ios": "show", "nxos": "show"}
# Refusals the model can correct are said plainly; "approval" was once reported
# for a ping whose option was merely out of place, and the model gave up on it.
REASONS = {
    "unsupported_probe_option": (
        "ping/tracert 选项不支持或位置不对，改写后可直接执行；写法：{example}"
    ),
    "unsupported_probe_target": "ping/tracert 的目标须是单个 IPv4 地址；写法：{example}",
    "unsupported_probe_source": "源地址须是 IPv4 单播地址（不接受接口名）；写法：{example}",
    "duplicate_or_missing_probe_option": "ping/tracert 选项重复或缺少取值；写法：{example}",
    "probe_budget_exceeded_or_invalid": (
        "次数、超时或包大小超出上限：次数不超过 5，超时不超过 2000 ms"
    ),
    "probe_filters_require_review": "探测命令不能加管道过滤，去掉过滤后可直接执行",
    "unknown_command_requires_review": (
        "不在只读命令库中，需要人工审批；该设备是 {name}，查看类命令用 {verb}"
    ),
    "configuration_or_mutation": "配置或变更类命令，不执行",
    "unsafe_pipe": "不允许的管道用法，去掉后重试",
    "control_operator_or_redirection": "不允许的控制符或重定向",
}


def why_not_run(platform, text, level):
    """Why a command was not sent, in words the model can act on."""
    reason = classify(platform, text).reason
    message = REASONS.get(reason)
    if message:
        return message.format(
            example=PROBE_EXAMPLE.get(platform, "见设备手册"),
            name=PLATFORM_NAMES.get(platform, platform),
            verb=SHOW_VERBS.get(platform, "display/show"),
        )
    return NOT_RUN.get(level, NOT_RUN["L4"]) + f"（{reason}）"


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
        device_name = chosen.name if chosen else host
    username, password = saved.login(owner, session_id)
    return port, username, password, device_name


def run(owner, host, platform, commands, purpose, turn_id):
    """Log in once, run up to five commands, close. Never retries a failed login."""
    ref = f"diagnosis:{turn_id}:{host}"
    view = {"host": host, "platform": platform, "observed_at": now().isoformat(), "commands": []}
    planned = list(commands)
    guard = service._guard(owner, "账号不能执行诊断登录")
    try:
        guard()
        port, username, password, device_name = login_for(owner, host)
        access = service.access(ref, host, port, username, password, device_name=device_name)
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
                    status="not_executed",
                    reason=why_not_run(platform, text, result.policy_level),
                )
            else:
                truncated = len(result.output.encode()) > EXCERPT_BYTES
                item.update(
                    output=result.output.encode()[:EXCERPT_BYTES].decode(errors="ignore"),
                    output_truncated=truncated,
                    output_complete=result.output_complete,
                    failure_code=result.failure_code,
                )
                if result.output_complete and result.failure_code is None:
                    live_outputs.keep(turn_id, host, text, result.output, result.finished_at)
                    if truncated:
                        item["note"] = EXCERPT_NOTE
            view["commands"].append(item)
            if result.execution_status == "failed":
                break
    finally:
        handle.close()
    return view
