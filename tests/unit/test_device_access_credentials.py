"""P1-1 vault boundaries, attempt budgets, write-only API and migration isolation."""

import json
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from paramiko import AuthenticationException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src.apps.device_access.backend import credential_api
from src.apps.device_access.backend.credential_schemas import CredentialCreate, CredentialRotate
from src.apps.device_access.backend.credentials import CredentialVault
from src.apps.device_access.backend.models import Base, CredentialSet, DeviceCredential
from src.auth.models import CurrentUser
from src.platform.device_access.contracts import DeviceAccessError
from src.platform.device_access.security import CredentialCandidate, credential_order

PASSWORD = "test-only-password-that-must-not-leak"
ENABLE = "test-only-enable-secret"


@pytest.fixture
def vault():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)

    @contextmanager
    def sessions():
        with Session(engine) as session, session.begin():
            yield session

    value = CredentialVault(
        sessions=sessions,
        key=Fernet.generate_key(),
        authorize=lambda *a: None,
        inventory=lambda *a: None,
    )
    yield value
    engine.dispose()


def create(vault, *, owner="alice", **kw):
    data = dict(
        name="lab",
        domain="lab",
        scope_kind="domain",
        scope_value="lab",
        username="operator",
        password=PASSWORD,
    )
    data.update(kw)
    return vault.create(owner, CredentialCreate(**data))


def selected(vault, **kwargs):
    return vault.candidates("alice", "lab", "10.0.0.1", **kwargs)


def test_ciphertexts_metadata_repr_and_rotation(vault, caplog):
    metadata = create(vault, enable_secret=ENABLE)
    with vault.sessions() as session:
        row = session.get(CredentialSet, metadata["id"])
        assert PASSWORD not in row.secret_ciphertext
        assert ENABLE not in row.enable_secret_ciphertext
    material = vault.resolve("alice", "lab", "10.0.0.1", selected(vault)[0])
    assert (material.password, material.enable_secret) == (PASSWORD, ENABLE)
    assert PASSWORD not in repr(material) and ENABLE not in repr(material)
    assert "ciphertext" not in json.dumps(vault.list("alice"))
    assert PASSWORD not in json.dumps(metadata)
    assert metadata["has_enable_secret"] is True
    vault.rotate("alice", metadata["id"], CredentialRotate(password="rotated"))
    material = vault.resolve("alice", "lab", "10.0.0.1", selected(vault)[0])
    assert (material.password, material.enable_secret) == ("rotated", ENABLE)
    vault.rotate("alice", metadata["id"], CredentialRotate(password="next", enable_secret=None))
    assert vault.resolve("alice", "lab", "10.0.0.1", selected(vault)[0]).enable_secret is None
    assert PASSWORD not in caplog.text and ENABLE not in caplog.text


def test_scope_order_longest_prefix_priority_and_memory(vault):
    domain = create(vault, priority=1000)
    wide = create(vault, scope_kind="cidr", scope_value="10.0.0.0/8", priority=999)
    narrow = create(vault, scope_kind="cidr", scope_value="10.0.0.0/24", priority=0)
    device = create(vault, scope_kind="device", scope_value="10.0.0.1", priority=-999)
    assert [c.id for c in selected(vault)] == [device["id"], narrow["id"], wide["id"]]
    vault._remember("alice", "lab", "10.0.0.1", domain["id"])
    assert [c.id for c in selected(vault)] == [domain["id"], device["id"], narrow["id"]]
    vault.disable("alice", domain["id"], True)
    assert selected(vault)[0].id == device["id"]


@pytest.mark.parametrize(
    "owner,domain,address",
    [
        ("bob", "lab", "10.0.0.1"),
        ("alice", "other", "10.0.0.1"),
        ("alice", "lab", "10.1.0.1"),
    ],
)
def test_scope_precedes_remembered_credential(vault, owner, domain, address):
    item = create(vault, scope_kind="cidr", scope_value="10.0.0.0/24")
    candidate = selected(vault)[0]
    assert not credential_order([candidate], owner, domain, address, remembered_id=item["id"])
    with pytest.raises(DeviceAccessError):
        vault.resolve(owner, domain, address, candidate)


@pytest.mark.parametrize("operation", ["disable", "rotate"])
def test_other_owner_cannot_maintain_metadata(vault, operation):
    item = create(vault)
    assert vault.list("bob") == []
    with pytest.raises(HTTPException) as error:
        if operation == "disable":
            vault.disable("bob", item["id"], True)
        else:
            vault.rotate("bob", item["id"], CredentialRotate(password="new"))
    assert error.value.status_code == 404


