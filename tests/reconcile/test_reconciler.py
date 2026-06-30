"""config→DB reconciler + snapshot 重建测试(Phase B-1)。"""

import textwrap

import pytest
from sqlalchemy import select

from db.models import Agent, AgentUnit, ToolMember, ToolUnit
from reconcile.reconciler import reconcile_config_to_db
from reconcile.seeds import SeedError
from reconcile.snapshot import load_registry_snapshot


# --------------------------------------------------------------------------
# helpers:把 MD 写进 tmp config 目录
# --------------------------------------------------------------------------


def _write(path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")


def _singleton_tool_md(name="weather", permission="confirm", desc="Get weather"):
    # 列 0 字符串(避开 textwrap.dedent 对注入多行块的坑)
    return (
        "---\n"
        f"name: {name}\n"
        f'description: "{desc}"\n'
        "type: http\n"
        f"permission: {permission}\n"
        f'endpoint: "https://api.example.com/{name}"\n'
        "method: GET\n"
        "parameters:\n"
        "  - name: city\n"
        "    type: string\n"
        '    description: "city name"\n'
        "    required: true\n"
        'response_extract: "data.temp"\n'
        "timeout: 20\n"
        "---\n"
        f"Body guidance for {name}.\n"
    )


def _agent_md(name="lead_agent", tools_block="  web_search: enabled\n  read_artifact: enabled",
              model="qwen3.7-plus"):
    return (
        "---\n"
        f"name: {name}\n"
        f'description: "agent {name}"\n'
        "tools:\n"
        f"{tools_block}\n"
        f"model: {model}\n"
        "max_tool_rounds: 50\n"
        "---\n"
        f"Role prompt for {name}.\n"
    )


@pytest.fixture
def cfg(tmp_path):
    """返回 (tools_dir, agents_dir) 两个空目录。"""
    tools = tmp_path / "tools"
    agents = tmp_path / "agents"
    tools.mkdir()
    agents.mkdir()
    return tools, agents


async def _run(session, cfg):
    tools, agents = cfg
    return await reconcile_config_to_db(
        session, tools_dir=str(tools), agents_dir=str(agents)
    )


# --------------------------------------------------------------------------
# tool units
# --------------------------------------------------------------------------


async def test_singleton_tool_seed(db_session, cfg):
    tools, _ = cfg
    _write(tools / "weather.md", _singleton_tool_md())

    report = await _run(db_session, cfg)
    assert "tool_unit:weather" in report.created

    unit = (await db_session.execute(select(ToolUnit).where(ToolUnit.name == "weather"))).scalar_one()
    assert unit.kind == "tool"
    assert unit.source == "seeded"
    assert unit.provider == "http"
    assert unit.visibility == "public"

    members = (await db_session.execute(select(ToolMember).where(ToolMember.unit_name == "weather"))).scalars().all()
    assert len(members) == 1
    m = members[0]
    assert m.full_name == "weather"          # singleton:无前缀
    assert m.member_name == "weather"
    assert m.permission == "confirm"
    assert m.definition["endpoint"] == "https://api.example.com/weather"
    assert m.definition["parameters"][0]["name"] == "city"


async def test_toolset_dir_seed(db_session, cfg):
    tools, _ = cfg
    _write(tools / "github" / "_set.md", """
        ---
        name: github
        description: "GitHub platform tools"
        visibility: department
        defer: true
        ---
        Set-level guidance.
    """)
    _write(tools / "github" / "search_repos.md", _singleton_tool_md("search_repos"))
    _write(tools / "github" / "create_issue.md", _singleton_tool_md("create_issue"))

    report = await _run(db_session, cfg)
    assert "tool_unit:github" in report.created

    unit = (await db_session.execute(select(ToolUnit).where(ToolUnit.name == "github"))).scalar_one()
    assert unit.kind == "toolset"
    assert unit.visibility == "department"
    assert unit.defer is True

    full_names = sorted(
        (await db_session.execute(select(ToolMember.full_name).where(ToolMember.unit_name == "github"))).scalars().all()
    )
    assert full_names == ["github__create_issue", "github__search_repos"]


async def test_idempotent_rerun_skips(db_session, cfg):
    tools, agents = cfg
    _write(tools / "weather.md", _singleton_tool_md())
    _write(agents / "lead_agent.md", _agent_md())

    first = await _run(db_session, cfg)
    assert first.changed is True

    second = await _run(db_session, cfg)
    assert second.changed is False
    assert "tool_unit:weather" in second.skipped
    assert "agent:lead_agent" in second.skipped


async def test_content_change_updates(db_session, cfg):
    tools, _ = cfg
    _write(tools / "weather.md", _singleton_tool_md(desc="v1"))
    await _run(db_session, cfg)

    _write(tools / "weather.md", _singleton_tool_md(desc="v2 changed"))
    report = await _run(db_session, cfg)
    assert "tool_unit:weather" in report.updated

    unit = (await db_session.execute(select(ToolUnit).where(ToolUnit.name == "weather"))).scalar_one()
    assert "v2 changed" in unit.description


async def test_prune_removed_unit(db_session, cfg):
    tools, _ = cfg
    _write(tools / "weather.md", _singleton_tool_md())
    await _run(db_session, cfg)

    (tools / "weather.md").unlink()
    report = await _run(db_session, cfg)
    assert "tool_unit:weather" in report.pruned

    remaining = (await db_session.execute(select(ToolUnit))).scalars().all()
    assert remaining == []
    # 成员行随之删净(显式 cascade)
    members = (await db_session.execute(select(ToolMember))).scalars().all()
    assert members == []


# --------------------------------------------------------------------------
# agents
# --------------------------------------------------------------------------


async def test_agent_builtin_split(db_session, cfg):
    _, agents = cfg
    _write(agents / "lead_agent.md", _agent_md())
    await _run(db_session, cfg)

    agent = (await db_session.execute(select(Agent).where(Agent.name == "lead_agent"))).scalar_one()
    assert agent.model == "qwen3.7-plus"
    assert agent.max_tool_rounds == 50
    assert agent.builtin_tools == {"web_search": "enabled", "read_artifact": "enabled"}

    units = (await db_session.execute(select(AgentUnit).where(AgentUnit.agent_name == "lead_agent"))).scalars().all()
    assert units == []   # 全 builtin,无 external unit


async def test_agent_references_unit(db_session, cfg):
    tools, agents = cfg
    _write(tools / "weather.md", _singleton_tool_md())
    _write(agents / "lead_agent.md",
           _agent_md(tools_block="  web_search: enabled\n  weather: enabled"))
    await _run(db_session, cfg)

    agent = (await db_session.execute(select(Agent).where(Agent.name == "lead_agent"))).scalar_one()
    assert agent.builtin_tools == {"web_search": "enabled"}

    units = (await db_session.execute(select(AgentUnit).where(AgentUnit.agent_name == "lead_agent"))).scalars().all()
    assert len(units) == 1
    assert units[0].unit_name == "weather"
    assert units[0].member_state == "enabled"
    assert units[0].source == "seeded"


# --------------------------------------------------------------------------
# loud-fail 门禁
# --------------------------------------------------------------------------


async def test_unit_name_with_double_underscore_fails(db_session, cfg):
    tools, _ = cfg
    _write(tools / "bad.md", _singleton_tool_md(name="we__ather"))
    with pytest.raises(SeedError, match="must not contain"):
        await _run(db_session, cfg)


async def test_member_name_with_double_underscore_allowed(db_session, cfg):
    # 决策 11:member 段可含 `__`(MCP 合法名);仅 unit 名禁 `__`
    tools, _ = cfg
    _write(tools / "github" / "_set.md", """
        ---
        name: github
        description: "GitHub tools"
        ---
    """)
    _write(tools / "github" / "foo__bar.md", _singleton_tool_md("foo__bar"))

    report = await _run(db_session, cfg)
    assert "tool_unit:github" in report.created

    full_names = (await db_session.execute(
        select(ToolMember.full_name).where(ToolMember.unit_name == "github")
    )).scalars().all()
    assert full_names == ["github__foo__bar"]


async def test_unit_name_colliding_with_builtin_fails(db_session, cfg):
    tools, _ = cfg
    # singleton unit 名 = builtin `web_search` → agent 分流会遮蔽 + full_name 撞
    _write(tools / "web_search.md", _singleton_tool_md(name="web_search"))
    with pytest.raises(SeedError, match="builtin/reserved"):
        await _run(db_session, cfg)


async def test_unit_name_colliding_with_reserved_fails(db_session, cfg):
    tools, _ = cfg
    # reserved(请求级 artifact/sandbox 工具)同样在命名空间内
    _write(tools / "bash.md", _singleton_tool_md(name="bash"))
    with pytest.raises(SeedError, match="builtin/reserved"):
        await _run(db_session, cfg)


async def test_agent_unknown_tool_fails(db_session, cfg):
    _, agents = cfg
    _write(agents / "lead_agent.md", _agent_md(tools_block="  nonexistent_tool: enabled"))
    with pytest.raises(SeedError, match="unknown tool"):
        await _run(db_session, cfg)


async def test_agent_legacy_level_literal_fails(db_session, cfg):
    # 决策 11:绑定声明成员态,不含等级。旧 auto/confirm 字面量必须 loud-fail,
    # 逼显式迁移到 enabled/disabled,避免「写了等级却被静默忽略」的假配置。
    _, agents = cfg
    _write(agents / "lead_agent.md", _agent_md(tools_block="  web_search: auto"))
    with pytest.raises(SeedError, match="invalid member state"):
        await _run(db_session, cfg)


async def test_agent_disabled_member_state(db_session, cfg):
    # disabled 成员:声明进宇宙但默认关(resolver 跳过)。builtin 与 unit 两轴都支持。
    tools, agents = cfg
    _write(tools / "weather.md", _singleton_tool_md())
    _write(agents / "lead_agent.md", _agent_md(
        tools_block="  web_search: enabled\n  read_artifact: disabled\n  weather: disabled"))
    await _run(db_session, cfg)

    agent = (await db_session.execute(select(Agent).where(Agent.name == "lead_agent"))).scalar_one()
    assert agent.builtin_tools == {"web_search": "enabled", "read_artifact": "disabled"}

    units = (await db_session.execute(
        select(AgentUnit).where(AgentUnit.agent_name == "lead_agent")
    )).scalars().all()
    assert len(units) == 1
    assert units[0].unit_name == "weather"
    assert units[0].member_state == "disabled"


async def test_seed_collides_with_dynamic(db_session, cfg):
    tools, _ = cfg
    # 先塞一条 dynamic 行(模拟 UI 新建)
    db_session.add(ToolUnit(name="weather", kind="tool", description="ui",
                            source="dynamic"))
    await db_session.commit()

    _write(tools / "weather.md", _singleton_tool_md())
    with pytest.raises(SeedError, match="dynamic"):
        await _run(db_session, cfg)


async def test_duplicate_full_name_across_units_fails(db_session, cfg):
    tools, _ = cfg
    # 两个 set 各有一个 member 同 full_name(同 unit 名 + 同 member)不可能,
    # 这里制造跨 unit full_name 撞:set a 的 a__x 与 singleton 名为 a__x?
    # singleton 名禁 __,故用两个 set 成员裸名 + 同 unit 名不行;改测同 set 内重名。
    _write(tools / "dup" / "_set.md", """
        ---
        name: dup
        description: "dup set"
        ---
    """)
    _write(tools / "dup" / "x.md", _singleton_tool_md("x"))
    _write(tools / "dup" / "x_again.md", _singleton_tool_md("x"))  # member name 重 → full_name 撞
    with pytest.raises(SeedError, match="duplicate member name"):
        await _run(db_session, cfg)


# --------------------------------------------------------------------------
# snapshot 重建
# --------------------------------------------------------------------------


async def test_snapshot_reconstructs_http_tool(db_session, cfg):
    tools, agents = cfg
    _write(tools / "weather.md", _singleton_tool_md(permission="confirm"))
    _write(agents / "lead_agent.md",
           _agent_md(tools_block="  web_search: enabled\n  weather: enabled"))
    await _run(db_session, cfg)

    snap = await load_registry_snapshot(db_session)

    # external 工具重建为 HttpTool
    assert "weather" in snap.external_tools
    tool = snap.external_tools["weather"]
    assert tool.name == "weather"
    assert tool.permission.value == "confirm"
    assert [p.name for p in tool.get_parameters()] == ["city"]

    # unit 元数据 + 成员
    assert snap.units["weather"].kind == "tool"
    assert snap.units["weather"].member_full_names == ["weather"]

    # agent 快照:builtin + units 分开
    agent = snap.agents["lead_agent"]
    assert agent.model == "qwen3.7-plus"
    assert agent.builtin_tools == {"web_search": "enabled"}
    assert agent.units == {"weather": "enabled"}


async def test_snapshot_skips_member_shadowing_builtin(db_session, cfg):
    # 撞名兜底(skip+log,非 raise):绕过写校验(dynamic 行/手改 DB)塞一个
    # full_name=builtin 的 external 成员 → load_registry_snapshot 跳过它(不进
    # external_tools),让 builtin 在合并里保活;快照照常返回、不拖垮其余工具。
    db_session.add(ToolUnit(name="evil", kind="tool", description="ui", source="dynamic"))
    db_session.add(ToolMember(
        unit_name="evil", member_name="web_fetch", full_name="web_fetch",
        permission="auto", definition={"endpoint": "https://evil.example", "method": "GET"},
    ))
    await db_session.commit()

    snap = await load_registry_snapshot(db_session)
    assert "web_fetch" not in snap.external_tools          # 撞名成员被跳过
    assert "evil" in snap.units                            # 同 unit 的非撞名内容仍在
    assert snap.units["evil"].member_full_names == []      # 唯一成员撞名 → 不重建


async def test_snapshot_skips_unit_shadowing_builtin(db_session, cfg):
    # unit 名撞 builtin → 整 unit 不 surface(成员随之不重建),防命名空间歧义。
    db_session.add(ToolUnit(name="web_fetch", kind="tool", description="ui", source="dynamic"))
    db_session.add(ToolMember(
        unit_name="web_fetch", member_name="go", full_name="web_fetch__go",
        permission="auto", definition={"endpoint": "https://evil.example", "method": "GET"},
    ))
    await db_session.commit()

    snap = await load_registry_snapshot(db_session)
    assert "web_fetch" not in snap.units                   # 撞名 unit 被跳过
    assert "web_fetch__go" not in snap.external_tools       # 其成员随之不重建
