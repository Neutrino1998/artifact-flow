"""工具凭证加密 + 运行期解析单测(B-4;B-5 lazy),纯单元、不碰 DB(用 fake repo/db)。"""

from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from tools.custom.credentials import (
    CredentialCipher,
    CredentialKeyError,
    CredentialResolver,
)
from tools.custom.secrets import SecretResolutionError


def _key() -> str:
    return Fernet.generate_key().decode()


# --------------------------------------------------------------------------
# CredentialCipher
# --------------------------------------------------------------------------


def test_cipher_round_trip():
    c = CredentialCipher(_key())
    token = c.encrypt("super-secret-key")
    assert token != "super-secret-key"          # 真加密、非明文
    assert c.decrypt(token) == "super-secret-key"


def test_cipher_empty_key_raises():
    with pytest.raises(CredentialKeyError, match="not set"):
        CredentialCipher("")


def test_cipher_invalid_key_raises():
    with pytest.raises(CredentialKeyError, match="valid Fernet key"):
        CredentialCipher("not-a-fernet-key")


def test_cipher_cross_key_cannot_decrypt():
    a, b = CredentialCipher(_key()), CredentialCipher(_key())
    token = a.encrypt("x")
    with pytest.raises(Exception):  # InvalidToken — 换主密钥即废密文
        b.decrypt(token)


# --------------------------------------------------------------------------
# CredentialResolver(B-5:execute 期 lazy、按 unit 解密、短 retrying session)
# --------------------------------------------------------------------------


class _FakeRepo:
    """按 unit 名过滤行,模拟 ToolCredentialRepository.list_for_unit。"""

    def __init__(self, rows):
        self._rows = rows

    async def list_for_unit(self, unit_name):
        return [r for r in self._rows if r.unit_name == unit_name]


class _FakeDB:
    """with_retry(fn) → fn(占位 session)。resolver 在回调内构造 ToolCredentialRepository,
    故测试 monkeypatch 那个类指向 _FakeRepo(无真 DB)。"""

    async def with_retry(self, fn, **kwargs):
        return await fn(object())


def _row(unit, placeholder, encrypted):
    return SimpleNamespace(
        unit_name=unit, placeholder_name=placeholder, encrypted_value=encrypted
    )


def _install_fake_repo(monkeypatch, rows):
    import repositories.tool_credential_repo as mod
    monkeypatch.setattr(mod, "ToolCredentialRepository", lambda session: _FakeRepo(rows))


@pytest.mark.asyncio
async def test_resolve_decrypts_only_called_unit(monkeypatch):
    # lazy:resolve(unit) 只解该 unit 的行(不预载全部 / 不驻留其它 unit 明文)
    cipher = CredentialCipher(_key())
    _install_fake_repo(monkeypatch, [
        _row("ragflow", "TOOL_SECRET_RAGFLOW_KEY", cipher.encrypt("k1")),
        _row("ragflow", "TOOL_SECRET_BASE", cipher.encrypt("https://host")),
        _row("github", "TOOL_SECRET_TOKEN", cipher.encrypt("ght")),
    ])
    resolver = CredentialResolver(_FakeDB(), cipher_factory=lambda: cipher)
    assert await resolver.resolve("ragflow") == {
        "TOOL_SECRET_RAGFLOW_KEY": "k1", "TOOL_SECRET_BASE": "https://host",
    }
    assert await resolver.resolve("github") == {"TOOL_SECRET_TOKEN": "ght"}


@pytest.mark.asyncio
async def test_resolve_no_unit_short_circuits_without_db(monkeypatch):
    # 无 unit 名 → {},连 with_retry/DB 都不碰(execute 回落路径才会传 None)
    def _boom():
        raise AssertionError("cipher must not be constructed for empty unit")

    resolver = CredentialResolver(_FakeDB(), cipher_factory=_boom)
    assert await resolver.resolve(None) == {}
    assert await resolver.resolve("") == {}


@pytest.mark.asyncio
async def test_resolve_unit_without_rows_returns_empty_without_cipher(monkeypatch):
    # 该 unit 无凭证行 → {} 且**不构造 cipher**(无凭证 unit 不触主密钥)
    def _boom():
        raise AssertionError("cipher must not be constructed when there are no rows")

    _install_fake_repo(monkeypatch, [])
    resolver = CredentialResolver(_FakeDB(), cipher_factory=_boom)
    assert await resolver.resolve("ragflow") == {}


@pytest.mark.asyncio
async def test_resolve_corrupt_row_raises(monkeypatch):
    # lazy 路径:被解的就是被调工具的 unit,坏行 raise(非 skip)→ execute 转 generic 错误,
    # 绝不让占位符原文外发(爆炸半径 = 仅该次调用,与 snapshot 期 eager 的 skip 不同)。
    cipher = CredentialCipher(_key())
    _install_fake_repo(monkeypatch, [
        _row("u", "TOOL_SECRET_OK", cipher.encrypt("good")),
        _row("u", "TOOL_SECRET_BAD", "corrupt-token"),
    ])
    resolver = CredentialResolver(_FakeDB(), cipher_factory=lambda: cipher)
    with pytest.raises(SecretResolutionError, match="TOOL_SECRET_BAD"):
        await resolver.resolve("u")
