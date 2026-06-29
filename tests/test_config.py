"""Tests for config.validate_config() — startup-time config assertions.

Focused on the DEP-01 CORS footgun guard: CORS_ALLOW_CREDENTIALS=True combined
with a '*' entry in CORS_ORIGINS makes Starlette reflect the request Origin,
turning an env misconfig into "any site may read authenticated responses".
"""

import pytest

from config import config, validate_config


@pytest.fixture
def _valid_prereqs(monkeypatch):
    """Satisfy the non-CORS checks so validate_config() reaches the CORS guard."""
    monkeypatch.setattr(config, "DATABASE_URL", "sqlite+aiosqlite:///data/test.db")
    monkeypatch.setattr(config, "DATABASE_URLS", "")
    monkeypatch.setattr(config, "REDIS_URL", "")  # skip the redis-prefix check
    monkeypatch.setattr(config, "JWT_SECRET", "x" * 32)
    monkeypatch.setattr(config, "CREDENTIAL_KEY", "x" * 32)


def test_missing_credential_key_is_rejected(_valid_prereqs, monkeypatch):
    # B-4: the credential master key is mandatory (mirrors JWT_SECRET) — required even
    # with no credentialed tools, so the runtime never carries a missing-key branch.
    monkeypatch.setattr(config, "CREDENTIAL_KEY", "")
    with pytest.raises(RuntimeError, match="ARTIFACTFLOW_CREDENTIAL_KEY"):
        validate_config()


def test_wildcard_origin_with_credentials_is_rejected(_valid_prereqs, monkeypatch):
    monkeypatch.setattr(config, "CORS_ALLOW_CREDENTIALS", True)
    monkeypatch.setattr(config, "CORS_ORIGINS", ["http://localhost:3000", "*"])
    with pytest.raises(RuntimeError, match="CORS_ALLOW_CREDENTIALS"):
        validate_config()


def test_explicit_origins_with_credentials_is_allowed(_valid_prereqs, monkeypatch):
    monkeypatch.setattr(config, "CORS_ALLOW_CREDENTIALS", True)
    monkeypatch.setattr(config, "CORS_ORIGINS", ["https://app.example.com"])
    validate_config()  # must not raise


def test_wildcard_origin_without_credentials_is_allowed(_valid_prereqs, monkeypatch):
    # Wildcard is only dangerous together with credentials; alone it's a valid
    # (if permissive) public-API config and must not be blocked.
    monkeypatch.setattr(config, "CORS_ALLOW_CREDENTIALS", False)
    monkeypatch.setattr(config, "CORS_ORIGINS", ["*"])
    validate_config()  # must not raise
