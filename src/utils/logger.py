"""
精简的日志系统
只保留核心功能：
- 分级日志记录
- 控制台和文件输出
- 日志轮转
- 调试模式切换
- 请求级上下文追踪（contextvars）
"""

import os
import sys
import json
import logging
import contextvars
from pathlib import Path
from datetime import datetime
from typing import Optional, Union, Dict
from logging.handlers import RotatingFileHandler


# ── 请求级上下文 ──────────────────────────────────────────────
# 利用 contextvars 天然的 asyncio 支持：
# - 每个 asyncio.Task 自动继承创建时的 context 副本
# - background task 中的日志会自动携带创建时设置的 message_id / conv_id
_request_ctx: contextvars.ContextVar[dict] = contextvars.ContextVar(
    'request_ctx', default={}
)

# request_id 是独立 contextvar，刻意不并入 _request_ctx：
# set_request_context 是整体替换（.set(ctx)），并入会被一次 chat 调用冲掉。
# 覆盖面也不同：request_id 是「每个 HTTP 请求」的通用兜底（上传 / auth / admin
# 等无 message_id 的路径都有），message_id / conv_id 是「一整轮对话」的桥（横跨
# 多个请求 + 后台引擎任务，且是日志 ↔ admin 监控页 ↔ MessageEvent 表的唯一关联）。
_request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    'request_id', default=""
)


def set_request_id(request_id: str):
    """设置当前 async context 的 request_id，返回可用于 reset 的 token。

    由 RequestContextMiddleware 在请求入口调用，finally 中 reset。
    asyncio.create_task 会拷贝创建时的 context，故后台引擎任务里
    get_request_id() 仍能拿到发起该任务的请求的 request_id。
    """
    return _request_id_ctx.set(request_id)


def reset_request_id(token) -> None:
    """恢复 request_id contextvar 到 set 之前的值"""
    _request_id_ctx.reset(token)


def get_request_id() -> str:
    """读取当前 context 的 request_id（无则空字符串）"""
    return _request_id_ctx.get("")


def set_request_context(*, message_id: str = "", conv_id: str = "") -> None:
    """
    设置当前 async context 的请求级日志上下文

    应在请求入口（如 chat.py send_message / resume_execution）调用，
    后续由 asyncio.create_task 创建的 background task 会自动继承。
    """
    ctx = {}
    if message_id:
        ctx["message_id"] = message_id
    if conv_id:
        ctx["conv_id"] = conv_id
    _request_ctx.set(ctx)


def clear_request_context() -> None:
    """清除当前 context 的请求级日志上下文"""
    _request_ctx.set({})


class RequestContextFilter(logging.Filter):
    """
    从 contextvars 读取请求上下文，自动注入到每条日志记录

    注入字段（完整，用于文件日志）：
    - record.request_id: 请求 ID（每个 HTTP 请求，空值 'no-req'）
    - record.message_id: 消息 ID（一整轮对话，空值 'no-ctx'）
    - record.conv_id: 对话 ID（一整轮对话，空值 'no-ctx'）

    注入字段（截短，前缀 + 4 字符，控制台只显示 request_id 避免挤爆行）：
    - record.request_id_short
    - record.message_id_short
    - record.conv_id_short
    """

    @staticmethod
    def _shorten_id(full_id: str) -> str:
        """截短 ID：'conv-00054133d0d4496b...' → 'conv-0005'"""
        if full_id == "no-ctx":
            return full_id
        parts = full_id.split("-", 1)
        if len(parts) == 2 and len(parts[1]) > 4:
            return f"{parts[0]}-{parts[1][:4]}"
        return full_id

    def filter(self, record):
        ctx = _request_ctx.get({})
        record.message_id = ctx.get("message_id", "no-ctx")
        record.conv_id = ctx.get("conv_id", "no-ctx")
        record.request_id = _request_id_ctx.get("") or "no-req"
        record.message_id_short = self._shorten_id(record.message_id)
        record.conv_id_short = self._shorten_id(record.conv_id)
        record.request_id_short = self._shorten_id(record.request_id)
        return True


