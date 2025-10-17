"""
重构后的Core模块使用示例
展示：多轮对话、权限管理、分支对话
"""

import asyncio
from core.graph import create_multi_agent_graph
from core.controller import ExecutionController
from utils.logger import get_logger
from utils.logger import set_global_debug

logger = get_logger("ArtifactFlow")
set_global_debug(True)

async def demo_multi_turn_conversation():
    """演示多轮对话"""
    logger.debug("="*60)
    logger.debug("📝 多轮对话演示")
    logger.debug("="*60)
    
    # 创建系统
    compiled_graph = create_multi_agent_graph()
    controller = ExecutionController(compiled_graph)
    
    # 第一轮
    result1 = await controller.execute(
        content="什么是量子计算？"
    )
    conv_id = result1["conversation_id"]
    logger.debug(f"\n轮次1: {result1['response'][:200]}...")
    
    # 第二轮（有对话历史）
    result2 = await controller.execute(
        content="帮我整理到artifact中，内容浅显易懂一点",
        conversation_id=conv_id
    )
    logger.debug(f"\n轮次2: {result2['response'][:200]}...")
    
    # 第三轮
    result3 = await controller.execute(
        content="帮我写一份最新的研究进展报告",
        conversation_id=conv_id
    )
    logger.debug(f"\n轮次3: {result3['response'][:200]}...")


async def demo_permission_flow():
    """演示权限确认流程"""
    logger.debug("="*60)
    logger.debug("🔐 权限确认演示")
    logger.debug("="*60)

    # 配置权限
    from tools.base import ToolPermission
    tool_permissions = {
        "web_fetch": ToolPermission.CONFIRM
    }
    
    compiled_graph = create_multi_agent_graph(tool_permissions=tool_permissions)
    controller = ExecutionController(compiled_graph)
    
    # 发起需要爬虫的任务
    result = await controller.execute(
        content="请抓取并分析 https://github.com/langchain-ai/langgraph 的内容"
    )
    
    if result.get("interrupted"):
        logger.debug(f"⚠️ 需要权限确认:")
        logger.debug(f"   工具: {result['interrupt_data']['tool_name']}")
        logger.debug(f"   Agent: {result['interrupt_data']['agent']}")
        
        # 批准
        result = await controller.execute(
            thread_id=result["thread_id"],
            resume_data={"type": "permission", "approved": False}
        )
        
        logger.debug(f"\n✅ 批准后完成: {result['response'][:200]}...")


async def demo_branch_conversation():
    """演示分支对话"""
    logger.debug("="*60)
    logger.debug("🌿 分支对话演示")
    logger.debug("="*60)
    
    compiled_graph = create_multi_agent_graph()
    controller = ExecutionController(compiled_graph)
    
    # 主线对话
    result1 = await controller.execute(
        content="计算 15 + 28 等于多少"
    )
    conv_id = result1["conversation_id"]
    msg1_id = result1["message_id"]
    
    logger.debug(f"\n主线: {result1['response'][:100]}...")
    
    # 继续主线
    result2 = await controller.execute(
        content="再乘以2",
        conversation_id=conv_id
    )
    
    logger.debug(f"\n主线续: {result2['response'][:100]}...")
    
    # 从msg1创建分支
    result3 = await controller.execute(
        content="再减去一万",
        conversation_id=conv_id,
        parent_message_id=msg1_id  # 从msg1分支
    )
    
    logger.debug(f"\n分支: {result3['response'][:100]}...")


async def main():
    print("\n🤖 ArtifactFlow Core模块演示")
    
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