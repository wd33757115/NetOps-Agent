"""Gateway/Worker authorization against the Django source of truth; fail closed."""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import httpx
import jwt
from fastapi import HTTPException, Request

from src.auth.models import CurrentUser
from src.platform.http_tls import client_tls_context

_connection: ContextVar[httpx.Client | None] = ContextVar("authority_connection", default=None)


@asynccontextmanager
async def authority_session():
    """One run owns its connection pool; decisions and credentials are never cached.

    asyncio.to_thread propagates this context; HTTPX Client supports concurrent threads.
    No process-global sockets, so Celery forks and separate runs cannot share a pool.
    """
    pending = asyncio.create_task(asyncio.to_thread(httpx.Client, timeout=5, verify=client_tls_context()))
    try:
        client = await asyncio.shield(pending)
    except asyncio.CancelledError:
        client = await pending
        await asyncio.to_thread(client.close)
        raise
    marker = _connection.set(client)
    try:
        yield
    finally:
        _connection.reset(marker)
        await asyncio.to_thread(client.close)


def authority_url():
    from src.common.config import get_settings

    return os.getenv("NETOPS_AUTHORITY_URL", get_settings().NETOPS_AUTHORITY_URL).rstrip("/")


def service_token(user_id: str, app_id: str):
    from src.common.config import get_settings

    secret = os.getenv("NETOPS_APP_INTERNAL_SECRET", get_settings().NETOPS_APP_INTERNAL_SECRET)
    if len(secret) < 32:
        raise HTTPException(503, "应用授权服务凭据未配置")
    now = datetime.now(timezone.utc)
    from src.platform.kernel.runtime import snapshot

    return jwt.encode(
        {
            "sub": str(user_id),
            "app_id": app_id,
            "aud": "netops-app-worker",
            "digest": snapshot().get(app_id),
            "iat": now,
            "exp": now + timedelta(seconds=30),
        },
        secret,
        algorithm="HS256",
    )


def parse_decision(response):
    if response.status_code != 200:
        status = response.status_code if response.status_code in (401, 403, 404) else 503
        raise HTTPException(status, "应用不可用或当前账号没有使用权限")
    try:
        return CurrentUser.model_validate(response.json()["data"]["user"])
    except (KeyError, ValueError, TypeError) as exc:
        raise HTTPException(503, "应用授权响应无效") from exc


def authorize_worker(user_id: str, app_id: str):
    from src.platform.kernel.runtime import token

    try:
        client = _connection.get()
        response = (client.post if client is not None else httpx.post)(
            f"{authority_url()}/api/platform/apps/{app_id}/worker-authorize/",
            headers={
                "Authorization": f"Bearer {service_token(user_id, app_id)}",
                "X-Netops-App-Service": "Bearer " + token(),
            },
            timeout=5,
        )
        return parse_decision(response)
    except httpx.HTTPError as exc:
        raise HTTPException(503, "应用授权服务暂不可用") from exc


@lru_cache
def require_app(app_id: str):
    from src.platform.kernel.runtime import snapshot

    loaded_digest = snapshot().get(app_id, "")

    async def check(request: Request):
        from src.platform.kernel.runtime import token

        # Never accept X-User-* as proof. Django validates the original bearer token
        # and the current user/group/App grant on every request.
        bearer = request.headers.get("authorization", "")
        if not bearer.lower().startswith("bearer "):
            raise HTTPException(401, "请登录后使用应用")
        try:
            async with httpx.AsyncClient(timeout=5, verify=client_tls_context()) as client:
                response = await client.post(
                    f"{authority_url()}/api/platform/apps/{app_id}/authorize/",
                    headers={
                        "Authorization": bearer,
                        "X-Netops-App-Digest": loaded_digest,
                        "X-Netops-App-Service": "Bearer " + token(),
                    },
                )
            user = parse_decision(response)
        except httpx.HTTPError as exc:
            raise HTTPException(503, "应用授权服务暂不可用") from exc
        except ValueError as exc:
            raise HTTPException(503, "应用授权服务凭据未配置") from exc
        request.state.current_user = user
        return user

    return check
