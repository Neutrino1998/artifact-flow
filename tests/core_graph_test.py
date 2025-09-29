"""
ArtifactFlow 系统主入口示例
演示如何使用完整的多Agent系统
"""

import asyncio
from typing import Optional
from core.graph import create_multi_agent_graph
from core.controller import ExecutionController
from utils.logger import get_logger, set_global_debug
from tools.base import ToolPermission

logger = get_logger("Core")


class ArtifactFlowSystem:
    """
    ArtifactFlow系统封装
    提供简单的接口来使用多Agent研究系统
    """
    
    def __init__(self, debug: bool = False, test_permissions: bool = False):
        """
        初始化系统
        
        Args:
            debug: 是否开启调试模式
            test_permissions: 是否启用权限测试模式
        """
        # 设置全局debug
        set_global_debug(debug)
        self.test_permissions = test_permissions
        
        # 创建Graph
        logger.info("Initializing ArtifactFlow system...")
        self.graph_builder = create_multi_agent_graph()
        
        # 如果启用权限测试，修改某些工具的权限级别
        if test_permissions:
            self._setup_permission_testing()
        
        self.compiled_graph = self.graph_builder.compile()
        
        # 创建控制器
        self.controller = ExecutionController(self.compiled_graph)
        
        logger.info("✅ ArtifactFlow system ready")
        if test_permissions:
            logger.info("🔐 Permission testing mode ENABLED")
    
    def _setup_permission_testing(self):
        """
        设置权限测试
        临时修改某些工具的权限级别以测试permission流程
        """
        # 获取所有已注册的agent
        for agent_name, agent in self.graph_builder.agents.items():
            if agent.toolkit:
                tools = agent.toolkit.list_tools()
                for tool in tools:
                    # 修改web_fetch为CONFIRM级别（需要确认）
                    if tool.name == "web_fetch":
                        tool.permission = ToolPermission.CONFIRM
                        logger.info(f"🔐 Changed {tool.name} permission to CONFIRM for testing")
                    
                    # 修改create_artifact为NOTIFY级别（执行后通知）
                    elif tool.name == "create_artifact":
                        tool.permission = ToolPermission.NOTIFY
                        logger.info(f"🔔 Changed {tool.name} permission to NOTIFY for testing")
    
    async def process(
        self,
        message: Optional[str] = None,
        thread_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        parent_message_id: Optional[str] = None,
        resume_data: Optional[dict] = None
    ) -> dict:
        """
        处理消息或恢复执行（使用统一接口）
        
        Args:
            message: 用户输入（新消息时必需）
            thread_id: 线程ID（恢复时必需）
            conversation_id: 对话ID
            parent_message_id: 父消息ID（用于分支）
            resume_data: 恢复数据（恢复时必需）
            
        Returns:
            处理结果字典
        """
        result = await self.controller.execute(
            content=message,
            thread_id=thread_id,
            conversation_id=conversation_id,
            parent_message_id=parent_message_id,
            resume_data=resume_data
        )
        
        # 处理中断情况
        if result.get("interrupted"):
            logger.info(f"⚠️ Execution interrupted: {result['interrupt_type']}")
            
            # 显示更多中断详情
            if result['interrupt_type'] == 'tool_permission':
                interrupt_data = result.get('interrupt_data', {})
                logger.info(f"🔐 Tool '{interrupt_data.get('tool_name')}' requires {interrupt_data.get('permission_level')} permission")
        
        return result
    
    def get_history(self, conversation_id: str) -> list:
        """获取对话历史"""
        return self.controller.get_conversation_history(conversation_id)
    
    def list_conversations(self) -> list:
        """列出所有对话"""
        return self.controller.list_conversations()