def test_disabled_after_selection_is_rechecked(vault):
    item = create(vault)
    candidate = selected(vault)[0]
    vault.disable("alice", item["id"], True)
    with pytest.raises(DeviceAccessError, match="禁用"):
        vault.resolve("alice", "lab", "10.0.0.1", candidate)


def test_authorization_is_checked_before_secrets_or_connect(vault):
    create(vault)

    def denied(*args):
        raise DeviceAccessError("not_authorized", "denied")

    vault.authorize = denied
    with pytest.raises(DeviceAccessError):
        vault.login("alice", "lab", "10.0.0.1", lambda *a: pytest.fail("opened SSH"))


def test_wrong_then_right_each_once_and_success_is_remembered(vault, caplog):
    create(vault, username="wrong", priority=20)
    good = create(vault, username="right", password="correct-password", priority=10)
    attempted = []
    connection = SimpleNamespace(disconnect=lambda: None)

    def connect(material, budget):
        assert 0 < budget <= 30
        attempted.append(material.username)
        if material.username == "wrong":
            raise AuthenticationException(PASSWORD)
        return connection

    assert vault.login("alice", "lab", "10.0.0.1", connect) is connection
    assert attempted == ["wrong", "right"]
    assert selected(vault)[0].id == good["id"]
    attempted.clear()
    vault.login("alice", "lab", "10.0.0.1", connect)
    assert attempted == ["right"]
    with vault.sessions() as session:
        row = session.get(DeviceCredential, ("alice", "lab", "10.0.0.1"))
        assert row.credential_set_id == good["id"] and row.last_failure_code is None
    assert PASSWORD not in caplog.text


def test_total_failure_stops_at_three_and_waits_for_input(vault, caplog):
    for n in range(5):
        create(vault, username=f"account-{n}", priority=n)
    attempts = []

    def connect(material, budget):
        attempts.append(material.username)
        raise AuthenticationException(PASSWORD)

    with pytest.raises(DeviceAccessError) as error:
        vault.login("alice", "lab", "10.0.0.1", connect)
    assert attempts == ["account-4", "account-3", "account-2"]
    assert error.value.failure_code == "waiting_for_input"
    assert PASSWORD not in str(error.value) and PASSWORD not in caplog.text


@pytest.mark.parametrize(
    "code",
    [
        "timeout",
        "host_key_rejected",
        "host_key_changed",
        "connection_failed",
        "not_authorized",
        "cancelled",
    ],
)
def test_non_authentication_errors_never_cycle_credentials(vault, code):
    create(vault, priority=2)
    create(vault, priority=1)
    attempts = []

    def connect(*args):
        attempts.append(1)
        raise DeviceAccessError(code, PASSWORD)

    with pytest.raises(DeviceAccessError) as error:
        vault.login("alice", "lab", "10.0.0.1", connect)
    assert len(attempts) == 1 and error.value.failure_code == code
    assert PASSWORD not in str(error.value)


def test_invalid_ciphertext_fails_without_plaintext_details(vault):
    item = create(vault)
    with vault.sessions() as session:
        session.get(CredentialSet, item["id"]).secret_ciphertext = PASSWORD
    with pytest.raises(DeviceAccessError) as error:
        vault.resolve("alice", "lab", "10.0.0.1", selected(vault)[0])
    assert error.value.failure_code == "credential_expired" and PASSWORD not in str(error.value)


def test_cancellation_after_connect_closes_connection_and_does_not_remember(vault):
    create(vault)
    state, closed = [True], []

    def connect(*args):
        state[0] = False
        return SimpleNamespace(disconnect=lambda: closed.append(True))

    with pytest.raises(DeviceAccessError) as error:
        vault.login("alice", "lab", "10.0.0.1", connect, should_continue=lambda: state[0])
    assert error.value.failure_code == "cancelled" and closed == [True]
    with vault.sessions() as session:
        assert session.get(DeviceCredential, ("alice", "lab", "10.0.0.1")) is None


