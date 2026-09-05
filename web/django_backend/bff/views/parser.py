"""BFF proxy for parser control-plane APIs."""

from __future__ import annotations

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from ..decorators import require_jwt, require_role_with_viewer_readonly
from ..proxy_client import proxy_to_fastapi
from ..response import bff_error
from ..sync_async import sync_bff_view
from ._helpers import forward_client_headers, parse_json_body

_ADMIN_ONLY_ACTIONS = {
    "approve",
    "force-activate",
    "force_activate",
}
_ADMIN_ONLY_PATH_MARKERS = (
    "/approve",
    "/force-activate",
    "/force_activate",
)

_REPORT_TIMEOUT = 120
_LONG_RUNNING_REPORT_SUFFIXES = (
    "/replay-summary",
    "/analysis-summary",
)


def _require_admin_for_sensitive(request: HttpRequest, *, action: str | None = None, path: str = "") -> JsonResponse | None:
    role = str(getattr(request, "bff_user_role", "") or "").lower()
    if role == "admin":
        return None
    normalized_action = str(action or "").strip().lower()
    normalized_path = f"/{str(path or '').strip().lower().strip('/')}"
    if normalized_action in _ADMIN_ONLY_ACTIONS:
        return bff_error("admin role required for this parser action", 403)
    if any(marker in normalized_path for marker in _ADMIN_ONLY_PATH_MARKERS):
        return bff_error("admin role required for this parser action", 403)
    return None


async def _proxy(request: HttpRequest, path: str) -> JsonResponse:
    from src.auth.netops_access import NETOPS_CONFIGURE, requires_netops_configuration
    if requires_netops_configuration(request.method, "/parser/" + path):
        if getattr(request, "bff_user_role", "") != "admin" and NETOPS_CONFIGURE not in getattr(request, "bff_user_permissions", []):
            return bff_error("需要设备与规则配置权限，请联系管理员授权", 403)
    normalized_path = path.split("?", 1)[0].rstrip("/")
    timeout = (
        _REPORT_TIMEOUT
        if normalized_path.endswith(_LONG_RUNNING_REPORT_SUFFIXES)
        else None
    )
    return await proxy_to_fastapi(
        method=request.method,
        fastapi_path=f"/api/v1/parser/{path}".rstrip("/"),
        request_id=request.bff_request_id,
        data=(
            parse_json_body(request)
            if request.method in {"POST", "PUT", "PATCH", "DELETE"}
            else None
        ),
        extra_headers=forward_client_headers(request),
        timeout=timeout,
    )


@csrf_exempt
@require_jwt
@require_role_with_viewer_readonly("admin", "operator")
@sync_bff_view
async def proxy_parser_collection(request: HttpRequest, resource: str) -> JsonResponse:
    query = request.META.get("QUERY_STRING", "")
    path = resource + (f"?{query}" if query else "")
    denied = _require_admin_for_sensitive(request, path=path)
    if denied is not None:
        return denied
    return await _proxy(request, path)


@csrf_exempt
@require_jwt
@require_role_with_viewer_readonly("admin", "operator")
@sync_bff_view
async def proxy_parser_action(
    request: HttpRequest, resource: str, item_id: str, action: str
) -> JsonResponse:
    denied = _require_admin_for_sensitive(request, action=action, path=f"{resource}/{item_id}/{action}")
    if denied is not None:
        return denied
    return await _proxy(request, f"{resource}/{item_id}/{action}")


@csrf_exempt
@require_jwt
@require_role_with_viewer_readonly("admin", "operator")
@sync_bff_view
async def proxy_parser_job_detail(request: HttpRequest, job_id: str) -> JsonResponse:
    return await _proxy(request, f"jobs/{job_id}")


@csrf_exempt
@require_jwt
@require_role_with_viewer_readonly("admin", "operator")
@sync_bff_view
async def proxy_parser_path(request: HttpRequest, path: str) -> JsonResponse:
    query = request.META.get("QUERY_STRING", "")
    target = path + (f"?{query}" if query else "")
    denied = _require_admin_for_sensitive(request, path=target)
    if denied is not None:
        return denied
    return await _proxy(request, target)
