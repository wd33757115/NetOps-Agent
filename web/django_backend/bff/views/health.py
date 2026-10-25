# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

import logging

from asgiref.sync import async_to_sync
from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from ..sync_async import sync_bff_view
from ..proxy_client import HEALTH_TIMEOUT, proxy_to_fastapi
from ._helpers import forward_client_headers

logger = logging.getLogger("bff.views.health")


async def _proxy_health_async(request: HttpRequest) -> JsonResponse:
    return await proxy_to_fastapi(
        method="GET",
        fastapi_path="/health",
        request_id=request.bff_request_id,
        timeout=HEALTH_TIMEOUT,
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
def proxy_health(request: HttpRequest) -> JsonResponse:
    """同步入口，兼容 Daphne + 同步中间件链。"""
    return async_to_sync(_proxy_health_async)(request)


async def _proxy_health_diagnostics_async(request: HttpRequest) -> JsonResponse:
    return await proxy_to_fastapi(
        method="GET",
        fastapi_path="/health/diagnostics",
        request_id=request.bff_request_id,
        timeout=HEALTH_TIMEOUT,
        extra_headers=forward_client_headers(request),
    )


@csrf_exempt
def proxy_health_diagnostics(request: HttpRequest) -> JsonResponse:
    return async_to_sync(_proxy_health_diagnostics_async)(request)
