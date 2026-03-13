"""
Pi-style 执行引擎手动测试套件

覆盖场景：
  1. 基本问答
  2. 多轮对话（跨 request_scope 的 conversation_id 保持）
  3. Artifact 工具调用
  4. 权限确认流（CONFIRM → interrupt → resume）
  5. 分支对话（parent_message_id 分支）

用法：
    python -m tests.manual.engine
"""

import asyncio
from typing import Dict, Any, Optional
from datetime import datetime
from contextlib import asynccontextmanager

from core.controller import ExecutionController
from core.events import StreamEventType
from core.conversation_manager import ConversationManager
from agents.loader import load_all_agents
from tools.registry import ToolRegistry
from tools.implementations.artifact_ops import ArtifactManager, create_artifact_tools
from tools.implementations.call_subagent import CallSubagentTool
from tools.implementations.web_search import WebSearchTool
from tools.implementations.web_fetch import WebFetchTool
from api.services.task_manager import TaskManager
from db.database import DatabaseManager
from repositories.artifact_repo import ArtifactRepository
from repositories.conversation_repo import ConversationRepository
from utils.logger import set_global_debug

set_global_debug(True)


# ============================================================
# 流式事件处理器
# ============================================================

class StreamEventHandler:
    """流式事件处理器 — 美化输出，处理累积式 llm_chunk"""

    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self.current_agent: Optional[str] = None
        self.llm_buffer = ""
        self.reasoning_buffer = ""
        self.start_time: Optional[datetime] = None

    def _handle_metadata(self, data: Dict):
        self.start_time = datetime.now()
        print("\n" + "-" * 80)
        print("开始执行")
        print("-" * 80)

    def _handle_stream_event(self, event: Dict):
        event_type = event.get("type")
        agent = event.get("agent", "unknown")
        data = event.get("data")

        # Agent 切换
        if agent != self.current_agent:
            if self.current_agent and (self.llm_buffer or self.reasoning_buffer):
                print()
            self.current_agent = agent
            self.llm_buffer = ""
            self.reasoning_buffer = ""

        if event_type == StreamEventType.AGENT_START.value:
            print(f"\n[{agent}] 开始执行...")

        elif event_type == StreamEventType.LLM_CHUNK.value:
            if data:
                # 累积式 reasoning_content
                reasoning = data.get("reasoning_content")
                if reasoning:
                    if reasoning.startswith(self.reasoning_buffer):
                        new_part = reasoning[len(self.reasoning_buffer):]
                        if new_part:
                            if not self.reasoning_buffer:
                                print(f"\n[{agent}] 思考中...", flush=True)
                            print(f"\033[90m{new_part}\033[0m", end="", flush=True)
                            self.reasoning_buffer = reasoning
                    else:
                        if self.reasoning_buffer:
                            print()
                        print(f"\n[{agent}] 思考中...", flush=True)
                        print(f"\033[90m{reasoning}\033[0m", end="", flush=True)
                        self.reasoning_buffer = reasoning

                # 累积式 content
                content = data.get("content")
                if content:
                    if self.reasoning_buffer and not self.llm_buffer:
                        print(f"\n[{agent}] 回答:", flush=True)

                    if content.startswith(self.llm_buffer):
                        new_part = content[len(self.llm_buffer):]
                        print(new_part, end="", flush=True)
                        self.llm_buffer = content
                    else:
                        if self.llm_buffer:
                            print()
                        print(content, end="", flush=True)
                        self.llm_buffer = content

        elif event_type == StreamEventType.LLM_COMPLETE.value:
            if self.llm_buffer or self.reasoning_buffer:
                print()
            print(f"[{agent}] LLM 输出完成")
            self.llm_buffer = ""
            self.reasoning_buffer = ""

            if self.verbose and data:
                token_usage = data.get("token_usage", {})
                if token_usage:
                    print(f"[{agent}] Token: {token_usage.get('input_tokens', 0)} in / {token_usage.get('output_tokens', 0)} out")

        elif event_type == StreamEventType.TOOL_START.value:
            tool_name = data.get("tool", "unknown") if data else "unknown"
            if self.llm_buffer or self.reasoning_buffer:
                print()
            print(f"[{agent}] 调用工具: {tool_name}...")
            self.llm_buffer = ""
            self.reasoning_buffer = ""

        elif event_type == StreamEventType.TOOL_COMPLETE.value:
            tool_name = data.get("tool", "unknown") if data else "unknown"
            success = data.get("success", False) if data else False
            duration = data.get("duration_ms", 0) if data else 0
            status = "OK" if success else "FAIL"
            print(f"[{agent}] 工具 {tool_name} 完成: {status} ({duration}ms)")

        elif event_type == StreamEventType.PERMISSION_REQUEST.value:
            tool_name = data.get("tool", "unknown") if data else "unknown"
            if self.llm_buffer or self.reasoning_buffer:
                print()
            print(f"\n[{agent}] 需要权限确认")
            if data:
                print(f"[{agent}]    工具: {tool_name}")
                print(f"[{agent}]    权限级别: {data.get('permission_level')}")
            self.llm_buffer = ""
            self.reasoning_buffer = ""

        elif event_type == StreamEventType.AGENT_COMPLETE.value:
            if self.llm_buffer or self.reasoning_buffer:
                print()
            print(f"[{agent}] 执行完成")
            self.llm_buffer = ""
            self.reasoning_buffer = ""

        elif event_type == StreamEventType.ERROR.value:
            if self.llm_buffer or self.reasoning_buffer:
                print()
            error_msg = data.get("error", "unknown") if data else "unknown"
            print(f"\n[{agent}] 错误: {error_msg}")
            self.llm_buffer = ""
            self.reasoning_buffer = ""

    def _handle_complete(self, data: Dict):
        if self.llm_buffer or self.reasoning_buffer:
            print()

        elapsed = (datetime.now() - self.start_time).total_seconds() if self.start_time else 0

        print("\n" + "-" * 80)
        success = data.get("success", False)
        interrupted = data.get("interrupted", False)

        if success and not interrupted:
            print(f"执行成功完成（耗时 {elapsed:.2f}s）")
            if self.verbose and data.get("execution_metrics"):
                metrics = data["execution_metrics"]
                print(f"   总耗时: {metrics.get('total_duration_ms', 0)}ms")
                print(f"   Agent 执行次数: {len(metrics.get('agent_executions', []))}")
                print(f"   工具调用次数: {len(metrics.get('tool_calls', []))}")
                total_in = sum(e.get("token_usage", {}).get("input_tokens", 0)
                               for e in metrics.get("agent_executions", []))
                total_out = sum(e.get("token_usage", {}).get("output_tokens", 0)
                                for e in metrics.get("agent_executions", []))
                print(f"   总 Token: {total_in} in / {total_out} out")
            if not self.verbose and data.get("response"):
                preview = data["response"][:150]
                if len(data["response"]) > 150:
                    preview += "..."
                print(f"   响应: {preview}")
        elif interrupted:
            print(f"执行中断（耗时 {elapsed:.2f}s）")
        else:
            print(f"执行失败: {data.get('error')}")

        print("-" * 80 + "\n")
        self.llm_buffer = ""
        self.reasoning_buffer = ""

    async def process_stream(self, stream_generator) -> Optional[Dict]:
        """处理整个流式过程，返回 complete/error 事件的 data"""
        result_data = None

        async for event in stream_generator:
            event_type = event.get("type")

            if event_type == StreamEventType.METADATA.value:
                self._handle_metadata(event.get("data", {}))
            elif event_type == StreamEventType.COMPLETE.value:
                self._handle_complete(event.get("data", {}))
                result_data = event.get("data", {})
            elif event_type == StreamEventType.ERROR.value:
                self._handle_stream_event(event)
                result_data = event.get("data", {})
            else:
                self._handle_stream_event(event)

        return result_data


