"""reconcile 结果汇报(给 ops log + 测试断言)。"""

from dataclasses import dataclass, field
from typing import List


@dataclass
class ReconcileReport:
    """一次 reconcile 的逐项结果。name 用 `<kind>:<name>` 前缀(如 `tool_unit:weather`)。"""

    created: List[str] = field(default_factory=list)
    updated: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    pruned: List[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        """这次是否有实际写入(幂等重跑应为 False)。"""
        return bool(self.created or self.updated or self.pruned)

    def summary(self) -> str:
        return (
            f"reconcile: created={len(self.created)} updated={len(self.updated)} "
            f"skipped={len(self.skipped)} pruned={len(self.pruned)}"
        )
