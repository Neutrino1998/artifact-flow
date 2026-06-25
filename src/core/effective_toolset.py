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

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

from reconcile.snapshot import AgentSnapshot, RegistrySnapshot
from tools.base import BaseTool, ToolPermission


@dataclass
class EffectiveToolset:
    """某 agent 解析后的可调工具集:`{full_name: ToolPermission}`。

    成员判定与等级查询的单一入口;读点只问它「在不在」「什么等级」。
    """
    permissions: Dict[str, ToolPermission]

    def __contains__(self, full_name: str) -> bool:
        return full_name in self.permissions

    def names(self) -> List[str]:
        return list(self.permissions.keys())

    def level(self, full_name: str) -> Optional[ToolPermission]:
        return self.permissions.get(full_name)

    def has_any(self, candidates: Iterable[str]) -> bool:
        return any(c in self.permissions for c in candidates)


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
        for full_name in unit.member_full_names:
            tool = tools.get(full_name)
            if tool is not None:
                permissions[full_name] = tool.permission

    return EffectiveToolset(permissions)


def resolve_all(
    snapshot: RegistrySnapshot,
    tools: Dict[str, BaseTool],
) -> Dict[str, EffectiveToolset]:
    """一次性解析快照里全部 agent 的可调工具集,供引擎按 agent_name 直接索引。"""
    return {
        name: resolve_effective_toolset(agent, snapshot, tools)
        for name, agent in snapshot.agents.items()
    }
