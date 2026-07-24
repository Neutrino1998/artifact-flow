"""store site notifications in shared database state

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-24

Physical multi-host deployments cannot make an online admin edit consistent
when each backend writes a target-local notifications.json. Store the small,
bulk-edited payload and its optimistic-lock revision in one singleton row.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, Sequence[str], None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    table = op.create_table(
        "site_notification_config",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("notifications", sa.JSON(), nullable=False),
        sa.Column("revision", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "id = 1", name="ck_site_notification_config_singleton"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.bulk_insert(table, [{"id": 1, "notifications": [], "revision": 0}])


def downgrade() -> None:
    op.drop_table("site_notification_config")
