# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

import logging

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from ..sync_async import sync_bff_view
from ..proxy_client import HEALTH_TIMEOUT, proxy_to_fastapi
from ._helpers import forward_client_headers

logger = logging.getLogger("bff.views.system")


@csrf_exempt
@sync_bff_view
async def proxy_gateway_info(request: HttpRequest) -> JsonResponse:
    """代理 FastAPI 根路径，返回网关服务信息。"""
    return await proxy_to_fastapi(
        method="GET",
        fastapi_path="/",
        request_id=request.bff_request_id,
        timeout=HEALTH_TIMEOUT,
        extra_headers=forward_client_headers(request),
    )
