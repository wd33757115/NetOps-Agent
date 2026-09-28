"""Host trust isolation, admin review, and password-before-trust regressions."""

from contextlib import contextmanager

import paramiko
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src.apps.device_access.backend import host_key_api
from src.apps.device_access.backend.host_key_api import KeyApproval, KeyReference
from src.apps.device_access.backend.host_keys import HostKeyStore
from src.apps.device_access.backend.models import Base
from src.auth.models import CurrentUser
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

    @contextmanager
    def sessions():
        with Session(engine) as session, session.begin():
            yield session

    yield HostKeyStore(sessions=sessions, authorize=lambda *a: None)
    engine.dispose()


@pytest.fixture(scope="module")
def keys():
    return paramiko.RSAKey.generate(2048), paramiko.RSAKey.generate(2048)


def target(domain="lab", host="10.0.0.1"):
    return Target("ref", host, management_domain=domain)


def approval(key, revision=0, domain="lab", host="10.0.0.1", port=22):
    return KeyApproval(
        domain=domain,
        host=host,
        port=port,
        revision=revision,
        key_type=key.get_name(),
        public_key=key.get_base64(),
        confirm_fingerprint=fingerprint(key),
    )


def test_first_pin_then_repeat_and_scoped_known_hosts(store, keys):
    key, _ = keys
    assert store.policy("lab") is None
    store.set_policy("admin", "lab", "tofu_pinned")
    assert store.verify("alice", target(), 22, key)
    before = store.list()[0]
    assert store.verify("alice", target(), 22, key)
    assert store.list()[0] == before
    assert before["status"] == "pinned"
    assert store.known_hosts("alice", "lab", "10.0.0.1", 22) == known_hosts_line(
        "10.0.0.1", 22, key
    )


def test_strict_unknown_rejected_until_admin_verifies(store, keys):
    key, _ = keys
    store.set_policy("admin", "lab", "strict")
    with pytest.raises(DeviceAccessError) as error:
        store.verify("alice", target(), 22, key)
    assert error.value.failure_code == "host_key_rejected"
    row = store.list()[0]
    assert row["status"] == "pinned"
    with pytest.raises(DeviceAccessError):
        store.known_hosts("alice", "lab", "10.0.0.1", 22)
    store.approve("admin", approval(key, row["revision"]))
    assert store.verify("alice", target(), 22, key)
    assert store.list()[0]["verified_by"] == "admin"


def test_changed_key_never_replaces_trust_or_auto_recovers(store, keys):
    old, new = keys
    store.set_policy("admin", "lab", "tofu_pinned")
    store.verify("alice", target(), 22, old)
    for key in (new, old):
        with pytest.raises(DeviceAccessError) as error:
            store.verify("alice", target(), 22, key)
        assert error.value.failure_code == "host_key_changed"
    row = store.list()[0]
    assert row["fingerprint_sha256"] == fingerprint(old)
    assert row["public_key"] == old.get_base64()
    assert row["observed_fingerprint"] == fingerprint(new)
    with pytest.raises(DeviceAccessError):
        store.known_hosts("alice", "lab", "10.0.0.1", 22)
    with pytest.raises(HTTPException) as conflict:
        store.approve("admin", approval(new, 1))
    assert conflict.value.status_code == 409
    store.approve("admin", approval(new, row["revision"]))
    assert store.verify("alice", target(), 22, new)
    assert store.list()[0]["observed_fingerprint"] is None


def test_revoked_key_is_not_repinned(store, keys):
    key, _ = keys
    store.set_policy("admin", "lab", "tofu_pinned")
    store.verify("alice", target(), 22, key)
    store.revoke("admin", KeyReference(domain="lab", host="10.0.0.1", revision=1))
    with pytest.raises(DeviceAccessError) as error:
        store.verify("alice", target(), 22, key)
    assert error.value.failure_code == "host_key_revoked"
    with pytest.raises(HTTPException):
        store.revoke("admin", KeyReference(domain="lab", host="10.0.0.1", revision=1))


