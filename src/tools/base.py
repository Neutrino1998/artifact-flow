"""
工具系统基类
提供所有工具的基础接口和通用功能
"""

import math
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
class ArtifactSpec:
    """工具声明式落盘:工具**声明**「把这份结果存成此 artifact」,由引擎中间件经
    ``ArtifactService.ingest_tool_result`` 落库(具名、带类型、blob 可、配额闸)。

    工具**不**持 ``ArtifactService`` 句柄(守三层模型:通用工具保持哑,只有内建
    artifact/sandbox 工具——它们本就是 manager 层——直接碰 service)。

    ``content`` / ``blob`` 的取舍:
    - 纯文本结果 → ``content``,``blob=None``。
    - 二进制结果(PDF/图片/office)→ ``blob`` + ``blob_content_type``;``content``
      留空或给一段简短文本预览(模型在 tool_result 里看到的就是它的截断)。
    """
    content_type: str                        # artifact 展示类型(如 application/pdf、text/csv)
    title: Optional[str] = None              # 展示标题;缺省由 filename/工具名派生
    filename: Optional[str] = None           # 决定 artifact id + 下载名;缺省由 title/工具名派生
    content: str = ""                        # 文本表示(模型预览来源);二进制可留空
    blob: Optional[bytes] = None             # 二进制原件
    blob_content_type: Optional[str] = None  # 原件真实 MIME(供 raw 端点);缺省取 content_type
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class ToolResult:
    """工具执行结果"""
    success: bool
    data: str = ""
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    # 声明式落盘:命中则引擎中间件把它存成具名 artifact、回填预览句柄(见 ArtifactSpec)。
    artifact: Optional["ArtifactSpec"] = None


@dataclass
class ToolParameter:
    """工具参数定义"""
    name: str
    type: str  # "string", "integer", "boolean", "number"
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
        max_result_size_chars: float = 50000,
    ):
        """
        初始化工具

        Args:
            name: 工具名称（唯一标识）
            description: 工具描述
            permission: 权限级别
            show_example: 是否在工具文档中显示XML调用示例
            max_result_size_chars: 工具结果字符数上限。超过则由引擎中间件
                自动落盘为 artifact，并把回填内容替换为预览 + artifact id。
                math.inf = 永不落盘（read_artifact 必须用，避免循环）；
                0 = 任何非空成功结果都落盘。默认 50000。
        """
        self.name = name
        self.description = description
        self.permission = permission
        self.show_example = show_example
        self.max_result_size_chars = max_result_size_chars
    
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

        # 检查 enum 约束
        for name, value in params.items():
            param_def = param_defs.get(name)
            if param_def and param_def.enum and isinstance(value, str):
                if value not in param_def.enum:
                    return f"Invalid value for '{name}': '{value}'. Must be one of: {param_def.enum}"

        # 检查类型（coerce 后仍为 str 说明转换失败）
        for name, value in params.items():
            param_def = param_defs.get(name)
            if param_def is None:
                continue
            target = param_def.type.lower()
            if target == "integer" and not isinstance(value, int):
                return f"Invalid value for '{name}': '{value}' is not a valid integer"
            if target == "number" and not isinstance(value, (int, float)):
                return f"Invalid value for '{name}': '{value}' is not a valid number"
            if target == "boolean" and not isinstance(value, bool):
                return f"Invalid value for '{name}': '{value}' is not a valid boolean (use true/false/yes/no/1/0)"

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
                    lower = value.lower()
                    if lower in ("true", "1", "yes"):
                        result[name] = True
                    elif lower in ("false", "0", "no"):
                        result[name] = False
                    # 其他值保持原始字符串，由 validate_params 报错
                elif target_type == "number":
                    result[name] = float(value)
                # "string" stays as-is
            except (ValueError, TypeError) as e:
                # 转换失败保持原值，由 validate_params 报错
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
  <reason><![CDATA[why you are calling {self.name}]]></reason>
  <name>{self.name}</name>
  <params>
{chr(10).join(param_lines)}
  </params>
</tool_call>"""


# 请求级创建的工具名字固定（artifact 工具 + 沙盒工具），需要在启动时排除自定义
# 工具同名冲突。
RESERVED_TOOL_NAMES = {"create_artifact", "update_artifact", "rewrite_artifact", "read_artifact", "grep_artifact", "bash", "mount", "persist"}


def build_tool_map(
    builtin_tools: List[BaseTool],
    custom_tools: List[BaseTool],
) -> Dict[str, BaseTool]:
    """
    构建 name → tool 映射，检测自定义工具与内置/保留名的冲突

    Args:
        builtin_tools: 内置工具列表
        custom_tools: 自定义工具列表

    Returns:
        合并后的工具字典

    Raises:
        ValueError: 自定义工具名与内置工具或保留名冲突
    """
    tool_map: Dict[str, BaseTool] = {}
    for tool in builtin_tools:
        tool_map[tool.name] = tool

    for tool in custom_tools:
        if tool.name in tool_map or tool.name in RESERVED_TOOL_NAMES:
            raise ValueError(
                f"Custom tool '{tool.name}' conflicts with a builtin tool. "
                f"Rename it in config/tools/ to avoid shadowing."
            )
        tool_map[tool.name] = tool

    return tool_map