"""Add PostgreSQL HASH partitioning for `row_findings` (and composite index on SQLite).

Revision ID: 0003_fase2_row_findings_partitioning
Revises: 0002_fase1_mvp_tables
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_fase2_row_findings_partitioning"
down_revision: str | None = "0002_fase1_mvp_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PG_HASH_PARTITIONS = 16


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE row_findings RENAME TO row_findings_unpartitioned")
        op.execute(
            "ALTER TABLE row_findings_unpartitioned "
            "RENAME CONSTRAINT row_findings_pkey TO row_findings_unpartitioned_pkey"
        )
        op.execute(
            """
            CREATE TABLE row_findings (
                id VARCHAR(36) NOT NULL,
                run_id VARCHAR(36) NOT NULL REFERENCES quality_runs(id) ON DELETE CASCADE,
                row_id VARCHAR(64) NOT NULL,
                module VARCHAR(64) NOT NULL,
                code VARCHAR(64) NOT NULL,
                severity VARCHAR(16) NOT NULL,
                score DOUBLE PRECISION NULL,
                span_start INTEGER NULL,
                span_end INTEGER NULL,
                detail JSONB NOT NULL,
                PRIMARY KEY (id, run_id)
            ) PARTITION BY HASH (run_id)
            """
        )
        for idx in range(_PG_HASH_PARTITIONS):
            op.execute(
                f"CREATE TABLE row_findings_p{idx} PARTITION OF row_findings "
                f"FOR VALUES WITH (MODULUS {_PG_HASH_PARTITIONS}, REMAINDER {idx})"
            )
        op.execute(
            """
            INSERT INTO row_findings
                (id, run_id, row_id, module, code, severity, score, span_start, span_end, detail)
            SELECT
                id, run_id, row_id, module, code, severity, score, span_start, span_end,
                detail::jsonb
            FROM row_findings_unpartitioned
            """
        )
        op.execute("DROP TABLE row_findings_unpartitioned")
        op.create_index("ix_row_findings_run_id", "row_findings", ["run_id"])
        op.create_index("ix_row_findings_row_id", "row_findings", ["row_id"])
        op.create_index(
            "ix_row_findings_run_sev_mod", "row_findings", ["run_id", "severity", "module"]
        )
        op.create_index("ix_row_findings_run_row", "row_findings", ["run_id", "row_id"])
    else:
        op.create_index("ix_row_findings_run_row", "row_findings", ["run_id", "row_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE row_findings RENAME TO row_findings_partitioned")
        op.execute(
            "ALTER TABLE row_findings_partitioned "
            "RENAME CONSTRAINT row_findings_pkey TO row_findings_partitioned_pkey"
        )
        op.create_table(
            "row_findings",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("run_id", sa.String(length=36), nullable=False),
            sa.Column("row_id", sa.String(length=64), nullable=False),
            sa.Column("module", sa.String(length=64), nullable=False),
            sa.Column("code", sa.String(length=64), nullable=False),
            sa.Column("severity", sa.String(length=16), nullable=False),
            sa.Column("score", sa.Float(), nullable=True),
            sa.Column("span_start", sa.Integer(), nullable=True),
            sa.Column("span_end", sa.Integer(), nullable=True),
            sa.Column("detail", sa.JSON(), nullable=False),
            sa.ForeignKeyConstraint(["run_id"], ["quality_runs.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.execute(
            """
            INSERT INTO row_findings
                (id, run_id, row_id, module, code, severity, score, span_start, span_end, detail)
            SELECT
                id, run_id, row_id, module, code, severity, score, span_start, span_end,
                detail::json
            FROM row_findings_partitioned
            """
        )
        op.execute("DROP TABLE row_findings_partitioned")
        op.create_index("ix_row_findings_run_id", "row_findings", ["run_id"])
        op.create_index("ix_row_findings_row_id", "row_findings", ["row_id"])
        op.create_index(
            "ix_row_findings_run_sev_mod", "row_findings", ["run_id", "severity", "module"]
        )
    else:
        op.drop_index("ix_row_findings_run_row", table_name="row_findings")
