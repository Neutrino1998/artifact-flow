"""HttpTool 运行期凭证注入(B-4):snapshot 已解密的纯 dict 路径 + env 回落 + 缺凭证。"""

import os
from unittest.mock import patch

import pytest

from tools.custom.http_tool import HttpTool, HttpToolConfig


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


@pytest.fixture
def _fake_client(monkeypatch):
    _CapturingClient.last = {}
    monkeypatch.setattr("tools.custom.http_tool.httpx.AsyncClient", _CapturingClient)


def _tool(resolved_credentials):
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
        resolved_credentials=resolved_credentials,
    )


async def test_resolved_credentials_substituted_into_request(_fake_client):
    # snapshot 已把本 unit 凭证解密成纯 dict;execute 纯替换(无 DB / await)
    result = await _tool(
        {"TOOL_SECRET_HOST": "host.local", "TOOL_SECRET_KEY": "live-key"}
    ).execute()
    assert result.success is True
    assert _CapturingClient.last["url"] == "https://host.local/api"
    assert _CapturingClient.last["headers"]["Authorization"] == "Bearer live-key"


async def test_missing_credential_is_generic_error(_fake_client):
    # 占位符无对应凭证(未配 / snapshot 解密失败被跳过)→ SecretResolutionError → generic
    # 错误,绝不外发占位符原文
    result = await _tool({"TOOL_SECRET_HOST": "host.local"}).execute()   # 缺 KEY
    assert result.success is False
    assert "required secret is unavailable" in result.error
    assert "TOOL_SECRET_KEY" not in result.error              # 占位符不回显给模型


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
