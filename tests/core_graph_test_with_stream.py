"""
Core模块流式输出测试
展示：多轮对话（流式）、权限管理（流式）、分支对话（流式）
"""

import asyncio
from typing import Dict, Any
from datetime import datetime

# 注意：这里假设你已经将改造后的文件放到了正确位置
# 如果使用新文件名，请调整导入路径
from core.graph import create_multi_agent_graph
from core.controller import ExecutionController, ControllerEventType
from utils.logger import get_logger, set_global_debug

logger = get_logger("ArtifactFlow")
set_global_debug(False)


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
        self.reasoning_buffer = ""  # 🆕 用于缓冲 reasoning_content
        self.start_time = None
    
    def handle_metadata(self, data: Dict):
        """处理元数据事件"""
        self.start_time = datetime.now()
        print("\n" + "-"*80)
        print(f"🚀 开始执行")
        if data.get("resuming"):
            print(f"状态: 🔄 从中断恢复")
        print("-"*80)
    
    def handle_stream(self, data: Dict):
        """处理流式内容事件"""
        stream_type = data["type"]
        agent = data["agent"]
        event_data = data.get("data")
        
        # Agent 切换
        if agent != self.current_agent:
            if self.current_agent and (self.llm_buffer or self.reasoning_buffer):
                print()  # 换行
            self.current_agent = agent
            self.llm_buffer = ""
            self.reasoning_buffer = ""
        
        if stream_type == "start":
            print(f"\n[{agent}] ⏰ 开始执行...")
        
        elif stream_type == "llm_chunk":
            if event_data:
                # 🆕 处理 reasoning_content（思考内容）
                reasoning = event_data.get("reasoning_content")
                if reasoning:
                    # 只显示新增的思考内容
                    if reasoning.startswith(self.reasoning_buffer):
                        new_reasoning = reasoning[len(self.reasoning_buffer):]
                        if new_reasoning:
                            # 第一次显示思考时，添加标记
                            if not self.reasoning_buffer:
                                print(f"\n[{agent}] 💭 思考中...", flush=True)
                            print(f"\033[90m{new_reasoning}\033[0m", end="", flush=True)  # 灰色显示
                            self.reasoning_buffer = reasoning
                    else:
                        # 思考内容重置（新一轮）
                        if self.reasoning_buffer:
                            print()  # 换行
                        print(f"\n[{agent}] 💭 思考中...", flush=True)
                        print(f"\033[90m{reasoning}\033[0m", end="", flush=True)
                        self.reasoning_buffer = reasoning
                
                # 处理 content（正常输出）
                content = event_data.get("content")
                if content:
                    # 如果有思考内容，先换行再显示正常内容
                    if self.reasoning_buffer and not self.llm_buffer:
                        print(f"\n[{agent}] 💬 回答:", flush=True)
                    
                    # 只显示新增内容
                    if content.startswith(self.llm_buffer):
                        new_content = content[len(self.llm_buffer):]
                        print(new_content, end="", flush=True)
                        self.llm_buffer = content
                    else:
                        # 内容重置（新一轮）
                        if self.llm_buffer:
                            print()  # 换行
                        print(content, end="", flush=True)
                        self.llm_buffer = content

        elif stream_type == "llm_complete":
            if self.llm_buffer or self.reasoning_buffer:
                print()  # 换行
            print(f"[{agent}] ✅ LLM 输出完成")
            self.llm_buffer = ""
            self.reasoning_buffer = ""
            
            if self.verbose and event_data:
                token_usage = event_data.get("token_usage", {})
                if token_usage:
                    input_tokens = token_usage.get("input_tokens", 0)
                    output_tokens = token_usage.get("output_tokens", 0)
                    print(f"[{agent}] 📊 Token: {input_tokens} in / {output_tokens} out")
        
        elif stream_type == "tool_start":
            if self.llm_buffer or self.reasoning_buffer:
                print()  # 换行
            print(f"[{agent}] 🔧 调用工具...")
            self.llm_buffer = ""
            self.reasoning_buffer = ""
        
        elif stream_type == "tool_result":
            print(f"[{agent}] ✅ 工具调用完成")
            
            if self.verbose and event_data:
                tool_calls = event_data.get("tool_calls", [])
                if tool_calls:
                    last_call = tool_calls[-1]
                    print(f"[{agent}]    工具: {last_call['tool']}")
                    success = last_call['result']['success']
                    status = "✓" if success else "✗"
                    print(f"[{agent}]    结果: {status}")
        
        elif stream_type == "permission_required":
            if self.llm_buffer or self.reasoning_buffer:
                print()
            print(f"\n[{agent}] ⚠️ 需要权限确认")
            if event_data and event_data.get("routing"):
                routing = event_data["routing"]
                print(f"[{agent}]    工具: {routing['tool_name']}")
                print(f"[{agent}]    权限级别: {routing['permission_level']}")
            self.llm_buffer = ""
            self.reasoning_buffer = ""
        
        elif stream_type == "complete":
            if self.llm_buffer or self.reasoning_buffer:
                print()
            print(f"[{agent}] 🎉 执行完成")
            self.llm_buffer = ""
            self.reasoning_buffer = ""
        
        elif stream_type == "error":
            if self.llm_buffer or self.reasoning_buffer:
                print()
            print(f"\n[{agent}] ❌ 执行错误")
            if event_data:
                print(f"[{agent}]    错误: {event_data.get('content')}")
            self.llm_buffer = ""
            self.reasoning_buffer = ""
    
    def handle_complete(self, data: Dict):
        """处理完成事件"""
        if self.llm_buffer or self.reasoning_buffer:
            print()  # 确保换行
        
        elapsed = (datetime.now() - self.start_time).total_seconds() if self.start_time else 0
        
        print("\n" + "-"*80)
        if data["success"]:
            if data.get("interrupted"):
                print(f"⚠️  执行中断")
                print(f"   中断类型: {data['interrupt_type']}")
                print(f"   耗时: {elapsed:.2f}s")
            else:
                print(f"✅ 执行成功完成")
                print(f"   耗时: {elapsed:.2f}s")
                if not self.verbose and data.get("response"):
                    response = data["response"]
                    preview = response[:150] + "..." if len(response) > 150 else response
                    print(f"   响应: {preview}")
        else:
            print(f"❌ 执行失败")
            print(f"   错误: {data.get('error')}")
            print(f"   耗时: {elapsed:.2f}s")
        print("-"*80 + "\n")
        
        self.llm_buffer = ""
        self.reasoning_buffer = ""
    
    async def process_stream(self, stream_generator):
        """处理整个流式过程"""
        result_data = None
        
        async for event in stream_generator:
            event_type = event["event_type"]
            data = event["data"]
            
            if event_type == ControllerEventType.METADATA:
                self.handle_metadata(data)
            elif event_type == ControllerEventType.STREAM:
                self.handle_stream(data)
            elif event_type == ControllerEventType.COMPLETE:
                self.handle_complete(data)
                result_data = data
        
        return result_data


