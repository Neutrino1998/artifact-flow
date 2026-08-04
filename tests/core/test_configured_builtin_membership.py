"""Agent 配置驱动的 builtin 成员关系回归测试。

read_skill、mount_skill、search_tools 与其它 builtin 采用同一规则：配置为 enabled
才进入初始可调集，配置为 disabled 才可能被 skill 直接点名激活，absent 永不注入。
defer 只是 schema 披露优化；没有 search_tools 时回退完整 schema。
"""

from core.context_manager import ContextManager
from core.effective_toolset import resolve_all, resolve_effective_toolset
from reconcile.snapshot import AgentSnapshot, RegistrySnapshot, SkillInfo, UnitInfo
from tools.base import ToolPermission


class _Tool:
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


def _unit(
    name,
    members,
    *,
    defer=False,
    description="",
    kind="toolset",
    provider="http",
    discovery_error=None,
):
    return UnitInfo(
        name=name,
        kind=kind,
        description=description,
        visibility="public",
        defer=defer,
        provider=provider,
        source="seeded",
        discovery_error=discovery_error,
        member_full_names=list(members),
    )


def _snapshot(units=None, agents=None):
    return RegistrySnapshot(
        external_tools={},
        units={u.name: u for u in (units or [])},
        agents={a.name: a for a in (agents or [])},
    )


def _skill(slug, allowed):
    return SkillInfo(
        id=slug,
        slug=slug,
        name=slug,
        description="",
        visibility="public",
        default_enabled=True,
        owner_user_id=None,
        allowed_tools=allowed,
    )


def test_request_builtins_require_agent_configuration():
    tools = {
        "read_skill": _Tool("read_skill", ToolPermission.AUTO),
        "mount_skill": _Tool("mount_skill", ToolPermission.AUTO),
        "search_tools": _Tool("search_tools", ToolPermission.AUTO),
    }
    snap = _snapshot(agents=[
        _agent(
            "lead_agent",
            builtin_tools={
                "read_skill": "enabled",
                "mount_skill": "enabled",
                "search_tools": "enabled",
            },
        ),
        _agent("vision_agent"),
    ])

    result = resolve_all(snap, tools)

    assert set(result["lead_agent"].names()) == set(tools)
    assert result["vision_agent"].names() == []


def test_configured_request_builtin_is_narrowed_when_object_absent():
    agent = _agent(builtin_tools={"read_skill": "enabled"})

    effective = resolve_effective_toolset(agent, _snapshot(), {})

    assert "read_skill" not in effective


def test_activating_bash_does_not_inject_mount_skill():
    agent = _agent(
        builtin_tools={"read_skill": "enabled", "bash": "disabled"}
    )
    tools = {
        "read_skill": _Tool("read_skill", ToolPermission.AUTO),
        "bash": _Tool("bash", ToolPermission.CONFIRM),
        "mount_skill": _Tool("mount_skill", ToolPermission.AUTO),
    }
    effective = resolve_effective_toolset(
        agent,
        _snapshot(),
        tools,
        {"sandbox": _skill("sandbox", ["bash"])},
    )

    effective.activate_skill("sandbox")

    assert "bash" in effective
    assert "mount_skill" not in effective
    assert effective.activatable_tool_names() == {"bash"}


def test_explicit_mount_skill_is_available_without_hidden_dependency_rule():
    agent = _agent(
        builtin_tools={
            "read_skill": "enabled",
            "mount_skill": "enabled",
            "bash": "disabled",
        }
    )
    tools = {
        "read_skill": _Tool("read_skill", ToolPermission.AUTO),
        "mount_skill": _Tool("mount_skill", ToolPermission.AUTO),
        "bash": _Tool("bash", ToolPermission.CONFIRM),
    }
    effective = resolve_effective_toolset(
        agent,
        _snapshot(),
        tools,
        {"sandbox": _skill("sandbox", ["bash"])},
    )

    assert "mount_skill" in effective
    assert "bash" not in effective
    effective.activate_skill("sandbox")
    assert "mount_skill" in effective and "bash" in effective


def test_deferred_skill_grant_without_search_tools_uses_full_schema():
    unit = _unit("github", ["github__list"], defer=True)
    agent = _agent(units={"github": "disabled"})
    tools = {
        "github__list": _Tool("github__list", ToolPermission.AUTO),
        "search_tools": _Tool("search_tools", ToolPermission.AUTO),
    }
    effective = resolve_effective_toolset(
        agent,
        _snapshot(units=[unit]),
        tools,
        {"github_skill": _skill("github_skill", ["github"])},
    )

    effective.activate_skill("github_skill")

    assert "github__list" in effective
    assert "search_tools" not in effective
    assert effective.deferred_units == {}
    assert effective.deferred_member_names() == set()


