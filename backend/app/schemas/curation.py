"""Pydantic v2 schemas for Explorer, Curation, Diff Preview, Policies, and Exports."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class RowItemResponse(BaseModel):
    """Single dataset row returned to Explorer (PII masked unless authorized)."""

    row_id: str
    line_number: int
    prompt: str
    response: str
    text: str
    is_valid: bool
    validation_error: str
    curation_status: str
    has_pii: bool = False


class PaginatedRowsResponse(BaseModel):
    """Server-side paginated rows for Data Explorer."""

    version_id: str
    total: int
    limit: int
    offset: int
    pii_revealed: bool
    items: list[RowItemResponse]


class CurationActionItem(BaseModel):
    """Single row curation action."""

    row_id: str
    action: Literal["approve", "reject", "flag", "edit", "mask_pii"]
    prompt: str | None = None
    response: str | None = None
    text: str | None = None


class CurationBatchRequest(BaseModel):
    """Batch curation request producing a new immutable DatasetVersion."""

    actions: list[CurationActionItem] = Field(min_length=1, max_length=5000)


class DiffPreviewRequest(BaseModel):
    """Preview PII masking and artifact cleaning before applying."""

    text: str
    pii_mode: Literal["detect_only", "mask", "hash"] = "mask"
    clean_artifacts: bool = True


class DiffPreviewResponse(BaseModel):
    """Before/after diff preview response."""

    before: str
    after: str
    changed: bool


class QualityPolicyPayload(BaseModel):
    """Workspace quality gate policy rules."""

    min_dqi: float = Field(default=60.0, ge=0.0, le=100.0)
    block_if_critical_pii: bool = True
    is_active: bool = True


class ExportCreateRequest(BaseModel):
    """Export dataset version request."""

    export_format: Literal["jsonl", "csv", "parquet"] = "jsonl"
    exclude_rejected: bool = True
    only_approved: bool = False
    enforce_quality_gate: bool = True


class ExportResponse(BaseModel):
    """Export artifact metadata and manifest."""

    id: str
    version_id: str
    export_format: str
    status: str
    manifest: dict[str, Any]
    created_at: datetime
