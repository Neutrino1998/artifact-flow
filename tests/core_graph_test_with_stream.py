"""
Core模块流式输出测试
展示：多轮对话（流式）、权限管理（流式）、分支对话（流式）

更新（v2.0）：
- 支持 ArtifactManager 持久化
- 支持 ConversationManager 持久化
- 使用内存数据库进行测试

更新（v2.1）：
- 事务管理模拟 API 层的依赖注入模式
- 每个 session 上下文代表一个 HTTP 请求
- session 结束时自动 commit
"""

import asyncio
from typing import Dict, Any, Optional
from datetime import datetime
from contextlib import asynccontextmanager

from core.graph import create_multi_agent_graph
from core.controller import ExecutionController
from core.events import StreamEventType
from core.conversation_manager import ConversationManager
from tools.implementations.artifact_ops import ArtifactManager
from db.database import DatabaseManager
from repositories.artifact_repo import ArtifactRepository
from repositories.conversation_repo import ConversationRepository
from utils.logger import get_logger, set_global_debug

logger = get_logger("ArtifactFlow")
set_global_debug(False)


# ============================================================
# 测试环境初始化
# ============================================================

class TestEnvironment:
    """
    测试环境管理器

    模拟 API 层的依赖注入模式：
    - db_manager 和 checkpointer 是全局共享的
    - 每个 "请求" 通过 request_scope() 获得独立的 session/manager/controller
    """

    def __init__(self):
        self.db_manager: DatabaseManager = None
        self._tool_permissions = None
        self._checkpointer = None  # LangGraph 状态持久化（跨请求共享）

    async def setup(self, tool_permissions=None):
        """
        初始化测试环境（全局资源）

        Args:
            tool_permissions: 工具权限配置
        """
        from core.graph import create_async_sqlite_checkpointer

        self._tool_permissions = tool_permissions

        # 创建内存数据库（全局共享）
        self.db_manager = DatabaseManager("sqlite+aiosqlite:///:memory:")
        await self.db_manager.initialize()

        # 创建共享的 checkpointer（用于 LangGraph 状态持久化）
        # 使用 AsyncSqliteSaver 替代 MemorySaver
        self._checkpointer = await create_async_sqlite_checkpointer("data/test_stream_langgraph.db")

        logger.info("Test environment initialized (using AsyncSqliteSaver)")
        return self

    @asynccontextmanager
    async def request_scope(self):
        """
        模拟 API 请求的 session 作用域

        在此上下文中：
        - 创建独立的数据库 session
        - 创建绑定到该 session 的 repository 和 manager
        - 创建绑定到这些 manager 的 controller
        - 上下文结束时自动 commit（成功）或 rollback（失败）

        用法：
            async with env.request_scope() as controller:
                async for event in controller.stream_execute(content="..."):
                    ...
        """
        async with self.db_manager.session() as session:
            # 创建 repositories（绑定到当前 session）
            artifact_repo = ArtifactRepository(session)
            conv_repo = ConversationRepository(session)

            # 创建 managers（绑定到 repositories）
            artifact_manager = ArtifactManager(artifact_repo)
            conversation_manager = ConversationManager(conv_repo)

            # 创建 graph（每次创建新的，因为它持有 artifact_manager 引用）
            # 但 checkpointer 是共享的，以支持跨请求的 interrupt/resume
            compiled_graph = await create_multi_agent_graph(
                tool_permissions=self._tool_permissions,
                artifact_manager=artifact_manager,
                checkpointer=self._checkpointer
            )

            # 创建 controller
            controller = ExecutionController(
                compiled_graph,
                artifact_manager=artifact_manager,
                conversation_manager=conversation_manager
            )

            yield controller
            # session 上下文结束时自动 commit

    async def cleanup(self):
        """清理测试环境"""
        # 关闭 checkpointer 的 aiosqlite 连接
        if self._checkpointer and hasattr(self._checkpointer, 'conn'):
            await self._checkpointer.conn.close()

        if self.db_manager:
            await self.db_manager.close()
        logger.info("Test environment cleaned up")


