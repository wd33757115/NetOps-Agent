"""Ticket-authenticated, bounded human terminal. No batch session is reused.

Keystrokes go to the device as typed; the platform gate checks each command on
Enter against the SSH whitelist and records it with its output.
"""

import asyncio
import codecs
import collections
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
from src.platform.device_access.terminal_gate import TerminalGate
from src.platform.kernel.runtime import invocation

from . import saved, service


def pump(payload, incoming, outgoing, stop):
    owner, operation_id = payload["owner"], payload["operation_id"]
    host, port, session_id = payload["host"], payload["port"], payload.get("session_id")
    started, last_input, last_check = time.monotonic(), time.monotonic(), 0
    output_bytes, input_bytes, digest = 0, 0, hashlib.sha256()
    operation = None
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
            device_name=payload.get("device_name") or host,
        )
        access = service.access(
            ref, host, port, username, password, device_name=payload.get("device_name") or host
        )
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

            def before_send():
                # Every allowed command re-checks the grant and the account.
                nonlocal last_check
                if stop.is_set():
                    raise DeviceAccessError("cancelled", "终端已停止")
                lease()
                authorized()
                last_check = time.monotonic()

            def show(text):
                outgoing.put({"type": "output", "data": text}, timeout=2)

            gate = TerminalGate(channel, operation, show, before_send=before_send)
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            outgoing.put({"type": "ready"}, timeout=2)
            held = collections.deque()  # input waits while Enter is being checked
            try:
                while not stop.is_set():
                    current = time.monotonic()
                    if current - started > 900 or current - last_input > 120:
                        raise DeviceAccessError("terminal_expired", "终端已达到会话或空闲时限")
                    if current - last_check > 5:
                        lease()
                        authorized()
                        last_check = current
                    while True:
                        try:
                            held.append(incoming.get_nowait())
                            last_input = current
                        except queue.Empty:
                            break
                    if held and not gate.busy:
                        event = held.popleft()
                        channel.settimeout(2)
                        if event["type"] == "input":
                            input_bytes += len(event["data"].encode())
                            if input_bytes > 1024 * 1024:
                                raise DeviceAccessError("input_limit", "终端输入超过会话上限")
                            notice = gate.typed(event["data"])
                            if notice:
                                outgoing.put({"type": "notice", "message": notice}, timeout=2)
                        else:
                            timer = threading.Timer(3, channel.close)
                            timer.daemon = True
                            timer.start()
                            try:
                                channel.resize_pty(width=event["cols"], height=event["rows"])
                                gate.resize(event["cols"], event["rows"])
                            finally:
                                timer.cancel()
                    if channel.recv_ready():
                        raw = channel.recv(8192)
                        if not raw:
                            break
                        output_bytes += len(raw)
                        digest.update(raw)
                        if output_bytes > 4 * 1024 * 1024:
                            raise DeviceAccessError("output_limit", "终端输出超过会话上限")
                        gate.received(decoder.decode(raw))
                    elif channel.closed or channel.exit_status_ready():
                        break
                    elif not held or gate.busy:
                        stop.wait(0.02)
                    gate.tick()
            except BaseException:
                # Keep the original failure; the unfinished command is recorded if possible.
                with contextlib.suppress(Exception):
                    gate.close()
                raise
            else:
                gate.close()
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
    incoming, outgoing = queue.Queue(maxsize=256), queue.Queue(maxsize=256)
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
                    event.get("type") == "input"
                    and isinstance(event.get("data"), str)
                    and 0 < len(event["data"]) <= 4096
                ):
                    incoming.put_nowait({"type": "input", "data": event["data"]})
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
