import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.apps.device_access.backend import terminal
from src.platform.device_access.contracts import DeviceAccessError


def application():
    from src.gateway.device_terminal import router

    app = FastAPI()
    app.include_router(router)
    return app


def test_invalid_ticket_cannot_open_a_transport(monkeypatch):
    def reject(*args):
        raise DeviceAccessError("not_authorized", "invalid ticket")

    monkeypatch.setattr(terminal.service, "consume_ticket", reject)
    monkeypatch.setattr(
        terminal, "pump", lambda *args: (_ for _ in ()).throw(AssertionError("transport opened"))
    )
    with (
        TestClient(application()) as client,
        client.websocket_connect("/ws/v1/terminals/test") as socket,
    ):
        socket.send_json({"ticket": "invalid"})
        assert socket.receive_json()["type"] == "closed"


def test_ticket_first_frame_and_bidirectional_resize_unicode(monkeypatch):
    received = []
    monkeypatch.setattr(
        terminal.service,
        "consume_ticket",
        lambda ticket, session: {"ticket": ticket, "session": session},
    )

    def pump(payload, incoming, outgoing, stop):
        assert payload == {"ticket": "one-use", "session": "test"}
        outgoing.put({"type": "ready"})
        for _ in range(2):
            received.append(incoming.get(timeout=3))
        outgoing.put({"type": "output", "data": "设备终端\r\n<SW>"})
        outgoing.put({"type": "closed", "reason": None})
        stop.set()

    monkeypatch.setattr(terminal, "pump", pump)
    with (
        TestClient(application()) as client,
        client.websocket_connect("/ws/v1/terminals/test") as socket,
    ):
        socket.send_json({"ticket": "one-use"})
        assert socket.receive_json()["type"] == "ready"
        socket.send_json({"type": "resize", "cols": 9999, "rows": 0})
        socket.send_json({"type": "command", "command": "display version", "request_id": "cmd1"})
        assert socket.receive_json() == {"type": "output", "data": "设备终端\r\n<SW>"}
        assert socket.receive_json()["type"] == "closed"
    assert received == [
        {"type": "resize", "cols": 300, "rows": 5},
        {"type": "command", "command": "display version", "request_id": "cmd1"},
    ]


def test_old_raw_input_protocol_cannot_bypass_command_policy(monkeypatch):
    import time

    received = []
    monkeypatch.setattr(
        terminal.service,
        "consume_ticket",
        lambda *args: {
            "owner": "u",
            "host": "10.0.0.1",
            "operation_id": "test",
        },
    )

    def pump(payload, incoming, outgoing, stop):
        outgoing.put({"type": "ready"})
        while not stop.is_set():
            if not incoming.empty():
                received.append(incoming.get_nowait())
            time.sleep(0.01)

    monkeypatch.setattr(terminal, "pump", pump)
    with TestClient(application()) as client:
        with client.websocket_connect("/ws/v1/terminals/test") as socket:
            socket.send_json({"ticket": "ticket"})
            assert socket.receive_json()["type"] == "ready"
            socket.send_json({"type": "input", "data": "reboot\n"})
            assert socket.receive_json()["type"] == "closed"
    assert received == []


