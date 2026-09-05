# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""
Skill 安全控制

提供 Skill 执行的安全管理：
1. 权限控制
2. 审计日志
3. 输入验证
4. 敏感信息过滤
"""

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class PermissionLevel(Enum):
    """权限级别"""
    ADMIN = "admin"  # 管理员
    POWER_USER = "power_user"  # 高级用户
    USER = "user"  # 普通用户
    GUEST = "guest"  # 访客


@dataclass
class AuditLog:
    """审计日志条目"""
    timestamp: float = field(default_factory=time.time)
    skill_name: str = ""
    user_id: str = ""
    action: str = ""  # execute, view, create, update, delete
    parameters: dict[str, Any] = field(default_factory=dict)
    result: str = "success"  # success, failure, timeout
    error_message: str | None = None
    duration_ms: float = 0
    trace_id: str | None = None
    ip_address: str | None = None


class SkillSecurityManager:
    """
    Skill 安全管理器

    提供：
    1. 执行权限控制
    2. 审计日志记录
    3. 敏感信息过滤
    """

    def __init__(self):
        self._audit_logs: list[AuditLog] = []
        self._permission_cache: dict[str, PermissionLevel] = {}
        self._max_audit_logs = 10000  # 保留最近 10000 条

        # Skill 权限配置 — 默认 USER 级别（内部系统，不过度限制）
        self._skill_permissions: dict[str, PermissionLevel] = {
            "*": PermissionLevel.USER
        }

        # 敏感参数名
        self._sensitive_params = {
            "password", "secret", "token", "api_key", "apikey",
            "credential", "private_key", "access_token"
        }

    def set_skill_permission(self, skill_name: str, level: PermissionLevel):
        """设置 Skill 的权限要求"""
        self._skill_permissions[skill_name] = level

    def get_skill_permission(self, skill_name: str) -> PermissionLevel:
        """获取 Skill 的权限要求"""
        return self._skill_permissions.get(
            skill_name,
            self._skill_permissions.get("*", PermissionLevel.ADMIN)
        )

    def check_permission(
        self,
        skill_name: str,
        user_level: PermissionLevel
    ) -> bool:
        """
        检查用户是否有执行权限

        Args:
            skill_name: Skill 名称
            user_level: 用户权限级别

        Returns:
            bool: 是否有权限
        """
        required_level = self.get_skill_permission(skill_name)

        # 权限级别顺序
        level_order = {
            PermissionLevel.GUEST: 0,
            PermissionLevel.USER: 1,
            PermissionLevel.POWER_USER: 2,
            PermissionLevel.ADMIN: 3
        }

        return level_order.get(user_level, 0) >= level_order.get(required_level, 3)

    def log_execution(
        self,
        skill_name: str,
        user_id: str,
        action: str,
        parameters: dict[str, Any],
        result: str = "success",
        error_message: str | None = None,
        duration_ms: float = 0,
        trace_id: str | None = None,
        ip_address: str | None = None
    ):
        """
        记录执行日志

        Args:
            skill_name: Skill 名称
            user_id: 用户 ID
            action: 操作类型
            parameters: 参数
            result: 结果
            error_message: 错误信息
            duration_ms: 执行耗时
            trace_id: 追踪 ID
            ip_address: IP 地址
        """
        # 过滤敏感参数
        filtered_params = self.filter_sensitive_data(parameters)

        log_entry = AuditLog(
            skill_name=skill_name,
            user_id=user_id,
            action=action,
            parameters=filtered_params,
            result=result,
            error_message=error_message,
            duration_ms=duration_ms,
            trace_id=trace_id,
            ip_address=ip_address
        )

        self._audit_logs.append(log_entry)

        # 限制日志数量
        if len(self._audit_logs) > self._max_audit_logs:
            self._audit_logs = self._audit_logs[-self._max_audit_logs:]

        # 记录到日志
        log_level = logging.INFO if result == "success" else logging.ERROR
        logger.log(
            log_level,
            f"[Audit] {action} {skill_name} by {user_id}: {result}",
            extra={
                "skill_name": skill_name,
                "user_id": user_id,
                "action": action,
                "result": result,
                "duration_ms": duration_ms,
                "trace_id": trace_id
            }
        )

    def get_audit_log(
        self,
        skill_name: str | None = None,
        user_id: str | None = None,
        start_time: float | None = None,
        end_time: float | None = None,
        limit: int = 100
    ) -> list[AuditLog]:
        """
        获取审计日志

        Args:
            skill_name: 按 Skill 名称过滤
            user_id: 按用户 ID 过滤
            start_time: 开始时间戳
            end_time: 结束时间戳
            limit: 返回数量限制

        Returns:
            List[AuditLog]: 审计日志列表
        """
        logs = self._audit_logs

        if skill_name:
            logs = [log for log in logs if log.skill_name == skill_name]

        if user_id:
            logs = [log for log in logs if log.user_id == user_id]

        if start_time:
            logs = [log for log in logs if log.timestamp >= start_time]

        if end_time:
            logs = [log for log in logs if log.timestamp <= end_time]

        # 按时间倒序
        logs = sorted(logs, key=lambda x: x.timestamp, reverse=True)

        return logs[:limit]

    def filter_sensitive_data(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        过滤敏感数据

        Args:
            data: 原始数据

        Returns:
            Dict: 过滤后的数据
        """
        if not isinstance(data, dict):
            return data

        filtered = {}
        for key, value in data.items():
            # 检查键名是否包含敏感关键词
            key_lower = key.lower()
            if any(s in key_lower for s in self._sensitive_params):
                # 隐藏敏感值
                if isinstance(value, str) and len(value) > 4:
                    filtered[key] = value[:2] + "***" + value[-2:]
                else:
                    filtered[key] = "***"
            elif isinstance(value, dict):
                filtered[key] = self.filter_sensitive_data(value)
            elif isinstance(value, list):
                filtered[key] = [
                    self.filter_sensitive_data(item) if isinstance(item, dict) else item
                    for item in value
                ]
            else:
                filtered[key] = value

        return filtered

    def validate_parameters(
        self,
        parameters: dict[str, Any],
        schema: dict[str, Any] | None = None
    ) -> tuple[bool, str | None]:
        """
        验证参数

        Args:
            parameters: 参数
            schema: 参数模式

        Returns:
            tuple: (是否有效, 错误信息)
        """
        if not schema:
            return True, None

        # 检查必填参数
        required = schema.get("required", [])
        for field_name in required:
            if field_name not in parameters:
                return False, f"缺少必填参数: {field_name}"

        # 检查类型
        for field_name, value in parameters.items():
            if field_name in schema.get("properties", {}):
                expected_type = schema["properties"][field_name].get("type")
                if expected_type:
                    if not self._check_type(value, expected_type):
                        return False, f"参数 {field_name} 类型错误，期望 {expected_type}"

        return True, None

    def _check_type(self, value: Any, expected_type: str) -> bool:
        """检查类型"""
        type_map = {
            "string": str,
            "integer": int,
            "number": (int, float),
            "boolean": bool,
            "array": list,
            "object": dict
        }

        expected = type_map.get(expected_type)
        if expected is None:
            return True

        return isinstance(value, expected)

    def get_statistics(self) -> dict[str, Any]:
        """获取统计信息"""
        total = len(self._audit_logs)
        success = sum(1 for log in self._audit_logs if log.result == "success")
        failure = sum(1 for log in self._audit_logs if log.result == "failure")

        # 按 Skill 统计
        by_skill = {}
        for log in self._audit_logs:
            if log.skill_name not in by_skill:
                by_skill[log.skill_name] = {"total": 0, "success": 0, "failure": 0}
            by_skill[log.skill_name]["total"] += 1
            by_skill[log.skill_name][log.result] += 1

        return {
            "total_executions": total,
            "success_count": success,
            "failure_count": failure,
            "success_rate": success / total if total > 0 else 0,
            "by_skill": by_skill
        }


# 全局安全管理器实例
_security_manager: SkillSecurityManager | None = None


def get_security_manager() -> SkillSecurityManager:
    """获取全局安全管理器"""
    global _security_manager
    if _security_manager is None:
        _security_manager = SkillSecurityManager()
    return _security_manager


# 便捷函数
def check_skill_permission(skill_name: str, user_level: PermissionLevel = PermissionLevel.USER) -> bool:
    """检查执行权限"""
    return get_security_manager().check_permission(skill_name, user_level)


def filter_sensitive_data(data: dict[str, Any]) -> dict[str, Any]:
    """过滤敏感数据"""
    return get_security_manager().filter_sensitive_data(data)
