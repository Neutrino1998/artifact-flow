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
        sa.Column('has_blob', sa.Boolean(), nullable=False, server_default=sa.text('false')),
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
    # 存储配额聚合用:SUM(size_bytes) 按 session_id 过滤/GROUP BY 走 index-only(复合
    # 主键以 artifact_id 打头无法服务)。见 models.py ArtifactBlob.__table_args__。
    op.create_index('ix_artifact_blobs_session_size', 'artifact_blobs', ['session_id', 'size_bytes'], unique=False)

    # ------------------------------------------------------------------
    # 工具/agent 注册表(config→DB 物化)。无存量数据,就地写进 squash 0001(沿用
    # has_blob 姿态,全新建库假设)。建表序:tool_units(被 tool_members/agent_units
    # 引用)→ agents → tool_members → agent_units。语义见 models.py 同名类。
    # ------------------------------------------------------------------
    op.create_table('tool_units',
        sa.Column('name', sa.String(length=64), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('visibility', sa.String(length=16), nullable=False, server_default='public'),
        sa.Column('defer', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('provider', sa.String(length=16), nullable=False, server_default='http'),
        sa.Column('source', sa.String(length=16), nullable=False),
        sa.Column('seed_hash', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('name'),
    )

    op.create_table('agents',
        sa.Column('name', sa.String(length=64), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('model', sa.String(length=64), nullable=False),
        sa.Column('max_tool_rounds', sa.Integer(), nullable=False, server_default='3'),
        sa.Column('internal', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('role_prompt', sa.Text(), nullable=False),
        sa.Column('builtin_tools', sa.JSON(), nullable=True),
        sa.Column('source', sa.String(length=16), nullable=False, server_default='seeded'),
        sa.Column('seed_hash', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('name'),
    )

    op.create_table('tool_members',
        sa.Column('unit_name', sa.String(length=64), nullable=False),
        sa.Column('member_name', sa.String(length=64), nullable=False),
        sa.Column('full_name', sa.String(length=130), nullable=False),
        sa.Column('permission', sa.String(length=16), nullable=False),
        sa.Column('definition', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['unit_name'], ['tool_units.name'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('unit_name', 'member_name'),
        sa.UniqueConstraint('full_name', name='uq_tool_members_full_name'),
    )

    op.create_table('agent_units',
        sa.Column('agent_name', sa.String(length=64), nullable=False),
        sa.Column('unit_name', sa.String(length=64), nullable=False),
        sa.Column('member_state', sa.String(length=16), nullable=False, server_default='enabled'),
        sa.Column('source', sa.String(length=16), nullable=False, server_default='seeded'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['agent_name'], ['agents.name'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['unit_name'], ['tool_units.name'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('agent_name', 'unit_name'),
    )

    # tool_credentials(B-4):external 工具 unit 级可逆加密凭证。FK→tool_units CASCADE
    # (删 unit 连带删密文)。故意无 ToolUnit→credentials relationship,密文不进快照/catalog。
    op.create_table('tool_credentials',
        sa.Column('unit_name', sa.String(length=64), nullable=False),
        sa.Column('placeholder_name', sa.String(length=128), nullable=False),
        sa.Column('encrypted_value', sa.Text(), nullable=False),
        sa.Column('source', sa.String(length=16), nullable=False, server_default='seeded'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['unit_name'], ['tool_units.name'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('unit_name', 'placeholder_name'),
    )

    # ------------------------------------------------------------------
    # Skill 系统(Phase C-1)。就地写进 squash 0001(全新建库假设,无 0002)。建表序:
    # skills(被 user_skills/department_skill_rules 引用,FK→users/departments 已建)→
    # user_skills / department_skill_rules / department_unit_rules(refs 已建表)。
    # 语义见 models.py 同名类。bundle = LargeBinary(MySQL LONGBLOB tier hint;PG/SQLite 忽略)。
    # ------------------------------------------------------------------
    op.create_table('skills',
        sa.Column('slug', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('visibility', sa.String(length=16), nullable=False, server_default='public'),
        sa.Column('default_enabled', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('owner_user_id', sa.String(length=64), nullable=True),
        sa.Column('allowed_tools', sa.JSON(), nullable=True),
        sa.Column('compatibility', sa.JSON(), nullable=True),
        sa.Column('metadata', sa.JSON(), nullable=True),
        sa.Column('skill_md', sa.Text(), nullable=False),
        sa.Column('bundle', sa.LargeBinary(length=100 * 1024 * 1024), nullable=True),
        sa.Column('source', sa.String(length=16), nullable=False),
        sa.Column('seed_hash', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['owner_user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('slug'),
    )
    op.create_index(op.f('ix_skills_owner_user_id'), 'skills', ['owner_user_id'], unique=False)

    op.create_table('user_skills',
        sa.Column('user_id', sa.String(length=64), nullable=False),
        sa.Column('skill_slug', sa.String(length=64), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['skill_slug'], ['skills.slug'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id', 'skill_slug'),
    )

    op.create_table('department_skill_rules',
        sa.Column('department_id', sa.String(length=64), nullable=False),
        sa.Column('skill_slug', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['department_id'], ['departments.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['skill_slug'], ['skills.slug'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('department_id', 'skill_slug'),
    )

    op.create_table('department_unit_rules',
        sa.Column('department_id', sa.String(length=64), nullable=False),
        sa.Column('unit_name', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['department_id'], ['departments.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['unit_name'], ['tool_units.name'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('department_id', 'unit_name'),
    )


def downgrade() -> None:
    """Drop all tables."""
    op.drop_table('department_unit_rules')
    op.drop_table('department_skill_rules')
    op.drop_table('user_skills')
    op.drop_index(op.f('ix_skills_owner_user_id'), table_name='skills')
    op.drop_table('skills')
    op.drop_table('tool_credentials')
    op.drop_table('agent_units')
    op.drop_table('tool_members')
    op.drop_table('agents')
    op.drop_table('tool_units')
    op.drop_index('ix_artifact_blobs_session_size', table_name='artifact_blobs')
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
