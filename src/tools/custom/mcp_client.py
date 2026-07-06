"""MCP client manager.

The rest of ArtifactFlow talks to this module through small dataclasses instead
of importing MCP SDK models. That keeps snapshot/tool construction stable while
the SDK evolves.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable, Optional

import httpx

from tools.custom.secrets import (
    SecretResolutionError,
    resolve_secrets,
    substitute_templates,
)
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


@dataclass(frozen=True)
class McpToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class McpToolCallResult:
    is_error: bool
    content: list[Any]
    structured_content: Any = None


@dataclass(frozen=True)
class McpListResult:
    tools: list[McpToolDefinition]
    error: Optional[str] = None


class McpClientError(RuntimeError):
    """MCP server connection/call failed. Message is safe for user-facing tool errors."""


SdkListCallable = Callable[[str, dict[str, str], int], Any]
SdkCallCallable = Callable[[str, dict[str, str], int, str, dict[str, Any]], Any]


class McpClientManager:
    """Per-worker MCP client facade.

    v0 keeps discovery results in a process-local cache keyed by unit name and
    connection config fingerprint. The authoritative truth remains the external
    server; the cache is only the current worker's turn-start view.
    """

    def __init__(
        self,
        *,
        list_callable: Optional[SdkListCallable] = None,
        call_callable: Optional[SdkCallCallable] = None,
    ) -> None:
        self._list_callable = list_callable or self._list_tools_via_sdk
        self._call_callable = call_callable or self._call_tool_via_sdk
        self._cache: dict[str, tuple[str, list[McpToolDefinition]]] = {}
        self._lock = asyncio.Lock()

    async def list_tools(
        self,
        unit_name: str,
        provider_config: dict[str, Any],
        *,
        credential_resolver=None,
    ) -> McpListResult:
        try:
            url, headers, timeout, fingerprint = await self._resolve_config(
                unit_name, provider_config, credential_resolver=credential_resolver
            )
        except McpClientError as exc:
            logger.warning("MCP server %r configuration error: %s", unit_name, exc)
            return McpListResult(tools=[], error=str(exc))

        async with self._lock:
            cached = self._cache.get(unit_name)
            if cached is not None and cached[0] == fingerprint:
                return McpListResult(tools=list(cached[1]))

        try:
            raw_tools = await self._list_callable(url, headers, timeout)
            tools = [_coerce_tool_definition(t) for t in raw_tools]
        except Exception as exc:
            logger.warning("MCP server %r tools/list failed: %s", unit_name, exc)
            return McpListResult(tools=[], error="MCP server is unavailable")

        async with self._lock:
            self._cache[unit_name] = (fingerprint, list(tools))
        return McpListResult(tools=tools)

    async def call_tool(
        self,
        unit_name: str,
        provider_config: dict[str, Any],
        tool_name: str,
        arguments: dict[str, Any],
        *,
        credential_resolver=None,
    ) -> McpToolCallResult:
        url, headers, timeout, _fingerprint = await self._resolve_config(
            unit_name, provider_config, credential_resolver=credential_resolver
        )
        try:
            return await self._call_callable(url, headers, timeout, tool_name, arguments)
        except McpClientError:
            raise
        except Exception as exc:
            logger.warning(
                "MCP server %r tools/call %r failed: %s",
                unit_name,
                tool_name,
                exc,
            )
            raise McpClientError("MCP request failed: could not reach the server") from exc

    async def _resolve_config(
        self,
        unit_name: str,
        provider_config: dict[str, Any],
        *,
        credential_resolver=None,
    ) -> tuple[str, dict[str, str], int, str]:
        cfg = dict(provider_config or {})
        if cfg.get("transport") != "streamable_http":
            raise McpClientError("MCP transport must be streamable_http")

        raw_url = cfg.get("url")
        if not isinstance(raw_url, str) or not raw_url.strip():
            raise McpClientError("MCP server URL is not configured")
        raw_headers = cfg.get("headers") or {}
        if not isinstance(raw_headers, dict):
            raise McpClientError("MCP headers must be an object")
        timeout = cfg.get("timeout", 60)
        if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout < 1:
            raise McpClientError("MCP timeout must be a positive integer")

        try:
            if credential_resolver is not None:
                values = await credential_resolver.resolve(unit_name)
                url = substitute_templates(raw_url, values)
                headers = substitute_templates(raw_headers, values)
            else:
                url = resolve_secrets(raw_url)
                headers = resolve_secrets(raw_headers)
        except SecretResolutionError as exc:
            raise McpClientError("MCP server credential is unavailable") from exc

        normalized_headers = {
            str(k): str(v) for k, v in headers.items()
            if v is not None
        }
        fingerprint = _fingerprint(url, normalized_headers, timeout)
        return url, normalized_headers, timeout, fingerprint

    async def _list_tools_via_sdk(
        self, url: str, headers: dict[str, str], timeout: int
    ) -> list[Any]:
        async with self._sdk_session(url, headers, timeout) as session:
            result = await session.list_tools()
            return list(getattr(result, "tools", []) or [])

    async def _call_tool_via_sdk(
        self,
        url: str,
        headers: dict[str, str],
        timeout: int,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> McpToolCallResult:
        async with self._sdk_session(url, headers, timeout) as session:
            result = await session.call_tool(
                tool_name,
                arguments,
                read_timeout_seconds=timedelta(seconds=timeout),
            )
        return McpToolCallResult(
            is_error=bool(_get_sdk_attr(result, "isError", "is_error", default=False)),
            content=list(getattr(result, "content", []) or []),
            structured_content=_get_sdk_attr(
                result, "structuredContent", "structured_content", default=None
            ),
        )

    def _sdk_session(self, url: str, headers: dict[str, str], timeout: int):
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client
        except ImportError as exc:  # pragma: no cover - exercised only without dependency.
            raise McpClientError("MCP SDK is not installed") from exc

        return _McpSessionContext(url, headers, timeout, ClientSession, streamable_http_client)


class _McpSessionContext:
    def __init__(self, url: str, headers: dict[str, str], timeout: int, session_cls, transport_factory) -> None:
        self._url = url
        self._headers = headers
        self._timeout = timeout
        self._session_cls = session_cls
        self._transport_factory = transport_factory
        self._stack = None
        self._session = None

    async def __aenter__(self):
        from contextlib import AsyncExitStack

        self._stack = AsyncExitStack()
        http_client = httpx.AsyncClient(
            headers=self._headers,
            timeout=float(self._timeout),
            trust_env=False,
        )
        await self._stack.enter_async_context(http_client)
        read_stream, write_stream, _get_session_id = await self._stack.enter_async_context(
            self._transport_factory(self._url, http_client=http_client)
        )
        self._session = await self._stack.enter_async_context(
            self._session_cls(
                read_stream,
                write_stream,
                read_timeout_seconds=timedelta(seconds=self._timeout),
            )
        )
        await self._session.initialize()
        return self._session

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        assert self._stack is not None
        return await self._stack.__aexit__(exc_type, exc_val, exc_tb)


def _coerce_tool_definition(raw: Any) -> McpToolDefinition:
    data = _to_plain_dict(raw)
    input_schema = data.get("input_schema") or data.get("inputSchema") or {}
    if not isinstance(input_schema, dict):
        input_schema = {}
    return McpToolDefinition(
        name=str(data.get("name") or ""),
        description=str(data.get("description") or ""),
        input_schema=input_schema,
    )


def _get_sdk_attr(value: Any, *names: str, default: Any) -> Any:
    for name in names:
        if hasattr(value, name):
            return getattr(value, name)
    return default


def _to_plain_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(by_alias=True, mode="json", exclude_none=True)
    if hasattr(value, "dict"):
        return value.dict()
    return {
        key: getattr(value, key)
        for key in ("name", "description", "input_schema", "inputSchema")
        if hasattr(value, key)
    }


def _fingerprint(url: str, headers: dict[str, str], timeout: int) -> str:
    payload = json.dumps(
        {"url": url, "headers": headers, "timeout": timeout},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
