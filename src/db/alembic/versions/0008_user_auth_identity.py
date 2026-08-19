"""add provider-scoped user authentication identities

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-18
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "0008"
down_revision: Union[str, Sequence[str], None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _auth_identity_string(length: int):
    return sa.String(length=length).with_variant(
        mysql.VARCHAR(
            length=length,
            charset="utf8mb4",
            collation="utf8mb4_bin",
        ),
        "mysql",
    )


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "auth_provider",
            _auth_identity_string(64),
            nullable=False,
            server_default="local_password",
        ),
    )
    op.add_column(
        "users",
        sa.Column("auth_subject", _auth_identity_string(256), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE users SET auth_subject = username "
            "WHERE auth_subject IS NULL"
        )
    )

    with op.batch_alter_table("users") as batch:
        batch.alter_column(
            "auth_provider",
            existing_type=_auth_identity_string(64),
            type_=_auth_identity_string(64),
            existing_nullable=False,
            server_default=None,
        )
        batch.alter_column(
            "auth_subject",
            existing_type=_auth_identity_string(256),
            type_=_auth_identity_string(256),
            nullable=False,
        )
        batch.alter_column(
            "hashed_password",
            existing_type=sa.String(length=256),
            nullable=True,
        )
        batch.drop_index("ix_users_username")
        batch.create_index("ix_users_username", ["username"], unique=False)
        batch.create_unique_constraint(
            "uq_users_auth_identity", ["auth_provider", "auth_subject"]
        )
        batch.create_check_constraint(
            "ck_users_auth_identity_nonempty",
            "length(auth_provider) > 0 AND length(auth_subject) > 0",
        )
        batch.create_check_constraint(
            "ck_users_auth_credentials",
            "(auth_provider = 'local_password' "
            "AND auth_subject = username "
            "AND hashed_password IS NOT NULL) "
            "OR (auth_provider <> 'local_password' "
            "AND hashed_password IS NULL "
            "AND must_change_password = false "
            "AND password_changed_at IS NULL)",
        )


def downgrade() -> None:
    bind = op.get_bind()
    remote_count = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM users "
            "WHERE auth_provider <> 'local_password'"
        )
    ).scalar_one()
    if remote_count:
        raise RuntimeError(
            "Cannot downgrade user auth identity while remote-provider users exist; "
            "restore the pre-migration database backup instead"
        )

    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("ck_users_auth_credentials", type_="check")
        batch.drop_constraint("ck_users_auth_identity_nonempty", type_="check")
        batch.drop_constraint("uq_users_auth_identity", type_="unique")
        batch.drop_index("ix_users_username")
        batch.create_index("ix_users_username", ["username"], unique=True)
        batch.alter_column(
            "hashed_password",
            existing_type=sa.String(length=256),
            nullable=False,
        )
        batch.drop_column("auth_subject")
        batch.drop_column("auth_provider")
