"""BFF proxy for system maintenance APIs."""

from __future__ import annotations

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from ..decorators import require_jwt, require_role
from ..proxy_client import proxy_to_fastapi
from ..sync_async import sync_bff_view
from ._helpers import forward_client_headers, parse_json_body


@csrf_exempt
@require_jwt
@require_role("admin")
@sync_bff_view
async def proxy_maintenance_path(request: HttpRequest, path: str) -> JsonResponse:
    query = request.META.get("QUERY_STRING", "")
    target = path + (f"?{query}" if query else "")
    return await proxy_to_fastapi(
        method=request.method,
        fastapi_path=f"/api/v1/maintenance/{target}".rstrip("/"),
        request_id=request.bff_request_id,
        data=parse_json_body(request) if request.method in {"POST", "PUT", "PATCH"} else None,
        extra_headers=forward_client_headers(request),
    )
