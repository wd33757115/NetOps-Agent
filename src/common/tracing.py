# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""
请求追踪系统

提供分布式追踪能力：
1. Trace ID 生成和传播
2. Span 记录
3. 上下文管理
"""

import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

# Trace ID 上下文变量
_trace_id_var: ContextVar[str | None] = ContextVar('trace_id', default=None)


def generate_trace_id() -> str:
    """生成新的 Trace ID"""
    return str(uuid.uuid4())


def get_trace_id() -> str | None:
    """获取当前 Trace ID"""
    return _trace_id_var.get()


def set_trace_id(trace_id: str) -> None:
    """设置当前 Trace ID"""
    _trace_id_var.set(trace_id)


def clear_trace_id() -> None:
    """清除当前 Trace ID"""
    _trace_id_var.set(None)


@dataclass
class Span:
    """
    追踪 Span

    记录一个操作的时间和属性。
    """
    name: str
    trace_id: str
    span_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    parent_id: str | None = None
    start_time: float = field(default_factory=time.time)
    end_time: float | None = None
    duration_ms: float | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    status: str = "OK"  # OK, ERROR, TIMEOUT
    error_message: str | None = None

    def end(self, status: str = "OK", error_message: str | None = None):
        """结束 Span"""
        self.end_time = time.time()
        self.duration_ms = (self.end_time - self.start_time) * 1000
        self.status = status
        self.error_message = error_message

    def set_attribute(self, key: str, value: Any):
        """设置属性"""
        self.attributes[key] = value

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "name": self.name,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_id": self.parent_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": self.duration_ms,
            "attributes": self.attributes,
            "status": self.status,
            "error_message": self.error_message
        }


class Tracer:
    """
    追踪器

    管理 Trace 和 Span 的创建和记录。
    """

    def __init__(self, service_name: str = "netops-agent"):
        self.service_name = service_name
        self._spans: list[Span] = []
        self._current_span: Span | None = None

    def start_trace(self, name: str, attributes: dict[str, Any] = None) -> str:
        """
        开始一个新的 Trace

        Args:
            name: Trace 名称
            attributes: 初始属性

        Returns:
            str: Trace ID
        """
        trace_id = generate_trace_id()
        set_trace_id(trace_id)

        self._spans.clear()

        span = Span(
            name=f"{name}_root",
            trace_id=trace_id,
            attributes=attributes or {}
        )
        self._current_span = span

        return trace_id

    def end_trace(self, status: str = "OK", error_message: str | None = None) -> dict[str, Any]:
        """
        结束当前 Trace

        Returns:
            Dict: Trace 统计信息
        """
        if self._current_span:
            self._current_span.end(status=status, error_message=error_message)
            self._spans.append(self._current_span)

        trace_id = get_trace_id()
        clear_trace_id()

        return {
            "trace_id": trace_id,
            "service_name": self.service_name,
            "total_spans": len(self._spans),
            "total_duration_ms": sum(s.duration_ms or 0 for s in self._spans),
            "status": status,
            "spans": [s.to_dict() for s in self._spans]
        }

    def start_span(self, name: str, attributes: dict[str, Any] = None) -> Span:
        """
        开始一个新的 Span

        Args:
            name: Span 名称
            attributes: 初始属性

        Returns:
            Span: Span 对象
        """
        trace_id = get_trace_id() or generate_trace_id()

        parent_id = None
        if self._current_span:
            parent_id = self._current_span.span_id

        span = Span(
            name=name,
            trace_id=trace_id,
            parent_id=parent_id,
            attributes=attributes or {}
        )

        self._current_span = span
        return span

    def end_span(
        self,
        span: Span,
        status: str = "OK",
        error_message: str | None = None
    ):
        """
        结束一个 Span

        Args:
            span: Span 对象
            status: 状态
            error_message: 错误信息
        """
        span.end(status=status, error_message=error_message)
        self._spans.append(span)

        # 恢复父 Span
        if span.parent_id:
            for s in self._spans:
                if s.span_id == span.parent_id:
                    self._current_span = s
                    break
        else:
            self._current_span = None

    def record_exception(self, span: Span, exception: Exception):
        """记录异常"""
        span.end(status="ERROR", error_message=str(exception))
        span.set_attribute("exception.type", type(exception).__name__)
        span.set_attribute("exception.message", str(exception))

    @property
    def spans(self) -> list[Span]:
        """获取所有 Span"""
        return self._spans


# 全局 Tracer 实例
_tracer: Tracer | None = None


def get_tracer(service_name: str = "netops-agent") -> Tracer:
    """获取全局 Tracer 实例"""
    global _tracer
    if _tracer is None:
        _tracer = Tracer(service_name)
    return _tracer


# 便捷上下文管理器
from contextlib import contextmanager


@contextmanager
def trace(name: str, attributes: dict[str, Any] = None):
    """
    追踪上下文管理器

    用法：
        with trace("skill_execution", {"skill_name": "firewall"}):
            # 执行操作
            pass
    """
    tracer = get_tracer()
    trace_id = get_trace_id()

    if not trace_id:
        trace_id = tracer.start_trace(name, attributes)

    span = tracer.start_span(name, attributes)

    try:
        yield span
    except Exception as e:
        tracer.record_exception(span, e)
        raise
    finally:
        tracer.end_span(span)
