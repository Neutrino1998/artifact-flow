"""
执行控制器 — Pi-style while loop

职责：
1. stream_execute() — 创建 state → 启动 execute_loop → 事件推 StreamTransport
2. resume() — 唤醒暂停的 coroutine
3. 对话管理复用 ConversationManager
"""

import asyncio
from typing import Awaitable, Callable, Dict, List, Optional, Any, AsyncGenerator
from uuid import uuid4

from sqlalchemy.exc import IntegrityError

from config import config
from core.engine import EngineHooks, create_initial_state, execute_loop, finalize_metrics
from core.events import StreamEventType, ExecutionEvent
from core.conversation_manager import ConversationManager
from core.post_processing import (
    PostProcessState,
    choose_response_for_terminal,
    decide_terminal,
    ensure_terminal,
    make_external_cancelled_event,
)
from tools.base import BaseTool
from tools.builtin.artifact_ops import ArtifactManager
from utils.logger import get_logger
from utils.time import utc_now

logger = get_logger("ArtifactFlow")

# Sentinel to distinguish "not provided" from "explicitly None"
_UNSET = object()

# Sentinel to signal end of event queue
_SENTINEL = object()


class ExecutionController:
    """Pi-style 执行控制器，驱动 agent/tool 循环并管理 interrupt resume。"""

    def __init__(
        self,
        agents: Dict[str, Any],           # {name: AgentConfig}
        tools: Dict[str, BaseTool],        # {name: BaseTool}
        hooks: EngineHooks,
        artifact_manager: Optional[ArtifactManager] = None,
        conversation_manager: Optional[ConversationManager] = None,
        message_event_repo: Optional[Any] = None,  # MessageEventRepository
        on_engine_exit: Optional[Callable[[str, str], Awaitable[None]]] = None,
        db_manager: Optional[Any] = None,
    ):
        self.agents = agents
        self.tools = tools
        self.hooks = hooks
        self.artifact_manager = artifact_manager
        self.conversation_manager = conversation_manager or ConversationManager()
        self.message_event_repo = message_event_repo
        self._on_engine_exit = on_engine_exit
        self._db_manager = db_manager
        logger.info("ExecutionController initialized")

    async def _with_db_retry(self, fn):
        """
        DB 操作重试适配器。

        fn: async (conv_mgr, event_repo, art_mgr) -> result
        有 db_manager 时委托 db_manager.with_retry（fresh session + 瞬断重试）。
        无 db_manager 时回退到 bound 实例（不重试）。
        """
        if not self._db_manager:
            return await fn(self.conversation_manager, self.message_event_repo, self.artifact_manager)

        from repositories.conversation_repo import ConversationRepository
        from repositories.message_event_repo import MessageEventRepository
        from repositories.artifact_repo import ArtifactRepository

        async def _with_session(session):
            conv_mgr = ConversationManager(ConversationRepository(session))
            event_repo = MessageEventRepository(session)
            art_mgr = ArtifactManager(ArtifactRepository(session))
            return await fn(conv_mgr, event_repo, art_mgr)

        return await self._db_manager.with_retry(_with_session)

    async def stream_execute(
        self,
        user_input: Optional[str] = None,
        conversation_id: Optional[str] = None,
        parent_message_id: Any = _UNSET,
        message_id: Optional[str] = None,
        uploaded_artifacts: Optional[List[Dict[str, str]]] = None,
        force_compact: bool = False,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        流式执行接口（新消息）

        Args:
            user_input: 用户消息内容
            conversation_id: 对话ID
            parent_message_id: 父消息ID
            message_id: 消息ID
            uploaded_artifacts: 本轮随消息上传的 artifact [{"id", "filename"}, ...]，
                                用于在 USER_INPUT 事件正文（仅 LLM 可见，不入 display）
                                追加归属说明，让 agent 知道哪些 artifact 是本轮新传的

        Yields:
            流式事件字典
        """
        if user_input is None:
            raise ValueError("'user_input' is required for new message execution")
        # 不变量下沉到核心入口：空文本且无附件 = 本轮无可处理输入，会让 USER_INPUT 正文
        # 为空 → 被 EventHistory 过滤 → 空 history → ContextManager.build 在 [-1] 崩。
        # 在此（任何 yield / DB 写之前）拒掉，不依赖调用方校验；router 另留 422 作为 HTTP
        # 快速边界。带附件时 execute_loop 会给 USER_INPUT 拼归属串（非空），故仅无附件时要求非空。
        # force_compact 同理：execute_loop 会注入压缩指令补足正文，纯压缩轮次（无文本无附件）放行。
        if not user_input.strip() and not uploaded_artifacts and not force_compact:
            raise ValueError(
                "'user_input' must be non-empty when no artifacts are attached"
            )

        # ========== 准备工作 ==========
        if not conversation_id:
            conversation_id = await self.conversation_manager.start_conversation_async()
        else:
            await self.conversation_manager.ensure_conversation_exists(conversation_id)

        # Auto-detect parent
        if parent_message_id is _UNSET:
            parent_message_id = await self.conversation_manager.get_active_branch(conversation_id)
            if parent_message_id:
                logger.debug(f"Auto-set parent_message_id to active_branch: {parent_message_id}")

        resolved_parent: Optional[str] = parent_message_id if isinstance(parent_message_id, str) else None

        # 生成 ID
        message_id = message_id or f"msg-{uuid4().hex}"

        # 先发送元数据事件 — as early as possible so frontend knows we're alive
        # Only needs message_id, not a persisted row
        yield {
            "type": StreamEventType.METADATA.value,
            "timestamp": utc_now().isoformat(),
            "data": {
                "conversation_id": conversation_id,
                "message_id": message_id,
            }
        }

        # Path events — load conversation path 上已持久化的全部事件作为 state["events"]
        # 的历史段（is_historical=True）。Compaction 在引擎内部同步触发，不再需要
        # 异步等待或分布式锁。
        if parent_message_id is not _UNSET and resolved_parent is None:
            path_events = []
        else:
            path_events = await self._with_db_retry(
                lambda cm, er, am: cm.load_event_history_async(
                    conv_id=conversation_id, to_message_id=resolved_parent
                )
            )

        # Session
        session_id = conversation_id  # session_id = conversation_id

        # 设置 artifact session
        if self.artifact_manager:
            self.artifact_manager.set_session(session_id)

        # 从父消息 metadata 中恢复 always_allowed_tools
        parent_always_allowed = []
        if resolved_parent:
            parent_meta = await self.conversation_manager.get_message_metadata_async(resolved_parent)
            parent_always_allowed = parent_meta.get("always_allowed_tools", [])

        # 创建初始状态
        initial_state = create_initial_state(
            task=user_input,
            session_id=session_id,
            message_id=message_id,
            path_events=path_events,
            always_allowed_tools=parent_always_allowed,
            uploaded_artifacts=uploaded_artifacts,
            force_compact=force_compact,
        )

        logger.info(f"Processing new message (streaming) in conversation {conversation_id}")

        # 添加消息到 conversation (after all pre-engine setup to avoid orphaned rows on failure)
        await self.conversation_manager.add_message_async(
            conv_id=conversation_id,
            message_id=message_id,
            user_input=user_input,
            parent_id=resolved_parent,
        )

        # ========== 执行引擎 ==========
        event_queue: asyncio.Queue = asyncio.Queue()
        final_state = None

        async def emit_to_queue(event_dict):
            """emit callback: 实时推送事件到 queue"""
            await event_queue.put(event_dict)

        async def run_engine():
            nonlocal final_state
            try:
                # 超时裹在引擎循环外层(engine_task 自己的 deadline)—— 无界工作
                # 的所在。超时后像协作式 cancel 一样"带 flag 正常返回",走完整
                # post-processing → decide_terminal 产出唯一的 TIMED_OUT 终态,
                # 而不是停在传输层当第二个 authority。Python 3.11+ 下 asyncio.timeout
                # 只把自己 deadline 触发的取消转成 TimeoutError;外部 task.cancel()
                # (lease fencing/shutdown)原样以 CancelledError 再抛 → 两个 except
                # 分支天然不混淆(超时在内层 engine_task,外部 cancel 来自外层 _wrapped)。
                # post-processing 本身不在此 deadline 内(只裹引擎):它是有界 DB 写 +
                # 函数级重试 + late-cancel 兜底,per-query wall-clock 由 DB 层负责
                # (PG command_timeout / MySQL server GUC,见 docs/architecture/execution-lifecycle.md)。
                async with asyncio.timeout(config.EXECUTION_TIMEOUT):
                    final_state = await execute_loop(
                        state=initial_state,
                        agents=self.agents,
                        tools=self.tools,
                        hooks=self.hooks,
                        artifact_manager=self.artifact_manager,
                        emit=emit_to_queue,
                    )
            except TimeoutError:
                # 引擎执行超时。模仿协作式 cancel:置 flag 正常返回,让 post-processing
                # 经 decide_terminal 产出 TIMED_OUT。注意只置 timed_out(不置 cancelled),
                # 否则会落进 decide_terminal 的 is_cancelled 分支被记成 CANCELLED。
                logger.warning(
                    f"Engine execution timed out after {config.EXECUTION_TIMEOUT}s "
                    f"for {message_id}; finalizing as TIMED_OUT"
                )
                initial_state["timed_out"] = True
                initial_state["completed"] = True
                # finalize_metrics 正常由 execute_loop 调;超时中断了它,这里补一次
                # 让 execution_metrics 可序列化(datetimes → isoformat)。
                try:
                    finalize_metrics(initial_state["execution_metrics"])
                except Exception as fm_err:
                    logger.warning(
                        f"finalize_metrics failed on timeout for {message_id}: {fm_err}",
                        exc_info=True,
                    )
                final_state = initial_state    # 正常返回 → 走完整 post-processing
            except asyncio.CancelledError:
                # External cancel cascaded into engine_task (lease fencing / shutdown).
                # CancelledError is BaseException — bypasses `except Exception` below — so
                # without this branch the in-memory state["events"] would die with the
                # task, violating "events persist unconditionally" (CLAUDE.md).
                # The cooperative path (RuntimeStore-driven cancel checked via
                # hooks.check_cancelled) sets state["cancelled"] and returns normally —
                # it never raises here. This branch fires only when the outer _wrapped
                # task is cancelled (e.g. _renew_loop fencing) and stream_execute's
                # finally cancels engine_task to propagate it inward.
                #
                # engine_task is its own asyncio.Task: once we catch this one cancel,
                # subsequent awaits in the handler (_persist_events) run normally.
                logger.warning(
                    f"Engine task cancelled externally for {message_id} "
                    f"(lease fencing/shutdown); persisting accumulated events"
                )
                initial_state["cancelled"] = True
                initial_state["completed"] = True
                # finalize_metrics is normally called by execute_loop; on this path it
                # was interrupted, so do it here to keep execution_metrics serializable
                # (datetimes → isoformat) before stuffing into the CANCELLED event data.
                try:
                    finalize_metrics(initial_state["execution_metrics"])
                except Exception as fm_err:
                    logger.warning(
                        f"finalize_metrics failed on cancel for {message_id}: {fm_err}",
                        exc_info=True,
                    )
                initial_state["events"].append(make_external_cancelled_event(
                    conversation_id=conversation_id,
                    message_id=message_id,
                    reason="external_cancel",
                    execution_metrics=initial_state.get("execution_metrics", {}),
                ))
                persisted = False
                try:
                    persisted = await self._persist_events(message_id, initial_state)
                except Exception as persist_err:
                    # Swallow to preserve CancelledError propagation; loud-log so ops sees it.
                    logger.exception(
                        f"Persist-on-cancel failed for {message_id}: {persist_err}"
                    )
                # Sync Message.response so the frontend renders the bubble +
                # event flow at all — MessageList gates AssistantMessage on
                # response being non-empty, and the events list is nested
                # inside AssistantMessage.
                #
                # CRITICAL: only write when events actually landed in DB.
                # Otherwise we create a "Message.response says cancelled,
                # but events table is empty" state — UI shows cancelled, but
                # next turn's LLM has no history of this turn. Mirrors the
                # success-path / late-cancel events-first invariant.
                #
                # Response text comes from the same decision function as
                # post-processing / late-cancel — single source of truth for
                # (terminal_type, cancel_source) → display string.
                if persisted:
                    pp_engine = PostProcessState(
                        conversation_id=conversation_id,
                        message_id=message_id,
                        final_state=initial_state,
                        terminal_type=StreamEventType.CANCELLED.value,
                        cancel_source="external",
                        terminal_appended=True,
                    )
                    response_to_write = choose_response_for_terminal(pp_engine)
                    try:
                        await self._with_db_retry(
                            lambda cm, er, am: cm.update_response_async(
                                conv_id=conversation_id,
                                message_id=message_id,
                                response=response_to_write,
                            )
                        )
                    except Exception as resp_err:
                        logger.warning(
                            f"update_response on external cancel failed for {message_id} "
                            f"(events persisted; UI may show empty bubble): {resp_err}",
                            exc_info=True,
                        )
                else:
                    logger.error(
                        f"Skipping update_response for {message_id}: event persist "
                        f"failed on cancel path — refusing to create 'cancel-shown-"
                        f"but-events-missing' state. UI bubble will be empty; user "
                        f"can retry from previous turn."
                    )
                # SSE is best-effort here (consumer likely gone too); skip yielding a
                # terminal event. History reconstruction reads from the persisted events.
                raise
            except Exception as e:
                logger.exception(f"Engine error: {e}")
                # Mark error on initial_state (final_state is still None at this point)
                initial_state["error"] = True
                initial_state["response"] = f"Engine error: {str(e)}"
                error_data = {
                    "success": False,
                    "conversation_id": conversation_id,
                    "message_id": message_id,
                    "error": str(e),
                }
                # Persist error event (will be written via _persist_events on initial_state)
                initial_state["events"].append(ExecutionEvent(
                    event_type=StreamEventType.ERROR.value,
                    agent_name=None,
                    data=error_data,
                ))
                await event_queue.put({
                    "type": StreamEventType.ERROR.value,
                    "timestamp": utc_now().isoformat(),
                    "data": error_data,
                })
            finally:
                await event_queue.put(_SENTINEL)

        engine_task = asyncio.create_task(run_engine())

        try:
            # Yield events in real-time as they arrive
            while True:
                event = await event_queue.get()
                if event is _SENTINEL:
                    break
                yield event
        finally:
            # On outer-task cancel, the generator's await above raises CancelledError;
            # engine_task is independent and would otherwise keep running. Cancel it
            # explicitly so its `except CancelledError` branch persists accumulated
            # events before we unwind. Awaiting then absorbs the cancel — engine_task
            # has already done the persist work.
            if not engine_task.done():
                engine_task.cancel()
            try:
                await engine_task
            except asyncio.CancelledError:
                pass

        # ========== Post-processing (with late-cancel boundary) ==========
        # Outer cancel (lease fencing / shutdown) can land in any of the awaits below
        # — _on_engine_exit, exists_async, flush_all, _persist_events, update_response,
        # update_metadata. The pp ledger records phase-by-phase progress so the
        # late-cancel recovery (_recover_from_late_cancel) doesn't reconstruct state
        # from scratch — it reads what already completed and continues from there.
        #
        # pp is created BEFORE the first await so late-cancel always has a ledger
        # to operate on. The only way pp stays None is if cancel lands between
        # `pp = PostProcessState(...)` construction and the next await — impossible
        # because there's no await in between.
        pp: Optional[PostProcessState] = None
        try:
            # final_state fallback BEFORE any await — covers the engine-error path
            # (run_engine's `except Exception` appended ERROR to initial_state["events"]
            # but never assigned final_state). Without this, late-cancel handler
            # would see final_state=None and skip persistence.
            if final_state is None:
                final_state = initial_state

            pp = PostProcessState(
                conversation_id=conversation_id,
                message_id=message_id,
                final_state=final_state,
            )

            # Engine 已退出，不会再 drain 消息 — 立即取消活跃映射，
            # 使 /inject 端点正确返回 409 而非假装成功入队
            if self._on_engine_exit:
                await self._on_engine_exit(conversation_id, message_id)

            # Layer 1: 早判 conversation 是否仍存在。
            # 删除路径不抢 lease，conv 行可能在 engine 跑完前消失（DELETE /chat/{id}
            # 或硬删用户触发的 CASCADE）。早返回跳过后续三段写库，避免撞 FK。
            try:
                pp.conv_alive = await self._with_db_retry(
                    lambda cm, er, am: cm.exists_async(conversation_id)
                )
            except Exception as exists_err:
                # exists 探测的瞬断不应阻塞 post-processing —— 当作 alive 走原流程。
                # 用 exception 落堆栈:这里能藏真 bug(DB 连接/查询逻辑错),不只是瞬断。
                logger.exception(
                    f"exists() probe failed for {conversation_id} (msg={message_id}), "
                    f"falling through to normal post-processing: {exists_err}"
                )
                pp.conv_alive = True

            if not pp.conv_alive:
                logger.info(
                    f"Conversation {conversation_id} deleted during execution, "
                    f"skip persistence (message_id={message_id})"
                )
                # Lease 由 runner 的 _wrapped finally → cleanup_execution 兜底释放
                return

            try:
                # Flush dirty artifacts to DB
                if self.artifact_manager:
                    try:
                        await self.artifact_manager.flush_all(
                            session_id, db_manager=self._db_manager
                        )
                        pp.artifacts_flushed = True
                    except IntegrityError as flush_ie:
                        # Layer 2: exists() 之后到 flush 之间 conv 被删（TOCTOU）
                        logger.warning(
                            f"Conversation {conversation_id} deleted mid-persist "
                            f"(artifact phase, msg={message_id}): {flush_ie}"
                        )
                        return
                    except Exception as flush_err:
                        logger.exception(f"Artifact flush failed after retries: {flush_err}")
                        pp.flush_error = f"Artifact persistence failed: {flush_err}"

                # 决定 terminal（纯函数,无 IO）。engine ERROR 路径已自行 append ERROR 到
                # events,decide_terminal 在 has_error 分支把 terminal_event 设为 None
                # 且把 terminal_appended 标 True,防止下面二次 append。
                decide_terminal(pp)

                if pp.terminal_event is not None and not pp.terminal_appended:
                    pp.final_state["events"].append(pp.terminal_event)
                    pp.terminal_appended = True

                # 持久化事件 —— 必须先于 Message.response 更新。
                # 新架构下 events 是历史 source of truth，持久化失败=下一轮恢复不了本轮。
                # 持久化成功后再更新 Message.response，避免出现"显示成功 + 历史丢失"的假成功状态。
                try:
                    pp.events_persisted = await self._persist_events(
                        message_id, pp.final_state
                    )
                except IntegrityError as events_ie:
                    # Layer 2: events 写阶段命中 conv 删除的 TOCTOU
                    logger.warning(
                        f"Conversation {conversation_id} deleted mid-persist "
                        f"(events phase, msg={message_id}): {events_ie}"
                    )
                    return

                if not pp.events_persisted:
                    # 持久化失败 → 整轮判定失败,覆盖终态为 ERROR,跳过 response/metadata 更新
                    logger.error(
                        f"Aborting turn {message_id}: event persistence failed, "
                        f"Message.response will not be updated"
                    )
                    yield {
                        "type": StreamEventType.ERROR.value,
                        "timestamp": utc_now().isoformat(),
                        "data": {
                            "success": False,
                            "conversation_id": conversation_id,
                            "message_id": message_id,
                            "error": "Event persistence failed — turn aborted, please retry",
                            "execution_metrics": pp.final_state.get("execution_metrics", {}),
                        },
                    }
                    return

                # events 已落库 → 写 Message.response (单一真相源:
                # success path 和 late-cancel handler 都调 choose_response_for_terminal,
                # 不再有"两份计算"。
                response_to_write = choose_response_for_terminal(pp)
                if response_to_write:
                    # CLAIM the slot BEFORE the await — see PostProcessState's
                    # response_update_attempted docstring for the cancel-mid-await
                    # race rationale. If cancel lands while the await is suspended,
                    # the DB may have already committed but Python never reached a
                    # post-await flag. Late handler checks attempted=True and skips.
                    pp.response_update_attempted = True
                    try:
                        await self._with_db_retry(
                            lambda cm, er, am: cm.update_response_async(
                                conv_id=conversation_id, message_id=message_id,
                                response=response_to_write,
                            )
                        )
                        pp.response_updated = True
                    except Exception as resp_err:
                        # events 已成功 → 历史正确,仅显示可能短暂落后,不把终态转为 ERROR
                        logger.warning(
                            f"Message.response update failed for {message_id} "
                            f"(events already persisted, display may lag): {resp_err}",
                            exc_info=True,
                        )

                metadata_updates = {}
                always_allowed = pp.final_state.get("always_allowed_tools", [])
                if always_allowed:
                    metadata_updates["always_allowed_tools"] = always_allowed
                execution_metrics = pp.final_state.get("execution_metrics", {})
                if execution_metrics:
                    metadata_updates["execution_metrics"] = execution_metrics
                if metadata_updates:
                    try:
                        await self._with_db_retry(
                            lambda cm, er, am: cm.update_message_metadata_async(
                                conv_id=conversation_id, message_id=message_id, metadata=metadata_updates,
                            )
                        )
                        pp.metadata_updated = True
                    except Exception as meta_err:
                        logger.warning(
                            f"Message.metadata update failed for {message_id}: {meta_err}",
                            exc_info=True,
                        )

                logger.info("Streaming execution completed")

                # 发送终态到 SSE。engine ERROR 路径 terminal_event=None (engine 已实时推送过),
                # 自然跳过。flush_error / cancelled / COMPLETE 都从 pp.terminal_event 取。
                if pp.terminal_event is not None:
                    yield {
                        "type": pp.terminal_event.event_type,
                        "timestamp": utc_now().isoformat(),
                        "data": pp.terminal_event.data,
                    }

            except Exception as e:
                logger.exception(f"Error in post-processing: {e}")
                yield {
                    "type": StreamEventType.ERROR.value,
                    "timestamp": utc_now().isoformat(),
                    "data": {
                        "success": False,
                        "conversation_id": conversation_id,
                        "message_id": message_id,
                        "error": str(e),
                    }
                }
        except asyncio.CancelledError:
            # Late-cancel landed during post-processing. pp ledger has every
            # phase's "did it complete" recorded — _recover_from_late_cancel
            # reads pp and continues, doesn't re-scan or recompute.
            if pp is not None:
                await self._recover_from_late_cancel(pp)
            raise

    async def _recover_from_late_cancel(self, pp: PostProcessState) -> None:
        """
        late-cancel handler — idempotent recovery driven by the pp ledger.

        Invariants (enforced structurally via pp + helpers, not via repeated checks):
        1. events 落库前不写 Message.response          (gate: pp.events_persisted)
        2. response slot 一旦 claimed 不再覆盖          (gate: pp.response_update_attempted)
        3. 已有 semantic terminal 不被 late-cancel 改   (ensure_terminal adopt path)
        4. 只在无 terminal 时才写 system placeholder    (choose_response_for_terminal 按 cancel_source 分)
        """
        # Phase 1: ensure events are in DB
        if not pp.events_persisted:
            ensure_terminal(pp)
            try:
                pp.events_persisted = await self._persist_events(
                    pp.message_id, pp.final_state
                )
                if pp.events_persisted:
                    logger.info(
                        f"Late-cancel persist succeeded for {pp.message_id} "
                        f"(cancel hit mid-post-processing)"
                    )
            except Exception as persist_err:
                # Loud-log but never shadow the propagating CancelledError —
                # the runner's cleanup needs to see a cancelled task.
                logger.exception(
                    f"Late-cancel persist failed for {pp.message_id}: {persist_err}"
                )
                pp.events_persisted = False

        if not pp.events_persisted:
            logger.error(
                f"Skipping update_response for {pp.message_id}: late-cancel "
                f"persist failed — refusing to create 'cancel-shown-but-"
                f"events-missing' state. UI bubble will be empty; user can "
                f"retry from previous turn."
            )
            return

        # Phase 2: write Message.response when slot not yet claimed
        if pp.response_update_attempted:
            # success path already claimed (or attempted) — defeats cancel-mid-await
            # race where DB committed real response but await raised before post-await
            # flag could be set
            return

        response_to_write = choose_response_for_terminal(pp)
        if not response_to_write:
            return

        pp.response_update_attempted = True
        try:
            await self._with_db_retry(
                lambda cm, er, am: cm.update_response_async(
                    conv_id=pp.conversation_id, message_id=pp.message_id,
                    response=response_to_write,
                )
            )
            pp.response_updated = True
        except Exception as resp_err:
            logger.warning(
                f"Late-cancel response update failed for {pp.message_id}: {resp_err}",
                exc_info=True,
            )

    async def _persist_events(self, message_id: str, final_state: Dict[str, Any]) -> bool:
        """
        持久化事件到 MessageEvent 表

        新架构下 events 是历史的 source of truth（Message.response 仅用于显示），
        持久化失败 = 下一轮恢复不了这一轮的上下文。因此返回 bool 让 caller 能据此
        把 terminal 转成 ERROR，而不是静默吞掉。

        Returns:
            True — 成功，或无需持久化（无事件 / 无 repo）
            False — 批量写入重试后仍失败

        Raises:
            IntegrityError — conv 已被删除（caller 应早返回，跳过后续阶段）
        """
        if not self.message_event_repo:
            return True

        all_events = final_state.get("events", [])
        # 只持久化本轮新产生的 events（历史 events 是 turn 开始时从 DB 载入的快照，
        # 已经在 DB 里，不要重复写）
        new_events = [e for e in all_events if not getattr(e, "is_historical", False)]
        if not new_events:
            return True

        # Assign stable event_id for retry idempotency: {message_id}-{seq}
        db_events = [
            {
                "event_id": f"{message_id}-{seq}",
                "message_id": message_id,
                "event_type": e.event_type,
                "agent_name": e.agent_name,
                "data": e.data,
                "created_at": e.created_at,
            }
            for seq, e in enumerate(new_events)
        ]

        try:
            await self._with_db_retry(
                lambda cm, er, am: er.batch_create(db_events)
            )
            logger.info(f"Persisted {len(db_events)} events for message {message_id}")
            return True
        except IntegrityError:
            # FK 违规通常意味着 conv/message 行已被删除（TOCTOU 窗口）。
            # 透传给 caller 区分"基础设施失败"和"被外部删除"，避免被
            # 当作普通持久化失败而错误地把整轮转 ERROR 给前端。
            raise
        except Exception as e:
            # 事件丢失 = 最该定位的失败:用 exception 落完整堆栈(原先 error 无堆栈)。
            logger.exception(
                f"Event persistence failed after retries for {message_id} "
                f"({len(db_events)} events lost): {e}"
            )
            return False
