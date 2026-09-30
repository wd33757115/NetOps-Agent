"""Ticket-authenticated, bounded human terminal. No batch session is reused."""

import asyncio
import contextlib
import hashlib
import json
import queue
import threading
import time

from fastapi import WebSocket

from src.platform.authorization.client import authorize_worker
from src.platform.device_access import AccessContext, AccessGrant
from src.platform.device_access.contracts import DeviceAccessError
from src.platform.device_access.governance import SSHOperation, write_event
from src.platform.device_access.human_cli import HumanCLI
from src.platform.kernel.runtime import invocation

from . import saved, service


def pump(payload, incoming, outgoing, stop):
    owner, operation_id = payload["owner"], payload["operation_id"]
    host, port, session_id = payload["host"], payload["port"], payload.get("session_id")
    started, last_input, last_check = time.monotonic(), time.monotonic(), 0
    output_bytes, input_bytes, digest = 0, 0, hashlib.sha256()
    operation = None
    seen = {}
    status, reason = "completed", None
    ref = session_id or operation_id
    try:

        def authorized():
            if stop.is_set():
                return False
            if not authorize_worker(owner, service.APP_ID).can_execute_skills():
                raise DeviceAccessError("not_authorized", "账号不能打开终端")
            return True

        if not authorized():
            return
        username, password = service.terminal_login(payload)
        operation = SSHOperation(
            owner,
            service.APP_ID,
            ref,
            host,
            port,
            task_id=operation_id,
            session_id=operation_id,
            secrets=(password,),
        )
        access = service.access(ref, host, port, username, password)
        context = AccessContext(
            owner,
            service.APP_ID,
            (AccessGrant(ref, ref, action="device.terminal"),),
            operation_id,
        )
        with (
            invocation(service.APP_ID),
            operation.activate(),
            access.terminal(context, ref, ref, should_continue=authorized) as (channel, lease),
        ):
            if payload.get("save") and session_id:
                saved.save_login(owner, session_id, username, password)

            def output(value):
                nonlocal output_bytes
                raw = value.encode()
                output_bytes += len(raw)
                digest.update(raw)
                if output_bytes > 4 * 1024 * 1024:
                    raise DeviceAccessError("output_limit", "终端输出超过会话上限")
                outgoing.put({"type": "output", "data": value}, timeout=2)

            def guard():
                nonlocal last_check
                if stop.is_set():
                    raise DeviceAccessError("cancelled", "终端已停止")
                if time.monotonic() - last_check >= 0.5:
                    lease()
                    if not authorized():
                        raise DeviceAccessError("cancelled", "终端已停止")
                    last_check = time.monotonic()
                if time.monotonic() - started > 900:
                    raise DeviceAccessError("terminal_expired", "终端已达到会话时限")

            def before_send():
                nonlocal last_check
                last_check = 0
                guard()

            cli = HumanCLI(channel, operation, guard, on_output=output, before_send=before_send)
            cli.initialize()
            outgoing.put({"type": "ready"}, timeout=2)
            while not stop.is_set():
                current = time.monotonic()
                if current - started > 900 or current - last_input > 120:
                    raise DeviceAccessError("terminal_expired", "终端已达到会话或空闲时限")
                if current - last_check > 5:
                    lease()
                    authorized()
                    last_check = current
                try:
                    event = incoming.get_nowait()
                except queue.Empty:
                    event = None
                if event:
                    last_input = current
                    channel.settimeout(2)
                    if event["type"] == "command":
                        command, request_id = event["command"], event["request_id"]
                        command_hash = hashlib.sha256(command.encode()).hexdigest()
                        if request_id in seen:
                            previous_hash, previous_event = seen[request_id]
                            if previous_hash != command_hash:
                                raise DeviceAccessError("invalid_request", "命令请求编号重复")
                            outgoing.put(previous_event, timeout=2)
                            continue
                        input_bytes += len(command.encode())
                        if input_bytes > 1024 * 1024:
                            raise DeviceAccessError("input_limit", "终端输入超过会话上限")
                        outgoing.put({"type": "command_sent", "request_id": request_id}, timeout=2)
                        try:
                            result = cli.execute(command, command_id=request_id)
                            reply = {
                                "type": "command_finished",
                                "request_id": request_id,
                                "status": result["status"],
                                "message": result.get("error"),
                            }
                        except DeviceAccessError as exc:
                            if not cli.usable or exc.failure_code == "audit_unavailable":
                                raise
                            reply = {
                                "type": "command_rejected",
                                "request_id": request_id,
                                "message": "命令被拒绝：" + str(exc),
                            }
                        seen[request_id] = (command_hash, reply)
                        outgoing.put(reply, timeout=2)
                    else:
                        timer = threading.Timer(3, channel.close)
                        timer.daemon = True
                        timer.start()
                        try:
                            channel.resize_pty(width=event["cols"], height=event["rows"])
                            operation.event(
                                "terminal_resized", cols=event["cols"], rows=event["rows"]
                            )
                        finally:
                            timer.cancel()
                if channel.closed or channel.exit_status_ready():
                    break
                else:
                    stop.wait(0.02)
    except Exception as exc:
        status = "waiting_for_input"
        reason = exc.failure_code if isinstance(exc, DeviceAccessError) else "terminal_unavailable"
    finally:
        try:
            try:
                write_event(
                    actor_id=owner,
                    source_app=service.APP_ID,
                    kind="terminal_closed",
                    status="failed" if reason else "success",
                    detail={
                        "host": host,
                        "port": port,
                        "session_id": operation_id,
                        "failure_code": reason,
                        "cancelled": stop.is_set(),
                    },
                )
            except DeviceAccessError:
                status, reason = "waiting_for_input", "audit_unavailable"
            service.finish(
                operation_id,
                status,
                {
                    "failure_code": reason,
                    "input_bytes": input_bytes,
                    "output_bytes": output_bytes,
                    "output_sha256": digest.hexdigest(),
                    "duration_seconds": round(time.monotonic() - started, 2),
                    "kind": "human_terminal_audit",
                },
            )
        finally:
            with contextlib.suppress(queue.Full):
                outgoing.put(
                    {"type": "closed", "reason": reason, "host": host, "port": port}, timeout=2
                )
            stop.set()


