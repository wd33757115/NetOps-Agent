"""Compare devices with the baselines inspection retained for them, in one call.

For each device: the newest retained, unparsed snapshot of every command inspection
keeps for it, one platform session running exactly those commands, and a line diff
per command. Only per-command status and diff lines reach the model, never a whole
output.
"""

import contextvars
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from ipaddress import ip_address

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.platform.clock import beijing
from src.platform.device_access import AccessContext, AccessGrant, text_table
from src.platform.device_access.command_policy import classify
from src.platform.device_access.contracts import DeviceAccessError
from src.platform.device_access.sessions import SessionBudget
from src.platform.device_access.text_compare import (
    comparable_lines,
    compare_text,
    tabular,
    unusable,
)
from src.platform.tools import registry

from . import diagnosis, live_outputs, service
from .models import now
from .sessions import write_audit

MAX_HOSTS = 4
MAX_COMMANDS = 12  # per device; the lab profile keeps nine
# A table this long is not read whole live: its statistics are compared and the
# model looks rows up (compare_rows) or runs a filtered command for the part it needs.
LARGE_LINES = 5000
LISTING_PAGES = 10
# The assistant keeps one evidence snapshot of at most 24000 bytes, as indented JSON.
MAX_RESULT_BYTES = 23000
SCOPE = "只覆盖 devices 里列出的设备和命令；没列出的设备、命令没有对比，不能据此说它们没变"
MEANING = (
    "每条命令：巡检保存的基线原文与现场同一命令的输出逐行对比（+ 现场新增，- 基线有而现场没有）。"
    "基线是当时的状态，不等于健康；差异不等于根因；老化时间、计数、Dead-Time 这类列本来就会变"
)


class CompareBaseline(BaseModel):
    # The commands come from the baselines, never from the model.
    model_config = ConfigDict(extra="forbid")
    hosts: list[str] = Field(
        min_length=1,
        max_length=MAX_HOSTS,
        description=f"要对比的设备管理地址，一次最多 {MAX_HOSTS} 台",
    )
    purpose: str = Field(min_length=1, max_length=512, description="本次对比的目的，写入审计")
    # Supplied by DiagnosticWorkspace from the turn's topology answers.
    platforms: dict[str, str]
    turn_id: str = Field(min_length=1, max_length=64)

    @field_validator("hosts")
    @classmethod
    def literal_addresses(cls, value):
        return list(dict.fromkeys(str(ip_address(host)) for host in value))

    @model_validator(mode="after")
    def every_platform(self):
        if any(not self.platforms.get(host) for host in self.hosts):
            raise ValueError("Every host needs its identified platform")
        return self



def _size(view):
    return len(json.dumps(view, ensure_ascii=False, sort_keys=True, default=str, indent=2).encode())


def _baselines(owner, host):
    """The newest usable retained snapshot of each command inspection keeps for the host."""
    newest, offset = {}, 0
    for _ in range(LISTING_PAGES):
        page = registry.invoke(
            "inspection__list_snapshots",
            {"host": host, "limit": 20, "offset": offset},
            actor_id=owner,
            read_only=True,
        )
        for item in page["items"]:
            if item.get("output_status") == "data" and item.get("snapshot_kind") == "full":
                newest.setdefault(item["command"], item)
        offset = page.get("next_offset")
        if offset is None:
            break
    return list(newest.values())


def _read(owner, host, snapshot_id):
    """One baseline, or None when inspection does not hold it for this device."""
    try:
        baseline = registry.invoke(
            "inspection__read_baseline",
            {"snapshot_id": snapshot_id, "host": host},
            actor_id=owner,
            read_only=True,
        )
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        return None
    # Validate the internal provider's identity before resolving credentials or logging in.
    if baseline["baseline"]["host"] != host or baseline["baseline"]["snapshot_id"] != snapshot_id:
        raise ValueError("Baseline snapshot does not match the requested device")
    return baseline


