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


# ============================================================
# 文件旁路(blob bypass)
# ============================================================

async def _public_resolve(host):
    return ["93.184.216.34"]  # 公网 IP,过 SSRF 校验


class _FakeBlobResp:
    """支持 `async with session.get(...) as resp` 的伪响应。"""

    def __init__(self, status=200, headers=None, chunks=(b"data",), content_length=None):
        self.status = status
        self.headers = headers or {}
        self.content = _FakeContent(list(chunks))
        self.content_length = content_length

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeSession:
    def __init__(self, resp):
        self._resp = resp
        self.get_calls = []

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return self._resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class TestBlobRouteHelpers:
    def test_blob_route_matching(self):
        tool = WebFetchTool()
        assert tool._blob_route_for_url("https://x.com/a.pdf")[1] == "application/pdf"
        # 大小写 + query string 不影响匹配
        assert tool._blob_route_for_url("https://x.com/a.PDF?token=1")[1] == "application/pdf"
        assert tool._blob_route_for_url("https://x.com/a.docx")[0] == ".docx"
        # 非文件类 → None(走 Jina 文本路径)
        assert tool._blob_route_for_url("https://x.com/page") is None
        assert tool._blob_route_for_url("https://x.com/article?id=5") is None

    def test_filename_from_url(self):
        tool = WebFetchTool()
        assert tool._filename_from_url("https://arxiv.org/pdf/1706.03762.pdf", ".pdf") == "1706.03762.pdf"
        # 末段为空 / 无扩展名 → download<suffix> 兜底
        assert tool._filename_from_url("https://x.com/files/", ".pdf") == "download.pdf"
        assert tool._filename_from_url("https://x.com/noext", ".docx") == "download.docx"
        # URL 编码还原
        assert tool._filename_from_url("https://x.com/my%20report.pdf", ".pdf") == "my report.pdf"


class TestBlobBypassRouting:
    async def test_file_suffix_routes_to_blob_not_jina(self, monkeypatch):
        """文件类尾缀在 Jina 之前分流到 blob 旁路 —— Jina 绝不能被调用。"""
        tool = WebFetchTool()

        async def sentinel_blob(url, suffix, mime):
            return {"success": True, "is_blob": True, "url": url, "blob": b"x",
                    "content_type": mime, "filename": "x.pdf", "fetched_at": "t"}

        async def must_not_jina(url):
            raise AssertionError("文件类 URL 不应走 Jina")

        monkeypatch.setattr(tool, "_fetch_file_as_blob", sentinel_blob)
        monkeypatch.setattr(tool, "_fetch_via_jina", must_not_jina)

        res = await tool._fetch_single_url("https://example.com/doc.pdf")
        assert res["is_blob"] is True
        assert res["success"] is True


class TestBlobBypassSSRF:
    async def test_rebind_blocked_before_connect(self, monkeypatch):
        """DNS 翻到内网 → 直连前自带校验拦下,session.get 永不触达。"""
        tool = WebFetchTool()

        async def flipped(host):
            return ["10.0.0.5"]

        def boom(*a, **k):
            raise AssertionError("SSRF 拦截后不应建立连接")

        monkeypatch.setattr(url_guard, "_resolve_host_ips", flipped)
        monkeypatch.setattr("tools.builtin.web_fetch.aiohttp.ClientSession", boom)

        res = await tool._fetch_file_as_blob(
            "http://rebind.example.com/x.pdf", ".pdf", "application/pdf"
        )
        assert res["success"] is False
        assert res["is_blob"] is True
        assert "not an allowed public address" in res["error"]

    async def test_metadata_ip_literal_blocked(self, monkeypatch):
        """直连元数据地址(169.254.169.254 IP 字面量)被 SSRF 校验拦下,不触网。"""
        tool = WebFetchTool()

        def boom(*a, **k):
            raise AssertionError("不应连接元数据地址")

        monkeypatch.setattr("tools.builtin.web_fetch.aiohttp.ClientSession", boom)

        res = await tool._fetch_file_as_blob(
            "http://169.254.169.254/latest/meta-data.pdf", ".pdf", "application/pdf"
        )
        assert res["success"] is False
        assert "not an allowed public address" in res["error"]

    async def test_302_not_followed(self, monkeypatch):
        """allow_redirects=False → 302→内网 不被跟随,直接当失败返回(没连上内网)。"""
        tool = WebFetchTool()
        monkeypatch.setattr(url_guard, "_resolve_host_ips", _public_resolve)
        resp = _FakeBlobResp(status=302, headers={"Location": "http://10.0.0.1/secret"})
        monkeypatch.setattr(
            "tools.builtin.web_fetch.aiohttp.ClientSession",
            lambda *a, **k: _FakeSession(resp),
        )
        res = await tool._fetch_file_as_blob(
            "https://example.com/a.pdf", ".pdf", "application/pdf"
        )
        assert res["success"] is False
        assert "302" in res["error"]


class TestBlobContentType:
    async def _run(self, monkeypatch, headers, fallback_mime):
        tool = WebFetchTool()
        monkeypatch.setattr(url_guard, "_resolve_host_ips", _public_resolve)
        resp = _FakeBlobResp(status=200, headers=headers, chunks=[b"%PDF-1.4"])
        monkeypatch.setattr(
            "tools.builtin.web_fetch.aiohttp.ClientSession",
            lambda *a, **k: _FakeSession(resp),
        )
        return await tool._fetch_file_as_blob("https://example.com/file.bin", ".docx", fallback_mime)

    async def test_header_preferred_charset_stripped(self, monkeypatch):
        res = await self._run(monkeypatch, {"Content-Type": "application/pdf; charset=binary"}, "FB")
        assert res["success"] is True
        assert res["content_type"] == "application/pdf"  # 头优先,charset 剥掉
        assert res["blob"] == b"%PDF-1.4"

    async def test_octet_stream_falls_back(self, monkeypatch):
        res = await self._run(monkeypatch, {"Content-Type": "application/octet-stream"}, "FALLBACK_MIME")
        assert res["content_type"] == "FALLBACK_MIME"  # 通用值不可信 → 尾缀兜底

    async def test_missing_header_falls_back(self, monkeypatch):
        res = await self._run(monkeypatch, {}, "FALLBACK_MIME")
        assert res["content_type"] == "FALLBACK_MIME"
