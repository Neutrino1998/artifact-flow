"""
对话管理器

职责：
1. 管理对话和消息的生命周期
2. 格式化对话历史
3. 通过 Repository 进行持久化
"""

from typing import Dict, List, Optional, Any

from repositories.conversation_repo import ConversationRepository
from repositories.message_event_repo import MessageEventRepository
from repositories.message_feedback_repo import MessageFeedbackRepository
from repositories.base import NotFoundError, DuplicateError
from db.models import Conversation, Message, MessageEvent, MessageFeedback
from utils.logger import get_logger
from utils.time import utc_now

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
            await manager.create(...)
    """

    def __init__(self, repository: Optional[ConversationRepository] = None):
        """
        初始化 ConversationManager

        Args:
            repository: ConversationRepository 实例（可以为 None）
        """
        self.repository = repository
        # DEBUG 而非 INFO:构造无上下文、每请求处理器各 new 一个(单轮可达 ~8 次),
        # INFO 下纯噪音会淹没真实里程碑。真业务事件(create 等)才打 INFO。
        logger.debug("ConversationManager initialized")

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
        if not first_line:
            return "Untitled"
        if len(first_line) > TITLE_MAX_LENGTH:
            return first_line[:TITLE_MAX_LENGTH] + "..."
        return first_line

    # ========================================
    # 对话操作
    # ========================================

    async def create(
        self,
        conversation_id: str,
        user_id: Optional[str] = None,
    ) -> str:
        """显式创建一个由调用方分配 ID 的 Conversation。

        ID 必须在任何 ``with_retry`` 边界外生成并在重试间保持稳定。同一操作
        首次已 commit、确认响应瞬断后的 Duplicate 视为幂等成功；客户端提供的
        已有 Conversation ID 不得进入本方法。

        Args:
            conversation_id: 调用方预先分配的稳定对话ID
            user_id: 用户ID（认证隔离）

        Returns:
            对话ID
        """
        repo = self._ensure_repository()
        try:
            await repo.create_conversation(
                conversation_id=conversation_id,
                title=None,
                user_id=user_id,
            )
        except DuplicateError:
            logger.debug(
                f"Conversation {conversation_id} already exists (idempotent retry)"
            )

        logger.info(f"Created conversation: {conversation_id}")
        return conversation_id

    async def require_owned(
        self,
        conversation_id: str,
        user_id: Optional[str] = None,
    ) -> None:
        """要求 Conversation 已存在且归属指定用户，否则按 404 语义失败。

        本方法只做校验，不返回 ORM snapshot；这样通过 ``with_retry`` 的 fresh
        session 调用时不会让 ORM 实例逃出其加载 session。不存在和 owner 不匹配
        使用同一个 ``NotFoundError``，避免泄露资源是否存在。

        Args:
            conversation_id: 对话ID
            user_id: 预期 owner；None 保留非 Web/manual 的无 owner Conversation 语义
        """
        repo = self._ensure_repository()
        existing = await repo.get_conversation(conversation_id)
        if not existing or existing.user_id != user_id:
            raise NotFoundError("Conversation", conversation_id)

    # ========================================
    # 消息操作
    # ========================================

    async def append_message(
        self,
        conv_id: str,
        message_id: str,
        user_input: str,
        parent_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict:
        """向一个已存在的 Conversation 追加消息，绝不创建父 Conversation。

        Args:
            conv_id: 对话ID
            message_id: 消息ID
            user_input: 消息内容
            parent_id: 父消息ID（分支时使用）
            metadata: 与用户输入同时确定的 display-only 快照

        Returns:
            消息对象字典
        """
        now = utc_now().isoformat()

        repo = self._ensure_repository()
        try:
            await repo.add_message(
                conversation_id=conv_id,
                message_id=message_id,
                user_input=user_input,
                parent_id=parent_id,
                metadata=metadata,
            )
        except DuplicateError:
            # 幂等(with_retry 契约):本方法被 _with_db_retry 包裹,瞬断会从头重跑;
            # 若上次尝试已 commit 了这条 message(message_id 是稳定幂等键),重跑会撞重 —
            # 当作上次已成功,不 raise(否则非瞬断异常逃出 with_retry → 整轮崩,即便消息
            # 已落库)。与兄弟 create 同范式。title 重设天然幂等,照常走。
            logger.debug(f"Message {message_id} already exists (idempotent retry)")

        # 如果是第一条消息（无 parent），自动生成 title。Duplicate 分支也必须继续到
        # 这里：Message/active_branch 可能已 commit，而上次尝试在 title 写前瞬断。
        if parent_id is None:
            title = self._generate_title(user_input)
            await repo.update_title(conv_id, title)
            logger.debug(f"Auto-generated title for conversation {conv_id}: {title}")

        return {
            "message_id": message_id,
            "parent_id": parent_id,
            "user_input": user_input,
            "timestamp": now,
            "response": None,
            "metadata": metadata or {},
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
        from repositories.artifact_repo import ArtifactRepository

        repo = self._ensure_repository()
        conversations = await repo.list_conversations(
            limit=limit,
            offset=offset,
            user_id=user_id,
            title_query=title_query,
            load_messages=True
        )
        # 逐项"附件占用"：一次 GROUP BY 聚合本页 session 的 blob 字节（同 session 复用
        # repo.session；只读 size_bytes，index-only）。缺失项 = 无 blob → 兜 0。
        art_repo = ArtifactRepository(repo.session)
        sizes = await art_repo.get_blob_bytes_by_sessions([conv.id for conv in conversations])
        return [
            {
                "conversation_id": conv.id,
                "title": conv.title,
                "message_count": len(conv.messages) if conv.messages else 0,
                "created_at": conv.created_at.isoformat(),
                "updated_at": conv.updated_at.isoformat(),
                "upload_bytes": sizes.get(conv.id, 0),
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

    async def get_user_upload_bytes(self, user_id: str) -> int:
        """该用户已占用的存储总字节 = artifact blob(跨其全部会话)+ 私有 skill bundle。

        上传/导入准入配额检查 + 存储用量进度条共用此口径（单一数据源：DB 现算，
        不存计数器；skill 与 artifact 共用一个池，见 config.ARTIFACT_USER_QUOTA_BYTES）。
        临时实例化 Repository 复用本 manager 的 session，维持三层边界。

        已接受的软度:artifact 写路径深处的 chokepoint(create_from_upload)保持
        blob-only 口径 —— 配额本就「挡量级非字节级」,不在 turn 内写路径加跨 repo 读。
        """
        from repositories.artifact_repo import ArtifactRepository
        from repositories.skill_repo import SkillRepository

        repo = self._ensure_repository()
        blob_bytes = await ArtifactRepository(repo.session).get_user_blob_bytes(user_id)
        bundle_bytes = await SkillRepository(repo.session).get_user_bundle_bytes(user_id)
        return blob_bytes + bundle_bytes

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
        try:
            await self.require_owned(conversation_id, user_id)
        except NotFoundError:
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

    async def set_message_feedback(
        self,
        *,
        conversation_id: str,
        message_id: str,
        user_id: str,
        rating: str,
        tags: list[str],
        detail: Optional[str],
    ) -> Optional[MessageFeedback]:
        """Create or replace feedback after checking conversation ownership."""
        repo = self._ensure_repository()
        conversation = await repo.get_conversation(conversation_id)
        if not conversation or conversation.user_id != user_id:
            return None
        message = await repo.get_message(message_id)
        if not message or message.conversation_id != conversation_id:
            return None
        return await MessageFeedbackRepository(repo.session).upsert(
            message_id, rating=rating, tags=tags, detail=detail
        )

    async def delete_message_feedback(
        self, *, conversation_id: str, message_id: str, user_id: str
    ) -> Optional[bool]:
        """Delete feedback idempotently; None means conversation/message is hidden."""
        repo = self._ensure_repository()
        conversation = await repo.get_conversation(conversation_id)
        if not conversation or conversation.user_id != user_id:
            return None
        message = await repo.get_message(message_id)
        if not message or message.conversation_id != conversation_id:
            return None
        return await MessageFeedbackRepository(repo.session).delete(message_id)

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

    async def list_admin_feedback(
        self,
        *,
        rating: Optional[str],
        query: Optional[str],
        limit: int,
        offset: int,
    ) -> tuple[
        list[tuple[MessageFeedback, Message, Conversation]],
        int,
        Dict[str, str],
    ]:
        """Admin read-only feedback records plus owner display-name projection."""
        from sqlalchemy import select
        from db.models import User

        repo = self._ensure_repository()
        feedback_repo = MessageFeedbackRepository(repo.session)
        rows = await feedback_repo.list_admin(
            rating=rating, query=query, limit=limit, offset=offset
        )
        total = await feedback_repo.count_admin(rating=rating, query=query)

        user_names: Dict[str, str] = {}
        user_ids = {conv.user_id for _, _, conv in rows if conv.user_id}
        if user_ids:
            result = await repo.session.execute(
                select(User.id, User.display_name, User.username).where(
                    User.id.in_(user_ids)
                )
            )
            for uid, display_name, username in result.all():
                user_names[uid] = display_name or username
        return rows, total, user_names

    async def get_admin_conversation_events(
        self,
        conv_id: str,
    ) -> Optional[tuple[Conversation, List[Message], List[MessageEvent], Optional[str]]]:
        """Admin 视图：取对话 + 所有消息 + 跨消息事件流（按 id 升序）。

        Returns:
            (conversation, messages, events, owner_display_name) 元组；
            对话不存在时返回 None。owner_display_name 在 session 内解析，
            避免 router 触碰 lazy 的 owner 关系（MissingGreenlet）。
        """
        from sqlalchemy import select
        from db.models import User

        repo = self._ensure_repository()
        conv = await repo.get_conversation(conv_id)
        if not conv:
            return None

        messages = await repo.get_conversation_messages(conv_id)
        event_repo = MessageEventRepository(repo.session)
        events = await event_repo.get_by_conversation(conv_id)

        owner_display_name: Optional[str] = None
        if conv.user_id:
            stmt = select(User.display_name, User.username).where(User.id == conv.user_id)
            row = (await repo.session.execute(stmt)).first()
            if row:
                owner_display_name = row[0] or row[1]

        return conv, messages, events, owner_display_name

    async def reconstruct_prompt(
        self,
        conv_id: str,
        message_id: str,
        agent_start_event_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Admin 取证：重建某一发 LLM 调用的 messages 语义输入。

        messages 忠实性策略 = 持久化后纯重放，不重新生成动态内容：
          - 静态 system_prompt + 动态 reminder 取自锚 agent_start 事件的持久化原值；
          - model 同样取自锚事件，避免当前配置覆盖历史调用；
          - 历史 messages 用 build_event_history 在「锚之前的 path 事件」上确定性重放；
          - 两者经 ContextManager.assemble（与 live build 同一拼接叶子）合成。
        完整 native tools schema 不持久化，因此这里不声称还原完整 provider 请求。
        分支安全：按 message_id 走 load_event_history_async（分支正确的 path），锚事件
        必须落在该 path 上，否则（选错分支 / 不存在）返回 None → 404。

        「100%」是**版本内**的 100%：历史重放仍跑当前版本的 build_event_history，且
        reminder 仅对本次变更上线后产生的事件存在（旧事件 reminder=None，只重建
        system_prompt + 历史，has_reminder=False 标注）。识图块因 vision_blocks_by_call 是
        per-turn 内存缓存、已不可得，统一降级为占位文本（与跨轮 reload 同口径）。

        Returns:
            重建结果 dict；conv / message / 锚事件任一在该 path 上找不到时返回 None。
        """
        from core.event_history import build_event_history
        from core.context_manager import ContextManager
        from core.events import StreamEventType

        events = await self.load_event_history_async(conv_id, to_message_id=message_id)
        if not events:
            return None

        anchor_idx = next(
            (i for i, e in enumerate(events)
             if e.event_type == StreamEventType.AGENT_START.value
             and e.event_id == agent_start_event_id),
            None,
        )
        if anchor_idx is None:
            return None

        anchor = events[anchor_idx]
        data = anchor.data or {}
        system_prompt = data.get("system_prompt") or ""
        reminder = data.get("reminder")  # 旧事件无此字段 → None
        model = data.get("model")
        agent_name = anchor.agent_name

        history = build_event_history(
            events[:anchor_idx],
            agent_name,
            # Old agent_start events predate the field and were generated while
            # reasoning replay was unconditional.
            replay_reasoning=data.get("replay_reasoning", True),
        )
        if not history:
            # 锚前无本 agent 历史 = 数据异常（正常下至少有 user_input / subagent_instruction）。
            # 取证场景、admin 会上报，记一笔便于排查。
            logger.warning(
                f"reconstruct_prompt: empty history before anchor "
                f"(conv={conv_id} msg={message_id} event={agent_start_event_id})"
            )
            return None

        messages = ContextManager.assemble(system_prompt, history, reminder)
        return {
            "conversation_id": conv_id,
            "message_id": message_id,
            "agent_start_event_id": agent_start_event_id,
            "agent_name": agent_name,
            "model": model,
            # None = legacy agent_start 没有采集；[] = 当次确实没有向模型暴露工具。
            "exposed_tool_names": data.get("exposed_tool_names"),
            "has_reminder": reminder is not None,
            "messages": messages,
        }

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

    async def exists_async(self, conversation_id: str) -> bool:
        """
        判断对话是否存在（薄包装 ConversationRepository.exists）

        controller post-processing 用来判定 conv 是否被中途 DELETE。
        """
        repo = self._ensure_repository()
        return await repo.exists(conversation_id)

    async def count_user_conversations(self, user_id: str) -> int:
        """
        统计指定用户拥有的对话数。

        用于硬删用户前的 impact 提示（"将级联删除该用户的 N 条会话"）。
        薄包装 ConversationRepository.count_by_user，维持 router → manager → repo
        的三层调用边界。
        """
        repo = self._ensure_repository()
        return await repo.count_by_user(user_id)

    async def count_users_conversations(self, user_ids: list[str]) -> int:
        """
        一次性统计一批用户共拥有的对话数。

        用于 PR5a 批量硬删用户前的 impact 提示。一次 IN 查询，避免 N+1。
        """
        repo = self._ensure_repository()
        return await repo.count_by_users(user_ids)
