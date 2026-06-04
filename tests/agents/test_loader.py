"""
Agent loader — loud-fail 契约回归

锁两条:agent MD 缺 model 字段 → load_agent 抛错;load_all_agents 遇任一坏
文件 → 聚合后启动期 raise(不静默丢弃)。两者都是"配置与体验不一致"的 silent
fallback 防线,曾因 load_all_agents catch-and-continue 被悄悄绕过。
"""

import pytest

from agents.loader import load_agent, load_all_agents


def _write(dir_path, name, body):
    p = dir_path / name
    p.write_text(body, encoding="utf-8")
    return str(p)


VALID = """---
name: good_agent
description: ok
model: gpt-4o-mini
---
role prompt
"""

NO_MODEL = """---
name: bad_agent
description: missing model
---
role prompt
"""


def test_load_agent_missing_model_raises(tmp_path):
    path = _write(tmp_path, "bad.md", NO_MODEL)
    with pytest.raises(ValueError, match="missing required 'model'"):
        load_agent(path)


def test_load_all_agents_ok(tmp_path):
    _write(tmp_path, "good.md", VALID)
    agents = load_all_agents(str(tmp_path))
    assert set(agents) == {"good_agent"}
    assert agents["good_agent"].model == "gpt-4o-mini"


def test_load_all_agents_raises_on_bad_file(tmp_path):
    """坏 agent 必须 loud-fail,不能被静默丢弃后只剩好 agent。"""
    _write(tmp_path, "good.md", VALID)
    _write(tmp_path, "bad.md", NO_MODEL)
    with pytest.raises(ValueError, match="Failed to load agent config"):
        load_all_agents(str(tmp_path))


def test_load_all_agents_aggregates_all_errors(tmp_path):
    """一次报全部坏文件,而非逐个修。"""
    _write(tmp_path, "bad1.md", NO_MODEL)
    _write(tmp_path, "bad2.md", NO_MODEL.replace("bad_agent", "bad_agent2"))
    with pytest.raises(ValueError) as exc:
        load_all_agents(str(tmp_path))
    msg = str(exc.value)
    assert "bad1.md" in msg and "bad2.md" in msg