async def interactive_demo():
    """
    交互式演示（增强版）
    """
    print("\n" + "="*60)
    print("🤖 ArtifactFlow Interactive Demo")
    print("="*60)
    
    # 询问是否启用权限测试
    test_perms = input("\nEnable permission testing? (y/n): ").strip().lower() == 'y'
    
    print("\nCommands:")
    print("  /help       - Show this help")
    print("  /history    - Show conversation history")
    print("  /list       - List all conversations")
    print("  /branch     - Create a branch from previous message")
    print("  /switch <id>- Switch to another conversation")
    print("  /debug      - Toggle debug mode")
    print("  /perms      - Toggle permission testing")
    print("  /exit       - Exit the demo")
    print("\nType your research request or command:\n")
    
    # 初始化系统
    system = ArtifactFlowSystem(debug=False, test_permissions=test_perms)
    current_conversation_id = None
    last_message_id = None
    last_thread_id = None
    debug_enabled = False
    
    while True:
        try:
            # 获取用户输入
            user_input = input("You: ").strip()
            
            # 处理命令
            if user_input.startswith("/"):
                command_parts = user_input.split()
                command = command_parts[0].lower()
                
                if command == "/exit":
                    print("Goodbye! 👋")
                    break
                
                elif command == "/help":
                    print("\nCommands:")
                    print("  /help       - Show this help")
                    print("  /history    - Show conversation history")
                    print("  /list       - List all conversations")
                    print("  /branch     - Create a branch from previous message")
                    print("  /switch <id>- Switch to another conversation")
                    print("  /debug      - Toggle debug mode")
                    print("  /perms      - Toggle permission testing")
                    print("  /exit       - Exit the demo\n")
                    continue
                
                elif command == "/history":
                    if current_conversation_id:
                        history = system.get_history(current_conversation_id)
                        print(f"\n📜 Conversation History ({current_conversation_id[:8]}...):")
                        for i, msg in enumerate(history):
                            role = "You" if msg["role"] == "user" else "AI"
                            content_preview = msg['content'][:100] + "..." if len(msg['content']) > 100 else msg['content']
                            print(f"{i+1}. {role}: {content_preview}")
                    else:
                        print("No conversation started yet.")
                    continue
                
                elif command == "/list":
                    conversations = system.list_conversations()
                    if conversations:
                        print("\n📚 All Conversations:")
                        for conv in conversations:
                            print(f"  - {conv['conversation_id'][:8]}... ({conv['message_count']} messages, {conv['branch_count']} branches)")
                    else:
                        print("No conversations yet.")
                    continue
                
                elif command == "/branch":
                    if last_message_id:
                        print(f"🌿 Creating branch from message {last_message_id[:8]}...")
                        print("Enter your new message for this branch:")
                        branch_message = input("Branch: ").strip()
                        
                        result = await system.process(
                            message=branch_message,
                            conversation_id=current_conversation_id,
                            parent_message_id=last_message_id
                        )
                        
                        # 更新状态
                        last_message_id = result.get("message_id")
                        last_thread_id = result.get("thread_id")
                        
                        # 显示响应
                        if result.get("response"):
                            print(f"\nAI: {result['response'][:300]}...")
                    else:
                        print("No previous message to branch from.")
                    continue
                
                elif command == "/switch":
                    if len(command_parts) > 1:
                        target_conv = command_parts[1]
                        # 这里可以添加实际的切换逻辑
                        current_conversation_id = target_conv
                        print(f"Switched to conversation {target_conv[:8]}...")
                    else:
                        print("Usage: /switch <conversation_id>")
                    continue
                
                elif command == "/debug":
                    debug_enabled = not debug_enabled
                    set_global_debug(debug_enabled)
                    print(f"Debug mode: {'ON 🐛' if debug_enabled else 'OFF'}")
                    continue
                
                elif command == "/perms":
                    # 动态切换权限测试（需要重新初始化系统）
                    test_perms = not test_perms
                    print(f"Permission testing: {'ON 🔐' if test_perms else 'OFF'}")
                    print("Reinitializing system...")
                    system = ArtifactFlowSystem(debug=debug_enabled, test_permissions=test_perms)
                    continue
                
                else:
                    print(f"Unknown command: {command}")
                    continue
            
            # 处理消息
            print("\n⏳ Processing...")
            
            result = await system.process(
                message=user_input,
                conversation_id=current_conversation_id
            )
            
            # 更新状态
            current_conversation_id = result.get("conversation_id")
            last_message_id = result.get("message_id")
            last_thread_id = result.get("thread_id")
            
            # 处理中断
            while result.get("interrupted"):
                interrupt_data = result.get("interrupt_data", {})
                interrupt_type = result.get("interrupt_type")
                
                if interrupt_type == "tool_permission":
                    print(f"\n🔐 Permission Required:")
                    print(f"  Agent: {interrupt_data.get('agent')}")
                    print(f"  Tool: {interrupt_data.get('tool_name')}")
                    print(f"  Permission Level: {interrupt_data.get('permission_level')}")
                    print(f"  Message: {interrupt_data.get('message')}")
                    
                    # 显示工具参数（如果有）
                    params = interrupt_data.get('params', {})
                    if params:
                        print(f"  Parameters:")
                        for key, value in params.items():
                            print(f"    - {key}: {str(value)[:50]}...")
                    
                    approval = input("\n✅ Approve? (y/n): ").strip().lower() == 'y'
                    
                    print("\n⏳ Resuming...")
                    result = await system.process(
                        thread_id=last_thread_id,
                        resume_data={
                            "type": "permission",
                            "approved": approval,
                            "reason": input("Reason (optional): ").strip() if not approval else None
                        }
                    )
                else:
                    # 其他类型的中断
                    print(f"\n⚠️ Interrupted: {interrupt_type}")
                    print(f"Details: {interrupt_data}")
                    break
            
            # 显示最终响应
            if result.get("response"):
                response = result['response']
                # 智能截断，保留完整的句子
                if len(response) > 500:
                    cutoff = response[:500].rfind('. ')
                    if cutoff > 0:
                        print(f"\nAI: {response[:cutoff+1]}...")
                    else:
                        print(f"\nAI: {response[:500]}...")
                else:
                    print(f"\nAI: {response}")
            
            print()  # 空行分隔
            
        except KeyboardInterrupt:
            print("\n⚠️ Interrupted. Use /exit to quit.")
        except Exception as e:
            print(f"\n❌ Error: {e}")
            if debug_enabled:
                import traceback
                traceback.print_exc()


