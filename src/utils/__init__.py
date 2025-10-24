 
"""
工具模块
提供日志、XML解析、重试等基础功能
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

# XML解析相关
from .xml_parser import (
    SimpleXMLParser,
    ToolCall,
    parse_tool_calls,
)

# 重试机制相关
from .retry import (
    RetryError,
    exponential_backoff,
    simple_retry,
    Retry,
    network_retry,
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
    
    # XML解析
    'SimpleXMLParser',
    'ToolCall',
    'parse_tool_calls'
    
    # 重试
    'RetryError',
    'exponential_backoff',
    'simple_retry',
    'Retry',
    'network_retry',
    'api_retry',
]

# 版本信息
__version__ = '0.1.0'