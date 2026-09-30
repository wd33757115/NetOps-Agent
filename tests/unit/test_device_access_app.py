from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from src.apps.device_access.backend import diagnosis, saved, service
from src.apps.device_access.backend.models import Base, DefaultLogin, SavedSession
from src.apps.device_access.backend.schemas import (
    BatchLogin,
    Login,
    SessionCreate,
    SessionUpdate,
    Ticket,
)
from src.infrastructure.db.models import AuditLogRecord
from src.platform.device_access.command_policy import classify
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
    AuditLogRecord.__table__.create(engine)

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
    calls, opened, handles, sent, options = [], [], [], [], []

    class Handle:
        """Stand-in for the platform session: real command classification, no device."""

        output = "output"

        def __init__(self, platform):
            self.platform, self.closed = platform, False

        def run(self, text, read_timeout=30):
            decision = classify(self.platform, text)
            sent.append(decision.normalized)
            ran = decision.level in {"L1", "L2"}
            return SimpleNamespace(
                normalized_command=decision.normalized,
                policy_level=decision.level,
                execution_status="succeeded" if ran else "denied",
                evidence_ref="device-access:audit:" + str(len(sent)),
                output=self.output if ran else "",
                output_complete=True,
                failure_code=None,
            )

        def close(self):
            self.closed = True

    class Access:
        failure = None

        def execute_batch(self, context, request, **kwargs):
            kwargs["should_continue"]()
            if self.failure:
                raise DeviceAccessError(self.failure, "failure")
            return SimpleNamespace(execution_status="succeeded", failure_code=None)

        def open_session(self, context, ref, **kwargs):
            opened.append((ref, kwargs["platform"], kwargs["purpose"], kwargs["budget"]))
            kwargs["should_continue"]()
            if self.failure:
                raise DeviceAccessError(self.failure, "failure")
            handles.append(Handle(kwargs["platform"]))
            return handles[-1]

    def access(ref, host, port, username, password, **kwargs):
        calls.append((ref, host, port, username, password))
        options.append(kwargs)
        return Access()

    monkeypatch.setattr(service, "access", access)
    yield SimpleNamespace(
        store=store,
        redis=redis,
        calls=calls,
        options=options,
        access=Access,
        sessions=sessions,
        opened=opened,
        handles=handles,
        sent=sent,
        handle=Handle,
    )
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


def test_diagnosis_logs_in_with_the_hosts_session_else_the_default(world):
    saved.save_default("u", Login(username="admin", password="default-secret"))
    new_session(host="10.0.0.2", username="operator", password="session-secret")
    diagnosis.run("u", "10.0.0.2", "comware", ["display version"], "核对版本", "turn-1")
    diagnosis.run("u", "10.0.0.3", "comware", ["display version"], "核对版本", "turn-1")
    assert [call[1:] for call in world.calls] == [
        ("10.0.0.2", 22, "operator", "session-secret"),
        ("10.0.0.3", 22, "admin", "default-secret"),
    ]
    ref, platform, purpose, budget = world.opened[0]
    assert (ref, platform, purpose) == ("diagnosis:turn-1:10.0.0.2", "comware", "核对版本")
    assert budget.max_commands == 5 and budget.total_seconds <= 75
    assert all(handle.closed for handle in world.handles)
    assert [option["device_name"] for option in world.options] == ["R1", "10.0.0.3"]


def test_saved_session_access_passes_its_configured_name(world):
    row = new_session(name="核心交换机", username="operator", password="session-secret")
    service.session_access("u", row["id"])
    assert world.options == [{"device_name": "核心交换机"}]


def test_access_target_keeps_optional_display_name_without_connecting():
    access = service.access("ref", "10.0.0.1", 22, "operator", "test", device_name="核心交换机")
    target = access.target_resolver("ref")
    assert (target.target_ref, target.host, target.display_name) == (
        "ref", "10.0.0.1", "核心交换机"
    )