async def permission_test_demo():
    """
    专门的权限测试演示
    """
    print("\n" + "="*60)
    print("🔐 Permission Testing Demo")
    print("="*60)
    print("\nThis demo will test the permission system by:")
    print("1. Setting web_fetch to require confirmation")
    print("2. Running a task that needs web crawling")
    print("3. Demonstrating approval/denial flow")
    
    # 初始化系统（启用权限测试）
    system = ArtifactFlowSystem(debug=False, test_permissions=True)
    
    # 测试任务 - 故意选择需要爬虫的任务
    test_task = "Please fetch and analyze the content from https://github.com/langchain-ai/langgraph"
    
    print(f"\n📝 Task: {test_task}")
    print("-" * 40)
    
    # 第一次执行
    result = await system.process(message=test_task)
    
    # 应该会被中断
    if result.get("interrupted"):
        print("\n✅ Permission system working! Execution interrupted as expected.")
        
        # 测试拒绝
        print("\n🔴 Testing DENIAL...")
        result = await system.process(
            thread_id=result["thread_id"],
            resume_data={
                "type": "permission",
                "approved": False,
                "reason": "Testing denial flow"
            }
        )
        
        if result.get("response"):
            print(f"Response after denial: {result['response'][:200]}...")
        
        # 新任务，测试批准
        print("\n🟢 Testing APPROVAL with new task...")
        result = await system.process(message=test_task)
        
        if result.get("interrupted"):
            result = await system.process(
                thread_id=result["thread_id"],
                resume_data={
                    "type": "permission",
                    "approved": True
                }
            )
            
            if result.get("response"):
                print(f"Response after approval: {result['response'][:200]}...")
    else:
        print("❌ Permission system not triggered. Check configuration.")
    
    print("\n" + "="*60)
    print("✅ Permission test completed")
    print("="*60)


async def main():
    """
    主函数 - 选择运行模式
    """
    print("\n🤖 Welcome to ArtifactFlow!")
    print("\nSelect mode:")
    print("1. Interactive Demo")
    print("2. Permission Testing Demo")
    print("3. Batch Processing Demo")
    print("4. Exit")
    
    choice = input("\nYour choice (1-4): ").strip()
    
    if choice == "1":
        await interactive_demo()
    elif choice == "2":
        await permission_test_demo()
    elif choice == "3":
        # 简单的批处理演示
        system = ArtifactFlowSystem(debug=False)
        tasks = [
            "What is quantum computing?",
            "Research AI safety in 2024"
        ]
        for task in tasks:
            print(f"\n📝 Processing: {task}")
            result = await system.process(message=task)
            if result.get("response"):
                print(f"✅ Done: {result['response'][:100]}...")
    else:
        print("Goodbye! 👋")


if __name__ == "__main__":
    asyncio.run(main())