"""A policy request sheet drafted from the resource ledger.

The ledger is asked through the platform tool registry, as the requesting user and under
that user's grant of the resource-ledger App, so this App never reads another App's tables.
The sheet is the downloadable request template with the rows filled in, so the rest of the
pipeline treats it exactly like a request a person filled in.
"""

from .engine.policy.scripts import request_template

XLSX = request_template.XLSX

PLAN_TOOL = "resource_ledger__plan_access"


def plan(user, body):
    """Request rows from the ledger; a missing ledger or grant reads as a plain reason."""
    from fastapi import HTTPException

    from src.platform.tools.registry import catalog, invoke

    if PLAN_TOOL not in catalog():
        raise RuntimeError("按台账生成申请表需要先安装并启用“资源台账”应用")
    arguments = {key: getattr(body, key) for key in (
        "source", "destination", "source_subsystem", "destination_subsystem", "ports") if getattr(body, key)}
    try:
        return invoke(PLAN_TOOL, arguments, actor_id=user.user_id, read_only=True)
    except HTTPException as exc:
        if exc.status_code in (401, 403):
            raise RuntimeError("当前账号没有“资源台账”应用的使用权限，无法按台账生成申请表") from None
        raise RuntimeError(f"资源台账：{exc.detail}") from None


def available(user):
    """Whether this user can draft from the ledger: the App is installed and granted to them."""
    from src.platform.tools.registry import available_tools

    return bool(available_tools(actor_id=user.user_id, read_only=True, names={PLAN_TOOL}))


def build(user, body):
    """Rows, notes and the request sheet for one source → destination access."""
    result = plan(user, body)
    rows, notes = result["rows"], result["notes"]
    if not rows:
        raise RuntimeError("台账里没有可开通的访问关系" + (f"：{'；'.join(notes)}" if notes else ""))
    note = f"由资源台账按“{body.source}”到“{body.destination}”生成" + (f"；{'；'.join(notes)}" if notes else "")
    data = workbook(rows, start_time=body.start_time, end_time=body.end_time, long_link=body.long_link, note=note)
    return rows, notes, data


def workbook(rows, *, start_time=None, end_time=None, long_link=None, note=""):
    return request_template.workbook(
        [
            {
                "源业务系统": row["source_system"],
                "src_ip": row["src_ip"],
                "目的业务系统": row["destination_system"],
                "dst_ip": row["dst_ip"],
                "port": row["port"],
                "proto": row["proto"],
                "start_time": start_time,
                "end_time": end_time,
                "action": "permit",
                "long_link": long_link if row["proto"] == "tcp" else None,
            }
            for row in rows
        ],
        note=note or None,
    )
