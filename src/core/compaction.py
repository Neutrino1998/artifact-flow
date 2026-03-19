"""
CompactionManager — 跨轮对话摘要压缩

异步后台执行 LLM 摘要生成，逐对处理，带上下文传递。
每对独立 DB session：读消息 → 关 session → 调 LLM → 开 session → 写 summary。
LLM 调用期间不持有任何 DB 资源。
"""

import asyncio
import re
from dataclasses import dataclass
from typing import Dict, Any, List, Optional

from agents.loader import AgentConfig
from db.database import DatabaseManager
from repositories.conversation_repo import ConversationRepository
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


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
    ):
        self._db_manager = db_manager
        self._agents = agents
        self._running: Dict[str, asyncio.Event] = {}  # conv_id → done_event

    async def maybe_trigger(
        self,
        conv_id: str,
        message_id: str,
        execution_metrics: Dict[str, Any],
        config: Any,
    ) -> None:
        """
        根据 execution_metrics 判断是否触发 compaction。

        从 last_token_usage 的 input_tokens 判断是否超过阈值。
        """
        last_usage = execution_metrics.get("last_token_usage")
        if not last_usage:
            return

        last_input_tokens = last_usage.get("input_tokens", 0)
        if last_input_tokens < config.COMPACTION_THRESHOLD:
            return

        if conv_id in self._running:
            logger.debug(f"Compaction already running for {conv_id}, skipping")
            return

        logger.info(f"Triggering compaction for {conv_id} (input_tokens={last_input_tokens})")
        done_event = asyncio.Event()
        self._running[conv_id] = done_event
        asyncio.create_task(self._run_compaction(conv_id, message_id, done_event, config))

    def is_running(self, conv_id: str) -> bool:
        """检查 conv_id 是否有正在运行的 compaction。"""
        return conv_id in self._running

    async def wait_if_running(self, conv_id: str) -> bool:
        """
        如果 conv_id 有正在运行的 compaction，等待其完成。

        Returns:
            True 如果等待了（compaction 完成），False 如果没有正在运行的 compaction。
        """
        event = self._running.get(conv_id)
        if event is None:
            return False

        await event.wait()
        return True

    async def trigger(self, conv_id: str, config: Any) -> bool:
        """
        手动触发 compaction。

        Returns:
            True 如果成功启动，False 如果已在运行。
        """
        if conv_id in self._running:
            return False

        done_event = asyncio.Event()
        self._running[conv_id] = done_event
        asyncio.create_task(self._run_compaction(conv_id, None, done_event, config))
        return True

    async def _run_compaction(
        self,
        conv_id: str,
        current_message_id: Optional[str],
        done_event: asyncio.Event,
        config: Any,
    ) -> None:
        """运行 compaction，带超时和清理。"""
        try:
            async with asyncio.timeout(config.COMPACTION_TIMEOUT):
                await self._compact(conv_id, current_message_id, config)
        except TimeoutError:
            logger.error(f"Compaction timed out for {conv_id} after {config.COMPACTION_TIMEOUT}s")
        except Exception as e:
            logger.exception(f"Compaction failed for {conv_id}: {e}")
        finally:
            done_event.set()
            self._running.pop(conv_id, None)

    async def _compact(
        self,
        conv_id: str,
        current_message_id: Optional[str],
        config: Any,
    ) -> None:
        """
        核心 compaction 逻辑。

        数据库交互模式：短事务。
        1. 短 session: 读取对话路径，提取为纯数据 (_PairInfo)，关闭 session
        2. 逐对: 调 LLM（无 DB 连接）→ 短 session: 写 summary，关闭 session
        """
        from models.llm import astream_with_retry, format_messages_for_debug

        compact_agent = self._agents.get("compact_agent")
        if not compact_agent:
            logger.error("compact_agent not found in agents config")
            return

        # ── Phase 1: 短事务读取，脱离 session ──
        pairs = await self._load_pairs(conv_id, current_message_id, config)
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

            # 构建 prompt — 从最新往前累积 summary，超出字符预算则丢弃最早的
            budget = getattr(config, "CONTEXT_MAX_CHARS", 80000)
            recent_summaries = []
            total_len = 0
            for s in reversed(prior_summaries):
                if total_len + len(s) > budget:
                    break
                recent_summaries.append(s)
                total_len += len(s)
            recent_summaries.reverse()

            prompt_parts = []
            if recent_summaries:
                context_block = "\n\n".join(
                    f"[Pair {i+1}]\n{s}" for i, s in enumerate(recent_summaries)
                )
                prompt_parts.append(f"<context>\n{context_block}\n</context>")

            prompt_parts.append(
                f"<user_input>\n{pair.user_input}\n</user_input>\n\n"
                f"<response>\n{pair.response}\n</response>"
            )

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
        config: Any,
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
