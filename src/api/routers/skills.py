"""用户侧 skill 管理 REST(C-3):列可见 skill + 个人 enable/disable。

作用域 = 用户自己的 skill 偏好(非 admin;守 feedback-admin-scope-user-mgmt)。可见性走
SkillManager 的 EffectiveSkillSet 单点闸,不可见 slug → 404(不泄露存在性)。动态 skill
CRUD + dept 授权 UI 留后续阶段(E/G)。
"""

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import get_current_user, get_skill_manager
from api.schemas.skills import SkillItem, SkillListResponse, SkillToggleRequest
from core.skill_manager import SkillManager, SkillManagerError

router = APIRouter()


def _map(e: SkillManagerError) -> HTTPException:
    return HTTPException(status_code=getattr(e, "status_code", 400), detail=str(e))


@router.get("", response_model=SkillListResponse)
async def list_skills(
    user=Depends(get_current_user),
    mgr: SkillManager = Depends(get_skill_manager),
) -> SkillListResponse:
    """列出对当前用户可见的 skill + 有效启用态。"""
    items = await mgr.list_for_user(user.user_id)
    return SkillListResponse(skills=[SkillItem(**it) for it in items])


@router.put("/{slug}/enabled", response_model=SkillItem)
async def set_skill_enabled(
    slug: str,
    body: SkillToggleRequest,
    user=Depends(get_current_user),
    mgr: SkillManager = Depends(get_skill_manager),
) -> SkillItem:
    """个人开关某 skill 是否进 L1 索引(写 user_skill 覆盖)。"""
    try:
        item = await mgr.set_enabled(user.user_id, slug, body.enabled)
    except SkillManagerError as e:
        raise _map(e)
    return SkillItem(**item)
