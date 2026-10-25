# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""
结构化日志系统

提供统一的日志记录接口，支持：
1. JSON 格式结构化日志
2. 不同级别的日志记录
3. 日志文件轮转
4. 上下文信息记录（Trace ID、Skill 名称等）
"""

import json
import logging
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path


class StructuredFormatter(logging.Formatter):
    """结构化日志格式化器"""

    def __init__(self, include_trace: bool = True):
        super().__init__()
        self.include_trace = include_trace

    def format(self, record: logging.LogRecord) -> str:
        """格式化日志记录"""
        log_data = {
            "timestamp": datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno
        }

        # 添加 Trace ID（如果存在）
        if self.include_trace:
            trace_id = getattr(record, "trace_id", None)
            if trace_id:
                log_data["trace_id"] = trace_id

        # 添加 Skill 名称（如果存在）
        skill_name = getattr(record, "skill_name", None)
        if skill_name:
            log_data["skill_name"] = skill_name

        # 添加额外字段
        if hasattr(record, "extra_data"):
            log_data.update(record.extra_data)

        # 添加异常信息
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_data, ensure_ascii=False, default=str)


class StructuredLogger:
    """
    结构化日志记录器

    提供简化的日志记录接口，自动添加 Trace ID 等上下文信息。
    """

    _instances: dict[str, 'StructuredLogger'] = {}

    def __init__(self, name: str, log_dir: str = "logs"):
        self.name = name
        self.logger = logging.getLogger(name)

        # 只设置一次
        if not self.logger.handlers:
            self.logger.setLevel(logging.INFO)

            # 控制台处理器
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setFormatter(StructuredFormatter())
            self.logger.addHandler(console_handler)

            # 文件处理器（带轮转）
            log_path = Path(log_dir)
            log_path.mkdir(exist_ok=True)

            file_handler = RotatingFileHandler(
                log_path / f"{name}.log",
                maxBytes=10 * 1024 * 1024,  # 10MB
                backupCount=5,
                encoding="utf-8"
            )
            file_handler.setFormatter(StructuredFormatter())
            self.logger.addHandler(file_handler)

    @classmethod
    def get_logger(cls, name: str = "app", log_dir: str = "logs") -> 'StructuredLogger':
        """获取 Logger 实例（单例）"""
        if name not in cls._instances:
            cls._instances[name] = cls(name, log_dir)
        return cls._instances

    def _log(
        self,
        level: str,
        event: str,
        message: str = "",
        trace_id: str | None = None,
        skill_name: str | None = None,
        **kwargs
    ):
        """内部日志记录方法"""
        extra = {
            "extra_data": kwargs
        }

        if trace_id:
            extra["trace_id"] = trace_id
        if skill_name:
            extra["skill_name"] = skill_name

        log_method = getattr(self.logger, level.lower())
        log_message = f"[{event}] {message}" if message else event

        log_method(log_message, extra=extra)

    def info(self, event: str, message: str = "", **kwargs):
        """记录 INFO 日志"""
        self._log("INFO", event, message, **kwargs)

    def warning(self, event: str, message: str = "", **kwargs):
        """记录 WARNING 日志"""
        self._log("WARNING", event, message, **kwargs)

    def error(self, event: str, message: str = "", **kwargs):
        """记录 ERROR 日志"""
        self._log("ERROR", event, message, **kwargs)

    def debug(self, event: str, message: str = "", **kwargs):
        """记录 DEBUG 日志"""
        self._log("DEBUG", event, message, **kwargs)

    def skill_execution(
        self,
        skill_name: str,
        duration_ms: float,
        success: bool,
        trace_id: str | None = None,
        **kwargs
    ):
        """记录 Skill 执行日志"""
        self._log(
            "INFO" if success else "ERROR",
            "skill_execution",
            f"Skill '{skill_name}' {'succeeded' if success else 'failed'} in {duration_ms:.2f}ms",
            trace_id=trace_id,
            skill_name=skill_name,
            duration_ms=duration_ms,
            success=success,
            **kwargs
        )

    def skill_routing(
        self,
        skill_name: str | None,
        query: str,
        trace_id: str | None = None,
        **kwargs
    ):
        """记录 Skill 路由日志"""
        skill_info = f"route to '{skill_name}'" if skill_name else "fallback to RAG"
        self._log(
            "INFO",
            "skill_routing",
            f"Route {query[:50]}... -> {skill_info}",
            trace_id=trace_id,
            skill_name=skill_name,
            query=query[:100],
            **kwargs
        )


# 便捷函数
def get_logger(name: str = "app") -> StructuredLogger:
    """获取 Logger 实例"""
    return StructuredLogger.get_logger(name)


# 应用级 Logger
app_logger = StructuredLogger.get_logger("app")
skill_logger = StructuredLogger.get_logger("skill")
agent_logger = StructuredLogger.get_logger("agent")
