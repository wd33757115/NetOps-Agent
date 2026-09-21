"""Package migration, durable task state, immutable plan/ticket and private artifacts."""

import json
import os
from contextlib import contextmanager
from datetime import timedelta
from io import BytesIO
from uuid import uuid4
from zipfile import ZipFile

import pandas as pd
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, text, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src.apps.firewall_policy.backend import service as svc
from src.apps.firewall_policy.backend import storage as blobs
from src.apps.firewall_policy.backend.models import Run, now
from src.apps.firewall_policy.backend.schemas import PolicyRequest, Request, TicketRequest
from src.platform.kernel.migrations import migrate
from src.platform.kernel.registry import discover


@pytest.fixture
def database():
    if os.getenv("TEST_FIREWALL_POSTGRES") == "1":
        from src.common.config import get_settings

        root = create_engine(get_settings().postgres_url)
        schema = "netops_firewall_test_" + uuid4().hex
        with root.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(
            get_settings().postgres_url, connect_args={"options": "-csearch_path=" + schema}
        )
        try:
            yield engine
        finally:
            engine.dispose()
            assert (
                schema.startswith("netops_firewall_test_")
                and len(schema) == len("netops_firewall_test_") + 32
            )
            with root.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            root.dispose()
    else:
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        yield engine
        engine.dispose()


@pytest.fixture
def environment(monkeypatch, database):
    engine = database
    assert migrate(discover()["firewall-policy"], engine) == "firewall_policy_0001"
    assert migrate(discover()["firewall-policy"], engine, check=True) == "firewall_policy_0001"

    @contextmanager
    def sessions():
        with Session(engine) as session, session.begin():
            yield session

    class MemoryStorage:
        def __init__(self):
            self.objects = {}

        def upload_file(self, key, data, content_type):
            self.objects[key] = data
            return True

        def download_file(self, key):
            return self.objects.get(key)

    memory = MemoryStorage()
    dispatched = []
    monkeypatch.setattr(svc, "sessions", sessions)
    monkeypatch.setattr(blobs, "storage", lambda: memory)
    monkeypatch.setattr(svc, "dispatch", dispatched.append)
    yield memory, dispatched


def inputs(owner="1"):
    buffer = BytesIO()
    pd.DataFrame(
        [["10.0.0.1", "10.1.0.1", "443", "tcp", "permit"]],
        columns=["src_ip", "dst_ip", "port", "proto", "action"],
    ).to_excel(buffer, index=False)
    policy = svc.save_upload(owner, "申请.xlsx", "policy", buffer.getvalue(), uuid4().hex)
    topology = {
        "firewalls": [
            {
                "name": "TEST-FW",
                "type": "华为",
                "security_domains": [
                    {"name": "Trust", "ip_ranges": ["10.0.0.0/24"]},
                    {"name": "Untrust", "ip_ranges": ["10.1.0.0/24"]},
                ],
            }
        ]
    }
    topo = svc.save_upload(
        owner, "拓扑.json", "topology", json.dumps(topology).encode(), uuid4().hex
    )
    return PolicyRequest(
        request_id=uuid4().hex,
        policy_upload_id=policy["id"],
        topology_upload_id=topo["id"],
        ticket_id="CHG-001",
    )


def allow(*args):
    pass


def test_real_policy_ticket_artifacts_share_one_immutable_plan(environment):
    body = inputs()
    submitted = svc.submit_policy("1", body)
    svc.execute_run(submitted["id"], allow)
    result = svc.read("1", submitted["id"])
    assert result["status"] == "completed", result
    assert len(result["artifacts"]) == 3
    assert "object_key" not in json.dumps(result, default=str)
    plan = result["result"]["policy_plan"]
    manifest = result["result"]["manifest"]
    assert plan["commands"]["TEST-FW"] == manifest["scripts"][0]["commands"]
    archive, _, _ = svc.download("1", submitted["id"], "config_zip")
    with ZipFile(BytesIO(archive)) as z:
        assert json.loads(z.read("policy_plan.json")) == plan
        assert z.read("system/TEST-FW.txt").decode("utf-8") == plan["commands"]["TEST-FW"]
    job = svc.submit_ticket(
        "1",
        submitted["id"],
        TicketRequest(request_id="ticket", fields={"ticket_title": "新标题", "assignee": "张工"}),
    )
    svc.execute_run(job["id"], allow)
    final = svc.read("1", submitted["id"])
    assert final["result"] == result["result"]
    child = final["tickets"][0]
    assert child["status"] == "completed", child
    assert child["result"]["policy_plan_id"] == plan["plan_id"]
    from openpyxl import load_workbook

    workbook = load_workbook(BytesIO(svc.download("1", job["id"], "change_excel")[0]))
    values = [str(cell.value) for sheet in workbook for row in sheet for cell in row]
    assert "新标题" in values
    assert plan["commands"]["TEST-FW"] in values
    with pytest.raises(HTTPException) as denied:
        svc.download("2", job["id"], "change_excel")
    assert denied.value.status_code == 404


def test_request_idempotency_conflict_and_owner_isolation(environment):
    body = inputs()
    first = svc.submit_policy("1", body)
    assert svc.submit_policy("1", body)["id"] == first["id"]
    assert svc.history("1")["total"] == 1
    with pytest.raises(HTTPException) as conflict:
        svc.submit_policy("1", body.model_copy(update={"ticket_id": "OTHER"}))
    assert conflict.value.status_code == 409
    with pytest.raises(HTTPException) as missing:
        svc.submit_policy("2", body)
    assert missing.value.status_code == 404
    assert svc.history("2")["total"] == 0


