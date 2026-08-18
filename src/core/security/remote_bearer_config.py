"""Startup-loaded configuration for the remote bearer userinfo provider.

The provider is deliberately a single, fixed-shape integration.  It is not a
general expression or authentication plugin system: field mappings are simple
dot-separated object paths and configuration changes take effect on restart.
"""

from __future__ import annotations

import ipaddress
import re
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qsl, urlsplit

import httpx
import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.security.identity import LOCAL_AUTH_PROVIDER


DEFAULT_REMOTE_BEARER_CONFIG_PATH = (
    Path(__file__).resolve().parents[3]
    / "config"
    / "auth"
    / "remote_bearer_userinfo.yaml"
)
_PROVIDER_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
_FIELD_PATH_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
_QUERY_PARAM_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$")
_DNS_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_INVALID_PERCENT_RE = re.compile(r"%(?![0-9A-Fa-f]{2})")


class RemoteBearerConfigError(ValueError):
    """The provider YAML is present but invalid."""


def _validate_http_url(name: str, value: str, *, allow_insecure_http: bool) -> None:
    """Validate the fixed outbound/browser URL using the runtime HTTP parser."""

    if any(
        char.isspace() or ord(char) < 0x20 or ord(char) == 0x7F
        for char in value
    ):
        raise ValueError(f"{name} must not contain whitespace or control characters")
    if _INVALID_PERCENT_RE.search(value):
        raise ValueError(f"{name} contains an invalid percent escape")
    try:
        parsed = httpx.URL(value)
        stdlib_parsed = urlsplit(value)
        # Force both parsers to validate the port while configuration is loading.
        parsed.port
        stdlib_parsed.port
    except (httpx.InvalidURL, ValueError) as exc:
        raise ValueError(f"{name} is not a valid URL") from exc

    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.host
        or stdlib_parsed.username is not None
        or stdlib_parsed.password is not None
        or stdlib_parsed.fragment
    ):
        raise ValueError(
            f"{name} must be an absolute HTTP/HTTPS URL without credentials or fragment"
        )

    host = parsed.host
    try:
        ipaddress.ip_address(host)
    except ValueError:
        ascii_host = parsed.raw_host.decode("ascii").removesuffix(".")
        labels = ascii_host.split(".")
        if (
            not ascii_host
            or len(ascii_host) > 253
            or any(not _DNS_LABEL_RE.fullmatch(label) for label in labels)
        ):
            raise ValueError(f"{name} contains an invalid hostname") from None

    if parsed.scheme == "http" and not allow_insecure_http:
        raise ValueError(
            f"{name} uses HTTP; set userinfo.allow_insecure_http=true explicitly"
        )


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RemoteBearerProvider(_StrictModel):
    id: str
    display_name: str = Field(min_length=1, max_length=128)
    type: str = "remote_bearer_userinfo"

    @model_validator(mode="after")
    def validate_provider(self) -> "RemoteBearerProvider":
        if not _PROVIDER_ID_RE.fullmatch(self.id):
            raise ValueError(
                "provider.id must match ^[a-z][a-z0-9_-]{1,63}$"
            )
        if self.id == LOCAL_AUTH_PROVIDER:
            raise ValueError(f"provider.id cannot be reserved value {LOCAL_AUTH_PROVIDER!r}")
        if self.type != "remote_bearer_userinfo":
            raise ValueError("provider.type must be 'remote_bearer_userinfo'")
        if self.display_name != self.display_name.strip():
            raise ValueError("provider.display_name must not have surrounding whitespace")
        return self


class RemoteBearerLogin(_StrictModel):
    url: str
    callback_url: str
    return_param: str = Field(min_length=1, max_length=64)
    token_param: str = Field(min_length=1, max_length=64)
    state_ttl_seconds: int = Field(default=300, ge=30, le=900)

    @model_validator(mode="after")
    def validate_parameter_names(self) -> "RemoteBearerLogin":
        for name, value in (
            ("return_param", self.return_param),
            ("token_param", self.token_param),
        ):
            if not _QUERY_PARAM_RE.fullmatch(value):
                raise ValueError(f"login.{name} is not a valid query parameter name")
        return self


class RemoteBearerFieldMappings(_StrictModel):
    subject: str
    username: str
    display_name: str
    enabled: str
    department_path: str
    department_leaf: str

    @model_validator(mode="after")
    def validate_paths(self) -> "RemoteBearerFieldMappings":
        for name, path in self.model_dump().items():
            if not _FIELD_PATH_RE.fullmatch(path):
                raise ValueError(
                    f"userinfo.fields.{name} must be a dot-separated object path"
                )
        return self


class RemoteBearerUserInfo(_StrictModel):
    url: str
    connect_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    read_timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    allow_insecure_http: bool = False
    department_separator: str = Field(default="/", min_length=1, max_length=8)
    fields: RemoteBearerFieldMappings


class RemoteBearerConfig(_StrictModel):
    version: int = 1
    enabled: bool = False
    provider: Optional[RemoteBearerProvider] = None
    login: Optional[RemoteBearerLogin] = None
    userinfo: Optional[RemoteBearerUserInfo] = None

    @model_validator(mode="after")
    def validate_contract(self) -> "RemoteBearerConfig":
        if self.version != 1:
            raise ValueError("version must be 1")
        if not self.enabled:
            return self
        if self.provider is None or self.login is None or self.userinfo is None:
            raise ValueError(
                "enabled provider requires provider, login, and userinfo sections"
            )

        urls = {
            "login.url": self.login.url,
            "login.callback_url": self.login.callback_url,
            "userinfo.url": self.userinfo.url,
        }
        for name, value in urls.items():
            _validate_http_url(
                name,
                value,
                allow_insecure_http=self.userinfo.allow_insecure_http,
            )

        if self.login.token_param == "af_sso_state":
            raise ValueError("login.token_param cannot use reserved name af_sso_state")

        login_query = urlsplit(self.login.url).query
        login_names = {name for name, _ in parse_qsl(login_query, keep_blank_values=True)}
        if self.login.return_param in login_names:
            raise ValueError("login.url must not predefine login.return_param")
        callback_query = urlsplit(self.login.callback_url).query
        callback_names = {
            name for name, _ in parse_qsl(callback_query, keep_blank_values=True)
        }
        if self.login.token_param in callback_names or "af_sso_state" in callback_names:
            raise ValueError(
                "login.callback_url must not predefine token_param or af_sso_state"
            )
        return self


def load_remote_bearer_config(
    path: Path = DEFAULT_REMOTE_BEARER_CONFIG_PATH,
) -> RemoteBearerConfig:
    """Load the fixed provider file; a missing file means SSO is disabled."""

    if not path.exists():
        return RemoteBearerConfig()
    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise RemoteBearerConfigError(f"Cannot read {path}: {exc}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise RemoteBearerConfigError(f"{path} root must be a YAML object")
    try:
        return RemoteBearerConfig.model_validate(raw)
    except ValueError as exc:
        raise RemoteBearerConfigError(f"Invalid {path}: {exc}") from exc
