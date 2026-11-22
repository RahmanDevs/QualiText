"""API endpoints for Module Catalog, VLM PDF Verification, Semantic Chunking, and QA/SFT."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.datasets import _ensure_dataset_access, _to_version_summary
from app.core.deps import get_current_user
from app.core.security import Permission
from app.db.models import User
from app.db.session import get_db
from app.schemas.datasets import DatasetResponse
from app.schemas.pipelines import (
    ModuleCatalogItem,
    QaGenerateRequest,
    QaGenerateResponse,
    SemanticChunkRequest,
    SemanticChunkResponse,
    VlmPageResult,
    VlmVerifyRequest,
    VlmVerifyResponse,
)
from app.services import dataset_service, pipeline_service, run_service
from app.services.audit_service import extract_client_ip

router = APIRouter(tags=["pipelines"])


@router.get("/modules", response_model=list[ModuleCatalogItem])
async def list_registered_modules(
    _user: User = Depends(get_current_user),
) -> list[ModuleCatalogItem]:
    """Return all registered QualityModules with their DQI dimension and version."""
    return [
        ModuleCatalogItem(name=mod.name, dimension=mod.dimension, version=mod.version)
        for mod in run_service.MODULE_REGISTRY.values()
    ]


@router.post("/versions/{version_id}/vlm-verify", response_model=VlmVerifyResponse)
async def verify_pdf_version(
    version_id: str,
    payload: VlmVerifyRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> VlmVerifyResponse:
    """Verify PDF extracted text and tables against rendered page visuals / structure."""
    ver = await dataset_service.get_version_or_404(db, version_id)
    await _ensure_dataset_access(db, user, ver.dataset_id, Permission.RUN_EXECUTE)
    res = await pipeline_service.run_vlm_pdf_verification(
        db,
        version_id=version_id,
        max_pages=payload.max_pages,
        min_accuracy_score=payload.min_accuracy_score,
        render_dpi=payload.render_dpi,
        user_id=user.id,
        ip_address=extract_client_ip(request),
    )
    return VlmVerifyResponse(
        version_id=str(res["version_id"]),
        total_pages=int(res["total_pages"]),
        verified_pages=int(res["verified_pages"]),
        flagged_pages=int(res["flagged_pages"]),
        avg_vlm_accuracy_score=float(res["avg_vlm_accuracy_score"]),
        avg_table_fidelity_score=float(res["avg_table_fidelity_score"]),
        vlm_verification_score=float(res["vlm_verification_score"]),
        verification_mode=str(res["verification_mode"]),
        render_backend=str(res["render_backend"]),
        pages=[VlmPageResult(**p) for p in res["pages"]],
    )


@router.post("/versions/{version_id}/semantic-chunk", response_model=SemanticChunkResponse)
async def semantic_chunk_version(
    version_id: str,
    payload: SemanticChunkRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SemanticChunkResponse:
    """Apply automatic semantic chunking + metadata enrichment into a new immutable version."""
    ver = await dataset_service.get_version_or_404(db, version_id)
    await _ensure_dataset_access(db, user, ver.dataset_id, Permission.DATASET_WRITE)
    res = await pipeline_service.apply_semantic_chunking(
        db,
        version_id=version_id,
        max_chunk_tokens=payload.max_chunk_tokens,
        min_chunk_tokens=payload.min_chunk_tokens,
        similarity_threshold=payload.similarity_threshold,
        overlap_sentences=payload.overlap_sentences,
        include_headings=payload.include_headings,
        mask_pii_in_metadata=payload.mask_pii_in_metadata,
        user_id=user.id,
        ip_address=extract_client_ip(request),
    )
    return SemanticChunkResponse(
        version=_to_version_summary(res["version"]),
        source_row_count=int(res["source_row_count"]),
        chunk_count=int(res["chunk_count"]),
        avg_tokens_per_chunk=float(res["avg_tokens_per_chunk"]),
        avg_coherence_score=float(res["avg_coherence_score"]),
        embedding_backend=str(res["embedding_backend"]),
        sample_chunks=list(res["sample_chunks"]),
    )


@router.post("/versions/{version_id}/generate-qa", response_model=QaGenerateResponse)
async def generate_qa_pairs(
    version_id: str,
    payload: QaGenerateRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> QaGenerateResponse:
    """Synthesize grounded QA/SFT pairs from raw or chunked rows into a new version or dataset."""
    ver = await dataset_service.get_version_or_404(db, version_id)
    await _ensure_dataset_access(db, user, ver.dataset_id, Permission.DATASET_WRITE)
    res = await pipeline_service.apply_qa_synthesis(
        db,
        version_id=version_id,
        pairs_per_row=payload.pairs_per_row,
        max_source_rows=payload.max_source_rows,
        language=payload.language,
        mode=payload.mode,
        mask_pii=payload.mask_pii,
        include_system_prompt=payload.include_system_prompt,
        seed=payload.seed,
        output_target=payload.output_target,
        target_dataset_name=payload.target_dataset_name,
        user_id=user.id,
        ip_address=extract_client_ip(request),
    )
    ver_summary = _to_version_summary(res["version"])
    ds_obj = res.get("dataset")
    ds_resp = (
        DatasetResponse(
            id=ds_obj.id,
            workspace_id=ds_obj.workspace_id,
            name=ds_obj.name,
            description=ds_obj.description,
            dataset_type=ds_obj.dataset_type,
            retention_days=ds_obj.retention_days,
            created_at=ds_obj.created_at,
            latest_version=ver_summary,
        )
        if ds_obj is not None
        else None
    )
    return QaGenerateResponse(
        dataset=ds_resp,
        version=ver_summary,
        source_rows_processed=int(res["source_rows_processed"]),
        generated_pairs_count=int(res["generated_pairs_count"]),
        generation_mode=str(res["generation_mode"]),
        pii_masked=bool(res["pii_masked"]),
        sample_pairs=list(res["sample_pairs"]),
    )
