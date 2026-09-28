"""Ticket-authenticated, bounded human terminal. No batch session is reused."""

import asyncio
import codecs
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
from src.platform.kernel.runtime import invocation

from . import service


def pump(payload, incoming, outgoing, stop):
    owner, connection_id, operation_id = (
        payload[k] for k in ("owner", "connection_id", "operation_id")
    )
    started, last_input, last_check = time.monotonic(), time.monotonic(), 0
    output_bytes, input_bytes, digest = 0, 0, hashlib.sha256()
    status, reason = "completed", None
    try:

        def authorized():
            if stop.is_set():
                return False
            user = authorize_worker(owner, service.APP_ID)
            if not user.can_execute_skills():
                raise DeviceAccessError("not_authorized", "账号不能打开终端")
            service.connection(owner, connection_id, "device.terminal")
            return True

        if not authorized():
            return
        access = service.access(owner, connection_id, "device.terminal")
        context = AccessContext(
            owner,
            service.APP_ID,
            (AccessGrant(connection_id, connection_id, action="device.terminal"),),
            operation_id,
        )
        with (
            invocation(service.APP_ID),
            access.terminal(context, connection_id, connection_id, should_continue=authorized) as (
                channel,
                lease,
            ),
        ):
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            outgoing.put({"type": "ready"}, timeout=2)
            while not stop.is_set():
                current = time.monotonic()
                if current - started > 900 or current - last_input > 120:
                    raise DeviceAccessError("terminal_expired", "终端已达到会话或空闲时限")
                if current - last_check > 5:
                    lease()
                    user = authorize_worker(owner, service.APP_ID)
                    if not user.can_execute_skills():
                        raise DeviceAccessError("not_authorized", "账号权限已改变")
                    service.connection(owner, connection_id, "device.terminal")
                    last_check = current
                try:
                    event = incoming.get_nowait()
                except queue.Empty:
                    event = None
                if event:
                    last_input = current
                    channel.settimeout(2)
                    if event["type"] == "input":
                        raw = event["data"].encode()
                        input_bytes += len(raw)
                        if input_bytes > 1024 * 1024:
                            raise DeviceAccessError("input_limit", "终端输入超过会话上限")
                        channel.sendall(raw)
                    else:
                        timer = threading.Timer(3, channel.close)
                        timer.daemon = True
                        timer.start()
                        try:
                            channel.resize_pty(width=event["cols"], height=event["rows"])
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
                    outgoing.put({"type": "output", "data": decoder.decode(raw)}, timeout=2)
                elif channel.closed or channel.exit_status_ready():
                    break
                else:
                    stop.wait(0.02)
    except Exception as exc:
        status = "waiting_for_input"
        reason = exc.failure_code if isinstance(exc, DeviceAccessError) else "terminal_unavailable"
    finally:
        try:
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
                outgoing.put({"type": "closed", "reason": reason}, timeout=2)
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
                if len(raw.encode()) > 4096:
                    raise ValueError("Input frame too large")
                event = json.loads(raw)
                if event.get("type") == "input" and isinstance(event.get("data"), str):
                    incoming.put_nowait({"type": "input", "data": event["data"]})
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
            await asyncio.wait(receivers, return_when=asyncio.FIRST_COMPLETED)
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
