# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

import logging

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from ..decorators import require_jwt
from ..sync_async import sync_bff_view
from ..proxy_client import TASK_TIMEOUT, proxy_to_fastapi
from ._helpers import forward_client_headers

logger = logging.getLogger("bff.views.tasks")


@csrf_exempt
@require_jwt
@sync_bff_view
async def proxy_task_status(request: HttpRequest, task_id: str) -> JsonResponse:
    return await proxy_to_fastapi(
        method="GET",
        fastapi_path=f"/api/v1/tasks/{task_id}",
        request_id=request.bff_request_id,
        timeout=TASK_TIMEOUT,
        extra_headers=forward_client_headers(request),
    )
