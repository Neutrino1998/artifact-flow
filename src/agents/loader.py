"""
Agent 加载器 — 从 MD 文件解析 AgentConfig

每个 agent 是一个 MD 文件：
- YAML frontmatter: name, description, tools, model, max_tool_rounds
- MD body: 角色提示词（role_prompt）
"""

import os
import yaml
from dataclasses import dataclass, field
from typing import Optional

from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


@dataclass
class AgentConfig:
    """Agent 配置（从 MD 文件加载）"""
    name: str
    description: str
    model: str  # 必填,无默认 — 缺失即 loud-fail(见 load_agent),不静默兜底到某个别名
    tools: dict[str, str] = field(default_factory=dict)  # {tool_name: permission_level}
    max_tool_rounds: int = 3
    internal: bool = False
    role_prompt: str = ""  # MD body（纯文本）


def load_agent(md_path: str) -> AgentConfig:
    """
    从 MD 文件加载 AgentConfig

    MD 文件格式：
    ---
    name: agent_name
    description: Agent description
    tools:
      web_search: auto
      web_fetch: confirm
    model: qwen3.7-plus
    max_tool_rounds: 100
    ---

    (role prompt body here)

    Args:
        md_path: MD 文件路径

    Returns:
        AgentConfig 实例
    """
    with open(md_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 解析 YAML frontmatter
    if not content.startswith("---"):
        raise ValueError(f"MD file must start with YAML frontmatter: {md_path}")

    # 找到第二个 ---
    end_idx = content.index("---", 3)
    frontmatter_str = content[3:end_idx].strip()
    body = content[end_idx + 3:].strip()

    frontmatter = yaml.safe_load(frontmatter_str)

    # model 必填:静默兜底到某个默认别名会让 agent 在用户没察觉时跑错模型
    # (配置与体验不一致)。缺失即 loud-fail,让 operator 在加载期就发现。
    if not frontmatter.get("model"):
        raise ValueError(f"Agent MD missing required 'model' field: {md_path}")

    return AgentConfig(
        name=frontmatter["name"],
        description=frontmatter.get("description", ""),
        model=frontmatter["model"],
        tools=frontmatter.get("tools", {}),
        max_tool_rounds=frontmatter.get("max_tool_rounds", 3),
        internal=frontmatter.get("internal", False),
        role_prompt=body,
    )


def load_all_agents(agents_dir: Optional[str] = None) -> dict[str, AgentConfig]:
    """
    加载目录下所有 .md 文件

    Args:
        agents_dir: agent MD 文件目录，默认为本模块所在目录

    Returns:
        {agent_name: AgentConfig} 字典
    """
    if agents_dir is None:
        # 默认从项目根目录 config/agents/ 加载
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        agents_dir = os.path.join(project_root, "config", "agents")

    agents = {}
    errors = []
    for filename in sorted(os.listdir(agents_dir)):
        # 隐藏文件(`.` 前缀)永远不是配置:macOS 传输垃圾(AppleDouble `._x.md`、
        # .DS_Store)/编辑器临时文件混进目录时不应阻断启动(2026-06-12 内网部署
        # `._lead_agent.md` 二进制解码失败拒启)。真实坏配置(非隐藏)仍 loud-fail。
        if not filename.endswith(".md") or filename.startswith("."):
            continue

        md_path = os.path.join(agents_dir, filename)
        try:
            config = load_agent(md_path)
            agents[config.name] = config
            logger.info(f"Loaded agent: {config.name} from {filename}")
        except Exception as e:
            # 静默丢弃坏 agent 也是一种 silent fallback:operator 把文件放进
            # config/agents/ 就期望它加载,丢失要到 /meta 或执行路径才暴露(若丢的是
            # lead_agent 更难定位)。聚合全部错误后启动期 loud-fail —— 一次看全所有坏
            # 文件,而非逐个修。与 JWT_SECRET/DATABASE_URL 缺失即停一致。
            logger.error(f"Failed to load agent from {filename}: {e}")
            errors.append(f"{filename}: {e}")

    if errors:
        raise ValueError(
            "Failed to load agent config(s) — fix before startup:\n  "
            + "\n  ".join(errors)
        )

    return agents
