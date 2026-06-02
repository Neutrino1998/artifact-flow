"""ArtifactWorkingSet —— turn 级纯状态(内存缓存 + dirty/new 标记)。

从原 ``ArtifactManager`` 的五职责里抽出**①内存工作缓存 + ②unit-of-work**两项,
其余(repo 持有、进程注册表、业务/序列化)归 ``ArtifactService``。

刻意**不持有** DB session、**不持有**进程级注册表、**不发**事件——它就是一个
turn 级的可变状态袋。控制器(执行轮)与 REST(请求级)各自独占一个实例,**绝不
共享**:跨实例共享内存态正是旧 ``_active_managers`` 在多 worker 下静默失效的根因
(见 docs/_archive/design/artifact-layer-redesign-plan.md 背景节)。REST 侧实例的
WorkingSet 始终为空,故其 Service 读取自然落到纯 DB。
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from utils.time import utc_now


# ============================================================
# 内存对象(用于 diff-match-patch 处理 / 序列化)
# ============================================================

@dataclass
class ArtifactVersionMemory:
    """Artifact 版本记录(内存对象)"""
    version: int
    content: str
    updated_at: datetime
    update_type: str  # "create", "update", "update_fuzzy", "rewrite"
    changes: Optional[List[Tuple[str, str]]] = None  # [(old_str, new_str), ...]


class ArtifactMemory:
    """Artifact 内存对象。

    用于处理 diff-match-patch 逻辑,与数据库模型分离,保持原有的模糊匹配能力。
    """

    def __init__(
        self,
        artifact_id: str,
        content_type: str,
        title: str,
        content: str,
        current_version: int = 1,
        metadata: Optional[Dict] = None,
        created_at: Optional[datetime] = None,
        source: str = "agent",
    ):
        self.id = artifact_id
        self.content_type = content_type
        self.title = title
        self.content = content
        self.metadata = metadata or {}
        self.current_version = current_version
        self.created_at = created_at or utc_now()
        self.updated_at = utc_now()
        self.source = source


# ============================================================
# WorkingSet —— 纯状态
# ============================================================

class ArtifactWorkingSet:
    """turn 级内存工作态:缓存 + dirty/new 标记。无 DB、无注册表、无事件。"""

    def __init__(self) -> None:
        self._cache: Dict[str, Dict[str, ArtifactMemory]] = {}  # {session_id: {artifact_id: ArtifactMemory}}
        self._current_session_id: Optional[str] = None
        # `_dirty` 与 `_new` 都用 insertion-ordered dict-as-ordered-set,使迭代
        # 跟随创建顺序而非 Python hash 顺序:
        # - Service.list_artifacts 迭代 `_new` 追加尚未 flush 的 artifact —— 缺了
        #   插入顺序,session 级消费者(grep_artifact 的 session cap 截断等)在不同
        #   进程间会因 hash 随机化抖动「哪个 artifact 命中预算上限」。
        # - Service.flush_all 迭代 `_dirty` 构造 INSERT 顺序,它决定新行的
        #   `created_at` server_default 排序;hash 序 flush 会把 hash 随机化泄漏到
        #   下一轮的 post-flush DB 排序。
        self._dirty: Dict[Tuple[str, str], None] = {}
        self._new: Dict[Tuple[str, str], None] = {}

    # ---- session ----

    def set_session(self, session_id: str) -> None:
        """设置当前 session(供工具读取),并确保缓存槽位存在。"""
        self._current_session_id = session_id
        self._cache.setdefault(session_id, {})

    @property
    def current_session_id(self) -> Optional[str]:
        return self._current_session_id

    # ---- 缓存读写(纯内存,不触发 DB)----

    def peek(self, session_id: str, artifact_id: str) -> Optional[ArtifactMemory]:
        """返回缓存里的 ArtifactMemory(miss 返回 None)。不查 DB。"""
        return self._cache.get(session_id, {}).get(artifact_id)

    def put(self, session_id: str, memory: ArtifactMemory) -> None:
        """写入/覆盖缓存条目。"""
        self._cache.setdefault(session_id, {})[memory.id] = memory

    def cached(self, session_id: str) -> Dict[str, ArtifactMemory]:
        """返回某 session 的缓存 dict(只读视图,调用方勿改)。"""
        return self._cache.get(session_id, {})

    # ---- dirty / new 标记 ----

    def mark_new(self, session_id: str, artifact_id: str) -> None:
        """标记为本轮新建(同时 dirty)。"""
        key = (session_id, artifact_id)
        self._dirty[key] = None
        self._new[key] = None

    def mark_dirty(self, session_id: str, artifact_id: str) -> None:
        """标记内容已改、待 flush。"""
        self._dirty[(session_id, artifact_id)] = None

    def is_dirty(self, session_id: str, artifact_id: str) -> bool:
        return (session_id, artifact_id) in self._dirty

    def is_new(self, session_id: str, artifact_id: str) -> bool:
        return (session_id, artifact_id) in self._new

    def has_dirty(self) -> bool:
        return bool(self._dirty)

    def dirty_keys(self, session_id: str) -> List[Tuple[str, str]]:
        """按插入顺序返回该 session 的所有 dirty 键。"""
        return [(sid, aid) for (sid, aid) in self._dirty if sid == session_id]

    def new_keys(self, session_id: str) -> List[Tuple[str, str]]:
        """按插入顺序返回该 session 的所有 new 键。"""
        return [(sid, aid) for (sid, aid) in self._new if sid == session_id]

    def clear_one(self, session_id: str, artifact_id: str) -> None:
        """flush 成功后清除 dirty/new 标记(缓存保留,供同轮后续读取)。"""
        key = (session_id, artifact_id)
        self._dirty.pop(key, None)
        self._new.pop(key, None)
