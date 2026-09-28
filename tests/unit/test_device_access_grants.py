"""Database scope isolation, revocation and explicit legacy import boundaries."""

from contextlib import contextmanager

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src.apps.device_access.backend import grant_api, service
from src.apps.device_access.backend.grants import GrantCreate, GrantImport, GrantState, GrantStore
from src.apps.device_access.backend.models import Base, GrantAudit
from src.auth.models import CurrentUser
from src.platform.device_access.contracts import DeviceAccessError


@pytest.fixture
def store(monkeypatch):
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)

    @contextmanager
    def sessions():
        with Session(engine) as session, session.begin():
            yield session

    monkeypatch.setattr(service, "sessions", sessions)
    yield GrantStore(sessions=sessions)
    engine.dispose()


def body(**kw):
    return GrantCreate(
        **(dict(owner_id="alice", domain="lab", cidr="10.0.0.0/24", action="device.collect") | kw)
    )


@pytest.mark.parametrize(
    "owner,domain,ip,action",
    [
        ("bob", "lab", "10.0.0.1", "device.collect"),
        ("alice", "prod", "10.0.0.1", "device.collect"),
        ("alice", "lab", "10.0.1.1", "device.collect"),
        ("alice", "lab", "10.0.0.1", "device.terminal"),
        ("alice", "lab", "10.0.0.1", "device.session"),
    ],
)
def test_scope_and_action_never_expand(store, owner, domain, ip, action):
    store.create("admin", body())
    store.authorize("alice", "lab", "10.0.0.1")
    with pytest.raises(DeviceAccessError) as exc:
        store.authorize(owner, domain, ip, action)
    assert exc.value.failure_code == "not_authorized"


def test_ipv6_single_ip_and_overlapping_prefixes(store):
    store.create("admin", body(cidr="2001:db8::/32"))
    store.create("admin", body(cidr="10.0.0.1"))
    store.create("admin", body())
    store.authorize("alice", "lab", "2001:db8::1")
    store.authorize("alice", "lab", "10.0.0.1")
    assert any(row["cidr"] == "10.0.0.1/32" for row in store.list())


@pytest.mark.parametrize(
    "kw",
    [
        {"cidr": "10.0.0.1/24"},
        {"cidr": "example.com"},
        {"action": "*"},
        {"action": "device.session"},
        {"owner_id": " "},
        {"domain": "a\nb"},
    ],
)
def test_invalid_grants_do_not_silently_broaden(kw):
    with pytest.raises(ValidationError):
        body(**kw)


def test_revocation_immediate_and_cas_prevents_stale_restore(store):
    created = store.create("admin", body())
    store.set_state("admin", created["id"], GrantState(revision=1, enabled=False))
    with pytest.raises(DeviceAccessError):
        store.authorize("alice", "lab", "10.0.0.1")
    with pytest.raises(HTTPException) as error:
        store.set_state("admin", created["id"], GrantState(revision=1, enabled=True))
    assert error.value.status_code == 409
    store.set_state("admin", created["id"], GrantState(revision=2, enabled=True))
    store.authorize("alice", "lab", "10.0.0.1")
    with store.sessions() as session:
        assert [
            row.operation
            for row in session.scalars(select(GrantAudit).order_by(GrantAudit.created_at))
        ] == ["create", "revoke", "enable"]


def legacy(cidrs=None):
    return GrantImport(
        grants={
            "alice": [
                {
                    "domain": "lab",
                    "cidrs": cidrs or ["10.0.0.0/24"],
                    "actions": ["device.collect", "device.terminal"],
                }
            ]
        }
    )


def test_import_idempotent_and_does_not_restore_revoked_scope(store):
    assert store.import_file("admin", legacy()) == {"added": 2, "skipped": 0}
    selected = next(row for row in store.list() if row["action"] == "device.collect")
    store.set_state("admin", selected["id"], GrantState(revision=1, enabled=False))
    assert store.import_file("admin", legacy()) == {"added": 0, "skipped": 2}
    with pytest.raises(DeviceAccessError):
        store.authorize("alice", "lab", "10.0.0.1")
    store.authorize("alice", "lab", "10.0.0.1", "device.terminal")


