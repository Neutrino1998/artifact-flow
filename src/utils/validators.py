"""
通用输入校验工具。

提供跨 router / schema / 批量导入复用的校验函数。
失败时抛 ValueError（带具体原因），由调用方决定转成 422 / 400。
（例外:`is_config_entry` 是 bool 谓词,不抛 —— 跳过不是错误。）
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


def is_config_entry(name: str) -> bool:
    """config 目录条目是否参与加载:跳过 `_`(operator 禁用约定)/ `.`(隐藏/传输垃圾)前缀。

    **唯一实现** —— tools/skills seed 解析、agents 的运行时 loader 与 DB seed 解析
    共用(两个活 lister 必须逐字节一致,否则 DB 物化集与运行时可加载集分裂)。
    落在 utils 叶子:seeds.py import agents.loader,谓词放 seeds 会成环。
    """
    return not name.startswith(("_", "."))