class ColoredFormatter(logging.Formatter):
    """彩色控制台输出"""
    
    COLORS = {
        'DEBUG': '\033[36m',      # 青色
        'INFO': '\033[32m',       # 绿色
        'WARNING': '\033[33m',    # 黄色
        'ERROR': '\033[31m',      # 红色
        'CRITICAL': '\033[35m',   # 紫色
        'RESET': '\033[0m',       # 重置
    }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._for_console = False  # 默认不启用颜色
    
    def format(self, record):
        # 只有明确标记为控制台输出时才使用颜色
        if self._for_console and sys.stdout.isatty():
            # 创建record的副本，避免修改原始record影响其他handler
            import copy
            colored_record = copy.copy(record)
            
            levelname = colored_record.levelname
            if levelname in self.COLORS:
                colored_record.levelname = f"{self.COLORS[levelname]}{levelname}{self.COLORS['RESET']}"
                colored_record.msg = f"{self.COLORS[levelname]}{colored_record.msg}{self.COLORS['RESET']}"
            
            return super().format(colored_record)
        else:
            return super().format(record)


class Logger:
    """简单的日志记录器"""
    
    def __init__(
        self,
        name: str = "ArtifactFlow",
        log_dir: str = "data/logs",
        debug: bool = False,
        console: bool = True,
        file: bool = True,
    ):
        """
        初始化日志记录器
        
        Args:
            name: 日志记录器名称
            log_dir: 日志文件目录
            debug: 是否开启调试模式
            console: 是否输出到控制台
            file: 是否输出到文件
        """
        self.name = name
        self.debug_mode = debug  # 改为 debug_mode 避免与方法名冲突

        # ARTIFACTFLOW_LOG_DIR 全局重定向日志目录,优先级高于构造参数:
        # 测试隔离到 tests/logs(否则 pytest 故意抛异常的路由把 traceback 灌进
        # 生产 data/logs),部署时也可落到挂载卷。
        log_dir = os.environ.get("ARTIFACTFLOW_LOG_DIR") or log_dir

        # 创建日志目录
        if file:
            self.log_dir = Path(log_dir)
            self.log_dir.mkdir(parents=True, exist_ok=True)
        
        # 创建logger
        self.logger = logging.getLogger(name)
        self.logger.setLevel(logging.DEBUG if self.debug_mode else logging.INFO)
        self.logger.handlers.clear()
        
        # 请求上下文 Filter（所有 handler 共享）
        context_filter = RequestContextFilter()

        # 添加控制台输出
        if console:
            console_handler = logging.StreamHandler(sys.stdout)
            console_formatter = ColoredFormatter(
                '%(asctime)s [%(levelname)s] [%(request_id_short)s] %(filename)s:%(funcName)s:%(lineno)d - %(message)s',
                datefmt='%H:%M:%S'
            )
            console_formatter._for_console = True  # 启用颜色
            console_handler.addFilter(context_filter)
            console_handler.setFormatter(console_formatter)
            self.logger.addHandler(console_handler)

        # 添加文件输出
        if file:
            # 常规日志
            file_handler = RotatingFileHandler(
                self.log_dir / f"{name.lower()}.log",
                maxBytes=10*1024*1024,  # 10MB
                backupCount=5,
                encoding='utf-8'
            )
            # 文件使用无颜色的普通formatter
            file_formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - [%(request_id)s|%(conv_id)s|%(message_id)s] %(filename)s:%(funcName)s:%(lineno)d - %(message)s'
            )
            file_handler.addFilter(context_filter)
            file_handler.setFormatter(file_formatter)
            self.logger.addHandler(file_handler)

            # 错误日志（单独文件）
            error_handler = RotatingFileHandler(
                self.log_dir / f"{name.lower()}_error.log",
                maxBytes=5*1024*1024,  # 5MB
                backupCount=3,
                encoding='utf-8'
            )
            error_handler.setLevel(logging.ERROR)
            error_handler.addFilter(context_filter)
            error_handler.setFormatter(file_formatter)  # 同样使用无颜色formatter
            self.logger.addHandler(error_handler)
    
    def set_debug(self, enabled: bool):
        """切换调试模式"""
        self.debug_mode = enabled
        self.logger.setLevel(logging.DEBUG if enabled else logging.INFO)
    
    # 基础日志方法
    def debug(self, msg: str, *args, **kwargs):
        kwargs['stacklevel'] = kwargs.get('stacklevel', 2)  # 默认为2，但允许覆盖
        self.logger.debug(msg, *args, **kwargs)

    def info(self, msg: str, *args, **kwargs):
        kwargs['stacklevel'] = kwargs.get('stacklevel', 2)
        self.logger.info(msg, *args, **kwargs)

    def warning(self, msg: str, *args, **kwargs):
        kwargs['stacklevel'] = kwargs.get('stacklevel', 2)
        self.logger.warning(msg, *args, **kwargs)

    def error(self, msg: str, *args, **kwargs):
        kwargs['stacklevel'] = kwargs.get('stacklevel', 2)
        self.logger.error(msg, *args, **kwargs)

    def critical(self, msg: str, *args, **kwargs):
        kwargs['stacklevel'] = kwargs.get('stacklevel', 2)
        self.logger.critical(msg, *args, **kwargs)

    def exception(self, msg: str, *args, **kwargs):
        """记录异常信息（自动包含堆栈）"""
        kwargs['stacklevel'] = kwargs.get('stacklevel', 2)
        self.logger.exception(msg, *args, **kwargs)

