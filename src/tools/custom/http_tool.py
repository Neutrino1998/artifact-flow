"""
HttpTool — 从 MD 配置生成的 HTTP API 工具

继承 BaseTool，将声明式 YAML 配置转化为可执行的 HTTP 调用。
"""

import json
import httpx
import jmespath
from jmespath.exceptions import JMESPathError
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field

from tools.base import BaseTool, ToolResult, ToolParameter, ToolPermission
from tools.custom.secrets import (
    resolve_secrets,
    substitute_templates,
    SecretResolutionError,
)
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
    response_extract: Optional[str] = None   # JMESPath 提取表达式(无 $. 前缀,如 data.price)
    timeout: int = 60                        # 请求超时（秒）。per-MD 可调;长任务 endpoint 放心设大 ——
                                             # cancel 延迟不再受此值支配(engine 工具 await 期轮询 cancel,
                                             # core/cancellation.py),本值只决定"等上游多久算失败"。


class HttpTool(BaseTool):
    """
    从 MD 配置生成的 HTTP API 工具

    执行流程：
    1. 注册时：MD frontmatter → HttpToolConfig → HttpTool 实例
    2. 调用时：LLM tool_call → 拼 HTTP 请求 → 发出 → 提取结果 → 返回
    3. 密钥处理：{{VAR}} 由 credential_resolver 在 execute 期按 unit 从 tool_credentials
       短 session 解密注入(只解被调工具、不驻留整轮);无 resolver 时回落 env;不暴露给 LLM
    """

    def __init__(self, config: HttpToolConfig, *, unit_name=None, credential_resolver=None):
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
        # 运行期凭证(B-4;B-5 退回 lazy):snapshot 重建时灌入 unit 名 + resolver。两者齐备
        # → execute 期按 unit 从 tool_credentials(加密落库)开短 session 解密填 {{NAME}}
        # (只解被调工具、用完即弃);否则回落 env(legacy loader / 直接构造的工具,无 unit
        # 上下文)。凭证 = unit 级,故按 unit 名(非 full_name)查。
        self._unit_name = unit_name
        self._credential_resolver = credential_resolver

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

        # 注入 secrets(缺失 / 非白名单前缀 / 解密失败 → 拒绝,不外发占位符原文)。
        # DB 路径(B-4;B-5 lazy):resolver + unit 名齐备 → execute 期开短 session 按 unit
        # 解密凭证表填 {{NAME}}(只解被调工具、不驻留整轮);回落路径:无注入(legacy loader /
        # 测试直接构造)→ env 解析(白名单前缀)。
        try:
            if self._credential_resolver is not None and self._unit_name is not None:
                values = await self._credential_resolver.resolve(self._unit_name)
                headers = substitute_templates(self._headers, values)
                endpoint = substitute_templates(self._endpoint, values)
            else:
                headers = resolve_secrets(self._headers)
                endpoint = resolve_secrets(self._endpoint)
        except SecretResolutionError as e:
            logger.error(f"HttpTool '{self.name}' secret resolution failed: {e}")
            return ToolResult(
                success=False,
                error="Tool configuration error: a required secret is unavailable",
            )

        # 注意:此处**刻意不**跑 validate_public_url。endpoint 是运维在 config/tools/*.md
        # （:ro 挂载、来源可信)里固定配置的,LLM 只能填 params(进 body / query),无法
        # 影响目标主机 —— 不是 SSRF 攻击面。内网 gateway 是合法用途,公网校验只会误伤它。
        # 密钥外泄由 {{TOOL_SECRET_}} 前缀白名单防,302→内网由 follow_redirects=False 防。
        # (validate_public_url 仍守在 web_fetch,那里 URL 才是 LLM 可控的真正 SSRF 面。)
        try:
            # follow_redirects=False：杜绝 302 → 内网 / 元数据 的重定向绕过
            # trust_env=False：httpx 默认 True 会读 HTTP(S)_PROXY/.netrc，污染后可把已校验的
            #   公网请求改道内网代理，绕过 IP 校验。与 web_fetch(aiohttp 默认 False)对齐。
            async with httpx.AsyncClient(
                timeout=self._timeout, follow_redirects=False, trust_env=False
            ) as client:
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
                # JMESPath 提取
                if self._response_extract:
                    extracted = jmespath.search(self._response_extract, data)
                    if extracted is None:
                        # 合法表达式但匹配不到(键缺失 / 值本就是 null)。旧实现这里静默返回 ""，
                        # 模型无法区分"路径配错"和"字段本就为空"—— 显式告知而非吞掉。
                        # (语法错的表达式已在写入边界 validate_response_extract loud-fail。)
                        return ToolResult(
                            success=True,
                            data=f"response_extract '{self._response_extract}' matched nothing in the response",
                            metadata={"status_code": response.status_code},
                        )
                    data = extracted
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
                    # 刻意不带 endpoint:host 是内网拓扑(允许内网 gateway 后更敏感),且会随
                    # metadata 进 tool_complete SSE → 浏览器 + MessageEvent.data 入库/事件历史。
                    # 调用身份已由 tool_complete 事件的 "tool" 字段标识,host 无额外价值。
                    "status_code": response.status_code,
                },
            )

        except httpx.HTTPStatusError as e:
            # 仅回状态码给 LLM；上游响应体可能含内网主机名 / 栈 / token，仅入 debug 日志
            logger.debug(
                f"HttpTool '{self.name}' upstream HTTP {e.response.status_code}: "
                f"{e.response.text[:500]}"
            )
            return ToolResult(
                success=False,
                error=f"HTTP {e.response.status_code}",
            )
        except httpx.RequestError as e:
            # str(e) 可能含内网地址/连接细节，不回显给 LLM
            logger.warning(f"HttpTool '{self.name}' request error: {e}")
            return ToolResult(
                success=False,
                error="Request failed: could not reach the endpoint",
            )
        except Exception as e:
            logger.exception(f"HttpTool '{self.name}' execution error")
            return ToolResult(
                success=False,
                error=f"Tool execution failed: {str(e)}",
            )