# ============================================================
# 测试场景
# ============================================================

async def demo_multi_turn_conversation():
    """演示多轮对话（流式）"""
    logger.debug("="*60)
    logger.debug("📝 多轮对话演示（流式）")
    logger.debug("="*60)
    
    # 创建系统
    compiled_graph = create_multi_agent_graph()
    controller = ExecutionController(compiled_graph)
    handler = StreamEventHandler(verbose=True)
    
    # 第一轮
    print("\n" + "🗣️  用户: 什么是量子计算？")
    result1 = await handler.process_stream(
        controller.stream_execute(content="什么是量子计算？")
    )
    conv_id = result1["conversation_id"]
    
    # 等待一下，让用户看清输出
    await asyncio.sleep(1)
    
    # 第二轮（有对话历史）
    print("\n" + "🗣️  用户: 帮我整理到artifact中，内容浅显易懂一点")
    result2 = await handler.process_stream(
        controller.stream_execute(
            content="帮我整理到artifact中，内容浅显易懂一点",
            conversation_id=conv_id
        )
    )
    
    await asyncio.sleep(1)
    
    # 第三轮
    print("\n" + "🗣️  用户: 帮我写一份最新的研究进展报告")
    result3 = await handler.process_stream(
        controller.stream_execute(
            content="帮我写一份最新的研究进展报告",
            conversation_id=conv_id
        )
    )
    
    print("\n✨ 多轮对话演示完成！")


async def demo_permission_flow():
    """演示权限确认流程（流式）"""
    logger.debug("="*60)
    logger.debug("🔐 权限确认演示（流式）")
    logger.debug("="*60)
    
    # 配置权限
    from tools.base import ToolPermission
    tool_permissions = {
        "web_fetch": ToolPermission.CONFIRM
    }
    
    compiled_graph = create_multi_agent_graph(tool_permissions=tool_permissions)
    controller = ExecutionController(compiled_graph)
    handler = StreamEventHandler(verbose=True)
    
    # 发起需要爬虫的任务
    print("\n" + "🗣️  用户: 请抓取并分析 https://github.com/langchain-ai/langgraph 的内容")
    result = await handler.process_stream(
        controller.stream_execute(
            content="请抓取并分析 https://github.com/langchain-ai/langgraph 的内容"
        )
    )
    
    # ✅ 循环处理多次中断
    max_retries = 3  # 最多处理3次权限确认
    retry_count = 0
    
    while result.get("interrupted") and retry_count < max_retries:
        retry_count += 1
        print(f"\n💭 系统请求权限确认... (第 {retry_count} 次)")
        print(f"   工具: {result['interrupt_data']['tool_name']}")
        print(f"   参数: {result['interrupt_data']['params']}")
        
        # 模拟用户决策
        print("\n🤔 用户思考中...")
        await asyncio.sleep(2)
        
        approved = False
        
        if approved:
            print("\n✅ 用户批准，继续执行...")
        else:
            print("\n❌ 用户拒绝，尝试其他方式...")
        
        # 继续执行
        result = await handler.process_stream(
            controller.stream_execute(
                thread_id=result["thread_id"],
                resume_data={"type": "permission", "approved": approved}
            )
        )
    
    if retry_count >= max_retries:
        print(f"\n⚠️ 达到最大重试次数 ({max_retries})")
    
    print("\n✨ 权限确认演示完成！")


