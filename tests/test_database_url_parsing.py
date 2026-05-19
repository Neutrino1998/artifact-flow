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
from sqlalchemy.engine import make_url

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

    def test_pg_sslmode_and_ssl_ca_rejected(self, tmp_path):
        # Mixing sslmode with file-based SSL params has ambiguous semantics
        # (disable+ssl_ca would silently enable TLS). Must fail fast.
        ca_file = self._write_self_signed_ca(tmp_path)
        with pytest.raises(ValueError, match="cannot mix 'sslmode' with file-based SSL"):
            DatabaseManager._parse_db_url(
                f"postgresql+asyncpg://h/d?sslmode=disable&ssl_ca={ca_file}"
            )

    def test_pg_sslmode_require_with_ssl_ca_also_rejected(self, tmp_path):
        # Even a "compatible-looking" combo like require+ssl_ca is rejected,
        # because asyncpg can't honor the subtler libpq semantics.
        ca_file = self._write_self_signed_ca(tmp_path)
        with pytest.raises(ValueError, match="cannot mix 'sslmode' with file-based SSL"):
            DatabaseManager._parse_db_url(
                f"postgresql+asyncpg://h/d?sslmode=require&ssl_ca={ca_file}"
            )


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


# ============================================================
# Session TZ injection: defense-in-depth beyond compose -c timezone=UTC.
# Cloud PG (RDS) / DATABASE_URLS failover targets aren't covered by compose
# flags — _apply_session_tz_kwargs forces UTC at connect time regardless of
# server config. (Incident 2026-05-14 PR-tz-unify reviewer round 1.)
# ============================================================


