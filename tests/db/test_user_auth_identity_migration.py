"""Real SQLite upgrade/downgrade coverage for the user identity cutover."""

from __future__ import annotations

import os
import sqlite3
import subprocess

import pytest


def _alembic(db_path, *args: str, succeeds: bool = True):
    env = os.environ.copy()
    env["ARTIFACTFLOW_DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"
    result = subprocess.run(
        ["alembic", *args],
        cwd=os.getcwd(),
        env=env,
        text=True,
        capture_output=True,
    )
    assert (result.returncode == 0) is succeeds, result.stdout + result.stderr
    return result


def test_user_identity_migration_round_trip(tmp_path):
    db_path = tmp_path / "identity.db"
    with sqlite3.connect(db_path) as conn:
        # Build the exact pre-0008 table shape directly. Earlier historical
        # migrations predate SQLite batch mode and cannot be replayed on SQLite;
        # this test is intentionally scoped to the new cutover migration.
        conn.executescript(
            """
            CREATE TABLE users (
                id VARCHAR(64) PRIMARY KEY,
                username VARCHAR(64) NOT NULL,
                hashed_password VARCHAR(256) NOT NULL,
                display_name VARCHAR(128),
                role VARCHAR(16) NOT NULL DEFAULT 'user',
                is_active BOOLEAN NOT NULL DEFAULT 1,
                password_version INTEGER NOT NULL DEFAULT 0,
                must_change_password BOOLEAN NOT NULL DEFAULT 0,
                password_changed_at DATETIME,
                password_history JSON,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                department_id VARCHAR(64)
            );
            CREATE UNIQUE INDEX ix_users_username ON users (username);
            """
        )
        conn.execute(
            "INSERT INTO users "
            "(id, username, hashed_password, role, is_active, password_version, "
            "must_change_password, password_history) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("local-1", "shared", "hash", "user", 1, 0, 0, "[]"),
        )
        conn.commit()

    _alembic(db_path, "stamp", "0007")
    _alembic(db_path, "upgrade", "head")
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT auth_provider, auth_subject FROM users WHERE id='local-1'"
        ).fetchone()
        assert row == ("local_password", "shared")
        auth_provider_column = next(
            row for row in conn.execute("PRAGMA table_info(users)") if row[1] == "auth_provider"
        )
        assert auth_provider_column[4] is None

        # Same display username is legal for a different provider subject.
        conn.execute(
            "INSERT INTO users "
            "(id, auth_provider, auth_subject, username, hashed_password, role, "
            "is_active, password_version, must_change_password, password_history) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "remote-1",
                "enterprise_sso",
                "subject-1",
                "shared",
                None,
                "user",
                1,
                0,
                0,
                "[]",
            ),
        )
        conn.commit()

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO users "
                "(id, auth_provider, auth_subject, username, hashed_password, role, "
                "is_active, password_version, must_change_password, password_history) "
                "VALUES ('remote-dup', 'enterprise_sso', 'subject-1', 'other', "
                "NULL, 'user', 1, 0, 0, '[]')"
            )
        conn.rollback()

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO users "
                "(id, auth_provider, auth_subject, username, hashed_password, role, "
                "is_active, password_version, must_change_password, password_history) "
                "VALUES ('bad-remote', 'enterprise_sso', 'subject-2', 'remote', "
                "'must-not-exist', 'user', 1, 0, 0, '[]')"
            )
        conn.rollback()

    # Downgrade refuses to erase the meaning of existing remote identities.
    _alembic(db_path, "downgrade", "0007", succeeds=False)
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM users WHERE auth_provider <> 'local_password'")
        conn.commit()
    _alembic(db_path, "downgrade", "0007")

    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        assert "auth_provider" not in columns
        assert "auth_subject" not in columns
