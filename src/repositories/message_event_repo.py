"""
MessageEvent Repository

事件持久化层，支持 batch write 和按 message/type 查询。
"""

from typing import List, Optional, Dict, Any

from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import MessageEvent
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


class MessageEventRepository:
    """
    MessageEvent 仓库

    职责：
    - batch_create: 执行完成时批量写入事件
    - get_by_message: 按 message_id 查询事件链
    - get_by_type: 按类型过滤
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def reset(self) -> None:
        """Rollback 当前事务，使 session 恢复可用状态。"""
        await self.session.rollback()

    async def batch_create(self, events: List[Dict[str, Any]]) -> List[MessageEvent]:
        """
        批量创建事件记录

        Args:
            events: 事件列表，每个元素包含：
                - message_id: str
                - event_type: str
                - agent_name: Optional[str]
                - data: Optional[dict]
                - created_at: Optional[datetime]

        Returns:
            创建的 MessageEvent 列表
        """
        if not events:
            return []

        db_events = []
        for event_data in events:
            event = MessageEvent(
                event_id=event_data.get("event_id"),
                message_id=event_data["message_id"],
                event_type=event_data["event_type"],
                agent_name=event_data.get("agent_name"),
                data=event_data.get("data"),
            )
            if "created_at" in event_data and event_data["created_at"]:
                event.created_at = event_data["created_at"]
            db_events.append(event)

        self.session.add_all(db_events)
        try:
            await self.session.flush()
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            # Verify this is a duplicate event_id conflict (previous retry committed),
            # not a different integrity violation (e.g. FK to messages).
            event_ids = [e.get("event_id") for e in events if e.get("event_id")]
            if event_ids:
                stmt = select(func.count()).select_from(MessageEvent).where(
                    MessageEvent.event_id.in_(event_ids)
                )
                result = await self.session.execute(stmt)
                existing_count = result.scalar() or 0
                if existing_count > 0:
                    logger.info(f"Events already persisted (duplicate event_id), skipping batch of {len(db_events)}")
                    return []
            # Not a duplicate — re-raise the real integrity error
            raise

        logger.debug(f"Batch created {len(db_events)} message events")
        return db_events

    async def get_by_message(self, message_id: str) -> List[MessageEvent]:
        """
        获取消息的所有事件

        Args:
            message_id: 消息ID

        Returns:
            事件列表（按 id 排序，即时间顺序）
        """
        stmt = (
            select(MessageEvent)
            .where(MessageEvent.message_id == message_id)
            .order_by(MessageEvent.id)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_type(
        self, message_id: str, event_type: str
    ) -> List[MessageEvent]:
        """
        按类型过滤消息的事件

        Args:
            message_id: 消息ID
            event_type: 事件类型

        Returns:
            过滤后的事件列表
        """
        stmt = (
            select(MessageEvent)
            .where(
                MessageEvent.message_id == message_id,
                MessageEvent.event_type == event_type,
            )
            .order_by(MessageEvent.id)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
