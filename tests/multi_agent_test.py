"""
Multi-Agent系统使用示例
展示如何配置和使用优化后的Agent系统
"""

import asyncio
from typing import Dict, Any, Optional

# Agent相关
from agents.lead_agent import LeadAgent, SubAgent, create_lead_agent
from agents.search_agent import SearchAgent, create_search_agent
from agents.crawl_agent import CrawlAgent, create_crawl_agent

# 工具相关
from tools.registry import ToolRegistry, AgentToolkit
from tools.implementations.artifact_ops import (
    CreateArtifactTool, UpdateArtifactTool, 
    RewriteArtifactTool, ReadArtifactTool
)
from tools.implementations.call_subagent import CallSubagentTool
from tools.implementations.web_search import WebSearchTool
from tools.implementations.web_fetch import WebFetchTool

# 工具相关
from utils.logger import get_logger
from utils.logger import set_global_debug

logger = get_logger("ArtifactFlow")
# 一行代码启用所有logger的debug模式
set_global_debug(True)

class MultiAgentSystem:
    """
    多Agent系统的简单封装
    """
    
    def __init__(self, debug: bool = False):  # 添加debug参数
        """初始化多Agent系统"""
        self.debug = debug  # 保存debug设置
        
        # 创建工具注册中心
        self.registry = ToolRegistry()
        
        # 注册所有工具到库
        self._register_all_tools()
        
        # 创建各个Agent及其工具包
        self.lead_agent = self._setup_lead_agent()
        self.search_agent = self._setup_search_agent()
        self.crawl_agent = self._setup_crawl_agent()
        
        # 在Lead Agent中注册子Agent
        self._register_subagents()
        
        # 获取全局artifact store
        from tools.implementations.artifact_ops import _artifact_store
        self.artifact_store = _artifact_store

        logger.info("Multi-Agent System initialized")
    
    def _register_all_tools(self):
        """注册所有工具到工具库"""
        # Artifact操作工具
        self.registry.register_tool_to_library(CreateArtifactTool())
        self.registry.register_tool_to_library(UpdateArtifactTool())
        self.registry.register_tool_to_library(RewriteArtifactTool())
        self.registry.register_tool_to_library(ReadArtifactTool())
        
        # Agent调用工具
        self.registry.register_tool_to_library(CallSubagentTool())
        
        # 搜索和抓取工具
        self.registry.register_tool_to_library(WebSearchTool())
        self.registry.register_tool_to_library(WebFetchTool())
        
        logger.info(f"Registered {len(self.registry.tool_library)} tools")
    
    def _setup_lead_agent(self) -> LeadAgent:
        """配置Lead Agent"""
        # 创建Lead Agent的工具包
        toolkit = self.registry.create_agent_toolkit(
            "lead_agent",
            tool_names=[
                "create_artifact", "update_artifact", 
                "rewrite_artifact", "read_artifact",
                "call_subagent"
            ]
        )
        
        # 创建Lead Agent
        lead = create_lead_agent(toolkit)
        return lead
    
    def _setup_search_agent(self) -> SearchAgent:
        """配置Search Agent"""
        # 创建Search Agent的工具包
        toolkit = self.registry.create_agent_toolkit(
            "search_agent",
            tool_names=["web_search"]
        )
        
        # 创建Search Agent
        search = create_search_agent(toolkit)
        return search
    
    def _setup_crawl_agent(self) -> CrawlAgent:
        """配置Crawl Agent"""
        # 创建Crawl Agent的工具包
        toolkit = self.registry.create_agent_toolkit(
            "crawl_agent",
            tool_names=["web_fetch"]
        )
        
        # 创建Crawl Agent
        crawl = create_crawl_agent(toolkit)
        return crawl
    
    def _register_subagents(self):
        """在Lead Agent中注册子Agent"""
        # 注册Search Agent
        self.lead_agent.register_subagent(SubAgent(
            name="search_agent",
            description="Searches the web for information",
            capabilities=[
                "Web search with various filters",
                "Search refinement and optimization",
                "Information extraction from search results"
            ]
        ))
        
        # 注册Crawl Agent
        self.lead_agent.register_subagent(SubAgent(
            name="crawl_agent",
            description="Extracts detailed content from specific web pages. Requires explicit URL lists in instructions.",
            capabilities=[
                "Deep content scrapping from URLs",
                "Content cleaning and filtering",
                "⚠️ IMPORTANT: Instructions must include specific URLs (1~3 urls) to crawl"
            ]
        ))
        
        logger.info("Registered 2 sub-agents in Lead Agent")
    
    async def process_task(
        self,
        task: str,
        complexity: str = "auto",
        session_id: Optional[str] = None  # 👈 新增参数
    ) -> Dict[str, Any]:
        """
        处理任务
        
        Args:
            task: 任务描述
            complexity: 复杂度提示（simple/moderate/complex/auto）
            session_id: 会话ID（可选，不提供则自动创建）
            
        Returns:
            处理结果
        """
        # 创建或切换到指定session
        if session_id:
            self.artifact_store.set_session(session_id)
        else:
            session_id = self.artifact_store.create_session()
        
        logger.info(f"Processing task in session {session_id}: {task[:100]}...")
        
        # 根据复杂度构建不同的指令
        if complexity == "simple":
            instruction = f"Please answer this directly: {task}"
        elif complexity == "moderate":
            instruction = f"Please handle this task (create task_plan if helpful): {task}"
        elif complexity == "complex":
            instruction = f"This is a complex task. Please create a task_plan first, then execute: {task}"
        else:  # auto
            instruction = f"Please analyze and handle this task appropriately: {task}"
        
        # 🔄 主执行循环（支持多轮路由）
        max_routing_rounds = 5  # 防止无限循环
        routing_count = 0
        all_responses = []  # 收集所有响应用于最终总结
        
        current_instruction = instruction
        current_agent = self.lead_agent
        
        while routing_count < max_routing_rounds:
            # 执行当前agent
            response = await current_agent.execute(current_instruction)
            all_responses.append(response)
            
            # 检查是否需要路由
            if response.routing:
                routing_count += 1
                target = response.routing["target"]
                sub_instruction = response.routing["instruction"]
                
                logger.info(f"Routing to {target}: {sub_instruction[:100]}...")
                
                # 🎯 调用目标sub-agent
                if target == "search_agent":
                    sub_response = await self.search_agent.execute(sub_instruction)
                elif target == "crawl_agent":
                    sub_response = await self.crawl_agent.execute(sub_instruction)
                else:
                    logger.error(f"Unknown routing target: {target}")
                    break
                
                # 🔙 将sub-agent的结果格式化为工具结果，回传给lead_agent
                tool_result_xml = f"""<tool_result>
    <name>call_subagent</name>
    <agent>{target}</agent>
    <success>true</success>
    <data>
        {sub_response.content}
    </data>
</tool_result>"""
                
                # 继续让lead_agent处理结果
                current_instruction = tool_result_xml
                current_agent = self.lead_agent
                
            else:
                # 没有路由，执行完成
                break
        
        # 返回最终结果
        final_response = all_responses[-1] if all_responses else None
        
        return {
            "success": True,
            "session_id": session_id,  # 👈 新增
            "response": final_response.content if final_response else "",
            "tool_calls": sum(r.metadata.get("tool_rounds", 0) for r in all_responses),
            "routing_count": routing_count,
            "metadata": final_response.metadata if final_response else {},
            "artifacts": self.artifact_store.list_artifacts()  # 👈 新增，返回创建的artifacts
        }


