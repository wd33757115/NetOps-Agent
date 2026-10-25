"""BFF proxy for Device Management APIs."""

from __future__ import annotations

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from ..decorators import require_jwt, require_role_with_viewer_readonly
from ..response import bff_error
from ..proxy_client import proxy_to_fastapi
from ..sync_async import sync_bff_view
from ._helpers import forward_client_headers, parse_json_body


async def _proxy(request: HttpRequest, path: str) -> JsonResponse:
    from src.auth.netops_access import requires_netops_configuration
    if requires_netops_configuration(request.method, "/devices/" + path):
        from .app_proxy import proxy_app
        return await proxy_app(request, "device-management", "devices/" + path.split("?", 1)[0])
    if path.startswith("?"):
        fastapi_path = f"/api/v1/devices{path}"
    elif path:
        fastapi_path = f"/api/v1/devices/{path}".rstrip("/")
    else:
        fastapi_path = "/api/v1/devices"
    return await proxy_to_fastapi(
        method=request.method,
        fastapi_path=fastapi_path,
        request_id=request.bff_request_id,
        data=parse_json_body(request) if request.method in {"POST", "PUT", "PATCH"} else None,
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_devices_collection(request: HttpRequest) -> JsonResponse:
    query = request.META.get("QUERY_STRING", "")
    path = f"?{query}" if query else ""
    return await _proxy(request, path)


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_device_path(request: HttpRequest, path: str) -> JsonResponse:
    query = request.META.get("QUERY_STRING", "")
    target = path + (f"?{query}" if query else "")
    return await _proxy(request, target)


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_patrol_batch_live_events(request: HttpRequest, batch_id: str):
    """SSE proxy for live patrol batch failure / stage events."""
    from ..proxy_client import proxy_stream_to_fastapi

    return await proxy_stream_to_fastapi(
        method="GET",
        fastapi_path=f"/api/v1/devices/patrol/batches/{batch_id}/events",
        request_id=request.bff_request_id,
        extra_headers=forward_client_headers(request),
    )
