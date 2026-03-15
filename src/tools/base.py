"""
工具系统基类
提供所有工具的基础接口和通用功能
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum

from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


class ToolPermission(Enum):
    """
    工具权限级别（两级模型）

    - AUTO: 自动执行，无需用户确认
    - CONFIRM: 执行前需用户确认（通过 interrupt 暂停）
    """
    AUTO = "auto"            # 自动执行（搜索、抓取、artifact 操作等）
    CONFIRM = "confirm"      # 需用户确认（敏感操作如发邮件、执行代码等）


@dataclass
class ToolResult:
    """工具执行结果"""
    success: bool
    data: str = ""
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolParameter:
    """工具参数定义"""
    name: str
    type: str  # "string", "integer", "boolean", "array[string]"
    description: str
    required: bool = True
    default: Any = None
    enum: Optional[List[str]] = None


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
        permission: ToolPermission = ToolPermission.AUTO,
        show_example: bool = True,
    ):
        """
        初始化工具

        Args:
            name: 工具名称（唯一标识）
            description: 工具描述
            permission: 权限级别
            show_example: 是否在工具文档中显示XML调用示例
        """
        self.name = name
        self.description = description
        self.permission = permission
        self.show_example = show_example
    
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
        missing = [p.name for p in param_defs.values() if p.required and p.name not in params]
        if missing:
            expected = [p.name for p in param_defs.values() if p.required]
            received = list(params.keys()) or ["(none)"]
            return f"Missing required parameter(s): {', '.join(missing)}. Required: {expected}. Received: {received}"

        # 检查未知参数
        unknown = [name for name in params if name not in param_defs]
        if unknown:
            return f"Unknown parameter(s): {', '.join(unknown)}. Valid: {list(param_defs.keys())}"
        
        return None
    
    def _coerce_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        根据 ToolParameter.type 做确定性类型转换

        XML parser 返回的值统一为 str，此方法将其转为目标类型。

        Args:
            params: 原始参数（值为 str）

        Returns:
            类型转换后的参数
        """
        param_defs = {p.name: p for p in self.get_parameters()}
        result = dict(params)

        for name, value in result.items():
            param_def = param_defs.get(name)
            if param_def is None or not isinstance(value, str):
                continue

            target_type = param_def.type.lower()
            try:
                if target_type == "integer":
                    result[name] = int(value)
                elif target_type == "boolean":
                    result[name] = value.lower() in ("true", "1", "yes")
                elif target_type == "number":
                    result[name] = float(value)
                # "string" and "array[*]" stay as-is
            except (ValueError, TypeError) as e:
                logger.warning(f"Type coercion failed for param '{name}': {value!r} -> {target_type}: {e}")

        return result

    def _apply_defaults(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        应用参数默认值

        Args:
            params: 原始参数

        Returns:
            填充默认值后的参数
        """
        result = dict(params)
        for param_def in self.get_parameters():
            if param_def.name not in result and param_def.default is not None:
                result[param_def.name] = param_def.default
        return result

    async def __call__(self, **params) -> ToolResult:
        """
        使工具可调用

        Args:
            **params: 工具参数

        Returns:
            ToolResult: 执行结果
        """
        # 类型转换（str → target type）
        params = self._coerce_params(params)

        # 应用默认值
        params = self._apply_defaults(params)

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
    
    def to_xml_example(self) -> str:
        """
        生成XML调用示例（使用CDATA包装所有值）

        Returns:
            XML格式的调用示例
        """
        params = self.get_parameters()
        param_lines = []

        for param in params:
            param_type = param.type.lower()

            # 处理数组类型 - 使用嵌套XML结构
            if param_type.startswith("array"):
                param_lines.append(f"    <{param.name}>")
                param_lines.append(f"      <item><![CDATA[value1]]></item>")
                param_lines.append(f"      <item><![CDATA[value2]]></item>")
                param_lines.append(f"    </{param.name}>")

            # 处理普通类型 - 统一使用CDATA
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

                param_lines.append(f"    <{param.name}><![CDATA[{value}]]></{param.name}>")

        return f"""<tool_call>
  <name>{self.name}</name>
  <params>
{chr(10).join(param_lines)}
  </params>
</tool_call>"""