"""
Base Agent抽象类
提供Agent的基础功能：工具调用循环、流式输出、统一完成判断等
增强功能：错误处理和重试机制、messages返回、权限检查、任务恢复与中断
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional, AsyncGenerator, Union, Tuple
from dataclasses import dataclass, field
from datetime import datetime
import asyncio

from utils.logger import get_logger
from tools.xml_parser import parse_tool_calls
from tools.prompt_generator import ToolPromptGenerator, format_result
from tools.registry import AgentToolkit
from core.events import StreamEventType, StreamEvent

logger = get_logger("ArtifactFlow")


@dataclass
class AgentConfig:
    """Agent配置"""
    name: str
    description: str

    # 元信息（用于注册到Lead、创建toolkit）
    capabilities: List[str] = field(default_factory=list)
    required_tools: List[str] = field(default_factory=list)

    # LLM配置
    model: str = "qwen-plus"
    temperature: float = 0.7
    max_tool_rounds: int = 3  # 最大工具调用轮数
    streaming: bool = True  # 是否默认流式输出

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
    1. 单轮 LLM 调用（工具循环由 Graph 控制）
    2. 流式输出支持
    3. 解析响应（工具调用 / subagent 路由 / 完成）
    4. 思考模型兼容（记录reasoning_content）
    5. 错误处理和重试机制

    职责边界：
    - Agent 只负责 LLM 调用和响应解析
    - 工具执行由 Graph 的 tool_execution_node 处理
    - 工具轮数限制由 Graph 层面控制（读取 agent.config.max_tool_rounds）
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
        单轮 LLM 调用生成器

        职责简化：
        1. 调用 LLM
        2. 解析响应（工具调用 / subagent 路由 / 完成）
        3. 设置 routing 返回，由 graph 处理工具执行

        Args:
            messages: 完整的消息列表（system + history + instruction + tool_result）
            is_resuming: 是否从工具执行恢复
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
            type=StreamEventType.AGENT_START,
            agent=self.config.name,
            data=current_response
        )

        try:
            # 根据 is_resuming 初始化交互历史
            if is_resuming:
                # 恢复时：最后一条消息是 tool_result，需要记录
                new_tool_interactions = [messages[-1]]
                logger.debug(f"Resuming: initialized tool_interactions with tool result")
            else:
                # 正常执行：从空开始
                new_tool_interactions = []

            logger.debug(f"[{self.config.name}] Messages:\n{self._format_messages_for_debug(messages)}")

            # ========== 单轮 LLM 调用 ==========
            response_content = ""
            reasoning_content = None
            token_usage = {}

            try:
                if streaming_tokens:
                    # 流式模式：逐token处理
                    # 新的 astream 返回 dict 格式：
                    # {"type": "content", "content": "..."}
                    # {"type": "reasoning", "content": "..."}
                    # {"type": "usage", "token_usage": {...}}
                    # {"type": "final", "content": "...", "reasoning_content": "...", "token_usage": {...}}
                    stream = await self._call_llm_with_retry(messages, streaming=True)
                    async for chunk in stream:
                        chunk_type = chunk.get("type")

                        if chunk_type == "content":
                            # 累积 content
                            response_content += chunk["content"]
                            current_response.content = response_content

                            # Yield LLM chunk事件
                            yield StreamEvent(
                                type=StreamEventType.LLM_CHUNK,
                                agent=self.config.name,
                                data=current_response
                            )

                        elif chunk_type == "reasoning":
                            # 累积 reasoning_content
                            if reasoning_content is None:
                                reasoning_content = ""
                            reasoning_content += chunk["content"]
                            current_response.reasoning_content = reasoning_content

                            # Yield LLM chunk事件（reasoning 也需要流式输出）
                            yield StreamEvent(
                                type=StreamEventType.LLM_CHUNK,
                                agent=self.config.name,
                                data=current_response
                            )

                        elif chunk_type == "usage":
                            # 获取 token_usage
                            token_usage = chunk["token_usage"]

                        elif chunk_type == "final":
                            # 最终响应（用于确保数据完整性）
                            if not response_content and chunk.get("content"):
                                response_content = chunk["content"]
                                current_response.content = response_content
                            if not reasoning_content and chunk.get("reasoning_content"):
                                reasoning_content = chunk["reasoning_content"]
                                current_response.reasoning_content = reasoning_content
                            if not token_usage and chunk.get("token_usage"):
                                token_usage = chunk["token_usage"]

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
                    current_response.token_usage = token_usage.copy()

            except Exception as llm_error:
                # LLM调用失败
                logger.error(f"LLM call failed after retries: {llm_error}")
                current_response.success = False
                current_response.content = f"LLM call failed: {str(llm_error)}"
                yield StreamEvent(
                    type=StreamEventType.ERROR,
                    agent=self.config.name,
                    data=current_response
                )
                return

            # 日志
            if reasoning_content:
                logger.debug(f"[{self.config.name}] Reasoning:\n{reasoning_content}")
            input_tokens = token_usage.get('input_tokens', 0)
            output_tokens = token_usage.get('output_tokens', 0)
            logger.debug(f"[{self.config.name}] LLM Response (input: {input_tokens}, output: {output_tokens}):\n{response_content}")

            # 记录 assistant 响应到交互历史
            new_tool_interactions.append({"role": "assistant", "content": response_content})

            # ========== 解析响应 ==========
            tool_calls = parse_tool_calls(response_content)

            if tool_calls:
                # 有工具调用（只取第一个，因为限制单工具调用）
                tool_call = tool_calls[0]
                logger.debug(f"{self.config.name} requesting tool: '{tool_call.name}'")

                # 设置路由信息
                if tool_call.name == "call_subagent":
                    # 调用 execute() 验证参数
                    result = await self.toolkit.execute_tool("call_subagent", tool_call.params)
                    if result.success:
                        # 验证通过，设置 subagent 路由
                        current_response.routing = {
                            "type": "subagent",
                            "target": result.data["agent_name"],
                            "instruction": result.data["instruction"]
                        }
                    else:
                        # 验证失败，当作普通 tool_call 让 graph 层返回错误
                        current_response.routing = {
                            "type": "tool_call",
                            "tool_name": tool_call.name,
                            "params": tool_call.params
                        }
                else:
                    current_response.routing = {
                        "type": "tool_call",
                        "tool_name": tool_call.name,
                        "params": tool_call.params
                    }

                # Agent 本轮完成（带路由），实际执行由 graph 层处理
                current_response.tool_interactions = new_tool_interactions
                yield StreamEvent(
                    type=StreamEventType.AGENT_COMPLETE,
                    agent=self.config.name,
                    data=current_response
                )
                return

            # ========== 无工具调用 → 完成 ==========
            current_response.tool_interactions = new_tool_interactions

            # 格式化最终响应
            final_response = self.format_final_response(
                current_response.content,
                current_response.tool_calls
            )
            current_response.content = final_response

            yield StreamEvent(
                type=StreamEventType.AGENT_COMPLETE,
                agent=self.config.name,
                data=current_response
            )

        except Exception as e:
            logger.exception(f"Unexpected error in {self.config.name}: {e}")
            current_response.success = False
            current_response.content = f"Agent execution failed: {str(e)}"
            current_response.tool_interactions = new_tool_interactions if 'new_tool_interactions' in locals() else []
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
        批量执行Agent任务（单轮 LLM 调用）

        Args:
            messages: 完整的消息列表
            is_resuming: 是否从工具执行恢复
        """
        final_response = None
        async for event in self._execute_generator(messages, is_resuming, streaming_tokens=False):
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
        流式执行Agent任务（单轮 LLM 调用）

        Args:
            messages: 完整的消息列表
            is_resuming: 是否从工具执行恢复
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