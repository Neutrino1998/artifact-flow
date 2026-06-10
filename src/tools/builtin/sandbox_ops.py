"""
沙盒工具(模型面)— C 阶段

三个分立动词(bash / mount / persist)共享一个 per-turn SandboxSession
(拍定 2026-06-03:分立参数面更小、对小模型更可读;共享 session 是实现层事实)。
lazy 创建 key 在「首个沙盒工具调用」—— mount 也会起容器(模型可能先 mount 再 bash)。

工厂 create_sandbox_tools 由 controller_factory 按请求调用(同
create_artifact_tools idiom),session / artifact_service 构造注入。

staging 走宿主直写直读(mount 写 / persist 读 session.workspace_dir),不走
docker cp/exec —— C′ 锁定 loop 池子方案时保住的机制(tmpfs 方案会逼 staging
改 exec+tar 流)。读写两侧都做 realpath 圈地 + O_NOFOLLOW:容器内代码(含
bash 留下的后台进程)能在工作区造任意 symlink,宿主侧跟链会读/写池外文件。
"""

import asyncio
import errno
import mimetypes
import os
import stat
from typing import List, Optional, Tuple

from config import config
from tools.base import BaseTool, ToolParameter, ToolPermission, ToolResult
from tools.builtin.artifact_service import ArtifactService
from tools.builtin.sandbox_session import (
    SandboxError,
    SandboxSession,
    WORKSPACE_MOUNT,
)
from utils.doc_converter import EXTENSION_MIME_MAP
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


# ----------------------------------------------------------------------
# 工作区路径解析 —— 逐级 openat,关掉父目录 TOCTOU
# ----------------------------------------------------------------------
#
# 单次 open(abs_path, O_NOFOLLOW) 只保护**最终组件**:内核逐组件解析路径,
# O_NOFOLLOW 仅拒末端是 symlink,中间目录是 symlink 照样跟过去。容器里的后台
# 进程(前一条 bash 留下的循环)能在"校验通过后、宿主打开前"把已验证的父目录
# 换成指向池外的 symlink → 宿主跟链读/写池外文件(persist = 宿主文件外流成
# artifact;mount = 写穿池外)。realpath 圈地也救不了:它按名字解析,同样有窗口。
#
# 修法 = 逐级 openat,每级目录 O_DIRECTORY|O_NOFOLLOW 持 fd。**fd 钉 inode 不
# 钉名字**:持有期间容器再怎么 rename/换链,fd 指向的还是原来那个真目录。整条
# 路径没有任何"按名字重新解析"的窗口。(Linux 5.6+ 的 openat2 RESOLVE_BENEATH
# 可一发解决,但 dev mac 无;逐级 openat 是可移植等价物。)


class _WorkspaceEscape(Exception):
    """路径逃出工作区:词法 .|.. / 中间组件是 symlink / 叶子是 symlink /
    叶子非普通文件。统一按"非法工作区路径"对模型报错。"""


class _FileTooLarge(Exception):
    def __init__(self, size: int):
        self.size = size


def _walk_to_parent(workspace_dir: str, rel: str) -> Tuple[List[int], str]:
    """逐级 openat 走到 rel 的父目录;返回 (open_fds, leaf_name)。

    open_fds[-1] 是父目录 fd,调用方在 finally 里把列表全 close。词法先拒
    空 / .|..,中间 symlink 由 O_NOFOLLOW 拒(ELOOP)。
    """
    parts = [p for p in rel.split("/") if p]
    if not parts or any(p in (".", "..") for p in parts):
        raise _WorkspaceEscape(rel)
    # base 也 NOFOLLOW:容器够不着 scratch 根(只 bind 了 workspace/ 内部),
    # 但 base 换链的兜底没成本。
    fds = [os.open(workspace_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)]
    try:
        for comp in parts[:-1]:
            try:
                fds.append(
                    os.open(
                        comp,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=fds[-1],
                    )
                )
            except OSError as e:
                # 中间组件是 symlink(ELOOP)或被换成非目录(ENOTDIR)= 逃逸尝试,
                # 报清晰的"非法工作区路径"而非泛化 IO 失败。其余 OSError(权限等)透传。
                if e.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise _WorkspaceEscape(rel) from e
                raise
    except BaseException:
        for fd in fds:
            os.close(fd)
        raise
    return fds, parts[-1]