def test_env_compatibility_and_temporary_last(vault, monkeypatch):
    monkeypatch.setenv("P1_TEST_DEVICE_SECRET", PASSWORD)
    vault.inventory = lambda *a: ("asset-1", "env-user", "env:P1_TEST_DEVICE_SECRET")
    candidates = selected(vault, temporary_ref="temp-ref")
    assert [c.scope_kind for c in candidates] == ["device", "temporary"]
    assert vault.resolve("alice", "lab", "10.0.0.1", candidates[0]).password == PASSWORD
    calls = []
    vault.temporary = SimpleNamespace(
        get=lambda *args: calls.append(args) or ("temp-user", "temp-password")
    )
    assert vault.resolve("alice", "lab", "10.0.0.1", candidates[1]).password == "temp-password"
    assert calls == [("temp-ref", "alice", "lab", "10.0.0.1")]
    create(vault)
    assert [c.scope_kind for c in selected(vault, temporary_ref="temp-ref")] == [
        "device",
        "domain",
        "temporary",
    ]


def test_device_uid_and_ipv6_scope(vault):
    create(vault, scope_kind="device", scope_value="uid:node-1")
    assert not selected(vault)
    assert len(selected(vault, device_uid="node-1")) == 1
    create(vault, scope_kind="cidr", scope_value="2001:db8::/64")
    assert len(vault.candidates("alice", "lab", "2001:db8::1")) == 1


def test_duplicate_candidates_count_once():
    candidate = CredentialCandidate("a", "alice", "lab", "domain", "lab")
    result = credential_order(
        [candidate, candidate, replace(candidate, id="b")], "alice", "lab", "10.0.0.1"
    )
    assert [c.id for c in result] == ["a", "b"]


def test_operator_can_select_a_lower_ranked_set_without_scope_bypass(vault):
    for n in range(4):
        create(vault, username=f"other-{n}", priority=n)
    preferred = create(vault, username="chosen", priority=-10)
    calls = []
    connection = SimpleNamespace(disconnect=lambda: None)
    assert (
        vault.login(
            "alice",
            "lab",
            "10.0.0.1",
            lambda material, budget: calls.append(material.username) or connection,
            credential_set_id=preferred["id"],
        )
        is connection
    )
    assert calls == ["chosen"]
    with pytest.raises(DeviceAccessError) as error:
        vault.login(
            "bob",
            "lab",
            "10.0.0.1",
            lambda *a: pytest.fail("scope bypass"),
            credential_set_id=preferred["id"],
        )
    assert error.value.failure_code == "waiting_for_input"


@pytest.mark.parametrize("budget", [0, -1, 301, float("nan"), float("inf"), True, "30"])
def test_invalid_login_budgets_never_connect(vault, budget):
    with pytest.raises(DeviceAccessError):
        vault.login(
            "alice", "lab", "10.0.0.1", lambda *a: pytest.fail("opened SSH"), budget_seconds=budget
        )


def test_real_ssh_authenticates_each_candidate_once(vault):
    import socket
    import threading

    import paramiko

    create(vault, username="incorrect", password="wrong", priority=10)
    good = create(vault, username="correct", password="correct-secret", priority=1)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(3)
    listener.settimeout(0.2)
    port = listener.getsockname()[1]
    key = paramiko.RSAKey.generate(2048)
    stopped = threading.Event()
    attempts, errors = [], []

    class Server(paramiko.ServerInterface):
        def check_auth_password(self, username, password):
            accepted = username == "correct" and password == "correct-secret"
            attempts.append((username, accepted))
            return paramiko.AUTH_SUCCESSFUL if accepted else paramiko.AUTH_FAILED

        def get_allowed_auths(self, username):
            return "password"

    def serve():
        while not stopped.is_set():
            transport = None
            try:
                try:
                    sock, _ = listener.accept()
                except TimeoutError:
                    continue
                transport = paramiko.Transport(sock)
                transport.add_server_key(key)
                transport.start_server(server=Server())
                while transport.is_active() and not stopped.wait(0.01):
                    pass
            except (OSError, EOFError):
                pass
            except Exception as exc:
                errors.append(type(exc).__name__)
            finally:
                if transport is not None:
                    transport.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

    def connect(material, budget):
        client = paramiko.SSHClient()
        client.get_host_keys().add(f"[127.0.0.1]:{port}", key.get_name(), key)
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        try:
            client.connect(
                "127.0.0.1",
                port=port,
                username=material.username,
                password=material.password,
                look_for_keys=False,
                allow_agent=False,
                timeout=min(budget, 2),
                auth_timeout=min(budget, 2),
                banner_timeout=min(budget, 2),
            )
        except Exception:
            client.close()
            raise
        return SimpleNamespace(disconnect=client.close)

    try:
        connection = vault.login("alice", "lab", "127.0.0.1", connect)
        connection.disconnect()
        assert attempts == [("incorrect", False), ("correct", True)]
        assert vault.candidates("alice", "lab", "127.0.0.1")[0].id == good["id"]
    finally:
        stopped.set()
        listener.close()
        thread.join(3)
    assert not thread.is_alive() and not errors


