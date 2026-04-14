"""Tests for DatabaseManager._parse_db_url query handling.

Covers the PG sslmode translation, MySQL pass-through, and the
reject-unknown-query-params behavior that prevents silent drops when
moving from DATABASE_URL (SQLAlchemy-parsed) to DATABASE_URLS
(raw asyncpg/aiomysql probes).
"""

import pytest

from db.database import DatabaseManager


class TestParseUrlBasics:
    def test_postgres_basic(self):
        driver, kw = DatabaseManager._parse_db_url(
            "postgresql+asyncpg://alice:secret@db.example.com:5432/mydb"
        )
        assert driver == "postgres"
        assert kw["host"] == "db.example.com"
        assert kw["port"] == 5432
        assert kw["database"] == "mydb"
        assert kw["user"] == "alice"
        assert kw["password"] == "secret"
        assert "ssl" not in kw

    def test_mysql_basic(self):
        driver, kw = DatabaseManager._parse_db_url(
            "mysql+aiomysql://bob:pw@mysql.example.com:3306/app"
        )
        assert driver == "mysql"
        assert kw["host"] == "mysql.example.com"
        assert kw["port"] == 3306
        assert kw["db"] == "app"
        assert kw["user"] == "bob"
        assert kw["password"] == "pw"

    def test_default_ports(self):
        _, pg_kw = DatabaseManager._parse_db_url("postgresql+asyncpg://h/d")
        assert pg_kw["port"] == 5432
        _, my_kw = DatabaseManager._parse_db_url("mysql+aiomysql://h/d")
        assert my_kw["port"] == 3306


class TestPostgresQueryParams:
    def test_sslmode_require_translates_to_ssl(self):
        driver, kw = DatabaseManager._parse_db_url(
            "postgresql+asyncpg://h/d?sslmode=require"
        )
        assert driver == "postgres"
        # asyncpg accepts the string directly
        assert kw["ssl"] == "require"

    def test_sslmode_disable(self):
        _, kw = DatabaseManager._parse_db_url(
            "postgresql+asyncpg://h/d?sslmode=disable"
        )
        assert kw["ssl"] == "disable"

    def test_command_timeout_pass_through(self):
        _, kw = DatabaseManager._parse_db_url(
            "postgresql+asyncpg://h/d?command_timeout=30"
        )
        assert kw["command_timeout"] == "30"

    def test_application_name_pass_through(self):
        _, kw = DatabaseManager._parse_db_url(
            "postgresql+asyncpg://h/d?application_name=artifact-flow"
        )
        assert kw["application_name"] == "artifact-flow"

    def test_multiple_allowed_params(self):
        _, kw = DatabaseManager._parse_db_url(
            "postgresql+asyncpg://h/d?sslmode=require&command_timeout=30"
        )
        assert kw["ssl"] == "require"
        assert kw["command_timeout"] == "30"

    def test_unknown_pg_query_rejected(self):
        with pytest.raises(ValueError, match="Unsupported DSN query param 'foo'"):
            DatabaseManager._parse_db_url(
                "postgresql+asyncpg://h/d?foo=bar"
            )

    def test_mysql_only_param_rejected_on_pg(self):
        # `charset` is MySQL-specific; must not silently leak into PG kwargs
        with pytest.raises(ValueError, match="Unsupported DSN query param 'charset'"):
            DatabaseManager._parse_db_url(
                "postgresql+asyncpg://h/d?charset=utf8mb4"
            )


class TestMysqlQueryParams:
    def test_charset_pass_through(self):
        _, kw = DatabaseManager._parse_db_url(
            "mysql+aiomysql://h/d?charset=utf8mb4"
        )
        assert kw["charset"] == "utf8mb4"

    def test_autocommit_pass_through(self):
        _, kw = DatabaseManager._parse_db_url(
            "mysql+aiomysql://h/d?autocommit=true"
        )
        assert kw["autocommit"] == "true"

    def test_unknown_mysql_query_rejected(self):
        with pytest.raises(ValueError, match="Unsupported DSN query param 'foo'"):
            DatabaseManager._parse_db_url("mysql+aiomysql://h/d?foo=bar")

    def test_pg_only_param_rejected_on_mysql(self):
        # `sslmode` is PG-specific
        with pytest.raises(ValueError, match="Unsupported DSN query param 'sslmode'"):
            DatabaseManager._parse_db_url(
                "mysql+aiomysql://h/d?sslmode=require"
            )


class TestSslFileParams:
    def test_ssl_ca_builds_sslcontext_for_pg(self, tmp_path):
        # Create a minimal self-signed CA file so load_verify_locations doesn't fail
        import subprocess
        ca_file = tmp_path / "ca.pem"
        subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                "-keyout", str(tmp_path / "ca.key"), "-out", str(ca_file),
                "-days", "1", "-subj", "/CN=test-ca",
            ],
            check=True, capture_output=True,
        )

        _, kw = DatabaseManager._parse_db_url(
            f"postgresql+asyncpg://h/d?ssl_ca={ca_file}"
        )
        import ssl as _ssl
        assert isinstance(kw["ssl"], _ssl.SSLContext)

    def test_ssl_ca_builds_sslcontext_for_mysql(self, tmp_path):
        import subprocess
        ca_file = tmp_path / "ca.pem"
        subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                "-keyout", str(tmp_path / "ca.key"), "-out", str(ca_file),
                "-days", "1", "-subj", "/CN=test-ca",
            ],
            check=True, capture_output=True,
        )

        _, kw = DatabaseManager._parse_db_url(
            f"mysql+aiomysql://h/d?ssl_ca={ca_file}"
        )
        import ssl as _ssl
        assert isinstance(kw["ssl"], _ssl.SSLContext)


class TestDriverMixDetection:
    def test_parse_returns_driver_tag(self):
        # Sanity check used by _failover_creator to validate single-driver
        pg_driver, _ = DatabaseManager._parse_db_url("postgresql+asyncpg://h/d")
        my_driver, _ = DatabaseManager._parse_db_url("mysql+aiomysql://h/d")
        assert pg_driver == "postgres"
        assert my_driver == "mysql"
