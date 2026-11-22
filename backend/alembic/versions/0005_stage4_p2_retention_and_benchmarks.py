"""Add dataset retention_days column and benchmark_sets table for Tahap 4.

Revision ID: 0005_stage4_p2_retention_and_benchmarks
Revises: 0004_stage3_enterprise_tables
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_stage4_p2_retention_and_benchmarks"
down_revision: str | None = "0004_stage3_enterprise_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("datasets", sa.Column("retention_days", sa.Integer(), nullable=True))

    op.create_table(
        "benchmark_sets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("entry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("prompts_json", sa.JSON(), nullable=False),
        sa.Column("storage_uri", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_benchmark_sets_workspace_id",
        "benchmark_sets",
        ["workspace_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_benchmark_sets_workspace_id", table_name="benchmark_sets")
    op.drop_table("benchmark_sets")
    with op.batch_alter_table("datasets") as batch_op:
        batch_op.drop_column("retention_days")
