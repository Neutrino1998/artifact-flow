"""
重试机制
核心功能：
- 指数退避重试装饰器
- API 调用重试策略
"""

import random
import asyncio
from typing import Callable, Optional, Type, Tuple
from functools import wraps


class RetryError(Exception):
    """重试失败异常"""
    def __init__(self, message: str, last_exception: Optional[Exception] = None):
        super().__init__(message)
        self.last_exception = last_exception


def exponential_backoff(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    exponential_base: float = 2.0,
    jitter: bool = True,
    retry_on: Optional[Tuple[Type[Exception], ...]] = None,
    retry_condition: Optional[Callable[[Exception], bool]] = None,
):
    """
    指数退避重试装饰器

    Args:
        max_retries: 最大重试次数
        base_delay: 基础延迟时间（秒）
        max_delay: 最大延迟时间（秒）
        exponential_base: 指数基数
        jitter: 是否添加随机抖动
        retry_on: 需要重试的异常类型元组
        retry_condition: 自定义重试条件函数

    Example:
        @exponential_backoff(max_retries=3, retry_on=(ConnectionError, TimeoutError))
        async def fetch_data():
            pass
    """
    def decorator(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            last_exception = None

            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_exception = e

                    # 检查是否应该重试
                    should_retry = False

                    if retry_on:
                        should_retry = isinstance(e, retry_on)
                    else:
                        should_retry = True

                    if should_retry and retry_condition:
                        should_retry = retry_condition(e)

                    if not should_retry or attempt == max_retries:
                        raise RetryError(
                            f"Max retries ({max_retries}) exceeded for {func.__name__}",
                            last_exception=e
                        ) from e

                    # 计算延迟时间
                    delay = min(base_delay * (exponential_base ** attempt), max_delay)

                    if jitter:
                        delay = delay * (0.5 + random.random())

                    await asyncio.sleep(delay)

            raise RetryError(
                f"Unexpected retry failure for {func.__name__}",
                last_exception=last_exception
            )

        # 本项目只使用 async 函数
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            raise TypeError(f"exponential_backoff only supports async functions, got {func.__name__}")

    return decorator


def api_retry():
    """API调用重试策略"""
    def should_retry(e: Exception) -> bool:
        error_msg = str(e).lower()
        # 重试：限流、超时、服务器错误
        retry_keywords = ['rate limit', 'timeout', '500', '502', '503', '504']
        # 不重试：认证错误、客户端错误
        no_retry_keywords = ['unauthorized', '401', '403', '404', 'invalid api key']

        if any(keyword in error_msg for keyword in no_retry_keywords):
            return False
        if any(keyword in error_msg for keyword in retry_keywords):
            return True
        return False

    return exponential_backoff(
        max_retries=3,
        base_delay=2.0,
        max_delay=30.0,
        retry_condition=should_retry
    )
