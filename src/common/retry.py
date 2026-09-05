# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""
重试机制

提供异步重试装饰器和工具函数。
"""

import asyncio
import functools
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


def async_retry(
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple[type[Exception], ...] = (Exception,),
    on_retry: Callable[[Exception, int], None] | None = None,
    logger_name: str = None
):
    """
    异步重试装饰器

    Args:
        max_attempts: 最大尝试次数
        delay: 初始延迟时间（秒）
        backoff: 退避系数（每次重试延迟乘以这个值）
        exceptions: 需要重试的异常类型
        on_retry: 重试时的回调函数 (exception, attempt) -> None
        logger_name: 日志记录器名称

    Usage:
        @async_retry(max_attempts=3, delay=1.0)
        async def call_api():
            ...

        @async_retry(max_attempts=5, exceptions=(ConnectionError, TimeoutError))
        async def call_external_service():
            ...
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            current_delay = delay
            last_exception = None

            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e

                    if attempt < max_attempts:
                        logger_msg = f"[{func.__name__}] Attempt {attempt}/{max_attempts} failed: {e}"
                        if logger_name:
                            logging.getLogger(logger_name).warning(
                                f"{logger_msg} Retrying in {current_delay}s..."
                            )
                        else:
                            logger.warning(logger_msg)

                        if on_retry:
                            on_retry(e, attempt)

                        await asyncio.sleep(current_delay)
                        current_delay *= backoff
                    else:
                        error_msg = f"[{func.__name__}] All {max_attempts} attempts failed. Last error: {e}"
                        if logger_name:
                            logging.getLogger(logger_name).error(error_msg)
                        else:
                            logger.error(error_msg)

            raise last_exception

        return wrapper
    return decorator


class RetryContext:
    """
    重试上下文

    手动控制重试逻辑。
    """

    def __init__(
        self,
        max_attempts: int = 3,
        delay: float = 1.0,
        backoff: float = 2.0
    ):
        self.max_attempts = max_attempts
        self.delay = delay
        self.backoff = backoff
        self.current_attempt = 0
        self.current_delay = delay
        self.last_exception: Exception | None = None
        self.start_time: datetime | None = None
        self.end_time: datetime | None = None

    def __enter__(self) -> 'RetryContext':
        self.start_time = datetime.now()
        self.current_attempt = 0
        self.current_delay = self.delay
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.end_time = datetime.now()

    async def execute(self, func: Callable, *args, **kwargs) -> Any:
        """
        执行函数，自动重试

        Args:
            func: 要执行的异步函数
            *args, **kwargs: 函数参数

        Returns:
            函数返回值

        Raises:
            最后一次尝试的异常
        """
        for attempt in range(1, self.max_attempts + 1):
            self.current_attempt = attempt

            try:
                result = await func(*args, **kwargs)
                return result
            except Exception as e:
                self.last_exception = e

                if attempt < self.max_attempts:
                    logger.warning(
                        f"Attempt {attempt}/{self.max_attempts} failed: {e}. "
                        f"Retrying in {self.current_delay}s..."
                    )
                    await asyncio.sleep(self.current_delay)
                    self.current_delay *= self.backoff
                else:
                    logger.error(
                        f"All {self.max_attempts} attempts failed. Last error: {e}"
                    )

        raise self.last_exception

    @property
    def total_duration(self) -> timedelta | None:
        """获取总耗时"""
        if self.start_time and self.end_time:
            return self.end_time - self.start_time
        return None

    @property
    def is_exhausted(self) -> bool:
        """是否已用完所有尝试"""
        return self.current_attempt >= self.max_attempts


# 便捷函数
async def retry_async(
    func: Callable,
    *args,
    max_attempts: int = 3,
    delay: float = 1.0,
    exceptions: tuple[type[Exception], ...] = (Exception,),
    **kwargs
) -> Any:
    """
    便捷函数：异步重试

    Usage:
        result = await retry_async(
            some_async_function,
            arg1, arg2,
            max_attempts=3,
            delay=1.0
        )
    """
    decorator = async_retry(max_attempts=max_attempts, delay=delay, exceptions=exceptions)
    decorated_func = decorator(func)
    return await decorated_func(*args, **kwargs)
