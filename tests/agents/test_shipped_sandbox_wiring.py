"""
出厂 agent 配置 — 沙盒接线回归(C-wire)

锁沙盒首次 live 暴露的两件事,防未来重构静默改坏:
  1. 拥有沙盒的 agent(lead / research / explore)确实在 `tools` 白名单里授予 bash/mount/persist
     —— 白名单是引擎对模型的可见性闸,漏一个工具就调不动。
  2. **bash 权限必须是 confirm**:bash 跑不可信(模型生成)代码,auto 会绕过
     Permission Interrupt 直接执行 —— 这是安全回归,不是风格问题。决策 11 后等级
     唯一来源是工具定义(非 agent MD),故 #2 直接锁 `BashTool` 的 permission;agent
     MD 只声明成员(`bash: enabled`),#1 仍只查白名单 key。

不在沙盒白名单的 agent(compact)绝不能拿到这三个工具。
"""

from pathlib import Path

import pytest

from agents.loader import load_all_agents
from tools.base import ToolPermission
from tools.builtin.sandbox_ops import BashTool

SANDBOX_TOOLS = {"bash", "mount", "persist"}
AGENTS_WITH_SANDBOX = {"lead_agent", "research_agent", "explore_agent"}

_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config" / "agents"


@pytest.fixture(scope="module")
def shipped_agents():
    return load_all_agents(str(_CONFIG_DIR))


@pytest.mark.parametrize("agent_name", sorted(AGENTS_WITH_SANDBOX))
def test_sandbox_agents_grant_all_three_tools(shipped_agents, agent_name):
    tools = shipped_agents[agent_name].tools
    assert SANDBOX_TOOLS <= set(tools), (
        f"{agent_name} 缺沙盒工具 {SANDBOX_TOOLS - set(tools)} —— 白名单漏给则模型调不动"
    )


def test_bash_is_confirm_not_auto():
    # 安全闸:bash 跑不可信代码,必须经 CONFIRM。auto 会让生成代码无确认直接执行。
    # 决策 11:等级唯一来源 = 工具定义,故锁 BashTool 本身(对所有 agent 生效),
    # 不再逐 agent 查 MD 覆盖(绑定不存等级)。session 仅 execute 期用,构造时传 None
    # 即可读 permission。
    assert BashTool(None).permission == ToolPermission.CONFIRM


def test_compact_agent_has_no_sandbox(shipped_agents):
    # compact 是 internal、无工具;沙盒绝不能渗到非授权 agent。
    assert SANDBOX_TOOLS.isdisjoint(set(shipped_agents["compact_agent"].tools))
