"""
Department path resolution.

resolve_department_path: 接收部门路径列表（顶层 → 末级），逐级
SELECT-or-CREATE，返回末级部门 id。空路径 / 全空字符串 → None。

并发安全：INSERT 命中 UNIQUE(parent_id, name) → rollback + 重 SELECT
拿被其他并发请求抢先创建的行；最多重试 1 次（第二次仍冲突说明 bug，向上抛）。

复用点：
- POST /admin/users（单条创建用户时按部门路径解析 / 落库）
- POST /departments/resolve（前端 cascader 提交前的显式解析）
- PR3 CSV 批量导入（每行的 dept_l1/2/3 → department_id）
"""

import uuid
from typing import Optional

from sqlalchemy.exc import IntegrityError

from db.models import Department
from repositories.department_repo import DepartmentRepository


def _new_dept_id() -> str:
    return f"dept-{uuid.uuid4()}"


async def resolve_department_path(
    repo: DepartmentRepository,
    path: list[str],
) -> Optional[str]:
    """
    解析部门路径，缺失的层级自动创建。

    Args:
        repo: DepartmentRepository（外部管理 session 生命周期）
        path: 部门名列表，从顶层开始；空列表或全空字符串 → 返回 None

    Returns:
        末级部门 id；空 path → None

    Raises:
        IntegrityError: 第二次 SELECT 仍找不到 — 说明 schema/逻辑有 bug，向上抛
    """
    cleaned = [seg.strip() for seg in path if seg and seg.strip()]
    if not cleaned:
        return None

    parent_id: Optional[str] = None
    for name in cleaned:
        existing = await repo.find_by_parent_and_name(parent_id, name)
        if existing is not None:
            parent_id = existing.id
            continue

        new_dept = Department(
            id=_new_dept_id(),
            parent_id=parent_id,
            name=name,
        )
        repo.session.add(new_dept)
        try:
            await repo.session.flush()
            await repo.session.commit()
            parent_id = new_dept.id
        except IntegrityError:
            # 并发命中 UNIQUE(parent_id, name) — 其他请求抢先创建了同名部门
            await repo.session.rollback()
            existing = await repo.find_by_parent_and_name(parent_id, name)
            if existing is None:
                # 罕见：rollback 后仍找不到 — 数据/逻辑有更深层问题，抛
                raise
            parent_id = existing.id

    return parent_id
