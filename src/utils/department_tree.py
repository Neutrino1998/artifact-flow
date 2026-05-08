"""
Department tree helpers.

expand_subtree: BFS 内存展开 — 给一组 seed dept_id 与全量部门列表，
返回 seeds 自身 + 所有子孙的 id 集合。

复用点：
- 用户搜索：admin 搜部门名 → 命中根部门时返回整个子树的所有用户
- 部门管理 UI cascader excludeSubtreeOf：搬家时禁选自己的子孙
"""

from typing import Iterable

from db.models import Department


def expand_subtree(
    all_depts: Iterable[Department],
    seeds: set[str],
) -> set[str]:
    """
    BFS 内存展开：seed 集合 + 它们所有子孙。

    Args:
        all_depts: 全部部门列表（一次拉取，避免递归 SQL）
        seeds: 起始 dept_id 集合

    Returns:
        包含 seeds 与所有子孙 dept_id 的集合；seeds 为空 → 空集
    """
    if not seeds:
        return set()

    # 反向索引：parent_id → [child_id, ...]
    children_by_parent: dict[str | None, list[str]] = {}
    for dept in all_depts:
        children_by_parent.setdefault(dept.parent_id, []).append(dept.id)

    result: set[str] = set()
    stack: list[str] = list(seeds)
    while stack:
        current = stack.pop()
        if current in result:
            continue
        result.add(current)
        stack.extend(children_by_parent.get(current, []))

    return result
