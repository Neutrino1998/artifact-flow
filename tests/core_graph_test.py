"""
重构后的Core模块使用示例
展示：多轮对话、权限管理、分支对话
"""

import asyncio
from core.graph import create_multi_agent_graph
from core.controller import ExecutionController
from utils.logger import set_global_debug

set_global_debug(True)


async def demo_multi_turn_conversation():
    """演示多轮对话"""
    print("\n" + "="*60)
    print("📝 多轮对话演示")
    print("="*60)
    
    # 创建系统
    compiled_graph = create_multi_agent_graph()
    controller = ExecutionController(compiled_graph)
    
    # 第一轮
    result1 = await controller.execute(
        content="什么是量子计算？"
    )
    conv_id = result1["conversation_id"]
    print(f"\n轮次1: {result1['response'][:200]}...")
    
    # 第二轮（有对话历史）
    result2 = await controller.execute(
        content="它有哪些应用？",
        conversation_id=conv_id
    )
    print(f"\n轮次2: {result2['response'][:200]}...")
    
    # 第三轮
    result3 = await controller.execute(
        content="给我搜索一下最新的研究进展",
        conversation_id=conv_id
    )
    print(f"\n轮次3: {result3['response'][:200]}...")


async def demo_permission_flow():
    """演示权限确认流程"""
    print("\n" + "="*60)
    print("🔐 权限确认演示")
    print("="*60)
    
    compiled_graph = create_multi_agent_graph()
    controller = ExecutionController(compiled_graph)
    
    # 修改web_fetch为需要确认
    from tools.base import ToolPermission
    for agent in compiled_graph.agents.values():
        if agent.toolkit:
            for tool in agent.toolkit.list_tools():
                if tool.name == "web_fetch":
                    tool.permission = ToolPermission.CONFIRM
    
    # 发起需要爬虫的任务
    result = await controller.execute(
        content="请抓取并分析 https://github.com/langchain-ai/langgraph 的内容"
    )
    
    if result.get("interrupted"):
        print(f"\n⚠️ 需要权限确认:")
        print(f"   工具: {result['interrupt_data']['tool_name']}")
        print(f"   Agent: {result['interrupt_data']['agent']}")
        
        # 批准
        result = await controller.execute(
            thread_id=result["thread_id"],
            resume_data={"type": "permission", "approved": True}
        )
        
        print(f"\n✅ 批准后完成: {result['response'][:200]}...")


async def demo_branch_conversation():
    """演示分支对话"""
    print("\n" + "="*60)
    print("🌿 分支对话演示")
    print("="*60)
    
    compiled_graph = create_multi_agent_graph()
    controller = ExecutionController(compiled_graph)
    
    # 主线对话
    result1 = await controller.execute(
        content="帮我研究AI在医疗领域的应用"
    )
    conv_id = result1["conversation_id"]
    msg1_id = result1["message_id"]
    
    print(f"\n主线: {result1['response'][:100]}...")
    
    # 继续主线
    result2 = await controller.execute(
        content="重点关注诊断方面",
        conversation_id=conv_id
    )
    
    print(f"\n主线续: {result2['response'][:100]}...")
    
    # 从msg1创建分支
    result3 = await controller.execute(
        content="换个方向，研究AI在手术辅助方面的应用",
        conversation_id=conv_id,
        parent_message_id=msg1_id  # 从msg1分支
    )
    
    print(f"\n分支: {result3['response'][:100]}...")


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