# ============================================================
# 测试环境
# ============================================================

class TestEnvironment:
    """
    测试环境管理器

    模拟 API 层的依赖注入模式：
    - db_manager 和 task_manager 是全局共享的
    - 每个 "请求" 通过 request_scope() 获得独立的 session/manager/controller
    """

    def __init__(self):
        self.db_manager: Optional[DatabaseManager] = None
        self.task_manager: Optional[TaskManager] = None
        self._agents: Optional[Dict] = None
        self._registry: Optional[ToolRegistry] = None

    async def setup(self):
        # 1. 内存数据库
        self.db_manager = DatabaseManager("sqlite+aiosqlite:///:memory:")
        await self.db_manager.initialize()

        # 2. 加载 agents
        self._agents = load_all_agents()
        print(f"Loaded agents: {list(self._agents.keys())}")

        # 3. Tool registry
        self._registry = ToolRegistry()
        for tool in [CallSubagentTool(), WebSearchTool(), WebFetchTool()]:
            self._registry.register_tool_to_library(tool)

        # 4. TaskManager
        self.task_manager = TaskManager(max_concurrent=5)

        return self

    @asynccontextmanager
    async def request_scope(self):
        """每个调用产出独立的 session + controller"""
        async with self.db_manager.session() as session:
            artifact_repo = ArtifactRepository(session)
            artifact_manager = ArtifactManager(artifact_repo)

            # 注册 artifact 工具
            registry = ToolRegistry()
            # 复制 library tools
            for name, tool in self._registry.tool_library.items():
                registry.register_tool_to_library(tool)
            for tool in create_artifact_tools(artifact_manager):
                registry.register_tool_to_library(tool)

            conv_repo = ConversationRepository(session)
            conv_manager = ConversationManager(conv_repo)

            controller = ExecutionController(
                agents=self._agents,
                tool_registry=registry,
                task_manager=self.task_manager,
                artifact_manager=artifact_manager,
                conversation_manager=conv_manager,
            )

            yield controller

    async def cleanup(self):
        if self.task_manager:
            await self.task_manager.shutdown()
        if self.db_manager:
            await self.db_manager.close()
        print("Test environment cleaned up")


