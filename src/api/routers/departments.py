"""
Departments Router

部门管理端点（全部 Admin-only）：
- GET    /api/v1/departments               — 列出某父下的子部门
- GET    /api/v1/departments/tree          — 完整树
- GET    /api/v1/departments/{id}          — 单个详情
- POST   /api/v1/departments               — 显式创建
- PATCH  /api/v1/departments/{id}          — 仅改名
- POST   /api/v1/departments/{id}/move     — 搬家（含环检测）
- DELETE /api/v1/departments/{id}          — 仅删空部门
- POST   /api/v1/departments/resolve       — 路径 → id（缺失自动建）
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError

from api.dependencies import get_department_repository
from api.schemas.department import (
    CreateDepartmentRequest,
    UpdateDepartmentRequest,
    MoveDepartmentRequest,
    ResolveDepartmentRequest,
    DepartmentResponse,
    DepartmentListResponse,
    DepartmentTreeNode,
    DepartmentTreeResponse,
    ResolveDepartmentResponse,
)
from api.services.auth import TokenPayload
from api.dependencies import require_admin
from db.models import Department
from repositories.department_repo import DepartmentRepository
from utils.department_resolve import resolve_department_path
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")

router = APIRouter()


# ============================================================
# Helpers
# ============================================================

async def _to_response(
    dept: Department, repo: DepartmentRepository
) -> DepartmentResponse:
    """ORM → Response（带直属计数）"""
    return DepartmentResponse(
        id=dept.id,
        parent_id=dept.parent_id,
        name=dept.name,
        user_count=await repo.count_users(dept.id),
        child_count=await repo.count_children(dept.id),
        created_at=dept.created_at,
        updated_at=dept.updated_at,
    )


def _new_dept_id() -> str:
    return f"dept-{uuid.uuid4()}"


# ============================================================
# Routes
# ============================================================

@router.get("", response_model=DepartmentListResponse)
async def list_departments(
    parent_id: Optional[str] = Query(default=None, description="Parent id; omit for top-level"),
    _admin: TokenPayload = Depends(require_admin),
    repo: DepartmentRepository = Depends(get_department_repository),
):
    """
    列出某父下的子部门（按 name 排序）。

    parent_id 缺省 → 一级部门；传具体 id → 该部门的直接子。
    给前端 cascader 每级渲染用。
    """
    children = await repo.list_children(parent_id)
    return DepartmentListResponse(
        departments=[await _to_response(d, repo) for d in children],
    )


@router.get("/tree", response_model=DepartmentTreeResponse)
async def get_tree(
    _admin: TokenPayload = Depends(require_admin),
    repo: DepartmentRepository = Depends(get_department_repository),
):
    """
    返回完整部门树（顶层节点列表 → children 嵌套）。

    一次性返回，部门表数量级别小（几十到几百）。给部门管理面板用。
    user_count 是该节点的**直属**用户数，子树合计由前端按需计算。
    """
    all_depts = await repo.list_all()
    # 一次性算每个 dept 的直属 user_count
    from sqlalchemy import select, func
    from db.models import User
    rows = (await repo.session.execute(
        select(User.department_id, func.count())
        .where(User.department_id.is_not(None))
        .group_by(User.department_id)
    )).all()
    user_count_by_dept = {dept_id: count for dept_id, count in rows}

    # 建反向索引：parent_id → [Department, ...]
    children_by_parent: dict[Optional[str], list[Department]] = {}
    for d in all_depts:
        children_by_parent.setdefault(d.parent_id, []).append(d)

    def _build_node(dept: Department) -> DepartmentTreeNode:
        return DepartmentTreeNode(
            id=dept.id,
            parent_id=dept.parent_id,
            name=dept.name,
            user_count=user_count_by_dept.get(dept.id, 0),
            children=[_build_node(c) for c in children_by_parent.get(dept.id, [])],
        )

    top_level = children_by_parent.get(None, [])
    return DepartmentTreeResponse(nodes=[_build_node(d) for d in top_level])


@router.get("/{dept_id}", response_model=DepartmentResponse)
async def get_department(
    dept_id: str,
    _admin: TokenPayload = Depends(require_admin),
    repo: DepartmentRepository = Depends(get_department_repository),
):
    """单个部门详情（含 user_count, child_count，给详情/删除前置展示用）"""
    dept = await repo.get_by_id(dept_id)
    if dept is None:
        raise HTTPException(status_code=404, detail="Department not found")
    return await _to_response(dept, repo)


@router.post("", response_model=DepartmentResponse)
async def create_department(
    request: CreateDepartmentRequest,
    _admin: TokenPayload = Depends(require_admin),
    repo: DepartmentRepository = Depends(get_department_repository),
):
    """
    显式创建部门（admin 在管理 UI 中点 "+ 新建" 走这里）。

    冲突 → 409。批量导入 / 创建用户时的"路径解析自动建" 走 /resolve 端点。
    """
    # 校验 parent 存在
    if request.parent_id is not None:
        parent = await repo.get_by_id(request.parent_id)
        if parent is None:
            raise HTTPException(status_code=400, detail="parent_id does not reference an existing department")

    # 唯一性的两层防线：
    #   1. 路由层 pre-check（这里）— 99% 常规情况走的快路径，给 admin 友好的 409
    #   2. DB 层（uq_dept_parent_name 给 parent_id 非 NULL 的行；uq_dept_root_name
    #      partial index 给 parent_id IS NULL 的根级行）— source of truth，
    #      原子拒绝并发 SELECT-then-INSERT 的 TOCTOU 窗口
    # pre-check 漏掉的并发情况由下面的 IntegrityError catch 兜住。
    existing = await repo.find_by_parent_and_name(request.parent_id, request.name)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"A department named '{request.name}' already exists under this parent",
        )

    new_dept = Department(
        id=_new_dept_id(),
        parent_id=request.parent_id,
        name=request.name,
    )
    repo.session.add(new_dept)
    try:
        await repo.session.flush()
        await repo.session.commit()
        await repo.session.refresh(new_dept)
    except IntegrityError:
        # 并发请求在 pre-check 之间抢先 INSERT — DB 唯一约束（含根级 partial
        # unique index）拦下，这里转 409 给 admin
        await repo.session.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"A department named '{request.name}' already exists under this parent",
        )

    logger.info(f"Department created: {new_dept.name} (id={new_dept.id}, parent={new_dept.parent_id})")
    return await _to_response(new_dept, repo)


@router.patch("/{dept_id}", response_model=DepartmentResponse)
async def rename_department(
    dept_id: str,
    request: UpdateDepartmentRequest,
    _admin: TokenPayload = Depends(require_admin),
    repo: DepartmentRepository = Depends(get_department_repository),
):
    """
    仅改部门名（搬家走 POST /{id}/move，路径分离避免 PATCH 字段歧义）。

    冲突（同父下重名） → 409。
    """
    dept = await repo.get_by_id(dept_id)
    if dept is None:
        raise HTTPException(status_code=404, detail="Department not found")

    if dept.name == request.name:
        # No-op；直接返回
        return await _to_response(dept, repo)

    # Pre-check + IntegrityError 两层防线（见 create_department 注释）
    conflict = await repo.find_by_parent_and_name(dept.parent_id, request.name)
    if conflict is not None and conflict.id != dept.id:
        raise HTTPException(
            status_code=409,
            detail=f"A department named '{request.name}' already exists under the same parent",
        )

    dept.name = request.name
    try:
        await repo.session.flush()
        await repo.session.commit()
        await repo.session.refresh(dept)
    except IntegrityError:
        await repo.session.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"A department named '{request.name}' already exists under the same parent",
        )

    logger.info(f"Department renamed: {dept.id} → '{dept.name}'")
    return await _to_response(dept, repo)


@router.post("/{dept_id}/move", response_model=DepartmentResponse)
async def move_department(
    dept_id: str,
    request: MoveDepartmentRequest,
    _admin: TokenPayload = Depends(require_admin),
    repo: DepartmentRepository = Depends(get_department_repository),
):
    """
    搬家：把部门挂到新父下。new_parent_id=None → 搬到根。

    校验：
    - 环检测：不能搬到自己 / 自己子孙下 → 400
    - 名称冲突：新父下已有同名 → 409
    - 新父不存在 → 400
    """
    dept = await repo.get_by_id(dept_id)
    if dept is None:
        raise HTTPException(status_code=404, detail="Department not found")

    if request.new_parent_id is not None:
        parent = await repo.get_by_id(request.new_parent_id)
        if parent is None:
            raise HTTPException(status_code=400, detail="new_parent_id does not reference an existing department")

    if await repo.would_create_cycle(dept_id, request.new_parent_id):
        raise HTTPException(
            status_code=400,
            detail="Cannot move department under itself or its descendants",
        )

    if dept.parent_id == request.new_parent_id:
        # No-op
        return await _to_response(dept, repo)

    # Pre-check + IntegrityError 两层防线（见 create_department 注释）
    conflict = await repo.find_by_parent_and_name(request.new_parent_id, dept.name)
    if conflict is not None and conflict.id != dept.id:
        raise HTTPException(
            status_code=409,
            detail=f"A department named '{dept.name}' already exists under the new parent",
        )

    dept.parent_id = request.new_parent_id
    try:
        await repo.session.flush()
        await repo.session.commit()
        await repo.session.refresh(dept)
    except IntegrityError:
        await repo.session.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"A department named '{dept.name}' already exists under the new parent",
        )

    logger.info(f"Department moved: {dept.id} → parent={dept.parent_id}")
    return await _to_response(dept, repo)


@router.delete("/{dept_id}", status_code=204)
async def delete_department(
    dept_id: str,
    _admin: TokenPayload = Depends(require_admin),
    repo: DepartmentRepository = Depends(get_department_repository),
):
    """
    删除部门 — 必须为空（无子部门、无直属用户）。

    非空 → 409 + body 含 user_count/child_count，前端据此提示先迁。
    parent_id ondelete=RESTRICT 在 DB 层兜底，但路由层先校验给出更友好错误。
    """
    dept = await repo.get_by_id(dept_id)
    if dept is None:
        raise HTTPException(status_code=404, detail="Department not found")

    user_count = await repo.count_users(dept_id)
    child_count = await repo.count_children(dept_id)
    if user_count > 0 or child_count > 0:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Department is not empty",
                "user_count": user_count,
                "child_count": child_count,
            },
        )

    await repo.delete(dept)
    logger.info(f"Department deleted: {dept_id} ('{dept.name}')")


@router.post("/resolve", response_model=ResolveDepartmentResponse)
async def resolve_path(
    request: ResolveDepartmentRequest,
    _admin: TokenPayload = Depends(require_admin),
    repo: DepartmentRepository = Depends(get_department_repository),
):
    """
    路径 → 末级 dept_id。缺失层级自动创建（admin 显式提交，安全）。

    给前端 cascader "+ 新建当前级" 入口、PR3 批量导入时按行解析用。
    空路径 / 全空字符串 → 返回 {id: null}。
    """
    dept_id = await resolve_department_path(repo, request.path)
    return ResolveDepartmentResponse(id=dept_id)
