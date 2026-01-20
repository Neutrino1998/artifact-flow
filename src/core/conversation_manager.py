"""
对话管理器

改造说明（v2.1）：
- 从 controller.py 抽离为独立模块
- 使用 ConversationRepository 进行持久化
- 维护内存缓存以提高性能
- 支持依赖注入
- 移除同步方法，统一使用异步接口

职责：
1. 维护用户的对话树
2. 格式化对话历史为可读文本
3. 协调内存缓存和数据库持久化
"""

from typing import Dict, List, Optional, Any
from datetime import datetime
from dataclasses import dataclass, field

from repositories.conversation_repo import ConversationRepository
from repositories.base import NotFoundError, DuplicateError
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")

# Title 生成配置
TITLE_MAX_LENGTH = 50  # 最大标题长度


# ============================================================
# 内存缓存对象
# ============================================================

@dataclass
class MessageCache:
    """消息缓存对象"""
    message_id: str
    parent_id: Optional[str]
    content: str
    thread_id: str
    timestamp: str
    graph_response: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ConversationCache:
    """对话缓存对象"""
    conversation_id: str
    branches: Dict[str, List[str]] = field(default_factory=dict)  # parent_id -> [child_ids]
    messages: Dict[str, MessageCache] = field(default_factory=dict)  # message_id -> MessageCache
    active_branch: str = ""
    created_at: str = ""
    updated_at: str = ""


# ============================================================
# ConversationManager
# ============================================================