# ============================================================
# 测试场景
# ============================================================

async def demo_basic():
    """基本问答"""
    print("\n" + "=" * 60)
    print("场景 1: 基本问答")
    print("=" * 60)

    env = await TestEnvironment().setup()
    try:
        handler = StreamEventHandler(verbose=True)

        print("\n用户: Hello! What can you do? (Keep your response brief, 2 sentences max)")
        async with env.request_scope() as controller:
            await handler.process_stream(
                controller.stream_execute(
                    content="Hello! What can you do? (Keep your response brief, 2 sentences max)"
                )
            )
    finally:
        await env.cleanup()


async def demo_multi_turn():
    """多轮对话 — conversation_id 跨 request_scope 保持"""
    print("\n" + "=" * 60)
    print("场景 2: 多轮对话")
    print("=" * 60)

    env = await TestEnvironment().setup()
    try:
        handler = StreamEventHandler(verbose=False)
        conv_id = None

        # 第一轮
        print("\n用户: 什么是量子计算？请简要回答，3句话以内。")
        async with env.request_scope() as controller:
            result1 = await handler.process_stream(
                controller.stream_execute(content="什么是量子计算？请简要回答，3句话以内。")
            )
            conv_id = result1["conversation_id"]

        # 第二轮（同一 conversation_id）
        print("\n用户: 它和经典计算有什么区别？同样简要回答。")
        async with env.request_scope() as controller:
            result2 = await handler.process_stream(
                controller.stream_execute(
                    content="它和经典计算有什么区别？同样简要回答。",
                    conversation_id=conv_id,
                )
            )

        print(f"\n多轮对话完成，conversation_id={conv_id}")
    finally:
        await env.cleanup()


async def demo_artifact():
    """Artifact 工具调用"""
    print("\n" + "=" * 60)
    print("场景 3: Artifact 工具调用")
    print("=" * 60)

    env = await TestEnvironment().setup()
    try:
        handler = StreamEventHandler(verbose=True)
        conv_id = None

        # 第一轮：给出内容
        print("\n用户: Python 的 async/await 是什么？简要解释。")
        async with env.request_scope() as controller:
            result1 = await handler.process_stream(
                controller.stream_execute(content="Python 的 async/await 是什么？简要解释。")
            )
            conv_id = result1["conversation_id"]

        # 第二轮：要求整理到 artifact
        print("\n用户: 帮我整理到 artifact 中")
        async with env.request_scope() as controller:
            result2 = await handler.process_stream(
                controller.stream_execute(
                    content="帮我整理到 artifact 中",
                    conversation_id=conv_id,
                )
            )

        print("\nArtifact 演示完成！")
    finally:
        await env.cleanup()


