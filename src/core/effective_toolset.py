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

Builtin 成员关系只来自 agent 配置。请求级工具对象是否存在只能收窄配置（例如本轮
没有可见 skill 就没有 read_skill 对象），不能根据 bash/deferred/skill 状态扩张 agent
宇宙。deferred 是 best-effort 的上下文优化：agent 未显式配置 search_tools 时回退为
完整 schema，不制造无法披露的死工具。
"""

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set

from reconcile.snapshot import AgentSnapshot, RegistrySnapshot, SkillInfo, UnitInfo
from tools.base import (
    SEARCH_TOOLS_NAME,
    BaseTool,
    ToolPermission,
    is_builtin_name,
    resolve_allowed_tool_entry,
)


@dataclass
class DeferredUnit:
    """一个 deferred unit 在 `<available_tools>` 里渲染索引行所需的信息。

    `member_full_names` 已过滤到本 turn **可调且工具对象存在**的成员(与 permissions
    同口径)—— 索引行只列模型真能 search 出来的工具,不挂死链。
    """
    name: str
    description: str
    discovery_error: Optional[str] = None
    member_full_names: List[str] = field(default_factory=list)
    defer: bool = True

    @classmethod
    def from_unit(cls, unit: UnitInfo, present_members: List[str]) -> "DeferredUnit":
        """从 unit + 本 turn 可建成员构造索引行(「只列可建成员、不挂死链」契约的单一
        出口)—— resolve ② 与 _bake_skill_grants 共用。是否真正 defer 由调用方根据
        search_tools 的显式成员关系决定。"""
        return cls(
            name=unit.name,
            description=unit.description,
            discovery_error=unit.discovery_error,
            member_full_names=present_members,
            defer=unit.defer,
        )


@dataclass
class SkillGrant:
    """一个 skill 激活时预烤好的授予。

    `permissions`:翻开哪些工具(该 skill allowed-tools ∩ 本 agent disabled 池)。
    `tool_units`:授予涉及的 external unit 目录信息。激活后若 search_tools 已经通过
    agent 配置或同一个 skill 显式启用，defer=True 的 unit 才进入渐进式披露；否则
    完整 schema 直接暴露。
    """
    permissions: Dict[str, ToolPermission] = field(default_factory=dict)
    tool_units: Dict[str, DeferredUnit] = field(default_factory=dict)


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
    # 预烤的 skill 能力授予(决策 11/changelog 06-30):`{slug: SkillGrant}` —— 每个 skill
    # 若激活会「翻开」哪些(= 该 skill 的 allowed-tools ∩ 本 agent 的 disabled 池,等级取自
    # 工具定义)+ 随之注册哪些 external unit。激活只 merge 配置里显式 disabled 的成员，
    # 不连带注入其它 builtin。
    skill_grants: Dict[str, SkillGrant] = field(default_factory=dict)
    tool_units: Dict[str, DeferredUnit] = field(default_factory=dict)
    # Agent 宇宙内显式 disabled、且本 turn 有工具对象的成员。它们不在 permissions，
    # 但执行闸需要区分「可经 skill 激活的 disabled」与「根本不属于该 agent 的 absent」。
    # 部门规则已在 resolver 中先收窄，因此这里不会泄露 dept-denied unit。
    disabled_tool_names: Set[str] = field(default_factory=set)

    def __contains__(self, full_name: str) -> bool:
        return full_name in self.permissions

    def activate_skill(self, slug: str) -> None:
        """激活一个 skill:把它预烤的授予 merge 进可调集(只翻 disabled 池、不碰等级),
        并注册授予携带的 external unit。Builtin 不做连带注入；只有 allowed-tools
        直接点名、且 agent 配置为 disabled 的同名工具会被翻开。

        deferred 只在 search_tools 已显式可调时启用；否则保留完整 schema。这是展示
        优化，不改变工具成员关系。幂等(merge 同值 + setdefault);未知 slug / 无授予 = no-op。
        inbound(回合起点恢复)与 mid-turn(read_skill)走同一入口。"""
        grant = self.skill_grants.get(slug)
        if grant is None:
            return
        self.permissions.update(grant.permissions)
        for unit_name, unit in grant.tool_units.items():
            self.tool_units.setdefault(unit_name, unit)
        if SEARCH_TOOLS_NAME in self.permissions:
            for unit_name, unit in self.tool_units.items():
                if unit.defer:
                    self.deferred_units.setdefault(unit_name, unit)

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

    def activatable_tool_names(self) -> set[str]:
        """可由任一当前可见 skill 从 disabled 池翻开的工具名。"""
        return {
            name
            for grant in self.skill_grants.values()
            for name in grant.permissions
        }


def unit_visible_by_department(
    unit: UnitInfo, dept_matched_units: Optional[Set[str]]
) -> bool:
    """该 unit 对当前用户部门是否可见(G-0)。

    `department_unit_rule` 没有 effect 列:命中集合只表示「该部门是例外成员」,
    方向完全由 unit.visibility 派生:
      - public:默认 allow,命中 = deny
      - department:默认 deny,命中 = grant
    未知 visibility fail-closed。builtin 不进 tool_unit,不走这里。
    """
    matched = dept_matched_units or set()
    if unit.visibility == "public":
        return unit.name not in matched
    if unit.visibility == "department":
        return unit.name in matched
    return False


def resolve_effective_toolset(
    agent: AgentSnapshot,
    snapshot: RegistrySnapshot,
    tools: Dict[str, BaseTool],
    skill_snapshot: Optional[Dict[str, SkillInfo]] = None,
    dept_matched_units: Optional[Set[str]] = None,
) -> EffectiveToolset:
    """解析单个 agent 的可调工具集。

    `tools` = 本 turn 合并后的全量工具对象(builtin + DB external + 请求级 artifact/
    sandbox),等级从其中的工具对象取。宇宙里声明了但 `tools` 缺席的项跳过(与旧
    `if name in tools` 行为一致 —— 如某 unit 成员的 HttpTool 未能重建)。

    `skill_snapshot`(C-2):据此预烤 `skill_grants` —— 每个 skill 的 allowed-tools 解析
    到 unit、与本 agent 的 **disabled 池**取交集(skill 只能翻 disabled、不引入 absent、
    不碰等级,决策 11)。激活在引擎按 slug merge,不再回 snapshot。

    `dept_matched_units`(G-0):当前用户部门祖先链命中的 unit 规则集合。dept 收窄在
    skill enable 之前应用,因此 skill 不能重新打开 dept-denied unit。
    """
    permissions: Dict[str, ToolPermission] = {}
    deferred_units: Dict[str, DeferredUnit] = {}
    tool_units: Dict[str, DeferredUnit] = {}
    disabled_tool_names: Set[str] = set()

    # ① builtin 轴:enabled 的 builtin,等级取工具对象
    for name, member_state in agent.builtin_tools.items():
        tool = tools.get(name)
        if member_state == "disabled":
            if tool is not None:
                disabled_tool_names.add(name)
            continue
        if member_state != "enabled":
            continue
        if tool is not None:
            permissions[name] = tool.permission

    # ② external 轴:enabled 的 unit → 展开成员 full_name,逐个取等级
    for unit_name, member_state in agent.units.items():
        if member_state not in {"enabled", "disabled"}:
            continue
        unit = snapshot.units.get(unit_name)
        if unit is None:
            continue
        if not unit_visible_by_department(unit, dept_matched_units):
            continue
        if member_state == "disabled":
            disabled_tool_names.update(
                full_name
                for full_name in unit.member_full_names
                if tools.get(full_name) is not None
            )
            continue
        present_members: List[str] = []
        for full_name in unit.member_full_names:
            tool = tools.get(full_name)
            if tool is not None:
                permissions[full_name] = tool.permission
                present_members.append(full_name)
        # defer 的 unit:成员仍可调(已进 permissions),但只渲索引行 → 记进 deferred_units。
        # 只在有可调成员或 discovery_error 时记;后者让 MCP server 不可达时在目录/search_tools
        # 中显式可见,而不是静默消失。
        if (
            unit.defer
            and SEARCH_TOOLS_NAME in permissions
            and (present_members or unit.discovery_error)
        ):
            deferred_units[unit_name] = DeferredUnit.from_unit(unit, present_members)
        if present_members or unit.discovery_error:
            tool_units[unit_name] = DeferredUnit.from_unit(unit, present_members)

    skill_grants = _bake_skill_grants(
        agent, snapshot, tools, skill_snapshot, dept_matched_units
    )

    ets = EffectiveToolset(
        permissions=permissions,
        deferred_units=deferred_units,
        skill_grants=skill_grants,
        tool_units=tool_units,
        disabled_tool_names=disabled_tool_names,
    )
    return ets


def _bake_skill_grants(
    agent: AgentSnapshot,
    snapshot: RegistrySnapshot,
    tools: Dict[str, BaseTool],
    skill_snapshot: Optional[Dict[str, SkillInfo]],
    dept_matched_units: Optional[Set[str]],
) -> Dict[str, SkillGrant]:
    """预烤 `{slug: SkillGrant}` —— 每个 skill 激活会翻开的工具(只在本 agent 的 disabled
    池里取)。enabled 的 unit 已在 permissions(no-op);absent 不在池(翻不开)。授予涉及
    external unit 时随授予携带目录信息。激活时只有显式可调的 search_tools 才会让
    defer=True 的 unit 进入渐进式披露；否则完整 schema 直接暴露。"""
    if not skill_snapshot:
        return {}

    visible_units = {
        name: unit for name, unit in snapshot.units.items()
        if unit_visible_by_department(unit, dept_matched_units)
    }
    known_unit_names = set(visible_units)
    known_full_names: Dict[str, str] = {
        fn: u.name for u in visible_units.values() for fn in u.member_full_names
    }

    grants_by_slug: Dict[str, SkillGrant] = {}
    for slug, info in skill_snapshot.items():
        grant = SkillGrant()
        for entry in (info.allowed_tools or []):
            unit = resolve_allowed_tool_entry(entry, known_unit_names, known_full_names)
            if unit is None:
                continue
            if is_builtin_name(unit):
                # builtin singleton:agent 显式 disabled 才可翻
                if agent.builtin_tools.get(unit) == "disabled":
                    tool = tools.get(unit)
                    if tool is not None:
                        grant.permissions[unit] = tool.permission
            elif agent.units.get(unit) == "disabled":
                # external unit 在本 agent 宇宙里 disabled → 翻开其全部可建成员
                u = visible_units.get(unit)
                if u is not None:
                    present: List[str] = []
                    for fn in u.member_full_names:
                        tool = tools.get(fn)
                        if tool is not None:
                            grant.permissions[fn] = tool.permission
                            present.append(fn)
                    if present or u.discovery_error:
                        grant.tool_units[unit] = DeferredUnit.from_unit(u, present)
        if grant.permissions:
            grants_by_slug[slug] = grant
    return grants_by_slug


def resolve_all(
    snapshot: RegistrySnapshot,
    tools: Dict[str, BaseTool],
    skill_snapshot: Optional[Dict[str, SkillInfo]] = None,
    dept_matched_units: Optional[Set[str]] = None,
) -> Dict[str, EffectiveToolset]:
    """一次性解析快照里全部 agent 的可调工具集,供引擎按 agent_name 直接索引。

    `skill_snapshot`(C-2)透传给每 agent 的解析,预烤其 skill_grants(激活在引擎)。
    `dept_matched_units`(G-0)同样透传,先收窄 unit 宇宙再烤 skill grants。"""
    return {
        name: resolve_effective_toolset(
            agent, snapshot, tools, skill_snapshot, dept_matched_units
        )
        for name, agent in snapshot.agents.items()
    }
