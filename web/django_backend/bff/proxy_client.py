# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

import json
import logging
from typing import Any

import httpx
from django.conf import settings
from django.http import JsonResponse
from src.platform.http_tls import client_tls_context

from .response import bff_error, bff_success

logger = logging.getLogger("bff.proxy")

FASTAPI_BASE_URL = getattr(settings, "FASTAPI_BASE_URL", "http://localhost:8000")

_client: httpx.AsyncClient | None = None

DEFAULT_TIMEOUT = 30
CHAT_TIMEOUT = 180
HEALTH_TIMEOUT = 30
TASK_TIMEOUT = 30


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(DEFAULT_TIMEOUT),
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
            verify=client_tls_context(),
        )
    return _client


def _build_url(path: str) -> str:
    return f"{FASTAPI_BASE_URL.rstrip('/')}/{path.lstrip('/')}"


def build_fastapi_ws_url(path: str, query_string: bytes = b"") -> str:
    """将 HTTP FastAPI 地址转换为 WebSocket 上游地址。"""
    base = FASTAPI_BASE_URL.rstrip("/")
    if base.startswith("https://"):
        ws_base = "wss://" + base[len("https://") :]
    elif base.startswith("http://"):
        ws_base = "ws://" + base[len("http://") :]
    else:
        ws_base = f"ws://{base}"

    url = f"{ws_base}/{path.lstrip('/')}"
    if query_string:
        url += "?" + query_string.decode("utf-8")
    return url


def _build_headers(request_id: str, extra: dict | None = None) -> dict:
    headers = {
        "X-Request-ID": request_id,
        "X-Forwarded-From": "django-bff",
        "X-Internal-Request": "true",
    }
    if extra:
        headers.update(extra)
    return headers


def _parse_upstream_error_body(body: Any) -> tuple[str, str | None, str | None, dict | None]:
    """解析 FastAPI 统一错误信封，返回 message, code, request_id, raw_body。"""
    if not isinstance(body, dict):
        return str(body), None, None, None

    if body.get("success") is False and isinstance(body.get("error"), dict):
        err = body["error"]
        message = str(err.get("message") or err.get("code") or "Upstream error")
        code = err.get("code")
        req_id = body.get("request_id")
        return message, str(code) if code else None, str(req_id) if req_id else None, body

    if isinstance(body.get("error"), dict):
        err = body["error"]
        message = str(err.get("message") or err.get("code") or "Upstream error")
        code = err.get("code")
        req_id = body.get("request_id")
        return message, str(code) if code else None, str(req_id) if req_id else None, body

    return _extract_upstream_error(body), None, None, body


def _extract_upstream_error(body: Any) -> str:
    if isinstance(body, dict):
        if body.get("success") is False and isinstance(body.get("error"), dict):
            return str(body["error"].get("message") or body["error"].get("code") or "Upstream error")
        if body.get("error") and not isinstance(body.get("error"), dict):
            return str(body["error"])
        detail = body.get("detail")
        if isinstance(detail, list) and detail:
            return str(detail[0].get("msg") if isinstance(detail[0], dict) else detail[0])
        if detail:
            return str(detail)
    return str(body)


async def _proxy_json_response(
    response: httpx.Response,
    request_id: str,
) -> JsonResponse:
    if response.status_code == 204:
        return bff_success(None, status=204)

    try:
        body = response.json()
    except Exception:
        body = {"raw": response.text}

    if response.status_code >= 400:
        message, code, upstream_request_id, raw_body = _parse_upstream_error_body(body)
        return bff_error(
            message,
            response.status_code,
            data=raw_body,
            code=code,
            request_id=upstream_request_id or response.headers.get("X-Request-Id") or response.headers.get("X-Request-ID") or request_id,
        )

    return bff_success(body, status=response.status_code)


async def proxy_request(
    method: str,
    path: str,
    request_id: str,
    data: dict | None = None,
    params: dict | None = None,
    timeout: int | None = None,
    extra_headers: dict | None = None,
    json_response: bool = True,
    files: dict | None = None,
) -> JsonResponse:
    client = _get_client()
    url = _build_url(path)
    headers = _build_headers(request_id, extra_headers)

    req_kwargs: dict[str, Any] = {"headers": headers}
    if timeout is not None:
        req_kwargs["timeout"] = httpx.Timeout(timeout)
    if params:
        req_kwargs["params"] = params
    if files is not None:
        headers.pop("Content-Type", None)
        req_kwargs["files"] = files
        if data is not None:
            req_kwargs["data"] = data
    elif data is not None and method in ("POST", "PUT", "PATCH", "DELETE"):
        req_kwargs["json"] = data

    try:
        response = await client.request(method, url, **req_kwargs)
        if json_response:
            return await _proxy_json_response(response, request_id)
        return bff_success({"raw": response.text}, status=response.status_code)
    except httpx.TimeoutException:
        logger.warning(f"[{request_id}] Upstream timeout: {method} {url}")
        return bff_error(
            f"Upstream service timeout after {timeout or DEFAULT_TIMEOUT}s",
            504,
            code="gateway_timeout",
            request_id=request_id,
        )
    except httpx.ConnectError as exc:
        logger.error(f"[{request_id}] Upstream connection failed: {method} {url} -> {exc}")
        return bff_error(
            f"Upstream service unreachable: {str(exc)}",
            502,
            code="service_unavailable",
            request_id=request_id,
        )
    except Exception as exc:
        logger.error(f"[{request_id}] Proxy error: {method} {url} -> {exc}")
        return bff_error(str(exc), 500, code="internal_error", request_id=request_id)