def run_pump(monkeypatch, *, failure=None, save=False):
    import contextlib
    import queue
    import threading
    from types import SimpleNamespace

    saved_logins, finished = [], []

    class Channel:
        closed = False
        pending = True

        def recv_ready(self):
            return self.pending

        def recv(self, size):
            self.pending = False
            return b"<R1>"

        def exit_status_ready(self):
            return True

    class Access:
        @contextlib.contextmanager
        def terminal(self, *args, **kwargs):
            from src.platform.device_access.governance import current_operation

            operation = current_operation()
            assert operation.device_name == "核心交换机"
            assert operation.session_id == "op"
            if failure:
                raise DeviceAccessError(failure, "failure")
            yield Channel(), lambda: None

    monkeypatch.setattr(
        terminal, "authorize_worker", lambda *a: SimpleNamespace(can_execute_skills=lambda: True)
    )
    monkeypatch.setattr(terminal.service, "terminal_login", lambda payload: ("ops", "typed"))
    monkeypatch.setattr(terminal.service, "access", lambda *args, **kwargs: Access())
    monkeypatch.setattr(terminal.service, "finish", lambda *args: finished.append(args))
    monkeypatch.setattr(terminal.saved, "save_login", lambda *args: saved_logins.append(args))
    outgoing = queue.Queue()
    payload = {
        "owner": "u",
        "operation_id": "op",
        "session_id": "s1",
        "host": "10.0.0.1",
        "device_name": "核心交换机",
        "port": 22,
        "credential_ref": "ref",
        "save": save,
    }
    terminal.pump(payload, queue.Queue(), outgoing, threading.Event())
    events = []
    while not outgoing.empty():
        events.append(outgoing.get())
    return events, saved_logins, finished


def test_failed_login_closes_with_a_reason_the_page_can_prompt_for(monkeypatch):
    for failure in ("authentication_failed", "host_key_unknown", "host_key_changed"):
        events, saved_logins, finished = run_pump(monkeypatch, failure=failure, save=True)
        assert events[-1] == {"type": "closed", "reason": failure, "host": "10.0.0.1", "port": 22}
        assert saved_logins == []
        assert finished[-1][1] == "waiting_for_input"


def test_typed_login_is_saved_only_after_a_successful_login(monkeypatch):
    events, saved_logins, _ = run_pump(monkeypatch, save=True)
    assert {"type": "ready", "completion": None} in events and events[-1]["reason"] is None
    assert saved_logins == [("u", "s1", "ops", "typed")]
    _, saved_logins, _ = run_pump(monkeypatch, save=False)
    assert saved_logins == []


def test_complete_frame_preserves_revision_without_becoming_a_command(monkeypatch):
    received = []
    monkeypatch.setattr(terminal.service, "consume_ticket", lambda *args: {})

    def pump(payload, incoming, outgoing, stop):
        outgoing.put({"type": "ready", "completion": "comware"})
        received.append(incoming.get(timeout=3))
        outgoing.put({"type": "closed", "reason": None})
        stop.set()

    monkeypatch.setattr(terminal, "pump", pump)
    with (
        TestClient(application()) as client,
        client.websocket_connect("/ws/v1/terminals/test") as socket,
    ):
        socket.send_json({"ticket": "ticket"})
        assert socket.receive_json()["completion"] == "comware"
        socket.send_json(
            {"type": "complete", "command": "dis", "request_id": "tab1", "revision": 2}
        )
        assert socket.receive_json()["type"] == "closed"
    assert received == [
        {"type": "complete", "command": "dis", "request_id": "tab1", "revision": 2, "tab_count": 1}
    ]


@pytest.mark.parametrize(
    "field,value",
    [
        ("revision", None),
        ("revision", True),
        ("revision", -1),
        ("revision", 2**31),
        ("revision", "1"),
        ("tab_count", True),
        ("tab_count", 0),
        ("tab_count", 33),
        ("tab_count", "2"),
    ],
)
def test_invalid_completion_metadata_never_reaches_transport(monkeypatch, field, value):
    import time

    received = []
    monkeypatch.setattr(
        terminal.service,
        "consume_ticket",
        lambda *args: {
            "owner": "u",
            "host": "10.0.0.1",
            "operation_id": "test",
        },
    )

    def pump(payload, incoming, outgoing, stop):
        outgoing.put({"type": "ready"})
        while not stop.is_set():
            if not incoming.empty():
                received.append(incoming.get_nowait())
            time.sleep(0.01)

    monkeypatch.setattr(terminal, "pump", pump)
    with (
        TestClient(application()) as client,
        client.websocket_connect("/ws/v1/terminals/test") as socket,
    ):
        socket.send_json({"ticket": "ticket"})
        socket.receive_json()
        socket.send_json(
            {
                "type": "complete",
                "command": "dis",
                "request_id": "tab1",
                "revision": 1,
                field: value,
            }
        )
        assert socket.receive_json()["type"] == "closed"
    assert received == []


