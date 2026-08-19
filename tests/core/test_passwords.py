"""bcrypt password-write limits and historical verification compatibility."""

import bcrypt
import pytest

from config import PASSWORD_MAX_BYTES
from core.security.passwords import hash_password, verify_password


def test_hash_password_writes_rollback_compatible_raw_bcrypt():
    hashed = hash_password("Aa1!" + "x" * (PASSWORD_MAX_BYTES - 4))

    assert hashed.startswith("$2b$")
    assert bcrypt.checkpw(
        ("Aa1!" + "x" * (PASSWORD_MAX_BYTES - 4)).encode("utf-8"),
        hashed.encode("utf-8"),
    )
    assert verify_password("Aa1!" + "x" * (PASSWORD_MAX_BYTES - 4), hashed)


def test_historical_overlong_password_remains_verifiable_without_migration():
    shared = "Aa1!" + "x" * 80
    first = shared + "A"
    second = shared + "B"
    legacy_hash = bcrypt.hashpw(
        first.encode("utf-8")[:72],
        bcrypt.gensalt(rounds=4),
    ).decode("utf-8")

    assert verify_password(first, legacy_hash) is True
    assert verify_password(second, legacy_hash) is True


@pytest.mark.parametrize(
    "password",
    [
        "x" * (PASSWORD_MAX_BYTES + 1),
        "A1!" + "中" * 24,
    ],
)
def test_hash_password_rejects_values_over_bcrypt_byte_limit(password):
    with pytest.raises(ValueError, match="72 字节"):
        hash_password(password)