class ConversationManager:
    """
    对话管理器

    职责：
    - 管理对话和消息的生命周期
    - 维护内存缓存
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
            repository: ConversationRepository 实例
                       可以为 None，稍后通过 set_repository 设置
        """
        self.repository = repository
        self._cache: Dict[str, ConversationCache] = {}

        logger.info("ConversationManager initialized")

    def set_repository(self, repository: ConversationRepository) -> None:
        """
        设置/更新 Repository

        Args:
            repository: ConversationRepository 实例
        """
        self.repository = repository

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
        # 取第一行，去除首尾空白
        first_line = content.strip().split('\n')[0].strip()

        # 截断到最大长度
        if len(first_line) > TITLE_MAX_LENGTH:
            return first_line[:TITLE_MAX_LENGTH] + "..."
        return first_line

    # ========================================
    # 对话操作
    # ========================================

    async def start_conversation_async(
        self,
        conversation_id: Optional[str] = None
    ) -> str:
        """
        开始新对话（异步版本，支持持久化）

        Args:
            conversation_id: 指定的对话ID

        Returns:
            对话ID
        """
        from uuid import uuid4

        conv_id = conversation_id or f"conv-{uuid4().hex}"
        now = datetime.now().isoformat()

        # 创建内存缓存
        self._cache[conv_id] = ConversationCache(
            conversation_id=conv_id,
            created_at=now,
            updated_at=now
        )

        # 持久化到数据库
        if self.repository:
            try:
                await self.repository.create_conversation(
                    conversation_id=conv_id,
                    title=None,
                    metadata={}
                )
            except DuplicateError:
                # 如果已存在，从数据库加载
                logger.debug(f"Conversation {conv_id} already exists, loading from DB")
                await self._load_conversation_from_db(conv_id)
            except Exception as e:
                logger.warning(f"Failed to persist conversation: {e}")

        logger.info(f"Started conversation: {conv_id}")
        return conv_id

    async def _load_conversation_from_db(self, conversation_id: str) -> None:
        """从数据库加载对话到缓存"""
        if not self.repository:
            return

        conversation = await self.repository.get_conversation(
            conversation_id,
            load_messages=True
        )

        if not conversation:
            return

        # 创建缓存对象
        cache = ConversationCache(
            conversation_id=conversation_id,
            active_branch=conversation.active_branch or "",
            created_at=conversation.created_at.isoformat(),
            updated_at=conversation.updated_at.isoformat()
        )

        # 加载消息
        for msg in conversation.messages:
            cache.messages[msg.id] = MessageCache(
                message_id=msg.id,
                parent_id=msg.parent_id,
                content=msg.content,
                thread_id=msg.thread_id,
                timestamp=msg.created_at.isoformat(),
                graph_response=msg.graph_response,
                metadata=msg.metadata_ or {}
            )

            # 更新分支关系
            if msg.parent_id:
                if msg.parent_id not in cache.branches:
                    cache.branches[msg.parent_id] = []
                cache.branches[msg.parent_id].append(msg.id)

        self._cache[conversation_id] = cache

    async def ensure_conversation_exists(self, conversation_id: str) -> None:
        """确保对话存在（异步版本）"""
        if conversation_id not in self._cache:
            # 尝试从数据库加载
            if self.repository:
                await self._load_conversation_from_db(conversation_id)

            # 如果仍不存在，创建新的
            if conversation_id not in self._cache:
                await self.start_conversation_async(conversation_id)

    # ========================================
    # 消息操作
    # ========================================

    async def add_message_async(
        self,
        conv_id: str,
        message_id: str,
        content: str,
        thread_id: str,
        parent_id: Optional[str] = None
    ) -> Dict:
        """
        添加消息到对话（异步版本，支持持久化）

        Args:
            conv_id: 对话ID
            message_id: 消息ID
            content: 消息内容
            thread_id: 关联的Graph线程ID
            parent_id: 父消息ID（分支时使用）

        Returns:
            用户消息对象
        """
        # 确保对话存在
        await self.ensure_conversation_exists(conv_id)

        cache = self._cache[conv_id]
        now = datetime.now().isoformat()

        # 创建消息缓存
        msg_cache = MessageCache(
            message_id=message_id,
            parent_id=parent_id,
            content=content,
            thread_id=thread_id,
            timestamp=now
        )

        # 保存到缓存
        cache.messages[message_id] = msg_cache

        # 更新分支关系
        if parent_id:
            if parent_id not in cache.branches:
                cache.branches[parent_id] = []
            cache.branches[parent_id].append(message_id)

            if len(cache.branches[parent_id]) > 1:
                logger.info(f"Created branch from message {parent_id}")

        # 更新活跃分支
        cache.active_branch = message_id
        cache.updated_at = now

        # 持久化到数据库
        if self.repository:
            try:
                await self.repository.add_message(
                    conversation_id=conv_id,
                    message_id=message_id,
                    content=content,
                    thread_id=thread_id,
                    parent_id=parent_id
                )

                # 如果是第一条消息（无 parent），自动生成 title
                if parent_id is None:
                    title = self._generate_title(content)
                    await self.repository.update_title(conv_id, title)
                    logger.debug(f"Auto-generated title for conversation {conv_id}: {title}")

            except Exception as e:
                logger.warning(f"Failed to persist message: {e}")

        return {
            "message_id": message_id,
            "parent_id": parent_id,
            "content": content,
            "thread_id": thread_id,
            "timestamp": now,
            "graph_response": None,
            "metadata": {}
        }

    async def update_response_async(
        self,
        conv_id: str,
        message_id: str,
        response: str
    ) -> None:
        """
        更新消息的Graph响应（异步版本，支持持久化）

        Args:
            conv_id: 对话ID
            message_id: 消息ID
            response: Graph响应内容
        """
        if conv_id in self._cache:
            cache = self._cache[conv_id]
            if message_id in cache.messages:
                cache.messages[message_id].graph_response = response
                cache.updated_at = datetime.now().isoformat()

        # 持久化到数据库
        if self.repository:
            try:
                await self.repository.update_graph_response(message_id, response)
            except Exception as e:
                logger.warning(f"Failed to persist response: {e}")

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
        await self.ensure_conversation_exists(conv_id)

        if conv_id in self._cache:
            return self._cache[conv_id].active_branch or None
        return None

    async def get_conversation_path_async(
        self,
        conv_id: str,
        to_message_id: Optional[str] = None
    ) -> List[Dict]:
        """
        获取对话路径（从根到指定消息）

        Args:
            conv_id: 对话ID
            to_message_id: 目标消息ID（None则使用活跃分支）

        Returns:
            消息路径列表
        """
        repo = self._ensure_repository()
        messages = await repo.get_conversation_path(conv_id, to_message_id)

        return [
            {
                "message_id": msg.id,
                "parent_id": msg.parent_id,
                "content": msg.content,
                "thread_id": msg.thread_id,
                "timestamp": msg.created_at.isoformat(),
                "graph_response": msg.graph_response,
                "metadata": msg.metadata_ or {}
            }
            for msg in messages
        ]

    async def format_conversation_history_async(
        self,
        conv_id: str,
        to_message_id: Optional[str] = None
    ) -> List[Dict]:
        """
        格式化对话历史为消息列表

        Args:
            conv_id: 对话ID
            to_message_id: 目标消息ID（None则使用活跃分支）

        Returns:
            消息列表 [{"role": "user", "content": ...}, {"role": "assistant", ...}, ...]
        """
        repo = self._ensure_repository()
        messages = await repo.format_conversation_history(conv_id, to_message_id)

        logger.debug(f"Formatted {len(messages)} messages from conversation history")
        return messages

    # ========================================
    # 列表操作
    # ========================================

    async def list_conversations_async(
        self,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict]:
        """
        列出所有对话（异步版本）

        Args:
            limit: 限制数量
            offset: 跳过数量

        Returns:
            对话列表
        """
        if self.repository:
            try:
                conversations = await self.repository.list_conversations(
                    limit=limit,
                    offset=offset
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
            except Exception as e:
                logger.warning(f"Failed to list conversations from DB: {e}")

        # 回退到内存数据
        conversations = []
        for conv_id, cache in self._cache.items():
            conversations.append({
                "conversation_id": conv_id,
                "title": None,
                "message_count": len(cache.messages),
                "branch_count": len(cache.branches),
                "created_at": cache.created_at,
                "updated_at": cache.updated_at
            })
        return conversations

    def clear_cache(self, conversation_id: Optional[str] = None) -> None:
        """
        清除缓存

        Args:
            conversation_id: 对话ID（None则清除所有）
        """
        if conversation_id:
            if conversation_id in self._cache:
                del self._cache[conversation_id]
        else:
            self._cache.clear()
