"""MCP tools exposed through ArtifactFlow's BaseTool contract."""

from __future__ import annotations

import json
import re
import base64
import binascii
from dataclasses import dataclass
from typing import Any, Optional

from jsonschema import ValidationError, validate

from config import config
from tools.artifact_output import build_artifact_spec
from tools.base import BaseTool, ToolParameter, ToolPermission, ToolResult
from tools.custom.mcp_client import McpClientError, McpClientManager, McpToolDefinition
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")

MCP_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
XML_PARAM_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")


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
        self._input_schema = config_.input_schema or {"type": "object", "properties": {}}
        self._provider_config = config_.provider_config
        self._client_manager = client_manager
        self._credential_resolver = credential_resolver
        self._param_defs = parameters_from_json_schema(self._input_schema)

    def get_parameters(self) -> list[ToolParameter]:
        return self._param_defs

    async def execute(self, **params) -> ToolResult:
        try:
            arguments = coerce_arguments_for_schema(params, self._input_schema)
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))

        try:
            result = await self._client_manager.call_tool(
                self._server_name,
                self._provider_config,
                self._tool_name,
                arguments,
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
        if len(data) > config.MCP_TOOL_MAX_RESULT_CHARS:
            data = data[:config.MCP_TOOL_MAX_RESULT_CHARS] + "\n\n[MCP response truncated...]"

        if result.is_error:
            return ToolResult(success=False, data=data, error=(data or "MCP tool returned an error"))

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
    invalid_params = invalid_xml_param_names(definition.input_schema)
    if invalid_params:
        logger.warning(
            "Skipping MCP tool %r from server %r: parameter name(s) are not XML-safe: %s",
            definition.name,
            server_name,
            ", ".join(invalid_params),
        )
        return None
    permission = str(provider_config.get("default_permission") or "confirm")
    full_name = f"{server_name}__{definition.name}"
    cfg = McpToolConfig(
        full_name=full_name,
        server_name=server_name,
        tool_name=definition.name,
        description=definition.description or f"MCP tool {definition.name} from {server_name}",
        permission=permission,
        input_schema=definition.input_schema,
        provider_config=provider_config,
    )
    return McpTool(
        cfg,
        client_manager=client_manager,
        credential_resolver=credential_resolver,
    )


def parameters_from_json_schema(schema: dict[str, Any]) -> list[ToolParameter]:
    if not isinstance(schema, dict):
        return []
    properties = schema.get("properties") or {}
    if not isinstance(properties, dict):
        return []
    required = set(schema.get("required") or [])

    params: list[ToolParameter] = []
    for name, prop in properties.items():
        if not isinstance(name, str) or not isinstance(prop, dict):
            continue
        schema_type = _schema_type(prop)
        param_type = _tool_param_type(schema_type)
        description = str(prop.get("description") or "")
        if schema_type in ("object", "array"):
            description = (description + " " if description else "") + "Pass as a JSON string."
        enum = prop.get("enum")
        params.append(
            ToolParameter(
                name=name,
                type=param_type,
                description=description,
                required=name in required,
                default=prop.get("default"),
                enum=enum if isinstance(enum, list) else None,
            )
        )
    return params


def invalid_xml_param_names(schema: dict[str, Any]) -> list[str]:
    properties = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(properties, dict):
        return []
    return [
        name for name in properties
        if not isinstance(name, str) or not XML_PARAM_NAME_RE.fullmatch(name)
    ]


def coerce_arguments_for_schema(params: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    arguments = dict(params)
    properties = schema.get("properties") if isinstance(schema, dict) else None
    if isinstance(properties, dict):
        for name, prop in properties.items():
            if name not in arguments or not isinstance(prop, dict):
                continue
            if _schema_type(prop) in ("object", "array") and isinstance(arguments[name], str):
                try:
                    arguments[name] = json.loads(arguments[name])
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Parameter '{name}' must be valid JSON") from exc
    try:
        validate(instance=arguments, schema=schema or {"type": "object"})
    except ValidationError as exc:
        path = ".".join(str(p) for p in exc.absolute_path)
        prefix = f"Parameter '{path}'" if path else "Parameters"
        raise ValueError(f"{prefix} failed MCP input schema validation: {exc.message}") from exc
    return arguments


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


def _schema_type(prop: dict[str, Any]) -> str:
    raw = prop.get("type")
    if isinstance(raw, list):
        raw = next((item for item in raw if item != "null"), raw[0] if raw else "string")
    return str(raw or "string")


def _tool_param_type(schema_type: str) -> str:
    if schema_type in ("integer", "number", "boolean", "string"):
        return schema_type
    if schema_type in ("object", "array"):
        return "json"
    return "string"


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


def _resource_text(resource: Any) -> str:
    text = _get_field(resource, "text")
    return text if isinstance(text, str) else ""


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
