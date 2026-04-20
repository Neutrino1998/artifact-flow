"""
对话管理器

职责：
1. 管理对话和消息的生命周期
2. 格式化对话历史
3. 通过 Repository 进行持久化
"""

from typing import Dict, List, Optional, Any
from datetime import datetime

from repositories.conversation_repo import ConversationRepository
from repositories.message_event_repo import MessageEventRepository
from repositories.base import NotFoundError, DuplicateError
from db.models import Conversation, Message, MessageEvent
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")

# Title 生成配置
TITLE_MAX_LENGTH = 50  # 最大标题长度


class ConversationManager:
    """
    对话管理器

    职责：
    - 管理对话和消息的生命周期
    - 通过 Repository 进行持久化
    - 格式化对话历史

    使用方式：
        async with db_manager.session() as session:
            repo = ConversationRepository(session)
            manager = ConversationManager(repo)
            await manager.start_conversation(...)
    """

    def __init__(self, repository: Optional[ConversationRepository] = None):
        """
        初始化 ConversationManager

        Args:
            repository: ConversationRepository 实例（可以为 None）
        """
        self.repository = repository
        logger.info("ConversationManager initialized")

    def _ensure_repository(self) -> ConversationRepository:
        """确保 Repository 已设置"""
        if self.repository is None:
            raise RuntimeError("ConversationManager: repository not configured")
        return self.repository

    @staticmethod
    def _generate_title(content: str) -> str:
        """
        从消息内容生成对话标题

        策略：取第一行内容，截断到最大长度

        Args:
            content: 用户消息内容

        Returns:
            生成的标题
        """
        first_line = content.strip().split('\n')[0].strip()
        if len(first_line) > TITLE_MAX_LENGTH:
            return first_line[:TITLE_MAX_LENGTH] + "..."
        return first_line

    # ========================================
    # 对话操作
    # ========================================

    async def start_conversation_async(
        self,
        conversation_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> str:
        """
        开始新对话（支持持久化）

        Args:
            conversation_id: 指定的对话ID（None 则自动生成）
            user_id: 用户ID（认证隔离）

        Returns:
            对话ID
        """
        from uuid import uuid4

        conv_id = conversation_id or f"conv-{uuid4().hex}"

        if self.repository:
            try:
                await self.repository.create_conversation(
                    conversation_id=conv_id,
                    title=None,
                    user_id=user_id,
                    metadata={}
                )
            except DuplicateError:
                logger.debug(f"Conversation {conv_id} already exists")
            except Exception as e:
                logger.warning(f"Failed to persist conversation: {e}")
                raise

        logger.info(f"Started conversation: {conv_id}")
        return conv_id

    async def ensure_conversation_exists(
        self, conversation_id: str, user_id: Optional[str] = None
    ) -> None:
        """
        确保对话存在（不存在则创建）

        Args:
            conversation_id: 对话ID
            user_id: 用户ID（创建时使用）
        """
        if self.repository:
            existing = await self.repository.get_conversation(conversation_id)
            if existing:
                return
        await self.start_conversation_async(conversation_id, user_id=user_id)

    # ========================================
    # 消息操作
    # ========================================

    async def add_message_async(
        self,
        conv_id: str,
        message_id: str,
        user_input: str,
        parent_id: Optional[str] = None
    ) -> Dict:
        """
        添加消息到对话（支持持久化）

        Args:
            conv_id: 对话ID
            message_id: 消息ID
            user_input: 消息内容
            parent_id: 父消息ID（分支时使用）

        Returns:
            消息对象字典
        """
        await self.ensure_conversation_exists(conv_id)

        now = datetime.now().isoformat()

        if self.repository:
            await self.repository.add_message(
                conversation_id=conv_id,
                message_id=message_id,
                user_input=user_input,
                parent_id=parent_id
            )

            # 如果是第一条消息（无 parent），自动生成 title
            if parent_id is None:
                title = self._generate_title(user_input)
                await self.repository.update_title(conv_id, title)
                logger.debug(f"Auto-generated title for conversation {conv_id}: {title}")

        return {
            "message_id": message_id,
            "parent_id": parent_id,
            "user_input": user_input,
            "timestamp": now,
            "response": None,
            "metadata": {}
        }

    async def update_response_async(
        self,
        conv_id: str,
        message_id: str,
        response: str
    ) -> None:
        """
        更新消息的助手响应（支持持久化）

        Args:
            conv_id: 对话ID
            message_id: 消息ID
            response: 助手响应内容
        """
        if self.repository:
            await self.repository.update_response(message_id, response)

    async def get_message_metadata_async(
        self,
        message_id: str,
    ) -> Dict[str, Any]:
        """
        获取消息的 metadata

        Args:
            message_id: 消息ID

        Returns:
            metadata 字典（不存在则返回空字典）
        """
        if self.repository:
            msg = await self.repository.get_message(message_id)
            if msg:
                return msg.metadata_ or {}
        return {}

    async def update_message_metadata_async(
        self,
        conv_id: str,
        message_id: str,
        metadata: Dict[str, Any],
    ) -> None:
        """
        更新消息的 metadata（merge 语义）

        Args:
            conv_id: 对话ID
            message_id: 消息ID
            metadata: 要合并的 metadata 字典
        """
        if self.repository:
            await self.repository.update_message_metadata(message_id, metadata)

    # ========================================
    # 查询操作
    # ========================================

    async def get_active_branch(self, conv_id: str) -> Optional[str]:
        """
        获取对话的活跃分支（当前最新消息ID）

        Args:
            conv_id: 对话ID

        Returns:
            活跃分支的消息ID，如果对话不存在或没有消息则返回 None
        """
        if self.repository:
            conv = await self.repository.get_conversation(conv_id)
            if conv:
                return conv.active_branch or None
        return None

    async def load_event_history_async(
        self,
        conv_id: str,
        to_message_id: Optional[str] = None,
    ) -> List[Any]:
        """
        加载对话 path 上的完整事件链，转为 is_historical=True 的 ExecutionEvent 列表。

        用于 turn 开始时初始化 state["events"]：返回列表会作为 state["events"]
        的起始内容，引擎执行中新产生的事件（is_historical=False）追加其后。

        Args:
            conv_id: 对话 ID
            to_message_id: 目标消息 ID（None 则使用 active_branch）

        Returns:
            按全局 id 升序的 ExecutionEvent 列表（is_historical=True）
        """
        from core.events import ExecutionEvent

        repo = self._ensure_repository()
        path = await repo.get_conversation_path(conv_id, to_message_id)
        if not path:
            return []

        message_ids = [msg.id for msg in path]
        event_repo = MessageEventRepository(repo.session)
        db_events = await event_repo.get_by_message_ids(message_ids)

        return [
            ExecutionEvent(
                event_type=ev.event_type,
                agent_name=ev.agent_name,
                data=ev.data,
                event_id=ev.event_id,
                created_at=ev.created_at,
                is_historical=True,
            )
            for ev in db_events
        ]

    # ========================================
    # 列表操作
    # ========================================

    async def list_conversations_async(
        self,
        limit: int = 50,
        offset: int = 0,
        user_id: Optional[str] = None,
        title_query: Optional[str] = None,
    ) -> List[Dict]:
        """
        列出所有对话

        Args:
            limit: 限制数量
            offset: 跳过数量
            user_id: 按用户ID筛选
            title_query: 按标题模糊搜索

        Returns:
            对话信息字典列表
        """
        repo = self._ensure_repository()
        conversations = await repo.list_conversations(
            limit=limit,
            offset=offset,
            user_id=user_id,
            title_query=title_query,
            load_messages=True
        )
        return [
            {
                "conversation_id": conv.id,
                "title": conv.title,
                "message_count": len(conv.messages) if conv.messages else 0,
                "created_at": conv.created_at.isoformat(),
                "updated_at": conv.updated_at.isoformat()
            }
            for conv in conversations
        ]

    async def count_conversations_async(self, user_id: Optional[str] = None, title_query: Optional[str] = None) -> int:
        """
        统计对话总数

        Args:
            user_id: 按用户ID筛选
            title_query: 按标题模糊搜索

        Returns:
            对话总数
        """
        repo = self._ensure_repository()
        return await repo.count_conversations(user_id=user_id, title_query=title_query)

    # ========================================
    # Router 代理方法
    # ========================================

    async def verify_ownership(self, conversation_id: str, user_id: str) -> bool:
        """
        校验 conversation 归属用户

        返回 False 而非 403，遵循 "404 not 403" 安全策略。

        Args:
            conversation_id: 对话ID
            user_id: 用户ID

        Returns:
            True 如果归属匹配，False 如果不存在或不匹配
        """
        repo = self._ensure_repository()
        conv = await repo.get_conversation(conversation_id)
        if not conv or conv.user_id != user_id:
            return False
        return True

    async def get_conversation_detail(self, conversation_id: str) -> Optional[Conversation]:
        """
        获取对话详情（含消息）

        Args:
            conversation_id: 对话ID

        Returns:
            对话对象（预加载消息），不存在则返回 None
        """
        repo = self._ensure_repository()
        return await repo.get_conversation(conversation_id, load_messages=True)

    async def get_conversation_messages(self, conversation_id: str) -> List[Message]:
        """
        获取对话的所有消息

        Args:
            conversation_id: 对话ID

        Returns:
            消息列表（按创建时间排序）
        """
        repo = self._ensure_repository()
        return await repo.get_conversation_messages(conversation_id)

    async def get_message(self, message_id: str) -> Optional[Message]:
        """
        获取消息

        Args:
            message_id: 消息ID

        Returns:
            消息对象，不存在则返回 None
        """
        repo = self._ensure_repository()
        return await repo.get_message(message_id)

    # ========================================
    # Event / Admin 查询（封装 MessageEventRepository 访问）
    # ========================================

    async def get_message_events(
        self,
        message_id: str,
        event_type: Optional[str] = None,
    ) -> List[MessageEvent]:
        """获取消息的事件链（用于历史回放和可观测性）。

        Router 不得直接实例化 MessageEventRepository — 通过本方法复用
        ConversationManager 持有的 session。
        """
        repo = self._ensure_repository()
        event_repo = MessageEventRepository(repo.session)
        if event_type:
            return await event_repo.get_by_type(message_id, event_type)
        return await event_repo.get_by_message(message_id)

    async def list_admin_conversations(
        self,
        *,
        limit: int,
        offset: int,
        title_query: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> tuple[List[Conversation], int, Dict[str, str]]:
        """Admin 视图：返回 (conversations, total, user_id → display_name 映射)。

        conversations 预加载 messages 以支持计数；user_names 用于避免
        N+1 查询。调用方需在 session 关闭前完成序列化。
        """
        from sqlalchemy import select
        from db.models import User

        repo = self._ensure_repository()
        conversations = await repo.list_conversations(
            limit=limit,
            offset=offset,
            title_query=title_query,
            user_id=user_id,
            load_messages=True,
        )
        total = await repo.count_conversations(
            title_query=title_query,
            user_id=user_id,
        )

        user_names: Dict[str, str] = {}
        user_ids = {c.user_id for c in conversations if c.user_id}
        if user_ids:
            stmt = select(User.id, User.display_name, User.username).where(
                User.id.in_(user_ids)
            )
            result = await repo.session.execute(stmt)
            for uid, display_name, username in result.all():
                user_names[uid] = display_name or username

        return conversations, total, user_names

    async def get_admin_conversation_events(
        self,
        conv_id: str,
    ) -> Optional[tuple[Conversation, List[Message], List[MessageEvent]]]:
        """Admin 视图：取对话 + 所有消息 + 跨消息事件流（按 id 升序）。

        Returns:
            (conversation, messages, events) 元组；对话不存在时返回 None。
        """
        repo = self._ensure_repository()
        conv = await repo.get_conversation(conv_id)
        if not conv:
            return None

        messages = await repo.get_conversation_messages(conv_id)
        event_repo = MessageEventRepository(repo.session)
        events = await event_repo.get_by_conversation(conv_id)
        return conv, messages, events

    async def delete_conversation(self, conversation_id: str) -> bool:
        """
        删除对话（级联删除消息和 Artifacts）

        Args:
            conversation_id: 对话ID

        Returns:
            是否成功删除
        """
        repo = self._ensure_repository()
        return await repo.delete_conversation(conversation_id)
