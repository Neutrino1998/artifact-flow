"""MCP tools exposed through ArtifactFlow's BaseTool contract."""

from __future__ import annotations

import re
import json
import base64
import binascii
from dataclasses import dataclass
from typing import Any, Optional

from config import config
from tools.artifact_output import build_artifact_spec
from tools.base import BaseTool, ToolPermission, ToolResult
from tools.custom.mcp_client import McpClientError, McpClientManager, McpToolDefinition
from tools.input_schema import (
    InputSchemaError,
    normalize_business_input_schema,
    validate_native_tool_name,
)
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")

MCP_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True)
class McpToolConfig:
    full_name: str
    server_name: str
    tool_name: str
    description: str
    permission: str
    input_schema: dict[str, Any]
    provider_config: dict[str, Any]


@dataclass(frozen=True)
class _McpArtifactBlock:
    kind: str
    content_type: str
    blob: bytes
    metadata: dict[str, Any]


@dataclass(frozen=True)
class _McpContentSummary:
    text_blocks: list[str]
    artifact_blocks: list[_McpArtifactBlock]
    unsupported_blocks: list[str]


class McpTool(BaseTool):
    def __init__(
        self,
        config_: McpToolConfig,
        *,
        client_manager: McpClientManager,
        credential_resolver=None,
    ) -> None:
        super().__init__(
            name=config_.full_name,
            description=config_.description,
            permission=ToolPermission(config_.permission),
        )
        self._server_name = config_.server_name
        self._tool_name = config_.tool_name
        self._input_schema = normalize_business_input_schema(
            config_.input_schema,
            source=f"MCP tool '{config_.full_name}' input_schema",
        )
        self._provider_config = config_.provider_config
        self._client_manager = client_manager
        self._credential_resolver = credential_resolver
    def get_input_schema(self) -> dict:
        return self._input_schema

    async def execute(self, **params) -> ToolResult:
        try:
            result = await self._client_manager.call_tool(
                self._server_name,
                self._provider_config,
                self._tool_name,
                params,
                credential_resolver=self._credential_resolver,
            )
        except McpClientError as exc:
            return ToolResult(success=False, error=str(exc))

        try:
            summary = summarize_mcp_content(result)
        except ValueError as exc:
            logger.warning(
                "MCP tool %r from server %r returned invalid content: %s",
                self._tool_name,
                self._server_name,
                exc,
            )
            return ToolResult(success=False, error=str(exc))

        data = render_mcp_result_data(result, summary=summary)

        if result.is_error:
            # 失败结果不会进入成功结果 artifact 路径；诊断仍须有界，避免恶意/异常
            # MCP server 用超长 error body 灌入事件与下一轮上下文。
            error_data = data
            if len(error_data) > config.TOOL_ERROR_MAX_CHARS:
                marker = "\n\n[MCP error response truncated...]"
                limit = config.TOOL_ERROR_MAX_CHARS
                error_data = (
                    marker[:limit]
                    if limit <= len(marker)
                    else error_data[: limit - len(marker)] + marker
                )
            return ToolResult(
                success=False,
                data=error_data,
                error=(error_data or "MCP tool returned an error"),
            )

        if summary.unsupported_blocks:
            unsupported = ", ".join(summary.unsupported_blocks)
            logger.warning(
                "MCP tool %r from server %r returned unsupported non-text content block(s): %s",
                self._tool_name,
                self._server_name,
                unsupported,
            )
            return ToolResult(
                success=False,
                error=f"MCP tool returned unsupported non-text content block(s): {unsupported}",
            )

        if summary.artifact_blocks and (summary.text_blocks or result.structured_content is not None):
            logger.warning(
                "MCP tool %r from server %r returned mixed text/structured and artifact content; "
                "mixed result payloads are not supported",
                self._tool_name,
                self._server_name,
            )
            return ToolResult(
                success=False,
                error=(
                    "MCP tool returned mixed text/structured and artifact content; "
                    "mixed result payloads are not supported in this version"
                ),
            )

        if len(summary.artifact_blocks) > 1:
            logger.warning(
                "MCP tool %r from server %r returned %d artifact-capable content blocks; "
                "multiple artifacts are not supported",
                self._tool_name,
                self._server_name,
                len(summary.artifact_blocks),
            )
            return ToolResult(
                success=False,
                error=(
                    "MCP tool returned multiple non-text content blocks; "
                    "multiple artifacts are not supported in this version"
                ),
            )

        if len(summary.artifact_blocks) == 1:
            artifact_block = summary.artifact_blocks[0]
            try:
                spec = build_artifact_spec(
                    tool_name=self.name,
                    config={
                        "enabled": True,
                        "mode": "binary",
                        "content_type": artifact_block.content_type,
                        "filename": None,
                        "title": None,
                    },
                    blob=artifact_block.blob,
                    metadata={
                        "mcp_server": self._server_name,
                        "mcp_tool": self._tool_name,
                        "mcp_content_block_type": artifact_block.kind,
                        **artifact_block.metadata,
                    },
                )
            except ValueError as exc:
                logger.warning(
                    "MCP tool %r from server %r returned invalid artifact content: %s",
                    self._tool_name,
                    self._server_name,
                    exc,
                )
                return ToolResult(success=False, error=str(exc))
            note = (
                f'<file content_type="{spec.content_type}" bytes="{len(artifact_block.blob)}">'
                "MCP content stored as artifact.</file>"
            )
            return ToolResult(
                success=True,
                data=note,
                metadata={"mcp_content_block_type": artifact_block.kind},
                artifact=spec,
            )

        return ToolResult(success=True, data=data)