def _compare(row, baseline_text, result, turn_id, host):
    row["evidence_ref"] = result.evidence_ref
    row["baseline_age_seconds"] = round(
        (
            datetime.fromisoformat(beijing(result.finished_at))
            - datetime.fromisoformat(row["baseline"]["observed_at"])
        ).total_seconds()
    )
    if result.failure_code:
        row["failure_code"] = result.failure_code
    if not (
        result.execution_status == "succeeded"
        and result.policy_level in {"L1", "L2"}
        and result.output_complete
        and result.failure_code is None
    ):
        row["quality_gaps"].append("live_output_incomplete_not_compared")
        return
    # Kept whole for this turn, so compare_rows can show any part without a login.
    live_outputs.keep(turn_id, host, row["command"], result.output, result.finished_at)
    for side, text in (("baseline", baseline_text), ("live", result.output)):
        if reason := unusable(text, row["command"]):
            row["quality_gaps"].append(f"{side}_{reason}_not_compared")
    if row["quality_gaps"]:
        return
    comparison = compare_text(baseline_text, result.output, command=row["command"])
    row.update(
        status=comparison["status"],
        added=comparison["added_count"],
        removed=comparison["removed_count"],
    )
    if comparison["changed_rows"]:
        row["changed"] = comparison["changed_rows"]
    if comparison["tables"]:
        row["tables"] = [
            {"summary": text_table.summary(table), **table} for table in comparison["tables"]
        ]
    if comparison["diff"]:
        row.update(diff=comparison["diff"], diff_truncated=comparison["diff_truncated"])


def _device(owner, host, platform, purpose, turn_id):
    device = {"host": host, "login": "not_needed", "commands": []}
    chosen = _baselines(owner, host)
    if not chosen:
        device["quality_gaps"] = ["no_retained_baseline"]
    if chosen[MAX_COMMANDS:]:
        device["commands_not_compared"] = [item["command"] for item in chosen[MAX_COMMANDS:]]
    planned = []
    for item in chosen[:MAX_COMMANDS]:
        baseline = _read(owner, host, item["id"])
        row = {
            "command": baseline["baseline"]["command"] if baseline else item["command"],
            "status": "unavailable",
            "baseline": {"snapshot_id": item["id"], "run_id": item.get("run_id")},
            "quality_gaps": [],
        }
        device["commands"].append(row)
        if baseline is None:
            row["quality_gaps"].append("baseline_not_held_for_device")
            continue
        row["baseline"]["observed_at"] = beijing(baseline["baseline"]["observed_at"])
        row["quality_gaps"] += baseline["quality_gaps"]
        size = baseline["text"].count("\n")
        if not baseline["complete"]:
            row["quality_gaps"].append("baseline_unavailable_no_live_read")
        elif classify(platform, row["command"]).level not in {"L1", "L2"}:
            row["quality_gaps"].append("baseline_command_not_readonly")
        elif size > LARGE_LINES and tabular(row["command"]):
            row["quality_gaps"].append("large_table_not_read_live")
            row["note"] = (
                f"基线 {size} 行，现场没有整表读取：用 device_access__compare_rows 按前缀、接口等"
                "查这张表；需要现场数据时用 device_access__run 执行带过滤条件的命令后再查"
            )
        else:
            planned.append((row, baseline["text"]))
    if not planned:
        return device
    ref = f"diagnosis-compare:{turn_id}:{host}"
    guard = service._guard(owner, "账号不能执行现场对比")
    handle = None
    try:
        guard()
        port, username, password, device_name = diagnosis.login_for(owner, host)
        access = service.access(ref, host, port, username, password, device_name=device_name)
        handle = access.open_session(
            AccessContext(
                owner,
                service.APP_ID,
                (AccessGrant(ref, ref, action="device.session"),),
                turn_id,
            ),
            ref,
            platform=platform,
            purpose=purpose,
            budget=SessionBudget(
                total_seconds=65, idle_seconds=30, max_commands=len(planned), max_probes=1
            ),
            audit=write_audit,
            should_continue=guard,
        )
        device["login"] = "succeeded"
        for row, text in planned:
            result = handle.run(row["command"], read_timeout=30)
            device.setdefault("session_id", result.execution_id)
            device["observed_at"] = beijing(result.finished_at)
            _compare(row, text, result, turn_id, host)
            if result.execution_status == "failed":
                break
    except DeviceAccessError as exc:
        if device["login"] != "succeeded":
            device["login"] = "failed"
        device["failure_code"] = exc.failure_code
        device["notice"] = "现场读取未完成，不自动重试；请核对 SSH 会话、权限和命令白名单。"
    finally:
        if handle is not None:
            handle.close()
    for row, _ in planned:
        if row["status"] == "unavailable" and not row["quality_gaps"]:
            row["quality_gaps"].append("live_read_not_done")
    return device


