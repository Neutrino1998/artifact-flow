"""
SSRF 防护：校验外联目标 URL 指向公网地址。

`web_fetch` / `http_tool` 共用。单层 pre-flight 校验：

`validate_public_url(url)` —— 解析 URL → 主机 → 全部解析 IP 逐个查，scheme 非
http(s)、主机为内网字面名、或任一 IP 命中危险网段即拒。调用方在**真正发起请求前**
调一次；并把外联客户端设为不跟随重定向（aiohttp `allow_redirects=False` /
httpx `follow_redirects=False`），这样 `302 → 内网` 也不会被跟。

**已知残留（best-effort，刻意不修）：** pre-flight 解析与实际 connect 之间存在
DNS-rebinding TOCTOU（域名 DNS 在两步之间翻转到内网）。关掉它需要连接时校验的
自定义 resolver，复杂度不划算；高价值目标（云元数据 / 内网）走 IP 字面量或稳定
DNS 时已被拦，rebinding 属罕见边角，接受。改动史见 docs/_archive/reviews/sec-review-findings.md。
"""

import asyncio
import ipaddress
import socket

from urllib.parse import urlsplit


# DNS 解析前先拦的字面主机名(省一次 getaddrinfo,也覆盖不解析为 IP 的特殊名)
_BLOCKED_HOSTNAMES = {"localhost", "metadata.google.internal"}
_BLOCKED_SUFFIXES = (".internal", ".local")

# 明确枚举"危险"网段 —— SSRF 真正的高价值目标:loopback / link-local(含云元数据
# 169.254.169.254)/ RFC1918 私网 / CGNAT / IPv6 ULA / link-local / multicast。
#
# 刻意**不**用 ipaddress 的 is_private / is_reserved 全集:那会连带拦掉
# 198.18.0.0/15(基准测试段,RFC2544)与 TEST-NET / 240.0.0.0/4。这些不是真实
# 内网服务段,却正是 fake-IP 代理(Clash / Surge / sing-box)给**域名**分配的
# 占位 IP —— 拦它们会在代理环境下误伤一切外联(github.com 解析成 198.18.x.x 即被拒)。
# 真正的内网/元数据攻击面是下列段 + IP 字面量,fake-IP 不会落在这些段里。
# 改回 is_private/is_reserved 前请先想清楚 fake-IP 误伤问题。
_BLOCKED_V4_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),       # "this host" / unspecified
    ipaddress.ip_network("10.0.0.0/8"),      # RFC1918
    ipaddress.ip_network("100.64.0.0/10"),   # CGNAT (RFC6598)
    ipaddress.ip_network("127.0.0.0/8"),     # loopback
    ipaddress.ip_network("169.254.0.0/16"),  # link-local + 云元数据
    ipaddress.ip_network("172.16.0.0/12"),   # RFC1918
    ipaddress.ip_network("192.168.0.0/16"),  # RFC1918
    ipaddress.ip_network("224.0.0.0/4"),     # multicast
]
_BLOCKED_V6_NETWORKS = [
    ipaddress.ip_network("::1/128"),    # loopback
    ipaddress.ip_network("::/128"),     # unspecified
    ipaddress.ip_network("fc00::/7"),   # ULA
    ipaddress.ip_network("fe80::/10"),  # link-local
    ipaddress.ip_network("ff00::/8"),   # multicast
]


class SsrfBlockedError(ValueError):
    """目标 URL 指向非公网地址(loopback / 私网 / link-local / metadata 等)。"""


def ip_is_blocked(ip_str: str) -> bool:
    """判断单个 IP 是否不可作为外联目标(命中危险网段即拒)。

    无法解析的字面量保守拒绝。见上方网段定义注释了解为何不用 is_private 全集。
    """
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True

    # IPv4-mapped IPv6(::ffff:169.254.169.254)→ 还原为 v4 再判
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped

    networks = _BLOCKED_V4_NETWORKS if ip.version == 4 else _BLOCKED_V6_NETWORKS
    return any(ip in net for net in networks)


def _hostname_is_blocked(host: str) -> bool:
    h = host.lower().rstrip(".")
    if h in _BLOCKED_HOSTNAMES:
        return True
    return any(h.endswith(suffix) for suffix in _BLOCKED_SUFFIXES)


async def _resolve_host_ips(host: str) -> list[str]:
    """解析主机名为 IP 列表(A + AAAA)。抽成独立函数便于测试 monkeypatch。"""
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return [info[4][0] for info in infos]


async def validate_public_url(url: str) -> None:
    """pre-flight 校验:URL 必须是 http(s) 且主机解析后全部 IP 均为公网。

    Raises:
        SsrfBlockedError: scheme 非 http(s)、主机为内网字面名 / IP、
            DNS 解析失败、或解析出的任一 IP 非公网。
    """
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise SsrfBlockedError(f"Unsupported URL scheme: {scheme or '(none)'}")

    host = parts.hostname
    if not host:
        raise SsrfBlockedError("URL has no host")

    if _hostname_is_blocked(host):
        raise SsrfBlockedError(f"Blocked host: {host}")

    # 主机是 IP 字面量 → 直接判,不走 DNS
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if ip_is_blocked(host):
            raise SsrfBlockedError(f"Blocked IP literal: {host}")
        return

    try:
        ips = await _resolve_host_ips(host)
    except socket.gaierror as e:
        raise SsrfBlockedError(f"DNS resolution failed for {host}") from e

    if not ips:
        raise SsrfBlockedError(f"DNS resolution returned no records for {host}")

    # 不回显具体 IP(SSRF-06):只说明"解析到非公网"。
    for ip_str in ips:
        if ip_is_blocked(ip_str):
            raise SsrfBlockedError(f"Host {host} resolves to a non-public address")


def safe_url_label(url: str) -> str:
    """脱敏的 URL 标签:仅 `scheme://host[:port]`,丢弃 userinfo / path / query。

    用于日志 / 事件 metadata —— endpoint 经 `{{TOOL_SECRET_*}}` 解析后可能把真实
    密钥带进 query 或 userinfo(如 `?key=REAL` / `user:pass@host`),原样回显会经
    SSE → 浏览器、`MessageEvent.data` → DB/事件历史 泄露。保留 host 供调试。
    """
    parts = urlsplit(url)
    host = parts.hostname or ""
    if host and parts.port:
        host = f"{host}:{parts.port}"
    return f"{parts.scheme}://{host}" if host else ""
