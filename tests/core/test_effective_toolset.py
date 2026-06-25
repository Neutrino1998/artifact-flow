"""EffectiveToolset resolver 单测(决策 11 单一解析点)。

覆盖:enabled/disabled/absent 三态、singleton 与 toolset 展开、等级取自工具对象
(非绑定)、缺席 unit 跳过、resolve_all 全 agent。
"""

from core.effective_toolset import (
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
        max_tool_rounds=10,
        internal=False,
        role_prompt="",
        builtin_tools=builtin_tools or {},
        units=units or {},
    )


def _unit(name, members, kind="tool"):
    return UnitInfo(
        name=name, kind=kind, description="", visibility="public",
        defer=False, provider="http", source="seeded",
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


def test_undeclared_tool_absent():
    agent = _agent(builtin_tools={"web_search": "enabled"})
    tools = {
        "web_search": _Tool("web_search", ToolPermission.AUTO),
        "web_fetch": _Tool("web_fetch", ToolPermission.CONFIRM),
    }
    eff = resolve_effective_toolset(agent, _snapshot(), tools)
    assert "web_fetch" not in eff  # 未声明 = 不在宇宙


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
