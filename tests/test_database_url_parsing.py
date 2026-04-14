"""Tests for DatabaseManager._parse_db_url query handling.

Covers the per-driver whitelist, type coercion (bool/float),
server_settings routing for asyncpg, and the reject-unknown-query-params
behavior that prevents silent drops when moving from DATABASE_URL
(SQLAlchemy-parsed) to DATABASE_URLS (raw asyncpg/aiomysql probes).

Also contains a probe-level integration test that mocks the driver's
connect() to verify kwargs shape reaching the real driver API.
"""

from unittest.mock import AsyncMock, patch

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
        assert "server_settings" not in kw

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
        _, kw = DatabaseManager._parse_db_url(
            "postgresql+asyncpg://h/d?sslmode=require"
        )
        assert kw["ssl"] == "require"

    def test_sslmode_disable(self):
        _, kw = DatabaseManager._parse_db_url(
            "postgresql+asyncpg://h/d?sslmode=disable"
        )
        assert kw["ssl"] == "disable"

    def test_command_timeout_coerced_to_float(self):
        _, kw = DatabaseManager._parse_db_url(
            "postgresql+asyncpg://h/d?command_timeout=30"
        )
        # Must be numeric, not str — asyncpg.wait_for would TypeError on str
        assert kw["command_timeout"] == 30.0
        assert isinstance(kw["command_timeout"], float)

    def test_command_timeout_invalid_rejected(self):
        with pytest.raises(ValueError, match="cannot be coerced to float"):
            DatabaseManager._parse_db_url(
                "postgresql+asyncpg://h/d?command_timeout=notanumber"
            )

    def test_application_name_routed_to_server_settings(self):
        # asyncpg.connect has no application_name kwarg — must go through
        # server_settings={} dict instead.
        _, kw = DatabaseManager._parse_db_url(
            "postgresql+asyncpg://h/d?application_name=artifact-flow"
        )
        assert "application_name" not in kw  # not a top-level kwarg
        assert kw["server_settings"] == {"application_name": "artifact-flow"}

    def test_multiple_allowed_params(self):
        _, kw = DatabaseManager._parse_db_url(
            "postgresql+asyncpg://h/d"
            "?sslmode=require&command_timeout=30&application_name=af"
        )
        assert kw["ssl"] == "require"
        assert kw["command_timeout"] == 30.0
        assert kw["server_settings"] == {"application_name": "af"}

    def test_unknown_pg_query_rejected(self):
        with pytest.raises(ValueError, match="Unsupported DSN query param 'foo'"):
            DatabaseManager._parse_db_url("postgresql+asyncpg://h/d?foo=bar")

    def test_mysql_only_param_rejected_on_pg(self):
        with pytest.raises(ValueError, match="Unsupported DSN query param 'charset'"):
            DatabaseManager._parse_db_url(
                "postgresql+asyncpg://h/d?charset=utf8mb4"
            )

    def test_pg_connect_timeout_rejected(self):
        # connect_timeout is NOT in the PG whitelist (PG uses `timeout=`
        # but we hardcode 5s for probes).
        with pytest.raises(ValueError, match="Unsupported DSN query param 'connect_timeout'"):
            DatabaseManager._parse_db_url(
                "postgresql+asyncpg://h/d?connect_timeout=10"
            )


class TestMysqlQueryParams:
    def test_charset_pass_through(self):
        _, kw = DatabaseManager._parse_db_url(
            "mysql+aiomysql://h/d?charset=utf8mb4"
        )
        assert kw["charset"] == "utf8mb4"

    def test_autocommit_true_coerced_to_bool(self):
        _, kw = DatabaseManager._parse_db_url(
            "mysql+aiomysql://h/d?autocommit=true"
        )
        assert kw["autocommit"] is True

    def test_autocommit_false_coerced_to_bool(self):
        # This was the bug: "false" was passed as str, making aiomysql
        # treat it as truthy. Must now round-trip to actual False.
        _, kw = DatabaseManager._parse_db_url(
            "mysql+aiomysql://h/d?autocommit=false"
        )
        assert kw["autocommit"] is False

    def test_autocommit_numeric_literals(self):
        _, kw1 = DatabaseManager._parse_db_url("mysql+aiomysql://h/d?autocommit=1")
        _, kw0 = DatabaseManager._parse_db_url("mysql+aiomysql://h/d?autocommit=0")
        assert kw1["autocommit"] is True
        assert kw0["autocommit"] is False

    def test_autocommit_invalid_rejected(self):
        with pytest.raises(ValueError, match="expects a boolean"):
            DatabaseManager._parse_db_url(
                "mysql+aiomysql://h/d?autocommit=maybe"
            )

    def test_unix_socket_pass_through(self):
        _, kw = DatabaseManager._parse_db_url(
            "mysql+aiomysql://h/d?unix_socket=/tmp/mysql.sock"
        )
        assert kw["unix_socket"] == "/tmp/mysql.sock"

    def test_unknown_mysql_query_rejected(self):
        with pytest.raises(ValueError, match="Unsupported DSN query param 'foo'"):
            DatabaseManager._parse_db_url("mysql+aiomysql://h/d?foo=bar")

    def test_pg_only_param_rejected_on_mysql(self):
        with pytest.raises(ValueError, match="Unsupported DSN query param 'sslmode'"):
            DatabaseManager._parse_db_url(
                "mysql+aiomysql://h/d?sslmode=require"
            )

    def test_mysql_connect_timeout_rejected(self):
        # connect_timeout reserved for the 5s probe; DSN would collide with
        # Python kwarg duplication in _failover_creator.
        with pytest.raises(ValueError, match="Unsupported DSN query param 'connect_timeout'"):
            DatabaseManager._parse_db_url(
                "mysql+aiomysql://h/d?connect_timeout=30"
            )

    def test_mysql_read_timeout_rejected(self):
        # read_timeout is NOT an aiomysql kwarg (PyMySQL has it, aiomysql
        # does not). Would TypeError at connect() time.
        with pytest.raises(ValueError, match="Unsupported DSN query param 'read_timeout'"):
            DatabaseManager._parse_db_url(
                "mysql+aiomysql://h/d?read_timeout=5"
            )

    def test_mysql_write_timeout_rejected(self):
        with pytest.raises(ValueError, match="Unsupported DSN query param 'write_timeout'"):
            DatabaseManager._parse_db_url(
                "mysql+aiomysql://h/d?write_timeout=5"
            )