# ============================================================
# 流式事件处理器
# ============================================================

class StreamEventHandler:
    """流式事件处理器 - 美化输出"""

    def __init__(self, verbose: bool = True):
        """
        初始化事件处理器

        Args:
            verbose: 是否显示详细信息（Token使用、工具参数等）
        """
        self.verbose = verbose
        self.current_agent = None
        self.llm_buffer = ""
        self.reasoning_buffer = ""
        self.start_time = None

    def handle_metadata(self, data: Dict):
        """处理元数据事件"""
        self.start_time = datetime.now()
        print("\n" + "-" * 80)
        print(f"开始执行")
        if data.get("resuming"):
            print(f"状态: 从中断恢复")
        print("-" * 80)

    def handle_stream_event(self, event: Dict):
        """处理流式内容事件（统一事件格式）"""
        event_type = event.get("type")
        agent = event.get("agent", "unknown")
        event_data = event.get("data")

        # Agent 切换
        if agent != self.current_agent:
            if self.current_agent and (self.llm_buffer or self.reasoning_buffer):
                print()  # 换行
            self.current_agent = agent
            self.llm_buffer = ""
            self.reasoning_buffer = ""

        if event_type == StreamEventType.AGENT_START.value:
            print(f"\n[{agent}] 开始执行...")

        elif event_type == StreamEventType.LLM_CHUNK.value:
            if event_data:
                # 处理 reasoning_content（思考内容）
                reasoning = event_data.get("reasoning_content")
                if reasoning:
                    if reasoning.startswith(self.reasoning_buffer):
                        new_reasoning = reasoning[len(self.reasoning_buffer):]
                        if new_reasoning:
                            if not self.reasoning_buffer:
                                print(f"\n[{agent}] 思考中...", flush=True)
                            print(f"\033[90m{new_reasoning}\033[0m", end="", flush=True)
                            self.reasoning_buffer = reasoning
                    else:
                        if self.reasoning_buffer:
                            print()
                        print(f"\n[{agent}] 思考中...", flush=True)
                        print(f"\033[90m{reasoning}\033[0m", end="", flush=True)
                        self.reasoning_buffer = reasoning

                # 处理 content（正常输出）
                content = event_data.get("content")
                if content:
                    if self.reasoning_buffer and not self.llm_buffer:
                        print(f"\n[{agent}] 回答:", flush=True)

                    if content.startswith(self.llm_buffer):
                        new_content = content[len(self.llm_buffer):]
                        print(new_content, end="", flush=True)
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

            if self.verbose and event_data:
                token_usage = event_data.get("token_usage", {})
                if token_usage:
                    input_tokens = token_usage.get("input_tokens", 0)
                    output_tokens = token_usage.get("output_tokens", 0)
                    print(f"[{agent}] Token: {input_tokens} in / {output_tokens} out")

        elif event_type == StreamEventType.TOOL_START.value:
            tool_name = event.get("tool", "unknown")
            if self.llm_buffer or self.reasoning_buffer:
                print()
            print(f"[{agent}] 调用工具: {tool_name}...")
            self.llm_buffer = ""
            self.reasoning_buffer = ""

        elif event_type == StreamEventType.TOOL_COMPLETE.value:
            tool_name = event.get("tool", "unknown")
            success = event_data.get("success", False) if event_data else False
            duration = event_data.get("duration_ms", 0) if event_data else 0
            status = "OK" if success else "FAIL"
            print(f"[{agent}] 工具 {tool_name} 完成: {status} ({duration}ms)")

        elif event_type == StreamEventType.PERMISSION_REQUEST.value:
            tool_name = event.get("tool", "unknown")
            if self.llm_buffer or self.reasoning_buffer:
                print()
            print(f"\n[{agent}] 需要权限确认")
            if event_data:
                print(f"[{agent}]    工具: {tool_name}")
                print(f"[{agent}]    权限级别: {event_data.get('permission_level')}")
            self.llm_buffer = ""
            self.reasoning_buffer = ""

        elif event_type == StreamEventType.AGENT_COMPLETE.value:
            if self.llm_buffer or self.reasoning_buffer:
                print()
            routing = event_data.get("routing") if event_data else None
            if routing:
                routing_type = routing.get("type")
                if routing_type == "tool_call":
                    print(f"[{agent}] 请求工具: {routing.get('tool_name')}")
                elif routing_type == "subagent":
                    print(f"[{agent}] 路由到: {routing.get('target')}")
            else:
                print(f"[{agent}] 执行完成")
            self.llm_buffer = ""
            self.reasoning_buffer = ""

        elif event_type == StreamEventType.ERROR.value:
            if self.llm_buffer or self.reasoning_buffer:
                print()
            print(f"\n[{agent}] 执行错误")
            if event_data:
                print(f"[{agent}]    错误: {event_data.get('content') or event_data.get('error')}")
            self.llm_buffer = ""
            self.reasoning_buffer = ""

    def handle_complete(self, data: Dict):
        """处理完成事件"""
        if self.llm_buffer or self.reasoning_buffer:
            print()

        elapsed = (datetime.now() - self.start_time).total_seconds() if self.start_time else 0

        print("\n" + "-" * 80)
        if data["success"]:
            if data.get("interrupted"):
                print(f"执行中断")
                print(f"   中断类型: {data['interrupt_type']}")
                print(f"   耗时: {elapsed:.2f}s")
            else:
                print(f"执行成功完成")
                print(f"   耗时: {elapsed:.2f}s")

                # 显示 execution_metrics
                if self.verbose and data.get("execution_metrics"):
                    metrics = data["execution_metrics"]
                    print(f"   总耗时: {metrics.get('total_duration_ms', 0)}ms")
                    print(f"   Agent 执行次数: {len(metrics.get('agent_executions', []))}")
                    print(f"   工具调用次数: {len(metrics.get('tool_calls', []))}")

                    # 汇总 token 使用
                    total_input = sum(e.get("token_usage", {}).get("input_tokens", 0) for e in metrics.get("agent_executions", []))
                    total_output = sum(e.get("token_usage", {}).get("output_tokens", 0) for e in metrics.get("agent_executions", []))
                    print(f"   总 Token: {total_input} in / {total_output} out")

                if not self.verbose and data.get("response"):
                    response = data["response"]
                    preview = response[:150] + "..." if len(response) > 150 else response
                    print(f"   响应: {preview}")
        else:
            print(f"执行失败")
            print(f"   错误: {data.get('error')}")
            print(f"   耗时: {elapsed:.2f}s")
        print("-" * 80 + "\n")

        self.llm_buffer = ""
        self.reasoning_buffer = ""

    async def process_stream(self, stream_generator):
        """处理整个流式过程"""
        result_data = None

        async for event in stream_generator:
            event_type = event.get("type")

            # 根据事件类型分发处理
            if event_type == StreamEventType.METADATA.value:
                self.handle_metadata(event.get("data", {}))
            elif event_type == StreamEventType.COMPLETE.value:
                self.handle_complete(event.get("data", {}))
                result_data = event.get("data", {})
            elif event_type == StreamEventType.ERROR.value:
                self.handle_stream_event(event)
                result_data = event.get("data", {})
            else:
                # 其他事件（agent/tool 相关）统一处理
                self.handle_stream_event(event)

        return result_data


