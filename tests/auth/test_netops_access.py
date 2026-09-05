import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from src.auth.dependencies import require_netops_access
from src.auth.models import CurrentUser
from src.auth.netops_access import NETOPS_CONFIGURE, requires_netops_configuration

@pytest.mark.parametrize("method,path,required", [
    ("GET", "/api/v1/devices", False),
    ("POST", "/api/v1/devices", True),
    ("PATCH", "/api/v1/devices/d1", True),
    ("POST", "/api/v1/devices/bulk/delete", True),
    ("GET", "/api/v1/devices/credentials", True),
    ("POST", "/api/v1/devices/patrol/launch", False),
    ("POST", "/api/v1/devices/d1/effective-plan", False),
    ("GET", "/api/parser/check-items/?limit=2", False),
    ("POST", "/api/parser/check-items", True),
    ("GET", "/api/parser/asset-source-jobs", True),
    ("POST", "/api/v1/parser/jobs", True),
    ("POST", "/api/v1/patrol/offline-jobs", False),
])
def test_shared_path_contract(method, path, required):
    assert requires_netops_configuration(method, path) is required

@pytest.mark.parametrize("role,permissions,allowed", [("admin", [], True), ("operator", [], False), ("viewer", [], False), ("operator", [NETOPS_CONFIGURE], True)])
def test_management_capability(role, permissions, allowed):
    user = CurrentUser(user_id="test", username="test", role=role, permissions=permissions)
    request = Request({"type": "http", "method": "POST", "path": "/api/v1/devices", "headers": []})
    with patch("src.auth.dependencies.get_current_user", AsyncMock(return_value=user)):
        if allowed:
            assert asyncio.run(require_netops_access(request)) is user
        else:
            with pytest.raises(HTTPException) as denied:
                asyncio.run(require_netops_access(request))
            assert denied.value.status_code == 403

def test_actual_api_routes_reject_ungranted_operator_before_execution():
    from src.gateway.devices_api import router as devices
    from src.gateway.parser_api import router as parser
    from src.gateway.offline_patrol_api import router as offline
    app = FastAPI()
    for router in (devices, parser, offline):
        app.include_router(router)
    from src.auth.dependencies import get_current_user
    operator = CurrentUser(user_id="test", username="test", role="operator")
    app.dependency_overrides[get_current_user] = lambda: operator
    with patch("src.auth.dependencies.get_current_user", AsyncMock(return_value=operator)), TestClient(app) as client:
        for method, path in [
            ("POST", "/api/v1/devices"), ("DELETE", "/api/v1/devices/test"),
            ("POST", "/api/v1/devices/bulk/apply"), ("GET", "/api/v1/devices/credentials"),
            ("GET", "/api/v1/parser/jobs"), ("POST", "/api/v1/parser/asset-source-jobs"),
        ]:
            response = client.request(method, path, json={})
            assert response.status_code == 403, (path, response.text)
