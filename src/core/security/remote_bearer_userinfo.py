"""Bounded HTTP client and strict DTO normalization for remote userinfo."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from core.security.remote_bearer_config import RemoteBearerConfig
from utils.tls import create_outbound_ssl_context
from utils.validators import validate_username


MAX_BEARER_TOKEN_CHARS = 16_384
MAX_USERINFO_RESPONSE_BYTES = 1024 * 1024
MAX_DEPARTMENT_PATH_CHARS = 2_048
MAX_DEPARTMENT_DEPTH = 32
MAX_DEPARTMENT_SEGMENT_CHARS = 128
_MISSING = object()


class RemoteBearerUserInfoError(Exception):
    """Base exception with a safe, non-secret diagnostic."""


class RemoteBearerCredentialsRejected(RemoteBearerUserInfoError):
    pass


class RemoteBearerUpstreamUnavailable(RemoteBearerUserInfoError):
    pass


class RemoteBearerProtocolError(RemoteBearerUserInfoError):
    pass


@dataclass(frozen=True)
class NormalizedRemoteIdentity:
    subject: str
    username: str
    display_name: str | None
    enabled: bool
    department_path: tuple[str, ...] | None


def _read_object_path(data: Any, path: str) -> Any:
    cursor = data
    for segment in path.split("."):
        if not isinstance(cursor, dict) or segment not in cursor:
            return _MISSING
        cursor = cursor[segment]
    return cursor


def _required_value(data: Any, path: str, name: str) -> Any:
    value = _read_object_path(data, path)
    if value is _MISSING or value is None:
        raise RemoteBearerProtocolError(f"userinfo is missing required field {name}")
    return value


def _optional_department_text(value: Any, name: str) -> str | None:
    if value is _MISSING or value is None:
        return None
    if not isinstance(value, str):
        raise RemoteBearerProtocolError(f"userinfo field {name} must be a string or null")
    cleaned = value.strip()
    return cleaned or None


def normalize_remote_userinfo(
    payload: Any, config: RemoteBearerConfig
) -> NormalizedRemoteIdentity:
    """Map an upstream object to the only identity shape accepted by JIT sync."""

    if not isinstance(payload, dict):
        raise RemoteBearerProtocolError("userinfo response root must be an object")
    if not config.enabled or config.userinfo is None:
        raise RemoteBearerProtocolError("remote bearer provider is disabled")

    fields = config.userinfo.fields
    subject_value = _required_value(payload, fields.subject, "subject")
    if isinstance(subject_value, bool) or not isinstance(subject_value, (str, int)):
        raise RemoteBearerProtocolError("userinfo subject must be a string or integer")
    subject = str(subject_value)
    if subject != subject.strip():
        raise RemoteBearerProtocolError(
            "userinfo subject must not contain leading or trailing whitespace"
        )
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in subject):
        raise RemoteBearerProtocolError("userinfo subject must not contain control characters")
    if not subject or len(subject) > 256:
        raise RemoteBearerProtocolError("userinfo subject must contain 1-256 characters")

    username_value = _required_value(payload, fields.username, "username")
    if not isinstance(username_value, str):
        raise RemoteBearerProtocolError("userinfo username must be a string")
    username = username_value.strip()
    try:
        validate_username(username)
    except ValueError as exc:
        raise RemoteBearerProtocolError(f"invalid userinfo username: {exc}") from exc

    display_value = _read_object_path(payload, fields.display_name)
    if display_value is _MISSING or display_value is None:
        display_name = None
    elif not isinstance(display_value, str):
        raise RemoteBearerProtocolError("userinfo display_name must be a string or null")
    else:
        display_name = display_value.strip() or None
        if display_name is not None and len(display_name) > 128:
            raise RemoteBearerProtocolError(
                "userinfo display_name must contain at most 128 characters"
            )

    enabled = _required_value(payload, fields.enabled, "enabled")
    if type(enabled) is not bool:
        raise RemoteBearerProtocolError("userinfo enabled must be a boolean")

    raw_path = _optional_department_text(
        _read_object_path(payload, fields.department_path), "department_path"
    )
    raw_leaf = _optional_department_text(
        _read_object_path(payload, fields.department_leaf), "department_leaf"
    )
    if raw_path is None and raw_leaf is None:
        department_path = None
    elif raw_path is None or raw_leaf is None:
        raise RemoteBearerProtocolError(
            "userinfo department_path and department_leaf must both be present or absent"
        )
    else:
        if len(raw_path) > MAX_DEPARTMENT_PATH_CHARS:
            raise RemoteBearerProtocolError("userinfo department path is too long")
        segments = tuple(segment.strip() for segment in raw_path.split(config.userinfo.department_separator))
        if not segments or any(not segment for segment in segments):
            raise RemoteBearerProtocolError("userinfo department path contains an empty segment")
        if len(segments) > MAX_DEPARTMENT_DEPTH:
            raise RemoteBearerProtocolError("userinfo department path is too deep")
        if any(len(segment) > MAX_DEPARTMENT_SEGMENT_CHARS for segment in segments):
            raise RemoteBearerProtocolError("userinfo department path segment is too long")
        if segments[-1] != raw_leaf:
            raise RemoteBearerProtocolError(
                "userinfo department leaf does not match the final path segment"
            )
        department_path = segments

    return NormalizedRemoteIdentity(
        subject=subject,
        username=username,
        display_name=display_name,
        enabled=enabled,
        department_path=department_path,
    )


class RemoteBearerUserInfoClient:
    """One fixed-origin bearer GET client; redirects and environment auth are off."""

    def __init__(self, provider_config: RemoteBearerConfig, *, max_connections: int):
        if not provider_config.enabled or provider_config.userinfo is None:
            raise ValueError("Cannot create userinfo client for a disabled provider")
        if max_connections <= 0:
            raise ValueError("max_connections must be positive")
        self._config = provider_config
        info = provider_config.userinfo
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=info.connect_timeout_seconds,
                read=info.read_timeout_seconds,
                write=info.connect_timeout_seconds,
                pool=info.connect_timeout_seconds,
            ),
            follow_redirects=False,
            verify=create_outbound_ssl_context(),
            trust_env=False,
            limits=httpx.Limits(
                max_connections=max_connections,
                max_keepalive_connections=max_connections,
            ),
        )

    async def close(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _normalize_token(raw_token: str) -> str:
        if not isinstance(raw_token, str):
            raise RemoteBearerCredentialsRejected("Upstream credential is invalid")
        token = raw_token.strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        if (
            not token
            or len(token) > MAX_BEARER_TOKEN_CHARS
            or any(ord(char) < 0x21 or ord(char) > 0x7E for char in token)
        ):
            raise RemoteBearerCredentialsRejected("Upstream credential is invalid")
        return token

    async def fetch(self, raw_token: str) -> NormalizedRemoteIdentity:
        token = self._normalize_token(raw_token)
        assert self._config.userinfo is not None
        try:
            async with self._client.stream(
                "GET",
                self._config.userinfo.url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
            ) as response:
                if response.status_code in {401, 403}:
                    raise RemoteBearerCredentialsRejected(
                        "Upstream credential was rejected"
                    )
                if response.status_code >= 500:
                    raise RemoteBearerUpstreamUnavailable(
                        f"userinfo upstream returned HTTP {response.status_code}"
                    )
                if response.status_code != 200:
                    raise RemoteBearerProtocolError(
                        f"userinfo upstream returned unexpected HTTP {response.status_code}"
                    )
                declared = response.headers.get("content-length")
                if declared is not None:
                    try:
                        declared_size = int(declared)
                        if declared_size < 0:
                            raise ValueError
                        if declared_size > MAX_USERINFO_RESPONSE_BYTES:
                            raise RemoteBearerProtocolError("userinfo response is too large")
                    except ValueError as exc:
                        raise RemoteBearerProtocolError(
                            "userinfo response has invalid Content-Length"
                        ) from exc
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > MAX_USERINFO_RESPONSE_BYTES:
                        raise RemoteBearerProtocolError("userinfo response is too large")
                    chunks.append(chunk)
        except RemoteBearerUserInfoError:
            raise
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise RemoteBearerUpstreamUnavailable(
                f"userinfo upstream request failed: {type(exc).__name__}"
            ) from exc
        except httpx.HTTPError as exc:
            raise RemoteBearerUpstreamUnavailable(
                f"userinfo upstream transport failed: {type(exc).__name__}"
            ) from exc

        try:
            payload = json.loads(b"".join(chunks))
        except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as exc:
            raise RemoteBearerProtocolError("userinfo response is not valid JSON") from exc
        return normalize_remote_userinfo(payload, self._config)
