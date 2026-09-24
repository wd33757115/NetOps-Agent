"""Route and admission contracts; no database/device/model calls."""
import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from src.apps.device_management.backend import api, access
from src.apps.device_management.backend.router import router
from src.apps.inspection.backend import access as inspection_access


def request(path, method='GET'):
    return Request({'type': 'http', 'method': method, 'path': path, 'headers': [], 'query_string': b''})


def test_package_routes_exclude_patrol_execution_and_rule_generation():
    paths = {route.path for route in router.routes}
    assert '/devices' in paths
    assert '/devices/import/preview' in paths
    assert '/command-profile-bindings/{binding_id}/enable' in paths
    assert not any('/patrol/' in path or '/generation' in path or '/parser/' in path for path in paths)


def test_every_canonical_route_requires_device_app():
    assert all(any(d.dependency is access.app_user for d in route.dependencies) for route in router.routes)
    app = FastAPI()
    app.include_router(router)
    async def denied():
        raise HTTPException(403, 'device App denied')
    app.dependency_overrides[access.app_user] = denied
    with TestClient(app) as client:
        assert client.get('/devices').status_code == 403
        assert client.get('/command-profiles').status_code == 403


def test_legacy_mutation_uses_new_app(monkeypatch):
    device = AsyncMock(return_value='device-user')
    inspection = AsyncMock(side_effect=AssertionError('inspection must not authorize edits'))
    monkeypatch.setattr(access, 'app_user', device)
    monkeypatch.setattr(inspection_access, 'app_user', inspection)
    assert asyncio.run(api.device_access(request('/api/v1/devices/d1', 'PATCH'))) == 'device-user'
    device.assert_awaited_once()


def test_inspection_only_user_can_read_inventory(monkeypatch):
    monkeypatch.setattr(inspection_access, 'app_user', AsyncMock(return_value='inspector'))
    monkeypatch.setattr(access, 'app_user', AsyncMock(side_effect=HTTPException(403)))
    assert asyncio.run(api.device_access(request('/api/v1/devices'))) == 'inspector'


def test_device_user_cannot_launch_inspection_without_inspection_grant(monkeypatch):
    monkeypatch.setattr(inspection_access, 'app_user', AsyncMock(side_effect=HTTPException(403)))
    monkeypatch.setattr(access, 'app_user', AsyncMock(return_value='device-user'))
    with pytest.raises(HTTPException) as caught:
        asyncio.run(api.device_access(request('/api/v1/devices/patrol/launch', 'POST')))
    assert caught.value.status_code == 403
