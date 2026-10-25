# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

import logging

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from ..decorators import require_jwt, require_role
from ..sync_async import sync_bff_view
from ..proxy_client import TASK_TIMEOUT, proxy_to_fastapi
from ..views._helpers import forward_client_headers, parse_json_body

logger = logging.getLogger("bff.views.skills")


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_skills_list(request: HttpRequest) -> JsonResponse:
    headers = forward_client_headers(request)
    if request.method == "GET":
        return await proxy_to_fastapi(
            method="GET",
            fastapi_path="/api/v1/skills",
            request_id=request.bff_request_id,
            extra_headers=headers,
        )
    data = parse_json_body(request)
    return await proxy_to_fastapi(
        method="POST",
        fastapi_path="/api/v1/skills",
        request_id=request.bff_request_id,
        data=data,
        extra_headers=headers,
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_skills_stats(request: HttpRequest) -> JsonResponse:
    return await proxy_to_fastapi(
        method="GET",
        fastapi_path="/api/v1/skills/stats",
        request_id=request.bff_request_id,
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@require_role("admin")
@sync_bff_view
async def proxy_reload_all_skills(request: HttpRequest) -> JsonResponse:
    return await proxy_to_fastapi(
        method="POST",
        fastapi_path="/api/v1/skills/reload-all",
        request_id=request.bff_request_id,
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_skill_content(request: HttpRequest, skill_name: str) -> JsonResponse:
    headers = forward_client_headers(request)
    path = f"/api/v1/skills/{skill_name}/content"
    if request.method == "GET":
        return await proxy_to_fastapi(
            method="GET",
            fastapi_path=path,
            request_id=request.bff_request_id,
            extra_headers=headers,
        )
    data = parse_json_body(request)
    return await proxy_to_fastapi(
        method="PUT",
        fastapi_path=path,
        request_id=request.bff_request_id,
        data=data,
        extra_headers=headers,
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_skill_files(request: HttpRequest, skill_name: str) -> JsonResponse:
    headers = forward_client_headers(request)
    path = f"/api/v1/skills/{skill_name}/files"
    if request.method == "GET":
        return await proxy_to_fastapi(
            method="GET",
            fastapi_path=path,
            request_id=request.bff_request_id,
            extra_headers=headers,
        )
    data = parse_json_body(request)
    return await proxy_to_fastapi(
        method="POST",
        fastapi_path=path,
        request_id=request.bff_request_id,
        data=data,
        extra_headers=headers,
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_skill_toggle(request: HttpRequest, skill_name: str) -> JsonResponse:
    data = parse_json_body(request)
    return await proxy_to_fastapi(
        method="PATCH",
        fastapi_path=f"/api/v1/skills/{skill_name}/toggle",
        request_id=request.bff_request_id,
        data=data,
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@require_role("admin")
@sync_bff_view
async def proxy_skill_reload(request: HttpRequest, skill_name: str) -> JsonResponse:
    return await proxy_to_fastapi(
        method="POST",
        fastapi_path=f"/api/v1/skills/{skill_name}/reload",
        request_id=request.bff_request_id,
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_skill_test_run(request: HttpRequest, skill_name: str) -> JsonResponse:
    return await proxy_to_fastapi(
        method="POST",
        fastapi_path=f"/api/v1/skills/{skill_name}/test-run",
        request_id=request.bff_request_id,
        timeout=330 if skill_name in {"firewall-policy-generator", "itsm-change-ticket-writer"} else TASK_TIMEOUT,
        data=parse_json_body(request),
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_skill_schema(request: HttpRequest, skill_name: str) -> JsonResponse:
    return await proxy_to_fastapi(
        method="GET",
        fastapi_path=f"/api/v1/skills/{skill_name}/schema",
        request_id=request.bff_request_id,
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_skill_detail(request: HttpRequest, skill_name: str) -> JsonResponse:
    headers = forward_client_headers(request)
    path = f"/api/v1/skills/{skill_name}"
    if request.method == "PUT":
        data = parse_json_body(request)
        return await proxy_to_fastapi(
            method="PUT",
            fastapi_path=path,
            request_id=request.bff_request_id,
            data=data,
            extra_headers=headers,
        )
    return await proxy_to_fastapi(
        method="DELETE",
        fastapi_path=path,
        request_id=request.bff_request_id,
        extra_headers=headers,
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_catalog_stats(request: HttpRequest) -> JsonResponse:
    return await proxy_to_fastapi(
        method="GET",
        fastapi_path="/api/v1/skills/catalog/stats",
        request_id=request.bff_request_id,
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@require_role("admin")
@sync_bff_view
async def proxy_catalog_reindex(request: HttpRequest) -> JsonResponse:
    force = request.GET.get("force", "false").lower() in ("1", "true", "yes")
    return await proxy_to_fastapi(
        method="POST",
        fastapi_path=f"/api/v1/skills/catalog/reindex?force={str(force).lower()}",
        request_id=request.bff_request_id,
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@require_role("admin")
@sync_bff_view
async def proxy_catalog_rollout(request: HttpRequest, skill_name: str) -> JsonResponse:
    data = parse_json_body(request)
    return await proxy_to_fastapi(
        method="PATCH",
        fastapi_path=f"/api/v1/skills/catalog/{skill_name}/rollout",
        request_id=request.bff_request_id,
        data=data,
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@require_role("admin")
@sync_bff_view
async def proxy_governance_archive_executions(request: HttpRequest) -> JsonResponse:
    data = parse_json_body(request)
    return await proxy_to_fastapi(
        method="POST",
        fastapi_path="/api/v1/skills/governance/archive-executions",
        request_id=request.bff_request_id,
        data=data,
        extra_headers=forward_client_headers(request),
    )
