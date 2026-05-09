"""
Artifact envelope renderer.

公共渲染器，被以下三处调用方共享：
- core/context_manager.py — inventory 注入
- tools/builtin/artifact_ops.py:ReadArtifactTool — read_artifact 返回
- core/engine.py — 工具结果落盘后的预览回填

设计要点：
- attribute 全部是受控值（数字/枚举/artifact id），不需转义
- body 和 <title> 也不转义、原文输出 — 模型用 update_artifact(old_string=...)
  时会用 read 出的内容作匹配源，escape 会破坏匹配
- 可空 attribute（如 total_lines、shown_lines、hint）省略输出
"""

from dataclasses import dataclass
from typing import Optional, Tuple


_VALID_TRUNCATED_BY = {"none", "preview", "char_limit", "line_limit"}


@dataclass
class ArtifactSlice:
    """渲染参数。所有可空字段省略时不出现在 attribute 里。"""

    id: str
    version: int
    content_type: str
    source: str  # "agent" | "user_upload" | "tool"
    title: str
    body: str  # 已截断好的内容（preview 或全文片段）
    total_chars: int
    shown_chars: int
    total_lines: Optional[int] = None
    shown_lines: Optional[Tuple[int, int]] = None  # (start, end), 1-indexed inclusive
    truncated_by: str = "none"  # 见 _VALID_TRUNCATED_BY
    has_more: bool = False
    hint: Optional[str] = None
    updated_at: Optional[str] = None

    def __post_init__(self) -> None:
        if self.truncated_by not in _VALID_TRUNCATED_BY:
            raise ValueError(
                f"Invalid truncated_by: {self.truncated_by!r}. "
                f"Must be one of {sorted(_VALID_TRUNCATED_BY)}"
            )


def render_artifact_slice(slice: ArtifactSlice) -> str:
    """渲染 ArtifactSlice 为 XML 字符串（给模型看，不参与机器解析）。"""
    attrs = [
        f'id="{slice.id}"',
        f'version="{slice.version}"',
        f'type="{slice.content_type}"',
        f'source="{slice.source}"',
        f'total_chars="{slice.total_chars}"',
        f'shown_chars="{slice.shown_chars}"',
    ]
    if slice.total_lines is not None:
        attrs.append(f'total_lines="{slice.total_lines}"')
    if slice.shown_lines is not None:
        start, end = slice.shown_lines
        attrs.append(f'shown_lines="{start}-{end}"')
    attrs.append(f'truncated_by="{slice.truncated_by}"')
    attrs.append(f'has_more="{"true" if slice.has_more else "false"}"')
    if slice.updated_at is not None:
        attrs.append(f'updated_at="{slice.updated_at}"')
    if slice.hint is not None:
        attrs.append(f'hint="{slice.hint}"')

    return (
        f"<artifact_slice {' '.join(attrs)}>\n"
        f"<title>{slice.title}</title>\n"
        f"{slice.body}\n"
        f"</artifact_slice>"
    )


def make_preview_slice(
    artifact_id: str,
    version: int,
    content_type: str,
    source: str,
    title: str,
    full_content: str,
    preview_len: int,
    hint: Optional[str] = None,
    updated_at: Optional[str] = None,
) -> ArtifactSlice:
    """
    构建一个"预览模式"的 ArtifactSlice（inventory + 持久化中间件复用）。

    总是按字符截断到 preview_len，不涉及行数语义（行数在 read_artifact 路径用）。
    """
    total_chars = len(full_content)
    if total_chars <= preview_len:
        body = full_content
        shown_chars = total_chars
        truncated_by = "none"
        has_more = False
    else:
        body = full_content[:preview_len]
        shown_chars = preview_len
        truncated_by = "preview"
        has_more = True

    return ArtifactSlice(
        id=artifact_id,
        version=version,
        content_type=content_type,
        source=source,
        title=title,
        body=body,
        total_chars=total_chars,
        shown_chars=shown_chars,
        truncated_by=truncated_by,
        has_more=has_more,
        hint=hint,
        updated_at=updated_at,
    )
