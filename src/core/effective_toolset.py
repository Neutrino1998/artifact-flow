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

请求级 builtin 注入(search_tools/read_skill/mount_skill)= permissions 每次变异后都
必须成立的不变量(F-0):resolve 末尾与 activate_skill 变异后跑同一份
`apply_injection_invariants`,注入上下文(`injectable_builtins`)在 resolve 期烤入。
"""

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

from reconcile.snapshot import AgentSnapshot, RegistrySnapshot, SkillInfo
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
    member_full_names: List[str] = field(default_factory=list)


# 请求级 builtin 名(注入不变量用;工具本体在 tools/builtin 各自定义)。
_READ_SKILL = "read_skill"
_MOUNT_SKILL = "mount_skill"


@dataclass
class SkillGrant:
    """一个 skill 激活时预烤好的授予(F-0 前 = 裸 {full_name: level} 字典)。

    `permissions`:翻开哪些工具(该 skill allowed-tools ∩ 本 agent disabled 池)。
    `deferred_units`:授予涉及 `defer=True` 的 unit 时随之注册的索引行 —— 激活后保持
    渐进式披露(索引行 + search_tools 按需补 schema),不把整组 schema 灌进上下文。
    """
    permissions: Dict[str, ToolPermission] = field(default_factory=dict)
    deferred_units: Dict[str, DeferredUnit] = field(default_factory=dict)


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
    # 工具定义)+ 随之注册哪些 deferred 索引行。激活 = merge 进 permissions/deferred_units,
    # 引擎不回 snapshot、不持闭包(纯字典操作,见 activate_skill)。
    skill_grants: Dict[str, SkillGrant] = field(default_factory=dict)
    # 请求级 builtin 的注入上下文(F-0):`{tool_name: 等级}`,resolve 期从本 turn 的 tools
    # 烤入(⊆ {search_tools, read_skill, mount_skill})。**presence 即闸** —— read_skill/
    # mount_skill 仅当有可见 (bundle) skill 时才被建(见 create_skill_tools),不在此 dict =
    # 本 turn 没那道能力,对应规则自然不触发。只存等级、不持工具对象/闭包。
    injectable_builtins: Dict[str, ToolPermission] = field(default_factory=dict)

    def __contains__(self, full_name: str) -> bool:
        return full_name in self.permissions

    def activate_skill(self, slug: str) -> None:
        """激活一个 skill:把它预烤的授予 merge 进可调集(只翻 disabled 池、不碰等级),
        并注册授予携带的 deferred 索引行,再重跑注入不变量(F-0:skill 可能翻开 bash/
        defer unit → mount_skill/search_tools 必须连动)。

        幂等(merge 同值 + setdefault + 不变量幂等);未知 slug / 无授予 = no-op。
        inbound(回合起点恢复)与 mid-turn(read_skill)走同一入口。"""
        grant = self.skill_grants.get(slug)
        if grant is None:
            return
        self.permissions.update(grant.permissions)
        for unit_name, du in grant.deferred_units.items():
            self.deferred_units.setdefault(unit_name, du)
        self.apply_injection_invariants()

    def apply_injection_invariants(self) -> None:
        """请求级 builtin 注入 = permissions 每次变异后都必须成立的不变量(F-0)。

        resolve 末尾与 activate_skill 变异后跑**同一份规则**,收拢原先散在 resolver
        (search_tools)与 controller_factory(read_skill/mount_skill setdefault)的两家
        注入 —— 修两条运行时不对称:skill 翻开 defer unit 后 search_tools 不注入 /
        翻开 bash 后 mount_skill 不连动。将来 MCP 注入作第三个消费方接进这里,不打
        新补丁。setdefault 语义:已在集内的不覆盖(等级唯一来源仍是工具定义)。
        """
        # deferred ⟹ search_tools(2026-06-26 决策):有 ≥1 deferred unit 的 agent 必须能
        # search,否则 deferred 工具成死工具。search_tools 是常驻 builtin,有 deferred unit
        # 它就**必须**已烤进 injectable_builtins;缺席 = 没注册 = 硬 bug,下标取当场
        # KeyError 炸出来,不静默 skip(builtin 一律假定存在,不写防御性 is-not-None)。
        if self.deferred_units and SEARCH_TOOLS_NAME not in self.permissions:
            self.permissions[SEARCH_TOOLS_NAME] = self.injectable_builtins[SEARCH_TOOLS_NAME]
        # 有可见 skill ⟹ read_skill:自包含(进 slug 出文本)→ 每个 agent 都注入。
        read_perm = self.injectable_builtins.get(_READ_SKILL)
        if read_perm is not None:
            self.permissions.setdefault(_READ_SKILL, read_perm)
        # bash ∧ 有可见 bundle skill ⟹ mount_skill:产物只有配 bash 才用得上(cat
        # references / 跑 scripts),无 bash 的 agent 不给死能力(按需注入)。
        mount_perm = self.injectable_builtins.get(_MOUNT_SKILL)
        if mount_perm is not None and "bash" in self.permissions:
            self.permissions.setdefault(_MOUNT_SKILL, mount_perm)

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
    skill_snapshot: Optional[Dict[str, SkillInfo]] = None,
) -> EffectiveToolset:
    """解析单个 agent 的可调工具集。

    `tools` = 本 turn 合并后的全量工具对象(builtin + DB external + 请求级 artifact/
    sandbox),等级从其中的工具对象取。宇宙里声明了但 `tools` 缺席的项跳过(与旧
    `if name in tools` 行为一致 —— 如某 unit 成员的 HttpTool 未能重建)。

    `skill_snapshot`(C-2):据此预烤 `skill_grants` —— 每个 skill 的 allowed-tools 解析
    到 unit、与本 agent 的 **disabled 池**取交集(skill 只能翻 disabled、不引入 absent、
    不碰等级,决策 11)。激活在引擎按 slug merge,不再回 snapshot。
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

    # ③ 请求级 builtin 注入上下文(F-0):从本 turn 的 tools 烤入三个可注入 builtin 的
    # 等级(presence 即闸:read_skill/mount_skill 只在有可见 (bundle) skill 时存在)。
    # 注入本身收进 apply_injection_invariants —— resolve 与 activate_skill 跑同一份规则。
    injectable: Dict[str, ToolPermission] = {}
    for name in (SEARCH_TOOLS_NAME, _READ_SKILL, _MOUNT_SKILL):
        tool = tools.get(name)
        if tool is not None:
            injectable[name] = tool.permission

    skill_grants = _bake_skill_grants(agent, snapshot, tools, skill_snapshot)

    ets = EffectiveToolset(permissions, deferred_units, skill_grants, injectable)
    ets.apply_injection_invariants()
    return ets


