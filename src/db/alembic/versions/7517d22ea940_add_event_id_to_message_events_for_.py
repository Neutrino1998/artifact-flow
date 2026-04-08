"""add event_id to message_events for retry idempotency

Revision ID: 7517d22ea940
Revises: 95715088ebe9
Create Date: 2026-04-08 10:07:23.780217

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7517d22ea940'
down_revision: Union[str, Sequence[str], None] = '95715088ebe9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add event_id column with unique constraint for retry idempotency."""
    op.add_column('message_events', sa.Column('event_id', sa.String(96), nullable=True))
    op.create_unique_constraint('uq_message_events_event_id', 'message_events', ['event_id'])


def downgrade() -> None:
    """Remove event_id column."""
    op.drop_constraint('uq_message_events_event_id', 'message_events', type_='unique')
    op.drop_column('message_events', 'event_id')
