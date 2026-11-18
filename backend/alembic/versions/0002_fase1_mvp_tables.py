"""Add Fase 1 MVP tables (datasets, versions, mappings, runs, findings, curation, exports).

Revision ID: 0002_fase1_mvp_tables
Revises: 0001_initial_foundation
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_fase1_mvp_tables"
down_revision: str | None = "0001_initial_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "datasets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("dataset_type", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_datasets_workspace_id", "datasets", ["workspace_id"])

    op.create_table(
        "dataset_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("dataset_id", sa.String(length=36), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("parent_version_id", sa.String(length=36), nullable=True),
        sa.Column("source_format", sa.String(length=32), nullable=False),
        sa.Column("storage_uri", sa.String(length=512), nullable=False),
        sa.Column("raw_storage_uri", sa.String(length=512), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("invalid_row_count", sa.Integer(), nullable=False),
        sa.Column("checksum", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("validation_summary", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["parent_version_id"], ["dataset_versions.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dataset_id", "version_number", name="uq_dataset_version_num"),
    )
    op.create_index("ix_dataset_versions_dataset_id", "dataset_versions", ["dataset_id"])

    op.create_table(
        "column_mappings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("version_id", sa.String(length=36), nullable=False),
        sa.Column("mapping_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["version_id"], ["dataset_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_column_mappings_version_id", "column_mappings", ["version_id"], unique=True)

    op.create_table(
        "quality_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("version_id", sa.String(length=36), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("config_snapshot", sa.JSON(), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("current_stage", sa.String(length=64), nullable=False),
        sa.Column("progress_pct", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dqi_total", sa.Float(), nullable=True),
        sa.Column("dqi_summary", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["version_id"], ["dataset_versions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_quality_runs_version_id", "quality_runs", ["version_id"])
    op.create_index("ix_quality_runs_status", "quality_runs", ["status"])

    op.create_table(
        "run_module_results",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("module", sa.String(length=64), nullable=False),
        sa.Column("dimension", sa.String(length=64), nullable=False),
        sa.Column("module_version", sa.String(length=32), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("findings_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"],
            [
                "quality_runs.id",
            ],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_run_module_results_run_id", "run_module_results", ["run_id"])

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
    op.create_index("ix_row_findings_run_id", "row_findings", ["run_id"])
    op.create_index("ix_row_findings_row_id", "row_findings", ["row_id"])
    op.create_index("ix_row_findings_run_sev_mod", "row_findings", ["run_id", "severity", "module"])

    op.create_table(
        "curation_actions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version_id", sa.String(length=36), nullable=False),
        sa.Column("resulting_version_id", sa.String(length=36), nullable=True),
        sa.Column("row_id", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("old_value", sa.JSON(), nullable=True),
        sa.Column("new_value", sa.JSON(), nullable=True),
        sa.Column("user_id", sa.String(length=36), nullable=True),
        sa.ForeignKeyConstraint(["version_id"], ["dataset_versions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["resulting_version_id"], ["dataset_versions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_curation_actions_created_at", "curation_actions", ["created_at"])
    op.create_index("ix_curation_actions_version_id", "curation_actions", ["version_id"])
    op.create_index("ix_curation_actions_row_id", "curation_actions", ["row_id"])

    op.create_table(
        "quality_policies",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("rules_json", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_quality_policies_workspace_id", "quality_policies", ["workspace_id"], unique=True
    )

    op.create_table(
        "exports",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("version_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=True),
        sa.Column("export_format", sa.String(length=32), nullable=False),
        sa.Column("filter_json", sa.JSON(), nullable=False),
        sa.Column("storage_uri", sa.String(length=512), nullable=False),
        sa.Column("manifest_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["version_id"], ["dataset_versions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_exports_version_id", "exports", ["version_id"])


def downgrade() -> None:
    op.drop_table("exports")
    op.drop_table("quality_policies")
    op.drop_table("curation_actions")
    op.drop_table("row_findings")
    op.drop_table("run_module_results")
    op.drop_table("quality_runs")
    op.drop_table("column_mappings")
    op.drop_table("dataset_versions")
    op.drop_table("datasets")
