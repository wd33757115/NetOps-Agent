# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""API 层统一异常与错误信封（P2）。"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from src.common.exceptions import (
    AuthenticationError,
    AuthorizationError,
    CeleryTaskError,
    ConfigurationError,
    DatabaseError,
    LLMError,
    NetOpsBaseError,
    RAGQueryError,
    SkillDisabledError,
    SkillExecutionError,
    SkillNotFoundError,
    SkillTimeoutError,
    SkillValidationError,
    StorageError,
    ValidationError,
)


class ErrorCode(StrEnum):
    """稳定机器可读错误码，供前端/BFF 分支处理。"""

    INTERNAL_ERROR = "internal_error"
    VALIDATION_ERROR = "validation_error"
    BAD_REQUEST = "bad_request"
    UNAUTHORIZED = "unauthorized"
    PERMISSION_DENIED = "permission_denied"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    SERVICE_UNAVAILABLE = "service_unavailable"
    GATEWAY_TIMEOUT = "gateway_timeout"
    BFF_ORIGIN_REQUIRED = "bff_origin_required"

    SKILL_NOT_FOUND = "skill_not_found"
    SKILL_DISABLED = "skill_disabled"
    SKILL_EXECUTION_FAILED = "skill_execution_failed"
    SKILL_VALIDATION_FAILED = "skill_validation_failed"
    SKILL_TIMEOUT = "skill_timeout"

    WORKFLOW_NOT_FOUND = "workflow_not_found"
    RAG_QUERY_FAILED = "rag_query_failed"
    DATABASE_ERROR = "database_error"
    STORAGE_ERROR = "storage_error"
    LLM_ERROR = "llm_error"
    CELERY_TASK_FAILED = "celery_task_failed"
    CONFIGURATION_ERROR = "configuration_error"


def http_status_to_error_code(status_code: int) -> ErrorCode:
    mapping: dict[int, ErrorCode] = {
        400: ErrorCode.BAD_REQUEST,
        401: ErrorCode.UNAUTHORIZED,
        403: ErrorCode.PERMISSION_DENIED,
        404: ErrorCode.NOT_FOUND,
        409: ErrorCode.CONFLICT,
        422: ErrorCode.VALIDATION_ERROR,
        503: ErrorCode.SERVICE_UNAVAILABLE,
        504: ErrorCode.GATEWAY_TIMEOUT,
    }
    return mapping.get(status_code, ErrorCode.INTERNAL_ERROR)


def error_envelope(
    *,
    code: str | ErrorCode,
    message: str,
    request_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """FastAPI / BFF 共用的错误响应体。"""
    body: dict[str, Any] = {
        "success": False,
        "error": {
            "code": str(code),
            "message": message,
            "details": details or {},
        },
    }
    if request_id:
        body["request_id"] = request_id
    return body


def normalize_error_detail(detail: Any) -> tuple[str, dict[str, Any]]:
    """将 HTTPException.detail / 校验错误转为 (message, details)。"""
    if detail is None:
        return "请求失败", {}
    if isinstance(detail, str):
        return detail, {}
    if isinstance(detail, dict):
        message = str(detail.get("message") or detail.get("msg") or detail)
        extra = {k: v for k, v in detail.items() if k not in {"message", "msg"}}
        return message, extra
    if isinstance(detail, list):
        items: list[dict[str, Any]] = []
        for item in detail:
            if isinstance(item, dict):
                items.append(item)
            else:
                items.append({"msg": str(item)})
        first_msg = str(items[0].get("msg") or items[0]) if items else "参数校验失败"
        return first_msg, {"errors": items}
    return str(detail), {}


class AppError(Exception):
    """可预期的业务/API 错误，由全局 handler 转为统一信封。"""

    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode | str = ErrorCode.BAD_REQUEST,
        status_code: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message
        self.code = str(code)
        self.status_code = status_code
        self.details = details or {}
        super().__init__(message)

    def to_envelope(self, request_id: str | None = None) -> dict[str, Any]:
        return error_envelope(
            code=self.code,
            message=self.message,
            request_id=request_id,
            details=self.details,
        )


def app_error_from_netops(exc: NetOpsBaseError) -> AppError:
    """将 domain 异常映射为 AppError（不改变现有 raise HTTPException 路径）。"""
    details = dict(exc.details or {})

    if isinstance(exc, SkillNotFoundError):
        return AppError(
            exc.message,
            code=ErrorCode.SKILL_NOT_FOUND,
            status_code=404,
            details=details,
        )
    if isinstance(exc, SkillDisabledError):
        return AppError(
            exc.message,
            code=ErrorCode.SKILL_DISABLED,
            status_code=403,
            details=details,
        )
    if isinstance(exc, SkillTimeoutError):
        return AppError(
            exc.message,
            code=ErrorCode.SKILL_TIMEOUT,
            status_code=504,
            details=details,
        )
    if isinstance(exc, SkillExecutionError):
        return AppError(
            exc.message,
            code=ErrorCode.SKILL_EXECUTION_FAILED,
            status_code=400,
            details=details,
        )
    if isinstance(exc, SkillValidationError):
        return AppError(
            exc.message,
            code=ErrorCode.SKILL_VALIDATION_FAILED,
            status_code=422,
            details=details,
        )
    if isinstance(exc, AuthenticationError):
        return AppError(
            exc.message,
            code=ErrorCode.UNAUTHORIZED,
            status_code=401,
            details=details,
        )
    if isinstance(exc, AuthorizationError):
        return AppError(
            exc.message,
            code=ErrorCode.PERMISSION_DENIED,
            status_code=403,
            details=details,
        )
    if isinstance(exc, ValidationError):
        return AppError(
            exc.message,
            code=ErrorCode.VALIDATION_ERROR,
            status_code=422,
            details=details,
        )
    if isinstance(exc, RAGQueryError):
        return AppError(
            exc.message,
            code=ErrorCode.RAG_QUERY_FAILED,
            status_code=500,
            details=details,
        )
    if isinstance(exc, DatabaseError):
        return AppError(
            exc.message,
            code=ErrorCode.DATABASE_ERROR,
            status_code=500,
            details=details,
        )
    if isinstance(exc, StorageError):
        return AppError(
            exc.message,
            code=ErrorCode.STORAGE_ERROR,
            status_code=503,
            details=details,
        )
    if isinstance(exc, LLMError):
        return AppError(
            exc.message,
            code=ErrorCode.LLM_ERROR,
            status_code=502,
            details=details,
        )
    if isinstance(exc, CeleryTaskError):
        return AppError(
            exc.message,
            code=ErrorCode.CELERY_TASK_FAILED,
            status_code=500,
            details=details,
        )
    if isinstance(exc, ConfigurationError):
        return AppError(
            exc.message,
            code=ErrorCode.CONFIGURATION_ERROR,
            status_code=500,
            details=details,
        )

    return AppError(
        exc.message,
        code=ErrorCode.BAD_REQUEST,
        status_code=400,
        details=details,
    )
