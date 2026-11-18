"""Foundation SQLAlchemy ORM models (Fase 0: Auth, RBAC, Workspace, Audit Log).

ARCHITECTURAL DIRECTION FOR FASE 1 (row_findings Partitioning Audit Note):
- When `datasets`, `dataset_versions`, `quality_runs`, `run_module_results`, and
  `row_findings` are added in Fase 1 (CLAUDE.md Section 2.5):
  1. `row_findings` will store per-row quality findings (NOT raw dataset text).
  2. For fast-dev (SQLite) and early MVP, `row_findings` may start as an indexed table
     with composite index `(run_id, severity, module)`.
  3. For PostgreSQL production scale (millions of rows), `row_findings` MUST use
     PostgreSQL declarative partitioning (`PARTITION BY LIST (run_id)` or
     `PARTITION BY HASH (run_id)`) via an Alembic migration so old run partitions
     can be dropped/archived cleanly without vacuum bloat.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, utc_now


class Organization(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Top-level tenant organization."""

    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    workspaces: Mapped[list["Workspace"]] = relationship(back_populates="organization")


class Workspace(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Team workspace isolating datasets, runs, and quality policies (Module G3)."""

    __tablename__ = "workspaces"
    __table_args__ = (UniqueConstraint("organization_id", "slug", name="uq_workspace_org_slug"),)

    organization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    organization: Mapped["Organization"] = relationship(back_populates="workspaces")
    memberships: Mapped[list["Membership"]] = relationship(back_populates="workspace")


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Platform user account (email + password for MVP; OIDC/SAML in Fase 2)."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_superadmin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    memberships: Mapped[list["Membership"]] = relationship(back_populates="user")


class Membership(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Workspace-scoped RBAC role assignment (Admin/Governance/Curator/Analyst/Viewer)."""

    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "workspace_id", name="uq_membership_user_ws"),)

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workspace_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="viewer")

    user: Mapped["User"] = relationship(back_populates="memberships")
    workspace: Mapped["Workspace"] = relationship(back_populates="memberships")


class AuditLog(UUIDPrimaryKeyMixin, Base):
    """Append-only audit log for sensitive actions (Module G4). Never update or delete rows."""

    __tablename__ = "audit_logs"

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    workspace_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(100), nullable=False)
    target_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class ApiToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Scoped API tokens stored as cryptographic hashes (never plaintext)."""

    __tablename__ = "api_tokens"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    scopes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


# --- Fase 1 MVP Core Models (CLAUDE.md Section 2.5) ---


class Dataset(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Logical dataset container within a workspace."""

    __tablename__ = "datasets"

    workspace_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    dataset_type: Mapped[str] = mapped_column(String(32), default="sft", nullable=False)
    created_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    versions: Mapped[list["DatasetVersion"]] = relationship(back_populates="dataset")


class DatasetVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Immutable snapshot of a dataset stored as Parquet in object storage."""

    __tablename__ = "dataset_versions"
    __table_args__ = (
        UniqueConstraint("dataset_id", "version_number", name="uq_dataset_version_num"),
    )

    dataset_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_version_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("dataset_versions.id", ondelete="SET NULL"), nullable=True
    )
    source_format: Mapped[str] = mapped_column(String(32), default="jsonl", nullable=False)
    storage_uri: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    raw_storage_uri: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    invalid_row_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    checksum: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="uploading", nullable=False)
    validation_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    dataset: Mapped["Dataset"] = relationship(back_populates="versions")


class ColumnMapping(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Maps source dataset columns to canonical NLP roles (prompt/response/messages/text)."""

    __tablename__ = "column_mappings"

    version_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("dataset_versions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    mapping_json: Mapped[dict[str, str]] = mapped_column(JSON, default=dict, nullable=False)


class QualityRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Execution of quality modules + DQI scoring on a specific dataset version."""

    __tablename__ = "quality_runs"

    version_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("dataset_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    seed: Mapped[int] = mapped_column(Integer, default=42, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False, index=True)
    current_stage: Mapped[str] = mapped_column(String(64), default="queued", nullable=False)
    progress_pct: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dqi_total: Mapped[float | None] = mapped_column(Float, nullable=True)
    dqi_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class RunModuleResult(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Per-module summary metrics for a quality run."""

    __tablename__ = "run_module_results"

    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("quality_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    module: Mapped[str] = mapped_column(String(64), nullable=False)
    dimension: Mapped[str] = mapped_column(String(64), nullable=False)
    module_version: Mapped[str] = mapped_column(String(32), nullable=False)
    metrics: Mapped[dict[str, float]] = mapped_column(JSON, default=dict, nullable=False)
    findings_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class RowFindingRecord(UUIDPrimaryKeyMixin, Base):
    """Per-row quality finding (never stores raw PII text).

    NOTE (Partitioning Direction): Indexed by `(run_id, severity, module)` for fast-dev;
    upgrade to PostgreSQL declarative partitioning by `run_id` for multi-million-row scale.
    """

    __tablename__ = "row_findings"
    __table_args__ = (Index("ix_row_findings_run_sev_mod", "run_id", "severity", "module"),)

    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("quality_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    row_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    module: Mapped[str] = mapped_column(String(64), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    span_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    span_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class CurationAction(UUIDPrimaryKeyMixin, Base):
    """Human-in-the-loop curation decision per row (Module F3)."""

    __tablename__ = "curation_actions"

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    version_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("dataset_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    resulting_version_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("dataset_versions.id", ondelete="SET NULL"), nullable=True
    )
    row_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    old_value: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    new_value: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class QualityPolicy(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Workspace quality gate thresholds (e.g., block export if critical PII remains)."""

    __tablename__ = "quality_policies"

    workspace_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    rules_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ExportRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Dataset export artifact + manifest metadata (Module F5)."""

    __tablename__ = "exports"

    version_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("dataset_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    export_format: Mapped[str] = mapped_column(String(32), nullable=False)
    filter_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    storage_uri: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    manifest_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="completed", nullable=False)