@pytest.fixture
def client(vault, monkeypatch):
    app = FastAPI()
    app.include_router(credential_api.router)
    app.dependency_overrides[credential_api.actor] = lambda: CurrentUser(
        user_id="alice", username="alice", role="operator"
    )
    monkeypatch.setattr(credential_api, "CredentialVault", lambda: vault)
    with TestClient(app) as client:
        yield client, app


def payload(**kw):
    return (
        dict(
            name="lab",
            domain="lab",
            username="operator",
            password=PASSWORD,
            scope_kind="domain",
            scope_value="lab",
        )
        | kw
    )


def test_http_crud_returns_metadata_only(client):
    http, _ = client
    response = http.post("/credential-sets", json=payload(enable_secret=ENABLE))
    assert response.status_code == 201
    identifier = response.json()["id"]
    for response in [
        response,
        http.get("/credential-sets"),
        http.patch(f"/credential-sets/{identifier}", json={"disabled": True}),
        http.post(f"/credential-sets/{identifier}/rotate", json={"password": "next-secret"}),
    ]:
        assert response.status_code in {200, 201}
        assert all(
            secret not in response.text
            for secret in [PASSWORD, ENABLE, "next-secret", "ciphertext"]
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"password": ""},
        {"password": PASSWORD * 100},
        {"scope_value": "wrong"},
        {"scope_kind": "bad"},
        {"priority": "1"},
        {"enable_secret": {"secret": PASSWORD}},
        {"unexpected": PASSWORD},
    ],
)
def test_invalid_http_payload_never_echoes_secret(client, changes):
    http, _ = client
    response = http.post("/credential-sets", json=payload(**changes))
    assert response.status_code == 422
    assert PASSWORD not in response.text and "input" not in response.text


def test_viewer_can_read_own_metadata_but_not_write(client):
    http, app = client
    item = http.post("/credential-sets", json=payload()).json()
    app.dependency_overrides[credential_api.actor] = lambda: CurrentUser(
        user_id="alice", username="alice", role="viewer"
    )
    assert http.get("/credential-sets").json()[0]["id"] == item["id"]
    assert http.post("/credential-sets", json=payload()).status_code == 403
    assert http.patch(f"/credential-sets/{item['id']}", json={"disabled": True}).status_code == 403
    assert (
        http.post(f"/credential-sets/{item['id']}/rotate", json={"password": "new"}).status_code
        == 403
    )


def test_vault_connection_service_rotates_and_redacts_evidence(vault, monkeypatch):
    from src.apps.device_access.backend import service
    from src.apps.device_access.backend.schemas import ConnectionRequest
    from src.apps.device_access.backend.vault_driver import VaultDriver
    from src.platform.device_access import AccessContext, AccessGrant, BatchRequest
    from src.platform.device_access.contracts import Command
    from src.platform.device_access.errors import CommandExecutionError

    create(vault, username="wrong", password="wrong-secret", priority=10)
    good = create(vault, username="correct", enable_secret=ENABLE)
    monkeypatch.setattr(service, "sessions", vault.sessions)
    monkeypatch.setattr(service, "CredentialVault", lambda: vault)
    monkeypatch.setattr(service, "authorize_address", lambda *a: None)
    monkeypatch.setattr(
        service, "authorize_worker", lambda *a: SimpleNamespace(can_execute_skills=lambda: True)
    )
    attempts, closed, evidence = [], [], []

    class Driver:
        name, version = "test", "1"

        def connect(self, target, profile, *, interactive=False):
            attempts.append((profile.username, profile.connection_attempts, profile.ssh_strict))
            if profile.username == "wrong":
                raise AuthenticationException("never expose wrong-secret")
            assert profile.password == PASSWORD
            return SimpleNamespace(
                find_prompt=lambda: PASSWORD,
                send_command=self.send_command,
                disconnect=lambda: closed.append(True),
            )

        @staticmethod
        def send_command(command, **kwargs):
            if command == "display interface":
                raise CommandExecutionError("output_incomplete", PASSWORD, ENABLE + PASSWORD)
            return PASSWORD + ENABLE

    monkeypatch.setattr(
        service, "VaultDriver", lambda *a, driver=None, **kw: VaultDriver(*a, **kw, driver=Driver())
    )
    body = ConnectionRequest(
        domain="lab",
        address="10.0.0.1",
        credential_mode="vault",
        profiles=["comware-lldp-v1"],
        allow_terminal=True,
    )
    row = service.create_connection("alice", body)
    assert PASSWORD not in json.dumps(row)
    identifier = row["connection_id"]
    commands = (Command("v", "display version"), Command("i", "display interface"))
    access = service.access("alice", identifier)

    class Admission:
        @contextmanager
        def hold(self, *args):
            yield lambda: None

    access.admission = Admission()
    result = access.execute_batch(
        AccessContext(
            "alice",
            "device-access",
            (AccessGrant(identifier, identifier, frozenset(c.text for c in commands)),),
        ),
        BatchRequest(identifier, identifier, commands, capture_prompt=True),
        on_result=evidence.append,
    )
    from dataclasses import asdict

    serialized = json.dumps(asdict(result)) + json.dumps([asdict(c) for c in evidence])
    assert all(secret not in serialized for secret in (PASSWORD, ENABLE, "wrong-secret"))
    assert "[redacted]" in serialized and result.failure_code == "output_incomplete"
    assert attempts == [("wrong", 1, True), ("correct", 1, True)] and closed == [True]
    assert selected(vault)[0].id == good["id"]
    service.revoke("alice", identifier)
    with pytest.raises(DeviceAccessError, match="过期"):
        service.access("alice", identifier)


