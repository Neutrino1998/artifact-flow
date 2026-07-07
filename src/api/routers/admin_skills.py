"""Admin Skills Router —— 共享 skill 管理(admin-only)。挂 /api/v1/admin,故路径:
- GET    /api/v1/admin/skills          列出共享 skill(含 seeded read-only)
- POST   /api/v1/admin/skills/import   导入为共享 skill(owner=null;可指定 public/department
                                       与默认启用;配额豁免,结构上限照查)
- GET    /api/v1/admin/skills/{slug}/export
                                       导出 shared catalog skill(绕过 admin 自身部门可见性)
- PATCH  /api/v1/admin/skills/{slug}   编辑 dynamic shared skill 的 visibility/default_enabled
- DELETE /api/v1/admin/skills/{slug}   删任意 dynamic skill(seeded → 400,config 所有)

router 只做 transport:认证(require_admin)、解析、SkillManagerError → HTTP 映射。
业务规则(硬门/撞名/seeded 闸)全在 SkillManager —— 与用户通道同一条 import_zip
管线,零漂移。admin export 只覆盖 shared catalog,不导出用户私有 skill。
"""

from typing import Literal

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import Response

from api.dependencies import get_skill_manager, require_admin
from api.routers.skills import _map
from api.schemas.skills import (
    AdminSkillItem,
    AdminSkillListResponse,
    AdminSkillUpdateRequest,
    SkillImportResponse,
)
from api.services.auth import TokenPayload
from core.skill_manager import SkillManager, SkillManagerError

router = APIRouter()


@router.get("/skills", response_model=AdminSkillListResponse)
async def admin_list_skills(
    _admin: TokenPayload = Depends(require_admin),
    mgr: SkillManager = Depends(get_skill_manager),
) -> AdminSkillListResponse:
    """列出所有 shared skill,不按 admin 自己的部门可见性过滤。"""
    items = await mgr.list_admin_shared()
    return AdminSkillListResponse(skills=[AdminSkillItem(**it) for it in items])


@router.post("/skills/import", response_model=SkillImportResponse)
async def admin_import_skill(
    file: UploadFile = File(...),
    visibility: Literal["public", "department"] = Form("public"),
    default_enabled: bool = Form(True),
    admin: TokenPayload = Depends(require_admin),
    mgr: SkillManager = Depends(get_skill_manager),
) -> SkillImportResponse:
    """导入共享 skill(owner=null),可指定 visibility/default_enabled 初始值。"""
    blob = await file.read()
    try:
        result = await mgr.import_zip(
            admin.user_id,
            blob,
            file.filename or "",
            audience="marketplace",
            visibility=visibility,
            default_enabled=default_enabled,
        )
    except SkillManagerError as e:
        raise _map(e)
    return SkillImportResponse(**result)


@router.get("/skills/{slug}/export")
async def admin_export_skill(
    slug: str,
    _admin: TokenPayload = Depends(require_admin),
    mgr: SkillManager = Depends(get_skill_manager),
) -> Response:
    """导出 shared catalog skill,不按当前 admin 的部门可见性过滤。"""
    try:
        blob = await mgr.export_admin_shared_bundle(slug)
    except SkillManagerError as e:
        raise _map(e)
    return Response(
        content=blob,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{slug}.zip"'},
    )


@router.patch("/skills/{slug}", response_model=AdminSkillItem)
async def admin_update_skill(
    slug: str,
    body: AdminSkillUpdateRequest,
    admin: TokenPayload = Depends(require_admin),
    mgr: SkillManager = Depends(get_skill_manager),
) -> AdminSkillItem:
    """编辑 dynamic shared skill 的 visibility/default_enabled。seeded 仍 config-owned。"""
    try:
        item = await mgr.update_admin_shared(
            admin.user_id,
            slug,
            visibility=body.visibility,
            default_enabled=body.default_enabled,
        )
    except SkillManagerError as e:
        raise _map(e)
    return AdminSkillItem(**item)


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
