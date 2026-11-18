"""Pydantic v2 schemas for Datasets, Versions, Uploads, and Column Mapping."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class DatasetCreateRequest(BaseModel):
    """Create dataset request payload."""

    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    dataset_type: Literal["sft", "preference", "rag", "raw"] = "sft"


class DatasetVersionSummary(BaseModel):
    """Summary of a single immutable dataset version."""

    id: str
    dataset_id: str
    version_number: int
    parent_version_id: str | None
    source_format: str
    row_count: int
    invalid_row_count: int
    checksum: str
    status: str
    validation_summary: dict[str, Any]
    created_at: datetime


class DatasetResponse(BaseModel):
    """Dataset metadata with latest version summary."""

    id: str
    workspace_id: str
    name: str
    description: str
    dataset_type: str
    created_at: datetime
    latest_version: DatasetVersionSummary | None = None


class DatasetDetailResponse(DatasetResponse):
    """Dataset detail including all immutable versions."""

    versions: list[DatasetVersionSummary] = Field(default_factory=list)


class UploadInitRequest(BaseModel):
    """Initialize a chunked upload for a new dataset version."""

    filename: str = Field(min_length=1, max_length=255)
    source_format: Literal["jsonl", "csv", "parquet", "txt"] = "jsonl"
    total_chunks: int = Field(default=1, ge=1, le=10000)


class UploadInitResponse(BaseModel):
    """Response with newly created version ID for uploading chunks."""

    version_id: str
    version_number: int
    status: str


class ColumnMappingPayload(BaseModel):
    """Maps source column names to canonical roles (`prompt`, `response`, `messages`, `text`)."""

    mapping: dict[str, str] = Field(default_factory=dict)


class ColumnMappingResponse(BaseModel):
    """Current column mapping + detected source columns."""

    version_id: str
    mapping: dict[str, str]
    detected_columns: list[str]
