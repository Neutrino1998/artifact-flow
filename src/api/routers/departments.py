"""Admin-only department-management HTTP endpoints."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from api.dependencies import get_department_manager, require_admin
from api.schemas.department import (
    CreateDepartmentRequest,
    DepartmentListResponse,
    DepartmentResponse,
    DepartmentTreeResponse,
    MoveDepartmentRequest,
    ResolveDepartmentRequest,
    ResolveDepartmentResponse,
    UpdateDepartmentRequest,
)
from api.services.auth import TokenPayload
from core.management.department_manager import (
    DepartmentConflictError,
    DepartmentCycleError,
    DepartmentInvalidParentError,
    DepartmentManagerError,
    DepartmentManager,
    DepartmentNotEmptyError,
    DepartmentNotFoundError,
)

router = APIRouter()


def _map_error(exc: Exception) -> HTTPException:
    if isinstance(exc, DepartmentNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, (DepartmentInvalidParentError, DepartmentCycleError)):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, DepartmentConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, DepartmentNotEmptyError):
        return HTTPException(
            status_code=409,
            detail={
                "message": str(exc),
                "user_count": exc.user_count,
                "child_count": exc.child_count,
            },
        )
    raise exc


@router.get("", response_model=DepartmentListResponse)
async def list_departments(
    parent_id: Optional[str] = Query(default=None),
    _admin: TokenPayload = Depends(require_admin),
    manager: DepartmentManager = Depends(get_department_manager),
):
    return DepartmentListResponse(departments=await manager.list_children(parent_id))


@router.get("/tree", response_model=DepartmentTreeResponse)
async def get_tree(
    _admin: TokenPayload = Depends(require_admin),
    manager: DepartmentManager = Depends(get_department_manager),
):
    return DepartmentTreeResponse(nodes=await manager.get_tree())


@router.get("/{dept_id}", response_model=DepartmentResponse)
async def get_department(
    dept_id: str,
    _admin: TokenPayload = Depends(require_admin),
    manager: DepartmentManager = Depends(get_department_manager),
):
    try:
        return DepartmentResponse(**await manager.get(dept_id))
    except DepartmentManagerError as exc:
        raise _map_error(exc) from exc


@router.post("", response_model=DepartmentResponse)
async def create_department(
    request: CreateDepartmentRequest,
    _admin: TokenPayload = Depends(require_admin),
    manager: DepartmentManager = Depends(get_department_manager),
):
    try:
        return DepartmentResponse(
            **await manager.create(name=request.name, parent_id=request.parent_id)
        )
    except DepartmentManagerError as exc:
        raise _map_error(exc) from exc


@router.patch("/{dept_id}", response_model=DepartmentResponse)
async def rename_department(
    dept_id: str,
    request: UpdateDepartmentRequest,
    _admin: TokenPayload = Depends(require_admin),
    manager: DepartmentManager = Depends(get_department_manager),
):
    try:
        return DepartmentResponse(**await manager.rename(dept_id, name=request.name))
    except DepartmentManagerError as exc:
        raise _map_error(exc) from exc


@router.post("/{dept_id}/move", response_model=DepartmentResponse)
async def move_department(
    dept_id: str,
    request: MoveDepartmentRequest,
    _admin: TokenPayload = Depends(require_admin),
    manager: DepartmentManager = Depends(get_department_manager),
):
    try:
        return DepartmentResponse(
            **await manager.move(dept_id, new_parent_id=request.new_parent_id)
        )
    except DepartmentManagerError as exc:
        raise _map_error(exc) from exc


@router.delete("/{dept_id}", status_code=204)
async def delete_department(
    dept_id: str,
    _admin: TokenPayload = Depends(require_admin),
    manager: DepartmentManager = Depends(get_department_manager),
):
    try:
        await manager.delete(dept_id)
    except DepartmentManagerError as exc:
        raise _map_error(exc) from exc


@router.post("/resolve", response_model=ResolveDepartmentResponse)
async def resolve_path(
    request: ResolveDepartmentRequest,
    _admin: TokenPayload = Depends(require_admin),
    manager: DepartmentManager = Depends(get_department_manager),
):
    return ResolveDepartmentResponse(id=await manager.resolve_path(request.path))
