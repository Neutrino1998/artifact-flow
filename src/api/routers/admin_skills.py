"""Admin Skills Router —— 共享 skill 管理(admin-only)。挂 /api/v1/admin,故路径:
- POST   /api/v1/admin/skills/import   导入为共享 skill(marketplace:public、owner=null、
                                       默认不进 L1,用户自选;配额豁免,结构上限照查)
- DELETE /api/v1/admin/skills/{slug}   删任意 dynamic skill(seeded → 400,config 所有)

router 只做 transport:认证(require_admin)、解析、SkillManagerError → HTTP 映射。
业务规则(硬门/撞名/seeded 闸)全在 SkillManager —— 与用户通道同一条 import_zip
管线,零漂移。用户侧列举/导出无 admin 变体(admin 本人走用户端点)。
"""

from fastapi import APIRouter, Depends, File, UploadFile

from api.dependencies import get_skill_manager, require_admin
from api.routers.skills import _map
from api.schemas.skills import SkillImportResponse
from api.services.auth import TokenPayload
from core.skill_manager import SkillManager, SkillManagerError

router = APIRouter()


@router.post("/skills/import", response_model=SkillImportResponse)
async def admin_import_skill(
    file: UploadFile = File(...),
    admin: TokenPayload = Depends(require_admin),
    mgr: SkillManager = Depends(get_skill_manager),
) -> SkillImportResponse:
    """导入共享 skill(visibility=public、default_enabled=False、owner=null)。"""
    blob = await file.read()
    try:
        result = await mgr.import_zip(
            admin.user_id, blob, file.filename or "", audience="marketplace"
        )
    except SkillManagerError as e:
        raise _map(e)
    return SkillImportResponse(**result)


@router.delete("/skills/{slug}", status_code=204)
async def admin_delete_skill(
    slug: str,
    admin: TokenPayload = Depends(require_admin),
    mgr: SkillManager = Depends(get_skill_manager),
) -> None:
    """删除任意 dynamic skill(绕过可见性;seeded → 400)。级联清 user_skill/dept 规则。"""
    try:
        await mgr.delete_skill(admin.user_id, slug, as_admin=True)
    except SkillManagerError as e:
        raise _map(e)
