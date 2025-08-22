"""
重试机制
核心功能：
- 指数退避重试
- 自定义重试条件
- 超时控制
- 简单装饰器接口
"""

import time
import random
import asyncio
from typing import Callable, Optional, Type, Tuple, Any, Union
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
        def fetch_data():
            # 可能失败的网络请求
            pass
    """
    def decorator(func):
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            last_exception = None
            
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    
                    # 检查是否应该重试
                    should_retry = False
                    
                    # 检查异常类型
                    if retry_on:
                        should_retry = isinstance(e, retry_on)
                    else:
                        should_retry = True  # 默认重试所有异常
                    
                    # 检查自定义条件
                    if should_retry and retry_condition:
                        should_retry = retry_condition(e)
                    
                    # 如果不应该重试或已达到最大次数，抛出异常
                    if not should_retry or attempt == max_retries:
                        raise RetryError(
                            f"Max retries ({max_retries}) exceeded for {func.__name__}",
                            last_exception=e
                        ) from e
                    
                    # 计算延迟时间
                    delay = min(base_delay * (exponential_base ** attempt), max_delay)
                    
                    # 添加抖动
                    if jitter:
                        delay = delay * (0.5 + random.random())
                    
                    time.sleep(delay)
            
            # 不应该到达这里
            raise RetryError(
                f"Unexpected retry failure for {func.__name__}",
                last_exception=last_exception
            )
        
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
                    
                    # 检查异常类型
                    if retry_on:
                        should_retry = isinstance(e, retry_on)
                    else:
                        should_retry = True
                    
                    # 检查自定义条件
                    if should_retry and retry_condition:
                        should_retry = retry_condition(e)
                    
                    # 如果不应该重试或已达到最大次数，抛出异常
                    if not should_retry or attempt == max_retries:
                        raise RetryError(
                            f"Max retries ({max_retries}) exceeded for {func.__name__}",
                            last_exception=e
                        ) from e
                    
                    # 计算延迟时间
                    delay = min(base_delay * (exponential_base ** attempt), max_delay)
                    
                    # 添加抖动
                    if jitter:
                        delay = delay * (0.5 + random.random())
                    
                    await asyncio.sleep(delay)
            
            # 不应该到达这里
            raise RetryError(
                f"Unexpected retry failure for {func.__name__}",
                last_exception=last_exception
            )
        
        # 根据函数类型返回对应的包装器
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    
    return decorator


def simple_retry(
    max_retries: int = 3,
    delay: float = 1.0,
    retry_on: Optional[Tuple[Type[Exception], ...]] = None,
):
    """
    简单重试装饰器（固定延迟）
    
    Args:
        max_retries: 最大重试次数
        delay: 固定延迟时间（秒）
        retry_on: 需要重试的异常类型
    
    Example:
        @simple_retry(max_retries=2, delay=0.5)
        def process_data():
            pass
    """
    return exponential_backoff(
        max_retries=max_retries,
        base_delay=delay,
        exponential_base=1.0,  # 指数为1，实现固定延迟
        jitter=False,
        retry_on=retry_on
    )


class Retry:
    """
    可配置的重试类（用于更复杂的场景）
    
    Example:
        retry = Retry(max_retries=3)
        result = retry.call(some_function, arg1, arg2)
    """
    
    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        exponential_base: float = 2.0,
        jitter: bool = True,
        retry_on: Optional[Tuple[Type[Exception], ...]] = None,
        retry_condition: Optional[Callable[[Exception], bool]] = None,
    ):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
        self.jitter = jitter
        self.retry_on = retry_on
        self.retry_condition = retry_condition
    
    def call(self, func: Callable, *args, **kwargs) -> Any:
        """执行函数并在失败时重试"""
        @exponential_backoff(
            max_retries=self.max_retries,
            base_delay=self.base_delay,
            max_delay=self.max_delay,
            exponential_base=self.exponential_base,
            jitter=self.jitter,
            retry_on=self.retry_on,
            retry_condition=self.retry_condition,
        )
        def wrapper():
            return func(*args, **kwargs)
        
        return wrapper()
    
    async def async_call(self, func: Callable, *args, **kwargs) -> Any:
        """异步执行函数并在失败时重试"""
        @exponential_backoff(
            max_retries=self.max_retries,
            base_delay=self.base_delay,
            max_delay=self.max_delay,
            exponential_base=self.exponential_base,
            jitter=self.jitter,
            retry_on=self.retry_on,
            retry_condition=self.retry_condition,
        )
        async def wrapper():
            if asyncio.iscoroutinefunction(func):
                return await func(*args, **kwargs)
            else:
                return func(*args, **kwargs)
        
        return await wrapper()


# 预定义的重试策略
def network_retry():
    """网络请求重试策略"""
    return exponential_backoff(
        max_retries=3,
        base_delay=1.0,
        retry_on=(ConnectionError, TimeoutError, OSError),
        retry_condition=lambda e: "rate limit" in str(e).lower()
    )


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


if __name__ == "__main__":
    # 测试代码
    import random
    
    # 测试同步重试
    @simple_retry(max_retries=2, delay=0.5)
    def unreliable_function():
        if random.random() < 0.7:
            print("  失败!")
            raise ConnectionError("Random failure")
        print("  成功!")
        return "Success"
    
    print("测试简单重试:")
    try:
        result = unreliable_function()
        print(f"结果: {result}")
    except RetryError as e:
        print(f"重试失败: {e}")
        if e.last_exception:
            print(f"最后的异常: {e.last_exception}")
    
    # 测试异步重试
    async def test_async():
        @exponential_backoff(max_retries=3, base_delay=0.5)
        async def async_unreliable():
            if random.random() < 0.5:
                print("  异步失败!")
                raise TimeoutError("Async timeout")
            print("  异步成功!")
            return "Async Success"
        
        print("\n测试异步重试:")
        try:
            result = await async_unreliable()
            print(f"结果: {result}")
        except RetryError as e:
            print(f"重试失败: {e}")
    
    # 运行异步测试
    asyncio.run(test_async())
    
    # 测试Retry类
    print("\n测试Retry类:")
    retry = Retry(max_retries=2, base_delay=0.3)
    
    def another_unreliable():
        if random.random() < 0.6:
            raise ValueError("Random error")
        return "OK"
    
    try:
        result = retry.call(another_unreliable)
        print(f"结果: {result}")
    except RetryError as e:
        print(f"重试失败: {e}")