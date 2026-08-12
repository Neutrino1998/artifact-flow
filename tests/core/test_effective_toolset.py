"""EffectiveToolset resolver 单测(决策 11 单一解析点)。

覆盖:enabled/disabled/absent 三态、singleton 与 toolset 展开、等级取自工具对象
(非绑定)、缺席 unit 跳过、resolve_all 全 agent。
"""

from core.capabilities.effective_toolset import (
    EffectiveToolset,
    resolve_all,
    resolve_effective_toolset,
)
from reconcile.snapshot import AgentSnapshot, RegistrySnapshot, UnitInfo
from tools.base import ToolPermission


class _Tool:
    """最小工具桩:resolver 只读 .permission。"""
    def __init__(self, name, permission):
        self.name = name
        self.permission = permission


def _agent(name="lead_agent", builtin_tools=None, units=None):
    return AgentSnapshot(
        name=name,
        description="d",
        model="m",
        internal=False,
        role_prompt="",
        builtin_tools=builtin_tools or {},
        units=units or {},
    )


def _unit(
    name,
    members,
    kind="tool",
    defer=False,
    description="",
    discovery_error=None,
    visibility="public",
):
    return UnitInfo(
        name=name, kind=kind, description=description, visibility=visibility,
        defer=defer, provider="http", source="seeded",
        discovery_error=discovery_error,
        member_full_names=list(members),
    )


def _snapshot(units=None, agents=None, external_tools=None):
    return RegistrySnapshot(
        external_tools=external_tools or {},
        units={u.name: u for u in (units or [])},
        agents={a.name: a for a in (agents or [])},
    )


def test_enabled_builtin_in_set_with_tool_level():
    agent = _agent(builtin_tools={"web_search": "enabled", "bash": "enabled"})
    tools = {
        "web_search": _Tool("web_search", ToolPermission.AUTO),
        "bash": _Tool("bash", ToolPermission.CONFIRM),
    }
    eff = resolve_effective_toolset(agent, _snapshot(), tools)
    assert "web_search" in eff
    assert "bash" in eff
    # 等级取自工具对象,绑定不存等级
    assert eff.level("web_search") == ToolPermission.AUTO
    assert eff.level("bash") == ToolPermission.CONFIRM


def test_disabled_builtin_absent():
    agent = _agent(builtin_tools={"web_search": "enabled", "bash": "disabled"})
    tools = {
        "web_search": _Tool("web_search", ToolPermission.AUTO),
        "bash": _Tool("bash", ToolPermission.CONFIRM),
    }
    eff = resolve_effective_toolset(agent, _snapshot(), tools)
    assert "web_search" in eff
    assert "bash" not in eff
    assert eff.disabled_tool_names == {"bash"}


def test_undeclared_tool_absent():
    agent = _agent(builtin_tools={"web_search": "enabled"})
    tools = {
        "web_search": _Tool("web_search", ToolPermission.AUTO),
        "web_fetch": _Tool("web_fetch", ToolPermission.CONFIRM),
    }
    eff = resolve_effective_toolset(agent, _snapshot(), tools)
    assert "web_fetch" not in eff  # 未声明 = 不在宇宙
    assert "web_fetch" not in eff.disabled_tool_names


def test_singleton_unit_enabled():
    agent = _agent(units={"weather": "enabled"})
    snap = _snapshot(units=[_unit("weather", ["weather"])])
    tools = {"weather": _Tool("weather", ToolPermission.CONFIRM)}
    eff = resolve_effective_toolset(agent, snap, tools)
    assert eff.names() == ["weather"]
    assert eff.level("weather") == ToolPermission.CONFIRM


def test_toolset_unit_expands_to_members():
    agent = _agent(units={"github": "enabled"})
    snap = _snapshot(units=[
        _unit("github", ["github__search_repos", "github__create_issue"], kind="toolset"),
    ])
    tools = {
        "github__search_repos": _Tool("github__search_repos", ToolPermission.AUTO),
        "github__create_issue": _Tool("github__create_issue", ToolPermission.CONFIRM),
    }
    eff = resolve_effective_toolset(agent, snap, tools)
    assert set(eff.names()) == {"github__search_repos", "github__create_issue"}
    assert eff.level("github__create_issue") == ToolPermission.CONFIRM


def test_disabled_unit_members_absent():
    agent = _agent(units={"github": "disabled"})
    snap = _snapshot(units=[_unit("github", ["github__search_repos"], kind="toolset")])
    tools = {"github__search_repos": _Tool("github__search_repos", ToolPermission.AUTO)}
    eff = resolve_effective_toolset(agent, snap, tools)
    assert eff.names() == []
    assert eff.disabled_tool_names == {"github__search_repos"}


