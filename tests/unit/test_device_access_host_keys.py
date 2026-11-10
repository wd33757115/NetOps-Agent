"""Host keys: confirm on first sight, warn on change, check before any password."""

from contextlib import contextmanager

import paramiko
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src.apps.device_access.backend import api
from src.apps.device_access.backend.host_keys import HostKeyStore
from src.apps.device_access.backend.models import Base
from src.auth.models import CurrentUser
from src.infrastructure.db.models import AuditLogRecord
from src.platform.device_access.contracts import ConnectionProfile, DeviceAccessError, Target
from src.platform.device_access.drivers.paramiko_driver import ParamikoDriver
from src.platform.device_access.host_keys import (
    DatabaseHostKeyPolicy,
    fingerprint,
    known_hosts_line,
    parse_public_key,
)
from tests.unit.test_device_access_ssh import ssh_server


@pytest.fixture
def store():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    AuditLogRecord.__table__.create(engine)

    @contextmanager
    def sessions():
        with Session(engine) as session, session.begin():
            yield session

    yield HostKeyStore(sessions=sessions)
    engine.dispose()


@pytest.fixture(scope="module")
def keys():
    return paramiko.RSAKey.generate(2048), paramiko.RSAKey.generate(2048)


def target(host="10.0.0.1"):
    return Target("ref", host)


def code(call):
    with pytest.raises(DeviceAccessError) as error:
        call()
    return error.value.failure_code


def test_terminal_shows_a_first_seen_key_and_trusts_it_after_confirmation(store, keys):
    key, other = keys
    assert code(lambda: store.verify("10.0.0.1", 22, key)) == "host_key_unknown"
    row = store.get("10.0.0.1", 22)
    assert (row["status"], row["fingerprint"]) == ("pending", fingerprint(key))
    assert code(lambda: store.known_hosts("10.0.0.1", 22)) == "host_key_rejected"
    with pytest.raises(HTTPException) as stale:
        store.accept("alice", "10.0.0.1", 22, fingerprint(other))
    assert stale.value.status_code == 409
    assert store.accept("alice", "10.0.0.1", 22, fingerprint(key))["status"] == "trusted"
    assert store.verify("10.0.0.1", 22, key) is True
    assert store.known_hosts("10.0.0.1", 22) == known_hosts_line("10.0.0.1", 22, key)


def test_discovery_records_a_first_seen_key_itself(store, keys):
    key, _ = keys
    assert store.verify("10.0.0.1", 22, key, auto_trust=True) is True
    assert store.get("10.0.0.1", 22)["status"] == "trusted"
    assert store.verify("10.0.0.1", 22, key) is True
    # A key waiting for the terminal user is also recorded by discovery.
    assert code(lambda: store.verify("10.0.0.2", 22, key)) == "host_key_unknown"
    assert store.verify("10.0.0.2", 22, key, auto_trust=True) is True


def test_host_key_observation_uses_current_connection_but_admin_confirmation_does_not(store, keys):
    from src.platform.device_access.governance import SSHOperation

    key, _ = keys
    operation = SSHOperation(
        "operator", "device-access", "saved-id", "10.0.0.1", device_name="核心交换机"
    )
    with operation.activate():
        assert code(lambda: store.verify("10.0.0.1", 22, key)) == "host_key_unknown"
    store.accept("admin", "10.0.0.1", 22, fingerprint(key))
    with store.sessions() as session:
        rows = {row.action: row for row in session.scalars(select(AuditLogRecord))}
        observed = rows["ssh.host_key_observed"]
        assert observed.detail["session_id"] == operation.session_id
        assert observed.detail["target_ref"] == "saved-id"
        assert observed.detail["device_name"] == "核心交换机"
        assert "session_id" not in rows["ssh.host_key_accepted"].detail


@pytest.mark.parametrize("auto_trust", [False, True])
def test_a_changed_key_stops_until_the_user_confirms_it(store, keys, auto_trust):
    key, changed = keys
    store.verify("10.0.0.1", 22, key, auto_trust=True)
    for _ in range(2):
        assert (
            code(lambda: store.verify("10.0.0.1", 22, changed, auto_trust=auto_trust))
            == "host_key_changed"
        )
    row = store.get("10.0.0.1", 22)
    assert row["fingerprint"] == fingerprint(key)
    assert row["observed_fingerprint"] == fingerprint(changed)
    assert code(lambda: store.known_hosts("10.0.0.1", 22)) == "host_key_rejected"
    store.accept("alice", "10.0.0.1", 22, fingerprint(changed))
    assert store.verify("10.0.0.1", 22, changed) is True
    assert code(lambda: store.verify("10.0.0.1", 22, key)) == "host_key_changed"


