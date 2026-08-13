"""
ErrorCounterHandler — 进程内 ERROR 日志计数器

实例心跳字段之一：面板的「黄色」信号里包含「近期是否出过 ERROR」。
用一个挂在 `ArtifactFlow` logger 上的 `logging.Handler` 累计 ERROR+ 记录数 + 最近
一次时间戳,心跳每次采样时读一份写进 `{af:instance:<id>}`。

为什么挂 `ArtifactFlow` 而非 root:root 会连带第三方库(litellm / sqlalchemy /
asyncpg)的 ERROR 一起计,那些不是「本服务出错」的信号,会把黄色噪声化。应用自身
统一走 `get_logger("ArtifactFlow")`(及其子 logger,propagate 默认 True 会上浮到
这里的 handler),所以挂在这一层既全又不掺外部噪声。

线程安全:emit 只做一次 int 自增 + 一次字符串赋值,GIL 下均为原子操作,不需要锁
(logging 本身也在 handler 层持锁 emit)。observer 不能拖累 observee —— emit 里
任何异常由 logging 框架的 handleError 吞掉,不冒泡到日志调用点。
"""

from __future__ import annotations

import logging
from typing import Optional

from utils.time import utc_now


class ErrorCounterHandler(logging.Handler):
    """累计 ERROR+ 记录数与最近一次时间戳(ISO8601 naive UTC)。"""

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self.count: int = 0
        self.last_ts: Optional[str] = None

    def emit(self, record: logging.LogRecord) -> None:
        # setLevel(ERROR) 已过滤,但显式再挡一次:防未来有人改 level 后语义漂移。
        if record.levelno < logging.ERROR:
            return
        self.count += 1
        self.last_ts = utc_now().isoformat()


# 进程单例:install 一次,心跳侧 get_counter 读同一个对象。
_handler: Optional[ErrorCounterHandler] = None


def install() -> ErrorCounterHandler:
    """幂等地把计数 handler 挂到 `ArtifactFlow` logger。返回单例。

    必须在 `ArtifactFlow` Logger 首次构造之后调用(构造函数会
    `handlers.clear()`,先挂会被清掉)—— 由 lifespan 的 _start_observability
    调用,那时 logger 早已被各模块 import 触发构造。

    残留隐患(当前不可达,备注留痕):`utils.logger` 的裸函数(`logger.error(...)`)
    走 `get_logger(None)` 会**首次**构造一个独立的 `_default_logger`,底层同名
    `logging.getLogger("ArtifactFlow")`,其 `__init__` 也 `handlers.clear()`。若这条
    路径在 install() **之后**才第一次触发,会把本 handler 清掉、`error_count` 冻死在 0。
    src/ 里目前无裸函数调用者(全走 `get_logger("ArtifactFlow")` 缓存实例,import 期已
    构造),故不可达 —— 不加防御机器,仅在此备注,新增裸调用时需重挂。
    """
    global _handler
    if _handler is None:
        _handler = ErrorCounterHandler()
        logging.getLogger("ArtifactFlow").addHandler(_handler)
    return _handler


def get_counter() -> Optional[ErrorCounterHandler]:
    """心跳侧读取;未 install 时返回 None(心跳把 error_count 记 0)。"""
    return _handler


def reset() -> None:
    """测试辅助:摘除 handler 并清单例,避免用例间串状态。"""
    global _handler
    if _handler is not None:
        logging.getLogger("ArtifactFlow").removeHandler(_handler)
        _handler = None