def _summary(device):
    rows, host = device["commands"], device["host"]
    if not rows:
        return f"{host}：没有可用的巡检基线，未对比"
    changed = [row for row in rows if row["status"] == "changed"]
    same = sum(row["status"] == "unchanged" for row in rows)
    large = sum("large_table_not_read_live" in row["quality_gaps"] for row in rows)
    text = f"{host}：对比 {len(rows)} 条基线命令，{len(changed)} 条有变化"
    if changed:
        text += "（" + "；".join(_phrase(row) for row in changed) + "）"
    text += f"，{same} 条无变化"
    if large:
        text += f"，{large} 张大表没有整表读取（可按需查）"
    if len(rows) - len(changed) - same - large:
        text += f"，{len(rows) - len(changed) - same - large} 条未能对比"
    if device["login"] == "failed":
        text += "（登录失败）"
    return text


def _phrase(row):
    """What changed in one command, in a few words."""
    parts = []
    for table in row.get("tables", []):
        parts.append("、".join(
            f"{word} {table[label]['count']}"
            for label, word in (("removed", "消失"), ("added", "新增"), ("changed", "内容变化"))
            if label in table
        ))
    if row.get("diff"):
        plus = sum(line.startswith("+") for line in row["diff"])
        minus = sum(line.startswith("-") for line in row["diff"])
        parts.append(f"文本 +{plus} -{minus} 行")
    # "；" separates commands in the device line, so parts of one command use "，".
    return f"{row['command']} " + "，".join(parts)