def _write_file_under(workspace_dir: str, rel: str, data: bytes) -> None:
    """逐级 openat 写(同步,调用方 to_thread)。先摘旧叶子(摘掉容器可能植的
    symlink)再 O_CREAT|O_NOFOLLOW 新建;fchmod 绕 umask 授 0o666。

    fchmod 必要:os.open 的 mode 是**请求值**,内核实际授 `mode & ~umask` ——
    backend 以 umask 077 跑时文件会落 0600、容器内 uid 1000 读不了(mount 报成功、
    后续 bash 才 permission denied)。fchmod 是显式改权限、不经 umask。
    """
    fds, leaf = _walk_to_parent(workspace_dir, rel)
    try:
        parent = fds[-1]
        try:
            os.unlink(leaf, dir_fd=parent)
        except FileNotFoundError:
            pass
        fd = os.open(
            leaf,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
            0o666,
            dir_fd=parent,
        )
        try:
            os.fchmod(fd, 0o666)
            os.write(fd, data)
        finally:
            os.close(fd)
    finally:
        for fd in fds:
            os.close(fd)


def _read_file_under(workspace_dir: str, rel: str, max_bytes: int) -> bytes:
    """逐级 openat 读(同步,调用方 to_thread)。fstat 在 race-free 的 fd 上做
    类型/大小判别 —— 目录/缺失/超大都从这一次 fstat 出,无独立 lstat 解析窗口。

    叶子 symlink → ELOOP → _WorkspaceEscape;目录 → IsADirectoryError;
    缺失 → FileNotFoundError;非普通文件 → _WorkspaceEscape;超 max_bytes →
    _FileTooLarge(读前拒,不把大文件物化进内存)。
    """
    fds, leaf = _walk_to_parent(workspace_dir, rel)
    try:
        try:
            fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fds[-1])
        except OSError as e:
            if e.errno == errno.ELOOP:
                raise _WorkspaceEscape(rel) from e
            raise
        try:
            st = os.fstat(fd)
            if stat.S_ISDIR(st.st_mode):
                raise IsADirectoryError(rel)
            if not stat.S_ISREG(st.st_mode):
                raise _WorkspaceEscape(rel)
            if st.st_size > max_bytes:
                raise _FileTooLarge(st.st_size)
            chunks: List[bytes] = []
            while True:
                block = os.read(fd, 1 << 20)
                if not block:
                    break
                chunks.append(block)
            return b"".join(chunks)
        finally:
            os.close(fd)
    finally:
        for fd in fds:
            os.close(fd)


