"""
CompactionManager — 跨轮对话摘要压缩

异步后台执行 LLM 摘要生成，逐对处理，带上下文传递。
每对摘要生成后立即写 DB，不等全部完成。
"""

import asyncio
import re
from typing import Dict, Any, Optional

from agents.loader import AgentConfig
from db.database import DatabaseManager
from repositories.conversation_repo import ConversationRepository
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


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

        从最后一次 agent_execution 的 input_tokens 判断是否超过阈值。
        """
        agent_executions = execution_metrics.get("agent_executions", [])
        if not agent_executions:
            return

        last_input_tokens = agent_executions[-1].get("token_usage", {}).get("input_tokens", 0)
        if last_input_tokens < config.COMPACTION_THRESHOLD:
            return

        if conv_id in self._running:
            logger.debug(f"Compaction already running for {conv_id}, skipping")
            return

        logger.info(f"Triggering compaction for {conv_id} (input_tokens={last_input_tokens})")
        done_event = asyncio.Event()
        self._running[conv_id] = done_event
        asyncio.create_task(self._run_compaction(conv_id, message_id, done_event, config))

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

        1. 开独立 DB session
        2. 加载对话路径
        3. 跳过最后 COMPACTION_PRESERVE_PAIRS 对
        4. 逐对处理（跳过已有 summary 的，但读取其 summary 作为上下文）
        """
        from models.llm import astream_with_retry

        compact_agent = self._agents.get("compact_agent")
        if not compact_agent:
            logger.error("compact_agent not found in agents config")
            return

        async with self._db_manager.session() as session:
            repo = ConversationRepository(session)
            path = await repo.get_conversation_path(conv_id, current_message_id)

            if not path:
                logger.debug(f"No messages found for compaction in {conv_id}")
                return

            # 跳过最后 N 对（preserve_pairs）
            preserve = config.COMPACTION_PRESERVE_PAIRS
            if len(path) <= preserve:
                logger.debug(f"Not enough messages to compact in {conv_id}")
                return

            pairs_to_process = path[:-preserve]
            prev_summary: Optional[str] = None

            for msg in pairs_to_process:
                # 如果已有 summary，读取作为上下文但跳过生成
                if msg.user_input_summary and msg.response_summary:
                    prev_summary = (
                        f"Previous pair summary:\n"
                        f"User: {msg.user_input_summary}\n"
                        f"Assistant: {msg.response_summary}"
                    )
                    continue

                # 没有 response 的消息跳过
                if not msg.response:
                    continue

                # 构建 prompt
                prompt_parts = []
                if prev_summary:
                    prompt_parts.append(f"<context>\n{prev_summary}\n</context>")

                prompt_parts.append(
                    f"<user_input>\n{msg.user_input}\n</user_input>\n\n"
                    f"<response>\n{msg.response}\n</response>"
                )

                messages = [
                    {"role": "system", "content": compact_agent.role_prompt},
                    {"role": "user", "content": "\n\n".join(prompt_parts)},
                ]

                # 调用 LLM
                response_text = ""
                try:
                    async for chunk in astream_with_retry(messages, model=compact_agent.model):
                        chunk_type = chunk.get("type")
                        if chunk_type == "content":
                            response_text += chunk["content"]
                        elif chunk_type == "final" and not response_text:
                            response_text = chunk.get("content", "")
                except Exception as e:
                    logger.error(f"Compaction LLM call failed for message {msg.id}: {e}")
                    continue

                # 解析 XML tags
                user_input_summary = self._extract_tag(response_text, "user_input_summary")
                response_summary = self._extract_tag(response_text, "response_summary")

                if not user_input_summary or not response_summary:
                    logger.warning(f"Failed to parse compaction summary for message {msg.id}")
                    continue

                # 写入 DB
                msg.user_input_summary = user_input_summary
                msg.response_summary = response_summary
                await session.flush()
                await session.commit()

                logger.debug(f"Compacted message {msg.id}")

                # 更新上下文
                prev_summary = (
                    f"Previous pair summary:\n"
                    f"User: {user_input_summary}\n"
                    f"Assistant: {response_summary}"
                )

            logger.info(f"Compaction completed for {conv_id}")

    @staticmethod
    def _extract_tag(text: str, tag: str) -> Optional[str]:
        """从文本中提取 XML tag 内容。"""
        pattern = rf"<{tag}>\s*(.*?)\s*</{tag}>"
        match = re.search(pattern, text, re.DOTALL)
        return match.group(1).strip() if match else None
