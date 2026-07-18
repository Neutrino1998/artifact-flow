"""
出厂 agent 配置 — 沙盒接线回归(C-wire)

锁沙盒首次 live 暴露的两件事,防未来重构静默改坏:
  1. 拥有沙盒的 agent(lead / research / explore)确实在 `tools` 白名单里授予 bash/mount/persist
     —— 白名单是引擎对模型的可见性闸,漏一个工具就调不动。
  2. **三个沙盒工具默认都是 auto**:沙盒的安全边界是 containment（gVisor /
     固定容器参数 / 禁网 / per-turn 销毁），不是 Permission Interrupt。决策 11 后
     等级唯一来源是工具定义(非 agent MD),故 #2 直接锁工具的 permission;
     agent MD 只声明成员(`bash: enabled`),#1 仍只查白名单 key。

不在沙盒白名单的 agent(compact)绝不能拿到这三个工具。
"""

import re
from pathlib import Path

import pytest

from agents.loader import load_all_agents
from tools.base import ToolPermission
from tools.builtin.sandbox_ops import BashTool, create_sandbox_tools

SANDBOX_TOOLS = {"bash", "mount", "persist"}
AGENTS_WITH_SANDBOX = {"lead_agent", "research_agent", "explore_agent"}

_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config" / "agents"
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _sandbox_apt_packages() -> set[str]:
    dockerfile = (_REPO_ROOT / "sandbox" / "Dockerfile").read_text()
    match = re.search(
        r"apt-get install -y --no-install-recommends \\\n(?P<body>.*?)\n\s*&& rm -rf",
        dockerfile,
        flags=re.S,
    )
    assert match, "sandbox/Dockerfile apt install block shape changed; update this guard"
    return set(re.findall(r"^\s+([a-z0-9.+-]+)\s*\\?$", match.group("body"), flags=re.M))


def _sandbox_python_packages() -> set[str]:
    packages: set[str] = set()
    for raw in (_REPO_ROOT / "sandbox" / "requirements.txt").read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        packages.add(re.split(r"[<>=!~]", line, maxsplit=1)[0])
    return packages


def _dependency_token_present(text: str, dependency: str) -> bool:
    # Dependencies are exact package/tool tokens, not arbitrary substrings:
    # `pypdf` must not be satisfied by `pypdfium2`, and `zip` must not be
    # satisfied by prose like "skill zip".
    token_chars = r"A-Za-z0-9_.+-"
    return re.search(
        rf"(?<![{token_chars}]){re.escape(dependency)}(?![{token_chars}])",
        text,
    ) is not None


def _paragraph_after(text: str, marker: str) -> str:
    start = text.find(marker)
    assert start != -1, f"missing marker {marker!r}"
    rest = text[start + len(marker):].lstrip()
    return rest.split("\n\n", 1)[0]


def _assert_dependency_tokens(label: str, text: str, dependencies: set[str]) -> None:
    for dependency in sorted(dependencies):
        assert _dependency_token_present(text, dependency), (
            f"{label} missing sandbox dependency '{dependency}'"
        )


@pytest.fixture(scope="module")
def shipped_agents():
    return load_all_agents(str(_CONFIG_DIR))


@pytest.mark.parametrize("agent_name", sorted(AGENTS_WITH_SANDBOX))
def test_sandbox_agents_grant_all_three_tools(shipped_agents, agent_name):
    tools = shipped_agents[agent_name].tools
    assert SANDBOX_TOOLS <= set(tools), (
        f"{agent_name} 缺沙盒工具 {SANDBOX_TOOLS - set(tools)} —— 白名单漏给则模型调不动"
    )


def test_sandbox_tools_are_auto():
    # 决策 11:等级唯一来源 = 工具定义,故直接锁三个工具的出厂权限
    # (对所有 agent 生效)，不再逐 agent 查 MD 覆盖(绑定不存等级)。
    tools = create_sandbox_tools(None, None)
    assert {tool.name: tool.permission for tool in tools} == {
        "bash": ToolPermission.AUTO,
        "mount": ToolPermission.AUTO,
        "persist": ToolPermission.AUTO,
    }


def test_compact_agent_has_no_sandbox(shipped_agents):
    # compact 是 internal、无工具;沙盒绝不能渗到非授权 agent。
    assert SANDBOX_TOOLS.isdisjoint(set(shipped_agents["compact_agent"].tools))


def test_sandbox_dependency_descriptions_track_image_inputs():
    system_tools = _sandbox_apt_packages()
    python_packages = _sandbox_python_packages()
    assert {"pandoc", "ripgrep", "zip", "git"} <= system_tools
    all_dependencies = system_tools | python_packages

    exact_dependency_docs = {
        "BashTool.description": BashTool(None).description,
        "docs/architecture/sandbox.md": (
            _REPO_ROOT / "docs" / "architecture" / "sandbox.md"
        ).read_text(),
    }
    for label, text in exact_dependency_docs.items():
        _assert_dependency_tokens(label, text, all_dependencies)

    environment = (
        _REPO_ROOT
        / "config"
        / "skills-src"
        / "skill-creator"
        / "references"
        / "environment.md"
    ).read_text()
    apt_paragraph = _paragraph_after(environment, "**系统工具(apt)**:")
    pip_paragraph = _paragraph_after(environment, "**Python 包(pip)**:")
    _assert_dependency_tokens("skill-creator environment.md apt list", apt_paragraph, system_tools)
    _assert_dependency_tokens("skill-creator environment.md pip list", pip_paragraph, python_packages)

    readme = (_REPO_ROOT / "sandbox" / "README.md").read_text()
    _assert_dependency_tokens("sandbox/README.md", readme, system_tools)
