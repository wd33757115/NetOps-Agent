import os
import shutil
from uuid import uuid4

import pytest
import yaml
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from src.platform.kernel.migrations import migrate, version_table
from src.platform.kernel.readiness import check_worker_reports
from src.platform.kernel.registry import APPS_ROOT, discover, package_digest


@pytest.fixture
def copied(tmp_path):
    target = tmp_path / "apps/delivery_probe"
    shutil.copytree(
        APPS_ROOT / "delivery_probe", target, ignore=shutil.ignore_patterns("__pycache__")
    )
    return target


@pytest.fixture
def engine(tmp_path):
    if os.getenv("TEST_PACKAGE_POSTGRES") == "1":
        from src.common.config import get_settings

        root = create_engine(get_settings().postgres_url)
        schema = "netops_p2_test_" + uuid4().hex
        with root.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        value = create_engine(
            get_settings().postgres_url, connect_args={"options": "-csearch_path=" + schema}
        )
        try:
            yield value
        finally:
            value.dispose()
            # Only the unique schema created by this fixture can be removed.
            assert schema.startswith("netops_p2_test_") and len(schema) == 47
            with root.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            root.dispose()
    else:
        value = create_engine("sqlite:///" + str(tmp_path / "business.sqlite3"))
        yield value
        value.dispose()


def manifest(copied):
    return discover(copied.parent)["delivery-probe"]


def test_copy_package_fresh_install_and_idempotent_upgrade(copied, engine):
    package = manifest(copied)
    assert package.digest == discover()["delivery-probe"].digest
    assert migrate(package, engine) == "delivery_probe_0002"
    assert migrate(package, engine) == "delivery_probe_0002"
    assert migrate(package, engine, check=True) == "delivery_probe_0002"
    assert set(inspect(engine).get_table_names()) == {
        "app_delivery_probe_runs",
        "alembic_app_delivery_probe",
    }


def test_upgrade_preserves_preexisting_rows(copied, engine):
    second = copied / "migrations/versions/0002_result.py"
    code = second.read_text()
    second.unlink()
    path = copied / "app.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["database"]["head"] = "delivery_probe_0001"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    migrate(manifest(copied), engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO app_delivery_probe_runs (id, owner_id, request_id, text, status) "
                "VALUES ('1','1','old','before upgrade','completed')"
            )
        )
    second.write_text(code)
    data["database"]["head"] = "delivery_probe_0002"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    migrate(manifest(copied), engine)
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT text, result FROM app_delivery_probe_runs")
        ).one() == ("before upgrade", None)


def test_failed_migration_rolls_back_ddl_and_can_retry(copied, engine):
    package = manifest(copied)
    migrate(package, engine)
    path = copied / "migrations/versions/0003_failure.py"
    path.write_text(
        "from alembic import op\nimport sqlalchemy as sa\nrevision='delivery_probe_0003'\n"
        "down_revision='delivery_probe_0002'\ndef upgrade():\n"
        "    op.add_column('app_delivery_probe_runs', sa.Column('attempted', sa.String(20)))\n"
        "    raise RuntimeError('injected failure')\n"
    )
    data = yaml.safe_load((copied / "app.yaml").read_text(encoding="utf-8"))
    data["database"]["head"] = "delivery_probe_0003"
    (copied / "app.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    package = manifest(copied)
    with pytest.raises(RuntimeError, match="injected failure"):
        migrate(package, engine)
    assert "attempted" not in {
        c["name"] for c in inspect(engine).get_columns("app_delivery_probe_runs")
    }
    with engine.connect() as connection:
        assert MigrationContext.configure(
            connection, opts={"version_table": version_table(package)}
        ).get_current_heads() == ("delivery_probe_0002",)
    path.write_text(
        path.read_text().replace("    raise RuntimeError('injected failure')", "    pass")
    )
    migrate(manifest(copied), engine)
    assert "attempted" in {
        c["name"] for c in inspect(engine).get_columns("app_delivery_probe_runs")
    }


def test_existing_unowned_table_cannot_be_stamped(copied, engine):
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE app_delivery_probe_runs (id INTEGER)"))
    with pytest.raises(ValueError, match="already exist"):
        migrate(manifest(copied), engine)


def test_source_fingerprint_ignores_caches_but_not_code(copied):
    before = package_digest(copied)
    cache = copied / "__pycache__"
    cache.mkdir(exist_ok=True)
    (cache / "noise.pyc").write_bytes(b"x")
    assert package_digest(copied) == before
    code = copied / "backend/api.py"
    code.write_bytes(code.read_bytes().replace(b"\n", b"\r\n"))
    assert package_digest(copied) == before
    code.write_bytes(code.read_bytes() + b"\n# changed\n")
    assert package_digest(copied) != before


def test_mixed_worker_versions_cannot_enable():
    package = discover()["delivery-probe"]
    queues = {"new": [{"name": "app.delivery-probe"}], "old": [{"name": "app.delivery-probe"}]}
    good = {"apps": {package.id: package.digest}, "tasks": package.data["backend"]["task_names"]}
    assert check_worker_reports(package, {"new": queues["new"]}, {"new": good}) == ["new"]
    with pytest.raises(ValueError, match="mismatch"):
        check_worker_reports(package, queues, {"new": good})
    with pytest.raises(ValueError, match="No Worker"):
        check_worker_reports(package, {}, {})


def test_api_tools_and_durable_task_share_copied_package(engine, monkeypatch):
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient

    from src.apps.delivery_probe.backend import api, service
    from src.auth.models import CurrentUser
    from src.platform.tools.registry import invoke

    package = discover()["delivery-probe"]
    migrate(package, engine)
    monkeypatch.setattr("src.platform.kernel.migrations.business_engine", lambda: engine)
    monkeypatch.setattr(service, "dispatch", lambda _: None)
    actor = CurrentUser(user_id="alice", username="alice")
    monkeypatch.setattr("src.platform.tools.registry.authorize_worker", lambda *a: actor)
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.user] = lambda: actor
    client = TestClient(app)
    first = client.post("/runs", json={"request_id": "one", "text": "NetOps"}).json()
    again = invoke(
        "delivery_probe__submit", {"request_id": "one", "text": "NetOps"}, actor_id="alice"
    )
    assert first["id"] == again["id"]
    service.execute_run(first["id"], lambda *a: actor)
    service.execute_run(first["id"], lambda *a: actor)
    assert client.get("/runs/" + first["id"]).json()["result"] == "NETOPS"
    queued = service.submit(actor, service.Submit(request_id="two", text="cancel"))
    assert service.pending() == 1
    service.drain()
    assert service.pending() == 0
    assert service.read(actor, queued["id"])["status"] == "cancelled"
    with pytest.raises(HTTPException):
        service.read(CurrentUser(user_id="bob", username="bob"), first["id"])