def test_deferred_skill_grant_uses_explicitly_configured_search_tools():
    unit = _unit("github", ["github__list"], defer=True)
    agent = _agent(
        builtin_tools={"search_tools": "enabled"},
        units={"github": "disabled"},
    )
    tools = {
        "github__list": _Tool("github__list", ToolPermission.AUTO),
        "search_tools": _Tool("search_tools", ToolPermission.AUTO),
    }
    effective = resolve_effective_toolset(
        agent,
        _snapshot(units=[unit]),
        tools,
        {"github_skill": _skill("github_skill", ["github"])},
    )

    assert "search_tools" in effective
    effective.activate_skill("github_skill")
    assert effective.deferred_member_names() == {"github__list"}


def test_discovery_error_only_skill_grant_is_visible_after_activation():
    unit = _unit(
        "inventory",
        [],
        kind="mcp",
        provider="mcp",
        defer=True,
        description="Inventory server",
        discovery_error="MCP server is unavailable",
    )
    agent = _agent(
        builtin_tools={"search_tools": "enabled"},
        units={"inventory": "disabled"},
    )
    tools = {"search_tools": _Tool("search_tools", ToolPermission.AUTO)}
    effective = resolve_effective_toolset(
        agent,
        _snapshot(units=[unit]),
        tools,
        {"inventory_skill": _skill("inventory_skill", ["inventory"])},
    )

    grant = effective.skill_grants["inventory_skill"]
    assert grant.permissions == {}
    assert grant.tool_units["inventory"].discovery_error == "MCP server is unavailable"

    effective.activate_skill("inventory_skill")

    assert effective.tool_units["inventory"].discovery_error == "MCP server is unavailable"
    assert effective.deferred_units["inventory"].discovery_error == "MCP server is unavailable"
    catalog = ContextManager._build_available_tools(effective, tools, set())
    assert "MCP server is unavailable" in catalog


def test_skill_can_directly_activate_disabled_search_tools_and_deferred_unit():
    unit = _unit("github", ["github__list"], defer=True)
    agent = _agent(
        builtin_tools={"search_tools": "disabled"},
        units={"github": "disabled"},
    )
    tools = {
        "github__list": _Tool("github__list", ToolPermission.AUTO),
        "search_tools": _Tool("search_tools", ToolPermission.AUTO),
    }
    effective = resolve_effective_toolset(
        agent,
        _snapshot(units=[unit]),
        tools,
        {"github_skill": _skill("github_skill", ["github", "search_tools"])},
    )

    assert effective.activatable_tool_names() == {"github__list", "search_tools"}
    effective.activate_skill("github_skill")
    assert "search_tools" in effective
    assert effective.deferred_member_names() == {"github__list"}


def test_later_explicit_search_activation_can_defer_an_active_unit():
    unit = _unit("github", ["github__list"], defer=True)
    agent = _agent(
        builtin_tools={"search_tools": "disabled"},
        units={"github": "disabled"},
    )
    tools = {
        "github__list": _Tool("github__list", ToolPermission.AUTO),
        "search_tools": _Tool("search_tools", ToolPermission.AUTO),
    }
    effective = resolve_effective_toolset(
        agent,
        _snapshot(units=[unit]),
        tools,
        {
            "github_skill": _skill("github_skill", ["github"]),
            "search_skill": _skill("search_skill", ["search_tools"]),
        },
    )

    effective.activate_skill("github_skill")
    assert effective.deferred_units == {}
    effective.activate_skill("search_skill")
    assert effective.deferred_member_names() == {"github__list"}


def test_activation_is_idempotent_without_injection_closure():
    unit = _unit("github", ["github__list"], defer=True)
    agent = _agent(
        builtin_tools={"search_tools": "enabled"},
        units={"github": "disabled"},
    )
    tools = {
        "github__list": _Tool("github__list", ToolPermission.AUTO),
        "search_tools": _Tool("search_tools", ToolPermission.AUTO),
    }
    effective = resolve_effective_toolset(
        agent,
        _snapshot(units=[unit]),
        tools,
        {"github_skill": _skill("github_skill", ["github"])},
    )

    effective.activate_skill("github_skill")
    permissions = dict(effective.permissions)
    deferred_units = dict(effective.deferred_units)
    effective.activate_skill("github_skill")

    assert effective.permissions == permissions
    assert effective.deferred_units == deferred_units
