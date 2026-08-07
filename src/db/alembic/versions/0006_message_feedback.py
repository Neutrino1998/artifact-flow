"""add message-level user feedback

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-07
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0006"
down_revision: Union[str, Sequence[str], None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "message_feedback",
        sa.Column("message_id", sa.String(length=64), nullable=False),
        sa.Column("rating", sa.String(length=16), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "rating IN ('positive', 'negative')",
            name="ck_message_feedback_rating",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"], ["messages.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("message_id"),
    )
    op.create_index(
        "ix_message_feedback_updated",
        "message_feedback",
        ["updated_at"],
        unique=False,
    )
    op.create_index(
        "ix_message_feedback_rating_updated",
        "message_feedback",
        ["rating", "updated_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_message_feedback_rating_updated", table_name="message_feedback"
    )
    op.drop_index("ix_message_feedback_updated", table_name="message_feedback")
    op.drop_table("message_feedback")
