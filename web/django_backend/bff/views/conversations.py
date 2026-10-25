# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

import logging

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from ..decorators import require_jwt
from ..proxy_client import DEFAULT_TIMEOUT, proxy_to_fastapi
from ..sync_async import sync_bff_view
from ._helpers import forward_client_headers, inject_user_into_body, parse_json_body

logger = logging.getLogger("bff.views.conversations")


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_conversations(request: HttpRequest) -> JsonResponse:
    headers = forward_client_headers(request)
    if request.method == "GET":
        params = {}
        user = getattr(request, "user", None)
        if user and getattr(user, "is_authenticated", False):
            params["user_id"] = str(user.id)
        elif request.GET.get("user_id"):
            params["user_id"] = request.GET.get("user_id")
        if request.GET.get("limit"):
            params["limit"] = request.GET.get("limit")
        if request.GET.get("offset"):
            params["offset"] = request.GET.get("offset")
        return await proxy_to_fastapi(
            method="GET",
            fastapi_path="/api/v1/conversations",
            request_id=request.bff_request_id,
            params=params,
            extra_headers=headers,
        )

    data = inject_user_into_body(request, parse_json_body(request))
    return await proxy_to_fastapi(
        method="POST",
        fastapi_path="/api/v1/conversations",
        request_id=request.bff_request_id,
        data=data,
        extra_headers=headers,
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_conversation_detail(request: HttpRequest, conversation_id: str) -> JsonResponse:
    headers = forward_client_headers(request)
    if request.method == "GET":
        return await proxy_to_fastapi(
            method="GET",
            fastapi_path=f"/api/v1/conversations/{conversation_id}",
            request_id=request.bff_request_id,
            extra_headers=headers,
        )
    if request.method == "PUT":
        data = inject_user_into_body(request, parse_json_body(request))
        return await proxy_to_fastapi(
            method="PUT",
            fastapi_path=f"/api/v1/conversations/{conversation_id}",
            request_id=request.bff_request_id,
            data=data,
            extra_headers=headers,
        )

    return await proxy_to_fastapi(
        method="DELETE",
        fastapi_path=f"/api/v1/conversations/{conversation_id}",
        request_id=request.bff_request_id,
        extra_headers=headers,
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_add_message(request: HttpRequest, conversation_id: str) -> JsonResponse:
    data = inject_user_into_body(request, parse_json_body(request))
    return await proxy_to_fastapi(
        method="POST",
        fastapi_path=f"/api/v1/conversations/{conversation_id}/messages",
        request_id=request.bff_request_id,
        data=data,
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_summarize_conversation(request: HttpRequest, conversation_id: str) -> JsonResponse:
    return await proxy_to_fastapi(
        method="POST",
        fastapi_path=f"/api/v1/conversations/{conversation_id}/summarize",
        request_id=request.bff_request_id,
        extra_headers=forward_client_headers(request),
    )
