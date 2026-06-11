"""Artifact 工具集(create / rewrite / read)。

历史上本文件是 artifact 大杂烩(含 ``ArtifactManager`` god-object)。重构后职责拆分:
- 纯状态(cache + dirty/new)→ ``tools/builtin/artifact_working_set.py``
- 编排 + 持久 + 发事件 → ``tools/builtin/artifact_service.py``
- 纯算法:``grep_artifact.py`` / ``update_artifact.py``
- 本文件:只剩**工具类**(模型可调用的 artifact 操作)+ 工厂函数。

工具通过依赖注入持有一个 ``ArtifactService`` 句柄(``set_service`` / 构造注入),
``current_session_id`` 由 Service 委托给其 WorkingSet。
"""

import asyncio
import math
from typing import List, Optional

from config import config
from tools.artifact_envelope import ArtifactSlice, render_artifact_slice
from tools.base import BaseTool, ToolResult, ToolParameter, ToolPermission
from tools.builtin.artifact_service import ArtifactService
from utils.image import resize_to_vision_data_uri
from utils.logger import get_logger
from utils.text_slicing import count_lines, slice_lines_by_offset_limit

logger = get_logger("ArtifactFlow")


# ============================================================
# 工具类
# ============================================================

class CreateArtifactTool(BaseTool):
    """创建 Artifact 工具"""

    def __init__(self, service: Optional[ArtifactService] = None):
        super().__init__(
            name="create_artifact",
            description="Create a new artifact. Check existing artifacts first to avoid duplicates.",
            permission=ToolPermission.AUTO
        )
        self._service = service

    def set_service(self, service: ArtifactService) -> None:
        """设置 ArtifactService(依赖注入)"""
        self._service = service

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="id",
                type="string",
                description="Unique identifier (e.g., 'task_plan', 'research_report')",
                required=True
            ),
            ToolParameter(
                name="content_type",
                type="string",
                description="MIME type of the artifact content",
                required=False,
                default="text/markdown",
                enum=["text/markdown", "text/plain", "text/x-python", "text/html", "application/json", "text/javascript", "text/yaml"]
            ),
            ToolParameter(
                name="title",
                type="string",
                description="Title of the artifact",
                required=True
            ),
            ToolParameter(
                name="content",
                type="string",
                description="Initial text content",
                required=True
            )
        ]

    async def execute(self, **params) -> ToolResult:
        if not self._service:
            return ToolResult(success=False, error="ArtifactService not configured")

        session_id = self._service.current_session_id
        if not session_id:
            return ToolResult(success=False, error="No active session")

        success, message = await self._service.create_artifact(
            session_id=session_id,
            artifact_id=params["id"],
            content_type=params["content_type"],  # 默认值已由 _apply_defaults 填充
            title=params["title"],
            content=params["content"]
        )

        if success:
            logger.info(message)
            return ToolResult(
                success=True,
                data=f'<artifact version="1"><id>{params["id"]}</id> {message}</artifact>',
            )
        return ToolResult(success=False, error=message)


class RewriteArtifactTool(BaseTool):
    """重写 Artifact 工具(完全替换内容)"""

    def __init__(self, service: Optional[ArtifactService] = None):
        super().__init__(
            name="rewrite_artifact",
            description="Completely replace artifact content. Use when changes are too extensive for update_artifact.",
            permission=ToolPermission.AUTO
        )
        self._service = service

    def set_service(self, service: ArtifactService) -> None:
        """设置 ArtifactService(依赖注入)"""
        self._service = service

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="id",
                type="string",
                description="Artifact ID to rewrite",
                required=True
            ),
            ToolParameter(
                name="content",
                type="string",
                description="New complete content",
                required=True
            )
        ]

    async def execute(self, **params) -> ToolResult:
        if not self._service:
            return ToolResult(success=False, error="ArtifactService not configured")

        session_id = self._service.current_session_id
        if not session_id:
            return ToolResult(success=False, error="No active session")

        success, message = await self._service.rewrite_artifact(
            session_id=session_id,
            artifact_id=params["id"],
            new_content=params["content"]
        )

        if success:
            logger.info(message)
            memory = await self._service.get_artifact(session_id, params["id"])
            version = memory.current_version if memory else None
            return ToolResult(
                success=True,
                data=f'<artifact version="{version}"><id>{params["id"]}</id> {message}</artifact>',
            )

        return ToolResult(success=False, error=message)