async def demo_permission():
    """权限确认流 — interrupt + resolve"""
    print("\n" + "=" * 60)
    print("场景 4: 权限确认流")
    print("=" * 60)

    env = await TestEnvironment().setup()
    try:
        handler = StreamEventHandler(verbose=True)

        # 发送会触发 web_fetch 的消息
        print("\n用户: 请抓取 https://example.com 的内容")

        # 由于 interrupt 是 in-memory asyncio.Event，
        # 需要在另一个 task 中 resolve
        permission_resolved = asyncio.Event()

        async def auto_approve():
            """监听 permission_request 事件，自动批准"""
            # 等待 interrupt 被创建
            while True:
                await asyncio.sleep(0.5)
                interrupts = env.task_manager._interrupts
                if interrupts:
                    msg_id = next(iter(interrupts))
                    interrupt = interrupts[msg_id]
                    if not interrupt.event.is_set():
                        print("\n[AUTO-APPROVE] 自动批准权限请求...")
                        await env.task_manager.resolve_interrupt(
                            msg_id, {"approved": True}
                        )
                        permission_resolved.set()
                        return
                if permission_resolved.is_set():
                    return

        approve_task = asyncio.create_task(auto_approve())

        async with env.request_scope() as controller:
            result = await handler.process_stream(
                controller.stream_execute(
                    content="请抓取 https://example.com 的内容"
                )
            )

        approve_task.cancel()

        print("\n权限确认演示完成！")
    finally:
        await env.cleanup()


async def demo_branch():
    """分支对话 — parent_message_id"""
    print("\n" + "=" * 60)
    print("场景 5: 分支对话")
    print("=" * 60)

    env = await TestEnvironment().setup()
    try:
        handler = StreamEventHandler(verbose=False)
        conv_id = None
        msg1_id = None

        # 第一条消息
        print("\n用户: 计算 15 + 28 等于多少")
        async with env.request_scope() as controller:
            result1 = await handler.process_stream(
                controller.stream_execute(content="计算 15 + 28 等于多少")
            )
            conv_id = result1["conversation_id"]
            msg1_id = result1["message_id"]

        # 第二条消息：继续主线
        print("\n用户: 再乘以 2")
        async with env.request_scope() as controller:
            result2 = await handler.process_stream(
                controller.stream_execute(
                    content="再乘以 2",
                    conversation_id=conv_id,
                )
            )

        # 第三条消息：从 msg1 创建分支
        print("\n从第一条消息创建分支...")
        print("用户: 再减去一万")
        async with env.request_scope() as controller:
            result3 = await handler.process_stream(
                controller.stream_execute(
                    content="再减去一万",
                    conversation_id=conv_id,
                    parent_message_id=msg1_id,  # 从 msg1 分支
                )
            )

        print("\n对话树结构:")
        print("   msg1: '15 + 28 = ?'")
        print("   ├── msg2: '再乘以 2'   ← 主线")
        print("   └── msg3: '再减去一万'  ← 分支")

        print("\n分支对话演示完成！")
    finally:
        await env.cleanup()


async def demo_all():
    """依次运行全部场景"""
    for demo in [demo_basic, demo_multi_turn, demo_artifact, demo_permission, demo_branch]:
        await demo()
        print()
        await asyncio.sleep(1)


# ============================================================
# 主程序
# ============================================================

DEMOS = {
    "basic": ("基本问答", demo_basic),
    "multi_turn": ("多轮对话", demo_multi_turn),
    "artifact": ("Artifact 工具调用", demo_artifact),
    "permission": ("权限确认流", demo_permission),
    "branch": ("分支对话", demo_branch),
    "all": ("全部演示", demo_all),
}

MENU_KEYS = list(DEMOS.keys())


async def main():
    import argparse

    parser = argparse.ArgumentParser(description="ArtifactFlow 执行引擎手动测试")
    parser.add_argument(
        "--test",
        type=str,
        default=None,
        choices=list(DEMOS.keys()),
        help="Run specific test (basic, multi_turn, artifact, permission, branch, all)",
    )
    args = parser.parse_args()

    print("\nArtifactFlow 执行引擎手动测试")

    if args.test:
        choice = args.test
    else:
        print("\n选择演示:")
        for i, key in enumerate(MENU_KEYS, 1):
            print(f"  {i}. {DEMOS[key][0]}")

        raw = input(f"\n选择 (1-{len(MENU_KEYS)}): ").strip()

        # Accept either number or name
        if raw.isdigit() and 1 <= int(raw) <= len(MENU_KEYS):
            choice = MENU_KEYS[int(raw) - 1]
        elif raw in DEMOS:
            choice = raw
        else:
            print("无效选择")
            return

    try:
        await DEMOS[choice][1]()
    except KeyboardInterrupt:
        print("\n\n用户中断")
    except Exception as e:
        print(f"\n\n错误: {e}")
        import traceback
        traceback.print_exc()

    print("\n" + "=" * 60)
    print("测试结束")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
