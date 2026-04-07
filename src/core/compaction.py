"""
CompactionManager — 跨轮对话摘要压缩

异步后台执行 LLM 摘要生成，逐对处理，带上下文传递。
每对独立 DB session：读消息 → 关 session → 调 LLM → 开 session → 写 summary。
LLM 调用期间不持有任何 DB 资源。

分布式锁（F-19）：当注入 RuntimeStore 时，使用 owner-key 原语实现跨实例互斥。
无 RuntimeStore 时回退到进程内 _running dict（单实例行为不变）。
"""

import asyncio
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, Any, List, Optional
from uuid import uuid4

from agents.loader import AgentConfig
from config import config
from db.database import DatabaseManager
from repositories.conversation_repo import ConversationRepository
from utils.logger import get_logger

if TYPE_CHECKING:
    from api.services.runtime_store import RuntimeStore

logger = get_logger("ArtifactFlow")

# Distributed lock constants
_LOCK_TTL = 60          # seconds, renewed every TTL/3
_LOCK_POLL_INTERVAL = 2  # seconds, for wait_if_running polling


@dataclass
class _PairInfo:
    """从 DB 读取后脱离 session 的消息对数据。"""
    message_id: str
    user_input: str
    response: str
    user_input_summary: Optional[str] = None
    response_summary: Optional[str] = None