async def proxy_to_fastapi(
    method: str,
    fastapi_path: str,
    request_id: str,
    data: dict | None = None,
    params: dict | None = None,
    timeout: int | None = None,
    extra_headers: dict | None = None,
) -> JsonResponse:
    return await proxy_request(
        method=method,
        path=fastapi_path,
        request_id=request_id,
        data=data,
        params=params,
        timeout=timeout,
        extra_headers=extra_headers,
    )


async def proxy_stream_to_fastapi(
    method: str,
    fastapi_path: str,
    request_id: str,
    data: dict | None = None,
    timeout: int | None = None,
    extra_headers: dict | None = None,
):
    """流式代理 FastAPI SSE（text/event-stream）。"""
    from django.http import StreamingHttpResponse

    client = _get_client()
    url = _build_url(fastapi_path)
    headers = _build_headers(request_id, extra_headers)
    req_kwargs: dict[str, Any] = {"headers": headers}
    if timeout is not None:
        req_kwargs["timeout"] = httpx.Timeout(timeout)
    if data is not None and method in ("POST", "PUT", "PATCH"):
        req_kwargs["json"] = data

    async def stream_upstream():
        try:
            async with client.stream(method, url, **req_kwargs) as response:
                if response.status_code >= 400:
                    body = await response.aread()
                    try:
                        payload = json.loads(body.decode("utf-8"))
                        message = _extract_upstream_error(payload)
                    except Exception:
                        message = body.decode("utf-8", errors="replace") or "Upstream error"
                    error_event = f"event: error\ndata: {json.dumps({'message': message}, ensure_ascii=False)}\n\n"
                    yield error_event.encode("utf-8")
                    return
                async for chunk in response.aiter_bytes():
                    yield chunk
        except httpx.TimeoutException:
            err = f"event: error\ndata: {json.dumps({'message': 'Upstream timeout'}, ensure_ascii=False)}\n\n"
            yield err.encode("utf-8")
        except httpx.ConnectError as exc:
            err = f"event: error\ndata: {json.dumps({'message': str(exc)}, ensure_ascii=False)}\n\n"
            yield err.encode("utf-8")
        except Exception as exc:
            logger.error(f"[{request_id}] Stream proxy error: {exc}")
            err = f"event: error\ndata: {json.dumps({'message': str(exc)}, ensure_ascii=False)}\n\n"
            yield err.encode("utf-8")

    response = StreamingHttpResponse(stream_upstream(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


async def proxy_binary_to_fastapi(
    method: str,
    fastapi_path: str,
    request_id: str,
    params: dict | None = None,
    timeout: int | None = None,
    extra_headers: dict | None = None,
    data: dict | None = None,
):
    """代理二进制响应（文件下载/预览）。"""
    from django.http import HttpResponse

    client = _get_client()
    url = _build_url(fastapi_path)
    headers = _build_headers(request_id, extra_headers)
    req_kwargs: dict[str, Any] = {"headers": headers}
    if data is not None:
        req_kwargs["json"] = data
    if timeout is not None:
        req_kwargs["timeout"] = httpx.Timeout(timeout)
    if params:
        req_kwargs["params"] = params

    try:
        response = await client.request(method, url, **req_kwargs)
    except httpx.TimeoutException:
        return bff_error(f"Upstream service timeout after {timeout or DEFAULT_TIMEOUT}s", 504)
    except httpx.ConnectError as exc:
        return bff_error(f"Upstream service unreachable: {str(exc)}", 502)
    except Exception as exc:
        logger.error(f"[{request_id}] Binary proxy error: {method} {url} -> {exc}")
        return bff_error(str(exc), 500)

    if response.status_code >= 400:
        return await _proxy_json_response(response, request_id)

    http_resp = HttpResponse(
        response.content,
        status=response.status_code,
        content_type=response.headers.get("content-type", "application/octet-stream"),
    )
    response_header_allowlist = (
        "Content-Disposition",
        "Accept-Ranges",
        "Content-Range",
        "ETag",
        "X-NetOps-Report-Status",
        "X-NetOps-Report-Facts-Status",
        "X-NetOps-Report-Source-Digest",
        "X-NetOps-Report-Release-Digest",
        "X-NetOps-Report-Manifest-Id",
        "X-NetOps-Report-Version",
        "X-NetOps-Raw-Evidence-Set-Digest",
        "X-NetOps-Raw-File-Count",
    )
    for header_name in response_header_allowlist:
        if value := response.headers.get(header_name):
            http_resp[header_name] = value
    return http_resp