# ============================================================
# 测试场景
# ============================================================

async def demo_multi_turn_conversation():
    """
    演示多轮对话（流式）

    每轮对话模拟一个 HTTP 请求，使用独立的 session。
    conversation_id 跨请求保持，用于关联对话历史。
    """
    logger.debug("=" * 60)
    logger.debug("多轮对话演示（流式）")
    logger.debug("=" * 60)

    # 初始化测试环境
    env = await TestEnvironment().setup()

    try:
        handler = StreamEventHandler(verbose=True)
        conv_id = None

        # 第一轮（模拟第一个 HTTP 请求）
        print("\n" + "用户: 什么是量子计算？")
        async with env.request_scope() as controller:
            result1 = await handler.process_stream(
                controller.stream_execute(content="什么是量子计算？")
            )
            conv_id = result1["conversation_id"]

        await asyncio.sleep(1)

        # 第二轮（模拟第二个 HTTP 请求）
        print("\n" + "用户: 帮我整理到artifact中，内容浅显易懂一点")
        async with env.request_scope() as controller:
            result2 = await handler.process_stream(
                controller.stream_execute(
                    content="帮我整理到artifact中，内容浅显易懂一点",
                    conversation_id=conv_id
                )
            )

        await asyncio.sleep(1)

        # 第三轮（模拟第三个 HTTP 请求）
        print("\n" + "用户: 帮我写一份最新的研究进展报告")
        async with env.request_scope() as controller:
            result3 = await handler.process_stream(
                controller.stream_execute(
                    content="帮我写一份最新的研究进展报告",
                    conversation_id=conv_id
                )
            )

        print("\n多轮对话演示完成！")

    finally:
        await env.cleanup()