class BashTool(BaseTool):
    """在 per-turn 沙盒容器内执行 bash 命令。

    - CONFIRM 权限:跑不可信(模型生成)代码。
    - 唯一参数 command —— 超时/配额/输出帽全是隐藏常量(参数面最小化)。
    - 命令退出码非零不算工具失败(grep 无命中 exit 1 是信息不是故障):
      success=True + 输出里带 exit code,让模型自己解读。success=False 只留给
      基建故障(容器起不来 / exec 通道卡死)。
    - 输出溢出分两层:session 侧 SANDBOX_MAX_OUTPUT_CHARS 硬帽(防内存放大,
      显式截断标记);>max_result_size_chars(50k)的部分由引擎溢出转 artifact
      idiom 接手,引擎零改动。
    """

    def __init__(self, session: SandboxSession):
        # TODO(C-wire): 描述补环境能力清单(python 科学栈/pandoc/ripgrep/git 版本)
        # —— 一次性事实进描述,场景 how-to 留 skill 系统。
        super().__init__(
            name="bash",
            description=(
                "Run a bash command inside this conversation's sandboxed Linux container. "
                "The sandbox has NO network access. Preinstalled: Python 3.11 with a "
                "scientific stack (numpy/pandas/matplotlib/openpyxl), pandoc, ripgrep. "
                f"The working directory {WORKSPACE_MOUNT} persists across bash calls "
                "within the current turn and is discarded when the turn ends. "
                f"Each command is killed after {config.SANDBOX_COMMAND_TIMEOUT}s."
            ),
            permission=ToolPermission.CONFIRM,
        )
        self._session = session

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="command",
                type="string",
                description="The bash command to run (executed via `bash -c`).",
                required=True,
            ),
        ]

    async def execute(self, command: str) -> ToolResult:
        if not command.strip():
            return ToolResult(success=False, error="Parameter 'command' must not be empty.")

        try:
            result = await self._session.exec(command)
        except SandboxError as e:
            # session 侧已按错误类型记过 ops 日志,这里只回模型面文案
            return ToolResult(success=False, error=str(e))

        lines = [result.output.rstrip("\n") if result.output.strip() else "(no output)"]
        if result.truncated:
            lines.append(
                f"[output truncated at {config.SANDBOX_MAX_OUTPUT_CHARS} chars]"
            )
        if result.exit_code != 0:
            note = f"[exit code: {result.exit_code}]"
            # timeout --signal=KILL 强杀 → 128+9;同码也可能是 OOM-kill,按时长归因
            if result.exit_code == 137 and result.duration >= config.SANDBOX_COMMAND_TIMEOUT:
                note = (
                    f"[exit code: 137 — killed by the "
                    f"{config.SANDBOX_COMMAND_TIMEOUT}s command timeout]"
                )
            lines.append(note)

        return ToolResult(
            success=True,
            data="\n".join(lines),
            metadata={
                "exit_code": result.exit_code,
                "duration_sec": round(result.duration, 2),
                "truncated": result.truncated,
            },
        )


class MountArtifactTool(BaseTool):
    """把一个 artifact 物化进沙盒工作区(显式 stage-in,原则 4)。

    - 文本 artifact:WorkingSet overlay 的当前内容(本轮 dirty/new 必须可 mount,
      直读 DB 是空的)按 UTF-8 写盘;blob artifact:原始字节(本轮 staged 上传
      经 get_blob 读 ArtifactMemory.blob,其余走 DB)。格式判别 = 有无 blob。
    - on-disk 名 = artifact id(决策 2:id 已是 fs-safe 句柄);重复 mount 同一
      id = 刷新副本(覆写)。
    - 返回纯事实(容器内路径/字节/MIME);"binary 须 mount" 的契约文案归
      inventory/read_artifact(C-wire),场景 how-to 归 skill。
    """

    def __init__(self, session: SandboxSession, service: ArtifactService):
        super().__init__(
            name="mount",
            description=(
                "Copy an artifact into the sandbox workspace as a file at "
                f"{WORKSPACE_MOUNT}/<artifact_id>, so bash commands can operate on it. "
                "Text artifacts are written as UTF-8 (including edits made earlier in "
                "this turn); binary artifacts (docx/pdf/images) are written as their "
                "original bytes. Mounting the same artifact again refreshes the copy."
            ),
            permission=ToolPermission.AUTO,
        )
        self._session = session
        self._service = service

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="artifact_id",
                type="string",
                description="ID of the artifact to copy into the sandbox workspace.",
                required=True,
            ),
        ]

    async def execute(self, artifact_id: str) -> ToolResult:
        artifact_id = artifact_id.strip()
        if not artifact_id:
            return ToolResult(success=False, error="Parameter 'artifact_id' must not be empty.")

        session_id = self._service.current_session_id
        if not session_id:
            return ToolResult(success=False, error="No active session")

        memory = await self._service.get_artifact(session_id, artifact_id)
        if memory is None:
            return ToolResult(
                success=False, error=f"Artifact '{artifact_id}' not found in this session."
            )

        # 字节来源二分(决策:blob-only 后每 artifact 单一权威载体)
        if (memory.metadata or {}).get("blob_content_type"):
            blob_info = await self._service.get_blob(session_id, artifact_id)
            if blob_info is None:
                return ToolResult(
                    success=False,
                    error=f"Artifact '{artifact_id}' has no stored binary content.",
                )
            data, mime = blob_info["data"], blob_info["content_type"]
        else:
            data, mime = memory.content.encode("utf-8"), memory.content_type

        try:
            await self._session.ensure_container()
        except SandboxError as e:
            return ToolResult(success=False, error=str(e))

        # id 模式([\w\-.]{1,64})无路径分隔符,叶子永远落 workspace 顶层 ——
        # 逐级 openat 退化为单级,父目录就是 workspace 本体(容器够不着、换不了)。
        # unlink + O_NOFOLLOW 新建:容器若在叶子位置植了 symlink,链本体被摘除、
        # 绝不跟链写池外。词法 .|.. 由 _walk_to_parent 拒。
        try:
            await asyncio.to_thread(
                _write_file_under, self._session.workspace_dir, artifact_id, data
            )
        except _WorkspaceEscape:
            return ToolResult(
                success=False,
                error=f"Artifact id '{artifact_id}' does not map to a valid workspace filename.",
            )
        except OSError as e:
            logger.error(
                f"Sandbox mount write failed for '{artifact_id}' "
                f"(msg={self._session.message_id}): {e}"
            )
            return ToolResult(
                success=False, error=f"Failed to write '{artifact_id}' into the workspace."
            )

        container_path = f"{WORKSPACE_MOUNT}/{artifact_id}"
        return ToolResult(
            success=True,
            data=f"Mounted artifact '{artifact_id}' at {container_path} ({len(data)} bytes, {mime}).",
            metadata={"path": container_path, "bytes": len(data), "content_type": mime},
        )