class ReadArtifactTool(BaseTool):
    """读取 Artifact 工具"""

    def __init__(self, service: Optional[ArtifactService] = None):
        super().__init__(
            name="read_artifact",
            description=(
                "Read artifact content with optional line-based pagination. "
                "Returns content wrapped in <artifact_slice> with metadata "
                "(shown_lines/total_lines/has_more). When has_more=true, use "
                "the offset hint to read the next slice. "
                "For an image artifact (content_type image/*, e.g. an uploaded "
                "photo or screenshot), this returns the actual image so you can "
                "see it — read the image artifact whenever you need to view it."
            ),
            permission=ToolPermission.AUTO,
            # Infinity = 永不落盘。read_artifact 自身的输出若被中间件再次落盘，
            # 会形成 Read→artifact→Read 循环。
            max_result_size_chars=math.inf,
        )
        self._service = service

    def set_service(self, service: ArtifactService) -> None:
        """设置 ArtifactService(依赖注入)"""
        self._service = service

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="id",
                type="string",
                description="Artifact ID to read",
                required=True
            ),
            ToolParameter(
                name="version",
                type="integer",
                description="Version number (optional, defaults to latest)",
                required=False,
                default=None
            ),
            ToolParameter(
                name="offset",
                type="integer",
                description="1-indexed start line (omit to read from line 1)",
                required=False,
                default=1,
            ),
            ToolParameter(
                name="limit",
                type="integer",
                description=(
                    "Maximum lines to read (omit to read until built-in size cap). "
                    "Use the offset hint from a prior call to continue reading large artifacts."
                ),
                required=False,
                default=None,
            ),
        ]

    async def execute(self, **params) -> ToolResult:
        if not self._service:
            return ToolResult(success=False, error="ArtifactService not configured")

        session_id = self._service.current_session_id
        if not session_id:
            return ToolResult(success=False, error="No active session")

        result = await self._service.read_artifact(
            session_id=session_id,
            artifact_id=params["id"],
            version=params.get("version")
        )

        if result is None:
            version = params.get("version")
            if version:
                return ToolResult(success=False, error=f"Version {version} not found")
            return ToolResult(success=False, error=f"Artifact '{params['id']}' not found")

        artifact_id = result.get("id", "")
        content_type = result.get("content_type", "")

        # 图片 artifact:走识图路径(非文本分页)。取 blob → 降采样 → 返回携 data-URI
        # 引用的 ToolResult。引擎把 data-URI 摘进本 turn 的 state["vision_blocks"]、事件
        # 只留引用:本轮模型看得到图,下一轮 state 已空 → 占位文本(再 read 即重看)。
        if content_type.startswith("image/"):
            return await self._read_image(session_id, artifact_id, result.get("version", 1))

        # 非图片的 blob-only artifact(docx/pdf 等富格式上传,C-0 起无文本表示):
        # 返回契约文案而非空 content。success=True —— 这是对"它是什么"的准确回答,
        # 不是失败(success=False 易诱发模型重试同一调用)。
        if result.get("blob_content_type"):
            original = result.get("original_filename") or artifact_id
            return ToolResult(
                success=True,
                data=(
                    f"Artifact '{artifact_id}' is a binary file "
                    f"({result['blob_content_type']}, original file '{original}'). "
                    "It has no text representation and cannot be read as text. "
                    "To inspect or convert it, mount it into the sandbox with the "
                    "`mount` tool and process it via bash. The user can also download "
                    "the original file from the artifact panel."
                ),
            )

        title = result.get("title", "")
        version_num = result.get("version", 1)
        source = result.get("source", "agent")
        updated_at = result.get("updated_at", "")
        content = result.get("content", "") or ""

        offset = params.get("offset") or 1
        limit = params.get("limit")  # None = 读到 char_cap
        explicit_version = params.get("version")  # None = latest

        body, shown_lines, truncated_by, has_more = slice_lines_by_offset_limit(
            content,
            offset=offset,
            limit=limit,
            char_cap=config.READ_ARTIFACT_MAX_CHARS,
        )
        total_lines = count_lines(content)

        hint = None
        if has_more and shown_lines is not None:
            # 透传调用者原始的 limit / version：避免续读悄悄换页大小或跳到 latest 版本
            next_offset = shown_lines[1] + 1
            cont_args = [f"id='{artifact_id}'", f"offset={next_offset}"]
            if limit is not None:
                cont_args.append(f"limit={limit}")
            if explicit_version is not None:
                cont_args.append(f"version={explicit_version}")
            hint = f"To continue: read_artifact({', '.join(cont_args)})"

        slice = ArtifactSlice(
            id=artifact_id,
            version=version_num,
            content_type=content_type,
            source=source,
            title=title,
            body=body,
            total_chars=len(content),
            shown_chars=len(body),
            total_lines=total_lines,
            shown_lines=shown_lines,
            truncated_by=truncated_by,
            has_more=has_more,
            hint=hint,
            updated_at=updated_at,
        )
        return ToolResult(success=True, data=render_artifact_slice(slice))

    async def _read_image(self, session_id: str, artifact_id: str, version) -> ToolResult:
        """识图:取 blob → 降采样 → ToolResult(data=文本标记, metadata.image=引用+data_uri)。

        data_uri 放 metadata,引擎转 emit 前会把它摘进 state、事件只留引用(见 engine
        tool_complete 注释)。resize 走 executor(Pillow C 扩展 + 解码,CPU 纪律);失败
        (含解压炸弹)→ loud-fail 给模型,模型据 tool result 改道。
        """
        blob = await self._service.get_blob(session_id, artifact_id)
        if blob is None:
            return ToolResult(
                success=False,
                error=f"Image artifact '{artifact_id}' has no stored image data",
            )
        loop = asyncio.get_running_loop()
        try:
            data_uri = await loop.run_in_executor(
                None, resize_to_vision_data_uri, blob["data"], config.VISION_IMAGE_MAX_EDGE
            )
        except Exception as e:
            logger.warning(f"Failed to prepare image '{artifact_id}' for viewing: {e}")
            return ToolResult(
                success=False,
                error=f"Failed to prepare image '{artifact_id}' for viewing: {e}",
            )
        ct = blob["content_type"]
        return ToolResult(
            success=True,
            data=f"[image artifact '{artifact_id}' v{version}, {ct}]",
            metadata={"image": {
                "artifact_id": artifact_id,
                "version": version,
                "content_type": ct,
                "data_uri": data_uri,
            }},
        )


# ============================================================
# 工厂函数
# ============================================================

def create_artifact_tools(service: ArtifactService) -> List[BaseTool]:
    """创建所有 Artifact 工具(工厂函数)。

    GrepArtifactTool / UpdateArtifactTool 局部 import 以避免与各自模块对
    ``ArtifactService`` 的类型引用形成包级循环。
    """
    from tools.builtin.grep_artifact import GrepArtifactTool
    from tools.builtin.update_artifact import UpdateArtifactTool

    return [
        CreateArtifactTool(service),
        UpdateArtifactTool(service),
        RewriteArtifactTool(service),
        ReadArtifactTool(service),
        GrepArtifactTool(service),
    ]