def test_frontend_metadata_cannot_hide_modified_assets(monkeypatch):
    import hashlib
    from dataclasses import replace

    import httpx

    from src.platform.kernel.readiness import probe

    package = discover()["delivery-probe"]
    package = replace(package, data={**package.data, "backend": {}})
    monkeypatch.setenv("NETOPS_APP_INTERNAL_SECRET", "test-deployment-credential-long-enough")
    asset = b"built application"
    corrupt = False

    def respond(request):
        if request.url.path == "/_internal/app-packages":
            payload = {"apps": {package.id: package.digest}}
        elif request.url.path == "/netops-apps.json":
            payload = {
                "mode": "production",
                "apps": {package.id: package.digest},
                "assets": {"index.html": hashlib.sha256(asset).hexdigest()},
            }
        else:
            return httpx.Response(200, content=b"stale application" if corrupt else asset)
        return httpx.Response(200, json=payload)

    factory = httpx.Client
    monkeypatch.setattr(
        "src.platform.kernel.readiness.httpx.Client",
        lambda **kwargs: factory(transport=httpx.MockTransport(respond), **kwargs),
    )
    probe(package, ["http://gateway"], "http://frontend")
    corrupt = True
    with pytest.raises(ValueError, match="asset mismatch"):
        probe(package, ["http://gateway"], "http://frontend")


def test_gateway_readiness_requires_service_credential_and_frozen_snapshot(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.platform.kernel.runtime import mount_readiness, token

    package = discover()["delivery-probe"]
    app = FastAPI()
    mount_readiness(app, {package.id: package})
    client = TestClient(app)
    monkeypatch.setenv("NETOPS_APP_INTERNAL_SECRET", "test-deployment-credential-long-enough")
    assert client.get("/_internal/app-packages").status_code == 401
    response = client.get("/_internal/app-packages", headers={"Authorization": "Bearer " + token()})
    assert response.json()["apps"] == {package.id: package.digest}
    assert response.json()["active"] == {package.id: 0}


def test_beat_does_not_dispatch_disabled_package(monkeypatch, tmp_path):
    from unittest.mock import patch

    import httpx
    from celery import Celery
    from celery.beat import PersistentScheduler, ScheduleEntry

    from src.platform.kernel.scheduler import AppScheduler

    monkeypatch.setenv("NETOPS_APP_INTERNAL_SECRET", "test-deployment-credential-long-enough")
    app = Celery("scheduler-test", broker="memory://")
    scheduler = AppScheduler(app=app, lazy=True, schedule_filename=str(tmp_path / "schedule"))
    entry = ScheduleEntry(
        name="app.delivery-probe.reconcile",
        task="app.delivery_probe.reconcile",
        schedule=30,
        app=app,
    )
    with (
        patch.object(scheduler, "reserve") as reserve,
        patch.object(PersistentScheduler, "apply_async") as send,
    ):
        monkeypatch.setattr(
            "src.platform.kernel.scheduler.httpx.post", lambda *a, **k: httpx.Response(404)
        )
        scheduler.apply_async(entry)
        reserve.assert_called_once()
        send.assert_not_called()
        monkeypatch.setattr(
            "src.platform.kernel.scheduler.httpx.post", lambda *a, **k: httpx.Response(200)
        )
        scheduler.apply_async(entry)
        send.assert_called_once()


def test_python_build_constraints_reject_conflicting_package_pins(tmp_path):
    from src.platform.kernel.build_constraints import collect

    for name, version in [("first", "1.0"), ("second", "2.0")]:
        directory = tmp_path / name
        directory.mkdir()
        (directory / "app.yaml").write_text("manifest_version: 1")
        (directory / "requirements.lock").write_text("shared-lib==" + version)
    with pytest.raises(ValueError, match="Conflicting"):
        collect(tmp_path)
