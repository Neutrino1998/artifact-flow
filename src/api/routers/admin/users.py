"""Admin-only user-management routes."""

from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile

from api.dependencies import get_admin_user_manager, require_admin
from api.schemas.auth import (
    MAX_BULK_USER_ACTION_IDS,
    BulkActionRequest,
    BulkActionResponse,
    BulkImpactResponse,
    BulkImportResponse,
    CreateUserRequest,
    UpdateUserRequest,
    UserImpactResponse,
    UserListResponse,
    UserResponse,
)
from api.services.auth import TokenPayload
from core.management.admin_user_manager import (
    AdminUserConflictError,
    AdminUserError,
    AdminUserForbiddenError,
    AdminUserInvalidError,
    AdminUserManager,
    AdminUserNotFoundError,
    AdminUserPayloadTooLargeError,
)

router = APIRouter()


def _map_error(exc: AdminUserError) -> HTTPException:
    if isinstance(exc, AdminUserNotFoundError):
        return HTTPException(status_code=404, detail=exc.detail)
    if isinstance(exc, AdminUserConflictError):
        return HTTPException(status_code=409, detail=exc.detail)
    if isinstance(exc, AdminUserForbiddenError):
        return HTTPException(status_code=403, detail=exc.detail)
    if isinstance(exc, AdminUserPayloadTooLargeError):
        return HTTPException(status_code=422, detail=exc.detail)
    if isinstance(exc, AdminUserInvalidError):
        return HTTPException(status_code=400, detail=exc.detail)
    return HTTPException(status_code=400, detail=exc.detail)


@router.post("/users", response_model=UserResponse)
async def create_user(
    request: CreateUserRequest,
    _admin: TokenPayload = Depends(require_admin),
    manager: AdminUserManager = Depends(get_admin_user_manager),
):
    try:
        return UserResponse(
            **await manager.create(
                username=request.username,
                password=request.password,
                display_name=request.display_name,
                role=request.role,
                department_id=request.department_id,
            )
        )
    except AdminUserError as exc:
        raise _map_error(exc) from exc


@router.get("/users", response_model=UserListResponse)
async def list_users(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    q: Optional[str] = Query(default=None, max_length=200),
    _admin: TokenPayload = Depends(require_admin),
    manager: AdminUserManager = Depends(get_admin_user_manager),
):
    users, total = await manager.list(limit=limit, offset=offset, query=q)
    return UserListResponse(users=users, total=total)


# Static bulk routes must be registered before /users/{user_id}.
@router.get("/users/bulk-impact", response_model=BulkImpactResponse)
async def get_users_bulk_impact(
    ids: list[str] = Query(
        ..., min_length=1, max_length=MAX_BULK_USER_ACTION_IDS
    ),
    _admin: TokenPayload = Depends(require_admin),
    manager: AdminUserManager = Depends(get_admin_user_manager),
):
    user_count, conversation_count = await manager.bulk_impact(ids)
    return BulkImpactResponse(
        user_count=user_count, conversation_count=conversation_count
    )


@router.post("/users/bulk-action", response_model=BulkActionResponse)
async def bulk_user_action(
    request: BulkActionRequest,
    current_user: TokenPayload = Depends(require_admin),
    manager: AdminUserManager = Depends(get_admin_user_manager),
):
    try:
        result = await manager.bulk_action(
            user_ids=request.ids,
            action=request.action,
            payload=request.payload,
            actor_user_id=current_user.user_id,
        )
        return BulkActionResponse(**result)
    except AdminUserError as exc:
        raise _map_error(exc) from exc


@router.post("/users/bulk-import", response_model=BulkImportResponse)
async def bulk_import_users(
    file: UploadFile = File(...),
    _admin: TokenPayload = Depends(require_admin),
    manager: AdminUserManager = Depends(get_admin_user_manager),
):
    try:
        return BulkImportResponse(**await manager.bulk_import(await file.read()))
    except AdminUserError as exc:
        raise _map_error(exc) from exc


@router.get("/users/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: str,
    _admin: TokenPayload = Depends(require_admin),
    manager: AdminUserManager = Depends(get_admin_user_manager),
):
    try:
        return UserResponse(**await manager.get(user_id))
    except AdminUserError as exc:
        raise _map_error(exc) from exc


@router.get("/users/{user_id}/impact", response_model=UserImpactResponse)
async def get_user_impact(
    user_id: str,
    _admin: TokenPayload = Depends(require_admin),
    manager: AdminUserManager = Depends(get_admin_user_manager),
):
    try:
        return UserImpactResponse(conversation_count=await manager.impact(user_id))
    except AdminUserError as exc:
        raise _map_error(exc) from exc


@router.put("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: str,
    request: UpdateUserRequest,
    current_user: TokenPayload = Depends(require_admin),
    manager: AdminUserManager = Depends(get_admin_user_manager),
):
    try:
        return UserResponse(
            **await manager.update(
                user_id,
                actor_user_id=current_user.user_id,
                display_name=request.display_name,
                password=request.password,
                role=request.role,
                is_active=request.is_active,
                department_id=request.department_id,
                department_supplied="department_id" in request.model_fields_set,
            )
        )
    except AdminUserError as exc:
        raise _map_error(exc) from exc


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(
    user_id: str,
    current_user: TokenPayload = Depends(require_admin),
    manager: AdminUserManager = Depends(get_admin_user_manager),
):
    try:
        await manager.delete(user_id, actor_user_id=current_user.user_id)
    except AdminUserError as exc:
        raise _map_error(exc) from exc
