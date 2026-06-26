"""HttpTool 运行期凭证注入(B-4):DB resolver 路径 + env 回落 + 解析失败。"""

import os
from unittest.mock import patch

import pytest

from tools.custom.http_tool import HttpTool, HttpToolConfig
from tools.custom.secrets import SecretResolutionError


class _CapturingResponse:
    def __init__(self):
        self.status_code = 200
        self.headers = {"content-type": "application/json"}
        self.text = '{"ok": 1}'

    def raise_for_status(self):
        pass

    def json(self):
        return {"ok": 1}


class _CapturingClient:
    """捕获最后一次请求的 url/headers,断言凭证已被替换进出站请求。"""
    last = {}

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def request(self, method, url, **kwargs):
        _CapturingClient.last = {"method": method, "url": url, "headers": kwargs.get("headers")}
        return _CapturingResponse()


class _StubResolver:
    def __init__(self, values=None, error=None):
        self._values = values or {}
        self._error = error
        self.calls = []

    async def resolve(self, unit_name):
        self.calls.append(unit_name)
        if self._error:
            raise self._error
        return self._values


@pytest.fixture
def _fake_client(monkeypatch):
    _CapturingClient.last = {}
    monkeypatch.setattr("tools.custom.http_tool.httpx.AsyncClient", _CapturingClient)


def _tool(resolver, *, unit_name="ragflow"):
    return HttpTool(
        HttpToolConfig(
            name="ragflow__query",
            description="q",
            permission="auto",
            endpoint="https://{{TOOL_SECRET_HOST}}/api",
            method="GET",
            headers={"Authorization": "Bearer {{TOOL_SECRET_KEY}}"},
            parameters=[],
        ),
        unit_name=unit_name,
        credential_resolver=resolver,
    )


async def test_db_resolver_substitutes_into_request(_fake_client):
    resolver = _StubResolver({"TOOL_SECRET_HOST": "host.local", "TOOL_SECRET_KEY": "live-key"})
    result = await _tool(resolver).execute()
    assert result.success is True
    assert resolver.calls == ["ragflow"]                      # 按 unit 名解析
    assert _CapturingClient.last["url"] == "https://host.local/api"
    assert _CapturingClient.last["headers"]["Authorization"] == "Bearer live-key"


async def test_resolver_failure_is_generic_error(_fake_client):
    # 解密/主密钥失败 → SecretResolutionError → generic 错误,绝不外发占位符
    resolver = _StubResolver(error=SecretResolutionError("master key missing"))
    result = await _tool(resolver).execute()
    assert result.success is False
    assert "required secret is unavailable" in result.error
    assert "master key" not in result.error                   # 内部细节不回显给模型


async def test_env_fallback_when_no_resolver(_fake_client):
    # 无 resolver / 无 unit 上下文(legacy loader / 直接构造)→ env 解析(白名单前缀)
    tool = HttpTool(HttpToolConfig(
        name="probe",
        description="p",
        permission="auto",
        endpoint="https://api/{{TOOL_SECRET_PATH}}",
        method="GET",
        parameters=[],
    ))
    with patch.dict(os.environ, {"TOOL_SECRET_PATH": "v1"}):
        result = await tool.execute()
    assert result.success is True
    assert _CapturingClient.last["url"] == "https://api/v1"