def _bake_skill_grants(
    agent: AgentSnapshot,
    snapshot: RegistrySnapshot,
    tools: Dict[str, BaseTool],
    skill_snapshot: Optional[Dict[str, SkillInfo]],
) -> Dict[str, SkillGrant]:
    """预烤 `{slug: SkillGrant}` —— 每个 skill 激活会翻开的工具(只在本 agent 的 disabled
    池里取)。enabled 的 unit 已在 permissions(no-op);absent 不在池(翻不开)。授予涉及
    `defer=True` 的 unit 时,随授予携带其 DeferredUnit(与 resolve ② 同判据)—— 激活时
    注册索引行,保持渐进式披露(否则翻开的成员被当 non-deferred 全 schema 渲染,F-0)。"""
    if not skill_snapshot:
        return {}

    known_unit_names = set(snapshot.units)
    known_full_names: Dict[str, str] = {
        fn: u.name for u in snapshot.units.values() for fn in u.member_full_names
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
                u = snapshot.units.get(unit)
                if u is not None:
                    present: List[str] = []
                    for fn in u.member_full_names:
                        tool = tools.get(fn)
                        if tool is not None:
                            grant.permissions[fn] = tool.permission
                            present.append(fn)
                    if u.defer and present:
                        grant.deferred_units[unit] = DeferredUnit(
                            name=u.name,
                            description=u.description,
                            member_full_names=present,
                        )
        if grant.permissions:
            grants_by_slug[slug] = grant
    return grants_by_slug


def resolve_all(
    snapshot: RegistrySnapshot,
    tools: Dict[str, BaseTool],
    skill_snapshot: Optional[Dict[str, SkillInfo]] = None,
) -> Dict[str, EffectiveToolset]:
    """一次性解析快照里全部 agent 的可调工具集,供引擎按 agent_name 直接索引。

    `skill_snapshot`(C-2)透传给每 agent 的解析,预烤其 skill_grants(激活在引擎)。"""
    return {
        name: resolve_effective_toolset(agent, snapshot, tools, skill_snapshot)
        for name, agent in snapshot.agents.items()
    }
