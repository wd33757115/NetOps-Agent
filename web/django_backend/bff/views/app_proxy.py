"""Fixed-upstream proxy for package APIs, including binary bodies and SSE."""

from urllib.parse import unquote

import httpx
from asgiref.sync import sync_to_async
from django.conf import settings
from django.http import HttpResponse, StreamingHttpResponse

from ..app_access import decision
from ..decorators import _authenticate_request
from ..response import bff_error
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
    client = httpx.AsyncClient(timeout=httpx.Timeout(320, connect=5), follow_redirects=False)
    try:
        response = await client.send(
            client.build_request(
                request.method,
                upstream,
                headers=headers,
                params=request.GET.urlencode(),
                content=request.body,
            ),
            stream=True,
        )
    except httpx.HTTPError:
        await client.aclose()
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
                    if await sync_to_async(decision)(request.user, app_id) != 200:
                        return
                    yield chunk
            finally:
                await response.aclose()
                await client.aclose()

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
        await client.aclose()


async def proxy_app(request, app_id, path=""):
    authenticated = await sync_to_async(_authenticate_request)(request, required=True)
    if isinstance(authenticated, HttpResponse):
        return authenticated
    if not valid_path(path):
        return bff_error("无效路径", 400)
    code = await sync_to_async(decision)(request.user, app_id)
    if code != 200:
        return bff_error("应用不可用或没有使用权限", code)
    return await forward(request, app_id, path)


# Django 4.2's csrf_exempt does not preserve async view detection.
proxy_app.csrf_exempt = True