def validate_response_extract(expr: Optional[str]) -> None:
    """response_extract(JMESPath)表达式的写入边界校验 —— 非法则 raise ValueError。

    两个写入边界(config-seed `seeds._build_http_member` 与 dynamic CRUD
    `tool_registry_manager._build_definition`)都调它,让 typo 的表达式在
    部署/保存期 loud-fail,而不是等到首次调用才在 jmespath.search 里抛。
    各调用方把 ValueError 包成自己的域错误(SeedError / InvalidUnitError)。

    JMESPath 是裸语法(`data.price`),不带 `$.` 前缀;旧式 `$.data.price` 会被
    jmespath 报 `Unknown token $`(刻意不做兼容 —— 工具 DB 化尚未发版,无存量 `$.` 值
    需迁移,clean break 优于永久双语法)。

    注:能编译但运行期匹配不到(键缺失/null)不是语法错,无法在此判定 —— 由
    HttpTool.execute 在那种情况显式回 "matched nothing",不再静默空。
    """
    if expr is None or expr == "":
        return
    # 非字符串(如 YAML 未加引号的 `response_extract: 123` → int,或 falsy 的 0/false):
    # jmespath.compile 会抛 TypeError 而非 JMESPathError,会绕过下面的 except 漏到
    # reconcile 顶层裸 traceback、丢掉 SeedError 的文件名归属(本函数存在的意义)。在此显式拦。
    if not isinstance(expr, str):
        raise ValueError(f"response_extract must be a string, got {type(expr).__name__}")
    try:
        jmespath.compile(expr)
    except JMESPathError as e:
        raise ValueError(f"invalid JMESPath expression {expr!r}: {e}") from e
