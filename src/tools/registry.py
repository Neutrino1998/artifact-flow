"""
工具注册和管理系统
支持Agent级别的工具集管理
"""

from typing import Dict, List, Optional, Type, Set
from tools.base import BaseTool, ToolPermission, ToolResult
from utils.logger import get_logger

logger = get_logger("ToolRegistry")


class AgentToolkit:
    """
    Agent工具包
    每个Agent拥有自己的工具集
    """
    
    def __init__(self, agent_name: str):
        """
        初始化Agent工具包
        
        Args:
            agent_name: Agent名称
        """
        self.agent_name = agent_name
        self.tools: Dict[str, BaseTool] = {}
        self.allowed_permissions: Set[ToolPermission] = {
            ToolPermission.PUBLIC,
            ToolPermission.NOTIFY
        }
    
    def add_tool(self, tool: BaseTool) -> None:
        """
        添加工具到工具包
        
        Args:
            tool: 工具实例
        """
        # 检查权限
        if tool.permission not in self.allowed_permissions:
            logger.warning(
                f"Tool '{tool.name}' requires {tool.permission.value} permission, "
                f"which is not allowed for agent '{self.agent_name}'"
            )
            return
        
        self.tools[tool.name] = tool
        logger.info(f"Added tool '{tool.name}' to {self.agent_name}'s toolkit")
    
    def add_tools(self, tools: List[BaseTool]) -> None:
        """批量添加工具"""
        for tool in tools:
            self.add_tool(tool)
    
    def get_tool(self, name: str) -> Optional[BaseTool]:
        """获取工具"""
        return self.tools.get(name)
    
    def list_tools(self) -> List[BaseTool]:
        """列出所有工具"""
        return list(self.tools.values())
    
    def set_allowed_permissions(self, permissions: Set[ToolPermission]) -> None:
        """设置允许的权限级别"""
        self.allowed_permissions = permissions
        logger.info(
            f"Set allowed permissions for {self.agent_name}: "
            f"{[p.value for p in permissions]}"
        )
    
    async def execute_tool(
        self,
        name: str,
        params: Dict,
        check_permission: bool = True
    ) -> ToolResult:
        """
        执行工具
        
        Args:
            name: 工具名称
            params: 工具参数
            check_permission: 是否检查权限
            
        Returns:
            执行结果
        """
        tool = self.get_tool(name)
        if not tool:
            return ToolResult(
                success=False,
                error=f"Tool '{name}' not available in {self.agent_name}'s toolkit"
            )
        
        # 权限检查
        if check_permission and tool.permission not in self.allowed_permissions:
            return ToolResult(
                success=False,
                error=f"Tool '{name}' requires {tool.permission.value} permission"
            )
        
        # 执行工具
        logger.debug(f"{self.agent_name} executing tool '{name}' with params: {params}")
        result = await tool(**params)
        
        if result.success:
            logger.info(f"{self.agent_name} successfully executed '{name}'")
        else:
            logger.error(f"{self.agent_name} failed to execute '{name}': {result.error}")
        
        return result


class ToolRegistry:
    """
    工具注册中心
    管理不同Agent的工具包
    """
    
    def __init__(self):
        """初始化注册中心"""
        # Agent工具包
        self.agent_toolkits: Dict[str, AgentToolkit] = {}
        
        # 工具库（所有可用的工具实例）
        self.tool_library: Dict[str, BaseTool] = {}
    
    def register_tool_to_library(self, tool: BaseTool) -> None:
        """
        注册工具到工具库
        
        Args:
            tool: 工具实例
        """
        self.tool_library[tool.name] = tool
        logger.info(f"Registered tool '{tool.name}' to library")
    
    def create_agent_toolkit(
        self,
        agent_name: str,
        tool_names: List[str] = None,
        permissions: Set[ToolPermission] = None
    ) -> AgentToolkit:
        """
        为Agent创建工具包
        
        Args:
            agent_name: Agent名称
            tool_names: 工具名称列表
            permissions: 允许的权限级别
            
        Returns:
            Agent工具包
        """
        # 创建工具包
        toolkit = AgentToolkit(agent_name)
        
        # 设置权限
        if permissions:
            toolkit.set_allowed_permissions(permissions)
        
        # 添加工具
        if tool_names:
            for tool_name in tool_names:
                if tool_name in self.tool_library:
                    toolkit.add_tool(self.tool_library[tool_name])
                else:
                    logger.warning(f"Tool '{tool_name}' not found in library")
        
        # 保存工具包
        self.agent_toolkits[agent_name] = toolkit
        
        logger.info(
            f"Created toolkit for {agent_name} with {len(toolkit.tools)} tools"
        )
        
        return toolkit
    
    def get_agent_toolkit(self, agent_name: str) -> Optional[AgentToolkit]:
        """获取Agent的工具包"""
        return self.agent_toolkits.get(agent_name)


# 全局注册中心实例
_global_registry = ToolRegistry()


# 便捷函数
def get_registry() -> ToolRegistry:
    """获取全局注册中心"""
    return _global_registry


def register_tool(tool: BaseTool) -> None:
    """注册工具到工具库"""
    _global_registry.register_tool_to_library(tool)


def create_agent_toolkit(agent_name: str, **kwargs) -> AgentToolkit:
    """创建Agent工具包"""
    return _global_registry.create_agent_toolkit(agent_name, **kwargs)


def get_agent_toolkit(agent_name: str) -> Optional[AgentToolkit]:
    """获取Agent工具包"""
    return _global_registry.get_agent_toolkit(agent_name)
