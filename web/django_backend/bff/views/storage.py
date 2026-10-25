# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

import logging

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from ..decorators import require_jwt
from ..proxy_client import proxy_binary_to_fastapi, proxy_to_fastapi
from ..sync_async import sync_bff_view
from ..views._helpers import forward_client_headers, parse_json_body

logger = logging.getLogger("bff.views.storage")


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_storage_health(request: HttpRequest) -> JsonResponse:
    return await proxy_to_fastapi(
        method="GET",
        fastapi_path="/api/v1/storage/health",
        request_id=request.bff_request_id,
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_storage_teams(request: HttpRequest) -> JsonResponse:
    headers = forward_client_headers(request)
    if request.method == "GET":
        return await proxy_to_fastapi(
            method="GET",
            fastapi_path="/api/v1/storage/teams",
            request_id=request.bff_request_id,
            extra_headers=headers,
        )
    data = parse_json_body(request)
    return await proxy_to_fastapi(
        method="POST",
        fastapi_path="/api/v1/storage/teams",
        request_id=request.bff_request_id,
        data=data,
        extra_headers=headers,
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_storage_team_detail(request: HttpRequest, team_id: str) -> JsonResponse:
    return await proxy_to_fastapi(
        method="DELETE",
        fastapi_path=f"/api/v1/storage/teams/{team_id}",
        request_id=request.bff_request_id,
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_storage_team_members(request: HttpRequest, team_id: str) -> JsonResponse:
    headers = forward_client_headers(request)
    if request.method == "GET":
        return await proxy_to_fastapi(
            method="GET",
            fastapi_path=f"/api/v1/storage/teams/{team_id}/members",
            request_id=request.bff_request_id,
            extra_headers=headers,
        )
    data = parse_json_body(request)
    return await proxy_to_fastapi(
        method="POST",
        fastapi_path=f"/api/v1/storage/teams/{team_id}/members",
        request_id=request.bff_request_id,
        data=data,
        extra_headers=headers,
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_storage_team_member_detail(
    request: HttpRequest, team_id: str, member_user_id: str
) -> JsonResponse:
    headers = forward_client_headers(request)
    if request.method == "PATCH":
        data = parse_json_body(request)
        return await proxy_to_fastapi(
            method="PATCH",
            fastapi_path=f"/api/v1/storage/teams/{team_id}/members/{member_user_id}",
            request_id=request.bff_request_id,
            data=data,
            extra_headers=headers,
        )
    return await proxy_to_fastapi(
        method="DELETE",
        fastapi_path=f"/api/v1/storage/teams/{team_id}/members/{member_user_id}",
        request_id=request.bff_request_id,
        extra_headers=headers,
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_storage_folders(request: HttpRequest) -> JsonResponse:
    data = parse_json_body(request)
    return await proxy_to_fastapi(
        method="POST",
        fastapi_path="/api/v1/storage/folders",
        request_id=request.bff_request_id,
        data=data,
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_storage_folder_detail(request: HttpRequest, folder_id: str) -> JsonResponse:
    headers = forward_client_headers(request)
    if request.method == "PATCH":
        data = parse_json_body(request)
        return await proxy_to_fastapi(
            method="PATCH",
            fastapi_path=f"/api/v1/storage/folders/{folder_id}",
            request_id=request.bff_request_id,
            data=data,
            extra_headers=headers,
        )
    return await proxy_to_fastapi(
        method="DELETE",
        fastapi_path=f"/api/v1/storage/folders/{folder_id}",
        request_id=request.bff_request_id,
        extra_headers=headers,
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_storage_folder_move(request: HttpRequest, folder_id: str) -> JsonResponse:
    data = parse_json_body(request)
    return await proxy_to_fastapi(
        method="POST",
        fastapi_path=f"/api/v1/storage/folders/{folder_id}/move",
        request_id=request.bff_request_id,
        data=data,
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_storage_folder_tree(request: HttpRequest) -> JsonResponse:
    qs = request.META.get("QUERY_STRING", "")
    path = "/api/v1/storage/folders/tree"
    if qs:
        path = f"{path}?{qs}"
    return await proxy_to_fastapi(
        method="GET",
        fastapi_path=path,
        request_id=request.bff_request_id,
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_storage_list(request: HttpRequest) -> JsonResponse:
    qs = request.META.get("QUERY_STRING", "")
    path = "/api/v1/storage/list"
    if qs:
        path = f"{path}?{qs}"
    return await proxy_to_fastapi(
        method="GET",
        fastapi_path=path,
        request_id=request.bff_request_id,
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_storage_upload_init(request: HttpRequest) -> JsonResponse:
    data = parse_json_body(request)
    return await proxy_to_fastapi(
        method="POST",
        fastapi_path="/api/v1/storage/upload/init",
        request_id=request.bff_request_id,
        data=data,
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_storage_upload_complete(request: HttpRequest) -> JsonResponse:
    data = parse_json_body(request)
    return await proxy_to_fastapi(
        method="POST",
        fastapi_path="/api/v1/storage/upload/complete",
        request_id=request.bff_request_id,
        data=data,
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_storage_file_content(request: HttpRequest, file_id: str) -> JsonResponse:
    qs = request.META.get("QUERY_STRING", "")
    path = f"/api/v1/storage/files/{file_id}/content"
    if qs:
        path = f"{path}?{qs}"
    return await proxy_binary_to_fastapi(
        method="GET",
        fastapi_path=path,
        request_id=request.bff_request_id,
        extra_headers=forward_client_headers(request),
        timeout=120,
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_storage_file_download(request: HttpRequest, file_id: str) -> JsonResponse:
    return await proxy_to_fastapi(
        method="GET",
        fastapi_path=f"/api/v1/storage/files/{file_id}/download",
        request_id=request.bff_request_id,
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_storage_file_detail(request: HttpRequest, file_id: str) -> JsonResponse:
    headers = forward_client_headers(request)
    if request.method == "PATCH":
        data = parse_json_body(request)
        return await proxy_to_fastapi(
            method="PATCH",
            fastapi_path=f"/api/v1/storage/files/{file_id}",
            request_id=request.bff_request_id,
            data=data,
            extra_headers=headers,
        )
    return await proxy_to_fastapi(
        method="DELETE",
        fastapi_path=f"/api/v1/storage/files/{file_id}",
        request_id=request.bff_request_id,
        extra_headers=headers,
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_storage_file_copy(request: HttpRequest, file_id: str) -> JsonResponse:
    data = parse_json_body(request)
    return await proxy_to_fastapi(
        method="POST",
        fastapi_path=f"/api/v1/storage/files/{file_id}/copy",
        request_id=request.bff_request_id,
        data=data,
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_storage_file_move(request: HttpRequest, file_id: str) -> JsonResponse:
    data = parse_json_body(request)
    return await proxy_to_fastapi(
        method="POST",
        fastapi_path=f"/api/v1/storage/files/{file_id}/move",
        request_id=request.bff_request_id,
        data=data,
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_storage_share(request: HttpRequest) -> JsonResponse:
    data = parse_json_body(request)
    return await proxy_to_fastapi(
        method="POST",
        fastapi_path="/api/v1/storage/share",
        request_id=request.bff_request_id,
        data=data,
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_storage_share_folder(request: HttpRequest) -> JsonResponse:
    data = parse_json_body(request)
    return await proxy_to_fastapi(
        method="POST",
        fastapi_path="/api/v1/storage/share/folder",
        request_id=request.bff_request_id,
        data=data,
        extra_headers=forward_client_headers(request),
    )
