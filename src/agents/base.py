"""
Base Agent抽象类
提供Agent的基础功能：工具调用循环、流式输出、统一完成判断等
增强功能：错误处理和重试机制、messages返回、权限检查
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional, AsyncGenerator, Union
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
    messages: List[Dict] = field(default_factory=list)  # 新增：完整对话历史
    pending_confirmation: Optional[Dict] = None  # 新增：待确认的工具调用


class BaseAgent(ABC):
    """
    所有Agent的基类
    
    核心功能：
    1. 统一的工具调用循环（最多N轮）
    2. 流式输出支持（LLM流式，工具批量）
    3. 统一的完成判断（无工具调用即完成）
    4. 思考模型兼容（记录reasoning_content）
    5. 简单的错误处理和重试机制
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
    
    async def _prepare_context_with_task_plan(self, user_context: Optional[Dict]) -> Dict:
        """
        准备context：
        1. 所有agent都注入task_plan（如果存在）
        2. Lead Agent额外注入artifacts列表
        """
        context = user_context or {}
        
        try:
            from tools.implementations.artifact_ops import _artifact_store
            
            # 1. 注入task_plan（所有agent都需要）
            task_plan = _artifact_store.get("task_plan")
            if task_plan:
                context["task_plan_content"] = task_plan.content
                context["task_plan_version"] = task_plan.current_version
                context["task_plan_updated"] = task_plan.updated_at.isoformat()
                logger.debug(f"{self.config.name} loaded task_plan (v{task_plan.current_version})")
            
            # 2. Lead Agent专属：注入完整的artifacts列表
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
        """执行单个工具调用（增强：权限检查）"""
        if not self.toolkit:
            return ToolResult(success=False, error="No toolkit available")
        
        tool = self.toolkit.get_tool(tool_call.name)
        if not tool:
            return ToolResult(success=False, error=f"Tool '{tool_call.name}' not found")
        
        # 权限检查：CONFIRM和RESTRICTED级别需要确认
        if tool.permission in [ToolPermission.CONFIRM, ToolPermission.RESTRICTED]:
            logger.info(f"Tool '{tool_call.name}' requires {tool.permission.value} permission")
            
            # 返回特殊的"需要确认"信号
            return ToolResult(
                success=True,
                data={
                    "_needs_confirmation": True,
                    "_tool_name": tool_call.name,
                    "_params": tool_call.params,
                    "_permission_level": tool.permission.value,
                    "_reason": f"Tool '{tool_call.name}' requires {tool.permission.value} permission"
                },
                metadata={"is_permission_request": True}
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
        
        # 不应该到达这里，但为了安全
        if last_error:
            raise last_error
        raise RuntimeError("LLM call failed without specific error")

    async def _execute_generator(
        self,
        user_input: str,
        context: Optional[Dict[str, Any]] = None,
        streaming_tokens: bool = False
    ) -> AsyncGenerator[StreamEvent, None]:
        """
        核心执行生成器，统一的执行逻辑（带错误处理）
        
        Args:
            user_input: 用户输入
            context: 执行上下文
            streaming_tokens: 是否流式输出LLM tokens
            
        Yields:
            StreamEvent: 执行过程中的各种事件
        """
        # 初始化响应对象
        current_response = AgentResponse(
            success=True,
            content="",
            tool_calls=[],
            reasoning_content=None,
            messages=[],  # 初始化messages
            metadata={
                "agent": self.config.name,
                "model": self.config.model,
                "started_at": datetime.now().isoformat()
            }
        )
        
        # Yield开始事件
        yield StreamEvent(
            type=StreamEventType.START,
            agent=self.config.name,
            data=current_response
        )
        
        try:  # 最外层异常处理
            # 重置状态
            self.tool_call_count = 0
            tool_history = []
            
            # 准备context
            enhanced_context = await self._prepare_context_with_task_plan(context)
            
            # 构建系统提示词
            system_prompt = self.build_system_prompt(enhanced_context)
            
            # 添加工具使用说明
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
            
            # 主循环开始
            final_content = ""
            accumulated_token_usage = {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0
            }
            
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
                try:
                    if streaming_tokens:
                        # 流式模式：逐token处理
                        response_content = ""
                        reasoning_content = None
                        token_usage = {}
                        
                        stream = await self._call_llm_with_retry(messages, streaming=True)
                        
                        async for chunk in stream:
                            # 累积content
                            if hasattr(chunk, 'content') and chunk.content:
                                response_content += chunk.content
                                current_response.content = response_content
                            
                            # 累积reasoning_content（如果有）
                            if hasattr(chunk, 'additional_kwargs'):
                                if 'reasoning_content' in chunk.additional_kwargs:
                                    chunk_reasoning = chunk.additional_kwargs.get('reasoning_content', '')
                                    if chunk_reasoning:
                                        # 第一次出现reasoning_content时初始化为空字符串
                                        if reasoning_content is None:
                                            reasoning_content = ""
                                        reasoning_content += chunk_reasoning
                                        current_response.reasoning_content = reasoning_content
                            
                            # 获取token_usage（通常在最后一个chunk）
                            if hasattr(chunk, 'response_metadata') and chunk.response_metadata:
                                if 'token_usage' in chunk.response_metadata:
                                    token_usage = chunk.response_metadata['token_usage']
                            
                            # Yield LLM chunk事件
                            yield StreamEvent(
                                type=StreamEventType.LLM_CHUNK,
                                agent=self.config.name,
                                data=current_response
                            )
                        
                        # 更新token统计
                        if token_usage:
                            accumulated_token_usage['input_tokens'] += token_usage.get('input_tokens', 0)
                            accumulated_token_usage['output_tokens'] += token_usage.get('output_tokens', 0)
                            accumulated_token_usage['total_tokens'] += token_usage.get('total_tokens', 0)
                            current_response.token_usage = accumulated_token_usage.copy()
                        
                    else:
                        # 批量模式：一次性获取完整响应
                        response = await self._call_llm_with_retry(messages, streaming=False)
                        
                        response_content = response.content
                        current_response.content = response_content
                        
                        # 获取reasoning_content
                        reasoning_content = None
                        if hasattr(response, 'additional_kwargs'):
                            reasoning_content = response.additional_kwargs.get('reasoning_content')
                            if reasoning_content:
                                current_response.reasoning_content = reasoning_content
                        
                        # 获取token_usage
                        token_usage = {}
                        if hasattr(response, 'response_metadata'):
                            token_usage = response.response_metadata.get('token_usage', {})
                            if token_usage:
                                accumulated_token_usage['input_tokens'] += token_usage.get('input_tokens', 0)
                                accumulated_token_usage['output_tokens'] += token_usage.get('output_tokens', 0)
                                accumulated_token_usage['total_tokens'] += token_usage.get('total_tokens', 0)
                                current_response.token_usage = accumulated_token_usage.copy()
                        
                        # Yield完整LLM响应事件
                        yield StreamEvent(
                            type=StreamEventType.LLM_COMPLETE,
                            agent=self.config.name,
                            data=current_response
                        )
                    
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
                    
                    # LLM调用失败是致命的，终止执行
                    return
                
                if self.config.debug:
                    if reasoning_content:
                        logger.debug(f"[{self.config.name} Round {round_num + 1}] Reasoning:\n{reasoning_content}")
                    input_tokens = token_usage.get('input_tokens', 0)
                    output_tokens = token_usage.get('output_tokens', 0)
                    logger.debug(f"[{self.config.name} Round {round_num + 1}] LLM Response (input: {input_tokens}, output: {output_tokens}):\n{response_content}")
                    logger.debug(f"[{self.config.name} Round {round_num + 1}] LLM Raw Response (input: {input_tokens}, output: {output_tokens}):\n{repr(response_content)}")
                
                # 解析工具调用
                tool_calls = parse_tool_calls(response_content)
                
                # 判断是否完成
                if not tool_calls or round_num >= self.config.max_tool_rounds:
                    final_content = response_content
                    break
                
                # 执行工具调用
                tool_results = []
                for tool_call in tool_calls:
                    self.tool_call_count += 1
                    logger.info(f"{self.config.name} calling tool: {tool_call.name}")

                    # 更新工具调用信息
                    tool_record = {
                        "tool": tool_call.name,
                        "params": tool_call.params,
                        "round": round_num + 1
                    }
                    
                    # Yield工具开始事件
                    current_response.metadata["current_tool"] = tool_call.name
                    yield StreamEvent(
                        type=StreamEventType.TOOL_START,
                        agent=self.config.name,
                        data=current_response
                    )
                    
                    # 执行工具
                    try:
                        result = await self._execute_single_tool(tool_call)
                        
                        # 检查是否需要权限确认
                        if result.success and result.data and result.data.get("_needs_confirmation"):
                            logger.info(f"Tool '{tool_call.name}' requires permission confirmation")
                            
                            # 设置待确认信息
                            current_response.pending_confirmation = {
                                "tool_name": tool_call.name,
                                "params": tool_call.params,
                                "permission_level": result.data.get("_permission_level"),
                                "reason": result.data.get("_reason")
                            }
                            
                            # Yield权限确认事件
                            yield StreamEvent(
                                type=StreamEventType.PERMISSION_REQUIRED,
                                agent=self.config.name,
                                data=current_response
                            )
                            
                            # 这里应该暂停等待确认，但为了向后兼容，我们继续执行
                            # 在实际的graph中会处理这个中断
                        
                        if result.success:
                            logger.info(f"{self.config.name} tool '{tool_call.name}': SUCCESS")
                        else:
                            logger.warning(f"{self.config.name} tool '{tool_call.name}': FAILED - {result.error}")
                            
                        # 更新工具记录
                        tool_record["result"] = result.to_dict()
                        tool_history.append(tool_record)
                        current_response.tool_calls = tool_history
                        
                        # Yield工具结果事件
                        current_response.metadata["last_tool_result"] = result.to_dict()
                        yield StreamEvent(
                            type=StreamEventType.TOOL_RESULT,
                            agent=self.config.name,
                            data=current_response
                        )
                        
                        # 检查路由（特殊工具）
                        if tool_call.name == "call_subagent" and result.success:
                            result_data = result.to_dict().get("data", {})
                            if result_data.get("_is_routing_instruction"):
                                # 设置路由信息
                                current_response.routing = {
                                    "target": result_data.get("_route_to"),
                                    "instruction": result_data.get("instruction"),
                                    "from_agent": self.config.name
                                }
                                current_response.metadata["needs_routing"] = True
                                current_response.metadata["rounds_completed"] = round_num + 1
                                
                                # 返回完整的messages历史
                                current_response.messages = messages.copy()
                                # Yield完成事件（带路由）
                                yield StreamEvent(
                                    type=StreamEventType.COMPLETE,
                                    agent=self.config.name,
                                    data=current_response
                                )
                                return  # 提前结束生成器
                        
                    except Exception as tool_error:
                        # 工具执行异常
                        logger.exception(f"Tool {tool_call.name} exception: {tool_error}")
                        
                        # 创建失败结果
                        result = ToolResult(
                            success=False,
                            error=f"Tool exception: {str(tool_error)}"
                        )
                        tool_record["result"] = result.to_dict()
                        tool_history.append(tool_record)
                        current_response.tool_calls = tool_history
                    
                    # 格式化工具结果（无论成功失败）
                    xml_result = format_result(tool_call.name, result.to_dict())
                    tool_results.append(xml_result)
                
                # 更新消息历史
                messages.append({"role": "assistant", "content": response_content})
                if tool_results:
                    messages.append({"role": "user", "content": "\n".join(tool_results)})
            
            # 格式化最终响应
            formatted_response = self.format_final_response(final_content, tool_history)
            
            # 更新最终响应
            current_response.content = formatted_response
            current_response.metadata["tool_rounds"] = self.tool_call_count
            current_response.messages = messages.copy()  # 返回完整对话历史
            
            yield StreamEvent(
                type=StreamEventType.COMPLETE,
                agent=self.config.name,
                data=current_response
            )
            
        except Exception as e:
            # 最外层异常捕获（未预期的错误）
            logger.exception(f"Unexpected error in {self.config.name}: {e}")
            current_response.success = False
            current_response.content = f"Agent execution failed: {str(e)}"
            current_response.messages = messages.copy() if 'messages' in locals() else []
            
            # Yield错误事件
            yield StreamEvent(
                type=StreamEventType.ERROR,
                agent=self.config.name,
                data=current_response
            )

    async def execute(
        self,
        user_input: str,
        context: Optional[Dict[str, Any]] = None
    ) -> AgentResponse:
        """
        批量执行Agent任务
        
        内部使用生成器收集所有事件，返回最终的完整响应
        
        Args:
            user_input: 用户输入/任务指令
            context: 执行上下文
            
        Returns:
            AgentResponse: 完整的响应对象
        """
        last_response = None
        
        # 遍历生成器
        async for event in self._execute_generator(user_input, context, streaming_tokens=False):
            # 更新最新响应
            if event.data:
                last_response = event.data
            
            # 如果是完成或错误事件，直接返回
            if event.type in [StreamEventType.COMPLETE, StreamEventType.ERROR]:
                return event.data
        
        # 异常情况：没有COMPLETE或ERROR事件
        return AgentResponse(
            success=False,
            content=f"Execution interrupted, last response: \n{last_response}",
            messages=[]
        )
    
    async def stream(
        self,
        user_input: str,
        context: Optional[Dict[str, Any]] = None
    ) -> AsyncGenerator[StreamEvent, None]:
        """
        流式执行Agent任务
        
        实时yield执行事件，调用者可以处理各种类型的事件
        
        Args:
            user_input: 用户输入/任务指令
            context: 执行上下文
            
        Yields:
            StreamEvent: 各种执行事件，data始终是AgentResponse对象
        """
        async for event in self._execute_generator(user_input, context, streaming_tokens=True):
            yield event
    
    async def reset(self):
        """重置Agent状态"""
        self.tool_call_count = 0
        self.conversation_history.clear()
        logger.debug(f"{self.config.name} state reset")


def create_agent_config(
    name: str,
    description: str,
    **kwargs
) -> AgentConfig:
    """创建Agent配置的便捷函数"""
    return AgentConfig(name=name, description=description, **kwargs)