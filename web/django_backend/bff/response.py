# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

from django.http import JsonResponse


def bff_success(data=None, status: int = 200) -> JsonResponse:
    payload = {"success": True, "data": data, "error": None}
    if status == 204:
        return JsonResponse(payload, status=200)
    return JsonResponse(payload, status=status, safe=False)


def bff_error(
    error: str,
    status: int = 400,
    data=None,
    *,
    code: str | None = None,
    request_id: str | None = None,
) -> JsonResponse:
    payload: dict = {
        "success": False,
        "data": data,
        "error": error,
        "code": code,
        "request_id": request_id,
    }
    response = JsonResponse(payload, status=status, safe=False)
    if request_id:
        response["X-Request-Id"] = request_id
    return response
