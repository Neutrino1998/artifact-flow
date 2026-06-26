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
from repositories.tool_credential_repo import ToolCredentialRepository
from tools.base import BaseTool, ToolParameter, is_builtin_name
from tools.custom.credentials import CredentialResolver
from tools.custom.http_tool import HttpTool, HttpToolConfig
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


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


def build_http_tool(
    full_name: str,
    permission: str,
    definition: dict,
    *,
    unit_name: Optional[str] = None,
    credential_resolver: Optional[CredentialResolver] = None,
) -> HttpTool:
    """从 tool_member 行重建 HttpTool。full_name 作工具名、permission 作等级。

    unit_name + credential_resolver 灌入运行期凭证通路(B-4):execute 期 {{NAME}}
    从 tool_credentials 按 unit 解密。两者缺省(测试直接调)→ HttpTool 回落 env 解析。
    """
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
    return HttpTool(config, unit_name=unit_name, credential_resolver=credential_resolver)


async def load_registry_snapshot(session: AsyncSession) -> RegistrySnapshot:
    """一次性读全部注册表行,重建 external 工具 + unit 元数据 + agent 元数据。

    显式 order_by 定序的是 **external/unit 轴**(member/agent_unit 行序)—— 这一轴喂
    进系统提示词的工具渲染顺序(EffectiveToolset.names() 里 unit 展开部分),无序则
    PG 不保证行序 → 提示词抖动击穿 APC 缓存 + prompt 快照 flaky。**builtin 轴**的顺序
    另有来源:`agent.builtin_tools` JSON key 序 = reconcile 时的 MD 声明序,由
    order-preserving `JSON` 列(非 `JSONB`)在 PG/SQLite 都保序,不归这里的 order_by 管。

    撞名兜底(skip+log,非 raise):external 名撞 builtin/reserved 时**跳过该行 + 打
    WARNING**,让 builtin 对象在 controller_factory 合并里继续活(消除遮蔽 = 权限绕过)。
    不 raise —— 本函数每 turn 每用户都跑,一行坏数据 raise 会拖垮全机群;主防线是写入期
    loud-fail(reconcile / B-4 CRUD),这里只作兜底,不该有全局爆炸半径。
    """
    # 单个 resolver 喂给本快照所有 HttpTool:句柄带 turn session(经 repo),execute 期
    # 按 unit 名 lazy 查密文 + 解密。密文不在此预载(故意无 ToolUnit→credentials 关系)。
    credential_resolver = CredentialResolver(ToolCredentialRepository(session))

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

    units: Dict[str, UnitInfo] = {}
    for u in units_rows:
        if is_builtin_name(u.name):
            # unit 名撞 builtin/reserved → 不 surface(其成员随之不重建,见下),
            # 防 B-3/B-4/C 精确匹配路径的命名空间歧义。
            logger.warning(
                "Skipping external tool unit %r: name collides with a builtin/reserved "
                "tool name (row bypassed write-time validation — fix the DB row)",
                u.name,
            )
            continue
        units[u.name] = UnitInfo(
            name=u.name,
            kind=u.kind,
            description=u.description,
            visibility=u.visibility,
            defer=u.defer,
            provider=u.provider,
            source=u.source,
        )

    external_tools: Dict[str, BaseTool] = {}
    for m in member_rows:
        # 撞名兜底:full_name 撞 builtin/reserved → 跳过(不进 external_tools),让
        # builtin 在 controller_factory 合并里保活(消除遮蔽 = 权限绕过)。skip+log
        # 而非 raise:本函数每 turn 每用户跑,raise = 一行坏数据拖垮全机群;主防线在
        # 写入期(reconcile / B-4 CRUD loud-fail),这里只兜绕过写校验的行。
        if is_builtin_name(m.full_name):
            logger.warning(
                "Skipping external tool member %r (unit %r): full_name collides with a "
                "builtin/reserved tool name — builtin kept live (fix the DB row)",
                m.full_name, m.unit_name,
            )
            continue
        # unit 名撞 builtin 被上面跳过的,其成员 m.unit_name ∉ units → 自然不重建
        # (无需在此再判 / 再 log,unit 级 WARNING 已覆盖)。
        if m.unit_name in units:
            units[m.unit_name].member_full_names.append(m.full_name)
        # 仅 http provider 重建为 HttpTool;mcp provider 的成员运行期另接
        unit = units.get(m.unit_name)
        if unit is not None and unit.provider == "http":
            external_tools[m.full_name] = build_http_tool(
                m.full_name, m.permission, m.definition or {},
                unit_name=m.unit_name,
                credential_resolver=credential_resolver,
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
