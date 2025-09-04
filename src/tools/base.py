"""
工具系统基类
提供所有工具的基础接口和通用功能
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Union
from dataclasses import dataclass, field
from enum import Enum


class ToolPermission(Enum):
    """工具权限级别"""
    PUBLIC = "public"        # 直接执行（如搜索）
    NOTIFY = "notify"        # 执行后通知（如保存文件）
    CONFIRM = "confirm"      # 需用户确认（如发邮件）
    RESTRICTED = "restricted"  # 需特殊授权（如执行代码）


@dataclass
class ToolResult:
    """工具执行结果"""
    success: bool
    data: Any = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "metadata": self.metadata
        }


@dataclass
class ToolParameter:
    """工具参数定义"""
    name: str
    type: str  # "string", "integer", "boolean", "array", "object"
    description: str
    required: bool = True
    default: Any = None
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典（用于生成提示）"""
        return {
            "name": self.name,
            "type": self.type,
            "description": self.description,
            "required": self.required,
            "default": self.default
        }


class BaseTool(ABC):
    """
    所有工具的基类
    
    子类需要实现:
    - execute(): 执行工具的核心逻辑
    - get_parameters(): 返回工具参数定义
    """
    
    def __init__(
        self,
        name: str,
        description: str,
        permission: ToolPermission = ToolPermission.PUBLIC,
        **kwargs
    ):
        """
        初始化工具
        
        Args:
            name: 工具名称（唯一标识）
            description: 工具描述
            permission: 权限级别
            **kwargs: 其他配置参数
        """
        self.name = name
        self.description = description
        self.permission = permission
        self.config = kwargs
    
    @abstractmethod
    async def execute(self, **params) -> ToolResult:
        """
        执行工具
        
        Args:
            **params: 工具参数
            
        Returns:
            ToolResult: 执行结果
        """
        pass
    
    @abstractmethod
    def get_parameters(self) -> List[ToolParameter]:
        """
        获取工具参数定义
        
        Returns:
            参数列表
        """
        pass
    
    def validate_params(self, params: Dict[str, Any]) -> Optional[str]:
        """
        验证参数（可选实现）
        
        Args:
            params: 待验证的参数
            
        Returns:
            错误信息，None表示验证通过
        """
        param_defs = {p.name: p for p in self.get_parameters()}
        
        # 检查必需参数
        for param_def in param_defs.values():
            if param_def.required and param_def.name not in params:
                return f"Missing required parameter: {param_def.name}"
        
        # 检查未知参数
        for param_name in params:
            if param_name not in param_defs:
                return f"Unknown parameter: {param_name}"
        
        return None
    
    async def __call__(self, **params) -> ToolResult:
        """
        使工具可调用
        
        Args:
            **params: 工具参数
            
        Returns:
            ToolResult: 执行结果
        """
        # 验证参数
        error = self.validate_params(params)
        if error:
            return ToolResult(success=False, error=error)
        
        # 执行工具
        try:
            return await self.execute(**params)
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"Tool execution failed: {str(e)}"
            )
    
    def get_info(self) -> Dict[str, Any]:
        """
        获取工具信息（用于注册和文档）
        
        Returns:
            工具信息字典
        """
        return {
            "name": self.name,
            "description": self.description,
            "permission": self.permission.value,
            "parameters": [p.to_dict() for p in self.get_parameters()]
        }
    
    def to_xml_example(self) -> str:
        """
        生成XML调用示例
        
        Returns:
            XML格式的调用示例
        """
        import re
        
        params = self.get_parameters()
        param_lines = []
        
        for param in params:
            param_type = param.type.lower()
            
            # 处理数组类型
            if param_type.startswith("array"):
                # 用正则提取[]中的元素类型
                match = re.search(r'array\[(\w+)\]', param_type)
                element_type = match.group(1) if match else "string"
                
                # 根据元素类型生成示例
                if element_type == "string":
                    value = '["item1", "item2"]'  # 字符串用引号
                else:
                    value = '[item1, item2]'      # 其他类型不用引号
            
            # 处理普通类型
            else:
                if param.default is not None:
                    value = str(param.default)
                elif param_type == "string":
                    value = f"your_{param.name}_here"
                elif param_type == "integer":
                    value = "123"
                elif param_type == "boolean":
                    value = "true"
                else:
                    value = "..."
            
            param_lines.append(f"    <{param.name}>{value}</{param.name}>")
        
        return f"""<tool_call>
  <name>{self.name}</name>
  <params>
{chr(10).join(param_lines)}
  </params>
</tool_call>"""


class SyncBaseTool(BaseTool):
    """
    同步工具基类（对于不需要异步的工具）
    """
    
    @abstractmethod
    def execute_sync(self, **params) -> ToolResult:
        """同步执行方法（子类实现）"""
        pass
    
    async def execute(self, **params) -> ToolResult:
        """异步包装器"""
        return self.execute_sync(**params)
    

if __name__ == "__main__":
    import asyncio
    
    # 简单测试用例
    async def test():
        print("\n🧪 工具系统基础测试")
        print("="*50)
        
        # 1. 创建简单工具
        class TestTool(SyncBaseTool):
            def __init__(self):
                super().__init__(
                    name="test_tool",
                    description="A test tool",
                    permission=ToolPermission.PUBLIC
                )
            
            def get_parameters(self):
                return [
                    ToolParameter("input", "string", "Test input", True),
                    ToolParameter("count", "integer", "Repeat count", False, 1)
                ]
            
            def execute_sync(self, **params):
                input_str = params.get("input", "")
                count = params.get("count", 1)
                result = input_str * count
                return ToolResult(
                    success=True,
                    data={"output": result, "length": len(result)}
                )
        
        # 2. 测试工具基本功能
        tool = TestTool()
        
        print(f"\n✅ 创建工具: {tool.name}")
        print(f"   描述: {tool.description}")
        print(f"   权限: {tool.permission.value}")
        
        # 3. 测试工具执行
        print("\n🚀 测试工具执行...")
        
        # 正常执行
        result = await tool(input="Hello ", count=3)
        print(f"   正常执行: {'✅' if result.success else '❌'}")
        print(f"   结果: {result.data}")
        
        # 缺少参数执行
        result = await tool(count=2)
        print(f"   缺少参数: {'✅' if result.success else '❌'}")
        print(f"   错误: {result.error}")
        
        # 4. 测试XML示例生成
        print("\n📄 XML调用示例:")
        print(tool.to_xml_example())
        
        # 5. 测试工具信息
        print("\n📊 工具信息:")
        info = tool.get_info()
        print(f"   名称: {info['name']}")
        print(f"   权限: {info['permission']}")
        print(f"   参数数量: {len(info['parameters'])}")
    
    # 运行测试
    asyncio.run(test())