def test_diagnosis_runs_read_only_commands_and_never_sends_the_rest(world):
    saved.save_default("u", Login(username="admin", password="default-secret"))
    view = diagnosis.run(
        "u",
        "10.0.0.2",
        "comware",
        [
            "display ip routing-table",
            "display current-configuration",
            "system-view",
            "debugging ospf event",
            "ping -c 2 192.168.1.2",
        ],
        "核对路由",
        "turn-1",
    )
    assert view["login"] == "succeeded"
    status = {c["command"]: (c.get("policy_level"), c["status"]) for c in view["commands"]}
    assert status["display ip routing-table"] == ("L1", "succeeded")
    assert status["display current-configuration"] == ("L1", "not_executed")
    assert status["debugging ospf event"] == ("L3", "not_executed")
    assert status["system-view"][1] == "not_executed"
    assert status["ping -c 2 192.168.1.2"][0] == "L2"
    # A configuration dump never reaches the session; the policy decides the rest there.
    assert "display current-configuration" not in world.sent
    routes = next(c for c in view["commands"] if c["command"] == "display ip routing-table")
    assert routes["output"] == "output" and routes["evidence_ref"].startswith("device-access:audit")


def test_diagnosis_login_failure_is_reported_once_and_not_retried(world):
    saved.save_default("u", Login(username="admin", password="default-secret"))
    world.access.failure = "authentication_failed"
    view = diagnosis.run("u", "10.0.0.2", "comware", ["display version"], "核对版本", "turn-1")
    assert (view["login"], view["failure_code"]) == ("failed", "authentication_failed")
    assert len(world.opened) == 1 and "secret" not in repr(view)


def test_diagnosis_bounds_output_and_skips_login_for_dumps_only(world):
    saved.save_default("u", Login(username="admin", password="default-secret"))
    # Bytes, not characters: interface descriptions may be Chinese.
    world.handle.output = "接口描述" * diagnosis.EXCERPT_BYTES
    [item] = diagnosis.run("u", "10.0.0.2", "comware", ["display arp"], "看 ARP", "t")["commands"]
    assert len(item["output"].encode()) <= diagnosis.EXCERPT_BYTES and item["output_truncated"]
    view = diagnosis.run("u", "10.0.0.2", "comware", ["display this"], "看配置", "t")
    assert view["login"] == "not_needed" and len(world.opened) == 1


def test_terminal_ticket_is_one_time_and_bound_to_its_operation(world):
    row = new_session(username="operator", password="session-secret")
    first = service.ticket("u", Ticket(request_id="1", session_id=row["id"]))
    with pytest.raises(DeviceAccessError):
        service.consume_ticket(first["ticket"], "wrong")
    with pytest.raises(DeviceAccessError):
        service.consume_ticket(first["ticket"], first["session_id"])
    second = service.ticket("u", Ticket(request_id="2", session_id=row["id"]))
    saved.update("u", row["id"], SessionUpdate(name="renamed"))
    payload = service.consume_ticket(second["ticket"], second["session_id"])
    assert (payload["owner"], payload["host"], payload["credential_ref"]) == ("u", "10.0.0.1", None)
    assert payload["device_name"] == "R1"
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
    assert payload["device_name"] == "10.0.0.9"
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


def trace_answer(*devices):
    hops = [
        {"device": d, "in_interface": None, "out_interface": None, "route": None} for d in devices
    ]
    return [{"forward": {"paths": [{"hops": hops, "end": {"status": "delivered"}}]}}]


def device(address, platform="comware", collected=True):
    return {
        "node_id": "n" + address,
        "address": address,
        "collected": collected,
        "platform": platform,
    }


@pytest.fixture
def workspace(monkeypatch):
    from src.apps.assistant.backend import diagnostic_workspace
    from src.platform.tools import registry

    calls = []

    def invoke(name, arguments, **kwargs):
        calls.append((name, arguments))
        if name == "topology__trace_path":
            return trace_answer(
                device("10.0.0.1"),
                device("10.0.0.2", platform=None),
                device("10.0.0.3", collected=False),
                device("10.0.0.4"),
            )
        return {"host": arguments["host"], "login": "succeeded", "commands": []}

    monkeypatch.setattr(registry, "invoke", invoke)
    space = diagnostic_workspace.DiagnosticWorkspace(
        "u", run_id="turn-1", reader=object(), check=lambda: None
    )
    space.evidence_tools = {"topology__trace_path", "topology__locate"}
    return SimpleNamespace(space=space, calls=calls, module=diagnostic_workspace)


