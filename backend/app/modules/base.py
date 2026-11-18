"""QualityModule plugin contract (CLAUDE.md Section 2.4).

ARCHITECTURAL DIRECTION (Fase 0 -> Fase 1 Audit Notes):
1. All quality modules (Modules A-E) MUST implement `QualityModule` so they can be
   enabled, configured, tested, and versioned uniformly.
2. Deterministic execution: identical `(data, config, seed, model_version)` MUST produce
   identical `ModuleResult` and findings.
3. Batched / Streaming execution direction:
   - `data` is passed as `pl.LazyFrame` to avoid loading entire multi-million-row datasets
     into memory at once.
   - Heavy modules (e.g., Presidio PII scan, sentence-transformer embeddings, LLM-as-a-judge)
     SHOULD process `data` in chunks internally (e.g., via `data.collect_batches(chunk_size=...)`
     or streaming sinks) in Fase 1/2, while still returning `(ModuleResult, pl.DataFrame)` or
     streaming findings into storage/DB as an extension in Fase 1 if memory bounds require it.
4. Privacy rule: NEVER write raw PII values into `RowFinding.detail`. Store only entity type,
   character span, and confidence score.
"""

from typing import Any, Literal, Protocol, runtime_checkable

import polars as pl
from pydantic import BaseModel, Field


class ModuleConfig(BaseModel):
    """Base configuration for a quality module, validated and versioned via Pydantic."""

    enabled: bool = True
    version: str = "0.1.0"
    params: dict[str, Any] = Field(default_factory=dict)


class RowFinding(BaseModel):
    """Single row-level finding emitted by a quality module (never contains raw PII)."""

    row_id: str
    module: str
    code: str
    severity: Literal["info", "warning", "critical"]
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    span: tuple[int, int] | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class ModuleResult(BaseModel):
    """Aggregated summary returned by a quality module for DQI scoring."""

    module: str
    dimension: str
    metrics: dict[str, float]
    findings_count: int = Field(ge=0)


@runtime_checkable
class QualityModule(Protocol):
    """Uniform interface implemented by every quality module in `app/modules/`."""

    name: str
    dimension: str
    version: str

    def run(
        self,
        data: pl.LazyFrame,
        config: ModuleConfig,
        seed: int,
    ) -> tuple[ModuleResult, pl.DataFrame]:
        """Execute quality checks on `data` and return `(summary, row_findings_df)`."""
        ...
