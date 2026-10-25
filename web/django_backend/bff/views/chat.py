# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

import logging

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from ..decorators import require_jwt
from ..proxy_client import CHAT_TIMEOUT, proxy_stream_to_fastapi, proxy_to_fastapi
from ..sync_async import sync_bff_view
from ._helpers import forward_client_headers, inject_user_into_body, parse_json_body

logger = logging.getLogger("bff.views.chat")


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_chat(request: HttpRequest) -> JsonResponse:
    data = inject_user_into_body(request, parse_json_body(request))
    return await proxy_to_fastapi(
        method="POST",
        fastapi_path="/api/v1/chat",
        request_id=request.bff_request_id,
        data=data,
        timeout=CHAT_TIMEOUT,
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_chat_stream(request: HttpRequest):
    """SSE 流式聊天代理 → FastAPI /api/v1/chat/stream。"""
    data = inject_user_into_body(request, parse_json_body(request))
    return await proxy_stream_to_fastapi(
        method="POST",
        fastapi_path="/api/v1/chat/stream",
        request_id=request.bff_request_id,
        data=data,
        timeout=CHAT_TIMEOUT,
        extra_headers=forward_client_headers(request),
    )