async def connect(socket: WebSocket, session_id: str):
    await socket.accept()
    stop = threading.Event()
    incoming, outgoing = queue.Queue(maxsize=32), queue.Queue(maxsize=32)
    worker = None
    try:
        raw = await asyncio.wait_for(socket.receive_text(), timeout=5)
        if len(raw) > 512:
            raise ValueError("Ticket frame too large")
        payload = await asyncio.to_thread(
            service.consume_ticket, json.loads(raw).get("ticket"), session_id
        )
        worker = asyncio.create_task(asyncio.to_thread(pump, payload, incoming, outgoing, stop))

        async def receive():
            while not stop.is_set():
                raw = await socket.receive_text()
                if len(raw.encode()) > 8192:
                    raise ValueError("Input frame too large")
                event = json.loads(raw)
                if (
                    event.get("type") == "command"
                    and isinstance(event.get("command"), str)
                    and isinstance(event.get("request_id"), str)
                    and 0 < len(event["request_id"]) <= 100
                ):
                    incoming.put_nowait(
                        {
                            "type": "command",
                            "command": event["command"],
                            "request_id": event["request_id"],
                        }
                    )
                elif event.get("type") == "cancel":
                    stop.set()
                    return
                elif (
                    event.get("type") == "resize"
                    and type(event.get("cols")) is int
                    and type(event.get("rows")) is int
                ):
                    incoming.put_nowait(
                        {
                            "type": "resize",
                            "cols": max(20, min(300, event["cols"])),
                            "rows": max(5, min(100, event["rows"])),
                        }
                    )
                else:
                    await asyncio.to_thread(
                        write_event,
                        actor_id=payload["owner"],
                        source_app=service.APP_ID,
                        kind="terminal_input_rejected",
                        status="denied",
                        detail={"host": payload["host"], "session_id": session_id},
                    )
                    raise ValueError("Invalid terminal event")

        async def send():
            while not stop.is_set() or not outgoing.empty():
                try:
                    event = outgoing.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(0.02)
                    continue
                await asyncio.wait_for(socket.send_json(event), timeout=5)

        receivers = [asyncio.create_task(receive()), asyncio.create_task(send())]
        try:
            done, _ = await asyncio.wait(receivers, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            for task in receivers:
                task.cancel()
            await asyncio.gather(*receivers, return_exceptions=True)
    except Exception:
        with contextlib.suppress(Exception):
            await socket.send_json({"type": "closed", "reason": "terminal_unavailable"})
    finally:
        stop.set()
        if worker:
            # Setup has its own timer; disconnect never leaves an unbounded worker.
            with contextlib.suppress(Exception):
                await asyncio.wait_for(asyncio.shield(worker), timeout=35)
        with contextlib.suppress(Exception):
            await socket.close()
