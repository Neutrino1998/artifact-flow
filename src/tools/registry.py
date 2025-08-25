"""
工具注册和管理系统
负责工具的注册、查找、权限检查等
"""

from typing import Dict, List, Optional, Type, Set
from .base import BaseTool, ToolPermission, ToolResult
from utils.logger import get_logger

logger = get_logger("ToolRegistry")


class ToolRegistry:
    """
    工具注册中心
    管理所有可用的工具
    """
    
    def __init__(self):
        """初始化注册中心"""
        self._tools: Dict[str, BaseTool] = {}
        self._tool_classes: Dict[str, Type[BaseTool]] = {}
        
    def register(self, tool: BaseTool) -> None:
        """
        注册工具实例
        
        Args:
            tool: 工具实例
        """
        if tool.name in self._tools:
            logger.warning(f"Tool '{tool.name}' already registered, overwriting")
        
        self._tools[tool.name] = tool
        logger.info(f"Registered tool: {tool.name} (permission: {tool.permission.value})")
    
    def register_class(self, name: str, tool_class: Type[BaseTool]) -> None:
        """
        注册工具类（延迟实例化）
        
        Args:
            name: 工具名称
            tool_class: 工具类
        """
        self._tool_classes[name] = tool_class
        logger.debug(f"Registered tool class: {name}")
    
    def get(self, name: str) -> Optional[BaseTool]:
        """
        获取工具实例
        
        Args:
            name: 工具名称
            
        Returns:
            工具实例，不存在则返回None
        """
        # 先查找已实例化的工具
        if name in self._tools:
            return self._tools[name]
        
        # 尝试从类创建实例
        if name in self._tool_classes:
            tool_class = self._tool_classes[name]
            tool = tool_class(name=name, description=f"Auto-created {name}")
            self.register(tool)
            return tool
        
        logger.warning(f"Tool '{name}' not found")
        return None
    
    def list_tools(
        self,
        permission: Optional[ToolPermission] = None,
        names_only: bool = False
    ) -> List:
        """
        列出工具
        
        Args:
            permission: 筛选指定权限级别的工具
            names_only: 是否只返回名称列表
            
        Returns:
            工具列表或名称列表
        """
        tools = list(self._tools.values())
        
        # 权限筛选
        if permission:
            tools = [t for t in tools if t.permission == permission]
        
        # 返回格式
        if names_only:
            return [t.name for t in tools]
        return tools
    
    def get_tools_for_agent(
        self,
        agent_name: str,
        allowed_permissions: Optional[Set[ToolPermission]] = None
    ) -> List[BaseTool]:
        """
        获取Agent可用的工具列表
        
        Args:
            agent_name: Agent名称
            allowed_permissions: 允许的权限级别集合
            
        Returns:
            工具列表
        """
        if allowed_permissions is None:
            # 默认权限：PUBLIC和NOTIFY
            allowed_permissions = {ToolPermission.PUBLIC, ToolPermission.NOTIFY}
        
        tools = []
        for tool in self._tools.values():
            if tool.permission in allowed_permissions:
                tools.append(tool)
        
        logger.debug(f"Agent '{agent_name}' has access to {len(tools)} tools")
        return tools
    
    async def execute_tool(
        self,
        name: str,
        params: Dict,
        check_permission: bool = True,
        user_confirmed: bool = False
    ) -> ToolResult:
        """
        执行工具
        
        Args:
            name: 工具名称
            params: 工具参数
            check_permission: 是否检查权限
            user_confirmed: 用户是否已确认（对于CONFIRM级别）
            
        Returns:
            执行结果
        """
        # 获取工具
        tool = self.get(name)
        if not tool:
            return ToolResult(
                success=False,
                error=f"Tool '{name}' not found"
            )
        
        # 权限检查
        if check_permission:
            if tool.permission == ToolPermission.RESTRICTED:
                return ToolResult(
                    success=False,
                    error=f"Tool '{name}' requires special authorization"
                )
            
            if tool.permission == ToolPermission.CONFIRM and not user_confirmed:
                return ToolResult(
                    success=False,
                    error=f"Tool '{name}' requires user confirmation",
                    metadata={"needs_confirmation": True}
                )
        
        # 执行工具
        logger.debug(f"Executing tool '{name}' with params: {params}")
        result = await tool(**params)
        
        # 记录结果
        if result.success:
            logger.info(f"Tool '{name}' executed successfully")
        else:
            logger.error(f"Tool '{name}' failed: {result.error}")
        
        return result
    
    def clear(self) -> None:
        """清空所有注册的工具"""
        self._tools.clear()
        self._tool_classes.clear()
        logger.info("Cleared all registered tools")
    
    def get_registry_info(self) -> Dict:
        """
        获取注册中心信息
        
        Returns:
            注册信息字典
        """
        permission_stats = {}
        for tool in self._tools.values():
            perm = tool.permission.value
            permission_stats[perm] = permission_stats.get(perm, 0) + 1
        
        return {
            "total_tools": len(self._tools),
            "total_classes": len(self._tool_classes),
            "permission_distribution": permission_stats,
            "tools": [tool.get_info() for tool in self._tools.values()]
        }


# 全局注册中心实例
_global_registry = ToolRegistry()


# 便捷函数
def register_tool(tool: BaseTool) -> None:
    """注册工具到全局注册中心"""
    _global_registry.register(tool)


def get_tool(name: str) -> Optional[BaseTool]:
    """从全局注册中心获取工具"""
    return _global_registry.get(name)


def list_tools(**kwargs) -> List:
    """列出全局注册中心的工具"""
    return _global_registry.list_tools(**kwargs)


def get_registry() -> ToolRegistry:
    """获取全局注册中心实例"""
    return _global_registry


async def execute_tool(name: str, params: Dict, **kwargs) -> ToolResult:
    """使用全局注册中心执行工具"""
    return await _global_registry.execute_tool(name, params, **kwargs)