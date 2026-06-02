"""Artifact 工具集(create / rewrite / read)。

历史上本文件是 artifact 大杂烩(含 ``ArtifactManager`` god-object)。重构后职责拆分:
- 纯状态(cache + dirty/new)→ ``tools/builtin/artifact_working_set.py``
- 编排 + 持久 + 发事件 → ``tools/builtin/artifact_service.py``
- 纯算法:``grep_artifact.py`` / ``update_artifact.py``
- 本文件:只剩**工具类**(模型可调用的 artifact 操作)+ 工厂函数。

工具通过依赖注入持有一个 ``ArtifactService`` 句柄(``set_service`` / 构造注入),
``current_session_id`` 由 Service 委托给其 WorkingSet。
"""

import math
from typing import List, Optional

from config import config
from tools.artifact_envelope import ArtifactSlice, render_artifact_slice
from tools.base import BaseTool, ToolResult, ToolParameter, ToolPermission
from tools.builtin.artifact_service import ArtifactService
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
                "the offset hint to read the next slice."
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