def test_diagnosis_can_only_reach_devices_this_turns_topology_answer_named(workspace):
    space = workspace.space
    run = {"host": "10.0.0.1", "commands": ["display version"], "purpose": "核对"}
    with pytest.raises(PermissionError):
        space.invoke("device_access__run", run)
    space.invoke("topology__trace_path", {"src": "1.1.1.2", "dst": "192.168.1.2"})
    # Collected devices with an identified platform only.
    assert space.scope == {"10.0.0.1": "comware", "10.0.0.4": "comware"}
    answer = space.invoke("device_access__run", run)
    assert "citation" in answer
    name, arguments = workspace.calls[-1]
    assert (name, arguments["platform"], arguments["turn_id"]) == (
        "device_access__run",
        "comware",
        "turn-1",
    )
    for host in ("10.0.0.2", "10.0.0.3", "10.9.9.9"):
        with pytest.raises(PermissionError):
            space.invoke("device_access__run", {**run, "host": host})
    with pytest.raises(PermissionError):
        space.invoke("device_access__run", {**run, "platform": "ios"})


def test_diagnosis_budgets_commands_and_devices_per_turn(workspace):
    space = workspace.space
    space.invoke("topology__trace_path", {"src": "1.1.1.2", "dst": "192.168.1.2"})
    five = {"host": "10.0.0.1", "commands": ["display version"] * 5, "purpose": "核对"}
    for _ in range(workspace.module.MAX_COMMANDS // 5):
        space.invoke("device_access__run", five)
    with pytest.raises(PermissionError):
        space.invoke("device_access__run", {**five, "commands": ["display version"]})
    many = [device(f"10.1.0.{n}") for n in range(20)]
    space.scope.clear()
    space.widen(trace_answer(*many))
    assert len(space.scope) == workspace.module.MAX_DEVICES


def test_config_comparison_uses_the_same_scope_and_command_budget(workspace):
    space = workspace.space
    query = {"host": "10.0.0.1", "snapshot_id": "s1", "purpose": "核对接入接口"}
    with pytest.raises(PermissionError):
        space.invoke("device_access__compare_config", query)
    space.invoke("topology__trace_path", {})
    for extra in (
        {"platform": "ios"},
        {"turn_id": "another"},
        {"commands": ["display version"]},
        {"command": "display version"},
    ):
        with pytest.raises(PermissionError):
            space.invoke("device_access__compare_config", {**query, **extra})
    result = space.invoke("device_access__compare_config", query)
    assert result["citation"] and space.commands == 1
    assert workspace.calls[-1][1]["turn_id"] == "turn-1"
    assert workspace.calls[-1][1]["platform"] == "comware"
    space.commands = workspace.module.MAX_COMMANDS
    with pytest.raises(PermissionError):
        space.invoke("device_access__compare_config", query)


def test_the_model_never_sees_server_owned_run_fields(workspace, monkeypatch):
    from src.apps.device_access.backend.schemas import DiagnosisRun
    from src.platform.tools import registry

    definition = {
        "name": "device_access__run",
        "description": "run",
        "input_schema": DiagnosisRun.model_json_schema(),
        "mutates": True,
    }
    monkeypatch.setattr(
        registry, "available_tools", lambda **kwargs: [definition] if kwargs.get("names") else []
    )
    monkeypatch.setattr(workspace.module.DocumentWorkspace, "definitions", lambda self: [])
    [offered] = workspace.space.definitions()
    assert not {"platform", "turn_id"} & set(offered["input_schema"]["properties"])
    assert not {"platform", "turn_id"} & set(offered["input_schema"]["required"])


@pytest.mark.parametrize("inspection_available", [True, False])
def test_comparison_requires_authorized_baseline_provider_and_hides_internal_fields(
    workspace, monkeypatch, inspection_available
):
    from src.apps.device_access.backend.config_compare import CompareConfig
    from src.platform.tools import registry

    definition = {
        "name": "device_access__compare_config", "description": "compare",
        "input_schema": CompareConfig.model_json_schema(), "mutates": True,
    }

    def available(**kwargs):
        if not kwargs.get("names"):
            return []
        return [definition] + ([{"name": "inspection__read_baseline"}]
                               if inspection_available else [])

    monkeypatch.setattr(registry, "available_tools", available)
    offered = workspace.space.definitions()
    assert len(offered) == int(inspection_available)
    if offered:
        assert offered[0]["name"] == "device_access__compare_config"
        assert not {"platform", "turn_id"} & set(offered[0]["input_schema"]["properties"])
        assert not {"platform", "turn_id"} & set(offered[0]["input_schema"]["required"])
