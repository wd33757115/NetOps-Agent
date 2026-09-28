"""Fixed-upstream proxy for package APIs, including binary bodies and SSE."""

from urllib.parse import unquote

import httpx
from asgiref.sync import sync_to_async
from django.conf import settings
from django.http import HttpResponse, StreamingHttpResponse

from ..app_access import decision
from ..decorators import _authenticate_request
from ..response import bff_error
from ..proxy_client import _get_client
from ..sync_async import release_proxy_connections
from ._helpers import forward_client_headers


def valid_path(path):
    # Reject nested encodings too; proxy only relative package paths.
    for _ in range(4):
        decoded = unquote(path)
        if decoded == path:
            break
        path = decoded
    return (
        not any(p in (".", "..") for p in path.split("/"))
        and not any(c in path for c in ("\\", "?", "#", "%", "\x00"))
        and not path.startswith("/")
    )


async def forward(request, app_id, path):
    upstream = (
        settings.FASTAPI_BASE_URL.rstrip("/")
        + f"/api/apps/{app_id}"
        + ("/" + path.rstrip("/") if path else "")
    )
    headers = forward_client_headers(request)
    headers.update(
        {
            "X-Forwarded-From": "django-bff",
            "X-Internal-Request": "true",
            "X-Request-ID": getattr(request, "bff_request_id", ""),
        }
    )
    if len(request.body) > 20 * 1024 * 1024:
        return bff_error("上传内容过大", 413)
    # Reuse the BFF pool; response streams own a connection, not the client.
    client = _get_client()
    try:
        response = await client.send(
            client.build_request(
                request.method,
                upstream,
                headers=headers,
                params=request.GET.urlencode(),
                content=request.body,
                timeout=httpx.Timeout(320, connect=5, pool=10),
            ),
            stream=True,
        )
    except httpx.HTTPError:
        return bff_error("业务服务暂不可用", 502)
    content_type = response.headers.get("content-type", "application/octet-stream")
    allowed = {
        k: v
        for k, v in response.headers.items()
        if k.lower()
        in (
            "content-disposition",
            "cache-control",
            "etag",
            "content-range",
            "accept-ranges",
            "x-content-type-options",
            "content-security-policy",
        )
    }
    if "text/event-stream" in content_type and response.status_code < 400:

        async def chunks():
            try:
                async for chunk in response.aiter_bytes():
                    if await sync_to_async(_stream_decision)(request.user, app_id) != 200:
                        return
                    yield chunk
            finally:
                await response.aclose()

        result = StreamingHttpResponse(
            chunks(), status=response.status_code, content_type=content_type, headers=allowed
        )
        result["X-Accel-Buffering"] = "no"
        return result
    try:
        return HttpResponse(
            await response.aread(),
            status=response.status_code,
            content_type=content_type,
            headers=allowed,
        )
    except httpx.TimeoutException:
        return bff_error("业务服务响应超时", 504)
    except httpx.HTTPError:
        return bff_error("业务服务连接中断", 502)
    finally:
        await response.aclose()


def _stream_decision(user, app_id):
    try:
        return decision(user, app_id)
    finally:
        release_proxy_connections()


def _admit(request, app_id):
    try:
        authenticated = _authenticate_request(request, required=True)
        if isinstance(authenticated, HttpResponse):
            return authenticated
        code = decision(request.user, app_id)
        return bff_error("应用不可用或没有使用权限", code) if code != 200 else None
    finally:
        release_proxy_connections()


async def proxy_app(request, app_id, path=""):
    denied = await sync_to_async(_admit)(request, app_id)
    if denied is not None:
        return denied
    if not valid_path(path):
        return bff_error("无效路径", 400)
    return await forward(request, app_id, path)


# Django 4.2's csrf_exempt does not preserve async view detection.
proxy_app.csrf_exempt = True