async def demo_permission_flow():
    """
    演示权限确认流程（流式）

    第一个请求触发权限中断，后续请求恢复执行。
    thread_id 跨请求保持，用于恢复中断的执行。
    """
    logger.debug("=" * 60)
    logger.debug("权限确认演示（流式）")
    logger.debug("=" * 60)

    # 配置权限
    from tools.base import ToolPermission
    tool_permissions = {
        "web_fetch": ToolPermission.CONFIRM
    }

    # 初始化测试环境
    env = await TestEnvironment().setup(tool_permissions=tool_permissions)

    try:
        handler = StreamEventHandler(verbose=True)
        thread_id = None
        conv_id = None
        msg_id = None
        result = None

        # 第一个请求：发起需要爬虫的任务
        print("\n" + "用户: 请抓取并分析 https://github.com/langchain-ai/langgraph 的内容")
        async with env.request_scope() as controller:
            result = await handler.process_stream(
                controller.stream_execute(
                    content="请抓取并分析 https://github.com/langchain-ai/langgraph 的内容"
                )
            )
            if result.get("interrupted"):
                thread_id = result["thread_id"]
                conv_id = result["conversation_id"]
                msg_id = result["message_id"]

        # 循环处理多次中断
        max_retries = 3
        retry_count = 0

        while result.get("interrupted") and retry_count < max_retries:
            retry_count += 1
            print(f"\n系统请求权限确认... (第 {retry_count} 次)")
            print(f"   工具: {result['interrupt_data']['tool_name']}")
            print(f"   参数: {result['interrupt_data']['params']}")

            print("\n用户思考中...")
            await asyncio.sleep(2)

            approved = False

            if approved:
                print("\n用户批准，继续执行...")
            else:
                print("\n用户拒绝，尝试其他方式...")

            # 后续请求：恢复执行
            async with env.request_scope() as controller:
                result = await handler.process_stream(
                    controller.stream_execute(
                        thread_id=thread_id,
                        conversation_id=conv_id,
                        message_id=msg_id,
                        resume_data={"type": "permission", "approved": approved}
                    )
                )
                if result.get("interrupted"):
                    thread_id = result["thread_id"]
                    conv_id = result["conversation_id"]
                    msg_id = result["message_id"]

        if retry_count >= max_retries:
            print(f"\n达到最大重试次数 ({max_retries})")

        print("\n权限确认演示完成！")

    finally:
        await env.cleanup()


