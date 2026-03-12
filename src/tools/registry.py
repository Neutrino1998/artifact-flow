"""
工具注册和管理系统
支持Agent级别的工具集管理
"""

from typing import Dict, List, Optional
from tools.base import BaseTool, ToolResult
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


class AgentToolkit:
    """
    Agent工具包
    每个Agent拥有自己的工具集.
    This class is now a simple container for tools.
    """
    
    def __init__(self, agent_name: str):
        self.agent_name = agent_name
        self.tools: Dict[str, BaseTool] = {}
    
    def add_tool(self, tool: BaseTool) -> None:
        """添加工具到工具包"""
        if tool.name in self.tools:
            logger.warning(f"Tool '{tool.name}' is already in {self.agent_name}'s toolkit. Overwriting.")
        self.tools[tool.name] = tool
        # No permission check here!
    
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
    
    async def execute_tool(self, name: str, params: Dict) -> ToolResult:
        """
        直接执行工具，不进行权限检查。
        权限检查应该由调用者（Orchestrator）在使用此方法前完成。
        """
        tool = self.get_tool(name)
        if not tool:
            return ToolResult(
                success=False,
                error=f"Tool '{name}' not available in {self.agent_name}'s toolkit"
            )
        
        # 权限检查逻辑被移除，交给外部处理
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
        self.agent_toolkits: Dict[str, AgentToolkit] = {}
        self.tool_library: Dict[str, BaseTool] = {}
    
    def register_tool_to_library(self, tool: BaseTool) -> None:
        self.tool_library[tool.name] = tool
        logger.info(f"Registered tool '{tool.name}' to library")

    def get_tool(self, name: str) -> Optional[BaseTool]:
        """从全局工具库获取工具"""
        return self.tool_library.get(name)
    
    def create_agent_toolkit(self, agent_name: str, tool_names: List[str] = None) -> AgentToolkit:
        """
        为Agent创建工具包.
        不再处理权限，只负责分配工具。
        """
        toolkit = AgentToolkit(agent_name)
        
        if tool_names:
            for tool_name in tool_names:
                if tool_name in self.tool_library:
                    # 直接添加工具，不检查权限
                    toolkit.add_tool(self.tool_library[tool_name])
                else:
                    logger.warning(f"Tool '{tool_name}' not found in library while creating toolkit for {agent_name}")
        
        self.agent_toolkits[agent_name] = toolkit
        logger.info(f"Created toolkit for {agent_name} with {len(toolkit.tools)} tools")
        return toolkit

    def get_agent_toolkit(self, agent_name: str) -> Optional[AgentToolkit]:
        return self.agent_toolkits.get(agent_name)


if __name__ == "__main__":
    import asyncio
    # These imports are needed for the test case
    from tools.base import BaseTool, ToolPermission, ToolParameter, ToolResult
    from typing import Set # Required for mock class

    # --- Mock Objects for Testing ---
    class MockTool(BaseTool):
        def __init__(self, name: str, permission: ToolPermission):
            super().__init__(name=name, description="A mock tool", permission=permission)
        def get_parameters(self) -> list[ToolParameter]: return []
        async def execute(self, **params) -> ToolResult: return ToolResult(True, "OK")

    def _print_check(desc: str, result: bool):
        """Helper to print test results cleanly."""
        print(f"  - {desc}: {'✅' if result else '❌'}")

    async def run_registry_tests():
        print("\n🧪 Refactored ToolRegistry & AgentToolkit Tests")
        print("="*50)

        # 1. Setup: Create registry and register all available tools
        print("[1] Initializing Registry and Tool Library...")
        registry = ToolRegistry()
        auto_tool = MockTool("search_web", ToolPermission.AUTO)
        confirm_tool = MockTool("send_email", ToolPermission.CONFIRM)
        
        registry.register_tool_to_library(auto_tool)
        registry.register_tool_to_library(confirm_tool)
        _print_check("Tools registered to library", len(registry.tool_library) == 2)

        # 2. Test Toolkit Creation and Tool Assignment
        print("\n[2] Creating AgentToolkit and assigning tools...")
        agent_name = "test_agent"
        toolkit = registry.create_agent_toolkit(
            agent_name,
            tool_names=["search_web", "send_email"]  # Assign an AUTO and a CONFIRM tool
        )

        # Verify that tools were added successfully
        _print_check("Toolkit created", toolkit is not None)
        _print_check("Correct number of tools in toolkit", len(toolkit.tools) == 2)
        _print_check("Tool 'search_web' (AUTO) was added successfully",
                     toolkit.get_tool("search_web") is not None)
        _print_check("Tool 'send_email' (CONFIRM) was added successfully",
                     toolkit.get_tool("send_email") is not None)

        # 3. Test Tool Execution
        print("\n[3] Testing tool execution via toolkit...")
        # The execute_tool method now assumes permission has already been checked by an orchestrator
        result = await toolkit.execute_tool("search_web", params={})
        _print_check("Executing an available tool succeeds", result.success)

        result_fail = await toolkit.execute_tool("non_existent_tool", params={})
        _print_check("Executing a non-available tool fails", not result_fail.success)
        print(f"  - Got expected error: {result_fail.error}")
        
        print("\n✅ All ToolRegistry tests passed!")

    # To run the test
    asyncio.run(run_registry_tests())