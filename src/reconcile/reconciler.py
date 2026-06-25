"""
config → DB 物化(async)。idempotent upsert(name 作 PK + 内容哈希)+ prune + 撞名 loud-fail。

不变量:
  - `seeded` 行 reconciler 拥有;碰到同名 `dynamic` 行(UI 新建)→ loud-fail,绝不覆盖。
  - hash 同 → skip(幂等);hash 异(同名)→ UPDATE 定义列,m2m 按 name 引用保留
    (例外:visibility 变更 → clear-on-visibility 钩子,见 `_clear_dept_rules_for_unit`)。
  - 删/改名 → 显式删子行(dialect-safe,不赖 FK cascade 的 per-connection pragma)。
"""

import os
from typing import Dict, List, Optional

from sqlalchemy import and_, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Agent, AgentUnit, ToolMember, ToolUnit
from reconcile.report import ReconcileReport
from reconcile.seeds import (
    AgentSeed,
    MemberSeed,
    SeedError,
    ToolUnitSeed,
    parse_agent_seeds,
    parse_tool_seeds,
)
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


def _default_config_dir(kind: str) -> str:
    # src/reconcile/reconciler.py → src → project_root
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(project_root, "config", kind)


async def reconcile_config_to_db(
    session: AsyncSession,
    *,
    tools_dir: Optional[str] = None,
    agents_dir: Optional[str] = None,
    commit: bool = True,
) -> ReconcileReport:
    """把 config/tools + config/agents 物化进 DB。tools 先(agent 分流需已知 unit)。"""
    tools_dir = tools_dir or _default_config_dir("tools")
    agents_dir = agents_dir or _default_config_dir("agents")
    report = ReconcileReport()

    tool_seeds = parse_tool_seeds(tools_dir)
    await _reconcile_tool_units(session, tool_seeds, report)

    # session 配 autoflush=False → 先 flush 让刚写的 unit/member 对下面的 SELECT 可见
    await session.flush()

    # agent 分流需要「已注册 unit」全集(seeded 刚写 + 任何已存在 dynamic)
    known_unit_names = set(
        (await session.execute(select(ToolUnit.name))).scalars().all()
    )
    fn_rows = (
        await session.execute(select(ToolMember.full_name, ToolMember.unit_name))
    ).all()
    known_full_names: Dict[str, str] = {fn: un for fn, un in fn_rows}

    agent_seeds = parse_agent_seeds(
        agents_dir,
        known_unit_names=known_unit_names,
        known_full_names=known_full_names,
    )
    await _reconcile_agents(session, agent_seeds, report)

    if commit:
        await session.commit()
    else:
        await session.flush()

    logger.info(report.summary())
    if report.pruned:
        # 改名/删配置会丢规则(决策 10:人工重授)→ loud-log,不静默归零
        logger.warning("reconcile pruned (rules dropped, re-grant manually): %s",
                       ", ".join(report.pruned))
    return report


# --------------------------------------------------------------------------
# tool units
# --------------------------------------------------------------------------


async def _reconcile_tool_units(
    session: AsyncSession, seeds: List[ToolUnitSeed], report: ReconcileReport
) -> None:
    existing = {
        u.name: u for u in (await session.execute(select(ToolUnit))).scalars().all()
    }
    desired = {s.name for s in seeds}

    for seed in seeds:
        label = f"tool_unit:{seed.name}"
        row = existing.get(seed.name)

        if row is None:
            session.add(_new_unit(seed))
            for m in seed.members:
                session.add(_new_member(seed.name, m))
            report.created.append(label)
            continue

        if row.source == "dynamic":
            raise SeedError(
                f"seed '{seed.name}' collides with a UI-created (dynamic) tool unit; "
                f"rename the config file or remove the dynamic unit"
            )
        if row.seed_hash == seed.seed_hash:
            report.skipped.append(label)
            continue

        # UPDATE:visibility 变更先清 dept 规则(决策 10),再覆盖定义列 + 重建成员
        if row.visibility != seed.visibility:
            await _clear_dept_rules_for_unit(session, seed.name)
        _apply_unit_cols(row, seed)
        await session.execute(
            delete(ToolMember).where(ToolMember.unit_name == seed.name)
        )
        for m in seed.members:
            session.add(_new_member(seed.name, m))
        report.updated.append(label)

    for name, row in existing.items():
        if name in desired or row.source != "seeded":
            continue
        await _prune_unit(session, name)
        report.pruned.append(f"tool_unit:{name}")


