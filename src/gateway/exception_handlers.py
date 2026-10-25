# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""FastAPI 全局异常处理器：统一错误信封 + request_id。"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.common.exceptions import NetOpsBaseError
from src.core.exceptions import (
    AppError,
    ErrorCode,
    app_error_from_netops,
    error_envelope,
    http_status_to_error_code,
    normalize_error_detail,
)
from src.core.logging import get_logger

log = get_logger(__name__)


def get_request_id(request: Request) -> str:
    state_id = getattr(request.state, "request_id", None)
    if state_id:
        return str(state_id)
    header_id = request.headers.get("X-Request-Id") or request.headers.get("X-Request-ID")
    if header_id:
        return header_id
    return str(uuid.uuid4())


def _json_error_response(
    *,
    request: Request,
    status_code: int,
    code: str | ErrorCode,
    message: str,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    request_id = get_request_id(request)
    content = error_envelope(
        code=code,
        message=message,
        request_id=request_id,
        details=details,
    )
    response = JSONResponse(status_code=status_code, content=content)
    response.headers["X-Request-Id"] = request_id
    return response


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    log.warning(
        "api_app_error",
        code=exc.code,
        status_code=exc.status_code,
        message=exc.message,
    )
    return _json_error_response(
        request=request,
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        details=exc.details,
    )


async def netops_base_error_handler(request: Request, exc: NetOpsBaseError) -> JSONResponse:
    mapped = app_error_from_netops(exc)
    return await app_error_handler(request, mapped)


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    message, details = normalize_error_detail(exc.detail)
    code = http_status_to_error_code(exc.status_code)
    if isinstance(exc.detail, dict) and exc.detail.get("code"):
        code = str(exc.detail["code"])
    log.warning(
        "api_http_error",
        status_code=exc.status_code,
        code=str(code),
        message=message,
    )
    return _json_error_response(
        request=request,
        status_code=exc.status_code,
        code=code,
        message=message,
        details=details,
    )


async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    message, details = normalize_error_detail(exc.errors())
    log.warning("api_validation_error", error_count=len(exc.errors()))
    return _json_error_response(
        request=request,
        status_code=422,
        code=ErrorCode.VALIDATION_ERROR,
        message=message or "参数校验失败",
        details=details,
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    log.error(
        "api_unhandled_error",
        error=str(exc),
        exc_info=exc,
    )
    return _json_error_response(
        request=request,
        status_code=500,
        code=ErrorCode.INTERNAL_ERROR,
        message="服务器内部错误",
        details={"type": type(exc).__name__},
    )


def register_exception_handlers(app: FastAPI) -> None:
    """注册全局异常处理器（幂等）。"""
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(NetOpsBaseError, netops_base_error_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
