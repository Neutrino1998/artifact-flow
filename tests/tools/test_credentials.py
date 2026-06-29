"""工具凭证加密 + 运行期解析单测(B-4),纯单元、不碰 DB(用 fake repo)。"""

from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from tools.custom.credentials import (
    CredentialCipher,
    CredentialKeyError,
    resolve_all_credentials,
)


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
# resolve_all_credentials(快照读边界一次性解密)
# --------------------------------------------------------------------------


class _FakeRepo:
    def __init__(self, rows):
        self._rows = rows

    async def list_all(self):
        return self._rows


def _row(unit, placeholder, encrypted):
    return SimpleNamespace(
        unit_name=unit, placeholder_name=placeholder, encrypted_value=encrypted
    )


@pytest.mark.asyncio
async def test_resolve_all_decrypts_grouped_by_unit():
    cipher = CredentialCipher(_key())
    repo = _FakeRepo([
        _row("ragflow", "TOOL_SECRET_RAGFLOW_KEY", cipher.encrypt("k1")),
        _row("ragflow", "TOOL_SECRET_BASE", cipher.encrypt("https://host")),
        _row("github", "TOOL_SECRET_TOKEN", cipher.encrypt("ght")),
    ])
    out = await resolve_all_credentials(repo, cipher_factory=lambda: cipher)
    assert out == {
        "ragflow": {"TOOL_SECRET_RAGFLOW_KEY": "k1", "TOOL_SECRET_BASE": "https://host"},
        "github": {"TOOL_SECRET_TOKEN": "ght"},
    }


@pytest.mark.asyncio
async def test_resolve_all_empty_returns_empty_without_cipher():
    # 无凭证行 → {} 且**不构造 cipher**(无凭证部署 snapshot 不触 cipher)
    def _boom():
        raise AssertionError("cipher must not be constructed when there are no rows")

    assert await resolve_all_credentials(_FakeRepo([]), cipher_factory=_boom) == {}


@pytest.mark.asyncio
async def test_resolve_all_corrupt_row_skipped_not_raised():
    # 单行解密失败 → skip(不 raise,不炸整轮);其余行照常返回。受影响工具 execute 期再 fail。
    cipher = CredentialCipher(_key())
    repo = _FakeRepo([
        _row("u", "TOOL_SECRET_OK", cipher.encrypt("good")),
        _row("u", "TOOL_SECRET_BAD", "corrupt-token"),
    ])
    out = await resolve_all_credentials(repo, cipher_factory=lambda: cipher)
    assert out == {"u": {"TOOL_SECRET_OK": "good"}}      # 坏行被跳过,好行保留
