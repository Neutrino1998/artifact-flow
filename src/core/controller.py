"""
执行控制器 — Pi-style while loop

职责：
1. stream_execute() — 创建 state → 启动 execute_loop → 事件推 StreamTransport
2. resume() — 唤醒暂停的 coroutine
3. 对话管理复用 ConversationManager
"""

import asyncio
from typing import Awaitable, Callable, Dict, Optional, Any, AsyncGenerator
from uuid import uuid4
from datetime import datetime

from sqlalchemy.exc import IntegrityError

from config import config
from core.engine import EngineHooks, create_initial_state, execute_loop, finalize_metrics
from core.events import StreamEventType, ExecutionEvent
from core.conversation_manager import ConversationManager
from tools.base import BaseTool
from tools.builtin.artifact_ops import ArtifactManager
from utils.logger import get_logger

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
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        流式执行接口（新消息）

        Args:
            user_input: 用户消息内容
            conversation_id: 对话ID
            parent_message_id: 父消息ID
            message_id: 消息ID

        Yields:
            流式事件字典
        """
        if user_input is None:
            raise ValueError("'user_input' is required for new message execution")

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
            "timestamp": datetime.now().isoformat(),
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
        # Hoisted to function scope so the outer late-cancel handler (below
        # post-processing) can read it. Stays False until _persist_events
        # returns True in the inner try block.
        events_persisted = False
        # Tracks whether the success-path update_response_async actually wrote
        # Message.response. Late-cancel handler uses this to avoid overwriting
        # a successfully-stored response (cancel hit at update_metadata, AFTER
        # response already committed) — only writes the CANCELLED_RESPONSE_BY_SYSTEM
        # placeholder when the response field is still empty.
        response_updated = False

        async def emit_to_queue(event_dict):
            """emit callback: 实时推送事件到 queue"""
            await event_queue.put(event_dict)

        async def run_engine():
            nonlocal final_state
            try:
                final_state = await execute_loop(
                    state=initial_state,
                    agents=self.agents,
                    tools=self.tools,
                    hooks=self.hooks,
                    artifact_manager=self.artifact_manager,
                    emit=emit_to_queue,
                )
            except asyncio.CancelledError:
                # External cancel cascaded into engine_task (lease fencing / shutdown).
                # CancelledError is BaseException — bypasses `except Exception` below — so
                # without this branch the in-memory state["events"] would die with the
                # task, violating "events persist unconditionally" (CLAUDE.md / bug ④
                # from 2026-05-14 incident). The cooperative path (RuntimeStore-driven
                # cancel checked via hooks.check_cancelled) sets state["cancelled"] and
                # returns normally — it never raises here. This branch fires only when
                # the outer _wrapped task is cancelled (e.g. _renew_loop fencing) and
                # stream_execute's finally cancels engine_task to propagate it inward.
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
                        f"finalize_metrics failed on cancel for {message_id}: {fm_err}"
                    )
                initial_state["events"].append(ExecutionEvent(
                    event_type=StreamEventType.CANCELLED.value,
                    agent_name=None,
                    data={
                        "success": False,
                        "cancelled": True,
                        "conversation_id": conversation_id,
                        "message_id": message_id,
                        "reason": "external_cancel",
                        "execution_metrics": initial_state.get("execution_metrics", {}),
                    },
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
                # CRITICAL: only write the cancel placeholder when events
                # actually landed in DB. Otherwise we create a "Message.response
                # says cancelled, but events table is empty" state — user sees a
                # cancel bubble, expects the next turn's LLM to know what
                # happened, and the LLM sees nothing (events are the history
                # source of truth). This mirrors the success-path invariant
                # documented at controller.py around the inner try block:
                # "持久化成功后再更新 Message.response，避免出现'显示成功 + 历史丢失'
                # 的假成功状态".
                if persisted:
                    try:
                        await self._with_db_retry(
                            lambda cm, er, am: cm.update_response_async(
                                conv_id=conversation_id,
                                message_id=message_id,
                                response=config.CANCELLED_RESPONSE_BY_SYSTEM,
                            )
                        )
                    except Exception as resp_err:
                        logger.warning(
                            f"update_response on external cancel failed for {message_id} "
                            f"(events persisted; UI may show empty bubble): {resp_err}"
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
                    "timestamp": datetime.now().isoformat(),
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

        # ========== Late-cancel boundary ==========
        # Outer cancel (lease fencing / shutdown) can arrive AFTER engine_task
        # already finished — i.e., during any of the post-processing awaits below
        # (_on_engine_exit, exists_async, flush_all, _persist_events). At that
        # point engine_task.done() is already True so the generator's finally
        # above is a no-op, and run_engine's `except CancelledError` cannot fire
        # (it has already returned). Without this outer guard, CancelledError
        # propagates straight through post-processing and skips _persist_events
        # — re-introducing bug ④ in a narrower window.
        #
        # Strategy: catch CancelledError, late-persist if events haven't landed
        # yet, then re-raise so the runner's cleanup still sees a cancelled task.
        try:
            # ========== Post-processing ==========
            # final_state fallback must happen BEFORE any await — otherwise a
            # late-cancel landing in _on_engine_exit / exists_async on the
            # engine-error path (run_engine `except Exception` appends ERROR to
            # initial_state["events"] but never assigns final_state) would hit
            # the late-cancel handler with final_state=None and skip persistence.
            if final_state is None:
                final_state = initial_state

            # Engine 已退出，不会再 drain 消息 — 立即取消活跃映射，
            # 使 /inject 端点正确返回 409 而非假装成功入队
            if self._on_engine_exit:
                await self._on_engine_exit(conversation_id, message_id)

            # Layer 1: 早判 conversation 是否仍存在。
            # 删除路径不抢 lease，conv 行可能在 engine 跑完前消失（DELETE /chat/{id}
            # 或硬删用户触发的 CASCADE）。早返回跳过后续三段写库，避免撞 FK。
            try:
                conv_alive = await self._with_db_retry(
                    lambda cm, er, am: cm.exists_async(conversation_id)
                )
            except Exception as exists_err:
                # exists 探测的瞬断不应阻塞 post-processing —— 当作 alive 走原流程
                logger.warning(
                    f"exists() probe failed for {conversation_id} (msg={message_id}), "
                    f"falling through to normal post-processing: {exists_err}"
                )
                conv_alive = True

            if not conv_alive:
                logger.info(
                    f"Conversation {conversation_id} deleted during execution, "
                    f"skip persistence (message_id={message_id})"
                )
                # Lease 由 runner 的 _wrapped finally → cleanup_execution 兜底释放
                return

            try:
                response = final_state.get("response", "")
                has_error = final_state.get("error", False)
                is_cancelled = final_state.get("cancelled", False)

                # Display-snapshot backfill. Message.response is display-only, and the
                # frontend gates AssistantMessage on it being non-empty. The engine only
                # packs tool-call-free prose into state["response"], so a turn cancelled
                # mid-tool-call / mid-reasoning / during TTFT arrives here with response
                # == "". Mirror the error path's placeholder so the turn still renders
                # (and its execution flow is reconstructed from events).
                display_response = (response or config.CANCELLED_RESPONSE_BY_USER) if is_cancelled else response

                # Flush dirty artifacts to DB
                flush_error: Optional[str] = None
                if self.artifact_manager:
                    try:
                        await self.artifact_manager.flush_all(
                            session_id, db_manager=self._db_manager
                        )
                    except IntegrityError as flush_ie:
                        # Layer 2: exists() 之后到 flush 之间 conv 被删（TOCTOU）
                        logger.warning(
                            f"Conversation {conversation_id} deleted mid-persist "
                            f"(artifact phase, msg={message_id}): {flush_ie}"
                        )
                        return
                    except Exception as flush_err:
                        logger.exception(f"Artifact flush failed after retries: {flush_err}")
                        flush_error = f"Artifact persistence failed: {flush_err}"

                # 终态事件：error 路径由 engine 已发（在 state["events"] 中），
                # controller 负责 success / cancelled / flush_error 路径的终态事件。
                terminal_event_dict = None
                if is_cancelled:
                    terminal_event_dict = {
                        "type": StreamEventType.CANCELLED.value,
                        "timestamp": datetime.now().isoformat(),
                        "data": {
                            "success": False,
                            "cancelled": True,
                            "conversation_id": conversation_id,
                            "message_id": message_id,
                            "response": display_response,
                            "execution_metrics": final_state.get("execution_metrics", {}),
                        }
                    }
                elif flush_error:
                    terminal_event_dict = {
                        "type": StreamEventType.ERROR.value,
                        "timestamp": datetime.now().isoformat(),
                        "data": {
                            "success": False,
                            "conversation_id": conversation_id,
                            "message_id": message_id,
                            "error": flush_error,
                            "execution_metrics": final_state.get("execution_metrics", {}),
                        }
                    }
                elif not has_error:
                    terminal_event_dict = {
                        "type": StreamEventType.COMPLETE.value,
                        "timestamp": datetime.now().isoformat(),
                        "data": {
                            "success": True,
                            "conversation_id": conversation_id,
                            "message_id": message_id,
                            "response": response,
                            "execution_metrics": final_state.get("execution_metrics", {}),
                        }
                    }

                if terminal_event_dict:
                    # 终态事件入库
                    final_state["events"].append(ExecutionEvent(
                        event_type=terminal_event_dict["type"],
                        agent_name=None,
                        data=terminal_event_dict["data"],
                    ))

                # 持久化事件 —— 必须先于 Message.response 更新。
                # 新架构下 events 是历史 source of truth，持久化失败=下一轮恢复不了本轮。
                # 持久化成功后再更新 Message.response，避免出现"显示成功 + 历史丢失"的假成功状态。
                try:
                    events_persisted = await self._persist_events(message_id, final_state)
                except IntegrityError as events_ie:
                    # Layer 2: events 写阶段命中 conv 删除的 TOCTOU
                    logger.warning(
                        f"Conversation {conversation_id} deleted mid-persist "
                        f"(events phase, msg={message_id}): {events_ie}"
                    )
                    return

                if not events_persisted:
                    # 持久化失败 → 整轮判定失败，覆盖终态为 ERROR，跳过 response/metadata 更新
                    logger.error(
                        f"Aborting turn {message_id}: event persistence failed, "
                        f"Message.response will not be updated"
                    )
                    terminal_event_dict = {
                        "type": StreamEventType.ERROR.value,
                        "timestamp": datetime.now().isoformat(),
                        "data": {
                            "success": False,
                            "conversation_id": conversation_id,
                            "message_id": message_id,
                            "error": "Event persistence failed — turn aborted, please retry",
                            "execution_metrics": final_state.get("execution_metrics", {}),
                        }
                    }
                else:
                    # events 已落库 → 可以更新 Message.response 和 metadata（best-effort）
                    # display_response 已处理 success / cancelled；error 单独兜底文案。
                    final_response = (response or "An error occurred during execution.") if has_error else display_response
                    try:
                        await self._with_db_retry(
                            lambda cm, er, am: cm.update_response_async(
                                conv_id=conversation_id, message_id=message_id, response=final_response,
                            )
                        )
                        response_updated = True
                    except Exception as resp_err:
                        # events 已成功 → 历史正确，仅显示可能短暂落后，不把终态转为 ERROR
                        logger.warning(
                            f"Message.response update failed for {message_id} "
                            f"(events already persisted, display may lag): {resp_err}"
                        )

                    metadata_updates = {}
                    always_allowed = final_state.get("always_allowed_tools", [])
                    if always_allowed:
                        metadata_updates["always_allowed_tools"] = always_allowed
                    execution_metrics = final_state.get("execution_metrics", {})
                    if execution_metrics:
                        metadata_updates["execution_metrics"] = execution_metrics
                    if metadata_updates:
                        try:
                            await self._with_db_retry(
                                lambda cm, er, am: cm.update_message_metadata_async(
                                    conv_id=conversation_id, message_id=message_id, metadata=metadata_updates,
                                )
                            )
                        except Exception as meta_err:
                            logger.warning(
                                f"Message.metadata update failed for {message_id}: {meta_err}"
                            )

                    logger.info("Streaming execution completed")

                # 发送终态到 SSE（error 已由 engine 实时推送）
                if terminal_event_dict:
                    yield terminal_event_dict

            except Exception as e:
                logger.exception(f"Error in post-processing: {e}")
                yield {
                    "type": StreamEventType.ERROR.value,
                    "timestamp": datetime.now().isoformat(),
                    "data": {
                        "success": False,
                        "conversation_id": conversation_id,
                        "message_id": message_id,
                        "error": str(e),
                    }
                }
        except asyncio.CancelledError:
            # Late-cancel landed mid-post-processing (between engine_task exit and
            # _persist_events). If events haven't been written yet, do a best-effort
            # idempotent persist before propagating. `events_persisted` was hoisted
            # to function scope so we can see whatever the inner try managed to set.
            #
            # Idempotency note: _persist_events uses {message_id}-{seq} stable IDs
            # and batch_create short-circuits to [] when ALL ids already exist.
            # That short-circuit breaks if we append a NEW event after a partial
            # write — so only append the CANCELLED terminal when no terminal exists
            # yet. If post-processing already appended COMPLETE/ERROR before the
            # cancel hit but persist hadn't yet run, we keep that terminal as-is
            # (engine semantically completed; cancel only hit infrastructure).
            # Track whether events end up in DB by ANY path (success-path persist
            # before cancel, or this late-cancel persist). Gates the response
            # update below — see "events-in-DB invariant" comment there.
            events_in_db = events_persisted
            if not events_persisted:
                # `final_state or initial_state` — defense-in-depth so this branch
                # stays safe even if a future change reintroduces the "final_state
                # is still None" window in post-processing.
                state_to_persist = final_state if final_state is not None else initial_state
                terminal_types = {
                    StreamEventType.COMPLETE.value,
                    StreamEventType.ERROR.value,
                    StreamEventType.CANCELLED.value,
                }
                has_terminal = any(
                    e.event_type in terminal_types
                    for e in state_to_persist.get("events", [])
                )
                if not has_terminal:
                    state_to_persist["cancelled"] = True
                    state_to_persist["events"].append(ExecutionEvent(
                        event_type=StreamEventType.CANCELLED.value,
                        agent_name=None,
                        data={
                            "success": False,
                            "cancelled": True,
                            "conversation_id": conversation_id,
                            "message_id": message_id,
                            "reason": "external_cancel_post_processing",
                        },
                    ))
                try:
                    events_in_db = await self._persist_events(message_id, state_to_persist)
                    if events_in_db:
                        logger.info(
                            f"Late-cancel persist succeeded for {message_id} "
                            f"(cancel hit mid-post-processing)"
                        )
                except Exception as persist_err:
                    # Loud-log but never shadow the propagating CancelledError —
                    # the runner's cleanup needs to see a cancelled task.
                    logger.exception(
                        f"Late-cancel persist failed for {message_id}: {persist_err}"
                    )
                    events_in_db = False
            # Sync Message.response too — same reasoning as the engine_task path:
            # frontend MessageList gates the whole AssistantMessage (and its
            # events flow) on response non-empty.
            #
            # CRITICAL "events-in-DB" invariant: only write the cancel placeholder
            # when events actually landed (success-path persist OR this late
            # persist). Without this check, persist failure produces a
            # "cancel-shown-but-events-missing" state — UI shows cancelled, but
            # the next turn's LLM has no history of this turn. Mirrors the
            # success-path's events_persisted gate.
            #
            # Also skip when response_updated is True (cancel hit at
            # update_metadata, AFTER response already committed with real engine
            # output — overwriting would clobber the real content).
            if events_in_db and not response_updated:
                try:
                    await self._with_db_retry(
                        lambda cm, er, am: cm.update_response_async(
                            conv_id=conversation_id,
                            message_id=message_id,
                            response=config.CANCELLED_RESPONSE_BY_SYSTEM,
                        )
                    )
                except Exception as resp_err:
                    logger.warning(
                        f"Late-cancel response update failed for {message_id}: {resp_err}"
                    )
            elif not events_in_db:
                logger.error(
                    f"Skipping update_response for {message_id}: late-cancel "
                    f"persist failed — refusing to create 'cancel-shown-but-"
                    f"events-missing' state. UI bubble will be empty; user can "
                    f"retry from previous turn."
                )
            raise

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
            logger.error(
                f"Event persistence failed after retries for {message_id} "
                f"({len(db_events)} events lost): {e}"
            )
            return False

