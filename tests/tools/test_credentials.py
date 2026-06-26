"""工具凭证加密 + 运行期解析单测(B-4),纯单元、不碰 DB(用 fake repo)。"""

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
# CredentialResolver
# --------------------------------------------------------------------------


class _FakeRepo:
    def __init__(self, rows_by_unit):
        self._rows = rows_by_unit

    async def list_for_unit(self, unit_name):
        return self._rows.get(unit_name, [])


def _row(placeholder, encrypted):
    return SimpleNamespace(placeholder_name=placeholder, encrypted_value=encrypted)


@pytest.mark.asyncio
async def test_resolver_decrypts_unit_credentials():
    key = _key()
    cipher = CredentialCipher(key)
    repo = _FakeRepo({"ragflow": [
        _row("TOOL_SECRET_RAGFLOW_KEY", cipher.encrypt("k1")),
        _row("TOOL_SECRET_BASE", cipher.encrypt("https://host")),
    ]})
    resolver = CredentialResolver(repo, cipher_factory=lambda: cipher)
    out = await resolver.resolve("ragflow")
    assert out == {"TOOL_SECRET_RAGFLOW_KEY": "k1", "TOOL_SECRET_BASE": "https://host"}


@pytest.mark.asyncio
async def test_resolver_no_unit_or_no_rows_returns_empty_without_cipher():
    # 无 unit / 无凭证行 → {} 且**不构造 cipher**(无凭证部署无需主密钥)
    def _boom():
        raise AssertionError("cipher must not be constructed for empty unit")

    resolver = CredentialResolver(_FakeRepo({"x": []}), cipher_factory=_boom)
    assert await resolver.resolve(None) == {}
    assert await resolver.resolve("x") == {}
    assert await resolver.resolve("absent") == {}


@pytest.mark.asyncio
async def test_resolver_missing_key_raises_secret_error():
    repo = _FakeRepo({"u": [_row("TOOL_SECRET_K", "whatever")]})

    def _no_key():
        raise CredentialKeyError("ARTIFACTFLOW_CREDENTIAL_KEY is not set")

    resolver = CredentialResolver(repo, cipher_factory=_no_key)
    with pytest.raises(SecretResolutionError, match="not set"):
        await resolver.resolve("u")


@pytest.mark.asyncio
async def test_resolver_corrupt_ciphertext_raises_secret_error():
    cipher = CredentialCipher(_key())
    repo = _FakeRepo({"u": [_row("TOOL_SECRET_K", "corrupt-token")]})
    resolver = CredentialResolver(repo, cipher_factory=lambda: cipher)
    with pytest.raises(SecretResolutionError, match="failed to decrypt"):
        await resolver.resolve("u")