# 默认logger实例
_default_logger = None

# 添加全局logger缓存
_logger_cache: Dict[str, Logger] = {}
_global_debug = False  # 全局debug开关

def get_logger(name: Optional[str] = None, **kwargs) -> Logger:
    """
    获取日志记录器（带缓存）
    
    Args:
        name: 日志记录器名称，None则返回默认logger
        **kwargs: Logger构造函数参数
    
    Returns:
        Logger实例
    """
    global _default_logger, _logger_cache, _global_debug
    
    if name is None:
        if _default_logger is None:
            _default_logger = Logger(debug=_global_debug, **kwargs)
        return _default_logger

    # 从缓存获取或创建新实例
    if name not in _logger_cache:
        _logger_cache[name] = Logger(name=name, debug=_global_debug, **kwargs)
    
    return _logger_cache[name]


def set_global_debug(enabled: bool):
    """
    设置全局debug模式
    
    Args:
        enabled: 是否启用debug模式
    """
    global _global_debug, _default_logger, _logger_cache
    _global_debug = enabled
    
    # 更新默认logger
    if _default_logger:
        _default_logger.set_debug(enabled)
    
    # 更新所有已创建的logger
    for logger in _logger_cache.values():
        logger.set_debug(enabled)
    
    # 记录变更
    if _default_logger:
        _default_logger.info(f"Global debug mode: {'ENABLED' if enabled else 'DISABLED'}")


def get_all_loggers() -> Dict[str, Logger]:
    """获取所有已创建的logger"""
    result = {}
    if _default_logger:
        result['_default'] = _default_logger
    result.update(_logger_cache)
    return result


# 全局便捷函数
def debug(msg: str, *args, **kwargs):
    kwargs['stacklevel'] = kwargs.get('stacklevel', 2)
    get_logger().debug(msg, *args, **kwargs)

def info(msg: str, *args, **kwargs):
    kwargs['stacklevel'] = kwargs.get('stacklevel', 2)
    get_logger().info(msg, *args, **kwargs)

def warning(msg: str, *args, **kwargs):
    kwargs['stacklevel'] = kwargs.get('stacklevel', 2)
    get_logger().warning(msg, *args, **kwargs)

def error(msg: str, *args, **kwargs):
    kwargs['stacklevel'] = kwargs.get('stacklevel', 2)
    get_logger().error(msg, *args, **kwargs)

def critical(msg: str, *args, **kwargs):
    kwargs['stacklevel'] = kwargs.get('stacklevel', 2)
    get_logger().critical(msg, *args, **kwargs)

def exception(msg: str, *args, **kwargs):
    kwargs['stacklevel'] = kwargs.get('stacklevel', 2)
    get_logger().exception(msg, *args, **kwargs)


if __name__ == "__main__":
    # 测试代码
    logger = get_logger("TestLogger")
    
    logger.debug("Debug message")
    logger.info("Info message")
    logger.warning("Warning message")
    logger.error("Error message")
    
    try:
        1 / 0
    except Exception as e:
        logger.exception("Math error occurred")
    
    # 切换模式
    logger.set_debug(False)
    logger.debug("This won't show in production mode")
    logger.info("This will show")