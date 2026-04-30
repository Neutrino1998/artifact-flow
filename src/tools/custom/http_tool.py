"""
HttpTool — 从 MD 配置生成的 HTTP API 工具

继承 BaseTool，将声明式 YAML 配置转化为可执行的 HTTP 调用。
"""

import json
import httpx
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field

from tools.base import BaseTool, ToolResult, ToolParameter, ToolPermission
from tools.custom.secrets import resolve_secrets
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


@dataclass
class HttpToolConfig:
    """HTTP 工具配置（从 MD frontmatter 解析）"""
    name: str
    description: str
    permission: str = "confirm"       # 自定义工具默认 confirm，更安全
    endpoint: str = ""
    method: str = "GET"
    headers: Dict[str, str] = field(default_factory=dict)
    parameters: List[ToolParameter] = field(default_factory=list)
    response_extract: Optional[str] = None   # JSONPath 提取表达式
    timeout: int = 30                        # 请求超时（秒）


class HttpTool(BaseTool):
    """
    从 MD 配置生成的 HTTP API 工具

    执行流程：
    1. 注册时：MD frontmatter → HttpToolConfig → HttpTool 实例
    2. 调用时：LLM tool_call → 拼 HTTP 请求 → 发出 → 提取结果 → 返回
    3. 密钥处理：{{VAR}} 在运行时从环境变量注入，不暴露给 LLM
    """

    def __init__(self, config: HttpToolConfig):
        super().__init__(
            name=config.name,
            description=config.description,
            permission=ToolPermission(config.permission),
        )
        self._endpoint = config.endpoint
        self._method = config.method.upper()
        self._headers = config.headers
        self._response_extract = config.response_extract
        self._timeout = config.timeout
        self._param_defs = config.parameters

    def get_parameters(self) -> List[ToolParameter]:
        return self._param_defs

    async def execute(self, **params) -> ToolResult:
        """
        发送 HTTP 请求并返回结果

        Args:
            **params: 工具参数（由 LLM 提供）

        Returns:
            ToolResult
        """
        if not self._endpoint:
            return ToolResult(success=False, error="Tool endpoint not configured")

        # 运行时注入 secrets
        headers = resolve_secrets(self._headers)
        endpoint = resolve_secrets(self._endpoint)

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                if self._method in ("POST", "PUT", "PATCH"):
                    response = await client.request(
                        self._method,
                        endpoint,
                        json=params,
                        headers=headers,
                    )
                else:
                    response = await client.request(
                        self._method,
                        endpoint,
                        params=params,
                        headers=headers,
                    )

            response.raise_for_status()

            # 解析响应
            content_type = response.headers.get("content-type", "")
            if "json" in content_type:
                data = response.json()
                # JSONPath 提取
                if self._response_extract:
                    data = _extract_jsonpath(data, self._response_extract)
                # dict/list → JSON（给 LLM 可读的格式，单引号 repr 容易误导）
                # 含非序列化类型时回退 str()
                if isinstance(data, (dict, list)):
                    try:
                        result_text = json.dumps(data, ensure_ascii=False)
                    except (TypeError, ValueError):
                        result_text = str(data)
                else:
                    result_text = "" if data is None else str(data)
            else:
                result_text = response.text

            # 限制返回长度
            max_len = 50000
            if len(result_text) > max_len:
                result_text = result_text[:max_len] + "\n\n[Response truncated...]"

            return ToolResult(
                success=True,
                data=result_text,
                metadata={
                    "status_code": response.status_code,
                    "endpoint": endpoint,
                },
            )

        except httpx.HTTPStatusError as e:
            return ToolResult(
                success=False,
                error=f"HTTP {e.response.status_code}: {e.response.text[:500]}",
            )
        except httpx.RequestError as e:
            return ToolResult(
                success=False,
                error=f"Request failed: {str(e)}",
            )
        except Exception as e:
            logger.exception(f"HttpTool '{self.name}' execution error")
            return ToolResult(
                success=False,
                error=f"Tool execution failed: {str(e)}",
            )


def _extract_jsonpath(data: Any, path: str) -> Any:
    """
    简易 JSONPath 提取（支持 $.key1.key2 和 $.key1[0] 格式）

    不依赖外部库，覆盖常见场景即可。

    Args:
        data: JSON 数据
        path: JSONPath 表达式（如 $.data.price）

    Returns:
        提取的值
    """
    if not path or path == "$":
        return data

    # 去掉 $ 前缀
    path = path.lstrip("$").lstrip(".")

    current = data
    for part in path.split("."):
        if not part:
            continue

        # 处理数组索引 key[0]
        if "[" in part:
            key, idx_str = part.split("[", 1)
            idx = int(idx_str.rstrip("]"))
            if key:
                if isinstance(current, dict):
                    current = current.get(key)
                else:
                    return None
            if isinstance(current, list) and 0 <= idx < len(current):
                current = current[idx]
            else:
                return None
        else:
            if isinstance(current, dict):
                current = current.get(part)
            else:
                return None

        if current is None:
            return None

    return current
