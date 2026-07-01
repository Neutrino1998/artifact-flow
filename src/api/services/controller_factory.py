"""
Controller Factory — ExecutionController 组装 + 执行推送

从 chat.py 提取，将 controller 组装和执行推送逻辑与路由层解耦。
"""

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator, AsyncIterator

from config import config
from api.dependencies import (
    get_db_manager,
    get_execution_runner,
    get_tools,
)
from api.services.stream_transport import StreamTransport
from utils.logger import get_logger, get_request_id
from utils.time import utc_now

logger = get_logger("ArtifactFlow")


def sanitize_error_event(event: dict) -> dict:
    """脱敏 error 事件并注入 request_id 定位码。

    request_id 不论 DEBUG 都注入(prod 回传给用户安全,是可回传的错误码);
    脱敏只删 error 文本,绝不删定位码。request_id 取自当前 context —— 后台
    引擎任务由 chat 请求 create_task 起,会继承发起请求的 request_id。
    """
    if event.get("type") != "error" or not isinstance(event.get("data"), dict):
        return event
    data = {**event["data"]}
    req_id = get_request_id()
    if req_id and not data.get("request_id"):
        data["request_id"] = req_id
    if not config.DEBUG:
        data["error"] = "Internal server error"
    return {**event, "data": data}


