"""Workflow actions: the ITSM firewall workflows call the same service as the page.

Each action submits an ordinary run of this App for the Workflow's user, waits for it and
returns its files; the platform stores them as the step's artifacts. Request ids derive from
the Workflow run, so a redelivered step finds its run again and a retried Workflow (a new
run) starts a new one.
"""

import time

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from src.platform import files
from src.platform.tools.actions import Action

from . import request_draft, service, storage
from .schemas import LedgerDraft, PolicyRequest, TicketFields, TicketRequest

DONE = {"completed", "failed", "cancelled"}
POLICY_SECONDS = 360  # computation is capped at 300 s, plus queueing
TICKET_SECONDS = 120
POLL_SECONDS = 2


class GeneratePolicy(BaseModel):
    # Steps also receive workflow_run_id and trace ids; only these fields are read.
    model_config = ConfigDict(extra="ignore")
    ticket_id: str = Field(min_length=1, max_length=80)
    policy_file_url: str = Field(min_length=1, max_length=2000)
    topology_file_url: str | None = Field(default=None, max_length=2000)
    merge_policies: StrictBool = True
    include_business_paths: StrictBool = False

    @field_validator("topology_file_url", mode="before")
    @classmethod
    def blank_is_none(cls, value):
        return value or None


class DraftRequest(LedgerDraft):
    model_config = ConfigDict(extra="ignore")
    ticket_id: str = Field(min_length=1, max_length=80)


class WriteTicket(BaseModel):
    model_config = ConfigDict(extra="ignore")
    policy_run_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    ticket_title: str | None = None
    change_background: str | None = None
    change_purpose: str | None = None
    requester: str | None = None
    requester_dept: str | None = None
    assignee: str | None = None
    due_date: str | None = None
    technical_reviewer: str | None = None
    reviewer: str | None = None


def fetch(value, kind, fallback_name, user):
    """The file a step names: a netdisk file read as the Workflow's user, or an http(s) URL."""
    name, data = files.fetch(value, user, limit=storage.MAX_UPLOAD)
    suffix = ".xlsx" if kind == "policy" else ".json"
    return (name if name and name.lower().endswith(suffix) else fallback_name), data


def wait(owner, run_id, seconds):
    deadline = time.monotonic() + seconds
    while True:
        run = service.read(owner, run_id)
        if run["status"] in DONE or time.monotonic() >= deadline:
            return run
        time.sleep(POLL_SECONDS)


def outcome(owner, run, seconds, label):
    """A finished run as an action result; files are read back through the owner's access."""
    if run["status"] == "completed":
        files = {}
        for key in run["artifacts"]:
            data, filename, content_type = service.download(owner, run["id"], key)
            files[key] = (filename, content_type, data)
        return files
    if run["status"] in DONE:
        raise RuntimeError(f"{label}{'已取消' if run['status'] == 'cancelled' else '失败'}：{run['error'] or '未知原因'}")
    raise RuntimeError(f"{label}未在 {seconds} 秒内完成，可在防火墙策略应用中查看任务 {run['id']}")


def upload(user, value, kind, fallback_name, request_id):
    try:
        filename, data = fetch(value, kind, fallback_name, user)
        return service.save_upload(user.user_id, filename, kind, data, request_id)["id"]
    except HTTPException as exc:
        raise ValueError(f"{'策略申请表' if kind == 'policy' else '拓扑文件'}无效：{exc.detail}") from None


def generate_policy(user, body, *, run_id):
    owner = user.user_id
    request_id = f"wf-{run_id}-policy"
    policy = upload(user, body.policy_file_url, "policy", f"{body.ticket_id}-策略申请表.xlsx",
                    f"{request_id}-file")
    topology = (
        upload(user, body.topology_file_url, "topology", f"{body.ticket_id}-拓扑.json",
               f"wf-{run_id}-topology-file")
        if body.topology_file_url else None
    )
    submitted = service.submit_policy(owner, PolicyRequest(
        request_id=request_id,
        policy_upload_id=policy,
        topology_upload_id=topology,
        ticket_id=body.ticket_id,
        merge_policies=body.merge_policies,
        include_business_paths=body.include_business_paths,
    ))
    run = wait(owner, submitted["id"], POLICY_SECONDS)
    files = outcome(owner, run, POLICY_SECONDS, "策略生成")
    manifest = run["result"]["manifest"]
    devices = [device["device_name"] for device in manifest.get("devices", [])]
    return {
        "success": True,
        "message": f"已为 {len(devices)} 台防火墙生成策略配置",
        "policy_run_id": run["id"],
        "ticket_id": body.ticket_id,
        "device_count": len(devices),
        "devices": devices,
        "risk_level": manifest.get("risk_level"),
        "app_url": f"/apps/firewall-policy?run={run['id']}",
        "files": files,
    }


def draft_request(user, body, *, run_id):
    rows, notes, data = request_draft.build(user, body)
    filename = f"{body.ticket_id}-策略申请表.xlsx"
    # Kept in the user's netdisk, so the next step and the user read the same file.
    saved = files.save(user, filename, data, request_draft.XLSX, folder="防火墙申请表")
    return {
        "success": True,
        "message": f"按资源台账生成了 {len(rows)} 条策略申请",
        "policy_file_url": files.reference(saved["file_id"]),
        "ticket_id": body.ticket_id,
        "rows": rows,
        "notes": notes,
        "files": {"policy_request_draft": (filename, request_draft.XLSX, data)},
    }


def write_change_ticket(user, body, *, run_id):
    owner = user.user_id
    fields = TicketFields.model_validate(
        {key: value for key, value in body.model_dump(exclude={"policy_run_id"}).items() if value}
    )
    submitted = service.submit_ticket(owner, body.policy_run_id, TicketRequest(
        request_id=f"wf-{run_id}-ticket", fields=fields,
    ))
    run = wait(owner, submitted["id"], TICKET_SECONDS)
    return {
        "success": True,
        "message": "变更工单已生成",
        "ticket_run_id": run["id"],
        "policy_run_id": body.policy_run_id,
        "files": outcome(owner, run, TICKET_SECONDS, "变更工单"),
    }


ACTIONS = [
    Action(
        "draft_request",
        "按资源台账生成策略申请表：source/destination 填 IP、网段、范围或业务系统名称，"
        "业务系统取其在用主机的 IP，端口取目的主机登记的服务端口（或 ports 指定）；"
        "可带 start_time/end_time 有效期和 long_link 长连接小时数；"
        "申请表存入发起人网盘“防火墙申请表”文件夹，结果 policy_file_url 可直接交给 generate_policy；"
        "产物 policy_request_draft",
        DraftRequest,
        draft_request,
    ),
    Action(
        "generate_policy",
        "取得策略申请表（及可选拓扑）：网盘文件 netdisk:<文件ID> 或 http(s) 地址，"
        "在防火墙策略应用中生成策略配置；"
        "产物 config_zip、policy_preview、policy_request，结果含 policy_run_id",
        GeneratePolicy,
        generate_policy,
    ),
    Action(
        "write_change_ticket",
        "按 generate_policy 的 policy_run_id 生成变更工单 Excel；产物 change_excel",
        WriteTicket,
        write_change_ticket,
    ),
]
