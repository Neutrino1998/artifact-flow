"""ArtifactService —— artifact 层的薄编排(无状态业务逻辑)。

承接原 ``ArtifactManager`` 五职责里的:③repo 持有 / ④业务编排 / ⑤序列化,
另加**发事件**。**纯状态(缓存 + dirty/new)归 ``ArtifactWorkingSet``**,
**纯算法(模糊匹配 ``compute_update`` / grep 扫描)留各自模块**——Service 只
**调用**,不内联。

实例化关系(关键):每个 Service **独占**自己的 ``ArtifactWorkingSet``,**不共享**。
旧 ``_active_managers`` 进程级注册表已删除——它在多 worker 下静默失效(REST live
overlay 跨 worker 读不到执行 worker 的内存态)。现在:
- **执行轮**:控制器持有一个 Service,WorkingSet 随 turn 填充;引擎在 loop 起点
  ``bind_emit`` 注入事件回调,create/update/rewrite/上传 stage 经此发 ``ARTIFACT_*``。
- **REST 请求**:``get_artifact_service`` 每请求新建一个 Service,WorkingSet 恒空、
  无 emit → 读取自然落到纯 DB(turn 中故意落后于 live,由前端事件流补,见 plan 决策 6)。
"""

import math  # noqa: F401  (保留:历史上 ReadArtifactTool 用过,避免无意义 churn——实际由 artifact_ops 持有)
import os
import re
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.exc import IntegrityError

from config import config
from repositories.artifact_repo import ArtifactRepository
from repositories.base import NotFoundError, DuplicateError
from tools.base import ArtifactSpec
from tools.builtin.artifact_working_set import ArtifactMemory, ArtifactWorkingSet
from utils.logger import get_logger
from utils.time import utc_now

logger = get_logger("ArtifactFlow")

# Artifact live 事件类型值。**不在顶层 import core.events**:tools 包被 core 先
# import,顶层引 core 会触发 core/__init__ → controller → 本模块的循环 import。
# 故延迟到首次调用(那时各模块都加载完了)再从权威 enum 取值并缓存 —— 值由 enum
# 直接派生,字面量复制带来的 drift 结构上不再可能(无需再靠 drift 测兜底)。
@lru_cache(maxsize=1)
def _evt_artifact_created() -> str:
    from core.events import StreamEventType
    return StreamEventType.ARTIFACT_CREATED.value


@lru_cache(maxsize=1)
def _evt_artifact_updated() -> str:
    from core.events import StreamEventType
    return StreamEventType.ARTIFACT_UPDATED.value


# Artifact ID 合法字符集:letter/digit/underscore + hyphen + dot,1-64 字符。
# envelope renderer 依赖此前提把 id 当受控值放入 XML attribute;create_artifact
# 入口校验,_stage_artifact(create_from_upload / ingest_tool_result 共用内核)
# 通过 _normalize_filename_to_id + dedup 保证生成的 ID 满足该 pattern。
_ARTIFACT_ID_PATTERN = re.compile(r"^[\w\-.]{1,64}$")


def _normalize_filename_to_id(filename: str, max_base_len: int = 56) -> str:
    """文件名 → 合法 artifact_id base。

    保留扩展名(如果短),预留 8 字符给 dedup suffix(_NNNN.ext)让最终 ID
    仍在 64 字符内。全 Unicode 文件名降级为 "upload"。
    """
    base = re.sub(r'[^\w\-.]', '_', filename).lower()
    if not base:
        base = "upload"
    if len(base) <= max_base_len:
        return base
    name_part, dot, ext_part = base.rpartition('.')
    # 短扩展名保留:keep + '.' + ext == max_base_len
    if name_part and dot and 1 <= len(ext_part) <= 10:
        keep = max_base_len - len(ext_part) - 1
        if keep >= 1:
            return name_part[:keep] + '.' + ext_part
    return base[:max_base_len]