def test_vault_connection_rechecks_disable_before_ssh(vault, monkeypatch):
    from src.apps.device_access.backend import service
    from src.apps.device_access.backend.schemas import ConnectionRequest

    item = create(vault)
    monkeypatch.setattr(service, "sessions", vault.sessions)
    monkeypatch.setattr(service, "CredentialVault", lambda: vault)
    monkeypatch.setattr(service, "authorize_address", lambda *a: None)
    monkeypatch.setattr(
        service, "authorize_worker", lambda *a: SimpleNamespace(can_execute_skills=lambda: True)
    )
    row = service.create_connection(
        "alice",
        ConnectionRequest(
            domain="lab",
            address="10.0.0.1",
            credential_mode="vault",
            profiles=["comware-lldp-v1"],
            credential_set_id=item["id"],
        ),
    )
    access = service.access("alice", row["connection_id"])
    access.driver.driver = SimpleNamespace(connect=lambda *a, **kw: pytest.fail("opened SSH"))
    vault.disable("alice", item["id"], True)
    with pytest.raises(DeviceAccessError) as error:
        access.driver.connect(access.target_resolver(None), access.profile_resolver(None))
    assert error.value.failure_code == "waiting_for_input"


def test_connection_validation_does_not_echo_password(monkeypatch):
    from src.apps.device_access.backend import api

    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.actor] = lambda: CurrentUser(
        user_id="alice", username="alice", role="operator"
    )
    with TestClient(app) as http:
        response = http.post(
            "/connections",
            json={
                "address": "invalid",
                "password": PASSWORD,
                "username": "test",
                "profiles": ["comware-lldp-v1"],
            },
        )
    assert response.status_code == 422 and PASSWORD not in response.text


def test_upgrade_preserves_existing_connections(tmp_path):
    from datetime import timedelta

    from alembic import command
    from sqlalchemy import inspect, select

    from src.apps.device_access.backend.models import Connection, now
    from src.platform.kernel.migrations import configuration
    from src.platform.kernel.registry import discover

    engine = create_engine(f"sqlite:///{tmp_path / 'upgrade.db'}")
    manifest = discover()["device-access"]
    with engine.begin() as connection:
        config, _ = configuration(manifest)
        config.attributes.update(connection=connection, version_table="alembic_app_device_access")
        command.upgrade(config, "device_access_0001")
        connection.execute(
            Connection.__table__.insert().values(
                id="old",
                owner_id="alice",
                settings={"address": "10.0.0.1"},
                credential_ref="temporary-existing",
                expires_at=now() + timedelta(hours=1),
                created_at=now(),
            )
        )
        command.upgrade(config, "device_access_0002")
        command.upgrade(config, "device_access_0002")
        assert connection.scalar(select(Connection.credential_ref)) == "temporary-existing"
        assert set(inspect(connection).get_table_names()) == {
            "alembic_app_device_access",
            "app_device_access_connections",
            "app_device_access_operations",
            "app_device_access_credential_sets",
            "app_device_access_device_credentials",
        }
    engine.dispose()
