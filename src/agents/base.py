"""
Base Agent抽象类
提供Agent的基础功能：工具调用循环、流式输出、统一完成判断等
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional, AsyncGenerator
from dataclasses import dataclass, field

from utils.logger import get_logger
from utils.xml_parser import parse_tool_calls
from tools.prompt_generator import ToolPromptGenerator, format_result
from tools.registry import AgentToolkit
from tools.base import ToolResult

logger = get_logger("BaseAgent")


@dataclass
class AgentConfig:
    """Agent配置"""
    name: str
    description: str
    model: str = "qwen-plus"
    temperature: float = 0.7
    max_tool_rounds: int = 3  # 最大工具调用轮数
    streaming: bool = True  # 是否支持流式输出
    debug: bool = False  # 是否开启调试模式


@dataclass
class AgentResponse:
    """Agent响应"""
    content: str  # 最终回复内容
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)  # 工具调用记录
    reasoning_content: Optional[str] = None  # 思考过程（如果有）
    metadata: Dict[str, Any] = field(default_factory=dict)  # 元数据


class BaseAgent(ABC):
    """
    所有Agent的基类
    
    核心功能：
    1. 统一的工具调用循环（最多3轮）
    2. 流式输出支持（LLM流式，工具批量）
    3. 统一的完成判断（无工具调用即完成）
    4. 思考模型兼容（记录reasoning_content）
    """
    
    def __init__(self, config: AgentConfig, toolkit: Optional[AgentToolkit] = None):
        """
        初始化Agent
        
        Args:
            config: Agent配置
            toolkit: 工具包（可选）
        """
        self.config = config
        self.toolkit = toolkit
        self.tool_call_count = 0  # 工具调用计数
        self.conversation_history = []  # 对话历史
        
        # 创建LLM实例
        from models.llm import create_llm
        self.llm = create_llm(
            model=config.model,
            temperature=config.temperature,
            streaming=config.streaming
        )
        
        logger.info(f"Initialized {config.name} with model {config.model}")
    
    @abstractmethod
    def build_system_prompt(self, context: Optional[Dict[str, Any]] = None) -> str:
        """
        构建系统提示词（子类实现）
        
        Args:
            context: 动态上下文（如task_plan内容）
            
        Returns:
            系统提示词
        """
        pass
    
    @abstractmethod
    def format_final_response(self, content: str, tool_history: List[Dict]) -> str:
        """
        格式化最终响应（子类实现）
        
        Args:
            content: LLM的最终回复
            tool_history: 工具调用历史
            
        Returns:
            格式化后的响应
        """
        pass
    
    async def execute(
        self,
        user_input: str,
        context: Optional[Dict[str, Any]] = None
    ) -> AgentResponse:
        """
        执行Agent任务（核心方法）
        
        实现统一的执行流程：
        1. 构建提示词
        2. 工具调用循环（最多3轮）
        3. 统一的完成判断
        4. 返回格式化结果
        
        Args:
            user_input: 用户输入/任务指令
            context: 执行上下文
            
        Returns:
            Agent响应
        """
        # 重置状态
        self.tool_call_count = 0
        tool_history = []
        
        # 构建系统提示词
        system_prompt = self.build_system_prompt(context)
        
        # 添加工具使用说明（如果有工具）
        if self.toolkit and self.toolkit.list_tools():
            tools_instruction = ToolPromptGenerator.generate_tool_instruction(
                self.toolkit.list_tools()
            )
            system_prompt += f"\n\n{tools_instruction}"
        
        # 准备消息
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input}
        ]
        
        # 工具调用循环
        final_content = ""
        reasoning_content = None
        
        for round_num in range(self.config.max_tool_rounds + 1):
            # 检查是否超过工具调用限制
            if round_num == self.config.max_tool_rounds:
                # 添加限制提示
                messages.append({
                    "role": "system",
                    "content": "⚠️ You have reached the maximum tool call limit. Please summarize your findings and provide the final response."
                })
            
            # 调用LLM
            if self.config.streaming:
                # 流式输出（用户可实时看到）
                response_content = ""
                async for chunk in self.llm.astream(messages):
                    if hasattr(chunk, 'content'):
                        response_content += chunk.content
                        # TODO: 这里可以yield chunk给前端
                    
                    # 记录思考过程（如果有）
                    if hasattr(chunk, 'additional_kwargs'):
                        if 'reasoning_content' in chunk.additional_kwargs:
                            reasoning_content = chunk.additional_kwargs['reasoning_content']
            else:
                # 批量输出
                response = await self.llm.ainvoke(messages)
                response_content = response.content
                
                # 记录思考过程
                if hasattr(response, 'additional_kwargs'):
                    if 'reasoning_content' in response.additional_kwargs:
                        reasoning_content = response.additional_kwargs['reasoning_content']
            
            # 调试模式：记录完整对话
            if self.config.debug:
                logger.debug(f"[Round {round_num + 1}] LLM Response: {response_content[:500]}...")
                if reasoning_content:
                    logger.debug(f"[Round {round_num + 1}] Reasoning: {reasoning_content[:500]}...")
            
            # 解析工具调用
            tool_calls = parse_tool_calls(response_content)
            
            # 判断是否完成（无工具调用即完成）
            if not tool_calls or round_num >= self.config.max_tool_rounds:
                final_content = response_content
                break
            
            # 执行工具调用
            tool_results = []
            for tool_call in tool_calls:
                self.tool_call_count += 1
                
                logger.info(f"{self.config.name} calling tool: {tool_call.name}")
                
                # 执行工具
                if self.toolkit:
                    result = await self.toolkit.execute_tool(
                        tool_call.name,
                        tool_call.params
                    )
                    
                    # 记录工具调用历史
                    tool_history.append({
                        "tool": tool_call.name,
                        "params": tool_call.params,
                        "result": result.to_dict()
                    })
                    
                    # 格式化工具结果为XML
                    xml_result = format_result(tool_call.name, result.to_dict())
                    tool_results.append(xml_result)
                else:
                    # 没有工具包，返回错误
                    tool_results.append(
                        f"<tool_result><name>{tool_call.name}</name>"
                        f"<success>false</success>"
                        f"<error>No toolkit available</error></tool_result>"
                    )
            
            # 将工具结果添加到对话历史
            messages.append({"role": "assistant", "content": response_content})
            messages.append({"role": "user", "content": "\n".join(tool_results)})
        
        # 格式化最终响应
        formatted_response = self.format_final_response(final_content, tool_history)
        
        # 构建响应对象
        return AgentResponse(
            content=formatted_response,
            tool_calls=tool_history,
            reasoning_content=reasoning_content,
            metadata={
                "agent": self.config.name,
                "model": self.config.model,
                "tool_rounds": self.tool_call_count,
                "completed": True
            }
        )
    
    async def reset(self):
        """重置Agent状态"""
        self.tool_call_count = 0
        self.conversation_history.clear()
        logger.debug(f"{self.config.name} state reset")


# 便捷函数
def create_agent_config(
    name: str,
    description: str,
    **kwargs
) -> AgentConfig:
    """创建Agent配置的便捷函数"""
    return AgentConfig(name=name, description=description, **kwargs)