def test_unit_missing_from_snapshot_skipped():
    # 宇宙引用了 unit,但快照里没有该 unit(被 prune 等)→ 跳过,不崩
    agent = _agent(units={"ghost": "enabled"})
    eff = resolve_effective_toolset(agent, _snapshot(), {})
    assert eff.names() == []


def test_member_without_tool_object_skipped():
    # unit 成员声明了,但工具对象未重建(如 mcp provider 未接)→ 跳过
    agent = _agent(units={"github": "enabled"})
    snap = _snapshot(units=[_unit("github", ["github__a", "github__b"], kind="toolset")])
    tools = {"github__a": _Tool("github__a", ToolPermission.AUTO)}  # b 缺工具对象
    eff = resolve_effective_toolset(agent, snap, tools)
    assert eff.names() == ["github__a"]


def test_resolve_all_covers_every_agent():
    a1 = _agent("lead_agent", builtin_tools={"web_search": "enabled"})
    a2 = _agent("explore_agent", units={"weather": "enabled"})
    snap = _snapshot(units=[_unit("weather", ["weather"])], agents=[a1, a2])
    tools = {
        "web_search": _Tool("web_search", ToolPermission.AUTO),
        "weather": _Tool("weather", ToolPermission.AUTO),
    }
    eff_map = resolve_all(snap, tools)
    assert set(eff_map) == {"lead_agent", "explore_agent"}
    assert "web_search" in eff_map["lead_agent"]
    assert "weather" in eff_map["explore_agent"]
    assert "web_search" not in eff_map["explore_agent"]


def test_has_any():
    eff = EffectiveToolset({"read_artifact": ToolPermission.AUTO})
    assert eff.has_any(["create_artifact", "read_artifact"]) is True
    assert eff.has_any(["bash", "mount"]) is False


# ============================================================
# B-3 渐进式披露:deferred_units + 显式 search_tools 配置
# ============================================================


def test_deferred_unit_members_callable_and_grouped():
    # defer 的 unit:成员仍可调(进 permissions),且记进 deferred_units 供索引行渲染
    agent = _agent(
        builtin_tools={"search_tools": "enabled"},
        units={"github": "enabled"},
    )
    snap = _snapshot(units=[
        _unit("github", ["github__a", "github__b"], kind="toolset",
              defer=True, description="GitHub API"),
    ])
    tools = {
        "github__a": _Tool("github__a", ToolPermission.AUTO),
        "github__b": _Tool("github__b", ToolPermission.CONFIRM),
        "search_tools": _Tool("search_tools", ToolPermission.AUTO),
    }
    eff = resolve_effective_toolset(agent, snap, tools)
    # 成员可调
    assert "github__a" in eff and "github__b" in eff
    # 分组进 deferred_units
    assert set(eff.deferred_units) == {"github"}
    du = eff.deferred_units["github"]
    assert du.description == "GitHub API"
    assert du.member_full_names == ["github__a", "github__b"]
    assert eff.deferred_member_names() == {"github__a", "github__b"}


def test_non_deferred_unit_not_grouped():
    agent = _agent(
        builtin_tools={"search_tools": "enabled"},
        units={"github": "enabled"},
    )
    snap = _snapshot(units=[_unit("github", ["github__a"], kind="toolset", defer=False)])
    tools = {"github__a": _Tool("github__a", ToolPermission.AUTO)}
    eff = resolve_effective_toolset(agent, snap, tools)
    assert "github__a" in eff
    assert eff.deferred_units == {}


def test_deferred_unit_only_present_members_grouped():
    # 索引行只列工具对象存在的成员(不挂死链)
    agent = _agent(
        builtin_tools={"search_tools": "enabled"},
        units={"github": "enabled"},
    )
    snap = _snapshot(units=[_unit("github", ["github__a", "github__b"],
                                  kind="toolset", defer=True)])
    tools = {
        "github__a": _Tool("github__a", ToolPermission.AUTO),  # b 缺工具对象
        "search_tools": _Tool("search_tools", ToolPermission.AUTO),
    }
    eff = resolve_effective_toolset(agent, snap, tools)
    assert eff.deferred_units["github"].member_full_names == ["github__a"]


def test_deferred_unit_without_configured_search_tools_uses_full_schema():
    # defer 是 best-effort 优化；未配置 search_tools 时不扩权，成员按完整 schema 暴露。
    agent = _agent(units={"github": "enabled"})
    snap = _snapshot(units=[_unit("github", ["github__a"], kind="toolset", defer=True)])
    tools = {
        "github__a": _Tool("github__a", ToolPermission.AUTO),
        "search_tools": _Tool("search_tools", ToolPermission.AUTO),
    }
    eff = resolve_effective_toolset(agent, snap, tools)
    assert "search_tools" not in eff
    assert "github__a" in eff
    assert eff.deferred_units == {}
    assert eff.deferred_member_names() == set()