def test_domains_and_ports_never_share_keys(store, keys):
    first, second = keys
    for domain in ("lab", "other"):
        store.set_policy("admin", domain, "tofu_pinned")
    store.verify("alice", target(), 22, first)
    store.verify("alice", target("other"), 22, second)
    store.verify("alice", target(), 2222, second)
    assert len(store.list()) == 3
    assert second.get_base64() in store.known_hosts("alice", "lab", "10.0.0.1", 2222)
    assert first.get_base64() not in store.known_hosts("alice", "other", "10.0.0.1", 22)


def test_authorization_checked_before_any_pin_or_export(store, keys):
    def deny(*args):
        raise DeviceAccessError("not_authorized", "denied")

    store.set_policy("admin", "lab", "tofu_pinned")
    store.authorize = deny
    with pytest.raises(DeviceAccessError, match="denied"):
        store.verify("alice", target(), 22, keys[0])
    assert store.list() == []
    with pytest.raises(DeviceAccessError, match="denied"):
        store.known_hosts("alice", "lab", "10.0.0.1", 22)


def test_offline_import_validates_fingerprint_and_detects_conflict(store, keys):
    body = approval(keys[0])
    body.confirm_fingerprint = fingerprint(keys[1])
    with pytest.raises(DeviceAccessError):
        store.approve("admin", body)
    assert store.list() == []
    store.approve("admin", approval(keys[0]))
    with pytest.raises(HTTPException) as error:
        store.approve("admin", approval(keys[1]))
    assert error.value.status_code == 409
    assert store.list()[0]["public_key"] == keys[0].get_base64()


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


@pytest.mark.parametrize("mode", ["tofu_pinned", "strict", "changed", "revoked"])
def test_real_handshake_checks_database_before_password(store, keys, tmp_path, mode):
    with ssh_server(tmp_path) as (port, known_hosts, _, authentications):
        key = paramiko.HostKeys(str(known_hosts))[f"[127.0.0.1]:{port}"]["ssh-rsa"]
        store.set_policy("admin", "lab", "tofu_pinned" if mode != "strict" else "strict")
        if mode in {"changed", "revoked"}:
            trusted = keys[0] if mode == "changed" else key
            store.verify("alice", target(host="127.0.0.1"), port, trusted)
            if mode == "revoked":
                store.revoke(
                    "admin", KeyReference(domain="lab", host="127.0.0.1", port=port, revision=1)
                )
        driver = ParamikoDriver(
            host_key_verifier=lambda target, port, key: store.verify("alice", target, port, key)
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
        if mode == "tofu_pinned":
            connection = driver.connect(target(host="127.0.0.1"), profile)
            assert "result for display version" in connection.send_command("display version", 2)
            connection.disconnect()
            assert authentications == ["operator"]
        else:
            with pytest.raises(DeviceAccessError) as error:
                driver.connect(target(host="127.0.0.1"), profile)
            assert (
                error.value.failure_code
                == {
                    "strict": "host_key_rejected",
                    "changed": "host_key_changed",
                    "revoked": "host_key_revoked",
                }[mode]
            )
            assert authentications == []


def test_admin_api_and_operator_isolation(store, keys, monkeypatch):
    monkeypatch.setattr(host_key_api, "HostKeyStore", lambda: store)
    app = FastAPI()
    app.include_router(host_key_api.router)
    app.dependency_overrides[host_key_api.actor] = lambda: CurrentUser(
        user_id="admin", username="admin", role="admin"
    )
    with TestClient(app) as http:
        assert (
            http.put("/host-keys/policy", json={"domain": "lab", "policy": "strict"}).status_code
            == 200
        )
        assert (
            http.post("/host-keys/verify", json=approval(keys[0]).model_dump()).status_code == 200
        )
        assert http.get("/host-keys").json()["keys"][0]["status"] == "verified"
        app.dependency_overrides[host_key_api.actor] = lambda: CurrentUser(
            user_id="operator", username="operator", role="operator"
        )
        assert http.get("/host-keys").status_code == 403
        assert (
            http.put(
                "/host-keys/policy", json={"domain": "lab", "policy": "tofu_pinned"}
            ).status_code
            == 403
        )
        assert (
            http.post("/host-keys/verify", json=approval(keys[1], 1).model_dump()).status_code
            == 403
        )
        assert (
            http.post(
                "/host-keys/revoke", json={"domain": "lab", "host": "10.0.0.1", "revision": 1}
            ).status_code
            == 403
        )
