from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from src.apps.device_access.backend import saved, service
from src.apps.device_access.backend.models import Base, DefaultLogin, Operation, SavedSession
from src.apps.device_access.backend.schemas import (
    BatchLogin,
    Collect,
    Login,
    SessionCreate,
    SessionUpdate,
    Ticket,
)
from src.platform.device_access.contracts import DeviceAccessError
from src.platform.device_access.security import CredentialStore


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


@pytest.fixture
def world(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)

    @contextmanager
    def sessions():
        with Session(engine) as session, session.begin():
            yield session

    monkeypatch.setenv("DEVICE_ACCESS_CREDENTIAL_KEY", Fernet.generate_key().decode())
    redis = Redis()
    store = CredentialStore(redis, Fernet.generate_key())
    monkeypatch.setattr(service, "sessions", sessions)
    monkeypatch.setattr(service, "CredentialStore", lambda: store)
    monkeypatch.setattr(
        service, "authorize_worker", lambda *args: SimpleNamespace(can_execute_skills=lambda: True)
    )
    calls = []

    class Access:
        failure = None

        def execute_batch(self, context, request, **kwargs):
            kwargs["should_continue"]()
            if self.failure:
                raise DeviceAccessError(self.failure, "failure")
            return SimpleNamespace(execution_status="succeeded", failure_code=None)

    def access(ref, host, port, username, password, **kwargs):
        calls.append((ref, host, port, username, password))
        return Access()

    monkeypatch.setattr(service, "access", access)
    yield SimpleNamespace(store=store, redis=redis, calls=calls, access=Access, sessions=sessions)
    engine.dispose()


def new_session(owner="u", **values):
    return saved.create(owner, SessionCreate(**{"name": "R1", "host": "10.0.0.1", **values}))


def test_session_crud_is_per_account(world):
    row = new_session(name="核心", username="operator", password="session-secret")
    assert row["uses_default"] is False and row["port"] == 22
    assert "password" not in row and "secret_ciphertext" not in row
    assert [r["id"] for r in saved.list_sessions("u")] == [row["id"]]
    assert saved.list_sessions("v") == []
    for call in (
        lambda: saved.update("v", row["id"], SessionUpdate(name="x")),
        lambda: saved.delete("v", row["id"]),
        lambda: saved.target("v", row["id"]),
    ):
        with pytest.raises(HTTPException):
            call()
    updated = saved.update("u", row["id"], SessionUpdate(name="边界", host="10.0.0.2", port=2222))
    assert (updated["name"], updated["host"], updated["port"]) == ("边界", "10.0.0.2", 2222)
    saved.delete("u", row["id"])
    assert saved.list_sessions("u") == []


def test_passwords_are_encrypted_at_rest(world):
    new_session(username="operator", password="session-secret")
    saved.save_default("u", Login(username="admin", password="default-secret"))
    with world.sessions() as session:
        stored = [row.secret_ciphertext for row in session.scalars(select(SavedSession))]
        stored += [row.secret_ciphertext for row in session.scalars(select(DefaultLogin))]
    assert len(stored) == 2
    assert all("secret" not in value for value in stored)
    assert saved.read_default("u") == {"username": "admin", "configured": True}


def test_login_falls_back_to_the_default(world):
    own = new_session(username="operator", password="session-secret")
    plain = new_session(name="R2")
    assert plain["uses_default"] is True
    with pytest.raises(DeviceAccessError) as missing:
        saved.login("u", plain["id"])
    assert missing.value.failure_code == "credentials_required"
    saved.save_default("u", Login(username="admin", password="default-secret"))
    assert saved.login("u", plain["id"]) == ("admin", "default-secret")
    assert saved.login("u", own["id"]) == ("operator", "session-secret")
    assert saved.login("u") == ("admin", "default-secret")
    # Clearing the username returns the session to the default login.
    saved.update("u", own["id"], SessionUpdate(username=""))
    assert saved.login("u", own["id"]) == ("admin", "default-secret")


def test_session_login_edit_keeps_or_requires_the_password(world):
    own = new_session(username="operator", password="session-secret")
    saved.update("u", own["id"], SessionUpdate(username="renamed"))
    assert saved.login("u", own["id"]) == ("renamed", "session-secret")
    plain = new_session(name="R2")
    with pytest.raises(HTTPException):
        saved.update("u", plain["id"], SessionUpdate(username="only-name"))
    with pytest.raises(ValueError):
        SessionCreate(name="R3", host="10.0.0.3", username="only-name")


