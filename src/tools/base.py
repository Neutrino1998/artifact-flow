"""
工具系统基类
提供所有工具的基础接口和通用功能
"""

import math
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Set
from dataclasses import dataclass, field
from enum import Enum

from config import config
from tools.input_schema import (
    build_native_function_schema,
    normalize_business_input_schema,
    validate_business_arguments,
)
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
    ``ArtifactService.ingest_tool_result`` 落库(具名、带类型、blob 可、文本/二进制准入)。

    工具**不**持 ``ArtifactService`` 句柄(守三层模型:通用工具保持哑,只有内建
    artifact/sandbox 工具——它们本就是 manager 层——直接碰 service)。

    **XOR 不变量**:一个 artifact 只承载**一份**实质 data —— 文本结果填 ``content``、
    二进制结果填 ``blob``,**二者必居其一、不可兼得**(双表示语义对模型 confusing;
    模型侧统一按「blob = 无文本表示、需 mount 进沙盒」认知)。违反在 ``_stage_artifact``
    loud-fail。
    - 纯文本结果 → ``content``(text MIME),``blob=None``。
    - 二进制结果(PDF/图片/office)→ ``blob``,``content=""``;``content_type`` 即原件
      真实 MIME(XOR 下 blob 的 content_type 就是它的 MIME,无需另给一个 blob MIME)。
    """
    content_type: str                        # artifact 类型 / blob 的真实 MIME(如 application/pdf、text/csv)
    title: Optional[str] = None              # 展示标题;缺省由 filename/工具名派生
    filename: Optional[str] = None           # 决定 artifact id + 下载名;缺省由 title/工具名派生
    content: str = ""                        # 文本结果(模型预览来源);二进制留空
    blob: Optional[bytes] = None             # 二进制原件
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
class ToolExecutionContext:
    """引擎在 execute 期注入给 ``wants_context=True`` 工具的运行期上下文。

    **只装非密的描述性事实**(谁在调、本 turn 有哪些工具、调用方 agent 的可调视图)——
    判别器:进得来的东西必须「描述性、非密、可安全 log、可安全给沙盒」。
    **secret(凭证 / OAuth)永不走这条**:它走 B-4 的独立 credential resolver(懒解、
    只解被调工具、对沙盒不发放),否则会同时撞穿沙盒红线 + 日志泄露面 + lazy 纪律。

    `effective_toolset` 故意宽松类型(``Any``)—— 它的实体是 `core.capabilities.effective_toolset.
    EffectiveToolset`,但 tools 层不该反向依赖 core(core 已依赖 tools)。
    """
    agent_name: str
    effective_toolset: Any        # core.capabilities.effective_toolset.EffectiveToolset(鸭子类型,避免 tools→core)
    tools: Dict[str, "BaseTool"]  # 本 turn 合并后的全量工具对象(name -> BaseTool)
    # 同一 provider response 里的全部 native calls 共用一个 epoch；turn 内跨
    # lead/subagent 单调递增。工具可用它拒绝基于过期环境认知生成的调用，值本身
    # 不进模型参数，也不承载任何权限或秘密。
    model_invocation_epoch: int
    disclosed_tools: Set[str] = field(default_factory=set)


class BaseTool(ABC):
    """
    所有工具的基类

    子类需要实现:
    - execute(): 执行工具的核心逻辑
    - get_input_schema(): 返回业务参数 JSON Schema
    """

    # opt-in:True → 引擎在正常执行路径里给 execute 多注入一个 `_context`
    # (ToolExecutionContext);用于像 search_tools 这种「产结果需要引擎上下文」的工具,
    # 让它走正常工具路由(validate/事件/取消/落盘安全网)而非引擎特殊分支。默认 False。
    wants_context: bool = False

    def __init__(
        self,
        name: str,
        description: str,
        permission: ToolPermission = ToolPermission.AUTO,
        max_result_size_chars: Optional[float] = None,
    ):
        """
        初始化工具

        Args:
            name: 工具名称（唯一标识）
            description: 工具描述
            permission: 权限级别
            max_result_size_chars: 工具结果字符数上限。None 使用部署级默认值；
                超过则由引擎中间件自动落盘为 artifact，并把回填内容替换为
                预览 + artifact id。
                math.inf = 永不落盘（read_artifact 必须用，避免循环）；
                0 = 任何非空成功结果都落盘。
        """
        self.name = name
        self.description = description
        self.permission = permission
        self.max_result_size_chars = (
            config.TOOL_RESULT_INLINE_MAX_CHARS
            if max_result_size_chars is None
            else max_result_size_chars
        )
    
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
    def get_input_schema(self) -> Dict[str, Any]:
        """
        获取业务输入 JSON Schema。
        
        Returns:
            根节点 ``type=object`` 的 JSON Schema。
        """
        pass

    def business_input_schema(self) -> Dict[str, Any]:
        """Return a validated copy so callers cannot mutate the tool definition."""
        return normalize_business_input_schema(
            self.get_input_schema(), source=f"tool {self.name!r}"
        )

    def to_native_tool_schema(self) -> Dict[str, Any]:
        return build_native_function_schema(
            name=self.name,
            description=self.description,
            business_schema=self.get_input_schema(),
        )

    async def __call__(self, _context: Optional["ToolExecutionContext"] = None, **params) -> ToolResult:
        """
        使工具可调用

        Args:
            _context: 引擎注入的运行期上下文(仅 wants_context=True 工具用);带下划线
                它不是模型可见的业务参数，不参与 JSON Schema 校验。
            **params: 工具参数

        Returns:
            ToolResult: 执行结果
        """
        try:
            params = validate_business_arguments(self.business_input_schema(), params)
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))

        # 执行工具
        try:
            if self.wants_context:
                return await self.execute(_context=_context, **params)
            return await self.execute(**params)
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"Tool execution failed: {str(e)}"
            )
    
# skill 工具的注册名(单一来源,同 SEARCH_TOOLS_NAME 姿态)—— 工具定义(read_skill.py
# 两处 name=)/ RESERVED 集 / agent 配置共用,改名只此一处。
READ_SKILL_NAME = "read_skill"
MOUNT_SKILL_NAME = "mount_skill"

# 请求级创建的工具名字固定（artifact 工具 + 沙盒工具），需要在启动时排除自定义
# 工具同名冲突。
RESERVED_TOOL_NAMES = {"create_artifact", "update_artifact", "rewrite_artifact", "read_artifact", "grep_artifact", "bash", "mount", "persist", READ_SKILL_NAME, MOUNT_SKILL_NAME}

# 渐进式披露检索器的注册名(单一来源)—— 引擎上下文注入 / self-exclusion / ctor /
# resolver 的显式成员判断共用,改名只此一处。
SEARCH_TOOLS_NAME = "search_tools"

# 进程级 builtin 工具(代码定义、for-everyone、不入 DB 注册表)。与 RESERVED(请求级
# artifact/sandbox 工具)合起来 = 全部 builtin 名,reconciler 据此把 agent MD `tools:`
# 条目分流 builtin vs external unit(决策 11);builtin 等级 = 工具定义、不进 agent_units。
GLOBAL_BUILTIN_TOOL_NAMES = {"web_search", "web_fetch", "call_subagent", SEARCH_TOOLS_NAME}
BUILTIN_TOOL_NAMES = RESERVED_TOOL_NAMES | GLOBAL_BUILTIN_TOOL_NAMES


def is_builtin_name(name: str) -> bool:
    """external 名是否撞 builtin/reserved(决策 11 的 full_name 全局唯一不变量)。

    单一谓词,写侧种子校验(reconcile)与读侧快照兜底(snapshot)共用 —— 不变量的
    规则只此一处,两侧覆盖不会发散(否则一侧加了新 reserved 形态、另一侧忘加,撞名
    行就从兜底溜过)。
    """
    return name in BUILTIN_TOOL_NAMES


def resolve_allowed_tool_entry(
    entry: str,
    known_unit_names: "set",
    known_full_names: Dict[str, str],
) -> Optional[str]:
    """把一条 `allowed-tools` 条目解析到它归属的 **unit**(决策 11 line 235,纯 exact-match)。

    skill 的 `allowed-tools` 与 dept/agent 一律 unit 粒度。import(校验存在性)与
    runtime(C-2 建 skill_grants)**共用此一个函数**,避免两侧解析口径漂移(reviewer P2)。

    解析序(exact-match,无模糊):
      ① builtin/reserved 名 → 该 builtin(= singleton unit,标准 allowed-tools 逐名原样工作);
      ② 已注册 unit 名 → 该 unit;
      ③ 已注册全名 `<unit>__<tool>` → 其所属 unit(按已知全名查、不 split `__`);
      ④ 裸成员名(无 `<known-unit>__` 前缀、又非 unit 名)→ 不接受,返回 None
         (import warn / runtime 忽略)。`search` ≠ `github__search`,裸名永不命中 set 成员。

    返回:命中的 unit 标识(builtin 名 / external unit 名);未命中 None。
    """
    if is_builtin_name(entry):
        return entry
    if entry in known_unit_names:
        return entry
    if entry in known_full_names:
        return known_full_names[entry]
    return None


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
