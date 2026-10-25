"""Bounded human batches. Durable progress, no automatic replay after interruption."""

import copy
import hashlib
import json
import time
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.platform.device_access import AccessContext, AccessGrant
from src.platform.device_access.contracts import DeviceAccessError
from src.platform.device_access.governance import SSHOperation, write_event
from src.platform.device_access.human_cli import HumanCLI
from src.platform.kernel.runtime import invocation

from . import saved, service
from .models import Operation, now

PROFILE = "human-batch"


def _owned(session, owner, batch_id, *, lock=False):
    row = session.get(Operation, batch_id, with_for_update=lock)
    if row is None or row.owner_id != str(owner) or row.profile_id != PROFILE:
        raise HTTPException(404, "批量执行记录不存在")
    return row


def _view(row):
    return {
        "id": row.id,
        "status": row.status,
        "results": row.result.get("results", []),
        "cancel_requested": row.result.get("cancel_requested", False),
        "notice": row.result.get("notice"),
    }


def create(owner, body):
    ids = list(dict.fromkeys(body.session_ids))
    targets = [(sid, *saved.target(owner, sid)) for sid in ids]
    fingerprint = hashlib.sha256(
        json.dumps([targets, body.commands], ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    secrets = []
    for sid in ids:
        try:
            secrets.append(saved.login(owner, sid)[1])
        except DeviceAccessError as exc:
            if exc.failure_code != "credentials_required":
                raise
    redactor = SSHOperation(owner, service.APP_ID, "batch", "batch", secrets=secrets)
    safe_commands = [redactor.redact(command) for command in body.commands]
    # Raw commands live only in the current worker. All stored command text is
    # redacted against the selected sessions' credentials before insertion.
    results = [
        {
            "session_id": sid,
            "connection_session_id": None,
            "name": name,
            "host": host,
            "port": port,
            "status": "queued",
            "commands": [
                {"command": command, "status": "queued", "output": ""} for command in safe_commands
            ],
        }
        for sid, name, host, port in targets
    ]

    def insert():
        with service.sessions() as session:
            existing = session.scalar(
                select(Operation).where(
                    Operation.owner_id == str(owner), Operation.request_id == body.request_id
                )
            )
            if existing:
                if (
                    existing.profile_id != PROFILE
                    or existing.result.get("fingerprint") != fingerprint
                ):
                    raise HTTPException(409, "请求编号已用于不同目标或命令")
                return _view(existing), False
            row = Operation(
                id=uuid4().hex,
                owner_id=str(owner),
                host="batch",
                request_id=body.request_id,
                profile_id=PROFILE,
                status="running",
                created_at=now(),
                result={"fingerprint": fingerprint, "results": results, "cancel_requested": False},
            )
            session.add(row)
            write_event(
                actor_id=owner,
                source_app=service.APP_ID,
                kind="batch_requested",
                detail={"task_id": row.id, "session_ids": ids, "commands": safe_commands},
                session=session,
            )
            return _view(row), True

    try:
        view, created = insert()
    except IntegrityError:
        view, created = insert()  # Competing identical request returns the same operation.
    return view, created, targets


def read(owner, batch_id):
    service.read(owner, batch_id)  # Marks interrupted workers without replaying them.
    with service.sessions() as session:
        return _view(_owned(session, owner, batch_id))


def cancel(owner, batch_id):
    with service.sessions() as session:
        row = _owned(session, owner, batch_id, lock=True)
        if row.status == "running":
            row.result = {**row.result, "cancel_requested": True}
            write_event(
                actor_id=owner,
                source_app=service.APP_ID,
                kind="batch_cancel_requested",
                detail={"task_id": batch_id},
                session=session,
            )
        return _view(row)


def _publish(owner, batch_id, results, status=None):
    with service.sessions() as session:
        row = _owned(session, owner, batch_id, lock=True)
        if row.status != "running":
            raise DeviceAccessError("cancelled", "批量执行已结束")
        row.result = {**row.result, "results": copy.deepcopy(results)}
        if status:
            row.status, row.finished_at = status, now()


def run(owner, batch_id, targets, commands):
    results = read(owner, batch_id)["results"]
    deadline, last_check = time.monotonic() + 900, 0
    guard_access = service._guard(owner, "账号不能执行 SSH 命令")
    cancelled, total_bytes = False, 0

    def guard():
        nonlocal last_check
        if time.monotonic() >= deadline:
            raise DeviceAccessError("budget_exceeded", "批量执行超过 15 分钟时限")
        if time.monotonic() - last_check >= 0.25:
            guard_access()
            with service.sessions() as session:
                row = _owned(session, owner, batch_id)
                if row.status != "running" or row.result.get("cancel_requested"):
                    raise DeviceAccessError("cancelled", "批量执行已停止")
            last_check = time.monotonic()

    try:
        with invocation(service.APP_ID):
            for index, (sid, _name, host, port) in enumerate(targets):
                device = results[index]
                op = None
                try:
                    last_check = 0
                    guard()
                    username, password = saved.login(owner, sid)
                    op = SSHOperation(
                        owner,
                        service.APP_ID,
                        sid,
                        host,
                        port,
                        task_id=batch_id,
                        secrets=(password,),
                        device_name=_name,
                    )
                    for item in device["commands"]:
                        item["command"] = op.redact(item["command"])
                    device["status"] = "running"
                    _publish(owner, batch_id, results)
                    access = service.access(
                        sid, host, port, username, password, device_name=_name
                    )
                    context = AccessContext(
                        owner,
                        service.APP_ID,
                        (AccessGrant(sid, sid, action="device.terminal"),),
                        batch_id,
                    )
                    with (
                        op.activate(),
                        access.terminal(context, sid, sid, should_continue=guard) as (
                            channel,
                            lease,
                        ),
                    ):
                        if op.connection_started:
                            device["connection_session_id"] = op.session_id
                        last_lease = 0

                        def checkpoint():
                            nonlocal last_lease
                            if time.monotonic() - last_lease >= 0.5:
                                lease()
                                last_lease = time.monotonic()
                            guard()

                        def before_send():
                            nonlocal last_check
                            last_check = 0
                            checkpoint()

                        cli = HumanCLI(channel, op, checkpoint, before_send=before_send)
                        cli.initialize()
                        for number, command in enumerate(commands):
                            last_check = 0
                            guard()
                            item = device["commands"][number]
                            item["status"] = "running"
                            _publish(owner, batch_id, results)
                            try:
                                result = cli.execute(
                                    command, command_id=f"{batch_id}:{index}:{number}"
                                )
                            except Exception as exc:
                                item.update(
                                    status="failed"
                                    if getattr(exc, "sent", False)
                                    else "not_executed",
                                    output=op.redact(getattr(exc, "output", ""))[:32768],
                                    error=op.redact(str(exc)),
                                    failure_code=getattr(exc, "failure_code", "connection_failed"),
                                    output_complete=False,
                                )
                                raise
                            total_bytes += len(result["output"].encode())
                            item.update(result)
                            item["truncated"] = len(item["output"]) > 32768
                            item["output"] = item["output"][:32768]
                            _publish(owner, batch_id, results)
                            if item["status"] != "succeeded":
                                raise DeviceAccessError("command_failed", "设备返回命令错误")
                            if total_bytes > 4 * 1024 * 1024:
                                raise DeviceAccessError("output_limit", "批量执行输出超过总上限")
                    device["status"] = "succeeded"
                except Exception as exc:
                    if op is not None and op.connection_started:
                        device["connection_session_id"] = op.session_id
                    code = getattr(exc, "failure_code", "connection_failed")
                    cancelled = code in {
                        "cancelled",
                        "not_authorized",
                        "budget_exceeded",
                        "output_limit",
                    }
                    device["status"] = "cancelled" if cancelled else "failed"
                    device["error"] = (
                        code  # Never persist driver exceptions containing credentials.
                    )
                    for item in device["commands"]:
                        if item["status"] in {"queued", "running"}:
                            item.update(status="not_executed", error=code)
                    write_event(
                        actor_id=owner,
                        source_app=service.APP_ID,
                        kind="batch_device_finished",
                        status="failed",
                        detail={
                            "task_id": batch_id,
                            "host": host,
                            "failure_code": code,
                            "results": device["commands"],
                            **(
                                {"session_id": device["connection_session_id"]}
                                if device.get("connection_session_id") else {}
                            ),
                        },
                    )
                _publish(owner, batch_id, results)
                if cancelled:
                    break
    finally:
        for device in results:
            if device["status"] == "queued":
                device["status"] = "not_executed"
                for item in device["commands"]:
                    item.update(status="not_executed", error="批量执行已停止")
        status = (
            "cancelled"
            if cancelled
            else ("completed" if all(d["status"] == "succeeded" for d in results) else "failed")
        )
        try:
            write_event(
                actor_id=owner,
                source_app=service.APP_ID,
                kind="batch_finished",
                status=status,
                detail={
                    "task_id": batch_id,
                    "devices": [{"host": d["host"], "status": d["status"]} for d in results],
                },
            )
        except DeviceAccessError:
            status = "failed"
        _publish(owner, batch_id, results, status)
