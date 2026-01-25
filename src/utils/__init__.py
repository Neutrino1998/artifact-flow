"""
工具模块
提供日志、重试等基础功能
"""

# 日志相关
from .logger import (
    Logger,
    get_logger,
    debug,
    info,
    warning,
    error,
    critical,
    exception,
)

# 重试机制相关
from .retry import (
    RetryError,
    exponential_backoff,
    api_retry,
)

__all__ = [
    # 日志
    'Logger',
    'get_logger',
    'debug',
    'info',
    'warning',
    'error',
    'critical',
    'exception',

    # 重试
    'RetryError',
    'exponential_backoff',
    'api_retry',
]

# 版本信息
__version__ = '0.1.0'