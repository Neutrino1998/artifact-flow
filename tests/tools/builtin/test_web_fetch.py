"""
web_fetch fallback 下载体封顶测试（SSRF-04）。

_read_capped 不依赖网络：用伪 response 验证 Content-Length 预检 + 流式累计中断。
"""

import pytest

from tools.builtin.web_fetch import _read_capped, _ResponseTooLargeError, WebFetchTool
from utils import url_guard


class _FakeContent:
    def __init__(self, chunks):
        self._chunks = chunks

    async def iter_chunked(self, _n):
        for chunk in self._chunks:
            yield chunk


class _FakeResponse:
    def __init__(self, chunks, content_length=None):
        self.content = _FakeContent(chunks)
        self.content_length = content_length


class TestReadCapped:
    async def test_under_limit_returns_full_body(self):
        resp = _FakeResponse([b"a" * 100, b"b" * 100])
        out = await _read_capped(resp, max_bytes=1000)
        assert out == b"a" * 100 + b"b" * 100

    async def test_content_length_precheck_aborts_early(self):
        # 声明的 Content-Length 超限 → 不读 body 直接中断
        resp = _FakeResponse([b"x"], content_length=10_000)
        with pytest.raises(_ResponseTooLargeError):
            await _read_capped(resp, max_bytes=1000)

    async def test_streaming_abort_when_length_unknown(self):
        # Content-Length 缺失（chunked / gzip 解压后膨胀）→ 累计字节超限即中断
        resp = _FakeResponse([b"x" * 600, b"x" * 600], content_length=None)
        with pytest.raises(_ResponseTooLargeError):
            await _read_capped(resp, max_bytes=1000)

    async def test_exact_limit_ok(self):
        resp = _FakeResponse([b"x" * 1000], content_length=1000)
        out = await _read_capped(resp, max_bytes=1000)
        assert len(out) == 1000


class TestFallbackRebindGuard:
    async def test_revalidates_before_direct_connect(self, monkeypatch):
        # 模拟:入口校验通过(公网)→ Jina 失败 → DNS 翻到内网 → 直连前重校验拦下,
        # fallback 绝不能被调用(否则就连上了内网)。
        tool = WebFetchTool()

        async def fake_jina(url):
            return None  # Jina 失败,进入 fallback 分支

        async def flipped_resolve(host):
            return ["10.0.0.5"]  # rebinding:此刻已翻到内网

        async def must_not_run(url):
            raise AssertionError("fallback 不应在 rebinding 拦截后运行")

        monkeypatch.setattr(tool, "_fetch_via_jina", fake_jina)
        monkeypatch.setattr(url_guard, "_resolve_host_ips", flipped_resolve)
        monkeypatch.setattr(tool, "_fetch_via_bs4", must_not_run)
        monkeypatch.setattr(tool, "_fetch_pdf", must_not_run)

        result = await tool._fetch_single_url("http://rebind.example.com/page")
        assert result["success"] is False
        assert "not an allowed public address" in result["error"]