def test_invalid_import_is_atomic(store):
    with pytest.raises(HTTPException):
        store.import_file("admin", legacy(["10.0.0.0/24", "10.1.1.1/24"]))
    assert store.list() == []


def test_existing_file_cannot_override_database_denial(store, tmp_path, monkeypatch):
    path = tmp_path / "grants.json"
    path.write_text(legacy().model_dump_json())
    monkeypatch.setenv("DEVICE_ACCESS_GRANTS_FILE", str(path))
    with pytest.raises(DeviceAccessError):
        service.authorize_address("alice", "lab", "10.0.0.1")


def test_database_failure_cannot_fall_back_to_file():
    @contextmanager
    def unavailable():
        raise OperationalError("secret database url", {}, Exception())
        yield

    with pytest.raises(DeviceAccessError) as error:
        GrantStore(sessions=unavailable).authorize("alice", "lab", "10.0.0.1")
    assert "secret" not in str(error.value)


def test_audit_failure_rolls_back_grant_creation(store, monkeypatch):
    def fail(*a):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(store, "audit", fail)
    with pytest.raises(RuntimeError):
        store.create("admin", body())
    assert store.list() == []


@pytest.mark.parametrize("role", ["viewer", "operator", "admin"])
def test_api_maintenance_role_and_owned_metadata(store, role):
    store.create("admin", body())
    store.create("admin", body(owner_id="bob"))
    app = FastAPI()
    app.include_router(grant_api.router)
    app.dependency_overrides[grant_api.actor] = lambda: CurrentUser(
        user_id="alice", username="alice", role=role
    )
    with TestClient(app) as client:
        assert len(client.get("/grants").json()) == (2 if role == "admin" else 1)
        result = client.post("/grants", json=body(cidr="10.2.0.0/16").model_dump())
        assert result.status_code == (201 if role == "admin" else 403)
        assert client.post("/grants/import", json=legacy().model_dump()).status_code == (
            200 if role == "admin" else 403
        )
        row = store.list("alice")[0]
        assert client.patch(
            f"/grants/{row['id']}", json={"revision": 1, "enabled": False}
        ).status_code == (200 if role == "admin" else 403)


def test_topology_and_vault_use_same_public_scope_service():
    from src.apps.device_access.backend import credentials, host_keys
    from src.apps.topology.backend import service as topology

    assert topology.authorize_address is service.authorize_address
    assert credentials.authorize_address is service.authorize_address
    assert host_keys.authorize_address is service.authorize_address


def test_package_migrations_reach_grants_without_touching_other_apps(tmp_path):
    from alembic import command
    from sqlalchemy import inspect

    from src.platform.kernel.migrations import configuration
    from src.platform.kernel.registry import discover

    engine = create_engine(f"sqlite:///{tmp_path / 'grants.db'}")
    manifest = discover()["device-access"]
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
        connection.exec_driver_sql("INSERT INTO unrelated VALUES (1)")
        config, _ = configuration(manifest)
        config.attributes.update(connection=connection, version_table="alembic_app_device_access")
        command.upgrade(config, "device_access_0004")
        command.upgrade(config, "device_access_0005")
        command.upgrade(config, "device_access_0005")
        assert connection.exec_driver_sql("SELECT id FROM unrelated").scalar() == 1
        assert set(inspect(connection).get_table_names()) == {
            "unrelated",
            "alembic_app_device_access",
            *manifest.data["database"]["tables"],
        }
    engine.dispose()


def test_live_connection_rechecks_grants_after_revocation(store):
    from datetime import timedelta

    from src.apps.device_access.backend.models import Connection, now

    grant = store.create("admin", body())
    with store.sessions() as session:
        session.add(
            Connection(
                id="connection",
                owner_id="alice",
                credential_ref="unused",
                settings={"domain": "lab", "address": "10.0.0.1"},
                expires_at=now() + timedelta(minutes=5),
            )
        )
    service.connection("alice", "connection")
    store.set_state("admin", grant["id"], GrantState(revision=1, enabled=False))
    with pytest.raises(DeviceAccessError):
        service.connection("alice", "connection")