def build_mcp_tool(
    *,
    server_name: str,
    provider_config: dict[str, Any],
    definition: McpToolDefinition,
    client_manager: McpClientManager,
    credential_resolver=None,
) -> Optional[McpTool]:
    if not MCP_TOOL_NAME_RE.fullmatch(definition.name):
        logger.warning(
            "Skipping MCP tool %r from server %r: tool name violates MCP name pattern",
            definition.name,
            server_name,
        )
        return None
    full_name = f"{server_name}__{definition.name}"
    try:
        validate_native_tool_name(full_name)
        input_schema = normalize_business_input_schema(
            definition.input_schema,
            source=f"MCP tool '{full_name}' input_schema",
        )
    except InputSchemaError as exc:
        logger.warning(
            "Skipping MCP tool %r from server %r: %s",
            definition.name,
            server_name,
            exc,
        )
        return None
    permission = str(provider_config.get("default_permission") or "confirm")
    cfg = McpToolConfig(
        full_name=full_name,
        server_name=server_name,
        tool_name=definition.name,
        description=definition.description or f"MCP tool {definition.name} from {server_name}",
        permission=permission,
        input_schema=input_schema,
        provider_config=provider_config,
    )
    return McpTool(
        cfg,
        client_manager=client_manager,
        credential_resolver=credential_resolver,
    )


def summarize_mcp_content(result) -> _McpContentSummary:
    text_blocks: list[str] = []
    artifact_blocks: list[_McpArtifactBlock] = []
    unsupported_blocks: list[str] = []

    for block in result.content:
        kind = _content_type(block)
        if kind == "text":
            text = _content_text(block)
            if text:
                text_blocks.append(text)
            continue

        if kind == "resource":
            resource = _get_field(block, "resource")
            text = _resource_text(resource)
            if text is not None:
                if text:
                    text_blocks.append(text)
                continue
            artifact = _artifact_block_from_resource(resource)
            if artifact is not None:
                artifact_blocks.append(artifact)
            else:
                unsupported_blocks.append("resource")
            continue

        artifact = _artifact_block_from_block(block)
        if artifact is not None:
            artifact_blocks.append(artifact)
        else:
            unsupported_blocks.append(kind or "unknown")

    return _McpContentSummary(
        text_blocks=text_blocks,
        artifact_blocks=artifact_blocks,
        unsupported_blocks=unsupported_blocks,
    )


def render_mcp_result_data(result, *, summary: Optional[_McpContentSummary] = None) -> str:
    if result.structured_content is not None:
        return json.dumps(result.structured_content, ensure_ascii=False)

    summary = summary or summarize_mcp_content(result)
    text_blocks = summary.text_blocks
    unsupported = len(summary.unsupported_blocks) + len(summary.artifact_blocks)
    if text_blocks:
        body = "\n\n".join(text_blocks)
        if unsupported:
            body += f"\n\n[{unsupported} non-text MCP content block(s) returned separately.]"
        return body
    if unsupported:
        return f"{unsupported} non-text MCP content block(s) returned."
    return ""


def _content_type(block: Any) -> str:
    if isinstance(block, dict):
        return str(block.get("type") or "")
    return str(getattr(block, "type", "") or "")


def _content_text(block: Any) -> str:
    if isinstance(block, dict):
        return str(block.get("text") or "")
    return str(getattr(block, "text", "") or "")


def _artifact_block_from_block(block: Any) -> Optional[_McpArtifactBlock]:
    kind = _content_type(block)
    if kind not in {"image", "audio", "blob"}:
        return None
    encoded = _get_field(block, "data", "blob")
    content_type = _get_field(block, "mimeType", "mime_type", "content_type")
    if not isinstance(content_type, str) or not content_type.strip():
        raise ValueError(f"MCP {kind} content block is missing mimeType")
    return _McpArtifactBlock(
        kind=kind,
        content_type=content_type.strip(),
        blob=_decode_base64_content(encoded, kind),
        metadata={},
    )


def _artifact_block_from_resource(resource: Any) -> Optional[_McpArtifactBlock]:
    encoded = _get_field(resource, "blob")
    if encoded is None:
        return None
    content_type = _get_field(resource, "mimeType", "mime_type") or "application/octet-stream"
    if not isinstance(content_type, str) or not content_type.strip():
        content_type = "application/octet-stream"
    uri = _get_field(resource, "uri")
    metadata = {"mcp_resource_uri": str(uri)} if uri is not None else {}
    return _McpArtifactBlock(
        kind="resource",
        content_type=content_type.strip(),
        blob=_decode_base64_content(encoded, "resource"),
        metadata=metadata,
    )


def _resource_text(resource: Any) -> Optional[str]:
    text = _get_field(resource, "text")
    return text if isinstance(text, str) else None


def _decode_base64_content(value: Any, kind: str) -> bytes:
    if not isinstance(value, str) or not value:
        raise ValueError(f"MCP {kind} content block is missing base64 data")
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"MCP {kind} content block contains invalid base64 data") from exc


def _get_field(value: Any, *names: str) -> Any:
    if isinstance(value, dict):
        for name in names:
            if name in value:
                return value[name]
        return None
    for name in names:
        if hasattr(value, name):
            return getattr(value, name)
    return None
