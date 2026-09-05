# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""
自定义异常定义

提供项目中使用的所有自定义异常类型。
"""


class NetOpsBaseError(Exception):
    """NetOps Agent 基础异常"""

    def __init__(self, message: str, details: dict = None):
        self.message = message
        self.details = details or {}
        super().__init__(self.message)


class SkillExecutionError(NetOpsBaseError):
    """Skill 执行错误"""

    def __init__(
        self,
        skill_name: str,
        message: str,
        original_error: Exception = None,
        details: dict = None
    ):
        self.skill_name = skill_name
        self.original_error = original_error
        details = details or {}
        details["skill_name"] = skill_name
        if original_error:
            details["original_error"] = str(original_error)
        super().__init__(f"[{skill_name}] {message}", details)


class SkillTimeoutError(SkillExecutionError):
    """Skill 执行超时"""

    def __init__(self, skill_name: str, timeout_seconds: int):
        self.timeout_seconds = timeout_seconds
        super().__init__(
            skill_name=skill_name,
            message=f"Skill 执行超时（{timeout_seconds}s）",
            details={"timeout_seconds": timeout_seconds}
        )


class SkillNotFoundError(NetOpsBaseError):
    """Skill 不存在"""

    def __init__(self, skill_name: str):
        self.skill_name = skill_name
        super().__init__(
            f"Skill '{skill_name}' 不存在",
            details={"skill_name": skill_name}
        )


class SkillDisabledError(NetOpsBaseError):
    """Skill 已禁用"""

    def __init__(self, skill_name: str):
        self.skill_name = skill_name
        super().__init__(
            f"Skill '{skill_name}' 已禁用",
            details={"skill_name": skill_name}
        )


class SkillValidationError(NetOpsBaseError):
    """Skill 参数校验错误"""

    def __init__(self, skill_name: str, field: str, message: str):
        self.skill_name = skill_name
        self.field = field
        super().__init__(
            f"[{skill_name}] 参数校验失败: {field} - {message}",
            details={"skill_name": skill_name, "field": field}
        )


class RAGQueryError(NetOpsBaseError):
    """RAG 查询错误"""

    def __init__(self, message: str, original_error: Exception = None):
        super().__init__(
            f"RAG 查询失败: {message}",
            details={"original_error": str(original_error)} if original_error else {}
        )


class DatabaseError(NetOpsBaseError):
    """数据库错误"""

    def __init__(self, message: str, operation: str = None, original_error: Exception = None):
        self.operation = operation
        super().__init__(
            f"数据库操作失败: {message}",
            details={
                "operation": operation,
                "original_error": str(original_error)
            } if original_error else {"operation": operation}
        )


class CeleryTaskError(NetOpsBaseError):
    """Celery 任务错误"""

    def __init__(self, task_name: str, task_id: str, message: str, original_error: Exception = None):
        self.task_name = task_name
        self.task_id = task_id
        super().__init__(
            f"Celery 任务失败 [{task_name}]: {message}",
            details={
                "task_name": task_name,
                "task_id": task_id,
                "original_error": str(original_error)
            } if original_error else {"task_name": task_name, "task_id": task_id}
        )


class LLMError(NetOpsBaseError):
    """LLM 调用错误"""

    def __init__(self, message: str, model: str = None, original_error: Exception = None):
        self.model = model
        super().__init__(
            f"LLM 调用失败: {message}",
            details={
                "model": model,
                "original_error": str(original_error)
            } if original_error else {"model": model}
        )


class ConfigurationError(NetOpsBaseError):
    """配置错误"""

    def __init__(self, message: str, config_key: str = None):
        self.config_key = config_key
        super().__init__(
            f"配置错误: {message}",
            details={"config_key": config_key} if config_key else {}
        )


class AuthenticationError(NetOpsBaseError):
    """认证错误"""

    def __init__(self, message: str = "认证失败"):
        super().__init__(message)


class AuthorizationError(NetOpsBaseError):
    """授权错误"""

    def __init__(self, message: str, skill_name: str = None, user_id: str = None):
        self.skill_name = skill_name
        self.user_id = user_id
        super().__init__(
            message,
            details={
                "skill_name": skill_name,
                "user_id": user_id
            } if skill_name or user_id else {}
        )


class ValidationError(NetOpsBaseError):
    """数据校验错误"""

    def __init__(self, message: str, field: str = None, value: any = None):
        self.field = field
        self.value = value
        super().__init__(
            message,
            details={
                "field": field,
                "value": str(value) if value is not None else None
            } if field else {}
        )


class StorageError(NetOpsBaseError):
    """存储错误"""

    def __init__(self, message: str, operation: str = None, path: str = None):
        self.operation = operation
        self.path = path
        super().__init__(
            f"存储操作失败: {message}",
            details={
                "operation": operation,
                "path": path
            } if operation or path else {}
        )
