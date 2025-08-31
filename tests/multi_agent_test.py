"""
Multi-Agent系统使用示例
展示如何配置和使用优化后的Agent系统
"""

import asyncio
from typing import Dict, Any

# Agent相关
from agents.lead_agent import LeadAgent, SubAgent
from agents.search_agent import SearchAgent
from agents.crawl_agent import CrawlAgent

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

logger = get_logger("AgentSystemTest")
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
        
        # 创建带debug的配置
        from agents.base import AgentConfig
        config = AgentConfig(
            name="lead_agent",
            description="Task coordinator and information integrator",
            model="qwen-plus",
            temperature=0.7,
            max_tool_rounds=5,
            streaming=True,
            debug=self.debug  # 使用系统的debug设置
        )
        
        # 创建Lead Agent
        lead = LeadAgent(config=config, toolkit=toolkit)
        return lead
    
    def _setup_search_agent(self) -> SearchAgent:
        """配置Search Agent"""
        # 创建Search Agent的工具包
        toolkit = self.registry.create_agent_toolkit(
            "search_agent",
            tool_names=["web_search"]
        )
        
        # 创建带debug的配置
        from agents.base import AgentConfig
        config = AgentConfig(
            name="search_agent",
            description="Web search and information retrieval specialist",
            model="qwen-plus",
            temperature=0.5,
            max_tool_rounds=3,
            streaming=True,
            debug=self.debug  # 使用系统的debug设置
        )
        
        # 创建Search Agent
        search = SearchAgent(config=config, toolkit=toolkit)
        return search
    
    def _setup_crawl_agent(self) -> CrawlAgent:
        """配置Crawl Agent"""
        # 创建Crawl Agent的工具包
        toolkit = self.registry.create_agent_toolkit(
            "crawl_agent",
            tool_names=["web_fetch"]
        )
        
        # 创建带debug的配置
        from agents.base import AgentConfig
        config = AgentConfig(
            name="crawl_agent",
            description="Web content extraction and cleaning specialist",
            model="qwen-plus",
            temperature=0.3,
            max_tool_rounds=2,
            streaming=True,
            debug=self.debug  # 使用系统的debug设置
        )
        
        # 创建Crawl Agent
        crawl = CrawlAgent(config=config, toolkit=toolkit)
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
            description="Extracts content from specific web pages",
            capabilities=[
                "Deep content extraction from URLs",
                "Content cleaning and filtering",
                "Anti-crawling detection"
            ]
        ))
        
        logger.info("Registered 2 sub-agents in Lead Agent")
    
    async def process_task(
        self,
        task: str,
        complexity: str = "auto"
    ) -> Dict[str, Any]:
        """
        处理任务
        
        Args:
            task: 任务描述
            complexity: 复杂度提示（simple/moderate/complex/auto）
            
        Returns:
            处理结果
        """
        logger.info(f"Processing task: {task[:100]}...")
        
        # 根据复杂度构建不同的指令
        if complexity == "simple":
            instruction = f"Please answer this directly: {task}"
        elif complexity == "moderate":
            instruction = f"Please handle this task (create task_plan if helpful): {task}"
        elif complexity == "complex":
            instruction = f"This is a complex task. Please create a task_plan first, then execute: {task}"
        else:  # auto
            instruction = f"Please analyze and handle this task appropriately: {task}"
        
        # 执行任务
        response = await self.lead_agent.execute(instruction)
        
        return {
            "success": True,
            "response": response.content,
            "tool_calls": len(response.tool_calls),
            "metadata": response.metadata
        }


async def main():
    """
    测试Multi-Agent系统
    """
    print("\n" + "="*60)
    print("🤖 Multi-Agent System Demo")
    print("="*60)
    
    # 初始化系统（开启debug模式）
    system = MultiAgentSystem(debug=True)  # 👈 设置debug=True
    
    print("🔧 Debug mode: ENABLED")  # 提示debug模式已开启
    
    # 测试不同复杂度的任务
    test_tasks = [
        {
            "task": "What is the capital of France?",
            "complexity": "simple",
            "description": "Simple factual question"
        },
        {
            "task": "Find recent information about quantum computing breakthroughs in 2024",
            "complexity": "moderate",
            "description": "Moderate search task"
        },
        {
            "task": (
                "Research and analyze the impact of AI on healthcare in 2024. "
                "Include recent developments, key players, and future trends."
            ),
            "complexity": "complex",
            "description": "Complex research task"
        }
    ]
    
    for i, test in enumerate(test_tasks, 1):
        print(f"\n{'='*60}")
        print(f"Test {i}: {test['description']}")
        print(f"Task: {test['task'][:100]}...")
        print(f"Complexity: {test['complexity']}")
        print("-"*60)
        
        try:
            result = await system.process_task(
                test["task"],
                complexity=test["complexity"]
            )
            
            print(f"✅ Task completed successfully")
            print(f"Tool calls: {result['tool_calls']}")
            print(f"Response preview:")
            print(result["response"][:500] + "..." if len(result["response"]) > 500 else result["response"])
            
        except Exception as e:
            print(f"❌ Task failed: {e}")
    
    print("\n" + "="*60)
    print("Demo completed!")
    print("="*60)


if __name__ == "__main__":
    # 运行示例
    asyncio.run(main())