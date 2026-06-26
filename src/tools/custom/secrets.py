"""
模板变量解析器
将 {{VAR_NAME}} 占位符替换为运行时环境变量值。

安全约束(SSRF-02):自定义工具只能解析带 `CUSTOM_TOOL_SECRET_PREFIX`
(默认 `TOOL_SECRET_`)前缀的变量 —— 把 JWT 签名密钥 / DB 密码 / 第三方
凭证等进程环境里的敏感变量挡在自定义工具可触及范围之外(最小权限)。

- 非白名单前缀的 {{VAR}} → 直接报错(load 时由 `assert_secret_refs_allowed`
  拦下,工具不注册;运行时若仍遇到也拒绝解析)。
- 前缀合规但环境缺失 → 报错而非把占位符原样外发(防把 `{{...}}` 当真值送出)。
"""

import os
import re
from typing import Any

from config import config


_TEMPLATE_PATTERN = re.compile(r"\{\{(\w+)\}\}")


class SecretResolutionError(ValueError):
    """模板变量不符合前缀白名单,或环境中缺失。"""


def resolve_secrets(obj: Any) -> Any:
    """递归解析对象中的 {{VAR}} 模板变量。

    Args:
        obj: 字符串、字典或列表

    Returns:
        替换后的对象(同类型)

    Raises:
        SecretResolutionError: 变量不符合前缀白名单,或环境中未设置。
    """
    if isinstance(obj, str):
        return _resolve_string(obj)
    elif isinstance(obj, dict):
        return {k: resolve_secrets(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [resolve_secrets(item) for item in obj]
    return obj


def _resolve_string(text: str) -> str:
    """替换字符串中的所有 {{VAR}} 占位符(强制前缀白名单 + 缺失即报错)。"""
    def _replace(match: re.Match) -> str:
        var_name = match.group(1)
        if not var_name.startswith(config.CUSTOM_TOOL_SECRET_PREFIX):
            raise SecretResolutionError(
                f"Template variable '{{{{{var_name}}}}}' is not allowed; "
                f"custom-tool secrets must use the "
                f"'{config.CUSTOM_TOOL_SECRET_PREFIX}' prefix"
            )
        value = os.environ.get(var_name)
        if value is None:
            raise SecretResolutionError(
                f"Template variable '{{{{{var_name}}}}}' is not set in environment"
            )
        return value

    return _TEMPLATE_PATTERN.sub(_replace, text)


def extract_placeholders(obj: Any) -> set:
    """递归收集对象内所有 `{{NAME}}` 占位符名(去前缀/裸名,原样返回花括号内的串)。

    reconciler 用它知道一个 unit 的定义引用了哪些 secret 占位符 → 据此从 env 取值
    加密落库。不校验前缀(前缀闸是 `assert_secret_refs_allowed` 的事,在 seed 解析期跑)。
    """
    names: set = set()
    if isinstance(obj, str):
        names.update(m.group(1) for m in _TEMPLATE_PATTERN.finditer(obj))
    elif isinstance(obj, dict):
        for value in obj.values():
            names |= extract_placeholders(value)
    elif isinstance(obj, list):
        for item in obj:
            names |= extract_placeholders(item)
    return names


def substitute_templates(obj: Any, values: dict) -> Any:
    """用 `values`(占位符名 → 明文)替换对象内的 `{{NAME}}`。

    与 env 版 `resolve_secrets` 的区别:值来源是**传入的 map**(凭证表解密结果),
    不读 env、不做前缀白名单 —— 值已是某 unit 自己策展的凭证行,前缀闸只在 env
    种子边界(seed/reconcile)需要。缺占位符 → `SecretResolutionError`(execute 期
    被 HttpTool 捕成 generic error + ops log,绝不外发占位符原文)。
    """
    if isinstance(obj, str):
        def _replace(match: "re.Match") -> str:
            name = match.group(1)
            if name not in values:
                raise SecretResolutionError(
                    f"Template variable '{{{{{name}}}}}' has no configured credential"
                )
            return values[name]
        return _TEMPLATE_PATTERN.sub(_replace, obj)
    elif isinstance(obj, dict):
        return {k: substitute_templates(v, values) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [substitute_templates(item, values) for item in obj]
    return obj


def assert_secret_refs_allowed(obj: Any) -> None:
    """load-time 闸门:递归断言对象内每个 {{VAR}} 引用都用了白名单前缀。

    只校验前缀(不读环境值,环境可能在 load 后才注入);命中非白名单即抛错,
    由 loader 把整个工具拒之门外。

    Raises:
        SecretResolutionError: 存在非白名单前缀的模板变量引用。
    """
    if isinstance(obj, str):
        for match in _TEMPLATE_PATTERN.finditer(obj):
            var_name = match.group(1)
            if not var_name.startswith(config.CUSTOM_TOOL_SECRET_PREFIX):
                raise SecretResolutionError(
                    f"Template variable '{{{{{var_name}}}}}' is not allowed; "
                    f"custom-tool secrets must use the "
                    f"'{config.CUSTOM_TOOL_SECRET_PREFIX}' prefix"
                )
    elif isinstance(obj, dict):
        for value in obj.values():
            assert_secret_refs_allowed(value)
    elif isinstance(obj, list):
        for item in obj:
            assert_secret_refs_allowed(item)