def test_batch_edit_sets_one_login_on_selected_sessions(world):
    first, second, other = new_session(), new_session(name="R2"), new_session(name="R3")
    rows = saved.set_login(
        "u", BatchLogin(session_ids=[first["id"], second["id"]], username="ops", password="new")
    )
    assert {row["username"] for row in rows} == {"ops"}
    assert saved.login("u", first["id"]) == saved.login("u", second["id"]) == ("ops", "new")
    with pytest.raises(DeviceAccessError):
        saved.login("u", other["id"])
    foreign = new_session(owner="v")
    with pytest.raises(HTTPException):
        saved.set_login(
            "u", BatchLogin(session_ids=[first["id"], foreign["id"]], username="x", password="y")
        )
    assert saved.login("u", first["id"]) == ("ops", "new")


def test_collect_uses_the_saved_session_and_does_not_repeat_a_failed_login(world):
    row = new_session(username="operator", password="session-secret")
    body = Collect(request_id="1", session_id=row["id"], profile_id="comware-lldp-v1")
    with pytest.raises(HTTPException):
        service.collect("v", body)
    with pytest.raises(HTTPException):
        service.collect("u", Collect(request_id="2", session_id=row["id"], profile_id="unknown"))
    world.access.failure = "authentication_failed"
    first = service.collect("u", body)
    assert first["status"] == "waiting_for_input"
    assert first["result"]["failure_code"] == "authentication_failed"
    assert service.collect("u", body)["id"] == first["id"]
    assert world.calls == [(row["id"], "10.0.0.1", 22, "operator", "session-secret")]
    with world.sessions() as session:
        assert "secret" not in repr(session.scalar(select(Operation)).result)


def test_terminal_ticket_is_one_time_and_bound_to_its_operation(world):
    row = new_session(username="operator", password="session-secret")
    first = service.ticket("u", Ticket(request_id="1", session_id=row["id"]))
    with pytest.raises(DeviceAccessError):
        service.consume_ticket(first["ticket"], "wrong")
    with pytest.raises(DeviceAccessError):
        service.consume_ticket(first["ticket"], first["session_id"])
    second = service.ticket("u", Ticket(request_id="2", session_id=row["id"]))
    payload = service.consume_ticket(second["ticket"], second["session_id"])
    assert (payload["owner"], payload["host"], payload["credential_ref"]) == ("u", "10.0.0.1", None)
    assert service.terminal_login(payload) == ("operator", "session-secret")
    with pytest.raises(DeviceAccessError):
        service.consume_ticket(second["ticket"], second["session_id"])


def test_terminal_asks_for_a_login_and_uses_a_typed_one_once(world):
    # No saved or default login: the terminal closes with a reason the page prompts for.
    first = service.ticket("u", Ticket(request_id="1", host="10.0.0.9"))
    with pytest.raises(DeviceAccessError) as missing:
        service.terminal_login(service.consume_ticket(first["ticket"], first["session_id"]))
    assert missing.value.failure_code == "credentials_required"
    ticket = service.ticket(
        "u",
        Ticket(request_id="2", host="10.0.0.9", login=Login(username="ops", password="typed")),
    )
    payload = service.consume_ticket(ticket["ticket"], ticket["session_id"])
    assert payload["credential_ref"] and payload["save"] is False
    assert all(
        b"typed" not in value for value in world.redis.data.values() if isinstance(value, bytes)
    )
    assert service.terminal_login(payload) == ("ops", "typed")
    with pytest.raises(DeviceAccessError):
        service.terminal_login(payload)


def test_saving_a_typed_login_waits_for_a_successful_login(world):
    row = new_session()
    with pytest.raises(ValueError):
        Ticket(request_id="x", host="10.0.0.1", login=Login(username="a", password="b"), save=True)
    ticket = service.ticket(
        "u",
        Ticket(
            request_id="1",
            session_id=row["id"],
            login=Login(username="ops", password="typed"),
            save=True,
        ),
    )
    payload = service.consume_ticket(ticket["ticket"], ticket["session_id"])
    assert payload["save"] is True
    # Not saved yet: the terminal saves it only after the device accepts it.
    with pytest.raises(DeviceAccessError):
        saved.login("u", row["id"])


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
        "u", session_ids=["allowed"], run_id="run", reader=object(), check=lambda: None
    )
    with pytest.raises(PermissionError):
        workspace.invoke(
            "device_access__collect", {"session_id": "forbidden", "profile_id": "comware-lldp-v1"}
        )
    arguments = {"session_id": "allowed", "profile_id": "comware-lldp-v1"}
    first = workspace.invoke("device_access__collect", arguments)
    assert workspace.invoke("device_access__collect", arguments) == first
    assert len(calls) == 1
    with pytest.raises(PermissionError):
        workspace.invoke(
            "device_access__collect", {"session_id": "allowed", "profile_id": "comware-routes-v1"}
        )
    assert "citation" in first