def _new_unit(seed: ToolUnitSeed) -> ToolUnit:
    return ToolUnit(
        name=seed.name,
        kind=seed.kind,
        description=seed.description,
        visibility=seed.visibility,
        defer=seed.defer,
        provider=seed.provider,
        source="seeded",
        seed_hash=seed.seed_hash,
    )


def _apply_unit_cols(row: ToolUnit, seed: ToolUnitSeed) -> None:
    row.kind = seed.kind
    row.description = seed.description
    row.visibility = seed.visibility
    row.defer = seed.defer
    row.provider = seed.provider
    row.seed_hash = seed.seed_hash


def _new_member(unit_name: str, m: MemberSeed) -> ToolMember:
    return ToolMember(
        unit_name=unit_name,
        member_name=m.member_name,
        full_name=m.full_name,
        permission=m.permission,
        definition=m.definition,
    )


async def _prune_unit(session: AsyncSession, name: str) -> None:
    # 显式删子行(dialect-safe);未来 department_unit_rule(C/G)在此一并删
    await session.execute(delete(AgentUnit).where(AgentUnit.unit_name == name))
    await session.execute(delete(ToolMember).where(ToolMember.unit_name == name))
    await session.execute(delete(ToolUnit).where(ToolUnit.name == name))


async def _clear_dept_rules_for_unit(session: AsyncSession, name: str) -> None:
    """改 visibility 清该 unit 的 dept 规则(决策 10 第二条路径)。

    当前空跑:`department_unit_rule` 表尚未存在。该表落地后在此 DELETE 它指向本 unit
    的行,与 Manager UI 改 visibility 路径同语义(否则 seeded 资源经 config 改
    visibility 时旧例外熬过 UPDATE、方向反转)。
    """
    return None


# --------------------------------------------------------------------------
# agents(seed-only 物化)
# --------------------------------------------------------------------------


async def _reconcile_agents(
    session: AsyncSession, seeds: List[AgentSeed], report: ReconcileReport
) -> None:
    existing = {
        a.name: a for a in (await session.execute(select(Agent))).scalars().all()
    }
    desired = {s.name for s in seeds}

    for seed in seeds:
        label = f"agent:{seed.name}"
        row = existing.get(seed.name)

        if row is None:
            session.add(_new_agent(seed))
            for u in seed.units:
                session.add(_new_agent_unit(seed.name, u))
            report.created.append(label)
            continue

        if row.source == "dynamic":
            raise SeedError(
                f"seed agent '{seed.name}' collides with a dynamic agent row"
            )
        if row.seed_hash == seed.seed_hash:
            report.skipped.append(label)
            continue

        _apply_agent_cols(row, seed)
        # 只替换 seeded agent_units,保留 dynamic(UI 挂载)
        await session.execute(
            delete(AgentUnit).where(
                and_(AgentUnit.agent_name == seed.name, AgentUnit.source == "seeded")
            )
        )
        for u in seed.units:
            session.add(_new_agent_unit(seed.name, u))
        report.updated.append(label)

    for name, row in existing.items():
        if name in desired or row.source != "seeded":
            continue
        await session.execute(delete(AgentUnit).where(AgentUnit.agent_name == name))
        await session.execute(delete(Agent).where(Agent.name == name))
        report.pruned.append(f"agent:{name}")


def _new_agent(seed: AgentSeed) -> Agent:
    return Agent(
        name=seed.name,
        description=seed.description,
        model=seed.model,
        max_tool_rounds=seed.max_tool_rounds,
        internal=seed.internal,
        role_prompt=seed.role_prompt,
        builtin_tools=seed.builtin_tools,
        source="seeded",
        seed_hash=seed.seed_hash,
    )


def _apply_agent_cols(row: Agent, seed: AgentSeed) -> None:
    row.description = seed.description
    row.model = seed.model
    row.max_tool_rounds = seed.max_tool_rounds
    row.internal = seed.internal
    row.role_prompt = seed.role_prompt
    row.builtin_tools = seed.builtin_tools
    row.seed_hash = seed.seed_hash


def _new_agent_unit(agent_name: str, u) -> AgentUnit:
    return AgentUnit(
        agent_name=agent_name,
        unit_name=u.unit_name,
        member_state=u.member_state,
        source="seeded",
    )
