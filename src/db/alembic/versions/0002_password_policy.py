"""password policy columns (门类三 账户与认证)

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-25

新增 users 三列支撑等保密码策略:
- must_change_password : 首次强制改密 / 周期到期强制改密 / 缺省密码根治的统一闸门标志
- password_changed_at  : 口令最后修改时间(naive UTC),登录时算龄判到期
- password_history     : 最近若干旧口令 hash(JSON 数组),改密查重「不重用」

存量老用户 backfill(2026-05-25 定标决策③「下次登录即强制改密」):
- must_change_password = True  → 所有现有用户下次登录被引导改密
- password_changed_at  = now() → 给个确定的基准时间(虽然 must_change 已强制)

注意:此迁移只在 PG/MySQL(生产/内网,走 alembic upgrade head)上执行 backfill;
SQLite dev 走 create_all 不跑迁移 —— 新建库自动带列,已存在的 dev 库需手动
`alembic stamp 0001 && alembic upgrade head` 或重建。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0002'
down_revision: Union[str, Sequence[str], None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. 加列。must_change_password NOT NULL + server_default=false,使存量行
    #    建列即有值(随后 backfill 改 True);password_changed_at / history 可空。
    op.add_column(
        'users',
        sa.Column(
            'must_change_password', sa.Boolean(),
            nullable=False, server_default=sa.text('false'),
        ),
    )
    op.add_column('users', sa.Column('password_changed_at', sa.DateTime(), nullable=True))
    op.add_column('users', sa.Column('password_history', sa.JSON(), nullable=True))

    # 2. backfill 存量用户:全部置 must_change_password=True + 基准改密时间。
    #    决策③:策略上线时老用户下次登录即强制改密。
    users = sa.table(
        'users',
        sa.column('must_change_password', sa.Boolean()),
        sa.column('password_changed_at', sa.DateTime()),
    )
    op.execute(
        users.update().values(
            must_change_password=True,
            password_changed_at=sa.func.now(),
        )
    )


def downgrade() -> None:
    op.drop_column('users', 'password_history')
    op.drop_column('users', 'password_changed_at')
    op.drop_column('users', 'must_change_password')
