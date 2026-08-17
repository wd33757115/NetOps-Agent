"""BFF proxy for durable offline patrol jobs and their artifacts."""

from __future__ import annotations

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from ..decorators import require_jwt, require_role
from ..proxy_client import TASK_TIMEOUT, proxy_binary_to_fastapi, proxy_to_fastapi
from ..sync_async import sync_bff_view
from ._helpers import forward_client_headers, parse_json_body


def _upstream_path(path: str, query: str = "") -> str:
    target = "/api/v1/patrol/offline-jobs"
    if path:
        target += f"/{path.strip('/')}"
    if query:
        target += f"?{query}"
    return target


def _patrol_api_path(path: str) -> str:
    return f"/api/v1/patrol/{path.strip('/')}"


async def _proxy(request: HttpRequest, path: str = ""):
    query = request.META.get("QUERY_STRING", "")
    target = _upstream_path(path, query)
    headers = forward_client_headers(request)
    if "/artifacts/" in f"/{path.strip('/')}" and request.method == "GET":
        return await proxy_binary_to_fastapi(
            method="GET",
            fastapi_path=target,
            request_id=request.bff_request_id,
            timeout=TASK_TIMEOUT,
            extra_headers=headers,
        )
    return await proxy_to_fastapi(
        method=request.method,
        fastapi_path=target,
        request_id=request.bff_request_id,
        data=parse_json_body(request) if request.method in {"POST", "PUT", "PATCH"} else None,
        timeout=TASK_TIMEOUT,
        extra_headers=headers,
    )


@csrf_exempt
@require_jwt
@require_role("admin", "operator")
@sync_bff_view
async def proxy_offline_patrol_jobs(request: HttpRequest) -> JsonResponse:
    return await _proxy(request)


@csrf_exempt
@require_jwt
@require_role("admin", "operator")
@sync_bff_view
async def proxy_offline_patrol_job_path(request: HttpRequest, path: str):
    return await _proxy(request, path)


@csrf_exempt
@require_jwt
@require_role("admin", "operator")
@sync_bff_view
async def proxy_patrol_path(request: HttpRequest, path: str) -> JsonResponse:
    return await proxy_to_fastapi(
        method=request.method,
        fastapi_path=_patrol_api_path(path),
        request_id=request.bff_request_id,
        params=(
            dict(request.GET.items())
            if request.method == "GET"
            else None
        ),
        data=(
            parse_json_body(request)
            if request.method in {"POST", "PUT", "PATCH"}
            else None
        ),
        timeout=TASK_TIMEOUT,
        extra_headers=forward_client_headers(request),
    )