def test_the_trusted_key_coming_back_clears_the_warning(store, keys):
    key, changed = keys
    store.verify("10.0.0.1", 22, key, auto_trust=True)
    code(lambda: store.verify("10.0.0.1", 22, changed))
    assert store.verify("10.0.0.1", 22, key) is True
    assert store.get("10.0.0.1", 22)["observed_fingerprint"] is None
    with pytest.raises(HTTPException):
        store.accept("alice", "10.0.0.1", 22, fingerprint(changed))


def test_ports_never_share_keys(store, keys):
    key, other = keys
    store.verify("10.0.0.1", 22, key, auto_trust=True)
    store.verify("10.0.0.1", 2222, other, auto_trust=True)
    assert store.get("10.0.0.1", 2222)["fingerprint"] == fingerprint(other)
    with pytest.raises(HTTPException):
        store.get("10.0.0.9", 22)


@pytest.mark.parametrize(
    "key_type,blob",
    [
        ("ssh-rsa", "!!!!"),
        ("unknown", "AAAA"),
        ("ssh-rsa", "AAAA"),
        ("ssh-rsa", "A" * 24000),
        ("ssh-rsa\n", "AAAA"),
    ],
)
def test_invalid_public_keys(key_type, blob):
    with pytest.raises(DeviceAccessError):
        parse_public_key(key_type, blob)


def test_key_roundtrip_and_ipv6_nonstandard_port(keys):
    key = keys[0]
    restored = parse_public_key(key.get_name(), key.get_base64())
    assert fingerprint(restored) == fingerprint(key)
    assert known_hosts_line("2001:db8::1", 2222, key).startswith("[2001:db8::1]:2222 ")
    with pytest.raises(ValueError):
        known_hosts_line("10.0.0.1\nmalicious", 22, key)


def test_callback_host_mismatch_and_false_return_fail_closed(keys):
    profile = ConnectionProfile("profile", "test", port=2222)
    calls = []
    policy = DatabaseHostKeyPolicy(target(), profile, lambda *a: calls.append(a) or False)
    with pytest.raises(DeviceAccessError):
        policy.missing_host_key(None, "10.0.0.1", keys[0])
    assert not calls
    with pytest.raises(DeviceAccessError):
        policy.missing_host_key(None, "[10.0.0.1]:2222", keys[0])
    assert len(calls) == 1


@pytest.mark.parametrize("mode", ["trusted", "unknown", "changed"])
def test_real_handshake_checks_the_key_before_the_password(store, keys, tmp_path, mode):
    with ssh_server(tmp_path) as (port, known_hosts, _, authentications):
        key = paramiko.HostKeys(str(known_hosts))[f"[127.0.0.1]:{port}"]["ssh-rsa"]
        if mode != "unknown":
            store.verify("127.0.0.1", port, key if mode == "trusted" else keys[0], auto_trust=True)
        driver = ParamikoDriver(
            host_key_verifier=lambda target, port, key: store.verify(target.host, port, key)
        )
        # This ambient file contains a valid server key. It MUST NOT bypass the database.
        profile = ConnectionProfile(
            "profile",
            "operator",
            "password",
            port=port,
            known_hosts_file=str(known_hosts),
            session_setup_timeout=4,
        )
        if mode == "trusted":
            connection = driver.connect(target("127.0.0.1"), profile)
            assert "result for display version" in connection.send_command("display version", 2)
            connection.disconnect()
            assert authentications == ["operator"]
        else:
            assert code(lambda: driver.connect(target("127.0.0.1"), profile)) == f"host_key_{mode}"
            assert authentications == []


def test_api_shows_and_accepts_keys_for_operators_only(store, keys, monkeypatch):
    monkeypatch.setattr(api, "HostKeyStore", lambda: store)
    app = FastAPI()
    app.include_router(api.router)
    user = CurrentUser(user_id="operator", username="operator", role="operator")
    app.dependency_overrides[api.actor] = lambda: user
    code(lambda: store.verify("10.0.0.1", 22, keys[0]))
    with TestClient(app) as http:
        shown = http.get("/host-keys/10.0.0.1/22").json()
        assert shown["status"] == "pending" and "public_key" not in shown
        accept = {"host": "10.0.0.1", "port": 22, "fingerprint": shown["fingerprint"]}
        assert http.post("/host-keys/accept", json=accept).json()["status"] == "trusted"
        user.role = "viewer"
        assert http.get("/host-keys/10.0.0.1/22").status_code == 403
        assert http.post("/host-keys/accept", json=accept).status_code == 403
