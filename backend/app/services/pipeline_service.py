"""Application services for VLM PDF verification, Semantic Chunking, and QA/SFT synthesis."""

import asyncio
import hashlib
import io
import json
from typing import Any

import polars as pl
from fastapi import status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.models import Dataset, DatasetVersion
from app.modules.cleaning.pii import apply_pii_transform
from app.modules.ingestion.semantic_chunker import semantic_chunk_rows
from app.modules.instruction.qa_synthesizer import synthesize_qa_pairs_from_rows
from app.modules.instruction.vlm_verifier import verify_pdf_extraction
from app.services.audit_service import record_audit_log
from app.services.dataset_service import (
    get_dataset_or_404,
    get_version_or_404,
    load_version_parquet_bytes,
    storage_key_from_uri,
)
from app.storage.s3 import get_storage_backend

_PARQUET_SCHEMA: Any = {
    "row_id": pl.Utf8,
    "line_number": pl.Int64,
    "prompt": pl.Utf8,
    "response": pl.Utf8,
    "chosen": pl.Utf8,
    "rejected": pl.Utf8,
    "text": pl.Utf8,
    "messages_json": pl.Utf8,
    "metadata_json": pl.Utf8,
    "is_valid": pl.Boolean,
    "validation_error": pl.Utf8,
    "curation_status": pl.Utf8,
}


async def _next_version_number(db: AsyncSession, dataset_id: str) -> int:
    max_num = (
        await db.execute(
            select(func.max(DatasetVersion.version_number)).where(
                DatasetVersion.dataset_id == dataset_id
            )
        )
    ).scalar_one_or_none()
    return (max_num or 0) + 1


