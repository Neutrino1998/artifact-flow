"""
Base Agent抽象类
提供Agent的基础功能：工具调用循环、流式输出、统一完成判断等
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional, AsyncGenerator, Union
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from utils.logger import get_logger
from utils.xml_parser import parse_tool_calls
from tools.prompt_generator import ToolPromptGenerator, format_result
from tools.registry import AgentToolkit
from tools.base import ToolResult

logger = get_logger("Agents")


class StreamEventType(Enum):
    """流式事件类型"""
    START = "start"                # 开始执行
    LLM_CHUNK = "llm_chunk"        # LLM输出片段
    LLM_COMPLETE = "llm_complete"  # LLM输出完成
    TOOL_START = "tool_start"      # 工具调用开始
    TOOL_RESULT = "tool_result"    # 工具调用结果
    COMPLETE = "complete"          # 执行完成
    ERROR = "error"                # 错误


@dataclass
class StreamEvent:
    """流式事件"""
    type: StreamEventType
    agent: str
    timestamp: datetime = field(default_factory=datetime.now)
    data: Dict[str, Any] = field(default_factory=dict)


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
    routing: Optional[Dict[str, Any]] = None  # 新增：路由信息

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

    def _format_messages_for_debug(self, messages: List[Dict], max_content_len: int = 100000) -> str:
        """将messages格式化为简洁的聊天记录格式"""
        formatted_lines = []
        
        for msg in messages:
            role = msg["role"]
            content = msg["content"]
            
            # 截断长内容
            if len(content) > max_content_len:
                content = content[:max_content_len] + "..."
            
            # 格式化为聊天记录格式
            formatted_lines.append(f"> {role}:")
            # 添加缩进
            for line in content.split('\n'):
                formatted_lines.append(f"  {line}")
            formatted_lines.append("")  # 空行分隔
        
        return "\n".join(formatted_lines)
    
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
            # 调试模式：记录完整的messages
            if self.config.debug:
                logger.debug(f"[{self.config.name} Round {round_num + 1}] =================")
                logger.debug(f"[{self.config.name} Round {round_num + 1}] Messages being sent to LLM:\n{self._format_messages_for_debug(messages)}")
                logger.debug(f"[{self.config.name} Round {round_num + 1}] =================")

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
                if reasoning_content:
                    logger.debug(f"[{self.config.name} Round {round_num + 1}] Reasoning (complete):\n{reasoning_content}")
                logger.debug(f"[{self.config.name} Round {round_num + 1}] LLM Response (complete):\n{response_content}")

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
                    
                    # 🔍 检查是否是路由指令
                    if tool_call.name == "call_subagent" and result.success:
                        result_data = result.to_dict().get("data", {})
                        if result_data.get("_is_routing_instruction"):
                            # 立即返回，带上路由信息
                            return AgentResponse(
                                content=response_content,  # 当前的响应内容
                                tool_calls=tool_history,
                                reasoning_content=reasoning_content,
                                routing={  # 路由信息
                                    "target": result_data.get("_route_to"),
                                    "instruction": result_data.get("instruction"),
                                    "from_agent": self.config.name
                                },
                                metadata={
                                    "agent": self.config.name,
                                    "model": self.config.model,
                                    "needs_routing": True
                                }
                            )

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
    
    async def execute_stream(
        self,
        user_input: str,
        context: Optional[Dict[str, Any]] = None
    ) -> AsyncGenerator[StreamEvent, None]:
        """
        流式执行Agent任务（用于LangGraph节点）
        
        Yields不同类型的事件，支持实时流式输出
        
        事件类型：
        - START: 执行开始
        - LLM_CHUNK: LLM输出片段
        - LLM_COMPLETE: LLM输出完成
        - TOOL_START: 工具调用开始
        - TOOL_RESULT: 工具调用结果
        - COMPLETE: 执行完成
        - ERROR: 错误
        
        Args:
            user_input: 用户输入/任务指令
            context: 执行上下文
            
        Yields:
            StreamEvent: 流式事件
        """
        # Yield开始事件
        yield StreamEvent(
            type=StreamEventType.START,
            agent=self.config.name,
            data={"user_input": user_input[:100], "has_context": context is not None}
        )
        
        # 重置状态
        self.tool_call_count = 0
        tool_history = []
        
        try:
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
                    messages.append({
                        "role": "system",
                        "content": "⚠️ You have reached the maximum tool call limit. Please summarize your findings and provide the final response."
                    })
                # 调试模式：记录完整的messages
                if self.config.debug:
                    logger.debug(f"[{self.config.name} Round {round_num + 1}] =================")
                    logger.debug(f"[{self.config.name} Round {round_num + 1}] Messages being sent to LLM:\n{self._format_messages_for_debug(messages)}")
                    logger.debug(f"[{self.config.name} Round {round_num + 1}] =================")

                # LLM流式调用
                response_content = ""
                chunk_count = 0
                
                if self.config.streaming:
                    # 流式输出
                    async for chunk in self.llm.astream(messages):
                        if hasattr(chunk, 'content') and chunk.content:
                            response_content += chunk.content
                            chunk_count += 1
                            
                            # Yield LLM chunk事件
                            yield StreamEvent(
                                type=StreamEventType.LLM_CHUNK,
                                agent=self.config.name,
                                data={
                                    "content": chunk.content,
                                    "round": round_num + 1,
                                    "chunk_index": chunk_count
                                }
                            )
                        
                        # 记录思考过程（如果有）
                        if hasattr(chunk, 'additional_kwargs'):
                            if 'reasoning_content' in chunk.additional_kwargs:
                                reasoning_content = chunk.additional_kwargs['reasoning_content']
                                # 可选：yield思考内容
                                if self.config.debug:
                                    yield StreamEvent(
                                        type=StreamEventType.LLM_CHUNK,
                                        agent=self.config.name,
                                        data={
                                            "thinking": reasoning_content,
                                            "is_thinking": True
                                        }
                                    )
                else:
                    # 批量输出（非流式）
                    response = await self.llm.ainvoke(messages)
                    response_content = response.content
                    
                    # Yield完整的LLM响应作为单个chunk
                    yield StreamEvent(
                        type=StreamEventType.LLM_CHUNK,
                        agent=self.config.name,
                        data={
                            "content": response_content,
                            "round": round_num + 1,
                            "is_complete": True
                        }
                    )
                    
                    # 记录思考过程
                    if hasattr(response, 'additional_kwargs'):
                        if 'reasoning_content' in response.additional_kwargs:
                            reasoning_content = response.additional_kwargs['reasoning_content']
                
                # Yield LLM完成事件
                yield StreamEvent(
                    type=StreamEventType.LLM_COMPLETE,
                    agent=self.config.name,
                    data={
                        "round": round_num + 1,
                        "content_length": len(response_content)
                    }
                )
                
                # 调试模式记录
                if self.config.debug:
                    if reasoning_content:
                        logger.debug(f"[{self.config.name} Round {round_num + 1}] Reasoning (complete):\n{reasoning_content}")
                    logger.debug(f"[{self.config.name} Round {round_num + 1}] LLM Response (complete):\n{response_content}")

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
                    
                    # Yield工具开始事件
                    yield StreamEvent(
                        type=StreamEventType.TOOL_START,
                        agent=self.config.name,
                        data={
                            "tool": tool_call.name,
                            "params": tool_call.params,
                            "round": round_num + 1,
                            "call_index": self.tool_call_count
                        }
                    )
                    
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
                        
                        # Yield工具结果事件
                        yield StreamEvent(
                            type=StreamEventType.TOOL_RESULT,
                            agent=self.config.name,
                            data={
                                "tool": tool_call.name,
                                "success": result.success,
                                "result": result.to_dict(),
                                "round": round_num + 1
                            }
                        )
                        
                        # 格式化工具结果为XML
                        xml_result = format_result(tool_call.name, result.to_dict())
                        tool_results.append(xml_result)
                    else:
                        # 没有工具包，返回错误
                        error_result = {
                            "success": False,
                            "error": "No toolkit available"
                        }
                        
                        yield StreamEvent(
                            type=StreamEventType.TOOL_RESULT,
                            agent=self.config.name,
                            data={
                                "tool": tool_call.name,
                                "success": False,
                                "error": "No toolkit available"
                            }
                        )
                        
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
            
            # 构建最终响应对象
            final_response = AgentResponse(
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
            
            # Yield完成事件
            yield StreamEvent(
                type=StreamEventType.COMPLETE,
                agent=self.config.name,
                data={
                    "response": final_response,
                    "tool_calls_count": len(tool_history),
                    "final_content_length": len(formatted_response)
                }
            )
            
        except Exception as e:
            # Yield错误事件
            logger.exception(f"Agent execution error: {str(e)}")
            yield StreamEvent(
                type=StreamEventType.ERROR,
                agent=self.config.name,
                data={
                    "error": str(e),
                    "error_type": type(e).__name__
                }
            )
            raise
    
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