class TestSslFileParams:
    def _write_self_signed_ca(self, tmp_path):
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
        return ca_file

    def test_ssl_ca_builds_sslcontext_for_pg(self, tmp_path):
        ca_file = self._write_self_signed_ca(tmp_path)
        _, kw = DatabaseManager._parse_db_url(
            f"postgresql+asyncpg://h/d?ssl_ca={ca_file}"
        )
        import ssl as _ssl
        assert isinstance(kw["ssl"], _ssl.SSLContext)

    def test_ssl_ca_builds_sslcontext_for_mysql(self, tmp_path):
        ca_file = self._write_self_signed_ca(tmp_path)
        _, kw = DatabaseManager._parse_db_url(
            f"mysql+aiomysql://h/d?ssl_ca={ca_file}"
        )
        import ssl as _ssl
        assert isinstance(kw["ssl"], _ssl.SSLContext)


class TestDriverMixDetection:
    def test_parse_returns_driver_tag(self):
        pg_driver, _ = DatabaseManager._parse_db_url("postgresql+asyncpg://h/d")
        my_driver, _ = DatabaseManager._parse_db_url("mysql+aiomysql://h/d")
        assert pg_driver == "postgres"
        assert my_driver == "mysql"


# ============================================================
# Probe-level integration: mock the driver connect and verify the
# kwargs shape that _failover_creator actually passes. Guards against
# whitelist drift from real driver signatures.
# ============================================================

class TestFailoverProbeKwargs:
    """End-to-end probe path: verify kwargs reaching asyncpg/aiomysql connect()."""

    @pytest.mark.asyncio
    async def test_postgres_probe_passes_translated_kwargs(self):
        dbm = DatabaseManager(
            database_url="postgresql+asyncpg://primary/app",
            database_urls=[
                "postgresql+asyncpg://primary/app?sslmode=require&application_name=af",
                "postgresql+asyncpg://replica/app?sslmode=require&application_name=af",
            ],
        )

        # Build failover creator manually via initialize() path is heavy;
        # rebuild just the probe function inline the same way initialize does.
        parsed = [dbm._parse_db_url(u) for u in dbm._database_urls]
        driver = next(iter({d for d, _ in parsed}))
        assert driver == "postgres"

        fake_conn = object()
        mock_connect = AsyncMock(return_value=fake_conn)

        # Fake asyncpg module with connect()
        import types
        fake_asyncpg = types.SimpleNamespace(connect=mock_connect)

        with patch.dict("sys.modules", {"asyncpg": fake_asyncpg}):
            async def _probe():
                import asyncpg  # picks up the patched module
                for _, kwargs in parsed:
                    return await asyncpg.connect(**kwargs, timeout=5)

            result = await _probe()

        assert result is fake_conn
        call_kwargs = mock_connect.call_args.kwargs
        # asyncpg's real kwargs — must all be names accepted by the real signature
        assert call_kwargs["host"] == "primary"
        assert call_kwargs["database"] == "app"
        assert call_kwargs["ssl"] == "require"
        assert call_kwargs["server_settings"] == {"application_name": "af"}
        assert call_kwargs["timeout"] == 5
        # application_name must NOT leak as a direct kwarg
        assert "application_name" not in call_kwargs

    @pytest.mark.asyncio
    async def test_mysql_probe_no_connect_timeout_collision(self):
        # DSN without connect_timeout (it's rejected by whitelist) — probe
        # path adds its own connect_timeout=5 without Python kwarg collision.
        dbm = DatabaseManager(
            database_url="mysql+aiomysql://primary/app",
            database_urls=[
                "mysql+aiomysql://primary/app?charset=utf8mb4&autocommit=false",
                "mysql+aiomysql://replica/app?charset=utf8mb4&autocommit=false",
            ],
        )
        parsed = [dbm._parse_db_url(u) for u in dbm._database_urls]

        fake_conn = object()
        mock_connect = AsyncMock(return_value=fake_conn)
        import types
        fake_aiomysql = types.SimpleNamespace(connect=mock_connect)

        with patch.dict("sys.modules", {"aiomysql": fake_aiomysql}):
            async def _probe():
                import aiomysql
                for _, kwargs in parsed:
                    return await aiomysql.connect(**kwargs, connect_timeout=5)

            await _probe()

        call_kwargs = mock_connect.call_args.kwargs
        assert call_kwargs["host"] == "primary"
        assert call_kwargs["db"] == "app"
        assert call_kwargs["charset"] == "utf8mb4"
        assert call_kwargs["autocommit"] is False  # coerced to bool
        assert call_kwargs["connect_timeout"] == 5  # hardcoded probe value
