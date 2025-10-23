"""
Base Agent抽象类
提供Agent的基础功能：工具调用循环、流式输出、统一完成判断等
增强功能：错误处理和重试机制、messages返回、权限检查、任务恢复与中断
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional, AsyncGenerator, Union, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import asyncio

from utils.logger import get_logger
from utils.xml_parser import parse_tool_calls
from tools.prompt_generator import ToolPromptGenerator, format_result
from tools.registry import AgentToolkit
from tools.base import ToolResult, ToolPermission

logger = get_logger("ArtifactFlow")


class StreamEventType(Enum):
    """流式事件类型"""
    START = "start"                # 开始执行
    LLM_CHUNK = "llm_chunk"        # LLM输出片段
    LLM_COMPLETE = "llm_complete"  # LLM输出完成
    TOOL_START = "tool_start"      # 工具调用开始
    TOOL_RESULT = "tool_result"    # 工具调用结果
    PERMISSION_REQUIRED = "permission_required"  # 需要权限确认
    COMPLETE = "complete"          # 执行完成
    ERROR = "error"                # 错误


@dataclass
class StreamEvent:
    """流式事件"""
    type: StreamEventType
    agent: str
    timestamp: datetime = field(default_factory=datetime.now)
    data: Any = None  # 统一为AgentResponse或None


@dataclass
class AgentConfig:
    """Agent配置"""
    name: str
    description: str
    model: str = "qwen-plus"
    temperature: float = 0.7
    max_tool_rounds: int = 3  # 最大工具调用轮数
    streaming: bool = False  # 是否默认流式输出
    
    llm_max_retries: int = 3  # LLM调用最大重试次数
    llm_retry_delay: float = 1.0  # 初始重试延迟（秒）


@dataclass
class AgentResponse:
    """Agent响应"""
    success: bool = True  # 执行是否成功
    content: str = ""  # 回复内容或错误信息
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)  # 工具调用记录
    tool_interactions: List[Dict] = field(default_factory=list)  # assistant-tool交互历史（用于恢复）
    reasoning_content: Optional[str] = None  # 思考过程（如果有）
    metadata: Dict[str, Any] = field(default_factory=dict)  # 元数据
    routing: Optional[Dict[str, Any]] = None  # 路由信息
    token_usage: Optional[Dict[str, Any]] = None  # Token使用统计


class BaseAgent(ABC):
    """
    所有Agent的基类
    
    核心功能：
    1. 统一的工具调用循环（最多N轮）
    2. 流式输出支持（LLM流式，工具批量）
    3. 统一的完成判断（无工具调用即完成）
    4. 思考模型兼容（记录reasoning_content）
    5. 错误处理和重试机制
    6. 权限管理和执行中断/恢复
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
        
        # 创建LLM实例
        from models.llm import create_llm
        self.llm = create_llm(
            model=config.model,
            temperature=config.temperature,
            streaming=config.streaming
        )
        
        logger.info(f"Initialized {config.name} with model {config.model}")
    
    def _format_messages_for_debug(self, messages: List[Dict], max_content_len: int = 100000) -> str:
        """格式化消息用于调试输出"""
        formatted_lines = []
        
        for msg in messages:
            role = msg["role"]
            content = msg.get("content", "")
            
            if not content: continue

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

    def build_complete_system_prompt(self, context: Optional[Dict[str, Any]] = None) -> str:
        """
        构建完整的系统提示词（包含工具说明）
        
        Args:
            context: 动态上下文
            
        Returns:
            完整的系统提示词
        """
        # 调用子类实现的业务逻辑部分
        prompt = self.build_system_prompt(context)
        
        # 追加工具使用说明
        if self.toolkit and self.toolkit.list_tools():
            from tools.prompt_generator import ToolPromptGenerator
            tools_instruction = ToolPromptGenerator.generate_tool_instruction(
                self.toolkit.list_tools()
            )
            prompt += f"\n\n{tools_instruction}"
        
        return prompt

    @abstractmethod
    def build_system_prompt(self, context: Optional[Dict[str, Any]] = None) -> str:
        """
        构建系统提示词（子类实现）
        
        Args:
            context: 动态上下文
            
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

    async def _execute_single_tool(self, tool_call) -> ToolResult:
        """执行单个工具调用（带权限检查）"""
        if not self.toolkit:
            return ToolResult(success=False, error="No toolkit available")
        
        tool = self.toolkit.get_tool(tool_call.name)
        if not tool:
            return ToolResult(success=False, error=f"Tool '{tool_call.name}' not found")
        
        # 权限检查：CONFIRM和RESTRICTED级别需要确认
        if tool.permission in [ToolPermission.CONFIRM, ToolPermission.RESTRICTED]:
            logger.info(f"Tool '{tool_call.name}' requires {tool.permission.value} permission")
            return ToolResult(
                success=True,
                data=f"Tool '{tool_call.name}' requires {tool.permission.value} permission to execute.",
                metadata={
                    "needs_confirmation": True,
                    "tool_name": tool_call.name,
                    "params": tool_call.params,
                    "permission_level": tool.permission.value
                }
            )
        
        # PUBLIC和NOTIFY级别直接执行
        return await self.toolkit.execute_tool(tool_call.name, tool_call.params)

    async def _call_llm_with_retry(
        self, 
        messages: List[Dict],
        streaming: bool = False,
        max_retries: Optional[int] = None
    ) -> Union[Any, AsyncGenerator]:
        """
        带重试的LLM调用
        
        Args:
            messages: 消息列表
            streaming: 是否流式
            max_retries: 最大重试次数（None则使用配置）
            
        Returns:
            LLM响应或流式生成器
            
        Raises:
            Exception: 重试失败后的最后异常
        """
        max_retries = max_retries or self.config.llm_max_retries
        delay = self.config.llm_retry_delay
        last_error = None
        
        for attempt in range(max_retries):
            try:
                if streaming:
                    # 流式调用直接返回生成器
                    return self.llm.astream(messages)
                else:
                    # 批量调用
                    return await self.llm.ainvoke(messages)
            except Exception as e:
                last_error = e
                error_str = str(e).lower()
                wait_time = delay * (1.5 ** attempt)
                
                # 分析错误类型
                if "rate" in error_str or "limit" in error_str:
                    # 速率限制：指数退避
                    wait_time = delay * (2 ** attempt)
                    logger.warning(f"LLM rate limited, retry {attempt+1}/{max_retries} after {wait_time}s")
                elif "timeout" in error_str:
                    # 超时：快速重试
                    wait_time = delay
                    logger.warning(f"LLM timeout, retry {attempt+1}/{max_retries} after {wait_time}s")
                elif "auth" in error_str or "api" in error_str and "key" in error_str:
                    # 认证错误：不重试
                    logger.error(f"LLM authentication error: {e}")
                    raise
                else:
                    # 其他错误：普通重试
                    wait_time = delay * (1.5 ** attempt)
                    logger.warning(f"LLM error: {e}, retry {attempt+1}/{max_retries} after {wait_time}s")
                
                # 如果是最后一次尝试，不等待直接抛出
                if attempt < max_retries - 1:
                    await asyncio.sleep(wait_time)
                else:
                    raise
        
        raise last_error or RuntimeError("LLM call failed without specific error")

    async def _execute_generator(
        self,
        messages: List[Dict],
        is_resuming: bool = False,
        streaming_tokens: bool = False
    ) -> AsyncGenerator[StreamEvent, None]:
        """
        核心执行生成器
        
        Args:
            messages: 完整的消息列表（system + history + instruction + interactions）
            is_resuming: 是否从中断恢复
            streaming_tokens: 是否流式输出LLM tokens
        """
        current_response = AgentResponse(
            metadata={
                "agent": self.config.name,
                "model": self.config.model,
                "started_at": datetime.now().isoformat()
            }
        )
        
        yield StreamEvent(
            type=StreamEventType.START, 
            agent=self.config.name, 
            data=current_response
        )
        
        try:
            # 根据 is_resuming 初始化交互历史
            if is_resuming:
                # 恢复时：最后一条消息是 pending_tool_result，需要记录
                new_tool_interactions = [messages[-1]]
                logger.debug(f"Resuming: initialized tool_interactions with pending result")
            else:
                # 正常执行：从空开始
                new_tool_interactions = []
            accumulated_token_usage = {
                "input_tokens": 0, 
                "output_tokens": 0, 
                "total_tokens": 0
            }
            
            # ========== 工具调用循环 ==========
            for round_num in range(self.config.max_tool_rounds + 1):
                # 检查是否超过工具调用限制
                if round_num == self.config.max_tool_rounds:
                    # 最后一轮，提示总结
                    messages.append({
                        "role": "system",
                        "content": "⚠️ Maximum tool calls reached. Please summarize your findings and provide the final response."
                    })
                
                logger.debug(f"[{self.config.name} Round {round_num + 1}] Messages:\n{self._format_messages_for_debug(messages)}")
                
                # ========== LLM调用 ========== 
                response_content = ""
                reasoning_content = None
                token_usage = {}
                
                try:
                    if streaming_tokens:
                        # 流式模式：逐token处理
                        stream = await self._call_llm_with_retry(messages, streaming=True)
                        async for chunk in stream:
                            # 累积content
                            if hasattr(chunk, 'content') and chunk.content:
                                response_content += chunk.content
                                current_response.content = response_content
                            
                            # 累积reasoning_content（如果有）
                            if hasattr(chunk, 'additional_kwargs') and 'reasoning_content' in chunk.additional_kwargs:
                                # 第一次出现reasoning_content时初始化为空字符串
                                if reasoning_content is None: 
                                    reasoning_content = ""
                                reasoning_content += chunk.additional_kwargs['reasoning_content']
                                current_response.reasoning_content = reasoning_content
                            
                            # 获取token_usage（通常在最后一个chunk）
                            if hasattr(chunk, 'response_metadata') and 'token_usage' in chunk.response_metadata:
                                token_usage = chunk.response_metadata['token_usage']

                            # Yield LLM chunk事件
                            yield StreamEvent(
                                type=StreamEventType.LLM_CHUNK, 
                                agent=self.config.name, 
                                data=current_response
                            )
                        # 流式结束后，yield 完成事件
                        yield StreamEvent(
                            type=StreamEventType.LLM_COMPLETE, 
                            agent=self.config.name, 
                            data=current_response
                        )
                    else:
                        # 批量模式：一次性获取完整响应
                        response = await self._call_llm_with_retry(messages, streaming=False)
                        response_content = response.content
                        current_response.content = response_content
                        # 获取reasoning_content（如果有）
                        if hasattr(response, 'additional_kwargs'):
                            reasoning_content = response.additional_kwargs.get('reasoning_content')
                            if reasoning_content:
                                current_response.reasoning_content = reasoning_content
                        # 获取token_usage
                        if hasattr(response, 'response_metadata'):
                            token_usage = response.response_metadata.get('token_usage', {})
                        # Yield完整LLM响应事件
                        yield StreamEvent(
                            type=StreamEventType.LLM_COMPLETE, 
                            agent=self.config.name, 
                            data=current_response
                        )

                    # 更新token统计
                    if token_usage:
                        # input_tokens, output_tokens, total_tokens
                        for key in accumulated_token_usage:
                            accumulated_token_usage[key] += token_usage.get(key, 0)
                        current_response.token_usage = accumulated_token_usage.copy()
                
                except Exception as llm_error:
                    # LLM调用失败
                    logger.error(f"LLM call failed after retries: {llm_error}")
                    
                    # 设置失败响应
                    current_response.success = False
                    current_response.content = f"LLM call failed: {str(llm_error)}"

                    # Yield错误事件
                    yield StreamEvent(
                        type=StreamEventType.ERROR, 
                        agent=self.config.name, 
                        data=current_response
                    )

                    return # LLM调用失败是致命的，中断执行

                if reasoning_content:
                    logger.debug(f"[{self.config.name} Round {round_num + 1}] Reasoning:\n{reasoning_content}")
                input_tokens = token_usage.get('input_tokens', 0)
                output_tokens = token_usage.get('output_tokens', 0)
                logger.debug(f"[{self.config.name} Round {round_num + 1}] LLM Response (input: {input_tokens}, output: {output_tokens}):\n{response_content}")
                logger.debug(f"[{self.config.name} Round {round_num + 1}] LLM Raw Response (input: {input_tokens}, output: {output_tokens}):\n{repr(response_content)}")
                
                messages.append({"role": "assistant", "content": response_content})
                new_tool_interactions.append({"role": "assistant", "content": response_content})
                
                # ========== 工具调用 ========== 
                # 解析工具调用
                tool_calls = parse_tool_calls(response_content)
                
                # 判断工具循环是否完成
                if not tool_calls or round_num >= self.config.max_tool_rounds:
                    # 没有工具调用或达到上限，结束
                    current_response.content = response_content
                    break
                
                # 执行工具调用
                tool_results_xml = []
                for tool_call in tool_calls:
                    self.tool_call_count += 1
                    logger.info(f"{self.config.name} calling tool: '{tool_call.name}'")
                    
                    yield StreamEvent(
                        type=StreamEventType.TOOL_START, 
                        agent=self.config.name, 
                        data=current_response
                    )
                    
                    try:
                        result = await self._execute_single_tool(tool_call)
                        if result.success and result.metadata.get("needs_confirmation") is not True:
                            logger.info(f"{self.config.name} tool '{tool_call.name}': SUCCESS")
                        elif result.metadata.get("needs_confirmation"):
                            logger.info(f"{self.config.name} tool '{tool_call.name}': PENDING CONFIRMATION")
                        else:
                            logger.warning(f"{self.config.name} tool '{tool_call.name}': FAILED - {result.error}")
                        
                        # ========== 检查是否需要中断 ==========
                        # 检查是否需要权限确认
                        if result.metadata and result.metadata.get("needs_confirmation"):
                            # 权限确认中断
                            current_response.tool_interactions = new_tool_interactions
                            # 配置工具权限路由
                            current_response.routing = {
                                "type": "permission_confirmation",
                                "tool_name": tool_call.name,
                                "params": tool_call.params,
                                "permission_level": result.metadata["permission_level"]
                            }
                            # Yield权限确认事件
                            yield StreamEvent(
                                type=StreamEventType.PERMISSION_REQUIRED, 
                                agent=self.config.name, 
                                data=current_response
                            )
                            return # 中断执行，等待权限确认

                        # 检查是否是Subagent路由指令
                        if (tool_call.name == "call_subagent" and 
                            result.success and 
                            isinstance(result.data, dict) and 
                            result.data.get("_is_routing_instruction")):
                            # Agent路由中断
                            current_response.tool_interactions = new_tool_interactions
                            current_response.routing = {
                                "type": "subagent",
                                "target": result.data.get("_route_to"),
                                "instruction": result.data.get("instruction")
                            }
                            # Yield Agent路由事件
                            yield StreamEvent(
                                type=StreamEventType.COMPLETE, 
                                agent=self.config.name, 
                                data=current_response
                            )
                            return # 中断执行，进行Agent路由
                        
                        # 更新工具调用记录（用于展示）
                        tool_history_entry = {
                            "tool": tool_call.name,
                            "params": tool_call.params,
                            "result": result.to_dict(),
                            "round": round_num + 1
                        }
                        current_response.tool_calls.append(tool_history_entry)
                        
                        yield StreamEvent(
                            type=StreamEventType.TOOL_RESULT, 
                            agent=self.config.name, 
                            data=current_response
                        )
                        
                    except Exception as tool_error:
                        logger.exception(f"Tool {tool_call.name} error: {tool_error}")
                        result = ToolResult(success=False, error=str(tool_error))
                    
                    # 格式化工具结果
                    tool_results_xml.append(format_result(tool_call.name, result.to_dict()))
                
                # 添加工具结果到消息
                if tool_results_xml:
                    tool_result_msg = "\n".join(tool_results_xml)
                    messages.append({"role": "user", "content": tool_result_msg})
                    new_tool_interactions.append({"role": "user", "content": tool_result_msg})
            
            # 保存完整的工具交互历史
            current_response.tool_interactions = new_tool_interactions
            current_response.metadata["tool_rounds"] = self.tool_call_count
            
            # 格式化最终响应
            final_response = self.format_final_response(
                current_response.content,
                current_response.tool_calls
            )
            current_response.content = final_response
            
            yield StreamEvent(
                type=StreamEventType.COMPLETE, 
                agent=self.config.name, 
                data=current_response
            )
            
        except Exception as e:
            logger.exception(f"Unexpected error in {self.config.name}: {e}")
            current_response.success = False
            current_response.content = f"Agent execution failed: {str(e)}"
            current_response.tool_interactions = new_tool_interactions  # 保存已有的交互
            yield StreamEvent(
                type=StreamEventType.ERROR, 
                agent=self.config.name, 
                data=current_response
            )

    async def execute(
        self,
        messages: List[Dict],
        is_resuming: bool = False
    ) -> AgentResponse:
        """
        批量执行Agent任务
        
        Args:
            messages: 完整的消息列表
            is_resuming: 是否从中断恢复
        """
        final_response = None
        async for event in self._execute_generator(messages, is_resuming, streaming_tokens=False):
            if event.type == StreamEventType.PERMISSION_REQUIRED:
                return event.data
            
            if event.data:
                final_response = event.data

        return final_response or AgentResponse(
            success=False, 
            content="Execution failed"
        )

    async def stream(
        self,
        messages: List[Dict],
        is_resuming: bool = False
    ) -> AsyncGenerator[StreamEvent, None]:
        """
        流式执行Agent任务
        
        Args:
            messages: 完整的消息列表
            is_resuming: 是否从中断恢复
        """
        async for event in self._execute_generator(messages, is_resuming, streaming_tokens=True):
            yield event


def create_agent_config(
    name: str,
    description: str,
    **kwargs
) -> AgentConfig:
    """创建Agent配置的便捷函数"""
    return AgentConfig(name=name, description=description, **kwargs)