@asynccontextmanager
async def create_controller(
    conversation_id: str, message_id: str, user_id: str
) -> AsyncGenerator:
    """
    Build a fresh ExecutionController wired to the db_manager factory.

    Why not Depends(get_controller)? send_message() launches a background task whose
    lifetime exceeds the HTTP request, so it can't ride the request-scoped session.

    B-5: rather than pre-open ONE turn-long session for the whole background task (held
    idle-in-transaction while awaiting LLM / authorization, and the turn's only DB read
    with no fresh-session retry), the controller holds the db_manager and opens a SHORT
    retrying session per DB touch — artifact (ArtifactService.db_manager) / conversation
    + event (controller._with_db_retry) / credential (resolver.with_retry). Only the
    one-shot registry snapshot is read here (also on a short session); everything else is
    lazy inside stream_execute.

    Usage:
        async with create_controller(conv_id, msg_id) as ctrl:
            async for event in ctrl.stream_execute(...):
                ...
    """
    from core.controller import ExecutionController
    from core.department_resolver import load_ancestor_ids
    from core.effective_skillset import resolve_effective_skillset
    from core.effective_toolset import resolve_all
    from core.engine import EngineHooks
    from reconcile.snapshot import load_registry_snapshot, load_skill_snapshot
    from repositories.skill_repo import SkillRepository
    from tools.builtin.artifact_service import ArtifactService
    from tools.builtin.artifact_ops import create_artifact_tools
    from tools.builtin.read_skill import create_skill_tools
    from tools.builtin.sandbox_session import SandboxSession
    from tools.builtin.sandbox_ops import create_sandbox_tools
    from tools.builtin.skill_service import SkillService

    db_manager = get_db_manager()
    runner = get_execution_runner()
    store = runner.store

    # per-turn 沙盒 session:对象壳在此创建(同 ArtifactService,构造注入工具),
    # 容器 lazy 于首个沙盒工具调用 —— 无沙盒 turn 壳零成本。拆除句柄注册进
    # runner,在 _wrapped 真 finally(cleanup_execution 旁)执行,与 lease 同生灭;
    # close 幂等不依赖 DB session,故晚于本 context manager 退出也安全。
    sandbox_session = SandboxSession(conversation_id, message_id)
    runner.register_cleanup(message_id, sandbox_session.close)

    # ArtifactService 持 db_manager(不绑一条 turn-long session):turn 期每次 DB 读/写各开
    # 短 retrying session 读完即关(B-5),WorkingSet 留实例做 turn-live 缓存。
    artifact_service = ArtifactService(db_manager=db_manager)

    # per-turn 注册表快照:agent 元数据 + external 工具(HttpTool)从 DB 重建。
    # 进程级 reconcile(entrypoint / 手动脚本)把 config 物化进库,这里每 turn 读一
    # 次快照 —— 避跨 worker 缓存失效,与引擎每 turn 重建 MessageEvent 历史同源(原则 5)。
    # 短 session 读完即关;凭证 resolver 拿 db_manager(execute 期再各开短 session lazy
    # 解密),不被快照 session 骑成 turn-long 连接。
    snapshot = await db_manager.with_retry(
        lambda session: load_registry_snapshot(session, db_manager=db_manager)
    )
    if "lead_agent" not in snapshot.agents:
        # DB 注册表为空/缺 lead_agent = reconcile 没跑(或跑挂)。引擎此时无可执行
        # agent,与其在 turn 中途撞 KeyError,不如在装配处 loud-fail 给出可操作指引。
        raise RuntimeError(
            "Tool/agent registry is empty or missing 'lead_agent' — run "
            "`python scripts/reconcile_config.py` to materialize config into the DB "
            "(prod: entrypoint does this under the migration lock)."
        )

    # skill 解析(C-2):一条短 session 读 user-agnostic skill 快照 + 该用户的 user_skill
    # 覆盖 + 部门祖先链命中(决策 10:父覆盖子树)。department_id 从 DB 取(不信 JWT —— dept
    # 授权是 correctness)。读完即关(B-5);read_skill 的正文取数另由 SkillService lazy。
    async def _load_skills(session):
        repo = SkillRepository(session)
        skill_snap = await load_skill_snapshot(session)
        dept_id = await repo.user_department_id(user_id)
        ancestors = await load_ancestor_ids(session, dept_id)
        overrides = await repo.user_overrides(user_id)
        dept_matched = await repo.dept_matched_slugs(ancestors)
        return skill_snap, overrides, dept_matched

    skill_snapshot, skill_overrides, dept_matched = await db_manager.with_retry(_load_skills)
    effective_skillset = resolve_effective_skillset(
        user_id, skill_snapshot, skill_overrides, dept_matched
    )

    # 合并工具来源:全局 builtin(进程级) + DB external(快照重建) + 请求级
    # artifact / 沙盒 / skill 工具。external 工具自此唯一来源是 DB —— 不再进程级加载
    # config/tools/*.md(见 dependencies._load_tools)。read_skill 请求级(持 EffectiveSkillSet
    # 做可见性闸 + SkillService lazy 取正文),仅有可见 skill 时建。
    artifact_tools = create_artifact_tools(artifact_service)
    sandbox_tools = create_sandbox_tools(sandbox_session, artifact_service)
    skill_tools = create_skill_tools(SkillService(db_manager=db_manager), effective_skillset)
    all_tools = {
        **get_tools(),
        **snapshot.external_tools,
        **{t.name: t for t in artifact_tools},
        **{t.name: t for t in sandbox_tools},
        **{t.name: t for t in skill_tools},
    }
    agents = snapshot.agents
    # skill_grants 只从**可见**子集烤(能力跟随当前可见性):不给用户已看不见的 skill 烤
    # 授予 → 跨回合恢复 active_skills 时,被撤销(admin 撤 dept 授权 / public→department /
    # 换部门)的 skill 其 activate_skill 自然空操作,by-construction 消泄漏,非在恢复循环加闸。
    # active_skills 名单仍 sticky(= 用户意图),能力每轮按可见性重算 → visible=correctness /
    # enabled=UX 的切分落到工具授予轴(admin 撤后又授,下轮 slug 仍在、能力自动回来)。
    visible_skill_snapshot = {
        slug: skill_snapshot[slug] for slug in effective_skillset.visible
    }
    # 决策 11 单一解析点:把每 agent 的宇宙(builtin ∪ units)解析成扁平
    # {full_name: 等级};等级从工具对象取(绑定不存等级)。引擎/上下文构建全程读这个。
    effective_toolsets = resolve_all(snapshot, all_tools, skill_snapshot=visible_skill_snapshot)

    # read_skill 注入:skill 全 agent 可见 → 有可见 skill 时把 read_skill 灌进每个 agent
    # 的可调集(等级取工具定义=AUTO)。setdefault 不覆盖(agent 本不会声明它,纯防御)。
    if skill_tools:
        read_skill_perm = all_tools["read_skill"].permission
        for ets in effective_toolsets.values():
            ets.permissions.setdefault("read_skill", read_skill_perm)

    hooks = EngineHooks(
        check_cancelled=store.is_cancelled,
        wait_for_interrupt=store.wait_for_interrupt,
        drain_messages=store.drain_messages,
    )

    async def _on_engine_exit(conv_id: str, msg_id: str) -> None:
        await store.clear_engine_interactive(conv_id, msg_id)

    # conversation_manager / message_event_repo 不在此绑 session(B-5):controller 经
    # _with_db_retry 每调一次开短 session(默认 ConversationManager() 仅作 no-db_manager
    # 回落,prod 永走 retry 路径);db_manager 在场,事件持久化 / 对话读写均不缺。
    yield ExecutionController(
        agents=agents,
        tools=all_tools,
        effective_toolsets=effective_toolsets,
        hooks=hooks,
        artifact_service=artifact_service,
        on_engine_exit=_on_engine_exit,
        db_manager=db_manager,
        sandbox_session=sandbox_session,
        effective_skillset=effective_skillset,
    )


