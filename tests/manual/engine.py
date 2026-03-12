"""
测试新执行引擎（Pi-style while loop）

用法：
    python -m tests.manual.engine
"""

import asyncio
from contextlib import asynccontextmanager

from core.controller import ExecutionController
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


async def main():
    # 1. 初始化数据库（内存模式）
    db_manager = DatabaseManager("sqlite+aiosqlite:///:memory:")
    await db_manager.initialize()

    # 2. 加载 agents
    agents = load_all_agents()
    print(f"Loaded agents: {list(agents.keys())}")

    # 3. 创建 tool registry
    registry = ToolRegistry()
    for tool in [CallSubagentTool(), WebSearchTool(), WebFetchTool()]:
        registry.register_tool_to_library(tool)

    # 4. 创建 TaskManager
    task_manager = TaskManager(max_concurrent=5)

    # 5. 使用 DB session 创建 managers
    async with db_manager.session() as session:
        artifact_repo = ArtifactRepository(session)
        artifact_manager = ArtifactManager(artifact_repo)

        # 注册 artifact 工具
        for tool in create_artifact_tools(artifact_manager):
            registry.register_tool_to_library(tool)

        conv_repo = ConversationRepository(session)
        conv_manager = ConversationManager(conv_repo)

        # 6. 创建 controller
        controller = ExecutionController(
            agents=agents,
            tool_registry=registry,
            task_manager=task_manager,
            artifact_manager=artifact_manager,
            conversation_manager=conv_manager,
        )

        # 7. 执行测试消息
        print("\n" + "=" * 60)
        print("Sending test message: 'Hello! What can you do?'")
        print("=" * 60 + "\n")

        async for event in controller.stream_execute(
            content="Hello! What can you do? (Keep your response brief, 2 sentences max)"
        ):
            event_type = event.get("type", "")
            data = event.get("data", {})

            if event_type == "metadata":
                print(f"[METADATA] conv={data.get('conversation_id')}, msg={data.get('message_id')}")
            elif event_type == "agent_start":
                print(f"[AGENT_START] {data.get('agent')}")
            elif event_type == "llm_chunk":
                content = data.get("content", "")
                print(content, end="", flush=True)
            elif event_type == "llm_complete":
                print(f"\n[LLM_COMPLETE] tokens={data.get('token_usage')}")
            elif event_type == "tool_start":
                print(f"[TOOL_START] {data.get('tool')}")
            elif event_type == "tool_complete":
                success = data.get("success")
                print(f"[TOOL_COMPLETE] {data.get('tool')} success={success}")
            elif event_type == "agent_complete":
                print(f"[AGENT_COMPLETE] {data.get('agent')}")
            elif event_type == "complete":
                print(f"\n[COMPLETE] success={data.get('success')}")
                metrics = data.get("execution_metrics", {})
                print(f"  Total duration: {metrics.get('total_duration_ms')}ms")
                for ae in metrics.get("agent_executions", []):
                    print(f"  Agent: {ae['agent_name']} (model={ae['model']}, llm_duration={ae['llm_duration_ms']}ms)")
            elif event_type == "error":
                print(f"\n[ERROR] {data.get('error')}")

    # Cleanup
    await task_manager.shutdown()
    await db_manager.close()

    print("\nTest completed!")


if __name__ == "__main__":
    asyncio.run(main())
