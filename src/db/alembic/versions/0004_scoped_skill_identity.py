"""scope skill slugs and move references to stable ids

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-28

Private and shared skills used to share one global slug primary key. That made an
invisible private upload reserve the name for every other user. Rebuild the three
skill tables so identity is a stable UUID, while ``(namespace_key, slug)`` makes
shared slugs globally unique and private slugs unique only for their owner.
"""

from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa


revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_BLOB_TYPE_TIER_HINT = 100 * 1024 * 1024


def _skill_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("namespace_key", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("visibility", sa.String(length=16), nullable=False, server_default="public"),
        sa.Column(
            "default_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("owner_user_id", sa.String(length=64), nullable=True),
        sa.Column("allowed_tools", sa.JSON(), nullable=True),
        sa.Column("compatibility", sa.JSON(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("skill_md", sa.Text(), nullable=False),
        sa.Column(
            "bundle", sa.LargeBinary(length=_BLOB_TYPE_TIER_HINT), nullable=False
        ),
        sa.Column(
            "has_extra_files", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("seed_hash", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    bind = op.get_bind()
    old_meta = sa.MetaData()
    old_skills = sa.Table("skills", old_meta, autoload_with=bind)
    old_user_skills = sa.Table("user_skills", old_meta, autoload_with=bind)
    old_dept_rules = sa.Table("department_skill_rules", old_meta, autoload_with=bind)

    skill_rows = list(bind.execute(sa.select(old_skills)).mappings())
    skill_ids = {row["slug"]: str(uuid.uuid4()) for row in skill_rows}
    user_rows = list(bind.execute(sa.select(old_user_skills)).mappings())
    dept_rows = list(bind.execute(sa.select(old_dept_rules)).mappings())

    skills_v2 = op.create_table(
        "skills_v2",
        *_skill_columns(),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("namespace_key", "slug", name="uq_skills_namespace_slug"),
        sa.CheckConstraint(
            "(owner_user_id IS NULL AND namespace_key = '') OR "
            "(owner_user_id IS NOT NULL AND namespace_key = owner_user_id)",
            name="ck_skills_namespace_owner",
        ),
    )
    user_skills_v2 = op.create_table(
        "user_skills_v2",
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("skill_id", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["skill_id"], ["skills_v2.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "skill_id"),
    )
    dept_rules_v2 = op.create_table(
        "department_skill_rules_v2",
        sa.Column("department_id", sa.String(length=64), nullable=False),
        sa.Column("skill_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["department_id"], ["departments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["skill_id"], ["skills_v2.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("department_id", "skill_id"),
    )

    if skill_rows:
        op.bulk_insert(
            skills_v2,
            [
                {
                    **dict(row),
                    "id": skill_ids[row["slug"]],
                    "namespace_key": row["owner_user_id"] or "",
                }
                for row in skill_rows
            ],
        )
    if user_rows:
        op.bulk_insert(
            user_skills_v2,
            [
                {
                    "user_id": row["user_id"],
                    "skill_id": skill_ids[row["skill_slug"]],
                    "enabled": row["enabled"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
                for row in user_rows
            ],
        )
    if dept_rows:
        op.bulk_insert(
            dept_rules_v2,
            [
                {
                    "department_id": row["department_id"],
                    "skill_id": skill_ids[row["skill_slug"]],
                    "created_at": row["created_at"],
                }
                for row in dept_rows
            ],
        )

    op.drop_table("department_skill_rules")
    op.drop_table("user_skills")
    op.drop_index(op.f("ix_skills_owner_user_id"), table_name="skills")
    op.drop_table("skills")
    op.rename_table("skills_v2", "skills")
    op.rename_table("user_skills_v2", "user_skills")
    op.rename_table("department_skill_rules_v2", "department_skill_rules")
    op.create_index(op.f("ix_skills_owner_user_id"), "skills", ["owner_user_id"])


def downgrade() -> None:
    bind = op.get_bind()
    meta = sa.MetaData()
    skills = sa.Table("skills", meta, autoload_with=bind)
    duplicate = bind.execute(
        sa.select(skills.c.slug)
        .group_by(skills.c.slug)
        .having(sa.func.count(skills.c.id) > 1)
        .limit(1)
    ).scalar_one_or_none()
    if duplicate is not None:
        raise RuntimeError(
            "cannot downgrade scoped skill identity while duplicate slug "
            f"{duplicate!r} exists across namespaces"
        )

    user_skills = sa.Table("user_skills", meta, autoload_with=bind)
    dept_rules = sa.Table("department_skill_rules", meta, autoload_with=bind)
    skill_rows = list(bind.execute(sa.select(skills)).mappings())
    slug_by_id = {row["id"]: row["slug"] for row in skill_rows}
    user_rows = list(bind.execute(sa.select(user_skills)).mappings())
    dept_rows = list(bind.execute(sa.select(dept_rules)).mappings())

    old_skills = op.create_table(
        "skills_v1",
        *[column for column in _skill_columns() if column.name not in {"id", "namespace_key"}],
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("slug"),
    )
    old_user_skills = op.create_table(
        "user_skills_v1",
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("skill_slug", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["skill_slug"], ["skills_v1.slug"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "skill_slug"),
    )
    old_dept_rules = op.create_table(
        "department_skill_rules_v1",
        sa.Column("department_id", sa.String(length=64), nullable=False),
        sa.Column("skill_slug", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["department_id"], ["departments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["skill_slug"], ["skills_v1.slug"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("department_id", "skill_slug"),
    )

    if skill_rows:
        op.bulk_insert(
            old_skills,
            [
                {k: v for k, v in dict(row).items() if k not in {"id", "namespace_key"}}
                for row in skill_rows
            ],
        )
    if user_rows:
        op.bulk_insert(
            old_user_skills,
            [
                {
                    "user_id": row["user_id"],
                    "skill_slug": slug_by_id[row["skill_id"]],
                    "enabled": row["enabled"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
                for row in user_rows
            ],
        )
    if dept_rows:
        op.bulk_insert(
            old_dept_rules,
            [
                {
                    "department_id": row["department_id"],
                    "skill_slug": slug_by_id[row["skill_id"]],
                    "created_at": row["created_at"],
                }
                for row in dept_rows
            ],
        )

    op.drop_table("department_skill_rules")
    op.drop_table("user_skills")
    op.drop_index(op.f("ix_skills_owner_user_id"), table_name="skills")
    op.drop_table("skills")
    op.rename_table("skills_v1", "skills")
    op.rename_table("user_skills_v1", "user_skills")
    op.rename_table("department_skill_rules_v1", "department_skill_rules")
    op.create_index(op.f("ix_skills_owner_user_id"), "skills", ["owner_user_id"])
