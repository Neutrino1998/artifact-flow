"""artifact binary storage (A 阶段 artifact 地基)

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-08

新增 artifact_blobs 表:与文本/inventory 热路径隔离的二进制存储,1:1 绑 Artifact
(复合外键),承载用户上传的富格式原始 blob(docx/pdf)+ 图片字节(png/jpeg)。
源不可变、A 阶段不随版本走(一个 artifact 一条 blob)。

类型(不依赖方言):泛型 LargeBinary(length=100MB)。MySQL 按 BLOB(M) 选能容下
M 字节的最小 tier ⇒ 100MB>16MB 落 LONGBLOB(4GB);PG → BYTEA、SQLite → BLOB
均忽略长度。一条泛型声明三库皆对,零 dialect import(与 models.py 同源)。

注意:此迁移只在 PG/MySQL(生产/内网,走 alembic upgrade head)上建表;
SQLite dev 走 create_all 不跑迁移 —— 新建库自动带表,已存在的 dev 库需
`alembic upgrade head` 或重建。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0003'
down_revision: Union[str, Sequence[str], None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'artifact_blobs',
        sa.Column('artifact_id', sa.String(length=64), nullable=False),
        sa.Column('session_id', sa.String(length=64), nullable=False),
        # length hint 仅为在 MySQL 上把列推到 LONGBLOB tier;PG/SQLite 忽略长度。
        sa.Column(
            'data',
            sa.LargeBinary(length=100 * 1024 * 1024),
            nullable=False,
        ),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.PrimaryKeyConstraint('artifact_id', 'session_id'),
        # 复合外键 → artifacts(id, session_id);prod DB 级级联兜底
        # (SQLite dev 不开 FK pragma,清理靠 ORM cascade)。
        sa.ForeignKeyConstraint(
            ['artifact_id', 'session_id'],
            ['artifacts.id', 'artifacts.session_id'],
            ondelete='CASCADE',
        ),
    )


def downgrade() -> None:
    op.drop_table('artifact_blobs')