async def run_vlm_pdf_verification(
    db: AsyncSession,
    *,
    version_id: str,
    max_pages: int,
    min_accuracy_score: float,
    render_dpi: int,
    user_id: str,
    ip_address: str | None = None,
) -> dict[str, Any]:
    """Run VLM / structural page verification on a PDF dataset version."""
    ver = await get_version_or_404(db, version_id)
    ds = await get_dataset_or_404(db, ver.dataset_id)

    if ver.source_format.lower() != "pdf":
        raise AppError(
            code="NOT_A_PDF_VERSION",
            message="Verifikasi VLM hanya berlaku untuk versi dataset berformat PDF.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    storage = get_storage_backend()
    raw_key = (
        storage_key_from_uri(ver.raw_storage_uri)
        or f"datasets/{ds.id}/v{ver.version_number}/raw.pdf"
    )
    if not storage.exists(raw_key):
        raise AppError(
            code="RAW_PDF_NOT_FOUND",
            message="Berkas PDF mentah tidak ditemukan di penyimpanan.",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    pdf_bytes = storage.get_bytes(raw_key)

    pq_bytes = load_version_parquet_bytes(ds.id, ver.version_number)
    df = pl.read_parquet(io.BytesIO(pq_bytes))

    page_parts: dict[int, list[str]] = {}
    for row in df.iter_rows(named=True):
        raw_page = row.get("line_number", 1)
        page_no = int(raw_page) if isinstance(raw_page, int) and raw_page >= 1 else 1
        page_parts.setdefault(page_no, []).append(str(row.get("text") or ""))
    page_texts = {p: "\n\n".join(parts) for p, parts in page_parts.items()}

    report = await asyncio.to_thread(
        verify_pdf_extraction,
        pdf_bytes,
        page_texts,
        max_pages=max_pages,
        min_accuracy_score=min_accuracy_score,
        render_dpi=render_dpi,
    )

    await record_audit_log(
        db,
        action="dataset.vlm_verify",
        target_type="dataset_version",
        target_id=ver.id,
        user_id=user_id,
        workspace_id=ds.workspace_id,
        ip_address=ip_address,
        metadata={
            "dataset_id": ds.id,
            "verified_pages": report["verified_pages"],
            "flagged_pages": report["flagged_pages"],
            "verification_mode": report["verification_mode"],
        },
    )
    await db.commit()

    return {"version_id": ver.id, **report}


async def apply_semantic_chunking(
    db: AsyncSession,
    *,
    version_id: str,
    max_chunk_tokens: int,
    min_chunk_tokens: int,
    similarity_threshold: float,
    overlap_sentences: int,
    include_headings: bool,
    mask_pii_in_metadata: bool,
    user_id: str,
    ip_address: str | None = None,
) -> dict[str, Any]:
    """Semantically split and metadata-enrich a dataset version into a new immutable version."""
    ver = await get_version_or_404(db, version_id)
    ds = await get_dataset_or_404(db, ver.dataset_id)

    pq_bytes = load_version_parquet_bytes(ds.id, ver.version_number)
    df = pl.read_parquet(io.BytesIO(pq_bytes))
    rows = [dict(r) for r in df.iter_rows(named=True)]

    chunk_rows, summary = await asyncio.to_thread(
        semantic_chunk_rows,
        rows,
        max_chunk_tokens=max_chunk_tokens,
        min_chunk_tokens=min_chunk_tokens,
        similarity_threshold=similarity_threshold,
        overlap_sentences=overlap_sentences,
        include_headings=include_headings,
        mask_pii_in_metadata=mask_pii_in_metadata,
    )
    if not chunk_rows:
        raise AppError(
            code="SEMANTIC_CHUNK_EMPTY",
            message="Tidak ada konten teks yang dapat diproses untuk semantic chunking.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    new_df = pl.DataFrame(chunk_rows, schema=_PARQUET_SCHEMA)
    buf = io.BytesIO()
    new_df.write_parquet(buf)
    pq_payload = buf.getvalue()

    next_num = await _next_version_number(db, ds.id)
    storage = get_storage_backend()
    pq_key = f"datasets/{ds.id}/v{next_num}/data.parquet"
    pq_uri = storage.put_bytes(pq_key, pq_payload)

    val_summary = {
        "total_rows": new_df.height,
        "valid_rows": new_df.height,
        "invalid_rows": 0,
        "detected_columns": list(new_df.columns),
        "errors": [],
        "chunking_summary": summary,
    }
    new_ver = DatasetVersion(
        dataset_id=ds.id,
        version_number=next_num,
        parent_version_id=ver.id,
        source_format=ver.source_format,
        storage_uri=pq_uri,
        raw_storage_uri=ver.raw_storage_uri,
        row_count=new_df.height,
        invalid_row_count=0,
        checksum=hashlib.sha256(pq_payload).hexdigest(),
        status="curated",
        validation_summary=val_summary,
    )
    db.add(new_ver)
    await db.flush()

    await record_audit_log(
        db,
        action="dataset.semantic_chunk",
        target_type="dataset_version",
        target_id=new_ver.id,
        user_id=user_id,
        workspace_id=ds.workspace_id,
        ip_address=ip_address,
        metadata={
            "parent_version_id": ver.id,
            "new_version_number": next_num,
            "chunk_count": summary["chunk_count"],
        },
    )
    await db.commit()
    await db.refresh(new_ver)

    samples: list[dict[str, Any]] = []
    for row in chunk_rows[:5]:
        meta_obj: dict[str, Any] = {}
        try:
            meta_obj = json.loads(str(row.get("metadata_json") or "{}"))
        except json.JSONDecodeError:
            meta_obj = {}
        samples.append(
            {
                "row_id": row["row_id"],
                "text_preview": apply_pii_transform(str(row["text"]), mode="mask")[:200],
                "metadata": meta_obj,
            }
        )

    return {
        "version": new_ver,
        **summary,
        "sample_chunks": samples,
    }


async def apply_qa_synthesis(
    db: AsyncSession,
    *,
    version_id: str,
    pairs_per_row: int,
    max_source_rows: int,
    language: str,
    mode: str,
    mask_pii: bool,
    include_system_prompt: bool,
    seed: int,
    output_target: str,
    target_dataset_name: str | None,
    user_id: str,
    ip_address: str | None = None,
) -> dict[str, Any]:
    """Synthesize grounded SFT QA pairs from a version into a new version or new SFT dataset."""
    ver = await get_version_or_404(db, version_id)
    ds = await get_dataset_or_404(db, ver.dataset_id)

    pq_bytes = load_version_parquet_bytes(ds.id, ver.version_number)
    df = pl.read_parquet(io.BytesIO(pq_bytes))
    rows = [dict(r) for r in df.iter_rows(named=True)]

    sft_rows, summary = await asyncio.to_thread(
        synthesize_qa_pairs_from_rows,
        rows,
        pairs_per_row=pairs_per_row,
        max_source_rows=max_source_rows,
        language=language,
        mode=mode,
        mask_pii=mask_pii,
        include_system_prompt=include_system_prompt,
        seed=seed,
    )
    if not sft_rows:
        raise AppError(
            code="QA_GENERATION_EMPTY",
            message="Tidak ada teks memadai untuk menghasilkan pasangan QA/SFT.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    new_df = pl.DataFrame(sft_rows, schema=_PARQUET_SCHEMA)
    buf = io.BytesIO()
    new_df.write_parquet(buf)
    pq_payload = buf.getvalue()

    target_ds: Dataset | None = None
    storage = get_storage_backend()
    val_summary = {
        "total_rows": new_df.height,
        "valid_rows": new_df.height,
        "invalid_rows": 0,
        "detected_columns": list(new_df.columns),
        "errors": [],
        "qa_synthesis_summary": summary,
    }

    if output_target == "new_dataset":
        clean_name = (target_dataset_name or "").strip() or f"{ds.name} (SFT QA)"
        target_ds = Dataset(
            workspace_id=ds.workspace_id,
            name=clean_name[:200],
            description=f"Sintesis QA/SFT dari {ds.name} v{ver.version_number}",
            dataset_type="sft",
            retention_days=ds.retention_days,
            created_by=user_id,
        )
        db.add(target_ds)
        await db.flush()

        pq_key = f"datasets/{target_ds.id}/v1/data.parquet"
        pq_uri = storage.put_bytes(pq_key, pq_payload)
        new_ver = DatasetVersion(
            dataset_id=target_ds.id,
            version_number=1,
            parent_version_id=None,
            source_format="jsonl",
            storage_uri=pq_uri,
            raw_storage_uri="",
            row_count=new_df.height,
            invalid_row_count=0,
            checksum=hashlib.sha256(pq_payload).hexdigest(),
            status="validated",
            validation_summary=val_summary,
        )
    else:
        next_num = await _next_version_number(db, ds.id)
        pq_key = f"datasets/{ds.id}/v{next_num}/data.parquet"
        pq_uri = storage.put_bytes(pq_key, pq_payload)
        new_ver = DatasetVersion(
            dataset_id=ds.id,
            version_number=next_num,
            parent_version_id=ver.id,
            source_format="jsonl",
            storage_uri=pq_uri,
            raw_storage_uri=ver.raw_storage_uri,
            row_count=new_df.height,
            invalid_row_count=0,
            checksum=hashlib.sha256(pq_payload).hexdigest(),
            status="curated",
            validation_summary=val_summary,
        )

    db.add(new_ver)
    await db.flush()

    await record_audit_log(
        db,
        action="dataset.generate_qa",
        target_type="dataset_version",
        target_id=new_ver.id,
        user_id=user_id,
        workspace_id=ds.workspace_id,
        ip_address=ip_address,
        metadata={
            "source_version_id": ver.id,
            "output_target": output_target,
            "generated_pairs_count": summary["generated_pairs_count"],
            "generation_mode": summary["generation_mode"],
        },
    )
    await db.commit()
    await db.refresh(new_ver)
    if target_ds is not None:
        await db.refresh(target_ds)

    samples = [
        {
            "row_id": r["row_id"],
            "prompt": apply_pii_transform(str(r["prompt"]), mode="mask")[:200],
            "response": apply_pii_transform(str(r["response"]), mode="mask")[:200],
        }
        for r in sft_rows[:5]
    ]
    return {
        "dataset": target_ds,
        "version": new_ver,
        **summary,
        "sample_pairs": samples,
    }
