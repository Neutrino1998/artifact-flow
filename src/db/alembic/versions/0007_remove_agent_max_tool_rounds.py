"""remove obsolete agent tool-round budget

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-11
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0007"
down_revision: Union[str, Sequence[str], None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("agents", "max_tool_rounds")


def downgrade() -> None:
    op.add_column(
        "agents",
        sa.Column(
            "max_tool_rounds",
            sa.Integer(),
            server_default="3",
            nullable=False,
        ),
    )
