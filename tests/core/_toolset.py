"""测试桥:从 legacy fake agent 配置构造 {agent_name: EffectiveToolset}。

生产侧 EffectiveToolset 由 DB 快照解析(core.effective_toolset.resolve_all)。引擎/
上下文构建单测仍用轻量 fake agent 配置(`.tools` = {name: level} dict);本桥按生产
契约镜像出 EffectiveToolset —— 成员来自 agent 声明的工具,等级在工具对象存在时取自
工具对象(决策 11:等级唯一来源是工具定义),否则回退 legacy 字面量(无工具对象的
纯成员判定用例)。
"""

from core.effective_toolset import EffectiveToolset
from tools.base import ToolPermission


def _perms_for(cfg, tools):
    perms = {}
    for tname, level in getattr(cfg, "tools", {}).items():
        t = tools.get(tname)
        if t is not None:
            perms[tname] = t.permission
        elif isinstance(level, ToolPermission):
            perms[tname] = level
        else:
            try:
                perms[tname] = ToolPermission(level)
            except (ValueError, TypeError):
                perms[tname] = ToolPermission.AUTO
    return perms


def effective_for(agents, tools=None):
    """{agent_name: EffectiveToolset} —— 供 execute_loop 的 effective_toolsets 参数。"""
    tools = tools or {}
    return {name: EffectiveToolset(_perms_for(cfg, tools)) for name, cfg in agents.items()}


def effective_one(agent_config, tools=None):
    """单个 EffectiveToolset —— 供 ContextManager.build 的 effective_toolset 参数。"""
    return EffectiveToolset(_perms_for(agent_config, tools or {}))
