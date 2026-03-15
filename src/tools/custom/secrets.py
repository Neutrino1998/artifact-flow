"""
模板变量解析器
将 {{VAR_NAME}} 占位符替换为运行时环境变量值
"""

import os
import re
from typing import Any, Dict


def resolve_secrets(obj: Any) -> Any:
    """
    递归解析对象中的 {{VAR}} 模板变量

    从环境变量（含 .env）中读取值。
    未找到的变量保持原样（不替换）并记录警告。

    Args:
        obj: 字符串、字典或列表

    Returns:
        替换后的对象（同类型）
    """
    if isinstance(obj, str):
        return _resolve_string(obj)
    elif isinstance(obj, dict):
        return {k: resolve_secrets(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [resolve_secrets(item) for item in obj]
    return obj


_TEMPLATE_PATTERN = re.compile(r"\{\{(\w+)\}\}")


def _resolve_string(text: str) -> str:
    """替换字符串中的所有 {{VAR}} 占位符"""
    def _replace(match: re.Match) -> str:
        var_name = match.group(1)
        value = os.environ.get(var_name)
        if value is None:
            from utils.logger import get_logger
            get_logger("ArtifactFlow").warning(
                f"Template variable '{{{{{var_name}}}}}' not found in environment"
            )
            return match.group(0)  # 保持原样
        return value

    return _TEMPLATE_PATTERN.sub(_replace, text)