def test_deferred_unit_with_discovery_error_still_registers_index_row():
    agent = _agent(
        builtin_tools={"search_tools": "enabled"},
        units={"inventory": "enabled"},
    )
    snap = _snapshot(units=[
        _unit(
            "inventory",
            [],
            kind="mcp",
            defer=True,
            description="Inventory MCP",
            discovery_error="MCP server is unavailable",
        )
    ])
    tools = {"search_tools": _Tool("search_tools", ToolPermission.AUTO)}

    eff = resolve_effective_toolset(agent, snap, tools)

    assert "search_tools" in eff
    assert eff.deferred_units["inventory"].member_full_names == []
    assert eff.deferred_units["inventory"].discovery_error == "MCP server is unavailable"


def test_configured_search_tools_remains_available_without_deferred_unit():
    # Agent 配置是成员关系唯一来源；没有 deferred unit 也不自动删已配置 builtin。
    agent = _agent(
        builtin_tools={"search_tools": "enabled"},
        units={"github": "enabled"},
    )
    snap = _snapshot(units=[_unit("github", ["github__a"], kind="toolset", defer=False)])
    tools = {
        "github__a": _Tool("github__a", ToolPermission.AUTO),
        "search_tools": _Tool("search_tools", ToolPermission.AUTO),
    }
    eff = resolve_effective_toolset(agent, snap, tools)
    assert "search_tools" in eff
    assert eff.level("search_tools") == ToolPermission.AUTO


def test_configured_search_tools_object_absent_falls_back_to_full_schema():
    # 请求级对象缺席只能收窄配置；deferred unit 仍可用，改为完整 schema。
    agent = _agent(
        builtin_tools={"search_tools": "enabled"},
        units={"github": "enabled"},
    )
    snap = _snapshot(units=[_unit("github", ["github__a"], kind="toolset", defer=True)])
    tools = {"github__a": _Tool("github__a", ToolPermission.AUTO)}
    eff = resolve_effective_toolset(agent, snap, tools)
    assert "search_tools" not in eff
    assert eff.deferred_units == {}
    assert "github__a" in eff


def test_disabled_search_tools_is_not_injected_for_deferred_unit():
    agent = _agent(
        builtin_tools={"search_tools": "disabled"},
        units={"github": "enabled"},
    )
    snap = _snapshot(units=[_unit("github", ["github__a"], kind="toolset", defer=True)])
    tools = {
        "github__a": _Tool("github__a", ToolPermission.AUTO),
        "search_tools": _Tool("search_tools", ToolPermission.AUTO),
    }
    eff = resolve_effective_toolset(agent, snap, tools)
    assert "search_tools" not in eff
    assert "search_tools" in eff.disabled_tool_names
    assert eff.deferred_units == {}


# ============================================================
# G-0 部门规则:unit visibility + department_unit_rule 命中集合
# ============================================================


def test_public_unit_denied_by_department_match():
    # public 默认 allow;department_unit_rule 命中 = deny 例外。
    agent = _agent(units={"weather": "enabled"})
    snap = _snapshot(units=[_unit("weather", ["weather"], visibility="public")])
    tools = {"weather": _Tool("weather", ToolPermission.AUTO)}

    eff = resolve_effective_toolset(agent, snap, tools, dept_matched_units={"weather"})

    assert "weather" not in eff
    assert "weather" not in eff.disabled_tool_names


def test_department_unit_requires_department_match():
    # department 默认 deny;命中 = grant。
    agent = _agent(units={"reports": "enabled"})
    snap = _snapshot(units=[_unit("reports", ["reports"], visibility="department")])
    tools = {"reports": _Tool("reports", ToolPermission.AUTO)}

    denied = resolve_effective_toolset(agent, snap, tools, dept_matched_units=set())
    granted = resolve_effective_toolset(agent, snap, tools, dept_matched_units={"reports"})

    assert "reports" not in denied
    assert "reports" not in denied.disabled_tool_names
    assert "reports" in granted


def test_skill_grant_cannot_reopen_dept_denied_unit():
    # dept 收窄在 skill enable 之前:即使 skill allowed-tools 点名该 disabled unit,
    # public+dept 命中已把它移出宇宙,skill grant 不得重开。
    from reconcile.snapshot import SkillInfo

    agent = _agent(units={"weather": "disabled"})
    snap = _snapshot(units=[_unit("weather", ["weather"], visibility="public")])
    tools = {"weather": _Tool("weather", ToolPermission.AUTO)}
    skills = {
        "s": SkillInfo(
            id="s", slug="s", name="s", description="", visibility="public",
            default_enabled=True, owner_user_id=None, allowed_tools=["weather"],
        )
    }

    eff = resolve_effective_toolset(
        agent, snap, tools, skill_snapshot=skills, dept_matched_units={"weather"}
    )
    eff.activate_skill("s")

    assert "weather" not in eff
    assert "weather" not in eff.disabled_tool_names