class ArtifactService:
    """Artifact 用例编排:dedup / 版本计算 / 调纯算法 / 上传 stage / tool-result
    持久化 / flush(WorkingSet→Repo)/ 发事件。无 artifact 领域状态(那在 WorkingSet)。

    用法(两种,B-5):
        # 引擎/turn 路径 —— 持 db_manager,DB 读/写各开短 retrying session 读完即关
        # (不骑 turn-long session),WorkingSet 留在本实例做 turn-live 缓存:
        service = ArtifactService(db_manager=db_manager)
        # REST/请求路径 —— 绑请求级 session 的 repo(请求生命周期内有效):
        service = ArtifactService(ArtifactRepository(session))
    """

    def __init__(
        self,
        repository: Optional[ArtifactRepository] = None,
        working_set: Optional[ArtifactWorkingSet] = None,
        db_manager=None,
    ):
        # repository = 请求级 bound session(REST/测试);db_manager = 短 session 工厂
        # (引擎/turn 路径)。turn-path DB 触碰一律经 _run_with_repo:有 db_manager → fresh
        # retrying session 读完即关(B-5);否则回落 bound repo(REST 请求级 / 测试)。
        self.repository = repository
        self._db_manager = db_manager
        self._ws = working_set or ArtifactWorkingSet()
        # turn 级事件回调(引擎 bind);REST 侧恒 None → 不发事件。详见模块 docstring。
        self._emit = None
        # 本 turn 已发过「整文 live 事件」(故前端已有 base)的 artifact id 集合。
        # update 只有在 id 已在此集合时才发 span delta,否则发整文——保证前端**永不**
        # 收到无 base 可应用的 delta,从而无需中途查 DB 取 base(守原则 1:中途不查 DB
        # 求 live 态)。bind_emit(真回调)时按 turn 重置。
        self._emitted_base: set = set()

    # ========================================
    # 接线 / 状态委托
    # ========================================

    def bind_emit(self, emit) -> None:
        """引擎在 execute_loop 起点注入 ``_emit`` 闭包,loop 末传 None 解绑,
        防止跨 turn 持有失效闭包。REST 永不调用此方法。"""
        self._emit = emit
        if emit is not None:
            self._emitted_base = set()  # 每 turn 重置 base 跟踪

    async def _emit_artifact(self, event_type: str, data: Dict[str, Any]) -> None:
        """发 SSE-only artifact 事件(仅当 emit 已 bind,即执行轮)。REST 侧 _emit
        为 None → no-op。agent=None:artifact 事件按 id 归并,不依赖 agent,且 SSE-only
        不入历史。"""
        if self._emit is None:
            return
        await self._emit(event_type, None, data, sse_only=True)

    @staticmethod
    def _content_payload(content: str) -> Dict[str, Any]:
        """整文事件载荷:超 live 上限则省略正文、只发已变更信号(靠 COMPLETE 后 DB
        对齐补全),否则带整文。"""
        if len(content) > config.ARTIFACT_LIVE_CONTENT_MAX_CHARS:
            return {"content_omitted": True}
        return {"content": content}

    def _note_base(self, artifact_id: str, payload: Dict[str, Any]) -> None:
        """记录前端是否已拿到该 artifact 的整文 base:带 content 则前端有 base
        (后续 update 可发 delta);content_omitted(超限)则前端没有 base,下次仍发整文。"""
        if "content" in payload:
            self._emitted_base.add(artifact_id)
        else:
            self._emitted_base.discard(artifact_id)

    async def _register_new(self, session_id: str, memory: ArtifactMemory) -> None:
        """把一个新建的 ArtifactMemory 落入 WorkingSet(mark_new → 随 flush_all 落库)
        并发 ARTIFACT_CREATED。模型自建与用户上传共用此路径(统一生命周期)。"""
        self._ws.put(session_id, memory)
        self._ws.mark_new(session_id, memory.id)
        payload = self._content_payload(memory.content)
        # 二进制 artifact:事件只带元数据(has_blob/size/真实 MIME),**绝不带字节**——
        # 前端据 has_blob 去 GET …/raw 取图渲染或下载原件(blob 有专属持久家,事件流
        # 里再塞一份字节无读者、且撑爆 SSE)。图片 content="" 本就天然 metadata-only。
        blob = getattr(memory, "blob", None)
        blob_meta: Dict[str, Any] = {}
        if blob is not None:
            # 前端据 has_blob 去 /raw 取图/下载;MIME 用事件已带的 content_type
            # (XOR 下即原件真实 MIME),不另发 blob_content_type。
            blob_meta = {
                "has_blob": True,
                "blob_size": len(blob),
            }
        # 用户上传件带 original_filename:前端据此把本轮 ARTIFACT_CREATED 关联回 send-local
        # 预览缓存里的图片 File(发送时存入、与 liveContent 同生命周期),turn 内(blob 未落库、
        # /raw 还 404)直接用本地副本渲染缩略图/预览,COMPLETE 后再切回 DB(见前端 ImagePreview
        # 本地优先解析)。模型自建无此字段 → 不带,事件保持干净。
        original_filename = (memory.metadata or {}).get("original_filename")
        name_meta = {"original_filename": original_filename} if original_filename else {}
        await self._emit_artifact(_evt_artifact_created(), {
            "id": memory.id,
            "title": memory.title,
            "content_type": memory.content_type,
            "source": memory.source,
            "current_version": memory.current_version,
            **name_meta,
            **blob_meta,
            **payload,
        })
        self._note_base(memory.id, payload)

    @property
    def working_set(self) -> ArtifactWorkingSet:
        return self._ws

    @property
    def current_session_id(self) -> Optional[str]:
        return self._ws.current_session_id

    def set_session(self, session_id: str) -> None:
        """设置当前 session(供工具读取 current_session_id)。"""
        self._ws.set_session(session_id)

    def _ensure_repository(self) -> ArtifactRepository:
        if self.repository is None:
            raise RuntimeError("ArtifactService: repository not configured")
        return self.repository

    async def _run_with_repo(self, fn):
        """单次 DB 触碰的会话边界(B-5)。有 db_manager → fresh retrying session,fn 在其
        内拿一个临时 ArtifactRepository、读完即关;否则回落 bound repo(REST 请求级 / 测试)。

        闸:fn **必须在回调内完成读 + 序列化**(返回纯 dict / 标量 / bool),不得把 ORM 行
        漏到回调外 —— session 已关,detached 实例触 lazy 属性会 MissingGreenlet。
        """
        if self._db_manager is not None:
            async def _attempt(session):
                return await fn(ArtifactRepository(session))
            return await self._db_manager.with_retry(_attempt)
        return await fn(self._ensure_repository())

    async def ensure_session_exists(self, session_id: str) -> None:
        """确保 ArtifactSession 存在(数据库层)。"""
        await self._run_with_repo(lambda repo: repo.ensure_session_exists(session_id))

    # ========================================
    # 创建
    # ========================================

    async def create_artifact(
        self,
        session_id: str,
        artifact_id: str,
        content_type: str,
        title: str,
        content: str,
        metadata: Optional[Dict] = None,
        source: str = "agent",
    ) -> Tuple[bool, str]:
        """创建新的 Artifact(只写 WorkingSet,flush_all 时持久化)。"""
        # ID 校验:限制为 word/hyphen/dot,1-64 字符。这是 envelope renderer 把 id
        # 放入 attribute 的安全前提,也和 create_from_upload 的 sanitize 规则一致。
        if not _ARTIFACT_ID_PATTERN.match(artifact_id):
            return False, (
                f"Invalid artifact_id '{artifact_id}': must be 1-64 chars of "
                f"letters/digits/underscore/hyphen/dot only."
            )
        try:
            await self.ensure_session_exists(session_id)

            # 检查缓存和 DB 中是否已存在(DB 查经短 session,B-5;只判存在性,不读行属性)
            if self._ws.peek(session_id, artifact_id) is not None:
                return False, f"Artifact '{artifact_id}' already exists in session"

            existing = await self._run_with_repo(
                lambda repo: repo.get_artifact(session_id, artifact_id)
            )
            if existing is not None:
                return False, f"Artifact '{artifact_id}' already exists in session"

            memory = ArtifactMemory(
                artifact_id=artifact_id,
                content_type=content_type,
                title=title,
                content=content,
                current_version=1,
                metadata=metadata,
                source=source,
            )
            await self._register_new(session_id, memory)

            logger.info(f"Created artifact '{artifact_id}' in session '{session_id}' (pending flush)")
            return True, f"Created artifact '{artifact_id}'"

        except NotFoundError as e:
            return False, str(e)
        except Exception as e:
            logger.exception(f"Failed to create artifact: {e}")
            return False, f"Failed to create artifact: {str(e)}"

    async def _stage_artifact(
        self,
        session_id: str,
        id_base: str,
        content_type: str,
        title: str,
        content: str,
        *,
        metadata: Optional[Dict] = None,
        blob: Optional[bytes] = None,
        source: str,
        original_filename: Optional[str] = None,
    ) -> Tuple[bool, str, Optional[str]]:
        """共享 stage 内核:XOR 校验 → dedup → ID 校验 → blob 上限 → per-user 配额 → register。
        返回 ``(success, message, artifact_id)``;失败时 artifact_id 为 None。

        三个调用方(用户上传 / 沙盒 persist / 工具结果)经此单一 chokepoint,blob
        存储边界、配额与 XOR 不变量只在这一处守门(不逐路径加闸)。``id_base`` 须已是
        合法 artifact_id base(经 ``_normalize_filename_to_id``)。
        """
        # XOR 不变量:一个 artifact 只存**一份**实质 data —— 文本走 content、二进制走 blob,
        # 二者不可兼得。双表示(blob 原件 + content 转换文本)语义对模型 confusing,且
        # backend 已无转换路径产生它;模型侧统一按「blob = 无文本表示、需 mount」认知
        # (见 read_artifact / inventory 文案)。空 content + blob = blob-only;有 content
        # + 无 blob = text;两者皆有 = loud-fail。
        if blob is not None and content:
            return False, (
                "An artifact stores exactly one representation: text in `content` "
                "OR binary in `blob`, never both."
            ), None

        # Dedup:同时对 WorkingSet(本轮已 stage / 缓存)与 DB(往轮已落库)去重,
        # 追加后缀直到唯一。整个冲突扫描在**一条**短 session 内走完(B-5 #4)——不再每探一次
        # 各开一条 session(K 个同名冲突 → K+1 次借还)。WorkingSet.peek 是内存、放回调内无妨;
        # 只判存在性(is not None),不读行属性,故 ORM 行不外逃。
        async def _dedup(repo) -> str:
            aid = id_base
            suffix = 0
            while (
                self._ws.peek(session_id, aid) is not None
                or await repo.get_artifact(session_id, aid) is not None
            ):
                suffix += 1
                name_part, _, ext_part = id_base.rpartition('.')
                if name_part:
                    aid = f"{name_part}_{suffix}.{ext_part}"
                else:
                    aid = f"{id_base}_{suffix}"
            return aid

        artifact_id = await self._run_with_repo(_dedup)

        # 最终守门员:理论上 normalize + dedup(≤4 位后缀)总 ≤ 64,但留个防御性
        # 检查避免万一边界 bug 让脏 ID 进 DB
        if not _ARTIFACT_ID_PATTERN.match(artifact_id):
            return False, (
                f"Generated invalid artifact_id {artifact_id!r} "
                f"(must match {_ARTIFACT_ID_PATTERN.pattern})"
            ), None

        # blob 大小上限 loud-fail(写入侧,不静默截断)。上传本已受 MAX_UPLOAD_SIZE
        # 约束,此处是 blob 存储边界的兜底守门(沙盒回写 / 工具结果走 blob 时同样受护)。
        if blob is not None and len(blob) > config.ARTIFACT_BLOB_MAX_BYTES:
            max_mb = config.ARTIFACT_BLOB_MAX_BYTES / 1024 / 1024
            return False, (
                f"Binary too large for storage: {len(blob) / 1024 / 1024:.1f}MB "
                f"(max {max_mb:.0f}MB)"
            ), None

        # per-用户 blob 配额：写入侧的真正守门。所有 blob 都经此 chokepoint（上传 +
        # 沙盒 persist + 工具结果 + 未来任何路径），所以一处校验全覆盖 —— 不逐路径加闸。
        # /chat 的 HTTP 预闸保留为 fail-fast（起 turn 前就拒、零 DB 状态），此处是
        # correctness 兜底，也是沙盒 persist / 工具结果唯一的配额闸。计入「DB 已落 +
        # 本轮已 stage 未 flush」的 blob：否则一轮内多次写各自只看 DB，会齐齐放行、
        # 整体击穿配额。超限 return False → 调用方转 ToolResult 错误（沙盒 persist /
        # 工具结果）或 loud-abort 本轮（上传 staging）。
        if blob is not None and config.ARTIFACT_USER_QUOTA_BYTES > 0:
            # 一趟查询：子查询内联把 session_id 解析成属主再聚合(属主跨全部会话已落字节)。
            # 无主会话 → 返回 0,仍由下方数值判定兜单个超大 blob。短 session 读(B-5),返回标量。
            committed = await self._run_with_repo(
                lambda repo: repo.get_user_blob_bytes_for_session(session_id)
            )
            # staged = 本轮已 stage 但**未 flush** 的 blob。扫 dirty 标记(非整个 cache):
            # flush 的 clear_one 已把已落盘条目移出 dirty,故已 committed 的 blob 不会
            # 在此被重复计数 —— 即便将来同一 service 在 flush 后继续 stage 也安全。
            staged = sum(
                len(m.blob)
                for sid, aid in self._ws.dirty_keys(session_id)
                if (m := self._ws.peek(sid, aid)) is not None and m.blob is not None
            )
            if committed + staged + len(blob) > config.ARTIFACT_USER_QUOTA_BYTES:
                quota_mb = config.ARTIFACT_USER_QUOTA_BYTES / 1024 / 1024
                used_mb = (committed + staged) / 1024 / 1024
                return False, (
                    f"Storage quota exceeded: this {len(blob) / 1024 / 1024:.1f}MB file "
                    f"would put the user over their {quota_mb:.0f}MB storage limit "
                    f"(currently using {used_mb:.1f}MB). Tell the user to delete a "
                    f"conversation to free space, then retry."
                ), None

        stage_metadata = dict(metadata or {})
        if original_filename is not None:
            stage_metadata["original_filename"] = original_filename  # 下载文件名
        # 「是否二进制」由 Artifact.has_blob 列承载(repo 建行时按 blob 在场写死),
        # 不再往 metadata 塞 blob_content_type —— content_type 已是原件 MIME。

        try:
            await self.ensure_session_exists(session_id)
            memory = ArtifactMemory(
                artifact_id=artifact_id,
                content_type=content_type,
                title=title,
                content=content,
                current_version=1,
                metadata=stage_metadata,
                source=source,
                blob=blob,
            )
            await self._register_new(session_id, memory)
            return True, f"Created artifact '{artifact_id}'", artifact_id
        except Exception as e:
            logger.exception(f"Failed to stage artifact: {e}")
            return False, f"Failed to create artifact: {str(e)}", None

    async def ingest_tool_result(
        self,
        session_id: str,
        spec: ArtifactSpec,
        tool_name: Optional[str] = None,
    ) -> Tuple[bool, str, Optional[str]]:
        """把工具声明的结果(``ArtifactSpec``)落盘为具名 artifact,返回
        ``(success, message, artifact_id)``。

        ``create_from_upload`` 之外的第三调用方,共用同一 ``_stage_artifact`` 内核
        (dedup / blob 上限 / 配额)。两种形态:① **具名**——工具显式给 spec(如
        web_fetch 文件旁路:blob + content_type);② **无名兜底**——引擎对超长文本
        结果合成一个 ``text/plain`` spec(原 ``persist_tool_result`` 的去向)。
        source 固定 "tool"。
        """
        # id base:filename 优先(决定下载名 + id),否则 title,否则工具名,最后兜底。
        base_name = spec.filename or spec.title or (
            f"{tool_name}_output" if tool_name else "tool_output"
        )
        id_base = _normalize_filename_to_id(base_name)

        tool_metadata = dict(spec.metadata or {})
        if tool_name:
            tool_metadata.setdefault("tool_name", tool_name)  # 审计:原始工具名
        tool_metadata.setdefault("persisted_at", utc_now().isoformat())

        title = spec.title or os.path.splitext(spec.filename or id_base)[0]

        return await self._stage_artifact(
            session_id=session_id,
            id_base=id_base,
            content_type=spec.content_type,
            title=title,
            content=spec.content or "",
            metadata=tool_metadata,
            blob=spec.blob,
            source="tool",
            original_filename=spec.filename,
        )

    async def create_from_upload(
        self,
        session_id: str,
        filename: str,
        content: str,
        content_type: str,
        metadata: Optional[Dict] = None,
        blob: Optional[bytes] = None,
        source: str = "user_upload",
    ) -> Tuple[bool, str, Optional[Dict]]:
        """从一个「文件」**stage** 一个 artifact 进 WorkingSet(不即时 commit)。

        与模型自建走同一 write-back 路径:mark_new → 发 ARTIFACT_CREATED → 随 turn 末
        flush_all 落库。两个调用方:① 用户上传(execute_loop 在 turn 起点调用,uploads
        closure-carry 进引擎;turn 中途死 = 与模型产物一致地丢失,用户重选文件重试);
        ② 沙盒 persist(source="sandbox",同一套 `_normalize` + `_N` dedup —— persist
        永远产新 artifact 的机制即在此)。dedup / blob 上限 / 配额走共享 ``_stage_artifact``。
        """
        title = os.path.splitext(filename)[0]
        ok, message, artifact_id = await self._stage_artifact(
            session_id=session_id,
            id_base=_normalize_filename_to_id(filename),
            content_type=content_type,
            title=title,
            content=content,
            metadata=metadata,
            blob=blob,
            source=source,
            original_filename=filename,
        )
        if not ok:
            return False, message, None
        return True, message, {
            "id": artifact_id,
            "session_id": session_id,
            "content_type": content_type,
            "title": title,
            "current_version": 1,
            "source": source,
            "original_filename": filename,
            "has_blob": blob is not None,
        }

    # ========================================
    # 读取
    # ========================================

    async def get_artifact(
        self,
        session_id: str,
        artifact_id: str,
    ) -> Optional[ArtifactMemory]:
        """获取 Artifact(优先从缓存,miss 时从 DB 加载并回填缓存)。"""
        cached = self._ws.peek(session_id, artifact_id)
        if cached is not None:
            return cached

        # cache-miss → 短 session 读 DB(B-5)。在回调内就地建 ArtifactMemory(纯 dataclass)
        # 再返回,ORM 行不出 session;回填 WorkingSet 后即 turn-live 缓存(故只 miss 时读 DB)。
        async def _load(repo) -> Optional[ArtifactMemory]:
            db_artifact = await repo.get_artifact(session_id, artifact_id)
            if not db_artifact:
                return None
            return ArtifactMemory(
                artifact_id=db_artifact.id,
                content_type=db_artifact.content_type,
                title=db_artifact.title,
                content=db_artifact.content,
                current_version=db_artifact.current_version,
                metadata=db_artifact.metadata_,
                created_at=db_artifact.created_at,
                source=db_artifact.source,
                has_blob=db_artifact.has_blob,  # blob lazy 未载,判别取列值
            )

        memory = await self._run_with_repo(_load)
        if memory is None:
            return None
        self._ws.put(session_id, memory)
        return memory

    async def read_artifact(
        self,
        session_id: str,
        artifact_id: str,
        version: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """读取 Artifact 内容(序列化为 dict)。"""
        if version is None:
            memory = await self.get_artifact(session_id, artifact_id)
            if not memory:
                return None
            # 当前版本视图 = 与 list_artifacts 同一形状,收敛到单一序列化点
            # (含 has_blob —— read 工具据此给二进制契约文案;避免 read/list 字段漂移)。
            return self._serialize_memory(memory, include_content=True)

        # 显式 version 读取:优先匹配 in-memory current_version。否则 read 一个还没
        # flush 的 artifact(如刚 web_fetch 持久化的)用它 envelope 里看到的 version=1
        # 会 404,模型困惑。
        memory = await self.get_artifact(session_id, artifact_id)
        if memory and memory.current_version == version:
            # 显式 version 命中当前版本 = 同上当前视图,复用同一序列化点。
            return self._serialize_memory(memory, include_content=True)

        # 不是当前版本 → 走 DB 取历史版本快照(短 session,B-5;返回纯文本)。
        # 这是**旧版本快照**,与「当前 artifact 视图」(_serialize_memory)是不同形状,故意不并:
        #   - updated_at=None —— 旧版本无独立更新时间(不存 per-version 时间戳);套当前版本的会误导
        #   - 不带 has_blob —— blob artifact 不可变单版、永无历史,走到这必是文本(has_blob 恒 False)
        #   - memory 可能为 None(当前行已不在、历史 content 仍可取的边界)→ 字段走兜底,helper 给不了
        content = await self._run_with_repo(
            lambda repo: repo.get_version_content(session_id, artifact_id, version)
        )
        if content is None:
            return None
        return {
            "id": artifact_id,
            "content_type": memory.content_type if memory else "unknown",
            "title": memory.title if memory else "Unknown",
            "content": content,
            "version": version,
            "source": memory.source if memory else "agent",
            "created_at": memory.created_at.isoformat() if memory else None,
            "updated_at": None,
        }

    async def get_blob(
        self,
        session_id: str,
        artifact_id: str,
    ) -> Optional[Dict[str, Any]]:
        """读取 artifact 的二进制原始字节 + 取字节所需的元数据。raw-fetch(REST)专用。

        返回 None = artifact 不存在或无 blob(纯文本 artifact)。元数据走 get_artifact
        (REST 路径自带空 WorkingSet → 落 DB),字节走 repo.get_blob(显式 SELECT,不
        碰 `Artifact.blob` 关系)。

        raw 服务的 MIME 即 artifact 自身 `content_type` —— XOR 下 blob artifact 的
        content_type 就是原件真实 MIME。
        """
        memory = await self.get_artifact(session_id, artifact_id)
        if not memory:
            return None
        meta = memory.metadata or {}
        # 字节来源二选一:① 本轮 staged 但**未 flush** 的上传 → 在 WorkingSet memory.blob
        # (用户传图即问的常见场景:upload→read 同一 turn,DB 里此时还没有行);② 否则
        # 查 DB(往轮已 flush / REST 路径空 WorkingSet)。两条都没有 → None(纯文本 artifact)。
        staged = getattr(memory, "blob", None)
        if staged is not None:
            data, size = staged, len(staged)
        else:
            # 短 session 读字节(B-5):工具路径(sandbox/artifact 投递)也走此法,不再骑
            # turn-long session。在回调内取列(data/size_bytes),不漏 ORM 行到 session 外。
            async def _load_blob(repo):
                row = await repo.get_blob(session_id, artifact_id)
                if row is None:
                    return None
                return row.data, row.size_bytes

            loaded = await self._run_with_repo(_load_blob)
            if loaded is None:
                return None
            data, size = loaded
        return {
            "data": data,
            "size_bytes": size,
            "content_type": memory.content_type,  # XOR:即原件真实 MIME
            "filename": meta.get("original_filename") or memory.id,
        }

    # ========================================
    # 更新 / 重写
    # ========================================

    @staticmethod
    def _binary_immutable_error(memory: ArtifactMemory) -> Optional[str]:
        """blob 类 artifact(图片/docx/pdf 等)= 不可变单版,文本编辑一律拒。

        否则 update/rewrite 会在二进制 artifact 上长出一份文本 content —— 与不可变
        blob 形成"哪份权威"的双轨(C-0 刚删掉的状态借编辑工具还魂)。改 = 产新
        artifact(沙盒 persist / create_artifact),源永不变。
        """
        if memory.has_blob:
            return (
                f"Artifact '{memory.id}' is a binary file and is immutable. "
                "Create a new artifact for derived content instead of editing it."
            )
        return None

    async def update_artifact(
        self,
        session_id: str,
        artifact_id: str,
        old_str: str,
        new_str: str,
    ) -> Tuple[bool, str, Optional[Dict]]:
        """更新 Artifact 内容(只改 WorkingSet,标记 dirty)。

        third-tuple semantics:
        * on success → dict with ``match_type`` / ``similarity`` / ...
          (plus ``fuzzy_stats`` when Layer 2 ran)
        * on failure → ``None`` for "not found", or ``{"fuzzy_stats": ...}`` when
          Layer 2 bailed(供工具层经 ``ToolResult.metadata`` surface 可观测)。
        """
        # 局部 import 避免与 update_artifact.py 的类型引用形成循环。
        from tools.builtin.update_artifact import compute_update

        memory = await self.get_artifact(session_id, artifact_id)
        if not memory:
            return False, f"Artifact '{artifact_id}' not found", None

        blocked = self._binary_immutable_error(memory)
        if blocked:
            return False, blocked, None

        info = compute_update(memory.content, old_str, new_str)

        if not info.success:
            failure_meta = (
                {"fuzzy_stats": info.fuzzy_stats} if info.fuzzy_stats else None
            )
            return False, info.message, failure_meta

        memory.content = info.new_content
        memory.current_version += 1
        memory.updated_at = utc_now()
        memory.source = "agent"

        self._ws.mark_dirty(session_id, artifact_id)

        # 仅当前端已有 base(本 turn 发过该 id 的整文)且命中 span 可用时,发权威
        # span delta(取自 compute_update,前端无法从 params 反推模糊命中位置);否则
        # (首次触碰本 turn 的旧 artifact / span 缺失)发整文,确保前端先有 base。
        has_base = artifact_id in self._emitted_base
        if has_base and info.offset is not None and info.deleted_len is not None:
            update_payload: Dict[str, Any] = {
                "delta": {
                    "offset": info.offset,
                    "deleted_len": info.deleted_len,
                    "inserted_text": new_str,
                },
            }
        else:
            update_payload = self._content_payload(memory.content)
            self._note_base(artifact_id, update_payload)
        await self._emit_artifact(_evt_artifact_updated(), {
            "id": artifact_id,
            "current_version": memory.current_version,
            **update_payload,
        })

        match_info: Dict[str, Any] = {
            "match_type": info.match_type,
            "similarity": info.similarity,
            "changes": info.changes,
        }
        if info.expected_text is not None:
            match_info["expected_text"] = info.expected_text
        if info.matched_text is not None:
            match_info["matched_text"] = info.matched_text
        if info.fuzzy_stats is not None:
            match_info["fuzzy_stats"] = info.fuzzy_stats

        return (
            True,
            f"Successfully updated artifact '{artifact_id}' (v{memory.current_version})",
            match_info,
        )

    async def rewrite_artifact(
        self,
        session_id: str,
        artifact_id: str,
        new_content: str,
    ) -> Tuple[bool, str]:
        """完全重写 Artifact 内容(只改 WorkingSet,标记 dirty)。"""
        memory = await self.get_artifact(session_id, artifact_id)
        if not memory:
            return False, f"Artifact '{artifact_id}' not found"

        blocked = self._binary_immutable_error(memory)
        if blocked:
            return False, blocked

        memory.content = new_content
        memory.current_version += 1
        memory.updated_at = utc_now()
        memory.source = "agent"

        self._ws.mark_dirty(session_id, artifact_id)

        # rewrite = 整文替换:发整文(无 span delta),刷新 base。
        rewrite_payload = self._content_payload(new_content)
        await self._emit_artifact(_evt_artifact_updated(), {
            "id": artifact_id,
            "current_version": memory.current_version,
            **rewrite_payload,
        })
        self._note_base(artifact_id, rewrite_payload)

        return True, f"Successfully rewritten artifact '{artifact_id}' (v{memory.current_version})"

    def discard_staged(self, session_id: str, artifact_id: str) -> None:
        """摘掉一个本轮 stage 的 artifact 的 dirty/new 标记,使其不会被 flush_all 落库。

        给上传 staging 的原子回滚用:多文件上传中途失败时,engine 调它把已 stage 的
        文件撤销,保证本轮"要么全落库、要么一个都不落",避免用户重试时撞 _N 副本
        (缓存条目保留无妨——本轮已 abort,不会再被读)。
        """
        self._ws.clear_one(session_id, artifact_id)

    # ========================================
    # 持久化(flush)
    # ========================================

    async def flush_all(self, session_id: str, *, db_manager=None) -> None:
        """将所有 dirty artifacts 持久化到数据库。

        Write-back 语义:执行期间 create/update/rewrite 只改 WorkingSet,flush_all
        在 engine loop 结束后统一持久化。同一轮执行内的多次编辑折叠为一个最终快照——
        DB 只产生一条版本记录,版本号取内存中的 current_version。这意味着
        ArtifactVersion 表的版本号可以是稀疏的(例如 v1 → v3),中间状态不可恢复,
        这是预期行为。

        - 新建的 artifact → repo.create_artifact(target_version=memory.current_version)
        - 已有的 artifact → repo.upsert_artifact_content(target_version=memory.current_version)

        db_manager 提供时,每个 artifact flush 使用 fresh session + retry(对 DB 瞬时
        失败有韧性)。只清除 flush 成功的条目。任一失败则 raise,由调用方决定终态。
        """
        if not self._ws.has_dirty():
            return

        to_flush = self._ws.dirty_keys(session_id)
        failed: list = []

        for sid, aid in to_flush:
            memory = self._ws.peek(sid, aid)
            if not memory:
                continue
            try:
                await self._flush_one(sid, aid, memory, db_manager=db_manager)
                self._ws.clear_one(sid, aid)
                logger.info(f"Flushed artifact '{aid}' in session '{sid}'")
            except Exception as e:
                logger.exception(f"Failed to flush artifact '{aid}': {e}")
                failed.append((aid, e))

        if failed:
            ids = ", ".join(aid for aid, _ in failed)
            raise RuntimeError(f"Failed to flush artifacts: {ids}")

    async def _flush_one(self, sid: str, aid: str, memory, *, db_manager=None) -> None:
        """Flush 单个 dirty artifact。db_manager 提供时用 fresh session + retry。"""
        is_new = self._ws.is_new(sid, aid)

        async def _write(repo):
            if is_new:
                # 新建时连同暂存的二进制源一并写(同一事务,原子)。blob=None 即纯文本/
                # 模型自建,不写 ArtifactBlob 行。
                await repo.create_artifact(
                    session_id=sid, artifact_id=aid,
                    content_type=memory.content_type, title=memory.title,
                    content=memory.content, metadata=memory.metadata,
                    source=memory.source, target_version=memory.current_version,
                    blob=getattr(memory, "blob", None),
                )
            else:
                await repo.upsert_artifact_content(
                    session_id=sid, artifact_id=aid,
                    new_content=memory.content, update_type="update",
                    source=memory.source, target_version=memory.current_version,
                )

        if db_manager:
            async def _attempt(session):
                try:
                    await _write(ArtifactRepository(session))
                except (DuplicateError, IntegrityError):
                    # 前一次 retry 已提交 — 视作成功
                    logger.info(f"Artifact '{aid}' already persisted (duplicate), skipping")

            await db_manager.with_retry(_attempt)
        else:
            await _write(self._ensure_repository())

    # ========================================
    # 版本 / 列表
    # ========================================

    async def get_version(self, session_id: str, artifact_id: str, version: int):
        """获取指定版本(ORM 对象)。"""
        repo = self._ensure_repository()
        return await repo.get_version(session_id, artifact_id, version)

    async def list_versions(self, session_id: str, artifact_id: str):
        """列出 Artifact 的所有版本(ORM 对象列表)。"""
        repo = self._ensure_repository()
        return await repo.list_versions(session_id, artifact_id)

    async def list_artifacts(
        self,
        session_id: str,
        content_type: Optional[str] = None,
        include_content: bool = True,
    ) -> List[Dict[str, Any]]:
        """列出 Session 的所有 Artifacts(序列化后的 dict)。

        合并 DB 结果与 WorkingSet 里的 dirty/new artifact,使引擎 context 装配看到
        同轮改动。REST 侧 WorkingSet 恒空,故等价纯 DB 列表。

        DB 读 + 序列化全在短 session 回调内完成(B-5):`art.*` 在 session 内即 序列化成
        纯 dict,ORM 行不出回调;WorkingSet 合并是内存操作,放回调内一并完成无妨。
        """
        async def _build(repo) -> List[Dict[str, Any]]:
            db_artifacts = await repo.list_artifacts(
                session_id=session_id,
                content_type=content_type,
            )

            seen_ids: set = set()
            result: List[Dict[str, Any]] = []
            for art in db_artifacts:
                memory = self._ws.peek(session_id, art.id)
                if memory and self._ws.is_dirty(session_id, art.id):
                    if content_type and memory.content_type != content_type:
                        continue
                    info = self._serialize_memory(memory, include_content)
                else:
                    info = {
                        "id": art.id,
                        "content_type": art.content_type,
                        "title": art.title,
                        "version": art.current_version,
                        "source": art.source,
                        "original_filename": (art.metadata_ or {}).get("original_filename"),
                        "has_blob": art.has_blob,
                        "created_at": art.created_at.isoformat(),
                        "updated_at": art.updated_at.isoformat(),
                    }
                    if include_content:
                        info["content"] = art.content
                result.append(info)
                seen_ids.add(art.id)

            # 追加 WorkingSet 里尚未落 DB 的 new artifact
            for sid, aid in self._ws.new_keys(session_id):
                if aid in seen_ids:
                    continue
                memory = self._ws.peek(sid, aid)
                if not memory:
                    continue
                if content_type and memory.content_type != content_type:
                    continue
                result.append(self._serialize_memory(memory, include_content))

            return result

        return await self._run_with_repo(_build)

    @staticmethod
    def _serialize_memory(memory: ArtifactMemory, include_content: bool) -> Dict[str, Any]:
        info: Dict[str, Any] = {
            "id": memory.id,
            "content_type": memory.content_type,
            "title": memory.title,
            "version": memory.current_version,
            "source": memory.source,
            "original_filename": (memory.metadata or {}).get("original_filename"),
            "has_blob": memory.has_blob,
            "created_at": memory.created_at.isoformat(),
            "updated_at": memory.updated_at.isoformat(),
        }
        if include_content:
            info["content"] = memory.content
        return info