async def demo_branch_conversation():
    """演示分支对话（流式）"""
    logger.debug("="*60)
    logger.debug("🌿 分支对话演示（流式）")
    logger.debug("="*60)
    
    compiled_graph = create_multi_agent_graph()
    controller = ExecutionController(compiled_graph)
    handler = StreamEventHandler(verbose=False)  # 简化输出
    
    # 主线对话
    print("\n" + "🗣️  用户: 计算 15 + 28 等于多少")
    result1 = await handler.process_stream(
        controller.stream_execute(content="计算 15 + 28 等于多少")
    )
    conv_id = result1["conversation_id"]
    msg1_id = result1["message_id"]
    
    await asyncio.sleep(1)
    
    # 继续主线
    print("\n" + "🗣️  用户: 再乘以2")
    result2 = await handler.process_stream(
        controller.stream_execute(
            content="再乘以2",
            conversation_id=conv_id
        )
    )
    
    await asyncio.sleep(1)
    
    # 从msg1创建分支
    print("\n" + "🌿 从第一条消息创建分支...")
    print("🗣️  用户: 再减去一万")
    result3 = await handler.process_stream(
        controller.stream_execute(
            content="再减去一万",
            conversation_id=conv_id,
            parent_message_id=msg1_id  # 从msg1分支
        )
    )
    
    print("📊 对话树结构:")
    print("   msg1: '15 + 28 = ?'")
    print("   ├─ msg2: '再乘以2'  ← 主线")
    print("   └─ msg3: '再减去一万' ← 分支")
    
    print("\n✨ 分支对话演示完成！")


async def demo_compare_batch_vs_stream():
    """对比批量模式 vs 流式模式"""
    logger.debug("="*60)
    logger.debug("⚖️  批量 vs 流式对比")
    logger.debug("="*60)
    
    compiled_graph = create_multi_agent_graph()
    controller = ExecutionController(compiled_graph)
    
    question = "简单介绍一下 Python"
    
    # 批量模式
    print("\n" + "="*80)
    print("📦 批量模式")
    print("="*80)
    print(f"\n🗣️  用户: {question}")
    print("\n⏳ 等待中...")
    
    start_time = datetime.now()
    result = await controller.execute(content=question)
    elapsed = (datetime.now() - start_time).total_seconds()
    
    if result["success"]:
        print(f"\n✅ 收到完整响应 (耗时 {elapsed:.2f}s):")
        print(f"\n{result['response'][:200]}...")
    
    await asyncio.sleep(2)
    
    # 流式模式
    print("\n" + "="*80)
    print("⚡ 流式模式")
    print("="*80)
    print(f"\n🗣️  用户: {question}")
    print("\n💬 实时输出:\n")
    
    handler = StreamEventHandler(verbose=False)
    start_time = datetime.now()
    result = await handler.process_stream(
        controller.stream_execute(content=question)
    )
    elapsed = (datetime.now() - start_time).total_seconds()
    
    print("✨ 对比演示完成！")


# ============================================================
# 主程序
# ============================================================

async def main():
    print("\n" + "🤖 ArtifactFlow 流式输出测试")
    
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
    
    choice = input("\n👉 选择 (1-7): ").strip()
    
    try:
        if choice == "7":
            # 全部演示
            for key in ["1", "2", "3", "4", "5", "6"]:
                await demos[key][1]()
                print("\n" + "-"*80)
                await asyncio.sleep(2)
        elif choice in demos:
            await demos[choice][1]()
        else:
            print("❌ 无效选择")
    except KeyboardInterrupt:
        print("\n\n⚠️  用户中断")
    except Exception as e:
        print(f"\n\n❌ 错误: {e}")
        import traceback
        traceback.print_exc()
    
    print("\n" + "="*80)
    print("👋 测试结束")
    print("="*80 + "\n")


if __name__ == "__main__":
    asyncio.run(main())