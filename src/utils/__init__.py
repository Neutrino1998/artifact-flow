"""
工具模块
提供日志等基础功能
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
]

# 版本信息
__version__ = '0.1.0'