class CompactionManager:
    """
    管理跨轮对话 compaction。

    - maybe_trigger: 自动触发（基于 token 阈值）
    - trigger: 手动触发
    - wait_if_running: 等待正在运行的 compaction 完成
    """

    def __init__(
        self,
        db_manager: DatabaseManager,
        agents: Dict[str, AgentConfig],
        runtime_store: Optional["RuntimeStore"] = None,
    ):
        self._db_manager = db_manager
        self._agents = agents
        self._store = runtime_store
        self._running: Dict[str, asyncio.Event] = {}  # conv_id → done_event (local fallback)

    def _lock_key(self, conv_id: str) -> str:
        return f"compact:{conv_id}"

    async def maybe_trigger(
        self,
        conv_id: str,
        message_id: str,
        execution_metrics: Dict[str, Any],
    ) -> None:
        """
        根据 execution_metrics 判断是否触发 compaction。

        从 last_context_chars（构建 context 时的 len() 总和）判断是否超过阈值。
        """
        last_context_chars = execution_metrics.get("last_context_chars", 0)
        if last_context_chars < config.COMPACTION_THRESHOLD:
            return

        if await self.is_running(conv_id):
            logger.debug(f"Compaction already running for {conv_id}, skipping")
            return

        logger.info(f"Triggering compaction for {conv_id} (context_chars={last_context_chars})")
        done_event = asyncio.Event()
        self._running[conv_id] = done_event
        asyncio.create_task(self._run_compaction(conv_id, message_id, done_event))

    async def is_running(self, conv_id: str) -> bool:
        """检查 conv_id 是否有正在运行的 compaction。"""
        # Local check first (same-process compaction)
        if conv_id in self._running:
            return True
        # Cross-instance check via distributed lock
        if self._store:
            owner = await self._store.get_owner(self._lock_key(conv_id))
            return owner is not None
        return False

    async def wait_if_running(self, conv_id: str) -> bool:
        """
        如果 conv_id 有正在运行的 compaction，等待其完成。

        Returns:
            True 如果等待了（compaction 完成），False 如果没有正在运行的 compaction。
        """
        # Local event available — wait on it directly
        event = self._running.get(conv_id)
        if event is not None:
            await event.wait()
            return True

        # Cross-instance: poll lock state
        if self._store:
            owner = await self._store.get_owner(self._lock_key(conv_id))
            if owner is None:
                return False
            # Poll until lock is released
            while True:
                await asyncio.sleep(_LOCK_POLL_INTERVAL)
                owner = await self._store.get_owner(self._lock_key(conv_id))
                if owner is None:
                    return True

        return False

    async def trigger(self, conv_id: str) -> bool:
        """
        手动触发 compaction。

        Returns:
            True 如果成功启动，False 如果已在运行。
        """
        if await self.is_running(conv_id):
            return False

        done_event = asyncio.Event()
        self._running[conv_id] = done_event
        asyncio.create_task(self._run_compaction(conv_id, None, done_event))
        return True

    async def _run_compaction(
        self,
        conv_id: str,
        current_message_id: Optional[str],
        done_event: asyncio.Event,
    ) -> None:
        """运行 compaction，带超时、分布式锁和清理。"""
        owner_id = uuid4().hex
        lock_key = self._lock_key(conv_id)
        heartbeat_task: Optional[asyncio.Task] = None

        try:
            # Acquire distributed lock (if store available)
            if self._store:
                acquired, _ = await self._store.acquire(lock_key, _LOCK_TTL, owner=owner_id)
                if not acquired:
                    logger.debug(f"Compaction lock already held for {conv_id}, skipping")
                    return

                # Start heartbeat renewal
                heartbeat_task = asyncio.create_task(
                    self._renew_loop(conv_id, lock_key, owner_id)
                )

            async with asyncio.timeout(config.COMPACTION_TIMEOUT):
                await self._compact(conv_id, current_message_id)
        except TimeoutError:
            logger.error(f"Compaction timed out for {conv_id} after {config.COMPACTION_TIMEOUT}s")
        except Exception as e:
            logger.exception(f"Compaction failed for {conv_id}: {e}")
        finally:
            if heartbeat_task:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass
            if self._store:
                await self._store.release(lock_key, owner_id)
            done_event.set()
            self._running.pop(conv_id, None)

    async def _renew_loop(self, conv_id: str, lock_key: str, owner_id: str) -> None:
        """Heartbeat loop: renew lock every TTL/3. Cancel compaction if lock is lost."""
        interval = _LOCK_TTL / 3
        while True:
            await asyncio.sleep(interval)
            try:
                renewed = await self._store.renew(lock_key, owner_id, _LOCK_TTL)
                if not renewed:
                    logger.warning(f"Compaction lock lost for {conv_id}, lock may have expired")
                    return
            except Exception as e:
                logger.warning(f"Compaction lock renewal failed for {conv_id}: {e}")
                return

    async def _compact(
        self,
        conv_id: str,
        current_message_id: Optional[str],
    ) -> None:
        """
        核心 compaction 逻辑。

        数据库交互模式：短事务。
        1. 短 session: 读取对话路径，提取为纯数据 (_PairInfo)，关闭 session
        2. 逐对: 调 LLM（无 DB 连接）→ 短 session: 写 summary，关闭 session
        """
        from models.llm import astream_with_retry, format_messages_for_debug
        from core.context_manager import ContextManager

        compact_agent = self._agents.get("compact_agent")
        if not compact_agent:
            logger.error("compact_agent not found in agents config")
            return

        # ── Phase 1: 短事务读取，脱离 session ──
        pairs = await self._load_pairs(conv_id, current_message_id)
        if not pairs:
            return

        # ── Phase 2: 逐对处理 ──
        prior_summaries: List[str] = []

        for pair in pairs:
            # 已有 summary → 收集为上下文，跳过生成
            if pair.user_input_summary and pair.response_summary:
                prior_summaries.append(
                    f"User: {pair.user_input_summary}\n"
                    f"Assistant: {pair.response_summary}"
                )
                continue

            # 构建 prompt — 先算固定部分，剩余预算分给 prior_summaries
            pair_content = (
                f"<user_input>\n{pair.user_input}\n</user_input>\n\n"
                f"<response>\n{pair.response}\n</response>"
            )
            fixed_chars = len(compact_agent.role_prompt) + len(pair_content)
            budget = max(config.CONTEXT_MAX_CHARS - fixed_chars, 0)

            summary_messages = [{"role": "user", "content": s} for s in prior_summaries]
            summary_messages = ContextManager.truncate_messages(
                summary_messages, budget, preserve_recent=2
            )
            # 提取截断后的 summaries（跳过 truncation marker）
            recent_summaries = [
                m["content"] for m in summary_messages
                if "truncated]" not in m["content"]
            ]

            prompt_parts = []
            if recent_summaries:
                context_block = "\n\n".join(
                    f"[Pair {i+1}]\n{s}" for i, s in enumerate(recent_summaries)
                )
                prompt_parts.append(f"<context>\n{context_block}\n</context>")

            prompt_parts.append(pair_content)

            prompt_content = "\n\n".join(prompt_parts)
            messages = [
                {"role": "system", "content": compact_agent.role_prompt},
                {"role": "user", "content": prompt_content},
            ]

            logger.debug(f"[compact_agent] Compacting {pair.message_id}:\n{format_messages_for_debug(messages)}")

            # 调用 LLM（此时无 DB 连接）
            response_text = ""
            try:
                async for chunk in astream_with_retry(messages, model=compact_agent.model):
                    chunk_type = chunk.get("type")
                    if chunk_type == "content":
                        response_text += chunk["content"]
                    elif chunk_type == "final" and not response_text:
                        response_text = chunk.get("content", "")
            except Exception as e:
                logger.error(f"Compaction LLM call failed for message {pair.message_id}: {e}")
                continue

            logger.debug(f"[compact_agent] Raw response for {pair.message_id}:\n{response_text}")

            # 解析 XML tags
            user_input_summary = self._extract_tag(response_text, "user_input_summary")
            response_summary = self._extract_tag(response_text, "response_summary")

            if not user_input_summary or not response_summary:
                logger.warning(f"Failed to parse compaction summary for message {pair.message_id}")
                continue

            # 短事务写入
            await self._write_summary(pair.message_id, user_input_summary, response_summary)

            logger.debug(f"Compacted message {pair.message_id}")

            # 累积上下文
            prior_summaries.append(
                f"User: {user_input_summary}\n"
                f"Assistant: {response_summary}"
            )

        logger.info(f"Compaction completed for {conv_id}")

    async def _load_pairs(
        self,
        conv_id: str,
        current_message_id: Optional[str],
    ) -> List[_PairInfo]:
        """短事务：读取对话路径，提取为脱离 session 的纯数据。"""
        async with self._db_manager.session() as session:
            repo = ConversationRepository(session)
            path = await repo.get_conversation_path(conv_id, current_message_id)

            if not path:
                logger.debug(f"No messages found for compaction in {conv_id}")
                return []

            preserve = config.COMPACTION_PRESERVE_PAIRS
            if len(path) <= preserve:
                logger.debug(f"Not enough messages to compact in {conv_id}")
                return []

            # 提取为纯数据，脱离 ORM session
            pairs = []
            for msg in path[:-preserve]:
                if not msg.response:
                    continue
                pairs.append(_PairInfo(
                    message_id=msg.id,
                    user_input=msg.user_input,
                    response=msg.response,
                    user_input_summary=msg.user_input_summary,
                    response_summary=msg.response_summary,
                ))
            return pairs
        # session 在此关闭

    async def _write_summary(
        self,
        message_id: str,
        user_input_summary: str,
        response_summary: str,
    ) -> None:
        """短事务：写入单条消息的 summary。"""
        async with self._db_manager.session() as session:
            repo = ConversationRepository(session)
            msg = await repo.get_message(message_id)
            if msg:
                msg.user_input_summary = user_input_summary
                msg.response_summary = response_summary
                await session.flush()
                await session.commit()
        # session 在此关闭

    @staticmethod
    def _extract_tag(text: str, tag: str) -> Optional[str]:
        """从文本中提取 XML tag 内容。"""
        pattern = rf"<{tag}>\s*(.*?)\s*</{tag}>"
        match = re.search(pattern, text, re.DOTALL)
        return match.group(1).strip() if match else None
