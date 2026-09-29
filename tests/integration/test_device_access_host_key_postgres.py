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
from src.platform.device_access.contracts import DeviceAccessError
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
        manifest = discover()["device-access"]
        config, _ = configuration(manifest)
        config.attributes.update(connection=connection, version_table="alembic_app_device_access")
        command.upgrade(config, manifest.data["database"]["head"])
        command.upgrade(config, manifest.data["database"]["head"])
        assert "app_device_access_host_keys" in inspect(connection).get_table_names()
    yield value
    value.dispose()


@pytest.mark.parametrize("same_key", [True, False])
def test_simultaneous_first_handshakes_pin_exactly_one_key(engine, same_key):
    barrier, thread = Barrier(2), local()
    host = "10.0.0.1" if same_key else "10.0.0.2"
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

    store = HostKeyStore(sessions=sessions)

    def attempt(key):
        try:
            return store.verify(host, 22, key, auto_trust=True)
        except DeviceAccessError as exc:
            return exc.failure_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, keys))
    row = store.get(host, 22)
    if same_key:
        assert results == [True, True]
        assert row["observed_fingerprint"] is None
    else:
        assert results.count(True) == 1 and results.count("host_key_changed") == 1
        assert row["observed_fingerprint"] != row["fingerprint"]
        for key in keys:
            assert attempt(key) == "host_key_changed"