class TestSessionTzInjection:
    """Unit tests for `_apply_session_tz_kwargs` injection helper."""

    def test_pg_empty_kwargs_gets_timezone_utc(self):
        out = DatabaseManager._apply_session_tz_kwargs("postgres", {})
        assert out == {"server_settings": {"timezone": "UTC"}}

    def test_pg_preserves_existing_server_settings(self):
        # DSN-supplied application_name (via _parse_db_url server_settings
        # routing) must survive UTC injection.
        out = DatabaseManager._apply_session_tz_kwargs(
            "postgres", {"server_settings": {"application_name": "af"}}
        )
        assert out["server_settings"] == {
            "application_name": "af",
            "timezone": "UTC",
        }

    def test_pg_user_explicit_timezone_wins(self):
        # If operator explicitly set timezone in DSN we don't fight them
        # (setdefault semantics) — covers edge case for cross-TZ debugging.
        out = DatabaseManager._apply_session_tz_kwargs(
            "postgres", {"server_settings": {"timezone": "Asia/Tokyo"}}
        )
        assert out["server_settings"]["timezone"] == "Asia/Tokyo"

    def test_mysql_empty_kwargs_gets_init_command(self):
        out = DatabaseManager._apply_session_tz_kwargs("mysql", {})
        assert out == {"init_command": "SET time_zone='+00:00'"}

    def test_mysql_prepends_existing_init_command(self):
        # Operator-supplied init_command runs AFTER our SET so they can
        # override if needed (MySQL parses sequentially).
        out = DatabaseManager._apply_session_tz_kwargs(
            "mysql", {"init_command": "SET autocommit=1"}
        )
        assert out["init_command"] == "SET time_zone='+00:00'; SET autocommit=1"

    def test_unknown_driver_passthrough(self):
        # SQLite / unknown drivers untouched — no session TZ concept.
        out = DatabaseManager._apply_session_tz_kwargs(
            "sqlite", {"check_same_thread": False}
        )
        assert out == {"check_same_thread": False}

    def test_does_not_mutate_input(self):
        # Failover loop reuses parsed_urls every reconnect; mutation would
        # accumulate `SET time_zone` prepends or wedge server_settings.
        src = {"server_settings": {"application_name": "af"}}
        DatabaseManager._apply_session_tz_kwargs("postgres", src)
        assert src == {"server_settings": {"application_name": "af"}}

        src_my = {"init_command": "SET autocommit=1"}
        DatabaseManager._apply_session_tz_kwargs("mysql", src_my)
        assert src_my == {"init_command": "SET autocommit=1"}

    @pytest.mark.asyncio
    async def test_failover_probe_applies_session_tz(self):
        """End-to-end probe path through `_apply_session_tz_kwargs`:
        the kwargs reaching asyncpg.connect must include UTC even though
        async_creator bypasses SQLAlchemy connect_args.

        Mirrors test_postgres_probe_passes_translated_kwargs but inserts
        the session-TZ injection step that runs inside `_failover_creator`
        before each connect attempt.
        """
        dbm = DatabaseManager(
            database_url="postgresql+asyncpg://primary/app",
            database_urls=[
                "postgresql+asyncpg://primary/app?application_name=af",
                "postgresql+asyncpg://replica/app?application_name=af",
            ],
        )
        parsed = [dbm._parse_db_url(u) for u in dbm._database_urls]
        driver = parsed[0][0]

        fake_conn = object()
        mock_connect = AsyncMock(return_value=fake_conn)
        import types
        fake_asyncpg = types.SimpleNamespace(connect=mock_connect)

        with patch.dict("sys.modules", {"asyncpg": fake_asyncpg}):
            async def _probe():
                import asyncpg
                for _, kwargs in parsed:
                    # Mirror _failover_creator's per-iteration injection
                    kwargs = DatabaseManager._apply_session_tz_kwargs(driver, kwargs)
                    return await asyncpg.connect(**kwargs, timeout=5)

            await _probe()

        call_kwargs = mock_connect.call_args.kwargs
        # Both DSN-derived application_name and injected timezone present
        assert call_kwargs["server_settings"] == {
            "application_name": "af",
            "timezone": "UTC",
        }

    @pytest.mark.asyncio
    async def test_initialize_pg_engine_kwargs_include_session_tz(self):
        """Single-URL PG initialize wires session-TZ into connect_args so
        SQLAlchemy → asyncpg sends `SET TIMEZONE = UTC` at connect time."""
        captured = {}

        def fake_create_engine(url, **kwargs):
            captured["url"] = str(url)
            captured["kwargs"] = kwargs
            # Raise to stop initialize() before it tries to connect.
            raise RuntimeError("stop-after-engine-build")

        dbm = DatabaseManager(database_url="postgresql+asyncpg://host/app")
        with patch("db.database.create_async_engine", side_effect=fake_create_engine):
            with pytest.raises(RuntimeError, match="stop-after-engine-build"):
                await dbm.initialize()

        assert "connect_args" in captured["kwargs"]
        assert captured["kwargs"]["connect_args"] == {
            "server_settings": {"timezone": "UTC"}
        }

    @pytest.mark.asyncio
    async def test_initialize_mysql_engine_kwargs_include_session_tz(self):
        captured = {}

        def fake_create_engine(url, **kwargs):
            captured["kwargs"] = kwargs
            raise RuntimeError("stop")

        dbm = DatabaseManager(database_url="mysql+aiomysql://host/app")
        with patch("db.database.create_async_engine", side_effect=fake_create_engine):
            with pytest.raises(RuntimeError, match="stop"):
                await dbm.initialize()

        assert captured["kwargs"]["connect_args"] == {
            "init_command": "SET time_zone='+00:00'"
        }

    @pytest.mark.asyncio
    async def test_initialize_sqlite_unchanged(self):
        """SQLite branch must NOT get session-TZ kwargs — SQLite has no
        session timezone concept and stdlib CURRENT_TIMESTAMP is UTC."""
        captured = {}

        def fake_create_engine(url, **kwargs):
            captured["kwargs"] = kwargs
            raise RuntimeError("stop")

        dbm = DatabaseManager(database_url="sqlite+aiosqlite:///:memory:")
        with patch("db.database.create_async_engine", side_effect=fake_create_engine):
            with pytest.raises(RuntimeError, match="stop"):
                await dbm.initialize()

        # SQLite path keeps only check_same_thread — no server_settings /
        # init_command leakage.
        assert captured["kwargs"]["connect_args"] == {"check_same_thread": False}

    @pytest.mark.asyncio
    async def test_initialize_mysql_preserves_dsn_init_command(self):
        """Reviewer round 2 regression: DSN-supplied `?init_command=...` must
        survive into aiomysql's connect kwargs. Without the URL-query merge,
        connect_args["init_command"] would replace SQLAlchemy's URL-parsed
        value as a whole-key override, silently dropping the user's command."""
        captured = {}

        def fake_create_engine(url, **kwargs):
            captured["kwargs"] = kwargs
            raise RuntimeError("stop")

        dbm = DatabaseManager(
            database_url="mysql+aiomysql://host/app?init_command=SET%20autocommit%3D1"
        )
        with patch("db.database.create_async_engine", side_effect=fake_create_engine):
            with pytest.raises(RuntimeError, match="stop"):
                await dbm.initialize()

        # Helper prepends UTC before the user's command — both run, in order.
        assert captured["kwargs"]["connect_args"] == {
            "init_command": "SET time_zone='+00:00'; SET autocommit=1"
        }

    @pytest.mark.asyncio
    async def test_initialize_pg_preserves_dsn_application_name(self):
        """Reviewer round 2 regression: DSN-supplied `?application_name=...`
        must survive into asyncpg's server_settings. SQLAlchemy translates
        the URL param into server_settings={"application_name": ...}; without
        the URL-query merge, connect_args["server_settings"] replaces that
        whole dict, dropping application_name."""
        captured = {}

        def fake_create_engine(url, **kwargs):
            captured["kwargs"] = kwargs
            raise RuntimeError("stop")

        dbm = DatabaseManager(
            database_url="postgresql+asyncpg://host/app?application_name=af"
        )
        with patch("db.database.create_async_engine", side_effect=fake_create_engine):
            with pytest.raises(RuntimeError, match="stop"):
                await dbm.initialize()

        # Both application_name (from DSN) and timezone (injected) present.
        assert captured["kwargs"]["connect_args"] == {
            "server_settings": {"application_name": "af", "timezone": "UTC"}
        }

    @pytest.mark.asyncio
    async def test_initialize_pg_strips_server_settings_keys_from_url(self):
        """Reviewer round 3 regression: after moving `application_name` into
        connect_args.server_settings (round 2), the URL still carries it in
        query. SQLAlchemy's asyncpg dialect dumps url.query verbatim into the
        opts passed to asyncpg.connect, so `application_name` would leak as
        an UNSUPPORTED top-level kwarg — asyncpg.connect raises TypeError on
        unknown kwargs, blocking startup. Fix: strip `_PG_SERVER_SETTINGS`
        keys from the URL itself before handing to create_async_engine."""
        captured = {}

        def fake_create_engine(url, **kwargs):
            captured["url"] = url
            captured["kwargs"] = kwargs
            raise RuntimeError("stop")

        dbm = DatabaseManager(
            database_url="postgresql+asyncpg://host/app?application_name=af"
        )
        with patch("db.database.create_async_engine", side_effect=fake_create_engine):
            with pytest.raises(RuntimeError, match="stop"):
                await dbm.initialize()

        # The URL passed to create_async_engine has the query stripped.
        engine_url = captured["url"]
        # create_async_engine accepts either str or URL; we pass URL object so
        # sanitization is visible via .query.
        assert hasattr(engine_url, "query"), (
            f"engine_url should be a URL object after sanitization, got {type(engine_url)}"
        )
        assert "application_name" not in engine_url.query, (
            f"application_name still present in URL query: {dict(engine_url.query)}"
        )
        # And it's still preserved via connect_args (round 2 invariant).
        assert captured["kwargs"]["connect_args"]["server_settings"] == {
            "application_name": "af",
            "timezone": "UTC",
        }

    def test_pg_asyncpg_dialect_on_sanitized_url_no_toplevel_application_name(self):
        """Direct dialect verification: feed SQLAlchemy's asyncpg dialect the
        sanitized URL we'd pass to create_async_engine, confirm the opts it
        produces for asyncpg.connect do NOT contain `application_name` at top
        level. This is the smoking-gun assertion — asyncpg.connect's signature
        rejects this kwarg, so opts containing it would TypeError at startup.

        Patching `asyncpg.connect` end-to-end requires a real AsyncEngine
        startup (initialize → _check_alembic_version → pool acquire → real
        connect). The dialect-level test isolates the URL-to-opts mapping
        which is the actual surface SQLAlchemy controls."""
        from sqlalchemy.engine import make_url
        from sqlalchemy.dialects.postgresql import asyncpg as asyncpg_dialect

        raw_url = make_url(
            "postgresql+asyncpg://host/app?application_name=af"
        )
        # Mirror what database.py does
        sanitized = raw_url.difference_update_query(
            DatabaseManager._PG_SERVER_SETTINGS
        )

        _, opts = asyncpg_dialect.dialect().create_connect_args(sanitized)

        assert "application_name" not in opts, (
            "SQLAlchemy asyncpg dialect emitted unsanitized URL params as "
            f"top-level kwargs to asyncpg.connect: {opts!r}. asyncpg.connect "
            "rejects unknown kwargs — startup would TypeError."
        )

    def test_pg_asyncpg_dialect_on_unsanitized_url_leaks_application_name(self):
        """Negative control: confirm the leak actually exists without
        sanitization. If SQLAlchemy upstream ever changes asyncpg dialect to
        translate `application_name` → `server_settings` automatically, this
        test fails and tells us round 3's sanitize step is no longer needed.
        Wedge against silent SQLAlchemy behavior drift."""
        from sqlalchemy.engine import make_url
        from sqlalchemy.dialects.postgresql import asyncpg as asyncpg_dialect

        raw_url = make_url(
            "postgresql+asyncpg://host/app?application_name=af"
        )
        _, opts = asyncpg_dialect.dialect().create_connect_args(raw_url)

        assert "application_name" in opts, (
            "SQLAlchemy asyncpg dialect no longer leaks application_name "
            "to top-level kwargs — database.py URL sanitization may now be "
            "unnecessary. Re-verify before removing."
        )


