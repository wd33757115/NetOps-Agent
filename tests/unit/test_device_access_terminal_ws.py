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
        socket.send_json({"type": "input", "data": "display version\n"})
        assert socket.receive_json() == {"type": "output", "data": "设备终端\r\n<SW>"}
        assert socket.receive_json()["type"] == "closed"
    assert received == [
        {"type": "resize", "cols": 300, "rows": 5},
        {"type": "input", "data": "display version\n"},
    ]