def completion_pump(monkeypatch, requests, *, reject=False):
    import contextlib
    import queue
    import threading
    from types import SimpleNamespace

    incoming, outgoing = queue.Queue(), queue.Queue()
    for request in requests:
        incoming.put(request)
    completed, executed, summaries = [], [], []

    class Channel:
        closed = False

        def settimeout(self, timeout):
            pass

        def exit_status_ready(self):
            return incoming.empty()

    class Access:
        @contextlib.contextmanager
        def terminal(self, *args, **kwargs):
            yield Channel(), lambda: None

    class CLI:
        completion_platform = "comware"
        usable = True

        def __init__(self, *args, **kwargs):
            pass

        def initialize(self):
            pass

        def complete(self, command, *, request_id, tab_count=1):
            completed.append((command, request_id))
            if reject:
                raise DeviceAccessError("completion_unsupported", "补全不受支持")
            return {"command": "display ", "changed": True, "received_bytes": 50}

        def execute(self, command, *, command_id):
            executed.append((command, command_id))
            return {"status": "succeeded"}

    monkeypatch.setattr(terminal, "HumanCLI", CLI)
    monkeypatch.setattr(
        terminal, "authorize_worker", lambda *args: SimpleNamespace(can_execute_skills=lambda: True)
    )
    monkeypatch.setattr(terminal.service, "terminal_login", lambda payload: ("ops", "test-secret"))
    monkeypatch.setattr(terminal.service, "access", lambda *args, **kwargs: Access())
    monkeypatch.setattr(terminal.service, "finish", lambda *args: summaries.append(args))
    terminal.pump(
        {"owner": "u", "operation_id": "op", "host": "192.0.2.1", "port": 22},
        incoming,
        outgoing,
        threading.Event(),
    )
    events = []
    while not outgoing.empty():
        events.append(outgoing.get())
    return completed, executed, events, summaries


def test_pump_completes_once_and_only_executes_a_separate_enter_command(monkeypatch):
    tab = {"type": "complete", "command": "dis", "request_id": "tab1", "revision": 3}
    command = {"type": "command", "command": "display clock", "request_id": "cmd1"}
    completed, executed, events, summaries = completion_pump(monkeypatch, [tab, tab, command])
    assert completed == [("dis", "tab1")]
    assert executed == [("display clock", "cmd1")]
    replies = [event for event in events if event["type"] == "completion_result"]
    assert len(replies) == 2 and replies[0] == replies[1]
    assert replies[0]["revision"] == 3 and replies[0]["command"] == "display "
    assert summaries[0][2]["completion_output_bytes"] == 50
    assert not any(event["type"] == "output" for event in events)


def test_completion_request_id_cannot_be_reused_to_execute_a_command(monkeypatch):
    completed, executed, events, _ = completion_pump(
        monkeypatch,
        [
            {"type": "complete", "command": "dis", "request_id": "one", "revision": 3},
            {"type": "command", "command": "dis", "request_id": "one"},
        ],
    )
    assert len(completed) == 1 and executed == []
    assert events[-1]["reason"] == "invalid_request"


def test_rejected_completion_preserves_revision_and_allows_normal_commands(monkeypatch):
    _, executed, events, _ = completion_pump(
        monkeypatch,
        [
            {"type": "complete", "command": "dis", "request_id": "tab1", "revision": 4},
            {"type": "command", "command": "display clock", "request_id": "cmd1"},
        ],
        reject=True,
    )
    reply = next(event for event in events if event["type"] == "completion_rejected")
    assert reply["revision"] == 4 and reply["request_id"] == "tab1"
    assert executed == [("display clock", "cmd1")]