# ============================================================
# Round 4: structural unification — DatabaseManager is the SOLE DSN query
# translator. _parse_db_query_params is the single source of truth shared
# by single-URL (initialize) and failover (_parse_db_url). Consumed keys are
# stripped from the URL handed to SQLAlchemy so the dialect cannot re-emit
# them as conflicting top-level kwargs. Without this, every new query key
# we care about would need another round-3-style patch.
# (Incident 2026-05-14 PR-tz-unify reviewer round 4.)
# ============================================================


class TestParseDbQueryParams:
    """Direct unit tests for the shared parser.

    Mirrors the assertions made indirectly via _parse_db_url (TestPostgresQueryParams,
    TestMysqlQueryParams) but also verifies the consumed_keys return value —
    the single-URL path relies on it to strip query keys from the URL.
    """

    def test_pg_empty_query_returns_empty(self):
        url = make_url("postgresql+asyncpg://host/app")
        result, consumed = DatabaseManager._parse_db_query_params(url, "postgres")
        assert result == {}
        assert consumed == frozenset()

    def test_mysql_empty_query_returns_empty(self):
        url = make_url("mysql+aiomysql://host/app")
        result, consumed = DatabaseManager._parse_db_query_params(url, "mysql")
        assert result == {}
        assert consumed == frozenset()

    def test_sqlite_driver_returns_empty(self):
        # SQLite has no DSN query semantics we route. Returning empty makes
        # the helper safe to call unconditionally without special-casing
        # the driver in the caller.
        url = make_url("sqlite+aiosqlite:///:memory:")
        result, consumed = DatabaseManager._parse_db_query_params(url, "sqlite")
        assert result == {}
        assert consumed == frozenset()

    def test_pg_sslmode_consumed(self):
        url = make_url("postgresql+asyncpg://host/app?sslmode=require")
        result, consumed = DatabaseManager._parse_db_query_params(url, "postgres")
        assert result == {"ssl": "require"}
        assert consumed == frozenset({"sslmode"})

    def test_pg_application_name_consumed(self):
        url = make_url("postgresql+asyncpg://host/app?application_name=af")
        result, consumed = DatabaseManager._parse_db_query_params(url, "postgres")
        assert result == {"server_settings": {"application_name": "af"}}
        assert consumed == frozenset({"application_name"})

    def test_pg_command_timeout_consumed(self):
        url = make_url("postgresql+asyncpg://host/app?command_timeout=30")
        result, consumed = DatabaseManager._parse_db_query_params(url, "postgres")
        assert result == {"command_timeout": 30.0}
        assert consumed == frozenset({"command_timeout"})

    def test_pg_all_known_consumed_together(self):
        # Critical: every PG key DatabaseManager knows about must be in
        # consumed_keys so the single-URL path strips them ALL — otherwise
        # SQLAlchemy's asyncpg dialect would re-emit the survivors as
        # top-level asyncpg.connect kwargs (TypeError at startup).
        url = make_url(
            "postgresql+asyncpg://host/app"
            "?sslmode=require&command_timeout=30&application_name=af"
        )
        result, consumed = DatabaseManager._parse_db_query_params(url, "postgres")
        assert result == {
            "ssl": "require",
            "command_timeout": 30.0,
            "server_settings": {"application_name": "af"},
        }
        assert consumed == frozenset(
            {"sslmode", "command_timeout", "application_name"}
        )

    def test_mysql_charset_consumed(self):
        url = make_url("mysql+aiomysql://host/app?charset=utf8mb4")
        result, consumed = DatabaseManager._parse_db_query_params(url, "mysql")
        assert result == {"charset": "utf8mb4"}
        assert consumed == frozenset({"charset"})

    def test_mysql_autocommit_consumed_as_bool(self):
        url = make_url("mysql+aiomysql://host/app?autocommit=false")
        result, consumed = DatabaseManager._parse_db_query_params(url, "mysql")
        assert result == {"autocommit": False}
        assert consumed == frozenset({"autocommit"})

    def test_mysql_init_command_consumed(self):
        # MySQL init_command is the only key the session-TZ helper also
        # mutates (prepend). Parser claims it on the way in; helper modifies
        # the value, not the key. URL strip + connect_args takeover are still
        # both needed for consistency with PG.
        url = make_url(
            "mysql+aiomysql://host/app?init_command=SET%20autocommit%3D1"
        )
        result, consumed = DatabaseManager._parse_db_query_params(url, "mysql")
        assert result == {"init_command": "SET autocommit=1"}
        assert consumed == frozenset({"init_command"})

    def test_pg_unknown_key_rejected_fail_loud(self):
        # Round 4 invariant: DatabaseManager is the SOLE translator. Unknown
        # keys must fail at init time, not silently pass through to SQLAlchemy
        # (which would then dump them as top-level asyncpg.connect kwargs →
        # TypeError at the actual connect attempt, much later in startup).
        url = make_url("postgresql+asyncpg://host/app?bogus=value")
        with pytest.raises(ValueError, match="Unsupported DSN query param 'bogus'"):
            DatabaseManager._parse_db_query_params(url, "postgres")

    def test_mysql_unknown_key_rejected_fail_loud(self):
        url = make_url("mysql+aiomysql://host/app?bogus=value")
        with pytest.raises(ValueError, match="Unsupported DSN query param 'bogus'"):
            DatabaseManager._parse_db_query_params(url, "mysql")

    def test_does_not_mutate_input_url(self):
        # Pure function: caller relies on the original URL still carrying
        # the query (single-URL path runs difference_update_query on it next).
        url = make_url(
            "postgresql+asyncpg://host/app?sslmode=require&application_name=af"
        )
        _, _ = DatabaseManager._parse_db_query_params(url, "postgres")
        assert dict(url.query) == {"sslmode": "require", "application_name": "af"}