def _bounded(view):
    """Trim the longest text diff, then table examples; say so, never call it unchanged.

    Counts, groupings and changed columns always stay: they are the summary.
    """
    rows = [row for device in view["devices"] for row in device["commands"]]
    while _size(view) >= MAX_RESULT_BYTES:
        longest = max(rows, key=lambda row: len(row.get("diff", [])), default=None)
        if longest and longest.get("diff"):
            longest["diff"] = longest["diff"][: len(longest["diff"]) * 3 // 4]
            longest["diff_truncated"] = True
            continue
        tables = [t for row in rows for t in row.get("tables", []) if not t.get("examples_cut")]
        if not tables:
            raise ValueError("Comparison metadata exceeds evidence budget")
        widest = max(tables, key=lambda t: len(json.dumps(t, ensure_ascii=False)))
        for label in ("added", "removed", "changed"):
            widest.get(label, {}).pop("examples", None)
        widest["examples_cut"] = True
    return view


def compare(owner, body: CompareBaseline):
    def one(host):
        return _device(owner, host, body.platforms[host], body.purpose, body.turn_id)

    if len(body.hosts) == 1:
        devices = [one(body.hosts[0])]
    else:
        # Devices are independent; one after another would not fit the live timeout.
        with ThreadPoolExecutor(max_workers=len(body.hosts)) as pool:
            futures = [pool.submit(contextvars.copy_context().run, one, h) for h in body.hosts]
            devices = [future.result() for future in futures]
    return _bounded(
        {
            "observed_at": max(
                (device["observed_at"] for device in devices if device.get("observed_at")),
                default=beijing(now()),
            ),
            "summary": [_summary(device) for device in devices],
            "scope": SCOPE,
            "meaning": MEANING,
            "devices": devices,
        }
    )


ROWS_MEANING = (
    "按关键词从基线原文和本轮现场输出里找出的内容。表格条目按键对齐：only_baseline 只在基线，"
    "only_current 只在现场，changed 内容变了（列出哪几列），same 相同；配置等文本按段给出。"
    "现场一侧取自本轮已读取的输出，没有再次登录设备"
)


class CompareRows(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str = Field(description="设备管理地址")
    command: str = Field(
        min_length=1,
        max_length=256,
        description="基线里的命令，如 display ip routing-table、display current-configuration",
    )
    match: str = Field(
        min_length=1,
        max_length=128,
        description="要找的内容：前缀、地址、接口、VLAN、MAC、配置关键词等，不区分大小写",
    )
    limit: int = Field(30, ge=1, le=100, description="最多返回的表格条目数")
    # Supplied by DiagnosticWorkspace.
    turn_id: str = Field(min_length=1, max_length=64)

    @field_validator("host")
    @classmethod
    def literal_address(cls, value):
        return str(ip_address(value))


def _bounded_rows(view):
    """Fewer entries and section lines before anything else goes; say so."""
    while _size(view) >= MAX_RESULT_BYTES:
        parts = [t["entries"] for t in view["tables"] if t["entries"]] + [
            s.get("lines") or s.get("baseline") or s.get("current")
            for s in view["sections"]
        ]
        parts = [part for part in parts if part]
        if not parts:
            raise ValueError("Lookup result exceeds evidence budget")
        longest = max(parts, key=len)
        del longest[max(1, len(longest) * 3 // 4) - (len(longest) == 1):]
        view["truncated"] = True
    return view


def rows(owner, body: CompareRows):
    """Rows or configuration sections matching a keyword, baseline beside current.

    No login: the current side is this turn's live output of the command, or, for
    a large table read only filtered, any output this turn with the same header.
    """
    wanted = live_outputs.normal(body.command)
    baseline = next(
        (
            _read(owner, body.host, item["id"])
            for item in _baselines(owner, body.host)
            if live_outputs.normal(item["command"]) == wanted
        ),
        None,
    )
    live, source = live_outputs.get(body.turn_id, body.host, body.command), body.command
    tables = tabular(body.command)
    if live is None and baseline and tables:
        headers = text_table.signatures(comparable_lines(baseline["text"], body.command))
        for other, output in live_outputs.outputs(body.turn_id, body.host).items():
            if headers & text_table.signatures(comparable_lines(output[0], other)):
                live, source = output, other
                break
    if baseline is None and live is None:
        raise ValueError("这台设备没有这条命令的巡检基线，本轮也没有读取过它的现场输出")
    old = comparable_lines(baseline["text"], body.command) if baseline else []
    new = comparable_lines(live[0], source) if live else []
    view = {
        "host": body.host,
        "command": body.command,
        "match": body.match,
        "baseline": {
            "snapshot_id": baseline["baseline"]["snapshot_id"],
            "observed_at": beijing(baseline["baseline"]["observed_at"]),
        } if baseline else None,
        "current": {"command": source, "observed_at": beijing(live[1])} if live else None,
        **text_table.find(old, new, body.match, body.limit, tables=tables),
        "meaning": ROWS_MEANING,
    }
    if live is None:
        view["note"] = (
            "本轮还没有读取这条命令的现场输出：先用 device_access__compare_baseline，"
            "或用 device_access__run 执行（可带过滤条件）后再查"
        )
    return _bounded_rows(view)
