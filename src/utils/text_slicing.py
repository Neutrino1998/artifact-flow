"""
按行 / 字符上限切片文本的工具函数。

read_artifact 用 slice_lines_by_offset_limit 实现 offset+limit 分段读取，
char_cap 优先级高于 limit（防止 limit 行加起来撑爆上下文）。
"""

from typing import Optional, Tuple


def count_lines(s: str) -> int:
    """
    返回字符串的行数。

    - 空串返回 0
    - 末尾有换行不算多一行（"abc\\n" 是 1 行，"abc\\ndef" 也是 2 行）
    - 末尾无换行也照常计数（"abc" 是 1 行）
    """
    if not s:
        return 0
    n = s.count("\n")
    # 末尾不是换行结尾的情况下要 +1（因为最后一行没有换行符但确实是一行）
    if not s.endswith("\n"):
        n += 1
    return n


def slice_lines_by_offset_limit(
    content: str,
    offset: int,
    limit: Optional[int],
    char_cap: int,
) -> Tuple[str, Optional[Tuple[int, int]], str, bool]:
    """
    从 offset 行开始按 limit 行 / char_cap 字符上限切片。

    Args:
        content: 完整文本
        offset: 起始行号（1-indexed）。<=0 容错 clamp 到 1
        limit: 最大行数（None = 读到 char_cap 截止）。0 合法（探测用）
        char_cap: 字符上限。limit 行数对应字符若超过 char_cap，按 char_cap 在
                  最近换行处截断

    Returns:
        (body, shown_lines_or_none, truncated_by, has_more)
        - body: 切片后的文本（保留行内换行符）
        - shown_lines_or_none: (start, end) 1-indexed inclusive；空切片时为 None
        - truncated_by: "none" | "char_limit" | "line_limit"
        - has_more: 是否还有未返回的内容
    """
    # offset clamp
    if offset <= 0:
        offset = 1

    if not content:
        return "", None, "none", False

    lines = content.splitlines(keepends=True)
    total_lines = len(lines)

    # offset 越界 → 空切片
    if offset > total_lines:
        return "", None, "none", False

    start_idx = offset - 1  # 0-indexed
    available = total_lines - start_idx

    # 决定窗口上限（按行数视角）
    if limit is None:
        end_idx_by_limit = total_lines  # 不限行数
        limit_was_binding = False
    else:
        if limit <= 0:
            # limit=0 合法 → 空切片，但 has_more 看后面还有没有
            return "", None, "none", available > 0
        end_idx_by_limit = min(start_idx + limit, total_lines)
        limit_was_binding = (start_idx + limit) < total_lines

    # 应用 char_cap：从 start_idx 开始累加，到加上某行后超过 cap 就在该行之前停
    body_chars = 0
    end_idx_by_cap = start_idx
    cap_was_binding = False
    for i in range(start_idx, end_idx_by_limit):
        line_len = len(lines[i])
        if body_chars + line_len > char_cap:
            cap_was_binding = True
            break
        body_chars += line_len
        end_idx_by_cap = i + 1

    # ─── 边界：单行 > char_cap → 硬截断 + 末尾打标 ─────────────────
    # mega-line（minified JSON / HTML 等）会让按行累加的 cap 检查从未
    # 推进 end_idx_by_cap，普通逻辑会返回空 body 卡死分页。这里硬截断
    # 到 cap 字符 + 加可见标记，让模型知道：
    #   - 该行被截断（body 末尾的 marker）
    #   - 截断后的 tail 无法通过 offset 续读（marker 文案明示）
    # 接受 tail 数据丢失换 context 有界 + 0 新 API 面。
    body_override: Optional[str] = None
    if end_idx_by_cap == start_idx and end_idx_by_limit > start_idx:
        original_line = lines[start_idx]
        head = original_line[:char_cap]
        marker = (
            f"\n[... line truncated at {char_cap} chars "
            f"(original {len(original_line)}); "
            f"remainder not retrievable via pagination ...]"
        )
        body_override = head + marker
        end_idx_by_cap = start_idx + 1
        cap_was_binding = True  # 真的截断了，metadata 这次诚实

    end_idx = end_idx_by_cap  # 0-indexed exclusive

    if body_override is not None:
        body = body_override
    else:
        body = "".join(lines[start_idx:end_idx])
    shown_lines = (start_idx + 1, end_idx)  # 1-indexed inclusive
    has_more = end_idx < total_lines  # 行级判断；within-line tail 不算 more

    if cap_was_binding:
        truncated_by = "char_limit"
    elif limit_was_binding:
        truncated_by = "line_limit"
    else:
        truncated_by = "none"

    return body, shown_lines, truncated_by, has_more
