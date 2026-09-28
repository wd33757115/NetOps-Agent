import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from src.apps.device_access.backend import service
from src.apps.device_access.backend.models import Base, Operation
from src.apps.device_access.backend.schemas import Collect, ConnectionRequest, Ticket
from src.platform.device_access.contracts import DeviceAccessError
from src.platform.device_access.security import CredentialStore, authorize_address


class Redis:
    def __init__(self):
        self.data, self.ttls = {}, {}

    def set(self, key, value, ex=None):
        self.data[key], self.ttls[key] = value, ex

    def get(self, key):
        return self.data.get(key)

    def getdel(self, key):
        return self.data.pop(key, None)

    def delete(self, key):
        self.data.pop(key, None)


def test_credentials_encrypted_scoped_expiring_and_revocable():
    redis = Redis()
    store = CredentialStore(redis, Fernet.generate_key())
    ref = store.put("u", "domain-a", ["10.1.0.0/24"], "operator", "secret", ttl=120)
    assert b"secret" not in redis.get(store.prefix + ref)
    assert redis.ttls[store.prefix + ref] == 120
    assert store.get(ref, "u", "domain-a", "10.1.0.2") == ("operator", "secret")
    for actor, domain, address in [
        ("other", "domain-a", "10.1.0.2"),
        ("u", "domain-b", "10.1.0.2"),
        ("u", "domain-a", "10.2.0.2"),
    ]:
        with pytest.raises(DeviceAccessError):
            store.get(ref, actor, domain, address)
    store.revoke(ref)
    with pytest.raises(DeviceAccessError):
        store.get(ref, "u", "domain-a", "10.1.0.2")


def test_admin_address_grants_fail_closed_and_distinguish_terminal(monkeypatch, tmp_path):
    monkeypatch.delenv("DEVICE_ACCESS_GRANTS_FILE", raising=False)
    with pytest.raises(DeviceAccessError):
        authorize_address("u", "default", "10.0.0.1")
    path = tmp_path / "grants.json"
    path.write_text(
        json.dumps(
            {"u": [{"domain": "default", "cidrs": ["10.0.0.0/24"], "actions": ["device.collect"]}]}
        )
    )
    monkeypatch.setenv("DEVICE_ACCESS_GRANTS_FILE", str(path))
    authorize_address("u", "default", "10.0.0.1")
    with pytest.raises(DeviceAccessError):
        authorize_address("u", "default", "10.0.0.1", "device.terminal")


@pytest.fixture
def world(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)

    @contextmanager
    def sessions():
        with Session(engine) as session, session.begin():
            yield session

    store = CredentialStore(Redis(), Fernet.generate_key())
    monkeypatch.setattr(service, "sessions", sessions)
    monkeypatch.setattr(service, "CredentialStore", lambda: store)
    monkeypatch.setattr(service, "authorize_address", lambda *args: None)
    monkeypatch.setattr(
        service, "authorize_worker", lambda *args: SimpleNamespace(can_execute_skills=lambda: True)
    )
    calls = []

    class Access:
        failure = None

        def execute_batch(self, context, request, **kwargs):
            calls.append(request)
            kwargs["should_continue"]()
            if self.failure:
                raise DeviceAccessError(self.failure, "failure")
            return SimpleNamespace(execution_status="succeeded", failure_code=None)

    monkeypatch.setattr(service, "access", lambda *args: Access())
    connection = service.create_connection(
        "u",
        ConnectionRequest(
            address="10.0.0.1",
            username="operator",
            password="secret",
            profiles=["comware-lldp-v1"],
            allow_terminal=True,
        ),
    )
    yield SimpleNamespace(
        store=store,
        calls=calls,
        connection_id=connection["connection_id"],
        access=Access,
        sessions=sessions,
    )
    engine.dispose()


def test_collect_rejects_ungranted_profile_and_foreign_connection(world):
    with pytest.raises(HTTPException):
        service.collect(
            "u",
            Collect(request_id="1", connection_id=world.connection_id, profile_id="nxos-routes-v1"),
        )
    with pytest.raises(HTTPException):
        service.collect(
            "v",
            Collect(
                request_id="1", connection_id=world.connection_id, profile_id="comware-lldp-v1"
            ),
        )
    assert not world.calls


def test_collect_idempotency_does_not_repeat_failed_login(world):
    body = Collect(request_id="1", connection_id=world.connection_id, profile_id="comware-lldp-v1")
    world.access.failure = "authentication_failed"
    first = service.collect("u", body)
    assert first["status"] == "waiting_for_input"
    assert service.collect("u", body)["id"] == first["id"]
    assert len(world.calls) == 1
    with world.sessions() as session:
        row = session.scalar(select(Operation))
        assert "secret" not in repr(row.result)


def test_terminal_ticket_is_one_time_and_session_bound(world):
    first = service.ticket("u", Ticket(request_id="1", connection_id=world.connection_id))
    with pytest.raises(DeviceAccessError):
        service.consume_ticket(first["ticket"], "wrong")
    with pytest.raises(DeviceAccessError):
        service.consume_ticket(first["ticket"], first["session_id"])
    second = service.ticket("u", Ticket(request_id="2", connection_id=world.connection_id))
    assert service.consume_ticket(second["ticket"], second["session_id"])["owner"] == "u"
    with pytest.raises(DeviceAccessError):
        service.consume_ticket(second["ticket"], second["session_id"])


def test_revocation_blocks_collection_and_terminal(world):
    service.revoke("u", world.connection_id)
    for action in ["device.collect", "device.terminal"]:
        with pytest.raises(DeviceAccessError):
            service.connection("u", world.connection_id, action)


def test_active_dsh_scope_and_repeat_suppression(monkeypatch):
    from src.apps.assistant.backend.diagnostic_workspace import DiagnosticWorkspace
    from src.platform.tools import registry

    calls = []

    def invoke(name, args, **kwargs):
        calls.append(args)
        return {
            "id": "op",
            "status": "waiting_for_input",
            "result": {"failure_code": "authentication_failed"},
        }

    monkeypatch.setattr(registry, "invoke", invoke)
    workspace = DiagnosticWorkspace(
        "u", connection_ids=["allowed"], run_id="run", reader=object(), check=lambda: None
    )
    with pytest.raises(PermissionError):
        workspace.invoke(
            "device_access__collect",
            {"connection_id": "forbidden", "profile_id": "comware-lldp-v1"},
        )
    arguments = {"connection_id": "allowed", "profile_id": "comware-lldp-v1"}
    first = workspace.invoke("device_access__collect", arguments)
    assert workspace.invoke("device_access__collect", arguments) == first
    assert len(calls) == 1
    with pytest.raises(PermissionError):
        workspace.invoke(
            "device_access__collect",
            {"connection_id": "allowed", "profile_id": "comware-routes-v1"},
        )
    assert "citation" in first
