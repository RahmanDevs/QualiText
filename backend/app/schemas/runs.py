"""Pydantic v2 schemas for Quality Runs, Summaries, and Row Findings."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class RunCreateRequest(BaseModel):
    """Trigger a quality assessment run on a dataset version."""

    modules: list[str] = Field(
        default_factory=lambda: [
            "token_analytics",
            "pii",
            "artifacts",
            "dedup_exact_near",
        ]
    )
    seed: int = Field(default=42, ge=0)
    params: dict[str, dict[str, Any]] = Field(default_factory=dict)


class RunResponse(BaseModel):
    """Quality run status and top-level DQI."""

    id: str
    version_id: str
    status: str
    current_stage: str
    progress_pct: int
    seed: int
    dqi_total: float | None
    dqi_summary: dict[str, Any]
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime


class RunSummaryResponse(BaseModel):
    """Detailed DQI breakdown and per-module metrics."""

    run_id: str
    version_id: str
    status: str
    dqi_total: float | None
    dqi_summary: dict[str, Any]
    module_results: list[dict[str, Any]]


class RowFindingResponse(BaseModel):
    """Single row finding item."""

    id: str
    row_id: str
    module: str
    code: str
    severity: str
    score: float | None
    span_start: int | None
    span_end: int | None
    detail: dict[str, Any]
