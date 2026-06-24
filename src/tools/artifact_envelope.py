"""
Artifact envelope renderer.

公共渲染器，被以下三处调用方共享：
- core/context_manager.py — inventory 注入
- tools/builtin/artifact_ops.py:ReadArtifactTool — read_artifact 返回
- core/engine.py — 工具结果落盘后的预览回填

设计要点：
- attribute 经 _attr 转义 &/"/<（多数是受控值，防御性）
- <title> 经 _text 转义 &/<（来源可含不可信文件名，且不参与匹配，可安全 escape）
- body 不转义、原文输出 — 模型用 update_artifact(old_string=...) 时会用 read 出的
  内容作匹配源，escape 会破坏匹配（body 含分隔符是已接受的限制）
- 此 slice 是给模型看的展示文本、非被解析的 XML，故不追求整体严格良构
- 可空 attribute（如 total_lines、shown_lines、hint）省略输出
"""

from dataclasses import dataclass
from typing import Optional, Tuple


_VALID_TRUNCATED_BY = {"none", "preview", "char_limit", "line_limit"}


def _attr(value) -> str:
    """
    Defensive attribute-value escape: `&`/`"`/`<` → 实体,产出始终是良构 XML。

    上游绝大多数 attribute 值是受控的（数字/枚举/sanitized id/ISO timestamp,天然
    不含这些字符,故此层对它们零影响）。但 content_type 等字段曾经/可能携带不可信
    来源（如远端 Content-Type 头),一旦含 `&`/`<` 就会让 envelope 非良构、甚至让
    `&quot;` 之外的边界错位。故 belt-and-suspenders 收口到渲染器单点:转义全部三个
    在 attribute value 里有结构意义的字符（`&` 必须最先,避免二次转义自己引入的实体）。
    """
    return (
        str(value)
        .replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
    )


def _text(value) -> str:
    """元素内容转义(`&`、`<`)。

    给 `<title>` 用:title 来源含不可信值(web_fetch 把 URL path `unquote` 成
    filename、上传用用户文件名),`x</title><i>` 会让 slice 结构错位、污染模型读取
    的 metadata 区。**注意 body 不走这里**——body 必须原文输出(模型用 read 出的正文
    作 update_artifact 的 old_string 匹配源,escape 了就匹配不上),body 含分隔符是
    已接受的限制。本 slice 是给模型看的展示文本、非被解析的 XML,故只收口能收口的
    title(它不参与匹配),不追求整体严格良构。
    """
    return str(value).replace("&", "&amp;").replace("<", "&lt;")


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
    """渲染 ArtifactSlice 为 XML 字符串（给模型看，不参与机器解析）。

    Body 原文输出（不转义）—— update_artifact 用 read 出的内容作 old_string 匹配，
    escape 会破坏匹配。<title> 过 _text() 转义 &/<（不参与匹配、来源含不可信文件名）。
    Attribute 过 _attr() 转义 &/"/<。
    """
    attrs = [
        f'id="{_attr(slice.id)}"',
        f'version="{slice.version}"',
        f'type="{_attr(slice.content_type)}"',
        f'source="{_attr(slice.source)}"',
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
        attrs.append(f'updated_at="{_attr(slice.updated_at)}"')
    if slice.hint is not None:
        attrs.append(f'hint="{_attr(slice.hint)}"')

    return (
        f"<artifact_slice {' '.join(attrs)}>\n"
        f"<title>{_text(slice.title)}</title>\n"
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
