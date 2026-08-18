#!/usr/bin/env python3
"""
Export users from an old ArtifactFlow PostgreSQL database to the CSV accepted by
POST /api/v1/admin/users/bulk-import, and optionally upload it to a new system.
It can also copy users DB-to-DB while preserving hashed_password.

Plaintext user passwords cannot be recovered from the database. For API import,
the CSV password column is populated from --initial-password, or with per-user
generated passwords when --generate-passwords is used. For DB-to-DB copy, use a
blank CSV password column as an archive and copy hashed_password directly.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import secrets
import string
import sys
from dataclasses import dataclass
from datetime import datetime
from getpass import getpass
from pathlib import Path
from typing import Iterable, Sequence
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
DEFAULT_ENV_FILE = ROOT / "deploy" / ".env"
DEFAULT_CSV_OUT = ROOT / "data" / "user-import.csv"
DEFAULT_BUNDLE_OUT = ROOT / "data" / "user-migration.json"
BUNDLE_FORMAT = "artifactflow-user-migration"
BUNDLE_VERSION = 1


@dataclass(frozen=True)
class ExportedUser:
    source_id: str | None
    username: str
    hashed_password: str
    display_name: str | None
    role: str
    is_active: bool
    password_version: int
    must_change_password: bool
    password_changed_at: datetime | None
    password_history_json: str
    created_at: datetime | None
    updated_at: datetime | None
    dept_path: list[str]


def _env_first(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None


def _postgres_dsn(raw: str) -> str:
    """asyncpg accepts postgresql://, not SQLAlchemy's postgresql+asyncpg://."""
    return (
        raw.replace("postgresql+asyncpg://", "postgresql://", 1)
        .replace("postgres+asyncpg://", "postgres://", 1)
        .replace("postgresql+psycopg://", "postgresql://", 1)
        .replace("postgresql+psycopg2://", "postgresql://", 1)
    )


def _quote_ident(value: str) -> str:
    if not value.replace("_", "").isalnum() or value[0].isdigit():
        raise ValueError(f"Unsafe SQL identifier: {value!r}")
    return '"' + value.replace('"', '""') + '"'


