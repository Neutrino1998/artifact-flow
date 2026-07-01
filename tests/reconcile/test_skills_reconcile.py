"""config→DB skill reconciler + snapshot 测试(Phase C-1;bundle = D-1)。"""

import io
import logging
import os
import textwrap
import zipfile

import pytest
from sqlalchemy import select

from db.models import (
    Department,
    DepartmentSkillRule,
    DepartmentUnitRule,
    Skill,
    ToolUnit,
    User,
    UserSkill,
)
from reconcile.reconciler import reconcile_config_to_db
from reconcile.seeds import SeedError, parse_skill_seeds
from reconcile.snapshot import load_skill_snapshot
from repositories.skill_repo import SkillRepository


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _write(path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")


def _skill_md(name="demo-skill", description="Use when demoing.",
              allowed_tools="  - read_artifact\n", visibility=None,
              default_enabled=None, extra="", body="Body guidance."):
    lines = ["---\n", f"name: {name}\n", f"description: {description}\n"]
    if allowed_tools is not None:
        lines.append("allowed-tools:\n")
        lines.append(allowed_tools)
    if visibility is not None:
        lines.append(f"visibility: {visibility}\n")
    if default_enabled is not None:
        lines.append(f"default_enabled: {str(default_enabled).lower()}\n")
    if extra:
        lines.append(extra)
    lines.append("---\n")
    lines.append(f"{body}\n")
    return "".join(lines)


def _singleton_tool_md(name="weather"):
    return (
        "---\n"
        f"name: {name}\n"
        f'description: "Get {name}"\n'
        "type: http\n"
        "permission: confirm\n"
        f'endpoint: "https://api.example.com/{name}"\n'
        "method: GET\n"
        "---\n"
        "Body.\n"
    )


@pytest.fixture
def cfg(tmp_path):
    """(tools, agents, skills) 三个空目录。"""
    tools = tmp_path / "tools"
    agents = tmp_path / "agents"
    skills = tmp_path / "skills"
    tools.mkdir()
    agents.mkdir()
    skills.mkdir()
    return tools, agents, skills


async def _run(session, cfg):
    tools, agents, skills = cfg
    return await reconcile_config_to_db(
        session, tools_dir=str(tools), agents_dir=str(agents), skills_dir=str(skills)
    )


# --------------------------------------------------------------------------
# 基本 upsert / 幂等 / 列映射
# --------------------------------------------------------------------------


async def test_skill_seed_created_and_columns(db_session, cfg):
    _, _, skills = cfg
    _write(skills / "demo-skill" / "SKILL.md", _skill_md(
        name="demo-skill",
        description="Use when X.",
        allowed_tools="  - read_artifact\n  - update_artifact\n",
        extra="license: MIT\nmetadata:\n  version: \"1.2\"\n",
        body="Detailed guidance body.",
    ))

    report = await _run(db_session, cfg)
    assert "skill:demo-skill" in report.created

    row = (await db_session.execute(
        select(Skill).where(Skill.slug == "demo-skill")
    )).scalar_one()
    assert row.name == "demo-skill"
    assert row.description == "Use when X."
    assert row.visibility == "public"        # default
    assert row.default_enabled is True        # default
    assert row.owner_user_id is None          # seeded = shared
    assert row.source == "seeded"
    assert row.allowed_tools == ["read_artifact", "update_artifact"]
    assert row.skill_md == "Detailed guidance body."
    # license/metadata → meta JSON 杂项列(系统不单独消费)
    assert row.meta == {"license": "MIT", "metadata": {"version": "1.2"}}
    assert row.bundle is None                 # 单 SKILL.md 无 bundle


async def test_skill_idempotent_then_update(db_session, cfg):
    _, _, skills = cfg
    _write(skills / "demo-skill" / "SKILL.md", _skill_md(body="v1"))
    assert "skill:demo-skill" in (await _run(db_session, cfg)).created

    # 重跑无改 → skipped
    r2 = await _run(db_session, cfg)
    assert "skill:demo-skill" in r2.skipped
    assert not r2.changed

    # 改正文 → updated
    _write(skills / "demo-skill" / "SKILL.md", _skill_md(body="v2"))
    r3 = await _run(db_session, cfg)
    assert "skill:demo-skill" in r3.updated
    row = (await db_session.execute(
        select(Skill).where(Skill.slug == "demo-skill")
    )).scalar_one()
    assert row.skill_md == "v2"


async def test_skill_prune_cascades_user_and_dept_rules(db_session, cfg):
    _, _, skills = cfg
    _write(skills / "gone" / "SKILL.md", _skill_md(name="gone"))
    await _run(db_session, cfg)

    # 建一个 user + department + 指向 skill 的 user_skill / dept rule
    db_session.add(User(id="u1", username="u1", hashed_password="x"))
    db_session.add(Department(id="d1", name="dept1"))
    await db_session.flush()
    db_session.add(UserSkill(user_id="u1", skill_slug="gone", enabled=True))
    db_session.add(DepartmentSkillRule(department_id="d1", skill_slug="gone"))
    await db_session.flush()

    # 从 config 删掉 skill → prune + 显式删子行
    (skills / "gone" / "SKILL.md").unlink()
    (skills / "gone").rmdir()
    report = await _run(db_session, cfg)
    assert "skill:gone" in report.pruned

    assert (await db_session.execute(select(Skill).where(Skill.slug == "gone"))).first() is None
    assert (await db_session.execute(
        select(UserSkill).where(UserSkill.skill_slug == "gone")
    )).first() is None
    assert (await db_session.execute(
        select(DepartmentSkillRule).where(DepartmentSkillRule.skill_slug == "gone")
    )).first() is None


# --------------------------------------------------------------------------
# clear-on-visibility(决策 10:改 visibility 清 dept 规则、留 user_skill)
# --------------------------------------------------------------------------


async def test_skill_visibility_change_clears_dept_rules_keeps_user(db_session, cfg):
    _, _, skills = cfg
    _write(skills / "s" / "SKILL.md", _skill_md(name="s", visibility="public"))
    await _run(db_session, cfg)

    db_session.add(User(id="u1", username="u1", hashed_password="x"))
    db_session.add(Department(id="d1", name="dept1"))
    await db_session.flush()
    db_session.add(UserSkill(user_id="u1", skill_slug="s", enabled=False))
    db_session.add(DepartmentSkillRule(department_id="d1", skill_slug="s"))
    await db_session.flush()

    # public → department:dept rule 方向会翻转 → 必须清,user_skill 保留
    _write(skills / "s" / "SKILL.md", _skill_md(name="s", visibility="department"))
    report = await _run(db_session, cfg)
    assert "skill:s" in report.updated

    assert (await db_session.execute(
        select(DepartmentSkillRule).where(DepartmentSkillRule.skill_slug == "s")
    )).first() is None
    us = (await db_session.execute(
        select(UserSkill).where(UserSkill.skill_slug == "s")
    )).scalar_one()
    assert us.enabled is False


# --------------------------------------------------------------------------
# 撞名 / 校验 loud-fail
# --------------------------------------------------------------------------


async def test_seed_collides_with_dynamic_skill(db_session, cfg):
    _, _, skills = cfg
    db_session.add(Skill(slug="dup", name="dup", source="dynamic", skill_md="x"))
    await db_session.flush()

    _write(skills / "dup" / "SKILL.md", _skill_md(name="dup"))
    with pytest.raises(SeedError, match="collides with a UI-uploaded"):
        await _run(db_session, cfg)


async def test_seeded_private_rejected(db_session, cfg):
    _, _, skills = cfg
    _write(skills / "p" / "SKILL.md", _skill_md(name="p", visibility="private"))
    with pytest.raises(SeedError, match="cannot be 'private'"):
        await _run(db_session, cfg)


async def test_missing_skill_md_loud_fails(db_session, cfg):
    _, _, skills = cfg
    (skills / "empty").mkdir()
    with pytest.raises(SeedError, match="missing SKILL.md"):
        await _run(db_session, cfg)


# --------------------------------------------------------------------------
# allowed-tools 校验(builtin 解析 / 未知 warn 但保留)
# --------------------------------------------------------------------------


async def test_allowed_tools_unknown_warns_but_kept(db_session, cfg, caplog):
    _, _, skills = cfg
    _write(skills / "s" / "SKILL.md", _skill_md(
        name="s", allowed_tools="  - read_artifact\n  - no_such_unit\n"
    ))
    with caplog.at_level(logging.WARNING):
        await _run(db_session, cfg)

    assert any("no_such_unit" in r.message for r in caplog.records)
    row = (await db_session.execute(select(Skill).where(Skill.slug == "s"))).scalar_one()
    # raw 条目原样保留(runtime 再解析),含解析不到的
    assert row.allowed_tools == ["read_artifact", "no_such_unit"]


# --------------------------------------------------------------------------
# snapshot 读侧 round-trip
# --------------------------------------------------------------------------


async def test_load_skill_snapshot_roundtrip(db_session, cfg):
    _, _, skills = cfg
    _write(skills / "a" / "SKILL.md", _skill_md(name="a", allowed_tools="  - read_artifact\n"))
    _write(skills / "b" / "SKILL.md", _skill_md(
        name="b", allowed_tools=None, visibility="department", default_enabled=False
    ))
    await _run(db_session, cfg)

    snap = await load_skill_snapshot(db_session)
    assert set(snap) == {"a", "b"}
    assert snap["a"].allowed_tools == ["read_artifact"]
    assert snap["a"].visibility == "public"
    assert snap["a"].default_enabled is True
    assert snap["b"].visibility == "department"
    assert snap["b"].default_enabled is False
    assert snap["b"].allowed_tools == []
    assert snap["a"].owner_user_id is None


# --------------------------------------------------------------------------
# unit 侧 dept 规则钩子在 C-1 已接通(建好空跑 + clear/prune 真删)
# --------------------------------------------------------------------------


async def test_unit_visibility_change_clears_dept_unit_rule(db_session, cfg):
    tools, _, _ = cfg
    _write(tools / "weather.md", _singleton_tool_md("weather"))
    await _run(db_session, cfg)

    db_session.add(Department(id="d1", name="dept1"))
    await db_session.flush()
    db_session.add(DepartmentUnitRule(department_id="d1", unit_name="weather"))
    await db_session.flush()

    # 改 unit visibility public → department → 清 department_unit_rule
    md = _singleton_tool_md("weather").replace(
        "method: GET\n", "method: GET\nvisibility: department\n"
    )
    _write(tools / "weather.md", md)
    await _run(db_session, cfg)

    assert (await db_session.execute(
        select(DepartmentUnitRule).where(DepartmentUnitRule.unit_name == "weather")
    )).first() is None


async def test_unit_prune_deletes_dept_unit_rule(db_session, cfg):
    tools, _, _ = cfg
    _write(tools / "weather.md", _singleton_tool_md("weather"))
    await _run(db_session, cfg)

    db_session.add(Department(id="d1", name="dept1"))
    await db_session.flush()
    db_session.add(DepartmentUnitRule(department_id="d1", unit_name="weather"))
    await db_session.flush()

    (tools / "weather.md").unlink()
    report = await _run(db_session, cfg)
    assert "tool_unit:weather" in report.pruned
    assert (await db_session.execute(
        select(DepartmentUnitRule).where(DepartmentUnitRule.unit_name == "weather")
    )).first() is None


# --------------------------------------------------------------------------
# bundle 物化(D-1:目录含附属文件 → 确定性 zip;单文件 → NULL)
# --------------------------------------------------------------------------


def _write_bundle_skill(skills, slug="pack", *, note="see script"):
    """写一个带 scripts/ + references/ 的多文件 skill,返回目录。"""
    _write(skills / slug / "SKILL.md", _skill_md(name=slug, allowed_tools=None,
                                                 body="Mount me."))
    _write(skills / slug / "scripts" / "run.py", "print('hi')\n")
    _write(skills / slug / "references" / "notes.md", f"# Notes\n\n{note}\n")
    return skills / slug


async def test_skill_bundle_built_for_multifile(db_session, cfg):
    _, _, skills = cfg
    _write_bundle_skill(skills, "pack")
    await _run(db_session, cfg)

    row = (await db_session.execute(select(Skill).where(Skill.slug == "pack"))).scalar_one()
    assert row.bundle is not None
    # 完整 zip 含 SKILL.md + 附属文件(决策 3)
    names = set(zipfile.ZipFile(io.BytesIO(row.bundle)).namelist())
    assert names == {"SKILL.md", "scripts/run.py", "references/notes.md"}

    snap = await load_skill_snapshot(db_session)
    assert snap["pack"].has_bundle is True


async def test_single_file_skill_has_no_bundle(db_session, cfg):
    _, _, skills = cfg
    _write(skills / "solo" / "SKILL.md", _skill_md(name="solo", allowed_tools=None))
    await _run(db_session, cfg)

    row = (await db_session.execute(select(Skill).where(Skill.slug == "solo"))).scalar_one()
    assert row.bundle is None
    snap = await load_skill_snapshot(db_session)
    assert snap["solo"].has_bundle is False


def test_bundle_zip_deterministic_ignores_mtime(cfg):
    """同源文件集 → 同 zip 字节,即使文件 mtime 变了(reconciler 幂等的前提)。"""
    _, _, skills = cfg
    d = _write_bundle_skill(skills, "det")
    kw = dict(known_unit_names=set(), known_full_names={})

    first = parse_skill_seeds(str(skills), **kw)[0].bundle
    # 改所有文件的 mtime(不改内容)
    for root, _dirs, files in os.walk(d):
        for f in files:
            os.utime(os.path.join(root, f), (1_000_000_000, 1_000_000_000))
    second = parse_skill_seeds(str(skills), **kw)[0].bundle

    assert first is not None
    assert first == second   # mtime 不泄进 zip 字节


async def test_bundle_hash_tracks_asset_change(db_session, cfg):
    """改附属文件(非 SKILL.md)→ seed_hash 变 → updated(不被 skip)。"""
    _, _, skills = cfg
    _write_bundle_skill(skills, "pack", note="v1")
    assert "skill:pack" in (await _run(db_session, cfg)).created
    # 重跑无改 → skipped
    assert "skill:pack" in (await _run(db_session, cfg)).skipped

    # 只改 references/(SKILL.md 不动)→ 必须 updated
    _write(skills / "pack" / "references" / "notes.md", "# Notes\n\nv2\n")
    report = await _run(db_session, cfg)
    assert "skill:pack" in report.updated
    row = (await db_session.execute(select(Skill).where(Skill.slug == "pack"))).scalar_one()
    zf = zipfile.ZipFile(io.BytesIO(row.bundle))
    assert b"v2" in zf.read("references/notes.md")


async def test_get_bundle_roundtrip(db_session, cfg):
    _, _, skills = cfg
    _write_bundle_skill(skills, "pack")
    _write(skills / "solo" / "SKILL.md", _skill_md(name="solo", allowed_tools=None))
    await _run(db_session, cfg)

    repo = SkillRepository(db_session)
    blob = await repo.get_bundle("pack")
    assert blob is not None
    assert "scripts/run.py" in zipfile.ZipFile(io.BytesIO(blob)).namelist()
    # 单文件 skill / 不存在的 slug → None
    assert await repo.get_bundle("solo") is None
    assert await repo.get_bundle("nope") is None
