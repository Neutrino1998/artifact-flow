"""
StreamTransport Protocol — 流式事件传输抽象

定义事件推送/消费的传输层接口，不包含执行语义（如 cancelled）。
当前 StreamManager 通过鸭子类型满足此协议。
未来可替换为 Redis Streams 实现。
"""

from typing import Protocol, runtime_checkable, Optional, Dict, Any, AsyncGenerator


class StreamNotFoundError(Exception):
    """Stream 不存在异常"""
    def __init__(self, message_id: str):
        self.message_id = message_id
        super().__init__(f"Stream '{message_id}' not found")


class StreamAlreadyExistsError(Exception):
    """Stream 已存在异常"""
    def __init__(self, message_id: str):
        self.message_id = message_id
        super().__init__(f"Stream '{message_id}' already exists")


@runtime_checkable
class StreamTransport(Protocol):
    """流式事件传输协议"""

    async def create_stream(self, stream_id: str, owner_user_id: Optional[str] = None) -> None: ...
    async def push_event(self, stream_id: str, event: Dict[str, Any]) -> bool: ...
    async def consume_events(
        self,
        stream_id: str,
        heartbeat_interval: Optional[float] = None,
        user_id: Optional[str] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]: ...
    async def close_stream(self, stream_id: str) -> bool: ...
    def get_stream_status(self, stream_id: str) -> Optional[str]: ...
