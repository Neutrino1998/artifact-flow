"""
对话和消息 Repository

提供对话和消息的 CRUD 操作，以及树结构查询。
"""

from typing import Optional, List, Dict, Any

from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models import Conversation, Message, ArtifactSession
from repositories.base import BaseRepository, NotFoundError, DuplicateError


class ConversationRepository(BaseRepository[Conversation]):
    """
    对话 Repository
    
    职责：
    - 对话的 CRUD 操作
    - 消息的 CRUD 操作
    - 树结构查询（获取对话路径）
    - ArtifactSession 的自动管理
    """
    
    def __init__(self, session: AsyncSession):
        super().__init__(session, Conversation)
    
    # ========================================
    # 对话操作
    # ========================================
    
    async def create_conversation(
        self,
        conversation_id: str,
        title: Optional[str] = None,
        user_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Conversation:
        """
        创建新对话（同时创建关联的 ArtifactSession）
        
        Args:
            conversation_id: 对话ID
            title: 对话标题
            user_id: 用户ID（预留）
            metadata: 扩展元数据
            
        Returns:
            创建的对话对象
            
        Raises:
            DuplicateError: 对话ID已存在
        """
        # 检查是否已存在
        existing = await self.get_by_id(conversation_id)
        if existing:
            raise DuplicateError("Conversation", conversation_id)
        
        # 创建对话
        conversation = Conversation(
            id=conversation_id,
            title=title,
            user_id=user_id,
            metadata_=metadata or {}
        )
        
        # 同时创建关联的 ArtifactSession
        artifact_session = ArtifactSession(id=conversation_id)
        conversation.artifact_session = artifact_session
        
        await self.add(conversation)
        return conversation
    
    async def get_conversation(
        self,
        conversation_id: str,
        *,
        load_messages: bool = False,
        load_artifacts: bool = False
    ) -> Optional[Conversation]:
        """
        获取对话
        
        Args:
            conversation_id: 对话ID
            load_messages: 是否预加载消息
            load_artifacts: 是否预加载 Artifacts
            
        Returns:
            对话对象，不存在则返回 None
        """
        query = select(Conversation).where(Conversation.id == conversation_id)
        
        # 配置预加载
        options = []
        if load_messages:
            options.append(selectinload(Conversation.messages))
        if load_artifacts:
            options.append(
                selectinload(Conversation.artifact_session)
                .selectinload(ArtifactSession.artifacts)
            )
        
        if options:
            query = query.options(*options)
        
        result = await self._session.execute(query)
        return result.scalar_one_or_none()
    
    async def get_conversation_or_raise(
        self,
        conversation_id: str,
        **kwargs
    ) -> Conversation:
        """
        获取对话（不存在则抛出异常）
        
        Args:
            conversation_id: 对话ID
            **kwargs: 传递给 get_conversation 的参数
            
        Returns:
            对话对象
            
        Raises:
            NotFoundError: 对话不存在
        """
        conversation = await self.get_conversation(conversation_id, **kwargs)
        if not conversation:
            raise NotFoundError("Conversation", conversation_id)
        return conversation
    
    async def update_title(
        self,
        conversation_id: str,
        title: str
    ) -> Conversation:
        """
        更新对话标题
        
        Args:
            conversation_id: 对话ID
            title: 新标题
            
        Returns:
            更新后的对话
        """
        conversation = await self.get_conversation_or_raise(conversation_id)
        conversation.title = title
        await self.update(conversation)
        return conversation
    
    async def list_conversations(
        self,
        *,
        user_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        order_by_updated: bool = True,
        load_messages: bool = False
    ) -> List[Conversation]:
        """
        列出对话

        Args:
            user_id: 按用户ID筛选（预留）
            limit: 限制数量
            offset: 跳过数量
            order_by_updated: 是否按更新时间降序排列
            load_messages: 是否预加载消息（用于计算消息数量）

        Returns:
            对话列表
        """
        query = select(Conversation)

        if load_messages:
            query = query.options(selectinload(Conversation.messages))

        if user_id:
            query = query.where(Conversation.user_id == user_id)

        if order_by_updated:
            query = query.order_by(Conversation.updated_at.desc())

        query = query.offset(offset).limit(limit)

        result = await self._session.execute(query)
        return list(result.scalars().all())
    
    async def count_conversations(self, *, user_id: Optional[str] = None) -> int:
        """
        统计对话总数

        Args:
            user_id: 按用户ID筛选（预留）

        Returns:
            对话总数
        """
        query = select(func.count()).select_from(Conversation)
        if user_id:
            query = query.where(Conversation.user_id == user_id)
        result = await self._session.execute(query)
        return result.scalar_one()

    async def delete_conversation(self, conversation_id: str) -> bool:
        """
        删除对话（级联删除消息和 Artifacts）
        
        Args:
            conversation_id: 对话ID
            
        Returns:
            是否成功删除
        """
        return await self.delete_by_id(conversation_id)
    
    # ========================================
    # 消息操作
    # ========================================
    
    async def add_message(
        self,
        conversation_id: str,
        message_id: str,
        user_input: str,
        parent_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Message:
        """
        添加消息到对话

        Args:
            conversation_id: 对话ID
            message_id: 消息ID
            user_input: 消息内容
            parent_id: 父消息ID（用于分支）
            metadata: 扩展元数据

        Returns:
            创建的消息

        Raises:
            NotFoundError: 对话不存在
            DuplicateError: 消息ID已存在
        """
        # 确保对话存在
        conversation = await self.get_conversation_or_raise(conversation_id)

        # 检查消息是否已存在
        existing_msg = await self.get_message(message_id)
        if existing_msg:
            raise DuplicateError("Message", message_id)

        # 创建消息
        message = Message(
            id=message_id,
            conversation_id=conversation_id,
            parent_id=parent_id,
            user_input=user_input,
            metadata_=metadata or {}
        )

        self._session.add(message)

        # 更新对话的活跃分支（onupdate=func.now() 自动处理 updated_at）
        conversation.active_branch = message_id

        await self._session.flush()
        await self._session.commit()
        await self._session.refresh(message)

        return message
    
    async def get_message(self, message_id: str) -> Optional[Message]:
        """
        获取消息
        
        Args:
            message_id: 消息ID
            
        Returns:
            消息对象，不存在则返回 None
        """
        return await self._session.get(Message, message_id)
    
    async def get_message_or_raise(self, message_id: str) -> Message:
        """
        获取消息（不存在则抛出异常）
        
        Args:
            message_id: 消息ID
            
        Returns:
            消息对象
            
        Raises:
            NotFoundError: 消息不存在
        """
        message = await self.get_message(message_id)
        if not message:
            raise NotFoundError("Message", message_id)
        return message
    
    async def update_response(
        self,
        message_id: str,
        response: str
    ) -> Message:
        """
        更新消息的助手响应

        Args:
            message_id: 消息ID
            response: 助手响应内容

        Returns:
            更新后的消息
        """
        message = await self.get_message_or_raise(message_id)
        message.response = response

        # Bulk UPDATE: conversation 自身无属性变化，onupdate 不会触发，
        # 需显式用 DB 时间更新 updated_at。commit 后同 session 已持有的
        # Conversation 实例会被 expire，不要直接访问其属性（见 CLAUDE.md 规范）
        await self._session.execute(
            update(Conversation)
            .where(Conversation.id == message.conversation_id)
            .values(updated_at=func.now()),
        )

        await self._session.flush()
        await self._session.commit()

        return message
    
    async def update_message_metadata(
        self,
        message_id: str,
        metadata: Dict[str, Any],
    ) -> Message:
        """
        更新消息的 metadata（merge 语义）

        Args:
            message_id: 消息ID
            metadata: 要合并的 metadata 字典

        Returns:
            更新后的消息
        """
        message = await self.get_message_or_raise(message_id)
        existing = message.metadata_ or {}
        existing.update(metadata)
        message.metadata_ = existing

        await self._session.flush()
        await self._session.commit()

        return message

    async def get_conversation_messages(
        self,
        conversation_id: str,
        *,
        limit: Optional[int] = None
    ) -> List[Message]:
        """
        获取对话的所有消息
        
        Args:
            conversation_id: 对话ID
            limit: 限制数量
            
        Returns:
            消息列表（按创建时间排序）
        """
        query = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at)
        )
        
        if limit:
            query = query.limit(limit)
        
        result = await self._session.execute(query)
        return list(result.scalars().all())
    
    # ========================================
    # 树结构查询
    # ========================================
    
    async def get_conversation_path(
        self,
        conversation_id: str,
        to_message_id: Optional[str] = None
    ) -> List[Message]:
        """
        获取对话路径（从根到指定消息的路径）
        
        遍历策略：从目标消息向上追溯到根。
        
        Args:
            conversation_id: 对话ID
            to_message_id: 目标消息ID（None 则使用 active_branch）
            
        Returns:
            消息路径列表（从根到目标，按时间顺序）
        """
        # 获取对话
        conversation = await self.get_conversation(conversation_id)
        if not conversation:
            return []
        
        # 确定目标消息
        target_id = to_message_id or conversation.active_branch
        if not target_id:
            return []
        
        # 预加载所有消息（用于快速查找）
        all_messages = await self.get_conversation_messages(conversation_id)
        message_map = {msg.id: msg for msg in all_messages}
        
        # 从目标向上追溯
        path = []
        current_id = target_id
        
        while current_id and current_id in message_map:
            message = message_map[current_id]
            path.insert(0, message)  # 插入到开头
            current_id = message.parent_id
        
        return path
    
