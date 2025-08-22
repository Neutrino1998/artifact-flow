"""
精简的日志系统
只保留核心功能：
- 分级日志记录
- 控制台和文件输出
- 日志轮转
- 调试模式切换
"""

import os
import sys
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, Union
from logging.handlers import RotatingFileHandler


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
    
    def format(self, record):
        if sys.stdout.isatty():  # 只在终端中使用颜色
            levelname = record.levelname
            if levelname in self.COLORS:
                record.levelname = f"{self.COLORS[levelname]}{levelname}{self.COLORS['RESET']}"
                record.msg = f"{self.COLORS[levelname]}{record.msg}{self.COLORS['RESET']}"
        return super().format(record)


class Logger:
    """简单的日志记录器"""
    
    def __init__(
        self,
        name: str = "ArtifactFlow",
        log_dir: str = "logs",
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
        
        # 创建日志目录
        if file:
            self.log_dir = Path(log_dir)
            self.log_dir.mkdir(parents=True, exist_ok=True)
        
        # 创建logger
        self.logger = logging.getLogger(name)
        self.logger.setLevel(logging.DEBUG if self.debug_mode else logging.INFO)
        self.logger.handlers.clear()
        
        # 添加控制台输出
        if console:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setFormatter(ColoredFormatter(
                '%(asctime)s [%(levelname)s] %(filename)s:%(funcName)s:%(lineno)d - %(message)s',
                datefmt='%H:%M:%S'
            ))
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
            file_handler.setFormatter(logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(funcName)s:%(lineno)d - %(message)s'
            ))
            self.logger.addHandler(file_handler)
            
            # 错误日志（单独文件）
            error_handler = RotatingFileHandler(
                self.log_dir / f"{name.lower()}_error.log",
                maxBytes=5*1024*1024,  # 5MB
                backupCount=3,
                encoding='utf-8'
            )
            error_handler.setLevel(logging.ERROR)
            error_handler.setFormatter(logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(funcName)s:%(lineno)d - %(message)s'
            ))
            self.logger.addHandler(error_handler)
    
    def set_debug(self, enabled: bool):
        """切换调试模式"""
        self.debug_mode = enabled
        self.logger.setLevel(logging.DEBUG if enabled else logging.INFO)
    
    # 基础日志方法
    def debug(self, msg: str, *args, **kwargs):
        self.logger.debug(msg, *args, **kwargs)
    
    def info(self, msg: str, *args, **kwargs):
        self.logger.info(msg, *args, **kwargs)
    
    def warning(self, msg: str, *args, **kwargs):
        self.logger.warning(msg, *args, **kwargs)
    
    def error(self, msg: str, *args, **kwargs):
        self.logger.error(msg, *args, **kwargs)
    
    def critical(self, msg: str, *args, **kwargs):
        self.logger.critical(msg, *args, **kwargs)
    
    def exception(self, msg: str, *args, **kwargs):
        """记录异常信息（自动包含堆栈）"""
        self.logger.exception(msg, *args, **kwargs)


# 默认logger实例
_default_logger = None


def get_logger(name: Optional[str] = None, **kwargs) -> Logger:
    """
    获取日志记录器
    
    Args:
        name: 日志记录器名称，None则返回默认logger
        **kwargs: Logger构造函数参数
    
    Returns:
        Logger实例
    """
    global _default_logger
    
    if name is None:
        if _default_logger is None:
            # 从环境变量读取配置
            debug = os.getenv('DEBUG', 'false').lower() == 'true'
            _default_logger = Logger(debug=debug, **kwargs)
        return _default_logger
    
    return Logger(name=name, **kwargs)


# 全局便捷函数
def debug(msg: str, *args, **kwargs):
    get_logger().debug(msg, *args, **kwargs)

def info(msg: str, *args, **kwargs):
    get_logger().info(msg, *args, **kwargs)

def warning(msg: str, *args, **kwargs):
    get_logger().warning(msg, *args, **kwargs)

def error(msg: str, *args, **kwargs):
    get_logger().error(msg, *args, **kwargs)

def critical(msg: str, *args, **kwargs):
    get_logger().critical(msg, *args, **kwargs)

def exception(msg: str, *args, **kwargs):
    get_logger().exception(msg, *args, **kwargs)


if __name__ == "__main__":
    # 测试代码
    logger = get_logger("TestLogger", debug=True)
    
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