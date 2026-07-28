"""用户侧 skill 管理 REST(C-3 列举/toggle + E-2 导入/导出/删除)。

作用域 = 用户自己的 skill(个人偏好 + 私有导入;守 feedback-admin-scope-user-mgmt)。
可见性走 SkillManager 的 EffectiveSkillSet 单点闸,不可见 skill → 404(不泄露存在性)。
admin 共享导入/删除在 routers/admin_skills.py。dept 授权 UI 留 G。
"""

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response

from api.dependencies import get_current_user, get_skill_manager
from api.schemas.skills import (
    SkillImportResponse,
    SkillItem,
    SkillListResponse,
    SkillToggleRequest,
)
from core.skill_manager import SkillManager, SkillManagerError, SkillValidationError

router = APIRouter()


def _map(e: SkillManagerError) -> HTTPException:
    if isinstance(e, SkillValidationError):
        # 硬门拒收:findings 结构化透出,前端逐条渲染(rule + severity badge + message)
        return HTTPException(
            status_code=e.status_code,
            detail={
                "message": str(e),
                "findings": [
                    {"rule": f.rule, "severity": f.severity, "message": f.message}
                    for f in e.findings
                ],
            },
        )
    return HTTPException(status_code=getattr(e, "status_code", 400), detail=str(e))


@router.get("", response_model=SkillListResponse)
async def list_skills(
    user=Depends(get_current_user),
    mgr: SkillManager = Depends(get_skill_manager),
) -> SkillListResponse:
    """列出对当前用户可见的 skill + 有效启用态。"""
    items = await mgr.list_for_user(user.user_id)
    return SkillListResponse(skills=[SkillItem(**it) for it in items])


@router.post("/import", response_model=SkillImportResponse)
async def import_skill(
    file: UploadFile = File(...),
    user=Depends(get_current_user),
    mgr: SkillManager = Depends(get_skill_manager),
) -> SkillImportResponse:
    """导入私有 skill zip(owner=本人,立即进自己的 L1)。硬门拒收 → 422 结构化
    findings;超单包上限 → 422;超存储配额 → 413;个人数量达上限或 slug 撞名 → 409。"""
    blob = await file.read()
    try:
        result = await mgr.import_zip(
            user.user_id, blob, file.filename or "", audience="private"
        )
    except SkillManagerError as e:
        raise _map(e)
    return SkillImportResponse(**result)


@router.get("/{skill_id}/export")
async def export_skill(
    skill_id: str,
    user=Depends(get_current_user),
    mgr: SkillManager = Depends(get_skill_manager),
) -> Response:
    """导出 DB 中保存的 skill zip。不可见 → 404。"""
    try:
        slug, blob = await mgr.export_bundle(user.user_id, skill_id)
    except SkillManagerError as e:
        raise _map(e)
    return Response(
        content=blob,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{slug}.zip"'},
    )


@router.delete("/{skill_id}", status_code=204)
async def delete_skill(
    skill_id: str,
    user=Depends(get_current_user),
    mgr: SkillManager = Depends(get_skill_manager),
) -> None:
    """删除自己导入的 dynamic skill。不可见 → 404;seeded → 400;非本人共享 → 403。"""
    try:
        await mgr.delete_skill(user.user_id, skill_id)
    except SkillManagerError as e:
        raise _map(e)


@router.put("/{skill_id}/enabled", response_model=SkillItem)
async def set_skill_enabled(
    skill_id: str,
    body: SkillToggleRequest,
    user=Depends(get_current_user),
    mgr: SkillManager = Depends(get_skill_manager),
) -> SkillItem:
    """个人开关某 skill 是否进 L1 索引(写 user_skill 覆盖)。"""
    try:
        item = await mgr.set_enabled(user.user_id, skill_id, body.enabled)
    except SkillManagerError as e:
        raise _map(e)
    return SkillItem(**item)