def test_cancel_retry_and_late_completion_cannot_publish(environment, monkeypatch):
    body = inputs()
    run = svc.submit_policy("1", body)
    from src.apps.firewall_policy.backend import engine_adapter

    def cancelled(*args):
        svc.cancel("1", run["id"])
        return {"success": True}, {"config_zip": ("x.zip", "application/zip", b"late")}

    monkeypatch.setattr(engine_adapter, "generate", cancelled)
    svc.execute_run(run["id"], allow)
    final = svc.read("1", run["id"])
    assert final["status"] == "cancelled" and not final["artifacts"]
    retried = svc.retry("1", run["id"], Request(request_id="retry"))
    assert retried["id"] != run["id"] and retried["status"] == "queued"
    assert svc.retry("1", run["id"], Request(request_id="retry"))["id"] == retried["id"]


def test_authority_outage_is_retried_but_revocation_cancels(environment):
    run = svc.submit_policy("1", inputs())

    def unavailable(*args):
        raise HTTPException(503, "offline")

    svc.execute_run(run["id"], unavailable)
    assert svc.read("1", run["id"])["status"] == "queued"
    assert svc.reconcile() == 1

    def revoked(*args):
        raise HTTPException(403, "revoked")

    svc.execute_run(run["id"], revoked)
    assert svc.read("1", run["id"])["status"] == "cancelled"


def test_lease_recovery_bounded_attempts_and_drain(environment):
    run = svc.submit_policy("1", inputs())
    with svc.sessions() as s:
        s.execute(
            update(Run).values(
                status="running", attempts=3, lease_until=now() - timedelta(seconds=1)
            )
        )
    assert svc.reconcile() == 1
    svc.execute_run(run["id"], allow)
    assert svc.read("1", run["id"])["status"] == "failed"
    svc.retry("1", run["id"], Request(request_id="retry"))
    assert svc.pending() == 1
    svc.drain()
    assert svc.pending() == 0


def test_revocation_before_publish_hides_all_artifacts(environment, monkeypatch):
    run = svc.submit_policy("1", inputs())
    from src.apps.firewall_policy.backend import engine_adapter

    def generated(*args):
        return {"success": True}, {"config_zip": ("x.zip", "application/zip", b"data")}

    monkeypatch.setattr(engine_adapter, "generate", generated)
    calls = []

    def revoke_at_publish(*args):
        calls.append(1)
        if len(calls) >= 4:
            raise HTTPException(403, "revoked")

    svc.execute_run(run["id"], revoke_at_publish)
    result = svc.read("1", run["id"])
    assert result["status"] == "cancelled" and not result["artifacts"]
    with pytest.raises(HTTPException):
        svc.download("1", run["id"], "config_zip")


def test_dispatch_outage_leaves_durable_row(environment, monkeypatch):
    from src.apps.firewall_policy.backend import tasks

    monkeypatch.setattr(svc, "dispatch", real_dispatch)

    def offline(*args, **kwargs):
        raise RuntimeError("broker offline")

    monkeypatch.setattr(tasks.execute, "apply_async", offline)
    run = svc.submit_policy("1", inputs())
    assert svc.read("1", run["id"])["status"] == "queued"


real_dispatch = svc.dispatch


@pytest.mark.parametrize(
    "filename,kind,data",
    [("x.xlsx", "policy", b"bad"), ("../x.json", "topology", b"{}"), ("x.json", "topology", b"[]")],
)
def test_reject_invalid_uploads(environment, filename, kind, data):
    with pytest.raises(HTTPException):
        svc.save_upload("1", filename, kind, data, "upload")
    assert not environment[0].objects


def test_upload_idempotency_and_type_validation(environment):
    a = svc.save_upload("1", "x.json", "topology", b"{}", "upload")
    assert svc.save_upload("1", "x.json", "topology", b"{}", "upload") == a
    with pytest.raises(HTTPException) as conflict:
        svc.save_upload("1", "x.json", "topology", b'{"new":1}', "upload")
    assert conflict.value.status_code == 409
    with pytest.raises(ValidationError):
        TicketRequest(request_id="bad", fields={"scripts": []})
    with pytest.raises(ValidationError):
        PolicyRequest(request_id="bad", policy_upload_id="file:///etc/password", ticket_id="CHG")


def test_http_upload_template_task_download_and_revoke(environment):
    from src.apps.firewall_policy.backend.api import actor, router
    from src.auth.models import CurrentUser

    app = FastAPI()
    app.include_router(router, prefix="/api/apps/firewall-policy")
    app.dependency_overrides[actor] = lambda: CurrentUser(
        user_id="1", username="operator", role="user"
    )
    client = TestClient(app)
    root = "/api/apps/firewall-policy"
    assert client.get(root + "/template").content[:2] == b"PK"
    response = client.post(
        root + "/uploads?filename=topology.json&kind=topology&request_id=http", content=b"{}"
    )
    assert response.status_code == 201, response.text
    body = inputs()
    response = client.post(root + "/runs", json=body.model_dump())
    assert response.status_code == 202, response.text
    identity = response.json()["id"]
    svc.execute_run(identity, allow)
    result = client.get(root + "/runs/" + identity).json()
    path = result["artifacts"]["config_zip"]["download_url"]
    response = client.get(path)
    assert response.status_code == 200 and response.content[:2] == b"PK"
    assert response.headers["cache-control"] == "no-store"

    def denied():
        raise HTTPException(403, "revoked")

    app.dependency_overrides[actor] = denied
    assert client.get(path).status_code == 403
    assert client.post(root + "/runs", json=body.model_dump()).status_code == 403


def test_legacy_signed_download_cannot_expose_package_objects(monkeypatch):
    from src.gateway.artifacts_api import router

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    assert (
        client.get(
            "/api/v1/artifacts/download",
            params={"key": blobs.PREFIX + "secret", "exp": 9999999999, "sig": "anything"},
        ).status_code
        == 403
    )
