"""
注册表快照(读侧)—— 从 DB 行重建运行期形状(external `HttpTool` + unit/agent 元数据)。

ORM 实例不外逃(CLAUDE.md):本模块在 session 内读行、就地重建出 detached 的
`HttpTool` / 纯 dataclass 返回,调用方拿不到 ORM 对象。
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Agent, AgentUnit, ToolMember, ToolUnit
from tools.base import BUILTIN_TOOL_NAMES, BaseTool, ToolParameter
from tools.custom.http_tool import HttpTool, HttpToolConfig


@dataclass
class UnitInfo:
    """unit 元数据 + 成员 full_name 列表(供披露 / 部门授权侧消费)。"""
    name: str
    kind: str
    description: str
    visibility: str
    defer: bool
    provider: str
    source: str
    member_full_names: List[str] = field(default_factory=list)


@dataclass
class AgentSnapshot:
    """agent 重建形状。`builtin_tools`/`units` 分开存(决策 11 两轴)。

    engine 消费侧的扁平 `{tool: level}` 合成属 `EffectiveToolset` resolver,不在此做。"""
    name: str
    description: str
    model: str
    max_tool_rounds: int
    internal: bool
    role_prompt: str
    builtin_tools: Dict[str, str] = field(default_factory=dict)   # {名: enabled|disabled}
    units: Dict[str, str] = field(default_factory=dict)           # {unit_name: enabled|disabled}


@dataclass
class RegistrySnapshot:
    external_tools: Dict[str, BaseTool]   # full_name -> HttpTool(external 单元成员)
    units: Dict[str, UnitInfo]            # unit_name -> UnitInfo
    agents: Dict[str, AgentSnapshot]      # agent_name -> AgentSnapshot


def build_http_tool(full_name: str, permission: str, definition: dict) -> HttpTool:
    """从 tool_member 行重建 HttpTool。full_name 作工具名、permission 作等级。"""
    params = [
        ToolParameter(
            name=p["name"],
            type=p.get("type", "string"),
            description=p.get("description", ""),
            required=p.get("required", True),
            default=p.get("default"),
            enum=p.get("enum"),
        )
        for p in (definition.get("parameters") or [])
    ]
    config = HttpToolConfig(
        name=full_name,
        description=definition.get("description", ""),
        permission=permission,
        endpoint=definition.get("endpoint", ""),
        method=definition.get("method", "GET"),
        headers=definition.get("headers", {}) or {},
        parameters=params,
        response_extract=definition.get("response_extract"),
        timeout=definition.get("timeout", 60),
    )
    return HttpTool(config)


async def load_registry_snapshot(session: AsyncSession) -> RegistrySnapshot:
    """一次性读全部注册表行,重建 external 工具 + unit 元数据 + agent 元数据。

    显式 order_by:本快照喂进系统提示词的工具渲染顺序(EffectiveToolset.names()),
    无序则 PG 不保证行序 → 提示词抖动击穿 APC 缓存 + prompt 快照 flaky。
    """
    units_rows = (await session.execute(
        select(ToolUnit).order_by(ToolUnit.name)
    )).scalars().all()
    member_rows = (await session.execute(
        select(ToolMember).order_by(ToolMember.full_name)
    )).scalars().all()
    agent_rows = (await session.execute(
        select(Agent).order_by(Agent.name)
    )).scalars().all()
    agent_unit_rows = (await session.execute(
        select(AgentUnit).order_by(AgentUnit.agent_name, AgentUnit.unit_name)
    )).scalars().all()

    units: Dict[str, UnitInfo] = {
        u.name: UnitInfo(
            name=u.name,
            kind=u.kind,
            description=u.description,
            visibility=u.visibility,
            defer=u.defer,
            provider=u.provider,
            source=u.source,
        )
        for u in units_rows
    }

    external_tools: Dict[str, BaseTool] = {}
    for m in member_rows:
        # 运行期撞名闸(decision-11 全局唯一不变量的运行期镜像):external full_name
        # 落进合并后的 tools dict,撞 builtin/reserved 名会**遮蔽 builtin 工具对象连同
        # 其 permission**(CONFIRM builtin 被换成任意 HttpTool = 权限绕过)。reconcile
        # 已在写入期对 seeded 撞名 loud-fail;此处是读侧兜底,堵 dynamic 行(B-4)/手改
        # DB 绕过写校验。B-4 CRUD 须在写入期校验,这道只作 backstop。
        if m.full_name in BUILTIN_TOOL_NAMES:
            raise RuntimeError(
                f"External tool full_name '{m.full_name}' (unit '{m.unit_name}') "
                f"collides with a builtin/reserved tool name — it would shadow the "
                f"builtin object and its permission. Remove/rename the DB row "
                f"(dynamic tools must not reuse builtin names)."
            )
        if m.unit_name in units:
            units[m.unit_name].member_full_names.append(m.full_name)
        # 仅 http provider 重建为 HttpTool;mcp provider 的成员运行期另接
        unit = units.get(m.unit_name)
        if unit is not None and unit.provider == "http":
            external_tools[m.full_name] = build_http_tool(
                m.full_name, m.permission, m.definition or {}
            )

    agents: Dict[str, AgentSnapshot] = {
        a.name: AgentSnapshot(
            name=a.name,
            description=a.description,
            model=a.model,
            max_tool_rounds=a.max_tool_rounds,
            internal=a.internal,
            role_prompt=a.role_prompt,
            builtin_tools=dict(a.builtin_tools or {}),
        )
        for a in agent_rows
    }
    for au in agent_unit_rows:
        agent = agents.get(au.agent_name)
        if agent is not None:
            agent.units[au.unit_name] = au.member_state

    return RegistrySnapshot(external_tools=external_tools, units=units, agents=agents)
