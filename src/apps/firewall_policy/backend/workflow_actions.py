"""Workflow actions: the ITSM firewall workflows call the same service as the page.

Each action submits an ordinary run of this App for the Workflow's user, waits for it and
returns its files; the platform stores them as the step's artifacts. Request ids derive from
the Workflow run, so a redelivered step finds its run again and a retried Workflow (a new
run) starts a new one.
"""

import time
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from src.platform.tools.actions import Action

from . import service, storage
from .schemas import PolicyRequest, TicketFields, TicketRequest

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


def fetch(url, kind, fallback_name):
    """Download a file the ITSM ticket points at: http(s) only, at most the upload limit."""
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError(f"只支持 http(s) 文件地址：{url[:200]}")
    data = bytearray()
    with httpx.stream("GET", url, timeout=60, follow_redirects=True) as response:
        if response.status_code != 200:
            raise ValueError(f"下载文件失败（HTTP {response.status_code}）：{url[:200]}")
        for chunk in response.iter_bytes():
            data.extend(chunk)
            if len(data) > storage.MAX_UPLOAD:
                raise ValueError("文件超过 10 MB")
    suffix = ".xlsx" if kind == "policy" else ".json"
    name = PurePosixPath(unquote(parts.path)).name
    return (name if name.lower().endswith(suffix) else fallback_name), bytes(data)


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


def upload(owner, url, kind, fallback_name, request_id):
    try:
        filename, data = fetch(url, kind, fallback_name)
        return service.save_upload(owner, filename, kind, data, request_id)["id"]
    except HTTPException as exc:
        raise ValueError(f"{'策略申请表' if kind == 'policy' else '拓扑文件'}无效：{exc.detail}") from None


def generate_policy(user, body, *, run_id):
    owner = user.user_id
    request_id = f"wf-{run_id}-policy"
    policy = upload(owner, body.policy_file_url, "policy", f"{body.ticket_id}-策略申请表.xlsx",
                    f"{request_id}-file")
    topology = (
        upload(owner, body.topology_file_url, "topology", f"{body.ticket_id}-拓扑.json",
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
        "generate_policy",
        "下载工单中的策略申请表（及可选拓扑），在防火墙策略应用中生成策略配置；"
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
