"""Pydantic v2 schemas for VLM PDF verification, Semantic Chunking, and QA/SFT synthesis."""

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.datasets import DatasetResponse, DatasetVersionSummary


class ModuleCatalogItem(BaseModel):
    """Metadata describing a registered QualityModule."""

    name: str
    dimension: str
    version: str


class VlmVerifyRequest(BaseModel):
    """Trigger VLM / structural PDF extraction verification."""

    max_pages: int = Field(default=20, ge=1, le=200)
    min_accuracy_score: float = Field(default=0.75, ge=0.0, le=1.0)
    render_dpi: int = Field(default=150, ge=72, le=300)


class VlmPageResult(BaseModel):
    """Per-page VLM / structural verification finding summary."""

    page_number: int
    accuracy_score: float
    table_fidelity_score: float
    has_embedded_images: bool
    has_table_grid: bool
    verification_mode: str
    issues: list[str]
    extracted_preview: str


class VlmVerifyResponse(BaseModel):
    """Aggregate PDF extraction verification report."""

    version_id: str
    total_pages: int
    verified_pages: int
    flagged_pages: int
    avg_vlm_accuracy_score: float
    avg_table_fidelity_score: float
    vlm_verification_score: float
    verification_mode: str
    render_backend: str
    pages: list[VlmPageResult]


class SemanticChunkRequest(BaseModel):
    """Configure automatic semantic chunking + Vector DB metadata enrichment."""

    max_chunk_tokens: int = Field(default=512, ge=32, le=4096)
    min_chunk_tokens: int = Field(default=64, ge=8, le=2048)
    similarity_threshold: float = Field(default=0.55, ge=0.0, le=0.99)
    overlap_sentences: int = Field(default=1, ge=0, le=5)
    include_headings: bool = True
    mask_pii_in_metadata: bool = True


class SemanticChunkResponse(BaseModel):
    """Result of semantic chunking creating a new immutable DatasetVersion."""

    version: DatasetVersionSummary
    source_row_count: int
    chunk_count: int
    avg_tokens_per_chunk: float
    avg_coherence_score: float
    embedding_backend: str
    sample_chunks: list[dict[str, Any]]


class QaGenerateRequest(BaseModel):
    """Configure automatic QA/SFT instruction pair synthesis."""

    pairs_per_row: int = Field(default=2, ge=1, le=10)
    max_source_rows: int = Field(default=100, ge=1, le=2000)
    language: Literal["auto", "id", "en"] = "auto"
    mode: Literal["auto", "local_template", "llm"] = "auto"
    mask_pii: bool = True
    include_system_prompt: bool = True
    seed: int = Field(default=42, ge=0)
    output_target: Literal["new_version", "new_dataset"] = "new_version"
    target_dataset_name: str | None = Field(default=None, max_length=200)


class QaGenerateResponse(BaseModel):
    """Result of QA/SFT synthesis producing a new version or new SFT dataset."""

    dataset: DatasetResponse | None = None
    version: DatasetVersionSummary
    source_rows_processed: int
    generated_pairs_count: int
    generation_mode: str
    pii_masked: bool
    sample_pairs: list[dict[str, Any]]