async def main():
    """
    测试Multi-Agent系统
    """
    logger.info("="*60)
    logger.info("🤖 Multi-Agent System Demo")
    logger.info("="*60)
    
    # 初始化系统（开启debug模式）
    system = MultiAgentSystem(debug=True)  # 👈 设置debug=True
    logger.info("🔧 Debug mode: ENABLED")  # 提示debug模式已开启
    
    # 测试不同复杂度的任务
    test_tasks = [
        {
            "task": "What is the capital of France?",
            "complexity": "simple",
            "description": "Simple factual question"
        },
        {
            "task": "Find recent information about quantum computing breakthroughs in this year",
            "complexity": "moderate",
            "description": "Moderate search task"
        },
        {
            "task": (
                "Research and analyze the news of AI on healthcare this month. "
                "Compile into a report that include 4~5 news articles."
                "Each article should include its own subtitle and content (~500 words)."
                "Write in coherent paragraphs with citations. Do not use bullet points."
            ),
            "complexity": "complex",
            "description": "Complex research task"
        }
    ]
    
    for i, test in enumerate(test_tasks, 1):
        logger.info("="*60)
        logger.info(f"Test {i}: {test['description']}")
        logger.info(f"Task: {test['task'][:100]}...")
        logger.info(f"Complexity: {test['complexity']}")
        logger.info("-"*60)
        
        try:
            # 每个任务使用独立的session
            result = await system.process_task(
                test["task"],
                complexity=test["complexity"]
                # session_id 自动生成
            )
            
            logger.info(f"✅ Task completed successfully")
            logger.info(f"Session ID: {result['session_id']}")  # 👈 显示session
            logger.info(f"Tool calls: {result['tool_calls']}")
            logger.info(f"Response preview:\n{result['response'][:500] + '...' if len(result['response']) > 500 else result['response']}")
            logger.info(f"Artifacts created: {len(result.get('artifacts', []))}")  # 👈 显示artifacts
            
            # 打印创建的artifacts
            for artifact in result.get('artifacts', []):
                logger.info(f"  - {artifact['id']} ({artifact['content_type']}): {artifact['title']}")

        except Exception as e:
            logger.exception(f"❌ Task failed: {e}")
    
    logger.info("="*60)
    logger.info("Demo completed!")
    logger.info("="*60)


if __name__ == "__main__":
    # 运行示例
    asyncio.run(main())