def _dt_to_json(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _dt_from_json(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _user_to_bundle_dict(user: ExportedUser) -> dict:
    return {
        "source_id": user.source_id,
        "username": user.username,
        "hashed_password": user.hashed_password,
        "display_name": user.display_name,
        "role": user.role,
        "is_active": user.is_active,
        "password_version": user.password_version,
        "must_change_password": user.must_change_password,
        "password_changed_at": _dt_to_json(user.password_changed_at),
        "password_history_json": user.password_history_json,
        "created_at": _dt_to_json(user.created_at),
        "updated_at": _dt_to_json(user.updated_at),
        "dept_path": user.dept_path,
    }


def _user_from_bundle_dict(raw: dict) -> ExportedUser:
    return ExportedUser(
        source_id=raw.get("source_id"),
        username=str(raw["username"]),
        hashed_password=str(raw["hashed_password"]),
        display_name=raw.get("display_name"),
        role=str(raw.get("role") or "user"),
        is_active=bool(raw.get("is_active", True)),
        password_version=int(raw.get("password_version") or 0),
        must_change_password=bool(raw.get("must_change_password", False)),
        password_changed_at=_dt_from_json(raw.get("password_changed_at")),
        password_history_json=str(raw.get("password_history_json") or "[]"),
        created_at=_dt_from_json(raw.get("created_at")),
        updated_at=_dt_from_json(raw.get("updated_at")),
        dept_path=[str(seg) for seg in raw.get("dept_path", [])],
    )


def write_bundle(
    users: Sequence[ExportedUser],
    bundle_out: Path,
    *,
    source_schema: str,
) -> None:
    from utils.time import utc_now

    bundle_out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "format": BUNDLE_FORMAT,
        "version": BUNDLE_VERSION,
        "exported_at": utc_now().isoformat(),
        "source_schema": source_schema,
        "users": [_user_to_bundle_dict(user) for user in users],
    }
    bundle_out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_bundle(bundle_in: Path) -> list[ExportedUser]:
    payload = json.loads(bundle_in.read_text(encoding="utf-8"))
    if payload.get("format") != BUNDLE_FORMAT:
        raise RuntimeError(f"Unsupported migration bundle format: {payload.get('format')!r}")
    if payload.get("version") != BUNDLE_VERSION:
        raise RuntimeError(f"Unsupported migration bundle version: {payload.get('version')!r}")
    return [_user_from_bundle_dict(raw) for raw in payload.get("users", [])]


async def _table_exists(conn, schema: str, table: str) -> bool:
    return bool(
        await conn.fetchval(
            """
            SELECT EXISTS (
              SELECT 1
              FROM information_schema.tables
              WHERE table_schema = $1 AND table_name = $2
            )
            """,
            schema,
            table,
        )
    )


async def _columns(conn, schema: str, table: str) -> set[str]:
    rows = await conn.fetch(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = $1 AND table_name = $2
        """,
        schema,
        table,
    )
    return {r["column_name"] for r in rows}


async def fetch_users(
    db_url: str,
    *,
    schema: str,
    include_inactive: bool,
    include_admins: bool,
    limit: int | None,
) -> list[ExportedUser]:
    import asyncpg

    conn = await asyncpg.connect(_postgres_dsn(db_url))
    try:
        if not await _table_exists(conn, schema, "users"):
            raise RuntimeError(f"Table {schema}.users does not exist")

        user_cols = await _columns(conn, schema, "users")
        if "username" not in user_cols:
            raise RuntimeError(f"Table {schema}.users has no username column")
        if "hashed_password" not in user_cols:
            raise RuntimeError(f"Table {schema}.users has no hashed_password column")

        dept_available = (
            "department_id" in user_cols
            and await _table_exists(conn, schema, "departments")
        )

        schema_sql = _quote_ident(schema)
        id_expr = "u.id" if "id" in user_cols else "NULL::text"
        display_expr = "u.display_name" if "display_name" in user_cols else "NULL::text"
        role_expr = "u.role" if "role" in user_cols else "'user'::text"
        active_expr = "u.is_active" if "is_active" in user_cols else "TRUE"
        pwd_version_expr = "u.password_version" if "password_version" in user_cols else "0"
        must_change_expr = (
            "u.must_change_password" if "must_change_password" in user_cols else "FALSE"
        )
        pwd_changed_expr = (
            "u.password_changed_at"
            if "password_changed_at" in user_cols
            else "NULL::timestamp"
        )
        pwd_history_expr = (
            "COALESCE(u.password_history::text, '[]')"
            if "password_history" in user_cols
            else "'[]'"
        )
        created_expr = "u.created_at" if "created_at" in user_cols else "NULL::timestamp"
        updated_expr = "u.updated_at" if "updated_at" in user_cols else "NULL::timestamp"

        where: list[str] = []
        # The archive/import format represents password identities only.  Newer
        # source databases may also contain SSO-only users with no password;
        # never silently turn those into local accounts during migration.
        if "auth_provider" in user_cols:
            where.append("u.auth_provider = 'local_password'")
        if not include_inactive and "is_active" in user_cols:
            where.append("u.is_active IS TRUE")
        if not include_admins and "role" in user_cols:
            where.append("COALESCE(u.role, 'user') <> 'admin'")
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        limit_sql = "LIMIT $1" if limit is not None else ""
        args: list[int] = [limit] if limit is not None else []

        if dept_available:
            sql = f"""
            WITH RECURSIVE dept_paths AS (
              SELECT id, parent_id, name, ARRAY[name::text] AS path
              FROM {schema_sql}.departments
              WHERE parent_id IS NULL
              UNION ALL
              SELECT d.id, d.parent_id, d.name, dp.path || d.name::text
              FROM {schema_sql}.departments d
              JOIN dept_paths dp ON d.parent_id = dp.id
            )
            SELECT
              {id_expr}::text AS source_id,
              u.username::text AS username,
              u.hashed_password::text AS hashed_password,
              {display_expr}::text AS display_name,
              {role_expr}::text AS role,
              {active_expr}::bool AS is_active,
              {pwd_version_expr}::int AS password_version,
              {must_change_expr}::bool AS must_change_password,
              {pwd_changed_expr}::timestamp AS password_changed_at,
              {pwd_history_expr}::text AS password_history_json,
              {created_expr}::timestamp AS created_at,
              {updated_expr}::timestamp AS updated_at,
              COALESCE(dp.path, ARRAY[]::text[]) AS dept_path
            FROM {schema_sql}.users u
            LEFT JOIN dept_paths dp ON dp.id = u.department_id
            {where_sql}
            ORDER BY u.username
            {limit_sql}
            """
        else:
            sql = f"""
            SELECT
              {id_expr}::text AS source_id,
              u.username::text AS username,
              u.hashed_password::text AS hashed_password,
              {display_expr}::text AS display_name,
              {role_expr}::text AS role,
              {active_expr}::bool AS is_active,
              {pwd_version_expr}::int AS password_version,
              {must_change_expr}::bool AS must_change_password,
              {pwd_changed_expr}::timestamp AS password_changed_at,
              {pwd_history_expr}::text AS password_history_json,
              {created_expr}::timestamp AS created_at,
              {updated_expr}::timestamp AS updated_at,
              ARRAY[]::text[] AS dept_path
            FROM {schema_sql}.users u
            {where_sql}
            ORDER BY u.username
            {limit_sql}
            """

        rows = await conn.fetch(sql, *args)
        return [
            ExportedUser(
                source_id=r["source_id"],
                username=r["username"],
                hashed_password=r["hashed_password"],
                display_name=r["display_name"],
                role=r["role"],
                is_active=bool(r["is_active"]),
                password_version=int(r["password_version"] or 0),
                must_change_password=bool(r["must_change_password"]),
                password_changed_at=r["password_changed_at"],
                password_history_json=r["password_history_json"] or "[]",
                created_at=r["created_at"],
                updated_at=r["updated_at"],
                dept_path=list(r["dept_path"] or []),
            )
            for r in rows
        ]
    finally:
        await conn.close()


def _generated_password() -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*()-_=+"
    while True:
        value = (
            secrets.choice(string.ascii_uppercase)
            + secrets.choice(string.ascii_lowercase)
            + secrets.choice(string.digits)
            + secrets.choice("!@#$%^&*")
            + "".join(secrets.choice(alphabet) for _ in range(16))
        )
        if len(value) <= 128:
            return value


def _check_dept_depth(users: Sequence[ExportedUser], *, truncate: bool) -> None:
    too_deep = [u for u in users if len(u.dept_path) > 3]
    if not too_deep or truncate:
        return
    examples = ", ".join(
        f"{u.username}: {' / '.join(u.dept_path)}" for u in too_deep[:5]
    )
    raise RuntimeError(
        "CSV import supports dept_l1/dept_l2/dept_l3 only; "
        f"{len(too_deep)} user(s) have deeper department paths. "
        f"Examples: {examples}. Re-run with --truncate-dept-path to keep the first 3 levels."
    )


def _warn_invalid_usernames(users: Iterable[ExportedUser]) -> int:
    from utils.validators import validate_username

    invalid = 0
    examples: list[str] = []
    for user in users:
        try:
            validate_username(user.username)
        except ValueError as exc:
            invalid += 1
            if len(examples) < 5:
                examples.append(f"{user.username!r} ({exc})")
    if invalid:
        print(
            f"WARNING: {invalid} username(s) do not match the new-system policy; "
            f"the import endpoint will mark those rows failed. Examples: {', '.join(examples)}",
            file=sys.stderr,
        )
    return invalid


def write_csv(
    users: Sequence[ExportedUser],
    csv_out: Path,
    *,
    initial_password: str | None,
    generate_passwords: bool,
    blank_password: bool,
    truncate_dept_path: bool,
) -> None:
    if blank_password and (generate_passwords or initial_password):
        raise RuntimeError("--blank-csv-password cannot be combined with CSV passwords")

    if not blank_password and not generate_passwords:
        from utils.password_policy import validate_password_strength

        if not initial_password:
            raise RuntimeError(
                "Provide --initial-password or set ARTIFACTFLOW_USER_IMPORT_INITIAL_PASSWORD, "
                "use --generate-passwords, or use --blank-csv-password for archive-only CSV."
            )
        validate_password_strength(initial_password)

    if not blank_password:
        _check_dept_depth(users, truncate=truncate_dept_path)

    csv_out.parent.mkdir(parents=True, exist_ok=True)
    with csv_out.open("w", encoding="utf-8-sig", newline="") as f:
        fieldnames = [
            "username",
            "password",
            "display_name",
            "dept_l1",
            "dept_l2",
            "dept_l3",
        ]
        if blank_password:
            fieldnames.extend(["dept_path", "role", "is_active", "source_id"])
        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames,
        )
        writer.writeheader()
        for user in users:
            if blank_password:
                password = ""
            else:
                from utils.password_policy import validate_password_strength

                password = _generated_password() if generate_passwords else initial_password
                assert password is not None
                validate_password_strength(password)
            path = list(user.dept_path[:3])
            while len(path) < 3:
                path.append("")
            row = {
                "username": user.username,
                "password": password,
                "display_name": user.display_name or "",
                "dept_l1": path[0],
                "dept_l2": path[1],
                "dept_l3": path[2],
            }
            if blank_password:
                row.update(
                    {
                        "dept_path": " / ".join(user.dept_path),
                        "role": user.role,
                        "is_active": str(user.is_active).lower(),
                        "source_id": user.source_id or "",
                    }
                )
            writer.writerow(row)


def _check_bcrypt_hashes(users: Sequence[ExportedUser], *, allow_non_bcrypt: bool) -> None:
    bad = [
        user for user in users
        if not user.hashed_password.startswith(("$2a$", "$2b$", "$2y$"))
    ]
    if not bad:
        return
    examples = ", ".join(f"{u.username}: {u.hashed_password[:12]}..." for u in bad[:5])
    message = (
        f"{len(bad)} user(s) do not look like bcrypt hashes; old passwords may not "
        f"work in the new system. Examples: {examples}"
    )
    if allow_non_bcrypt:
        print(f"WARNING: {message}", file=sys.stderr)
        return
    raise RuntimeError(message + ". Re-run with --allow-non-bcrypt-hashes to copy anyway.")


async def _resolve_department_path_db(
    conn,
    *,
    schema_sql: str,
    path: Sequence[str],
) -> str | None:
    import asyncpg

    parent_id: str | None = None
    for raw_name in path:
        name = raw_name.strip()
        if not name:
            continue

        if parent_id is None:
            existing = await conn.fetchval(
                f"""
                SELECT id
                FROM {schema_sql}.departments
                WHERE parent_id IS NULL AND name = $1
                """,
                name,
            )
        else:
            existing = await conn.fetchval(
                f"""
                SELECT id
                FROM {schema_sql}.departments
                WHERE parent_id = $1 AND name = $2
                """,
                parent_id,
                name,
            )
        if existing:
            parent_id = existing
            continue

        dept_id = f"dept-{uuid4()}"
        try:
            await conn.execute(
                f"""
                INSERT INTO {schema_sql}.departments (id, parent_id, name)
                VALUES ($1, $2, $3)
                """,
                dept_id,
                parent_id,
                name,
            )
            parent_id = dept_id
        except asyncpg.UniqueViolationError:
            if parent_id is None:
                parent_id = await conn.fetchval(
                    f"""
                    SELECT id
                    FROM {schema_sql}.departments
                    WHERE parent_id IS NULL AND name = $1
                    """,
                    name,
                )
            else:
                parent_id = await conn.fetchval(
                    f"""
                    SELECT id
                    FROM {schema_sql}.departments
                    WHERE parent_id = $1 AND name = $2
                    """,
                    parent_id,
                    name,
                )
            if parent_id is None:
                raise
    return parent_id


async def copy_users_to_target_db(
    users: Sequence[ExportedUser],
    *,
    target_db_url: str,
    target_schema: str,
    on_conflict: str,
    allow_non_bcrypt_hashes: bool,
    reset_password_age: bool,
) -> dict[str, int]:
    import asyncpg

    _check_bcrypt_hashes(users, allow_non_bcrypt=allow_non_bcrypt_hashes)

    conn = await asyncpg.connect(_postgres_dsn(target_db_url))
    try:
        await conn.execute("SET TIME ZONE 'UTC'")
        if not await _table_exists(conn, target_schema, "users"):
            raise RuntimeError(f"Table {target_schema}.users does not exist in target DB")
        if not await _table_exists(conn, target_schema, "departments"):
            raise RuntimeError(f"Table {target_schema}.departments does not exist in target DB")

        schema_sql = _quote_ident(target_schema)
        created = 0
        updated = 0
        skipped = 0
        async with conn.transaction():
            for user in users:
                password_changed_at = None if reset_password_age else user.password_changed_at
                must_change_password = False if reset_password_age else user.must_change_password
                existing_id = await conn.fetchval(
                    f"SELECT id FROM {schema_sql}.users "
                    "WHERE auth_provider = 'local_password' AND auth_subject = $1",
                    user.username,
                )
                if existing_id:
                    if on_conflict == "skip":
                        skipped += 1
                        continue
                    if on_conflict == "update-password":
                        await conn.execute(
                            f"""
                            UPDATE {schema_sql}.users
                            SET hashed_password = $2,
                                password_version = $3,
                                must_change_password = $4,
                                password_changed_at = COALESCE(
                                    $5::timestamp, now() AT TIME ZONE 'UTC'
                                ),
                                password_history = $6::json,
                                updated_at = now() AT TIME ZONE 'UTC'
                            WHERE id = $1
                            """,
                            existing_id,
                            user.hashed_password,
                            user.password_version,
                            must_change_password,
                            password_changed_at,
                            user.password_history_json,
                        )
                    elif on_conflict == "update-all":
                        department_id = await _resolve_department_path_db(
                            conn, schema_sql=schema_sql, path=user.dept_path
                        )
                        await conn.execute(
                            f"""
                            UPDATE {schema_sql}.users
                            SET hashed_password = $2,
                                display_name = $3,
                                role = $4,
                                is_active = $5,
                                password_version = $6,
                                must_change_password = $7,
                                password_changed_at = COALESCE(
                                    $8::timestamp, now() AT TIME ZONE 'UTC'
                                ),
                                password_history = $9::json,
                                department_id = $10,
                                updated_at = now() AT TIME ZONE 'UTC'
                            WHERE id = $1
                            """,
                            existing_id,
                            user.hashed_password,
                            user.display_name,
                            user.role,
                            user.is_active,
                            user.password_version,
                            must_change_password,
                            password_changed_at,
                            user.password_history_json,
                            department_id,
                        )
                    else:
                        raise RuntimeError(f"Unknown on-conflict mode: {on_conflict}")
                    updated += 1
                    continue

                department_id = await _resolve_department_path_db(
                    conn, schema_sql=schema_sql, path=user.dept_path
                )
                await conn.execute(
                    f"""
                    INSERT INTO {schema_sql}.users (
                        id,
                        auth_provider,
                        auth_subject,
                        username,
                        hashed_password,
                        display_name,
                        role,
                        is_active,
                        password_version,
                        must_change_password,
                        password_changed_at,
                        password_history,
                        department_id,
                        created_at,
                        updated_at
                    )
                    VALUES (
                        $1, 'local_password', $2, $2, $3, $4, $5, $6, $7, $8,
                        COALESCE($9::timestamp, now() AT TIME ZONE 'UTC'),
                        $10::json,
                        $11,
                        COALESCE($12::timestamp, now() AT TIME ZONE 'UTC'),
                        COALESCE($13::timestamp, now() AT TIME ZONE 'UTC')
                    )
                    """,
                    f"user-{uuid4().hex}",
                    user.username,
                    user.hashed_password,
                    user.display_name,
                    user.role,
                    user.is_active,
                    user.password_version,
                    must_change_password,
                    password_changed_at,
                    user.password_history_json,
                    department_id,
                    user.created_at,
                    user.updated_at,
                )
                created += 1

        return {"created": created, "updated": updated, "skipped": skipped}
    finally:
        await conn.close()


def _api_v1_base(raw: str) -> str:
    base = raw.rstrip("/")
    return base if base.endswith("/api/v1") else f"{base}/api/v1"


async def import_csv(
    *,
    api_url: str,
    admin_username: str,
    admin_password: str,
    csv_path: Path,
) -> dict:
    import httpx

    api_base = _api_v1_base(api_url)
    timeout = httpx.Timeout(connect=10, read=180, write=180, pool=10)
    async with httpx.AsyncClient(timeout=timeout) as client:
        login_resp = await client.post(
            f"{api_base}/auth/login",
            json={"username": admin_username, "password": admin_password},
        )
        login_resp.raise_for_status()
        token = login_resp.json()["access_token"]

        with csv_path.open("rb") as f:
            import_resp = await client.post(
                f"{api_base}/admin/users/bulk-import",
                headers={"Authorization": f"Bearer {token}"},
                files={"file": (csv_path.name, f, "text/csv")},
            )
        import_resp.raise_for_status()
        return import_resp.json()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export/import ArtifactFlow users, including offline bundles that preserve password hashes."
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=DEFAULT_ENV_FILE,
        help=f"dotenv file to load (default: {DEFAULT_ENV_FILE})",
    )
    parser.add_argument(
        "--source-db-url",
        help=(
            "Old PostgreSQL URL. Env fallback: OLD_ARTIFACTFLOW_DATABASE_URL, "
            "OLD_DATABASE_URL, SOURCE_DATABASE_URL, then ARTIFACTFLOW_DATABASE_URL."
        ),
    )
    parser.add_argument("--schema", default="public", help="PostgreSQL schema (default: public)")
    parser.add_argument(
        "--target-schema",
        default=None,
        help="Target PostgreSQL schema (default: same as --schema)",
    )
    parser.add_argument(
        "--csv-out",
        type=Path,
        default=None,
        help=(
            "CSV output path. When exporting from a source DB, default is "
            f"{DEFAULT_CSV_OUT}; with --bundle-in, CSV is written only if this is set."
        ),
    )
    parser.add_argument(
        "--no-csv",
        action="store_true",
        help="Do not write the archive/import CSV.",
    )
    parser.add_argument(
        "--bundle-out",
        type=Path,
        default=None,
        help=(
            "Write an offline JSON migration bundle containing users, departments paths, "
            f"and password hashes (default suggestion: {DEFAULT_BUNDLE_OUT})."
        ),
    )
    parser.add_argument(
        "--bundle-in",
        type=Path,
        default=None,
        help="Read users from a JSON migration bundle instead of connecting to the old DB.",
    )
    parser.add_argument(
        "--initial-password",
        help=(
            "Shared temporary password for all imported users. Env fallback: "
            "ARTIFACTFLOW_USER_IMPORT_INITIAL_PASSWORD or USER_IMPORT_INITIAL_PASSWORD."
        ),
    )
    parser.add_argument(
        "--generate-passwords",
        action="store_true",
        help="Generate a different temporary password per user and write it into the CSV.",
    )
    parser.add_argument(
        "--blank-csv-password",
        action="store_true",
        help="Write an empty CSV password column. Use this when copying hashed passwords DB-to-DB.",
    )
    parser.add_argument(
        "--include-inactive",
        action="store_true",
        help="Include inactive users from the old database.",
    )
    parser.add_argument(
        "--include-admins",
        action="store_true",
        help="Include old admin users. By default admins are skipped because CSV import creates role=user.",
    )
    parser.add_argument(
        "--truncate-dept-path",
        action="store_true",
        help="If a department path is deeper than 3 levels, keep only the first 3 levels.",
    )
    parser.add_argument("--limit", type=int, help="Export at most N users, useful for smoke tests.")
    parser.add_argument(
        "--import",
        dest="do_import",
        action="store_true",
        help="After writing the CSV, upload it to the new system.",
    )
    parser.add_argument(
        "--copy-to-target-db",
        action="store_true",
        help="Copy users directly into the target DB, preserving hashed_password.",
    )
    parser.add_argument(
        "--target-db-url",
        help=(
            "New PostgreSQL URL for --copy-to-target-db. Env fallback: "
            "NEW_ARTIFACTFLOW_DATABASE_URL, NEW_DATABASE_URL, TARGET_DATABASE_URL, "
            "ARTIFACTFLOW_TARGET_DATABASE_URL, or ARTIFACTFLOW_DATABASE_URL when using "
            "--bundle-in / OLD/SOURCE env separation."
        ),
    )
    parser.add_argument(
        "--on-conflict",
        choices=["skip", "update-password", "update-all"],
        default="skip",
        help="What to do when target already has the username (default: skip).",
    )
    parser.add_argument(
        "--allow-non-bcrypt-hashes",
        action="store_true",
        help="Copy password hashes that do not look like bcrypt anyway.",
    )
    parser.add_argument(
        "--reset-password-age",
        action="store_true",
        help=(
            "For DB copy, set password_changed_at to now and must_change_password=false "
            "so migrated users can keep using old passwords without immediate forced reset."
        ),
    )
    parser.add_argument(
        "--new-api-url",
        help=(
            "New system base URL. Env fallback: NEW_ARTIFACTFLOW_API_URL, "
            "NEW_API_URL, ARTIFACTFLOW_API_URL, NEXT_PUBLIC_API_URL, or https://$AF_DOMAIN."
        ),
    )
    parser.add_argument(
        "--admin-username",
        default=None,
        help="New system admin username. Env fallback: NEW_ADMIN_USERNAME, ADMIN_USERNAME. Default: admin.",
    )
    parser.add_argument(
        "--admin-password",
        default=None,
        help="New system admin password. Env fallback: NEW_ADMIN_PASSWORD, ADMIN_PASSWORD; prompts if missing.",
    )
    return parser


async def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.env_file.exists():
        from dotenv import load_dotenv

        load_dotenv(args.env_file)
    else:
        print(f"WARNING: env file not found: {args.env_file}", file=sys.stderr)

    sys.path.insert(0, str(SRC_DIR))

    if args.bundle_in and args.bundle_out:
        parser.error("--bundle-in and --bundle-out are mutually exclusive")
    if args.generate_passwords and args.initial_password:
        parser.error("--generate-passwords and --initial-password are mutually exclusive")

    blank_csv_password = (
        args.blank_csv_password
        or args.copy_to_target_db
        or bool(args.bundle_out)
        or bool(args.bundle_in)
    )
    if blank_csv_password and (args.generate_passwords or args.initial_password):
        parser.error("--blank-csv-password / bundle DB-copy mode cannot be combined with CSV passwords")
    if args.do_import and (blank_csv_password or args.copy_to_target_db):
        parser.error("--import requires non-empty CSV passwords; use DB copy without --import")
    if args.do_import and args.no_csv:
        parser.error("--import needs a CSV file; remove --no-csv")

    source_env_url = _env_first(
        "OLD_ARTIFACTFLOW_DATABASE_URL",
        "OLD_DATABASE_URL",
        "SOURCE_DATABASE_URL",
        "ARTIFACTFLOW_OLD_DATABASE_URL",
    )
    source_db_url: str | None = None
    initial_password = args.initial_password or _env_first(
        "ARTIFACTFLOW_USER_IMPORT_INITIAL_PASSWORD",
        "USER_IMPORT_INITIAL_PASSWORD",
        "IMPORT_INITIAL_PASSWORD",
    )

    if args.bundle_in:
        users = load_bundle(args.bundle_in)
        if args.limit is not None:
            users = users[:args.limit]
        print(f"Loaded {len(users)} user row(s) from bundle {args.bundle_in}")
    else:
        source_db_url = args.source_db_url or source_env_url or os.getenv("ARTIFACTFLOW_DATABASE_URL")
        if not source_db_url:
            parser.error("missing --source-db-url and no database URL found in env")
        users = await fetch_users(
            source_db_url,
            schema=args.schema,
            include_inactive=args.include_inactive,
            include_admins=args.include_admins,
            limit=args.limit,
        )

    _warn_invalid_usernames(users)

    if args.bundle_out:
        write_bundle(users, args.bundle_out, source_schema=args.schema)
        print(f"Wrote migration bundle with {len(users)} user row(s) to {args.bundle_out}")
        print(
            "WARNING: the migration bundle contains password hashes; treat it like a password file.",
            file=sys.stderr,
        )

    csv_out: Path | None = None
    if not args.no_csv:
        csv_out = args.csv_out
        if csv_out is None and not args.bundle_in:
            csv_out = DEFAULT_CSV_OUT

    if csv_out is not None:
        write_csv(
            users,
            csv_out,
            initial_password=initial_password,
            generate_passwords=args.generate_passwords,
            blank_password=blank_csv_password,
            truncate_dept_path=args.truncate_dept_path,
        )
        print(f"Wrote {len(users)} user row(s) to {csv_out}")

    if not users:
        return 0

    if args.copy_to_target_db:
        target_db_url = args.target_db_url or _env_first(
            "NEW_ARTIFACTFLOW_DATABASE_URL",
            "NEW_DATABASE_URL",
            "TARGET_DATABASE_URL",
            "ARTIFACTFLOW_TARGET_DATABASE_URL",
        )
        if not target_db_url and (args.bundle_in or source_env_url):
            target_db_url = os.getenv("ARTIFACTFLOW_DATABASE_URL")
        if not target_db_url:
            parser.error("missing --target-db-url and no target database URL found in env")
        if source_db_url and _postgres_dsn(target_db_url) == _postgres_dsn(source_db_url):
            parser.error("source and target database URLs are identical; refusing DB copy")
        copy_result = await copy_users_to_target_db(
            users,
            target_db_url=target_db_url,
            target_schema=args.target_schema or args.schema,
            on_conflict=args.on_conflict,
            allow_non_bcrypt_hashes=args.allow_non_bcrypt_hashes,
            reset_password_age=args.reset_password_age,
        )
        print(
            "DB copy result: "
            f"created={copy_result['created']} "
            f"updated={copy_result['updated']} "
            f"skipped={copy_result['skipped']}"
        )

    if not args.do_import:
        if not args.copy_to_target_db:
            print("Import not run. Add --import to upload the CSV to the new system.")
        return 0

    api_url = args.new_api_url or _env_first(
        "NEW_ARTIFACTFLOW_API_URL",
        "NEW_API_URL",
        "ARTIFACTFLOW_API_URL",
        "NEXT_PUBLIC_API_URL",
    )
    if not api_url:
        domain = os.getenv("AF_DOMAIN")
        if domain:
            api_url = f"https://{domain}"
    if not api_url:
        parser.error("missing --new-api-url and no new-system API URL found in env")

    admin_username = (
        args.admin_username
        or _env_first("NEW_ADMIN_USERNAME", "ADMIN_USERNAME", "ARTIFACTFLOW_ADMIN_USERNAME")
        or "admin"
    )
    admin_password = args.admin_password or _env_first(
        "NEW_ADMIN_PASSWORD",
        "ADMIN_PASSWORD",
        "ARTIFACTFLOW_ADMIN_PASSWORD",
    )
    if not admin_password:
        admin_password = getpass("New system admin password: ")

    result = await import_csv(
        api_url=api_url,
        admin_username=admin_username,
        admin_password=admin_password,
        csv_path=csv_out or DEFAULT_CSV_OUT,
    )
    print(
        "Import result: "
        f"total={result.get('total_rows')} "
        f"created={len(result.get('created', []))} "
        f"failed={len(result.get('failed', []))} "
        f"skipped={len(result.get('skipped', []))}"
    )
    for key in ("failed", "skipped"):
        rows = result.get(key, [])
        if rows:
            print(f"{key} examples: {rows[:5]}")
    warnings = result.get("warnings", [])
    if warnings:
        print(f"warnings: {warnings}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
