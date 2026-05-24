"""
SSRF URL 守卫测试（utils/url_guard）。

覆盖：
- ip_is_blocked：各类内网 / 特殊地址拒绝、公网放行、IPv4-mapped IPv6 还原
- validate_public_url：scheme、IP 字面量、字面主机黑名单、DNS 解析到内网
"""

import socket

import pytest

from utils.url_guard import (
    ip_is_blocked,
    validate_public_url,
    safe_url_label,
    SsrfBlockedError,
)
from utils import url_guard


# ============================================================
# ip_is_blocked
# ============================================================

class TestIpIsBlocked:
    @pytest.mark.parametrize("ip", [
        "127.0.0.1",        # loopback
        "10.0.0.1",         # 私网 10/8
        "172.16.0.1",       # 私网 172.16/12 下界
        "172.31.255.255",   # 私网 172.16/12 上界
        "192.168.1.1",      # 私网 192.168/16
        "169.254.169.254",  # link-local / 云元数据
        "0.0.0.0",          # unspecified
        "224.0.0.1",        # multicast
        "::1",              # IPv6 loopback
        "fe80::1",          # IPv6 link-local
        "fc00::1",          # IPv6 ULA
        "fd00::1",          # IPv6 ULA
        "::ffff:169.254.169.254",  # IPv4-mapped link-local
        "::ffff:10.0.0.1",         # IPv4-mapped 私网
        "not-an-ip",        # 非法字面量 → 保守拒绝
    ])
    def test_blocked(self, ip):
        assert ip_is_blocked(ip) is True

    @pytest.mark.parametrize("ip", [
        "8.8.8.8",
        "1.1.1.1",
        "93.184.216.34",        # example.com
        "172.15.0.1",           # 172.16/12 之外 → 公网
        "172.32.0.1",           # 172.16/12 之外 → 公网
        "2606:4700:4700::1111",  # 公网 IPv6
        # 以下是 fake-IP 代理(Clash/Surge/sing-box)给域名分配的占位段 + TEST-NET，
        # 不是真实内网服务段，必须放行，否则代理环境下一切外联误伤。
        "198.18.0.29",          # 198.18.0.0/15 基准测试段（fake-IP 常用）
        "198.51.100.1",         # TEST-NET-2
        "203.0.113.5",          # TEST-NET-3
    ])
    def test_allowed(self, ip):
        assert ip_is_blocked(ip) is False


# ============================================================
# validate_public_url
# ============================================================

class TestValidatePublicUrl:
    async def test_rejects_non_http_scheme(self):
        for url in ["ftp://example.com/x", "file:///etc/passwd", "gopher://x"]:
            with pytest.raises(SsrfBlockedError, match="scheme"):
                await validate_public_url(url)

    async def test_rejects_no_host(self):
        with pytest.raises(SsrfBlockedError):
            await validate_public_url("http://")

    async def test_rejects_internal_ip_literal(self):
        with pytest.raises(SsrfBlockedError, match="Blocked IP literal"):
            await validate_public_url("http://169.254.169.254/latest/meta-data/")

    async def test_allows_public_ip_literal(self):
        # 公网 IP 字面量无需 DNS，直接通过
        await validate_public_url("http://8.8.8.8/")

    async def test_rejects_localhost(self):
        with pytest.raises(SsrfBlockedError, match="Blocked host"):
            await validate_public_url("http://localhost/admin")

    async def test_rejects_internal_suffix(self):
        for url in ["http://foo.internal/x", "http://db.local/"]:
            with pytest.raises(SsrfBlockedError, match="Blocked host"):
                await validate_public_url(url)

    async def test_rejects_dns_resolving_to_internal(self, monkeypatch):
        async def fake_resolve(host):
            return ["10.0.0.5"]
        monkeypatch.setattr(url_guard, "_resolve_host_ips", fake_resolve)
        with pytest.raises(SsrfBlockedError, match="non-public"):
            await validate_public_url("http://rebind.example.com/")

    async def test_rejects_when_any_resolved_ip_internal(self, monkeypatch):
        # 一公一私混合也必须拒（防只校验首条记录）
        async def fake_resolve(host):
            return ["93.184.216.34", "127.0.0.1"]
        monkeypatch.setattr(url_guard, "_resolve_host_ips", fake_resolve)
        with pytest.raises(SsrfBlockedError, match="non-public"):
            await validate_public_url("http://mixed.example.com/")

    async def test_allows_dns_resolving_to_public(self, monkeypatch):
        async def fake_resolve(host):
            return ["93.184.216.34"]
        monkeypatch.setattr(url_guard, "_resolve_host_ips", fake_resolve)
        await validate_public_url("http://example.com/page")

    async def test_dns_failure_blocked(self, monkeypatch):
        async def fake_resolve(host):
            raise socket.gaierror("nope")
        monkeypatch.setattr(url_guard, "_resolve_host_ips", fake_resolve)
        with pytest.raises(SsrfBlockedError, match="DNS resolution failed"):
            await validate_public_url("http://nxdomain.example.com/")


# ============================================================
# safe_url_label —— 脱敏(防 endpoint 密钥经 metadata 泄露)
# ============================================================

class TestSafeUrlLabel:
    def test_strips_query_secret(self):
        assert safe_url_label(
            "https://api.example.com/v1/data?api_key=SUPERSECRET"
        ) == "https://api.example.com"

    def test_strips_userinfo(self):
        assert safe_url_label(
            "https://user:pass@api.example.com/x"
        ) == "https://api.example.com"

    def test_keeps_port(self):
        assert safe_url_label(
            "https://api.example.com:8443/x?k=s"
        ) == "https://api.example.com:8443"

    def test_no_secret_substring_survives(self):
        for url in [
            "https://h.example.com/p?token=ABC123SECRET",
            "https://k3y:s3cr3t@h.example.com/",
            "https://h.example.com/ABC123SECRET/x",
        ]:
            assert "SECRET" not in safe_url_label(url)
            assert "s3cr3t" not in safe_url_label(url)

    def test_garbage_returns_empty(self):
        assert safe_url_label("not a url") == ""
