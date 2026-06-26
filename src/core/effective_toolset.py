"""
EffectiveToolset —— agent 的「可调工具集 + 等级」唯一解析点。

把原本散落在 4 处(context_manager 渲染/条件段、engine 执行闸/等级检查)对
`AgentConfig.tools` 的直读收成一个解析点(决策 11)。输入只两样静态来源:
  ① agent 宇宙 = `builtin_tools`(声明的 builtin) ∪ `agent_units`(external 单元)
     —— 每项带 enabled/disabled,absent 即不在宇宙;
  ② tool-set 展开 —— 一个 enabled 的 unit 展开成它全部成员 `full_name`。
输出扁平 `{full_name: ToolPermission}`。

**等级唯一来源是工具定义**(决策 11):builtin = `BaseTool.permission`,external =
`tool_member.permission`(已在快照重建进 `HttpTool.permission`)。绑定表只存成员态
(enabled/disabled),不存等级 —— 故这里的 level 一律从工具对象本身取。

dept/skill/MCP 是后续阶段各加一个输入层(不再碰这些读点);本解析只做静态两样。
"""

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

from reconcile.snapshot import AgentSnapshot, RegistrySnapshot
from tools.base import SEARCH_TOOLS_NAME, BaseTool, ToolPermission


@dataclass
class DeferredUnit:
    """一个 deferred unit 在 `<available_tools>` 里渲染索引行所需的信息。

    `member_full_names` 已过滤到本 turn **可调且工具对象存在**的成员(与 permissions
    同口径)—— 索引行只列模型真能 search 出来的工具,不挂死链。
    """
    name: str
    description: str
    member_full_names: List[str] = field(default_factory=list)


@dataclass
class EffectiveToolset:
    """某 agent 解析后的可调工具集:`{full_name: ToolPermission}`。

    成员判定与等级查询的单一入口;读点只问它「在不在」「什么等级」。

    `deferred_units`:本 agent 宇宙里 `defer=True` 的 unit(B-3 渐进式披露)——
    它们的成员仍在 `permissions`(可调),但 `<available_tools>` 只渲索引行、完整
    schema 由 `search_tools` 按需补。defer 分组在 resolver 一处算好,context_manager
    只消费 effective_toolset(不再碰 snapshot),维持单一解析点。
    """
    permissions: Dict[str, ToolPermission]
    deferred_units: Dict[str, DeferredUnit] = field(default_factory=dict)

    def __contains__(self, full_name: str) -> bool:
        return full_name in self.permissions

    def names(self) -> List[str]:
        return list(self.permissions.keys())

    def level(self, full_name: str) -> Optional[ToolPermission]:
        return self.permissions.get(full_name)

    def has_any(self, candidates: Iterable[str]) -> bool:
        return any(c in self.permissions for c in candidates)

    def deferred_member_names(self) -> set:
        """所有 deferred unit 的成员 full_name 扁平集(渲染时据此把它们排除出完整 doc)。"""
        names: set = set()
        for unit in self.deferred_units.values():
            names.update(unit.member_full_names)
        return names


def resolve_effective_toolset(
    agent: AgentSnapshot,
    snapshot: RegistrySnapshot,
    tools: Dict[str, BaseTool],
) -> EffectiveToolset:
    """解析单个 agent 的可调工具集。

    `tools` = 本 turn 合并后的全量工具对象(builtin + DB external + 请求级 artifact/
    sandbox),等级从其中的工具对象取。宇宙里声明了但 `tools` 缺席的项跳过(与旧
    `if name in tools` 行为一致 —— 如某 unit 成员的 HttpTool 未能重建)。
    """
    permissions: Dict[str, ToolPermission] = {}
    deferred_units: Dict[str, DeferredUnit] = {}

    # ① builtin 轴:enabled 的 builtin,等级取工具对象
    for name, member_state in agent.builtin_tools.items():
        if member_state != "enabled":
            continue
        tool = tools.get(name)
        if tool is not None:
            permissions[name] = tool.permission

    # ② external 轴:enabled 的 unit → 展开成员 full_name,逐个取等级
    for unit_name, member_state in agent.units.items():
        if member_state != "enabled":
            continue
        unit = snapshot.units.get(unit_name)
        if unit is None:
            continue
        present_members: List[str] = []
        for full_name in unit.member_full_names:
            tool = tools.get(full_name)
            if tool is not None:
                permissions[full_name] = tool.permission
                present_members.append(full_name)
        # defer 的 unit:成员仍可调(已进 permissions),但只渲索引行 → 记进 deferred_units。
        # 只在有可调成员时记(空 unit 无可披露内容,索引行也无意义)。
        if unit.defer and present_members:
            deferred_units[unit_name] = DeferredUnit(
                name=unit.name,
                description=unit.description,
                member_full_names=present_members,
            )

    # ③ defer 自动注入 search_tools(2026-06-26 决策):有 ≥1 deferred unit 的 agent
    # 必须能 search,否则 deferred 工具成死工具。把「deferred ⟹ 可搜索」做成 by-construction
    # 不变量 —— operator 无需在 agent MD 显式声明 search_tools(忘了也不漏)。
    # search_tools 是常驻 builtin,有 deferred unit 它就**必须**在 tools 里;下标取而非 .get
    # —— 缺席 = 它没注册 = 硬 bug,当场 KeyError 炸出来,不静默 skip(builtin 一律假定存在,
    # 同 artifact/sandbox 工具,不写防御性 is-not-None)。
    if deferred_units and SEARCH_TOOLS_NAME not in permissions:
        permissions[SEARCH_TOOLS_NAME] = tools[SEARCH_TOOLS_NAME].permission

    return EffectiveToolset(permissions, deferred_units)


def resolve_all(
    snapshot: RegistrySnapshot,
    tools: Dict[str, BaseTool],
) -> Dict[str, EffectiveToolset]:
    """一次性解析快照里全部 agent 的可调工具集,供引擎按 agent_name 直接索引。"""
    return {
        name: resolve_effective_toolset(agent, snapshot, tools)
        for name, agent in snapshot.agents.items()
    }
