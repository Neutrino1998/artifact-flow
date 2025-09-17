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

logger = get_logger("Agents")


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
    debug: bool = False  # 是否开启调试模式
    
    llm_max_retries: int = 3  # LLM调用最大重试次数
    llm_retry_delay: float = 1.0  # 初始重试延迟（秒）


@dataclass
class AgentResponse:
    """Agent响应"""
    success: bool = True  # 执行是否成功
    content: str = ""  # 回复内容或错误信息
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)  # 工具调用记录
    reasoning_content: Optional[str] = None  # 思考过程（如果有）
    metadata: Dict[str, Any] = field(default_factory=dict)  # 元数据
    routing: Optional[Dict[str, Any]] = None  # 路由信息
    token_usage: Optional[Dict[str, Any]] = None  # Token使用统计
    messages: List[Dict] = field(default_factory=list)  # 新增：完整对话历史（不含系统提示词）


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
    
    async def _prepare_context_with_task_plan(self, user_context: Optional[Dict]) -> Dict:
        """
        准备context：
        1. 所有agent都注入task_plan（如果存在）
        2. Lead Agent额外注入artifacts列表
        """
        context = user_context or {}
        
        try:
            from tools.implementations.artifact_ops import _artifact_store
            
            task_plan = _artifact_store.get("task_plan")
            if task_plan:
                context["task_plan_content"] = task_plan.content
                context["task_plan_version"] = task_plan.current_version
                context["task_plan_updated"] = task_plan.updated_at.isoformat()
                logger.debug(f"{self.config.name} loaded task_plan (v{task_plan.current_version})")
            
            if self.config.name == "lead_agent":
                artifacts_list = _artifact_store.list_artifacts()
                if artifacts_list:
                    context["artifacts_inventory"] = artifacts_list
                    context["artifacts_count"] = len(artifacts_list)
                    logger.debug(f"Lead Agent loaded {len(artifacts_list)} artifacts inventory")
                        
        except Exception as e:
            logger.debug(f"{self.config.name} context preparation partial failure: {e}")
        
        return context

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
        instruction: str,
        context: Optional[Dict[str, Any]] = None,
        external_history: Optional[List[Dict]] = None,
        pending_tool_result: Optional[Tuple[str, ToolResult]] = None,
        streaming_tokens: bool = False
    ) -> AsyncGenerator[StreamEvent, None]:
        """
        核心执行生成器, 统一的执行逻辑（支持中断和恢复）
        
        Args:
            instruction: 用户指令（仅在新执行时使用）
            context: 执行上下文
            external_history: 外部提供的历史记录（用于恢复）
            pending_tool_result: 待处理的工具结果（用于恢复）
            streaming_tokens: 是否流式输出LLM tokens
        """
        current_response = AgentResponse(
            metadata={
                "agent": self.config.name,
                "model": self.config.model,
                "started_at": datetime.now().isoformat()
            }
        )
        
        yield StreamEvent(type=StreamEventType.START, agent=self.config.name, data=current_response)
        
        messages = []
        try:
            self.tool_call_count = 0
            tool_history = []
            
            # 准备context
            enhanced_context = await self._prepare_context_with_task_plan(context)
            
            # 构建系统提示词
            system_prompt = self.build_system_prompt(enhanced_context)
            
            # 添加工具使用说明
            if self.toolkit and self.toolkit.list_tools():
                tools_instruction = ToolPromptGenerator.generate_tool_instruction(self.toolkit.list_tools())
                system_prompt += f"\n\n{tools_instruction}"
            
            messages = [{"role": "system", "content": system_prompt}]

            if external_history:
                messages.extend(external_history)
                if pending_tool_result:
                    tool_name, result = pending_tool_result
                    tool_result_text = format_result(tool_name, result.to_dict())
                    messages.append({"role": "user", "content": tool_result_text})
                    logger.info(f"Resumed with tool result for '{tool_name}'")
            else:
                messages.append({"role": "user", "content": instruction})

            final_content = ""
            accumulated_token_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
            
            for round_num in range(self.config.max_tool_rounds + 1):
                # 检查是否超过工具调用限制
                if round_num == self.config.max_tool_rounds:
                    messages.append({
                        "role": "system",
                        "content": "⚠️ You have reached the maximum tool call limit. Please summarize your findings and provide the final response."
                    })
                
                if self.config.debug:
                    logger.debug(f"[{self.config.name} Round {round_num + 1}] Messages:\n{self._format_messages_for_debug(messages)}")
                
                # LLM调用
                response_content = ""
                try:
                    if streaming_tokens:
                        # 流式模式：逐token处理
                        response_content, reasoning_content, token_usage = "", None, {}
                        stream = await self._call_llm_with_retry(messages, streaming=True)
                        async for chunk in stream:
                            # 累积content
                            if hasattr(chunk, 'content') and chunk.content:
                                response_content += chunk.content
                                current_response.content = response_content
                            
                            # 累积reasoning_content（如果有）
                            if hasattr(chunk, 'additional_kwargs') and 'reasoning_content' in chunk.additional_kwargs:
                                # 第一次出现reasoning_content时初始化为空字符串
                                if reasoning_content is None: reasoning_content = ""
                                reasoning_content += chunk.additional_kwargs['reasoning_content']
                                current_response.reasoning_content = reasoning_content
                            
                            # 获取token_usage（通常在最后一个chunk）
                            if hasattr(chunk, 'response_metadata') and 'token_usage' in chunk.response_metadata:
                                token_usage = chunk.response_metadata['token_usage']

                            # Yield LLM chunk事件
                            yield StreamEvent(type=StreamEventType.LLM_CHUNK, agent=self.config.name, data=current_response)
                    else:
                        # 批量模式：一次性获取完整响应
                        response = await self._call_llm_with_retry(messages, streaming=False)
                        response_content = response.content
                        current_response.content = response_content
                        # 获取reasoning_content
                        reasoning_content = getattr(response, 'additional_kwargs', {}).get('reasoning_content')
                        if reasoning_content: current_response.reasoning_content = reasoning_content
                        # 获取token_usage
                        token_usage = getattr(response, 'response_metadata', {}).get('token_usage', {})
                        # Yield完整LLM响应事件
                        yield StreamEvent(type=StreamEventType.LLM_COMPLETE, agent=self.config.name, data=current_response)

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
                    yield StreamEvent(type=StreamEventType.ERROR, agent=self.config.name, data=current_response)

                    return # LLM调用失败是致命的，中断执行

                if self.config.debug:
                    if reasoning_content:
                        logger.debug(f"[{self.config.name} Round {round_num + 1}] Reasoning:\n{reasoning_content}")
                    input_tokens = token_usage.get('input_tokens', 0)
                    output_tokens = token_usage.get('output_tokens', 0)
                    logger.debug(f"[{self.config.name} Round {round_num + 1}] LLM Response (input: {input_tokens}, output: {output_tokens}):\n{response_content}")
                    logger.debug(f"[{self.config.name} Round {round_num + 1}] LLM Raw Response (input: {input_tokens}, output: {output_tokens}):\n{repr(response_content)}")
                
                messages.append({"role": "assistant", "content": response_content})
                # 解析工具调用
                tool_calls = parse_tool_calls(response_content)
                
                # 判断工具循环是否完成
                if not tool_calls or round_num >= self.config.max_tool_rounds:
                    final_content = response_content
                    break
                
                # 执行工具调用
                tool_results_xml = []
                for tool_call in tool_calls:
                    self.tool_call_count += 1
                    logger.info(f"{self.config.name} calling tool: {tool_call.name}")
                    yield StreamEvent(type=StreamEventType.TOOL_START, agent=self.config.name, data=current_response)
                    
                    # 执行工具
                    try:
                        result = await self._execute_single_tool(tool_call)
                        if result.success and result.metadata.get("needs_confirmation") is not True:
                            logger.info(f"{self.config.name} tool '{tool_call.name}': SUCCESS")
                        else:
                            logger.warning(f"{self.config.name} tool '{tool_call.name}': FAILED - {result.error}")

                        # 检查是否需要权限确认（从metadata中检查）
                        if result.success and result.metadata and result.metadata.get("needs_confirmation"):
                            logger.info(f"Execution interrupted for tool '{tool_call.name}' pending permission.")
                            current_response.messages = [m for m in messages if m["role"] != "system"]
                            # 配置工具权限路由
                            current_response.routing = {
                                "type": "permission_confirmation",
                                "tool_name": tool_call.name,
                                "params": tool_call.params,
                                "permission_level": result.metadata["permission_level"]
                            }
                            # Yield权限确认事件
                            yield StreamEvent(type=StreamEventType.PERMISSION_REQUIRED, agent=self.config.name, data=current_response)
                            return # 中断执行，等待权限确认

                        # 更新工具记录
                        tool_history.append({"tool": tool_call.name, "params": tool_call.params, "round": round_num + 1, "result": result.to_dict()})
                        current_response.tool_calls = tool_history
                        yield StreamEvent(type=StreamEventType.TOOL_RESULT, agent=self.config.name, data=current_response)
                        
                        # 检查Agent路由
                        if tool_call.name == "call_subagent" and result.success and isinstance(result.data, dict) and result.data.get("_is_routing_instruction"):
                            current_response.routing = {
                                "type": "subagent",
                                "target": result.data.get("_route_to"),
                                "instruction": result.data.get("instruction"),
                                "from_agent": self.config.name
                            }
                            current_response.messages = [m for m in messages if m["role"] != "system"]
                            # Yield Agent路由事件
                            yield StreamEvent(type=StreamEventType.COMPLETE, agent=self.config.name, data=current_response)
                            return # 中断执行，进行Agent路由

                    except Exception as tool_error:
                        # 工具执行异常
                        logger.exception(f"Tool {tool_call.name} exception: {tool_error}")
                        result = ToolResult(success=False, error=f"Tool exception: {str(tool_error)}")
                        tool_history.append({"tool": tool_call.name, "params": tool_call.params, "round": round_num + 1, "result": result.to_dict()})
                        current_response.tool_calls = tool_history
                    
                    # 格式化工具结果（无论成功失败）
                    tool_results_xml.append(format_result(tool_call.name, result.to_dict()))

                if tool_results_xml:
                    messages.append({"role": "user", "content": "\n".join(tool_results_xml)})
            
            # 格式化最终响应
            formatted_response = self.format_final_response(final_content, tool_history)
            current_response.content = formatted_response
            current_response.metadata["tool_rounds"] = self.tool_call_count
            current_response.messages = [m for m in messages if m["role"] != "system"] # 返回完整对话历史（不含系统提示词）
            
            yield StreamEvent(type=StreamEventType.COMPLETE, agent=self.config.name, data=current_response)
            
        except Exception as e:
            logger.exception(f"Unexpected error in {self.config.name}: {e}")
            current_response.success = False
            current_response.content = f"Agent execution failed: {str(e)}"
            current_response.messages = [m for m in messages if m["role"] != "system"] if messages else []
            yield StreamEvent(type=StreamEventType.ERROR, agent=self.config.name, data=current_response)

    async def execute(
        self,
        instruction: str,
        context: Optional[Dict[str, Any]] = None,
        external_history: Optional[List[Dict]] = None,
        pending_tool_result: Optional[Tuple[str, ToolResult]] = None
    ) -> AgentResponse:
        """
        批量执行Agent任务（支持恢复）
        
        Args:
            instruction: 用户指令
            context: 上下文信息
            external_history: 外部提供的历史记录（用于恢复）
            pending_tool_result: 待处理的工具结果（用于恢复）
        """
        final_response = None
        async for event in self._execute_generator(
            instruction, context, external_history, pending_tool_result, streaming_tokens=False
        ):
            if event.type == StreamEventType.PERMISSION_REQUIRED:
                return event.data  # Immediately return on permission request
            
            if event.data:
                final_response = event.data

        return final_response or AgentResponse(success=False, content="Execution failed to produce a final state.")
    
    async def stream(
        self,
        instruction: str,
        context: Optional[Dict[str, Any]] = None,
        external_history: Optional[List[Dict]] = None,
        pending_tool_result: Optional[Tuple[str, ToolResult]] = None
    ) -> AsyncGenerator[StreamEvent, None]:
        """
        流式执行Agent任务（支持恢复）

        Args:
            instruction: 用户指令
            context: 上下文信息
            external_history: 外部提供的历史记录（用于恢复）
            pending_tool_result: 待处理的工具结果（用于恢复）
        """
        async for event in self._execute_generator(
            instruction, context, external_history, pending_tool_result, streaming_tokens=True
        ):
            yield event

def create_agent_config(
    name: str,
    description: str,
    **kwargs
) -> AgentConfig:
    """创建Agent配置的便捷函数"""
    return AgentConfig(name=name, description=description, **kwargs)