class PersistFileTool(BaseTool):
    """把工作区文件回写成**新 artifact**(显式 stage-out,原则 4)。

    - 永远产新 artifact(同名 `_N` dedup;blob 不版本化、不覆写 —— 二进制
      契约 = 不可变单版,文本 = 可编辑版本化)。
    - persist 落回来就是一次普通 artifact 写:进 WorkingSet,随 turn 末
      flush_all 落库,与 create_artifact 同路。
    - 文本/二进制二分:可严格 UTF-8 解码且 ≤ SANDBOX_PERSIST_MAX_TEXT_BYTES
      → 文本 artifact;否则 blob(MIME 按扩展名猜,兜底 octet-stream)。
    """

    def __init__(self, session: SandboxSession, service: ArtifactService):
        super().__init__(
            name="persist",
            description=(
                "Save a file from the sandbox workspace as a NEW artifact. The sandbox "
                "workspace is discarded when the turn ends — persist is the only way to "
                "keep results. Text files become editable text artifacts; binary files "
                "(docx/xlsx/images/archives...) become artifacts the user can download. "
                "Always creates a new artifact; an existing id gets a numeric suffix."
            ),
            permission=ToolPermission.AUTO,
        )
        self._session = session
        self._service = service

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="path",
                type="string",
                description=(
                    f"Path of the file to save, relative to {WORKSPACE_MOUNT} "
                    "(e.g. 'report.docx' or 'out/plot.png')."
                ),
                required=True,
            ),
        ]

    @staticmethod
    def _classify(filename: str, data: bytes) -> Tuple[Optional[str], str]:
        """(text_content, mime):text_content=None 表示按 blob 存。"""
        if len(data) <= config.SANDBOX_PERSIST_MAX_TEXT_BYTES:
            try:
                text = data.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                text = None
            if text is not None:
                ext = os.path.splitext(filename)[1].lower()
                return text, EXTENSION_MIME_MAP.get(ext, "text/plain")
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        return None, mime

    async def execute(self, path: str) -> ToolResult:
        raw_path = path.strip()
        if not raw_path:
            return ToolResult(success=False, error="Parameter 'path' must not be empty.")

        session_id = self._service.current_session_id
        if not session_id:
            return ToolResult(success=False, error="No active session")

        # sticky 优先于 "nothing to persist":超额杀 / 容器中途死后 _container 已置
        # None(started=False),若先撞 not-started 会把配额失败吞成"没用过沙盒",
        # 与 bash/mount 的 sticky 复述不一致(P3)。配额杀的契约 = 本 turn 沙盒不可用,
        # 不开"抢救残留产物"通道(超额现场文件完整性不可信、等于给超额留后门)。
        sticky = self._session.sticky_failure
        if sticky is not None:
            return ToolResult(success=False, error=sticky)

        if not self._session.started:
            return ToolResult(
                success=False,
                error="The sandbox has not been used this turn; there is nothing to persist.",
            )

        rel = raw_path
        if rel.startswith("/"):
            if rel == WORKSPACE_MOUNT or rel.startswith(WORKSPACE_MOUNT + "/"):
                rel = rel[len(WORKSPACE_MOUNT):].lstrip("/")
            else:
                return ToolResult(
                    success=False,
                    error=f"Only files under {WORKSPACE_MOUNT} can be persisted.",
                )
        if not rel:
            return ToolResult(success=False, error="Parameter 'path' must name a file.")

        # 逐级 openat 读:逃逸 / 缺失 / 目录 / 超大都从 race-free 的 fstat 出
        try:
            data = await asyncio.to_thread(
                _read_file_under,
                self._session.workspace_dir,
                rel,
                config.ARTIFACT_BLOB_MAX_BYTES,
            )
        except _WorkspaceEscape:
            return ToolResult(
                success=False, error=f"Path '{raw_path}' escapes the sandbox workspace."
            )
        except FileNotFoundError:
            return ToolResult(
                success=False, error=f"File '{raw_path}' not found in the workspace."
            )
        except IsADirectoryError:
            return ToolResult(
                success=False,
                error=(
                    f"'{raw_path}' is a directory. Archive it first via bash "
                    "(e.g. `zip -r out.zip <dir>`) and persist the archive."
                ),
            )
        except _FileTooLarge as e:
            max_mb = config.ARTIFACT_BLOB_MAX_BYTES / 1024 / 1024
            return ToolResult(
                success=False,
                error=(
                    f"File too large to persist: {e.size / 1024 / 1024:.1f}MB "
                    f"(max {max_mb:.0f}MB)"
                ),
            )
        except OSError as e:
            logger.error(
                f"Sandbox persist read failed for '{raw_path}' "
                f"(msg={self._session.message_id}): {e}"
            )
            return ToolResult(
                success=False, error=f"Failed to read '{raw_path}' from the workspace."
            )

        filename = os.path.basename(rel)
        text, mime = self._classify(filename, data)
        if text is not None:
            success, message, info = await self._service.create_from_upload(
                session_id=session_id,
                filename=filename,
                content=text,
                content_type=mime,
                source="sandbox",
            )
        else:
            # C-0 blob-only 约定:无文本表示,content="",content_type=真实 MIME
            success, message, info = await self._service.create_from_upload(
                session_id=session_id,
                filename=filename,
                content="",
                content_type=mime,
                blob=data,
                blob_content_type=mime,
                source="sandbox",
            )
        if not success:
            return ToolResult(success=False, error=message)

        artifact_id = info["id"]
        kind = "editable text artifact" if text is not None else "binary artifact (user-downloadable)"
        return ToolResult(
            success=True,
            data=(
                f"Persisted '{raw_path}' as new artifact '{artifact_id}' "
                f"({len(data)} bytes, {mime}, {kind})."
            ),
            metadata={
                "artifact_id": artifact_id,
                "bytes": len(data),
                "content_type": mime,
                "has_blob": text is None,
            },
        )


def create_sandbox_tools(
    session: SandboxSession, artifact_service: ArtifactService
) -> List[BaseTool]:
    """创建沙盒工具(工厂,按请求调用,同 create_artifact_tools idiom)。"""
    return [
        BashTool(session),
        MountArtifactTool(session, artifact_service),
        PersistFileTool(session, artifact_service),
    ]
