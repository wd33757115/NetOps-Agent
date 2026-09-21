# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""
性能指标收集

提供应用性能指标收集能力：
1. 计数器（Counter）
2. 计时器（Timer）
3. 指标聚合
"""

import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class Metric:
    """指标数据"""
    name: str
    value: float
    unit: str = "count"
    timestamp: float = field(default_factory=time.time)
    tags: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "timestamp": self.timestamp,
            "tags": self.tags
        }


class Counter:
    """计数器"""

    def __init__(self, name: str, initial_value: int = 0, tags: dict[str, str] = None):
        self.name = name
        self._value = initial_value
        self.tags = tags or {}
        self._lock = threading.Lock()

    def increment(self, value: int = 1) -> int:
        """增加计数"""
        with self._lock:
            self._value += value
            return self._value

    def decrement(self, value: int = 1) -> int:
        """减少计数"""
        return self.increment(-value)

    def get(self) -> int:
        """获取当前值"""
        return self._value

    def reset(self):
        """重置计数器"""
        with self._lock:
            self._value = 0


class Gauge:
    """仪表（瞬时值）"""

    def __init__(self, name: str, initial_value: float = 0.0, tags: dict[str, str] = None):
        self.name = name
        self._value = initial_value
        self.tags = tags or {}
        self._lock = threading.Lock()

    def set(self, value: float):
        """设置值"""
        with self._lock:
            self._value = value

    def get(self) -> float:
        """获取当前值"""
        return self._value

    def increment(self, value: float = 1.0):
        """增加值"""
        with self._lock:
            self._value += value

    def decrement(self, value: float = 1.0):
        """减少值"""
        self.increment(-value)


class Histogram:
    """直方图（分布统计）"""

    def __init__(
        self,
        name: str,
        buckets: list[float] = None,
        tags: dict[str, str] = None
    ):
        self.name = name
        self.buckets = buckets or [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
        self.tags = tags or {}
        self._values: list[float] = []
        self._lock = threading.Lock()

    def observe(self, value: float):
        """记录值"""
        with self._lock:
            self._values.append(value)

    def get_stats(self) -> dict[str, float]:
        """获取统计数据"""
        with self._lock:
            if not self._values:
                return {
                    "count": 0,
                    "sum": 0,
                    "min": 0,
                    "max": 0,
                    "avg": 0,
                    "p50": 0,
                    "p90": 0,
                    "p99": 0
                }

            sorted_values = sorted(self._values)
            n = len(sorted_values)

            return {
                "count": n,
                "sum": sum(sorted_values),
                "min": sorted_values[0],
                "max": sorted_values[-1],
                "avg": sum(sorted_values) / n,
                "p50": sorted_values[int(n * 0.5)],
                "p90": sorted_values[int(n * 0.9)],
                "p99": sorted_values[int(n * 0.99)] if n > 1 else sorted_values[0]
            }

    def reset(self):
        """重置"""
        with self._lock:
            self._values.clear()


class Timer:
    """计时器"""

    def __init__(self, histogram: Histogram = None):
        self.histogram = histogram
        self._start_time: float | None = None
        self._lock = threading.Lock()

    def start(self):
        """开始计时"""
        self._start_time = time.time()

    def stop(self) -> float:
        """停止计时，返回耗时（秒）"""
        if self._start_time is None:
            raise RuntimeError("Timer not started")

        with self._lock:
            elapsed = time.time() - self._start_time
            if self.histogram:
                self.histogram.observe(elapsed * 1000)  # 转换为毫秒
            self._start_time = None
            return elapsed

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()


class MetricsCollector:
    """
    指标收集器

    统一管理所有指标类型。
    """

    def __init__(self):
        self._counters: dict[str, Counter] = defaultdict(Counter)
        self._gauges: dict[str, Gauge] = defaultdict(Gauge)
        self._histograms: dict[str, Histogram] = defaultdict(Histogram)
        self._metrics: list[Metric] = []
        self._lock = threading.Lock()

    def counter(self, name: str, tags: dict[str, str] = None) -> Counter:
        """获取或创建计数器"""
        key = self._make_key(name, tags)
        if key not in self._counters:
            self._counters[key] = Counter(name, tags=tags)
        return self._counters[key]

    def gauge(self, name: str, tags: dict[str, str] = None) -> Gauge:
        """获取或创建仪表"""
        key = self._make_key(name, tags)
        if key not in self._gauges:
            self._gauges[key] = Gauge(name, tags=tags)
        return self._gauges[key]

    def histogram(self, name: str, tags: dict[str, str] = None) -> Histogram:
        """获取或创建直方图"""
        key = self._make_key(name, tags)
        if key not in self._histograms:
            self._histograms[key] = Histogram(name, tags=tags)
        return self._histograms[key]

    def timer(self) -> Timer:
        """创建计时器"""
        return Timer()

    def record(self, name: str, value: float, unit: str = "count", tags: dict[str, str] = None):
        """记录指标"""
        with self._lock:
            self._metrics.append(Metric(name=name, value=value, unit=unit, tags=tags or {}))

    def increment(self, name: str, value: int = 1, tags: dict[str, str] = None):
        """增加计数器"""
        self.counter(name, tags).increment(value)

    def decrement(self, name: str, value: int = 1, tags: dict[str, str] = None):
        """减少计数器"""
        self.counter(name, tags).decrement(value)

    def set_gauge(self, name: str, value: float, tags: dict[str, str] = None):
        """设置仪表值"""
        self.gauge(name, tags).set(value)

    def observe(self, name: str, value: float, tags: dict[str, str] = None):
        """记录直方图值"""
        self.histogram(name, tags).observe(value)

    def get_summary(self) -> dict[str, Any]:
        """获取指标摘要"""
        summary = {
            "timestamp": datetime.now().isoformat(),
            "counters": {},
            "gauges": {},
            "histograms": {}
        }

        # 计数器摘要
        for key, counter in self._counters.items():
            summary["counters"][key] = {
                "value": counter.get(),
                "tags": counter.tags
            }

        # 仪表摘要
        for key, gauge in self._gauges.items():
            summary["gauges"][key] = {
                "value": gauge.get(),
                "tags": gauge.tags
            }

        # 直方图摘要
        for key, histogram in self._histograms.items():
            summary["histograms"][key] = {
                "stats": histogram.get_stats(),
                "tags": histogram.tags
            }

        return summary

    def reset(self):
        """重置所有指标"""
        self._counters.clear()
        self._gauges.clear()
        self._histograms.clear()
        self._metrics.clear()

    @staticmethod
    def _make_key(name: str, tags: dict[str, str] = None) -> str:
        """生成唯一键"""
        if not tags:
            return name
        tag_str = ",".join(f"{k}={v}" for k, v in sorted(tags.items()))
        return f"{name}{{{tag_str}}}"


# 全局指标收集器
_metrics_collector: MetricsCollector | None = None


def get_metrics_collector() -> MetricsCollector:
    """获取全局指标收集器"""
    global _metrics_collector
    if _metrics_collector is None:
        _metrics_collector = MetricsCollector()
    return _metrics_collector


# 便捷指标函数
def increment_counter(name: str, value: int = 1, tags: dict[str, str] = None):
    """增加计数器"""
    get_metrics_collector().increment(name, value, tags)


def set_gauge(name: str, value: float, tags: dict[str, str] = None):
    """设置仪表值"""
    get_metrics_collector().set_gauge(name, value, tags)


def observe_histogram(name: str, value: float, tags: dict[str, str] = None):
    """记录直方图值"""
    get_metrics_collector().observe(name, value, tags)


# 预定义的指标名称
class Metrics:
    """指标名称常量"""
    # 请求指标
    REQUEST_TOTAL = "requests_total"
    REQUEST_SUCCESS = "requests_success"
    REQUEST_ERROR = "requests_error"
    REQUEST_DURATION = "request_duration_ms"

    # Skill 指标
    SKILL_EXECUTION_TOTAL = "skill_execution_total"
    SKILL_EXECUTION_SUCCESS = "skill_execution_success"
    SKILL_EXECUTION_ERROR = "skill_execution_error"
    SKILL_EXECUTION_DURATION = "skill_execution_duration_ms"

    # LLM 指标
    LLM_REQUEST_TOTAL = "llm_request_total"
    LLM_REQUEST_SUCCESS = "llm_request_success"
    LLM_REQUEST_ERROR = "llm_request_error"
    LLM_REQUEST_DURATION = "llm_request_duration_ms"
    LLM_TOKEN_USAGE = "llm_token_usage"

