"""Authenticated deployment inspection; snapshots are captured at process startup."""

import os
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from threading import Lock

import jwt

_active = Counter()
_active_lock = Lock()


@contextmanager
def invocation(app_id):
    with _active_lock:
        _active[app_id] += 1
    try:
        yield
    finally:
        with _active_lock:
            _active[app_id] -= 1


def active_snapshot(app_ids):
    with _active_lock:
        return {app_id: _active[app_id] for app_id in app_ids}


def secret():
    value = os.getenv("NETOPS_APP_INTERNAL_SECRET", "")
    if len(value) < 32:
        raise ValueError("NETOPS_APP_INTERNAL_SECRET is not configured")
    return value


def token():
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {"aud": "netops-app-deployment", "iat": now, "exp": now + timedelta(seconds=30)},
        secret(),
        algorithm="HS256",
    )


def authenticate(header):
    if not header.startswith("Bearer "):
        raise ValueError("Missing deployment credential")
    jwt.decode(
        header[7:],
        secret(),
        algorithms=["HS256"],
        audience="netops-app-deployment",
        options={"require": ["exp", "iat", "aud"]},
    )


@lru_cache
def snapshot():
    from .registry import discover

    return {m.id: m.digest for m in discover().values()}


def dependency_errors(manifests):
    from .dependencies import check_dependencies

    errors = {}
    for m in manifests.values():
        try:
            check_dependencies(m)
        except Exception as exc:
            errors[m.id] = type(exc).__name__
    return errors


def mount_readiness(app, manifests):
    from fastapi import HTTPException, Request

    # Do not rediscover files for this endpoint: imported code may be older.
    loaded = {m.id: m.digest for m in manifests.values()}
    errors = dependency_errors(manifests)

    # ASGI middleware counts the entire streaming response lifetime.
    class ActiveRequests:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            app_id = None
            if scope["type"] == "http":
                path = scope.get("path", "")
                if path.startswith("/api/apps/"):
                    app_id = path.split("/")[3]
                elif path.startswith("/api/v1/official-documents"):
                    app_id = "official-documents"
                elif path.startswith(("/api/v1/chat", "/api/v1/conversations", "/api/chat/")):
                    app_id = "assistant"
                elif path.startswith("/api/v1/parser"):
                    app_id = "rule-governance"
                elif path.startswith(("/api/v1/patrol", "/api/v1/workflows/runs/patrol-batches")):
                    app_id = "inspection"
                elif path.startswith("/api/v1/devices"):
                    from src.auth.netops_access import requires_netops_configuration
                    if requires_netops_configuration(scope.get("method", "GET"), path):
                        app_id = "rule-governance"
                    else:
                        app_id = "inspection"
            if app_id not in loaded:
                return await self.app(scope, receive, send)
            with invocation(app_id):
                await self.app(scope, receive, send)

    app.add_middleware(ActiveRequests)

    @app.get("/_internal/app-packages", include_in_schema=False)
    def readiness(request: Request):
        try:
            authenticate(request.headers.get("authorization", ""))
        except (ValueError, jwt.InvalidTokenError) as exc:
            raise HTTPException(401, "Invalid deployment credential") from exc
        return {
            "role": "gateway",
            "apps": loaded,
            "active": active_snapshot(loaded),
            "dependency_errors": errors,
        }


def register_worker_inspection():
    from celery.worker.control import inspect_command

    loaded = snapshot()
    from .registry import discover

    errors = dependency_errors(discover())

    @inspect_command(name="app_packages")
    def app_packages(state, **kwargs):
        return {
            "dependency_errors": errors,
            "apps": loaded,
            "queues": sorted(q.name for q in state.consumer.task_consumer.queues),
            "tasks": sorted(state.app.tasks),
        }
