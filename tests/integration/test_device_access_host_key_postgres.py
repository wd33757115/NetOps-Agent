"""Run against a disposable PostgreSQL database named netops_validation_* only."""

import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier, local

import paramiko
import pytest
from alembic import command
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from src.apps.device_access.backend.host_keys import HostKeyStore
from src.apps.device_access.backend.models import HostKey
from src.platform.device_access.contracts import DeviceAccessError, Target
from src.platform.kernel.migrations import configuration
from src.platform.kernel.registry import discover


@pytest.fixture(scope="module")
def engine():
    url = os.getenv("NETOPS_HOSTKEY_TEST_DATABASE_URL")
    if not url:
        pytest.skip("requires a disposable PostgreSQL database")
    parsed = make_url(url)
    assert parsed.drivername.startswith("postgresql")
    assert (parsed.database or "").startswith("netops_validation_")
    value = create_engine(url)
    with value.begin() as connection:
        config, _ = configuration(discover()["device-access"])
        config.attributes.update(connection=connection, version_table="alembic_app_device_access")
        command.upgrade(config, "device_access_0003")
        command.upgrade(config, "device_access_0003")
        assert "app_device_access_host_keys" in inspect(connection).get_table_names()
    yield value
    value.dispose()


@pytest.mark.parametrize("same_key", [True, False])
def test_simultaneous_first_handshakes_pin_exactly_one_key(engine, same_key):
    barrier, thread = Barrier(2), local()
    domain = "same" if same_key else "different"
    keys = [paramiko.RSAKey.generate(2048), paramiko.RSAKey.generate(2048)]
    if same_key:
        keys[1] = keys[0]

    class RacingSession(Session):
        def get(self, entity, *args, **kwargs):
            row = super().get(entity, *args, **kwargs)
            if entity is HostKey and row is None and not getattr(thread, "arrived", False):
                thread.arrived = True
                barrier.wait(timeout=5)
            return row

    @contextmanager
    def sessions():
        with RacingSession(engine) as session, session.begin():
            yield session

    store = HostKeyStore(sessions=sessions, authorize=lambda *a: None)
    store.set_policy("admin", domain, "tofu_pinned")
    target = Target("target", "10.0.0.1", management_domain=domain)

    def attempt(key):
        try:
            return store.verify("alice", target, 22, key)
        except DeviceAccessError as exc:
            return exc.failure_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, keys))
    rows = [row for row in store.list() if row["domain"] == domain]
    assert len(rows) == 1
    if same_key:
        assert results == [True, True]
        assert rows[0]["observed_fingerprint"] is None
    else:
        assert results.count(True) == 1 and results.count("host_key_changed") == 1
        assert rows[0]["observed_fingerprint"] != rows[0]["fingerprint_sha256"]
        for key in keys:
            assert attempt(key) == "host_key_changed"
