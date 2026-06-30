"""EffectiveSkillSet —— skill 侧单点可见性 resolver(对应工具的 EffectiveToolset,
并列非合并,决策 1/10 + changelog 06-23)。

两轴:
  - **visible**(正确性,决定能否 read/mount;miss → 404):
      private    → owner 匹配
      public     → 默认可见,dept 规则例外 = deny(列出部门反而不可见)
      department → 默认不可见,dept 规则例外 = grant(列出部门才可见)
    方向派生自 visibility(无 effect 列);dept 命中 = 用户祖先链 ∩ department_skill_rule。
  - **enabled**(UX,只决定上不上 L1):visible ∩ (default_enabled + user_skill 覆盖)。
    用户关掉的 skill 仍 visible(可 read_skill / `/skill` 显式 opt-in),只是不进 L1。

**全 agent 可见、无 agent 维度**(效果按各 agent 宇宙 + 部门双重收窄,在 EffectiveToolset
做,决策 11)。与 EffectiveToolset 只共用部门祖先链 helper(department_resolver),不共其余。
"""

from dataclasses import dataclass, field
from typing import Dict, List, Set

from reconcile.snapshot import SkillInfo


@dataclass
class EffectiveSkillSet:
    """某用户解析后的 skill 可见/启用集。"""
    visible: Dict[str, SkillInfo] = field(default_factory=dict)  # slug -> info(read/mount 允许)
    enabled: Set[str] = field(default_factory=set)               # slug 子集(进 L1)

    def __contains__(self, slug: str) -> bool:
        return slug in self.visible

    def available_for_l1(self) -> List[SkillInfo]:
        """L1 注入用:visible 中按 enabled 过滤(保 snapshot 的 slug 顺序,prompt 稳定)。"""
        return [info for slug, info in self.visible.items() if slug in self.enabled]


def resolve_effective_skillset(
    user_id: str,
    skill_snapshot: Dict[str, SkillInfo],
    user_overrides: Dict[str, bool],
    dept_matched: Set[str],
) -> EffectiveSkillSet:
    """从 user-agnostic 快照 + 该用户的覆盖/部门命中,解析出 EffectiveSkillSet。

    dept_matched = 用户祖先链中任一部门对其有 department_skill_rule 的 slug 集
    (由 SkillRepository.dept_matched_slugs + department_resolver 在调用方算好)。
    """
    visible: Dict[str, SkillInfo] = {}
    enabled: Set[str] = set()

    for slug, info in skill_snapshot.items():
        vis = info.visibility
        if vis == "private":
            if info.owner_user_id != user_id:
                continue
        elif vis == "public":
            if slug in dept_matched:        # public 默认 allow,列出 = deny 例外
                continue
        elif vis == "department":
            if slug not in dept_matched:     # department 默认 deny,列出 = grant 例外
                continue
        else:
            continue  # 未知 visibility:防御性跳过(不泄露)

        visible[slug] = info
        # enabled:用户显式覆盖优先,否则 default_enabled
        if user_overrides.get(slug, info.default_enabled):
            enabled.add(slug)

    return EffectiveSkillSet(visible=visible, enabled=enabled)
