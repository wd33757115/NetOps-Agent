import shutil
from unittest.mock import patch

import httpx
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.platform.kernel.registry import APPS_ROOT, discover, mount_apps, task_configuration


@pytest.fixture
def manifests(tmp_path):
    # Include actual source and resources: discovery must not import the backend.
    shutil.copytree(
        APPS_ROOT / "official_documents",
        tmp_path / "official_documents",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    return tmp_path


def change(root, **values):
    path = root / "official_documents/app.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data.update(values)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


def test_discovers_complete_package_and_task_schedule():
    assert discover()["official-documents"].data["database"]["mode"] == "legacy"
    modules, routes, schedules = task_configuration()
    assert "src.apps.official_documents.backend.tasks" in modules
    assert routes["execute_document_operation"]["queue"] == "netops.ai"
    assert schedules["app.official-documents.reconcile"]["task"] == "reconcile_document_operations"


def test_invalid_path_version_and_dependency_fail_before_import(manifests):
    change(manifests, frontend={"entry": "../../../outside.tsx"})
    with pytest.raises(ValueError, match="frontend"):
        discover(manifests)


def test_missing_dependency_is_rejected(manifests):
    change(manifests, dependencies={"absent": ">=1"})
    with pytest.raises(ValueError, match="dependency"):
        discover(manifests)


def test_self_dependency_is_rejected(manifests):
    change(manifests, dependencies={"official-documents": ">=1"})
    with pytest.raises(ValueError, match="cycle"):
        discover(manifests)


def test_entrypoint_cannot_escape_package(manifests):
    change(manifests, backend={"router": "os:system"})
    with pytest.raises(ValueError, match="outside package"):
        discover(manifests)


def test_authorization_cannot_be_forged_with_user_headers():
    app = FastAPI()
    mount_apps(app)
    response = TestClient(app).get(
        "/api/apps/official-documents/templates", headers={"X-User-Id": "1", "X-User-Role": "admin"}
    )
    assert response.status_code == 401


def test_gateway_rechecks_authority_and_fails_closed():
    from unittest.mock import AsyncMock

    app = FastAPI()
    mount_apps(app)
    client = TestClient(app)
    with patch("src.platform.authorization.client.httpx.AsyncClient") as factory:
        authority = factory.return_value.__aenter__.return_value
        authority.post = AsyncMock(return_value=httpx.Response(403))
        assert (
            client.get(
                "/api/apps/official-documents/templates", headers={"Authorization": "Bearer valid"}
            ).status_code
            == 403
        )
        authority.post.return_value = httpx.Response(
            200, json={"data": {"user": {"user_id": "1", "username": "reader", "role": "viewer"}}}
        )
        assert (
            client.get(
                "/api/apps/official-documents/templates", headers={"Authorization": "Bearer valid"}
            ).status_code
            == 200
        )
        authority.post.side_effect = httpx.ConnectError("authority unavailable")
        assert (
            client.get(
                "/api/apps/official-documents/templates", headers={"Authorization": "Bearer valid"}
            ).status_code
            == 503
        )
        assert authority.post.call_count == 3


def test_compatibility_imports_preserve_module_identity():
    from src.apps.official_documents.backend import service as new
    from src.core.official_documents import service as old

    assert old is new


def test_package_resources_are_local():
    from src.apps.official_documents.backend.templates import REGISTRY_PATH, catalog
    from src.apps.official_documents.backend.writing.service import get_skill_base_path

    assert REGISTRY_PATH.is_relative_to(APPS_ROOT / "official_documents")
    assert get_skill_base_path().is_relative_to(APPS_ROOT / "official_documents")
    assert catalog()["templates"]


def test_one_application_that_cannot_load_never_stops_the_gateway(monkeypatch):
    """Platform and application readiness are separate: a broken package stays
    unavailable (not mounted, not reported as loaded) while the others serve."""
    from src.platform.kernel import registry
    from src.platform.kernel.runtime import token

    real = registry.load_symbol

    def load(reference):
        if reference.startswith("src.apps.topology."):
            raise ImportError("broken package")
        return real(reference)

    monkeypatch.setattr(registry, "load_symbol", load)
    monkeypatch.setenv("NETOPS_APP_INTERNAL_SECRET", "x" * 32)
    app = FastAPI()
    mount_apps(app)
    paths = {route.path for route in app.routes}
    assert not any(path.startswith("/api/apps/topology/") for path in paths)
    assert any(path.startswith("/api/apps/official-documents/") for path in paths)
    readiness = TestClient(app).get(
        "/_internal/app-packages", headers={"Authorization": "Bearer " + token()}
    ).json()
    assert "topology" not in readiness["apps"] and "official-documents" in readiness["apps"]


def test_workers_skip_an_application_whose_dependencies_are_missing(monkeypatch):
    from src.platform.kernel import dependencies

    real = dependencies.check_dependencies

    def check(manifest, **kwargs):
        if manifest.id == "official-documents":
            raise ImportError("missing distribution")
        return real(manifest, **kwargs)

    monkeypatch.setattr(dependencies, "check_dependencies", check)
    modules, routes, schedules = task_configuration()
    assert "src.apps.official_documents.backend.tasks" not in modules
    assert "execute_document_operation" not in routes
    assert modules  # the other applications' tasks still load