class TestInitializeUnifiedTranslation:
    """initialize() single-URL must produce the same connect_args as the
    failover path for the same DSN query — and strip all consumed keys from
    the URL handed to create_async_engine.

    Smoking-gun for the round 4 unification: a DSN with sslmode +
    application_name + command_timeout exercises THREE different translation
    paths (ssl=, server_settings.application_name, float coercion). Before
    round 4, initialize() only handled application_name + (MySQL) init_command;
    the other keys leaked through SQLAlchemy to asyncpg.connect as unsupported
    top-level kwargs.
    """

    @pytest.mark.asyncio
    async def test_initialize_pg_sslmode_translates_and_strips_url(self):
        captured = {}

        def fake_create_engine(url, **kwargs):
            captured["url"] = url
            captured["kwargs"] = kwargs
            raise RuntimeError("stop")

        dbm = DatabaseManager(
            database_url="postgresql+asyncpg://host/app?sslmode=require"
        )
        with patch("db.database.create_async_engine", side_effect=fake_create_engine):
            with pytest.raises(RuntimeError, match="stop"):
                await dbm.initialize()

        # sslmode → ssl in connect_args
        assert captured["kwargs"]["connect_args"]["ssl"] == "require"
        # sslmode stripped from URL → SQLAlchemy dialect can't re-emit it
        assert "sslmode" not in captured["url"].query

    @pytest.mark.asyncio
    async def test_initialize_pg_command_timeout_coerced_and_strips_url(self):
        captured = {}

        def fake_create_engine(url, **kwargs):
            captured["url"] = url
            captured["kwargs"] = kwargs
            raise RuntimeError("stop")

        dbm = DatabaseManager(
            database_url="postgresql+asyncpg://host/app?command_timeout=30"
        )
        with patch("db.database.create_async_engine", side_effect=fake_create_engine):
            with pytest.raises(RuntimeError, match="stop"):
                await dbm.initialize()

        # Float coercion at parse time, not at connect time
        assert captured["kwargs"]["connect_args"]["command_timeout"] == 30.0
        assert isinstance(captured["kwargs"]["connect_args"]["command_timeout"], float)
        assert "command_timeout" not in captured["url"].query

    @pytest.mark.asyncio
    async def test_initialize_pg_combined_keys_all_translated_and_stripped(self):
        """The combined assertion: with all three keys in one DSN, every one
        of them must be translated to its asyncpg-acceptable form AND removed
        from the URL. If any key leaks back through the URL, asyncpg.connect
        would TypeError at startup. This is the regression test we want every
        time someone adds a new key to _PG_DIRECT_KWARGS or _PG_SERVER_SETTINGS."""
        captured = {}

        def fake_create_engine(url, **kwargs):
            captured["url"] = url
            captured["kwargs"] = kwargs
            raise RuntimeError("stop")

        dbm = DatabaseManager(
            database_url=(
                "postgresql+asyncpg://host/app"
                "?sslmode=require&command_timeout=30&application_name=af"
            )
        )
        with patch("db.database.create_async_engine", side_effect=fake_create_engine):
            with pytest.raises(RuntimeError, match="stop"):
                await dbm.initialize()

        ca = captured["kwargs"]["connect_args"]
        assert ca["ssl"] == "require"
        assert ca["command_timeout"] == 30.0
        assert ca["server_settings"] == {
            "application_name": "af",
            "timezone": "UTC",
        }
        # And NONE of the consumed keys remain on the URL.
        for key in ("sslmode", "command_timeout", "application_name"):
            assert key not in captured["url"].query, (
                f"'{key}' leaked back to URL query: {dict(captured['url'].query)}"
            )

    @pytest.mark.asyncio
    async def test_initialize_mysql_charset_translates_and_strips_url(self):
        captured = {}

        def fake_create_engine(url, **kwargs):
            captured["url"] = url
            captured["kwargs"] = kwargs
            raise RuntimeError("stop")

        dbm = DatabaseManager(
            database_url="mysql+aiomysql://host/app?charset=utf8mb4"
        )
        with patch("db.database.create_async_engine", side_effect=fake_create_engine):
            with pytest.raises(RuntimeError, match="stop"):
                await dbm.initialize()

        assert captured["kwargs"]["connect_args"]["charset"] == "utf8mb4"
        # Round 4: MySQL too — even though aiomysql.connect accepts charset
        # verbatim (no TypeError risk like asyncpg's application_name), we
        # still strip for consistency, single translator, predictable shape.
        assert "charset" not in captured["url"].query

    @pytest.mark.asyncio
    async def test_initialize_mysql_autocommit_coerced_and_strips_url(self):
        captured = {}

        def fake_create_engine(url, **kwargs):
            captured["url"] = url
            captured["kwargs"] = kwargs
            raise RuntimeError("stop")

        dbm = DatabaseManager(
            database_url="mysql+aiomysql://host/app?autocommit=false"
        )
        with patch("db.database.create_async_engine", side_effect=fake_create_engine):
            with pytest.raises(RuntimeError, match="stop"):
                await dbm.initialize()

        # The original bug from TestMysqlQueryParams.test_autocommit_false_coerced_to_bool:
        # without coercion at parse time, "false" reaches aiomysql as truthy str.
        assert captured["kwargs"]["connect_args"]["autocommit"] is False
        assert "autocommit" not in captured["url"].query

    @pytest.mark.asyncio
    async def test_initialize_pg_unknown_key_fails_at_init_not_connect(self):
        """Fail-loud invariant: bad DSN keys raise immediately, not 10s later
        when the connection pool tries to acquire a real asyncpg conn."""
        dbm = DatabaseManager(
            database_url="postgresql+asyncpg://host/app?bogus=value"
        )
        # Patch create_async_engine so the test never tries to actually connect.
        # We expect _parse_db_query_params to raise BEFORE create_async_engine
        # is called; if it raises after, our refactor regressed.
        with patch(
            "db.database.create_async_engine",
            side_effect=AssertionError("create_async_engine should not be called"),
        ):
            with pytest.raises(ValueError, match="Unsupported DSN query param 'bogus'"):
                await dbm.initialize()

    @pytest.mark.asyncio
    async def test_initialize_pg_single_url_matches_failover_kwargs(self):
        """End-to-end semantic equivalence: same DSN, same shape via either path.

        Round 4's stated goal — DatabaseManager is the *single* DSN translator.
        Before round 4 this would have failed: single-URL handled application_name
        only; failover handled the full set. Different paths, different kwargs.
        """
        dsn = (
            "postgresql+asyncpg://host/app"
            "?sslmode=require&application_name=af&command_timeout=30"
        )

        # 1) Single-URL path: capture what initialize() builds.
        captured = {}

        def fake_create_engine(url, **kwargs):
            captured["kwargs"] = kwargs
            raise RuntimeError("stop")

        dbm = DatabaseManager(database_url=dsn)
        with patch("db.database.create_async_engine", side_effect=fake_create_engine):
            with pytest.raises(RuntimeError, match="stop"):
                await dbm.initialize()

        single_url_kwargs = captured["kwargs"]["connect_args"]

        # 2) Failover path: parse via _parse_db_url (excluding host/port/auth
        # which only the failover path needs) + _apply_session_tz_kwargs.
        _, failover_kwargs = DatabaseManager._parse_db_url(dsn)
        # Strip the host/port/auth — those are the failover-specific bits
        # that don't appear in the single-URL connect_args (SQLAlchemy gets
        # them via the URL itself, not connect_args).
        for k in ("host", "port", "database", "db", "user", "password"):
            failover_kwargs.pop(k, None)
        failover_kwargs = DatabaseManager._apply_session_tz_kwargs(
            "postgres", failover_kwargs
        )

        assert single_url_kwargs == failover_kwargs, (
            f"Single-URL vs failover diverged on the same DSN:\n"
            f"single: {single_url_kwargs!r}\n"
            f"failover: {failover_kwargs!r}"
        )


