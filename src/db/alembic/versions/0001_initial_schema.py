"""initial schema (squashed 0001-0003)

Revision ID: 0001
Revises:
Create Date: 2026-04-09

合并历史链 0001(initial)+ 0002(password policy 三列)+ 0003(artifact_blobs)为单一
initial —— 目标生产为全新建库,旧链无增量价值。0002 的存量用户 backfill
(must_change_password=True)被刻意丢弃:全新库无存量行,一次性数据迁移正是 squash
该剥离的部分。

注意:此迁移只在 PG/MySQL(生产/内网,走 alembic upgrade head)上执行;SQLite dev 走
create_all 不跑迁移 —— 新建库由 models.py 自动带全部表/列。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0001'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create all tables matching models.py."""
    # 跨方言根级去重：root_name_key 生成列 + UNIQUE 见 models.py Department
    # __table_args__ 的 uq_dept_root_name 注释。STORED generated column 在
    # SQLite 3.31+ / PostgreSQL 12+ / MySQL 5.7+ 都支持。
    op.create_table('departments',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('parent_id', sa.String(length=64), nullable=True),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column(
            'root_name_key', sa.String(length=128),
            sa.Computed('CASE WHEN parent_id IS NULL THEN name END', persisted=True),
            nullable=True,
        ),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['parent_id'], ['departments.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('parent_id', 'name', name='uq_dept_parent_name'),
        sa.UniqueConstraint('root_name_key', name='uq_dept_root_name'),
    )
    op.create_index(op.f('ix_departments_parent_id'), 'departments', ['parent_id'], unique=False)

    # users:含等保密码策略三列(原 0002)——must_change_password / password_changed_at /
    # password_history,语义见 models.py User。inline 进建表,新库即带列、无需 backfill。
    op.create_table('users',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('username', sa.String(length=64), nullable=False),
        sa.Column('hashed_password', sa.String(length=256), nullable=False),
        sa.Column('display_name', sa.String(length=128), nullable=True),
        sa.Column('role', sa.String(length=16), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('password_version', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('must_change_password', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('password_changed_at', sa.DateTime(), nullable=True),
        sa.Column('password_history', sa.JSON(), nullable=True),
        sa.Column('department_id', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['department_id'], ['departments.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_users_username'), 'users', ['username'], unique=True)
    op.create_index(op.f('ix_users_department_id'), 'users', ['department_id'], unique=False)

    op.create_table('conversations',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('active_branch', sa.String(length=64), nullable=True),
        sa.Column('title', sa.String(length=256), nullable=True),
        sa.Column('user_id', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_conversations_user_id'), 'conversations', ['user_id'], unique=False)
    op.create_index('ix_conversations_user_updated', 'conversations', ['user_id', 'updated_at'], unique=False)

    op.create_table('artifact_sessions',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['id'], ['conversations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )

    op.create_table('messages',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('conversation_id', sa.String(length=64), nullable=False),
        sa.Column('parent_id', sa.String(length=64), nullable=True),
        sa.Column('user_input', sa.Text(), nullable=False),
        sa.Column('response', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('metadata', sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_messages_conv_created', 'messages', ['conversation_id', 'created_at'], unique=False)
    op.create_index(op.f('ix_messages_conversation_id'), 'messages', ['conversation_id'], unique=False)
    op.create_index(op.f('ix_messages_parent_id'), 'messages', ['parent_id'], unique=False)

    op.create_table('artifacts',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('session_id', sa.String(length=64), nullable=False),
        sa.Column('content_type', sa.String(length=64), nullable=False),
        sa.Column('source', sa.String(length=32), nullable=False),
        sa.Column('title', sa.String(length=256), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('current_version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('metadata', sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(['session_id'], ['artifact_sessions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', 'session_id')
    )

    op.create_table('message_events',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('event_id', sa.String(length=96), nullable=True),
        sa.Column('message_id', sa.String(length=64), nullable=False),
        sa.Column('event_type', sa.String(length=32), nullable=False),
        sa.Column('agent_name', sa.String(length=64), nullable=True),
        sa.Column('data', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['message_id'], ['messages.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('event_id', name='uq_message_events_event_id')
    )
    op.create_index('ix_message_events_message', 'message_events', ['message_id'], unique=False)

    op.create_table('artifact_versions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('artifact_id', sa.String(length=64), nullable=False),
        sa.Column('session_id', sa.String(length=64), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('update_type', sa.String(length=32), nullable=False),
        sa.Column('changes', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['artifact_id', 'session_id'], ['artifacts.id', 'artifacts.session_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('artifact_id', 'session_id', 'version', name='uq_artifact_version')
    )
    op.create_index('ix_artifact_versions_artifact', 'artifact_versions', ['artifact_id', 'session_id'], unique=False)

    # artifact_blobs(原 0003):与文本热路径隔离的二进制存储,1:1 绑 Artifact(复合外键)。
    # 类型不依赖方言:泛型 LargeBinary(length=100MB)→ MySQL LONGBLOB / PG BYTEA / SQLite BLOB。
    op.create_table('artifact_blobs',
        sa.Column('artifact_id', sa.String(length=64), nullable=False),
        sa.Column('session_id', sa.String(length=64), nullable=False),
        sa.Column('data', sa.LargeBinary(length=100 * 1024 * 1024), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ['artifact_id', 'session_id'],
            ['artifacts.id', 'artifacts.session_id'],
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('artifact_id', 'session_id')
    )


def downgrade() -> None:
    """Drop all tables."""
    op.drop_table('artifact_blobs')
    op.drop_index('ix_artifact_versions_artifact', table_name='artifact_versions')
    op.drop_table('artifact_versions')
    op.drop_index('ix_message_events_message', table_name='message_events')
    op.drop_table('message_events')
    op.drop_table('artifacts')
    op.drop_index(op.f('ix_messages_parent_id'), table_name='messages')
    op.drop_index(op.f('ix_messages_conversation_id'), table_name='messages')
    op.drop_index('ix_messages_conv_created', table_name='messages')
    op.drop_table('messages')
    op.drop_table('artifact_sessions')
    op.drop_index('ix_conversations_user_updated', table_name='conversations')
    op.drop_index(op.f('ix_conversations_user_id'), table_name='conversations')
    op.drop_table('conversations')
    op.drop_index(op.f('ix_users_department_id'), table_name='users')
    op.drop_index(op.f('ix_users_username'), table_name='users')
    op.drop_table('users')
    op.drop_index(op.f('ix_departments_parent_id'), table_name='departments')
    op.drop_table('departments')
