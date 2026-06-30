"""read_skill 工具(L2,Phase C-2)。

镜像 read_artifact 的**工具契约**(句柄进 / 内容出、`max_result_size_chars=inf` 永不
二次 persist、`AUTO`),但可见性**不照抄 owner-only** —— skill 多 department 轴,走
EffectiveSkillSet(否则 dept skill 注入挡得住、read 挡不住,changelog 06-23)。

激活语义(决策 11/原则 8):read_skill 既返回正文,又声明式回填 `metadata.activated_skill`
—— 引擎据此把 slug 进 `active_skills` + 在已算好的 EffectiveToolset 上 merge 预烤
skill_grants(纯字典、本回合即生效)。工具保持哑、不持引擎态(对齐 ToolResult.artifact)。
"""

import math
from typing import List, Optional

from core.effective_skillset import EffectiveSkillSet
from tools.base import BaseTool, ToolParameter, ToolPermission, ToolResult
from tools.builtin.skill_service import SkillService

_MOUNT_HINT = (
    "\n\n---\n"
    "Above is this skill's guidance (SKILL.md). Any further files it references "
    "(references/, scripts/, assets/) are NOT shown here — mount the skill into the "
    "sandbox to read or run them."
)


class ReadSkillTool(BaseTool):
    def __init__(self, service: SkillService, skillset: EffectiveSkillSet):
        super().__init__(
            name="read_skill",
            description=(
                "Load a skill's full guidance (its SKILL.md body) by slug. Call this when a "
                "skill listed in <available_skills> fits the current task — it returns the "
                "instructions AND activates the skill for this conversation (any tools the "
                "skill needs become available). The returned guidance is for this conversation; "
                "if it later scrolls out of context, just read it again."
            ),
            permission=ToolPermission.AUTO,
            # inf = 永不落盘(同 read_artifact:自身输出再落盘会成 read→artifact→read 环)
            max_result_size_chars=math.inf,
        )
        self._service = service
        self._skillset = skillset

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="slug",
                type="string",
                description="Skill slug to load (as shown in <available_skills>).",
                required=True,
            )
        ]

    async def execute(self, **params) -> ToolResult:
        slug = (params.get("slug") or "").strip()
        if not slug:
            return ToolResult(success=False, error="read_skill requires a 'slug'.")
        # 可见性闸 = EffectiveSkillSet(含用户关掉但仍 visible 的 → 合法 opt-in)。
        # 不可见 → 404 风格,不泄露存在性(决策:cross-scope 不漏)。
        if slug not in self._skillset:
            return ToolResult(success=False, error=f"Skill '{slug}' not found.")
        body = await self._service.get_skill_md(slug)
        if body is None:
            return ToolResult(success=False, error=f"Skill '{slug}' has no content.")
        return ToolResult(
            success=True,
            data=body + _MOUNT_HINT,
            metadata={"activated_skill": slug},  # 引擎据此激活(append + merge skill_grants)
        )


def create_skill_tools(
    service: SkillService, skillset: Optional[EffectiveSkillSet]
) -> List[BaseTool]:
    """请求级 skill 工具工厂(同 create_artifact_tools)。skillset 缺省(无 skill)→ 空集。"""
    if skillset is None or not skillset.visible:
        return []
    return [ReadSkillTool(service, skillset)]