async def run_and_push(
    stream_transport: StreamTransport,
    stream_id: str,
    event_stream: AsyncIterator[dict],
) -> None:
    """
    Consume events from a controller stream and push them to the StreamTransport.

    Handles timeout and unexpected errors, pushing sanitized error events.
    Execution runs to completion even if the SSE client disconnects.
    Stream is always closed by the producer in the finally block.
    """
    stream_closed = False
    # llm_chunk coalescing: 保留最新快照，定时 flush（80ms）
    pending_chunks: dict[str, dict] = {}  # "content" | "reasoning_content" → latest event
    last_flush_time = 0.0

    async def flush_pending():
        nonlocal last_flush_time, stream_closed
        for key in list(pending_chunks):
            if stream_closed:
                pending_chunks.clear()
                return
            if not await stream_transport.push_event(stream_id, sanitize_error_event(pending_chunks.pop(key))):
                logger.info(f"Stream {stream_id} closed, execution continues")
                stream_closed = True
        last_flush_time = asyncio.get_event_loop().time()

    # 纯转发器:不再裹 asyncio.timeout —— 超时裁判已下沉到 controller 的 engine_task
    # (run_engine 的 asyncio.timeout(EXECUTION_TIMEOUT) → TIMED_OUT 终态),与 DB 终态
    # 同源、经同一个 decide_terminal dispatcher 产出。这里只转发包括 TIMED_OUT 在内的
    # 所有事件。(后处理的 wall-clock 上界由 DB 层负责,见 controller.run_engine 注释。)
    try:
        async for event in event_stream:
            if stream_closed:
                continue

            if event.get("type") == "llm_chunk":
                data = event.get("data", {})
                chunk_key = "reasoning_content" if "reasoning_content" in data else "content"
                pending_chunks[chunk_key] = event
                now = asyncio.get_event_loop().time()
                if now - last_flush_time >= 0.08:
                    await flush_pending()
            else:
                await flush_pending()  # 非 chunk 前先 flush
                if stream_closed:
                    continue
                if not await stream_transport.push_event(stream_id, sanitize_error_event(event)):
                    logger.info(f"Stream {stream_id} closed, execution continues")
                    stream_closed = True

        await flush_pending()  # 流结束 flush 残余

    except Exception as e:
        logger.exception(f"Error in execution: {e}")
        await stream_transport.push_event(stream_id, sanitize_error_event({
            "type": "error",
            "timestamp": utc_now().isoformat(),
            "data": {"success": False, "error": str(e)}
        }))

    finally:
        # Producer 侧关闭 stream — 设 closed 状态 + 延迟清理 TTL
        await stream_transport.close_stream(stream_id)
