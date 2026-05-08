"""
通用输入校验工具。

提供跨 router / schema / 批量导入复用的校验函数。
失败时抛 ValueError（带具体原因），由调用方决定转成 422 / 400。
"""

import re

USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{2,64}$")


def validate_username(name: str) -> None:
    """校验 username 格式。

    规则：长度 2-64，仅允许字母、数字、'.'、'_'、'-'。
    禁止空格和非 ASCII 字符（避免 URL/日志/SSE channel 出现诡异 bug）。
    中文等字符应放在 display_name。

    Raises:
        ValueError: 校验失败，message 为具体原因。
    """
    if not name:
        raise ValueError("Username cannot be empty")
    if " " in name:
        raise ValueError("Username cannot contain spaces")
    if not USERNAME_RE.fullmatch(name):
        raise ValueError(
            "Username must be 2-64 chars of letters, digits, '.', '_' or '-'"
        )
