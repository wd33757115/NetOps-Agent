"""Shared facts migration and concurrent candidate creation on disposable PostgreSQL."""

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from threading import Barrier

import pytest
from alembic import command
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from src.apps.topology.backend.facts.models import Device
from src.apps.topology.backend.facts.repository import FactInput, FactRepository
from src.auth.models import CurrentUser
from src.platform.kernel.migrations import configuration
from src.platform.kernel.registry import discover


def test_shared_candidates_and_complete_view_in_postgres():
    url = os.getenv("NETOPS_TWIN_TEST_DATABASE_URL")
    if not url:
        pytest.skip("requires disposable PostgreSQL")
    parsed = make_url(url)
    assert parsed.drivername.startswith("postgresql")
    assert parsed.database.startswith("netops_validation_")
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            config, _ = configuration(discover()["topology"])
            config.attributes.update(connection=connection, version_table="alembic_app_topology")
            command.upgrade(config, "topology_0002")
            command.upgrade(config, "topology_0002")

        @contextmanager
        def sessions():
            with Session(engine) as session, session.begin():
                yield session

        evidence = {}

        def repo(owner):
            return FactRepository(
                CurrentUser(user_id=owner, username=owner),
                sessions=sessions,
                evidence_reader=evidence.__getitem__,
            )

        barrier = Barrier(2)

        def create(owner):
            barrier.wait(timeout=5)
            return repo(owner).create_device("lab", ["10.10.1.1"])["device_uid"]

        with ThreadPoolExecutor(max_workers=2) as pool:
            uids = list(pool.map(create, ["alice", "bob"]))
        assert uids[0] == uids[1]
        uid = uids[0]
        with sessions() as session:
            assert session.scalar(select(func.count()).select_from(Device)) == 1
        payload = {"domain": "lab", "device_uid": uid, "outputs": {"version": "H3C lab"}}
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        reference = f"apps/topology/facts/{uid}/pg/{digest}.json"
        evidence[reference] = payload
        body = FactInput(
            capability="version",
            observed_at=datetime.now(timezone.utc),
            status="complete",
            source="offline",
            platform_profile="h3c",
            parser_ref="pg-test",
            evidence_ref=reference,
            summary={"hostname": "H3C"},
        )
        first = repo("alice").append("lab", uid, body)
        repo("bob").append("lab", uid, body.model_copy(update={"status": "failed", "summary": {}}))
        view = repo("bob").current("lab", uid, "version")
        assert view["current"]["fact_set"]["id"] == first["id"]
        assert view["latest_attempt"]["status"] == "failed"
    finally:
        engine.dispose()
