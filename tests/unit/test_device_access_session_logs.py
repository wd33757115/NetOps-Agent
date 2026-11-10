from urllib.parse import quote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.apps.device_access.backend import api
from src.auth.models import CurrentUser
from src.platform.device_access.contracts import DeviceAccessError


@pytest.fixture
def web():
    app = FastAPI()
    app.include_router(api.router)
    user = [CurrentUser(user_id="alice", username="Alice", role="operator")]
    app.dependency_overrides[api.actor] = lambda: user[0]
    with TestClient(app) as client:
        yield client, user


@pytest.fixture
def log_record():
    return {
        "id": "connection-one",
        "filename": "核心交换机_2026-09-30_11-20-35.123456.log",
        "device_name": "核心交换机",
        "host": "192.0.2.1",
        "port": 22,
        "source_app": "device-access",
        "actor_id": "alice",
        "started_at": "2026-09-30T11:20:35.123456+08:00",
        "updated_at": "2026-09-30T11:20:38+08:00",
        "status": "closed",
        "content": "设备：核心交换机\n[11:20:36] display clock\n  retained  columns\n",
    }


def test_list_passes_authenticated_scope_and_cursor(web, log_record, monkeypatch):
    client, users = web
    calls = []

    def listing(**kwargs):
        calls.append(kwargs)
        return {"items": [{k: v for k, v in log_record.items() if k != "content"}], "total": 3}

    monkeypatch.setattr(api.session_logs, "list_logs", listing)
    response = client.get("/session-logs?host=192.0.2.1&limit=1&before=1&actor_id=bob")
    assert response.status_code == 200
    assert response.json()["next_cursor"] == "2"
    assert "content" not in response.json()["items"][0]
    assert calls[-1] == {"actor_id": "alice", "host": "192.0.2.1", "limit": 1, "offset": 1}
    users[0] = CurrentUser(user_id="admin", username="Admin", role="admin")
    assert client.get("/session-logs?before=2").json()["next_cursor"] is None
    assert calls[-1]["actor_id"] is None


@pytest.mark.parametrize("suffix", ["", "/download"])
def test_preview_and_download_enforce_current_owner(web, log_record, monkeypatch, suffix):
    client, users = web
    calls = []

    def read(identifier, *, actor_id):
        calls.append((identifier, actor_id))
        if actor_id != "alice" and actor_id is not None:
            raise DeviceAccessError("session_log_not_found", "日志不存在")
        return log_record

    monkeypatch.setattr(api.session_logs, "read_log", read)
    assert client.get("/session-logs/connection-one" + suffix).status_code == 200
    assert calls[-1] == ("connection-one", "alice")
    users[0] = CurrentUser(user_id="bob", username="Bob", role="viewer")
    denied = client.get("/session-logs/connection-one" + suffix)
    assert denied.status_code == 404
    assert log_record["filename"] not in denied.text
    users[0] = CurrentUser(user_id="admin", username="Admin", role="admin")
    assert client.get("/session-logs/connection-one" + suffix).status_code == 200
    assert calls[-1][1] is None


def test_download_is_utf8_attachment_and_preserves_spacing(web, log_record, monkeypatch):
    client, _ = web
    monkeypatch.setattr(api.session_logs, "read_log", lambda *args, **kwargs: log_record)
    response = client.get("/session-logs/connection-one/download")
    assert response.status_code == 200
    assert response.content == log_record["content"].encode("utf-8")
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert "filename*=UTF-8''" + quote(log_record["filename"], safe="") in response.headers[
        "content-disposition"
    ]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_management_records_keep_actor_scope(web, monkeypatch):
    client, _ = web
    calls = []

    def query(**kwargs):
        calls.append(kwargs)
        return {"items": [], "total": 0}

    monkeypatch.setattr(api.governance, "query_audit", query)
    assert client.get("/audit?management_only=true").status_code == 200
    assert calls[-1]["actor_id"] == "alice"
    assert calls[-1]["management_only"] is True


def test_invalid_page_never_reaches_storage(web, monkeypatch):
    client, _ = web
    monkeypatch.setattr(
        api.session_logs, "list_logs", lambda **kwargs: pytest.fail("unexpected read")
    )
    for query in ("before=-1", "limit=101", "limit=0"):
        assert client.get("/session-logs?" + query).status_code == 422


def test_unavailable_file_reports_error_instead_of_empty_success(web, monkeypatch):
    client, _ = web

    def unavailable(*args, **kwargs):
        raise DeviceAccessError("audit_unavailable", "会话日志文件不可用")

    monkeypatch.setattr(api.session_logs, "read_log", unavailable)
    response = client.get("/session-logs/connection-one/download")
    assert response.status_code == 422
    assert "content-disposition" not in response.headers
