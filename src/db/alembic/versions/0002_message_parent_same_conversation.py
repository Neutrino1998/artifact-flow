"""enforce same-conversation message parents

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-10

Messages form a per-conversation tree. A stale frontend branch pointer once
created a child whose parent_id did not exist, leaving the frontend with no
root to render. Backfill existing bad parent pointers to root messages, then
make the invariant structural.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Clean dangling/cross-conversation parents and add the structural guard."""
    op.execute(
        """
        UPDATE messages
        SET parent_id = NULL
        WHERE parent_id IS NOT NULL
          AND NOT EXISTS (
            SELECT 1
            FROM (SELECT id, conversation_id FROM messages) AS parent_msg
            WHERE parent_msg.id = messages.parent_id
              AND parent_msg.conversation_id = messages.conversation_id
          )
        """
    )
    op.create_unique_constraint(
        "uq_messages_conversation_id_id",
        "messages",
        ["conversation_id", "id"],
    )
    op.create_foreign_key(
        "fk_messages_parent_same_conversation",
        "messages",
        "messages",
        ["conversation_id", "parent_id"],
        ["conversation_id", "id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    """Drop the same-conversation parent guard."""
    op.drop_constraint(
        "fk_messages_parent_same_conversation",
        "messages",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_messages_conversation_id_id",
        "messages",
        type_="unique",
    )
