"""
重构后的Core模块使用示例
展示：多轮对话、权限管理、分支对话

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
from contextlib import asynccontextmanager
from core.graph import create_multi_agent_graph
from core.controller import ExecutionController
from core.conversation_manager import ConversationManager
from tools.implementations.artifact_ops import ArtifactManager
from db.database import DatabaseManager
from repositories.artifact_repo import ArtifactRepository
from repositories.conversation_repo import ConversationRepository
from utils.logger import get_logger, set_global_debug

logger = get_logger("ArtifactFlow")
set_global_debug(True)


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

        # 1. 创建内存数据库（全局共享）
        self.db_manager = DatabaseManager("sqlite+aiosqlite:///:memory:")
        await self.db_manager.initialize()

        # 2. 创建共享的 checkpointer（用于 LangGraph 状态持久化）
        # 使用 AsyncSqliteSaver 替代 MemorySaver
        self._checkpointer = await create_async_sqlite_checkpointer("data/test_langgraph.db")

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
                result = await controller.execute(content="...")
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
# 测试场景
# ============================================================

async def demo_multi_turn_conversation():
    """
    演示多轮对话

    每轮对话模拟一个 HTTP 请求，使用独立的 session。
    conversation_id 跨请求保持，用于关联对话历史。
    """
    logger.debug("=" * 60)
    logger.debug("多轮对话演示")
    logger.debug("=" * 60)

    # 初始化测试环境
    env = await TestEnvironment().setup()

    try:
        conv_id = None

        # 第一轮（模拟第一个 HTTP 请求）
        async with env.request_scope() as controller:
            result1 = await controller.execute(
                content="什么是量子计算？"
            )
            conv_id = result1["conversation_id"]
            logger.debug(f"\n轮次1: {result1['response'][:200]}...")

        # 第二轮（模拟第二个 HTTP 请求，使用相同的 conversation_id）
        async with env.request_scope() as controller:
            result2 = await controller.execute(
                content="帮我整理到artifact中，内容浅显易懂一点",
                conversation_id=conv_id
            )
            logger.debug(f"\n轮次2: {result2['response'][:200]}...")

        # 第三轮（模拟第三个 HTTP 请求）
        async with env.request_scope() as controller:
            result3 = await controller.execute(
                content="帮我写一份最新的研究进展报告",
                conversation_id=conv_id
            )
            logger.debug(f"\n轮次3: {result3['response'][:200]}...")

    finally:
        await env.cleanup()


async def demo_permission_flow():
    """
    演示权限确认流程

    第一个请求触发权限中断，第二个请求恢复执行。
    thread_id 跨请求保持，用于恢复中断的执行。
    """
    logger.debug("=" * 60)
    logger.debug("权限确认演示")
    logger.debug("=" * 60)

    # 配置权限
    from tools.base import ToolPermission
    tool_permissions = {
        "web_fetch": ToolPermission.CONFIRM
    }

    # 初始化测试环境
    env = await TestEnvironment().setup(tool_permissions=tool_permissions)

    try:
        thread_id = None
        conv_id = None
        msg_id = None

        # 第一个请求：发起需要爬虫的任务
        async with env.request_scope() as controller:
            result = await controller.execute(
                content="请抓取并分析 https://github.com/langchain-ai/langgraph 的内容"
            )

            if result.get("interrupted"):
                logger.debug(f"需要权限确认:")
                logger.debug(f"   工具: {result['interrupt_data']['tool_name']}")
                logger.debug(f"   Agent: {result['interrupt_data']['agent']}")
                thread_id = result["thread_id"]
                conv_id = result["conversation_id"]
                msg_id = result["message_id"]

        # 第二个请求：恢复执行（用户批准/拒绝）
        if thread_id:
            async with env.request_scope() as controller:
                result = await controller.execute(
                    thread_id=thread_id,
                    conversation_id=conv_id,
                    message_id=msg_id,
                    resume_data={"type": "permission", "approved": False}
                )
                logger.debug(f"\n批准后完成: {result['response'][:200]}...")

    finally:
        await env.cleanup()


async def demo_branch_conversation():
    """
    演示分支对话

    多个请求创建对话分支，conversation_id 和 message_id 跨请求保持。
    """
    logger.debug("=" * 60)
    logger.debug("分支对话演示")
    logger.debug("=" * 60)

    # 初始化测试环境
    env = await TestEnvironment().setup()

    try:
        conv_id = None
        msg1_id = None

        # 第一个请求：主线对话
        async with env.request_scope() as controller:
            result1 = await controller.execute(
                content="计算 15 + 28 等于多少"
            )
            conv_id = result1["conversation_id"]
            msg1_id = result1["message_id"]
            logger.debug(f"\n主线: {result1['response'][:100]}...")

        # 第二个请求：继续主线
        async with env.request_scope() as controller:
            result2 = await controller.execute(
                content="再乘以2",
                conversation_id=conv_id
            )
            logger.debug(f"\n主线续: {result2['response'][:100]}...")

        # 第三个请求：从msg1创建分支
        async with env.request_scope() as controller:
            result3 = await controller.execute(
                content="再减去一万",
                conversation_id=conv_id,
                parent_message_id=msg1_id  # 从msg1分支
            )
            logger.debug(f"\n分支: {result3['response'][:100]}...")

    finally:
        await env.cleanup()


async def main():
    print("\nArtifactFlow Core模块演示")

    # 选择演示
    demos = {
        "1": ("多轮对话", demo_multi_turn_conversation),
        "2": ("权限确认", demo_permission_flow),
        "3": ("分支对话", demo_branch_conversation),
        "4": ("全部演示", None)
    }

    print("\n选择演示:")
    for key, (name, _) in demos.items():
        print(f"{key}. {name}")

    choice = input("\n选择 (1-4): ").strip()

    if choice == "4":
        for key in ["1", "2", "3"]:
            await demos[key][1]()
    elif choice in demos:
        await demos[choice][1]()
    else:
        print("无效选择")


if __name__ == "__main__":
    asyncio.run(main())
