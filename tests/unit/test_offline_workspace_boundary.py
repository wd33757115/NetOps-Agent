"""HTTP workspace identity must survive mounting at a different URL."""
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.auth.dependencies import get_current_user, require_netops_access
from src.auth.models import CurrentUser
from src.gateway import offline_job_api


@pytest.fixture
def workspace(monkeypatch):
    actor = CurrentUser(user_id="42", username="operator", role="operator")
    jobs = {
        "source": {"id": "source", "owner_id": "42", "purpose": "rule_asset_source"},
        "patrol": {"id": "patrol", "owner_id": "42", "purpose": "offline_patrol"},
        "private": {"id": "private", "owner_id": "99", "purpose": "rule_asset_source"},
    }
    writes = []
    audits = []

    class Service:
        def get_job(self, job_id, *, owner_id=None, is_admin=False, purpose=None):
            job = jobs[job_id]
            if purpose is not None and purpose != job["purpose"]:
                raise KeyError("job not in this workspace")
            if job["owner_id"] != owner_id and not is_admin:
                raise PermissionError("private job")
            return dict(job)

        def create_job(self, **values):
            writes.append(values)
            return {**values, "id": "new", "folder_id": "existing-private-folder"}

        def list_jobs(self, **values):
            return {"items": [], "scope": values}

        def start_execution(self, **values):
            writes.append(values)
            return {"job_id": values["job_id"], "status": "queued"}

    monkeypatch.setattr(offline_job_api, "OfflinePatrolJobService", Service)
    monkeypatch.setattr(offline_job_api, "write_audit_log", lambda **values: audits.append(values))

    def unexpected_database_access():
        raise AssertionError("HTTP boundary tests must not access the application database")

    monkeypatch.setattr(offline_job_api, "get_db_session", unexpected_database_access)

    def user():
        return actor

    app = FastAPI()
    from src.apps.rule_governance.backend import source_api
    from src.gateway import patrol_offline_api

    def unexpected_business_access():
        raise AssertionError("workspace denial must precede business execution")

    monkeypatch.setattr(source_api, "RuleAssetCommandUnitService", unexpected_business_access)
    monkeypatch.setattr(patrol_offline_api, "PatrolCurrentAuthorityService", unexpected_business_access)
    from src.apps.rule_governance.backend.access import app_user
    app.dependency_overrides[app_user] = user
    app.dependency_overrides[get_current_user] = user
    app.dependency_overrides[require_netops_access] = user
    app.include_router(source_api.router, prefix="/actual/source")
    app.include_router(patrol_offline_api.router, prefix="/actual/patrol")
    for prefix, purpose in (("/renamed/source", "rule_asset_source"), ("/renamed/patrol", "offline_patrol")):
        app.include_router(
            offline_job_api.build_workspace_router(purpose, read_user=user, write_user=user),
            prefix=prefix,
        )
    with TestClient(app) as client:
        yield client, writes, audits


def test_create_uses_bound_purpose_and_authenticated_actor(workspace):
    client, writes, audits = workspace
    response = client.post("/renamed/source", json={
        "job_type": "parser_validation", "name": "CLI evidence", "periods": [],
        "idempotency_key": "source-request-1", "owner_id": "99", "purpose": "offline_patrol",
    })
    assert response.status_code == 201, response.text
    assert writes[0]["owner_id"] == "42"
    assert writes[0]["purpose"] == "rule_asset_source"
    assert audits[0]["user_id"] == "42"
    assert audits[0]["resource_type"] == "rule_asset_source_job"


@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize("job_id,status", [("patrol", 404), ("private", 403)])
def test_scope_blocks_cross_workspace_and_private_data_before_execution(workspace, method, job_id, status):
    client, writes, _ = workspace
    path = f"/renamed/source/{job_id}"
    response = client.get(path) if method == "get" else client.post(path + "/execute", json={
        "idempotency_key": "execute-request-1",
    })
    assert response.status_code == status, response.text
    assert writes == []


@pytest.mark.parametrize("payload", [
    {"job_type": "single_patrol"},
    {"job_type": "parser_validation", "publish_events": True},
])
def test_source_workspace_cannot_request_production_patrol(workspace, payload):
    client, writes, _ = workspace
    response = client.post("/renamed/source", json={
        "name": "sample", "periods": [], "idempotency_key": "source-request-2", **payload,
    })
    assert response.status_code == 400
    assert writes == []


def test_list_cannot_select_another_workspace_by_query(workspace):
    client, _, _ = workspace
    response = client.get("/renamed/patrol?purpose=rule_asset_source&owner_id=99")
    assert response.status_code == 200
    assert response.json()["scope"]["purpose"] == "offline_patrol"
    assert response.json()["scope"]["owner_id"] == "42"


@pytest.mark.parametrize("path,status", [
    ("/actual/source/patrol/command-asset-units", 404),
    ("/actual/source/private/command-asset-units", 403),
    ("/actual/patrol/source/current-authority", 404),
    ("/actual/source/source/current-authority", 404),
    ("/actual/patrol/patrol/command-asset-units", 404),
])
def test_business_routes_inherit_scope_and_stay_in_own_workspace(workspace, path, status):
    client, writes, _ = workspace
    assert client.get(path).status_code == status
    assert writes == []


def test_owner_can_supply_app_admission_before_repository_access(monkeypatch):
    calls = []

    def denied():
        raise HTTPException(403, "application access revoked")

    def user():
        calls.append("read-user")
        return CurrentUser(user_id="42", username="operator")

    app = FastAPI()
    app.include_router(offline_job_api.build_workspace_router(
        "rule_asset_source", read_user=user, write_user=user, admission=denied,
    ), prefix="/source")
    with TestClient(app) as client:
        assert client.get("/source/job").status_code == 403
    assert calls == []