class TestDialectSmokingGunsRound4:
    """SQLAlchemy asyncpg dialect: after our sanitize step, the opts dict
    handed to asyncpg.connect must not contain any key that DatabaseManager
    has claimed. These are the actual TypeError-at-connect-time risks the
    refactor closes; tests round 3 handled `application_name` only.
    """

    def _consumed_then_opts(self, url_str: str, driver: str):
        from sqlalchemy.engine import make_url
        from sqlalchemy.dialects.postgresql import asyncpg as asyncpg_dialect

        raw_url = make_url(url_str)
        _, consumed = DatabaseManager._parse_db_query_params(raw_url, driver)
        sanitized = raw_url.difference_update_query(consumed)
        _, opts = asyncpg_dialect.dialect().create_connect_args(sanitized)
        return consumed, opts

    def test_sanitized_url_no_consumed_keys_in_dialect_opts(self):
        """Composite assertion: ALL keys DatabaseManager consumed are absent
        from dialect.create_connect_args opts. This is the round 4 invariant
        in its strongest form — if any single new key is added to a whitelist
        but the URL strip path is forgotten, this test fails."""
        consumed, opts = self._consumed_then_opts(
            "postgresql+asyncpg://host/app"
            "?sslmode=require&command_timeout=30&application_name=af",
            "postgres",
        )
        for key in consumed:
            assert key not in opts, (
                f"Sanitized URL still emits '{key}' to dialect opts: {opts!r}. "
                "DatabaseManager._parse_db_query_params claimed this key but "
                "the URL strip step (difference_update_query) didn't take it "
                "off the URL — single-URL path would TypeError at connect."
            )

    def test_unsanitized_url_leaks_at_least_one_consumed_key(self):
        """Negative control across the round 4 set, not just application_name.
        If SQLAlchemy upstream ever stops dumping these to top-level opts
        (e.g. translates sslmode → server_settings on its own), at least one
        of these assertions will start failing and we'll know our strip step
        for that key has become redundant."""
        from sqlalchemy.engine import make_url
        from sqlalchemy.dialects.postgresql import asyncpg as asyncpg_dialect

        raw_url = make_url(
            "postgresql+asyncpg://host/app"
            "?sslmode=require&command_timeout=30&application_name=af"
        )
        _, opts = asyncpg_dialect.dialect().create_connect_args(raw_url)

        leaked = {
            key for key in ("sslmode", "command_timeout", "application_name")
            if key in opts
        }
        assert leaked, (
            "SQLAlchemy asyncpg dialect no longer leaks ANY of "
            "{sslmode, command_timeout, application_name} to top-level opts. "
            "Round 4's URL-strip step may now be unnecessary for all of them. "
            "Re-verify each before simplifying."
        )