async def demo_branch_conversation():
    """
    演示分支对话（流式）

    多个请求创建对话分支，conversation_id 和 message_id 跨请求保持。
    """
    logger.debug("=" * 60)
    logger.debug("分支对话演示（流式）")
    logger.debug("=" * 60)

    # 初始化测试环境
    env = await TestEnvironment().setup()

    try:
        handler = StreamEventHandler(verbose=False)
        conv_id = None
        msg1_id = None

        # 第一个请求：主线对话
        print("\n" + "用户: 计算 15 + 28 等于多少")
        async with env.request_scope() as controller:
            result1 = await handler.process_stream(
                controller.stream_execute(content="计算 15 + 28 等于多少")
            )
            conv_id = result1["conversation_id"]
            msg1_id = result1["message_id"]

        await asyncio.sleep(1)

        # 第二个请求：继续主线
        print("\n" + "用户: 再乘以2")
        async with env.request_scope() as controller:
            result2 = await handler.process_stream(
                controller.stream_execute(
                    content="再乘以2",
                    conversation_id=conv_id
                )
            )

        await asyncio.sleep(1)

        # 第三个请求：从msg1创建分支
        print("\n" + "从第一条消息创建分支...")
        print("用户: 再减去一万")
        async with env.request_scope() as controller:
            result3 = await handler.process_stream(
                controller.stream_execute(
                    content="再减去一万",
                    conversation_id=conv_id,
                    parent_message_id=msg1_id
                )
            )

        print("对话树结构:")
        print("   msg1: '15 + 28 = ?'")
        print("   +-- msg2: '再乘以2'  <- 主线")
        print("   +-- msg3: '再减去一万' <- 分支")

        print("\n分支对话演示完成！")

    finally:
        await env.cleanup()


async def demo_compare_batch_vs_stream():
    """
    对比批量模式 vs 流式模式

    两个独立的请求分别使用批量和流式模式。
    """
    logger.debug("=" * 60)
    logger.debug("批量 vs 流式对比")
    logger.debug("=" * 60)

    # 初始化测试环境
    env = await TestEnvironment().setup()

    try:
        question = "简单介绍一下 Python"

        # 批量模式（第一个请求）
        print("\n" + "=" * 80)
        print("批量模式")
        print("=" * 80)
        print(f"\n用户: {question}")
        print("\n等待中...")

        start_time = datetime.now()
        async with env.request_scope() as controller:
            result = await controller.execute(content=question)
        elapsed = (datetime.now() - start_time).total_seconds()

        if result["success"]:
            print(f"\n收到完整响应 (耗时 {elapsed:.2f}s):")
            print(f"\n{result['response'][:200]}...")

        await asyncio.sleep(2)

        # 流式模式（第二个请求）
        print("\n" + "=" * 80)
        print("流式模式")
        print("=" * 80)
        print(f"\n用户: {question}")
        print("\n实时输出:\n")

        handler = StreamEventHandler(verbose=False)
        start_time = datetime.now()
        async with env.request_scope() as controller:
            result = await handler.process_stream(
                controller.stream_execute(content=question)
            )
        elapsed = (datetime.now() - start_time).total_seconds()

        print("对比演示完成！")

    finally:
        await env.cleanup()


# ============================================================
# 主程序
# ============================================================

async def main():
    print("\n" + "ArtifactFlow 流式输出测试")

    # 选择演示
    demos = {
        "1": ("多轮对话（流式）", demo_multi_turn_conversation),
        "2": ("权限确认（流式）", demo_permission_flow),
        "3": ("分支对话（流式）", demo_branch_conversation),
        "4": ("批量 vs 流式对比", demo_compare_batch_vs_stream),
        "5": ("全部演示", None)
    }

    print("\n" + "选择演示:")
    print()
    for key, (name, _) in demos.items():
        print(f"  {key}. {name}")

    choice = input("\n选择 (1-5): ").strip()

    try:
        if choice == "5":
            for key in ["1", "2", "3", "4"]:
                await demos[key][1]()
                print("\n" + "-" * 80)
                await asyncio.sleep(2)
        elif choice in demos:
            await demos[choice][1]()
        else:
            print("无效选择")
    except KeyboardInterrupt:
        print("\n\n用户中断")
    except Exception as e:
        print(f"\n\n错误: {e}")
        import traceback
        traceback.print_exc()

    print("\n" + "=" * 80)
    print("测试结束")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
