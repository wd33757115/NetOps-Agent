"""Use a disposable PostgreSQL database; verify grant revision races."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier

from alembic import command
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.apps.device_access.backend.grants import GrantCreate, GrantState, GrantStore
from src.apps.device_access.backend.models import GrantAudit
from src.platform.kernel.migrations import configuration
from src.platform.kernel.registry import discover
from tests.integration.test_device_access_host_key_postgres import engine  # noqa: F401


def test_concurrent_revision_updates_have_one_winner_and_one_audit(engine):  # noqa: F811
    with engine.begin() as connection:
        config, _ = configuration(discover()["device-access"])
        config.attributes.update(connection=connection, version_table="alembic_app_device_access")
        command.upgrade(config, "device_access_0005")

    @contextmanager
    def sessions():
        with Session(engine) as session, session.begin():
            yield session

    store = GrantStore(sessions=sessions)
    row = store.create(
        "admin",
        GrantCreate(owner_id="race", domain="race", cidr="10.0.0.0/24", action="device.collect"),
    )
    barrier = Barrier(2)

    def change(enabled):
        barrier.wait(timeout=5)
        try:
            return store.set_state("admin", row["id"], GrantState(revision=1, enabled=enabled))
        except HTTPException as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(change, [True, False]))
    assert results.count(409) == 1
    assert store.list()[0]["revision"] == 2
    with sessions() as session:
        assert len(list(session.scalars(select(GrantAudit)))) == 2
