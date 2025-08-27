"""
Agents模块
提供多智能体研究系统的Agent实现
"""

# 导入基类
from agents.base import (
    BaseAgent,
    AgentConfig,
    AgentResponse,
    create_agent_config
)

# 导入具体Agent实现
from agents.lead_agent import (
    LeadAgent,
    create_lead_agent
)

from agents.search_agent import (
    SearchAgent,
    create_search_agent
)

from agents.crawl_agent import (
    CrawlAgent,
    create_crawl_agent
)

# 版本信息
__version__ = "0.1.0"

# 公开接口
__all__ = [
    # 基类和数据结构
    "BaseAgent",
    "AgentConfig",
    "AgentResponse",
    "create_agent_config",
    
    # Lead Agent
    "LeadAgent",
    "create_lead_agent",
    
    # Search Agent
    "SearchAgent",
    "create_search_agent",
    
    # Crawl Agent
    "CrawlAgent",
    "create_crawl_agent",
    
    # 便捷函数
    "create_agent",
    "list_available_agents",
]
