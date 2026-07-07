"""Admin Department Access Router —— department-scoped skill/unit rules.

Mounted at /api/v1/admin:
- GET    /department-access/{dept_id}
- PUT    /department-access/{dept_id}/skills/{slug}
- DELETE /department-access/{dept_id}/skills/{slug}
- PUT    /department-access/{dept_id}/units/{unit_name}
- DELETE /department-access/{dept_id}/units/{unit_name}
"""

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import get_department_access_manager, require_admin
from api.schemas.department_access import DepartmentAccessResponse
from api.services.auth import TokenPayload
from core.department_access_manager import (
    DepartmentAccessError,
    DepartmentAccessManager,
)

router = APIRouter()


def _map(e: DepartmentAccessError) -> HTTPException:
    return HTTPException(status_code=e.status_code, detail=str(e))


@router.get(
    "/department-access/{dept_id}", response_model=DepartmentAccessResponse
)
async def get_department_access(
    dept_id: str,
    _admin: TokenPayload = Depends(require_admin),
    mgr: DepartmentAccessManager = Depends(get_department_access_manager),
):
    try:
        return await mgr.get_department_access(dept_id)
    except DepartmentAccessError as e:
        raise _map(e)


@router.put("/department-access/{dept_id}/skills/{slug}", status_code=204)
async def put_department_skill_rule(
    dept_id: str,
    slug: str,
    _admin: TokenPayload = Depends(require_admin),
    mgr: DepartmentAccessManager = Depends(get_department_access_manager),
) -> None:
    try:
        await mgr.put_skill_rule(dept_id, slug)
    except DepartmentAccessError as e:
        raise _map(e)


@router.delete("/department-access/{dept_id}/skills/{slug}", status_code=204)
async def delete_department_skill_rule(
    dept_id: str,
    slug: str,
    _admin: TokenPayload = Depends(require_admin),
    mgr: DepartmentAccessManager = Depends(get_department_access_manager),
) -> None:
    try:
        await mgr.delete_skill_rule(dept_id, slug)
    except DepartmentAccessError as e:
        raise _map(e)


@router.put("/department-access/{dept_id}/units/{unit_name}", status_code=204)
async def put_department_unit_rule(
    dept_id: str,
    unit_name: str,
    _admin: TokenPayload = Depends(require_admin),
    mgr: DepartmentAccessManager = Depends(get_department_access_manager),
) -> None:
    try:
        await mgr.put_unit_rule(dept_id, unit_name)
    except DepartmentAccessError as e:
        raise _map(e)


@router.delete("/department-access/{dept_id}/units/{unit_name}", status_code=204)
async def delete_department_unit_rule(
    dept_id: str,
    unit_name: str,
    _admin: TokenPayload = Depends(require_admin),
    mgr: DepartmentAccessManager = Depends(get_department_access_manager),
) -> None:
    try:
        await mgr.delete_unit_rule(dept_id, unit_name)
    except DepartmentAccessError as e:
        raise _map(e)
