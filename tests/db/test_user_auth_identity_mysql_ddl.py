"""MySQL/TDSQL authentication identity comparison must be byte-exact."""

from __future__ import annotations

import importlib

from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable

from db.models import User


def test_user_model_mysql_identity_columns_use_binary_utf8mb4_collation():
    ddl = str(CreateTable(User.__table__).compile(dialect=mysql.dialect()))

    assert ddl.count("CHARACTER SET utf8mb4 COLLATE utf8mb4_bin") >= 2
    assert "CONSTRAINT uq_users_auth_identity UNIQUE (auth_provider, auth_subject)" in ddl


def test_0008_migration_uses_the_same_mysql_identity_types():
    migration = importlib.import_module("db.alembic.versions.0008_user_auth_identity")

    for length in (64, 256):
        identity_type = migration._auth_identity_string(length).dialect_impl(
            mysql.dialect()
        )
        assert identity_type.charset == "utf8mb4"
        assert identity_type.collation == "utf8mb4_bin"
