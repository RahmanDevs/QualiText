"""Explorer, PII masking/reveal, diff preview, and immutable curation service."""

import hashlib
import io
from typing import Any

import polars as pl
from fastapi import status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.models import CurationAction, DatasetVersion
from app.modules.cleaning.artifacts import clean_text_artifacts
from app.modules.cleaning.pii import apply_pii_transform, detect_pii_spans
from app.schemas.curation import CurationActionItem, PaginatedRowsResponse, RowItemResponse
from app.services.audit_service import record_audit_log
from app.services.dataset_service import (
    get_dataset_or_404,
    get_version_or_404,
    load_version_parquet_bytes,
)
from app.storage.s3 import get_storage_backend


async def list_version_rows(
    db: AsyncSession,
    *,
    version_id: str,
    user_id: str,
    can_view_pii: bool,
    reveal_pii: bool,
    curation_status: str | None = None,
    only_invalid: bool = False,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> PaginatedRowsResponse:
    """Read Parquet rows with server-side filtering, pagination, and PII masking."""
    ver = await get_version_or_404(db, version_id)
    ds = await get_dataset_or_404(db, ver.dataset_id)
    pq_bytes = load_version_parquet_bytes(ds.id, ver.version_number)
    df = pl.read_parquet(io.BytesIO(pq_bytes))

    if curation_status:
        df = df.filter(pl.col("curation_status") == curation_status)
    if only_invalid:
        df = df.filter(~pl.col("is_valid"))
    if search:
        needle = search.lower()
        df = df.filter(
            pl.col("text").str.to_lowercase().str.contains(needle, literal=True)
            | pl.col("row_id").str.to_lowercase().str.contains(needle, literal=True)
        )

    total = df.height
    page_df = df.slice(offset, limit)

    should_reveal = reveal_pii and can_view_pii
    if reveal_pii and not can_view_pii:
        raise AppError(
            code="FORBIDDEN_PII_VIEW",
            message="Anda tidak memiliki izin `pii:view` untuk membuka teks PII asli.",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    if should_reveal:
        await record_audit_log(
            db,
            action="pii.reveal",
            target_type="dataset_version",
            target_id=ver.id,
            user_id=user_id,
            workspace_id=ds.workspace_id,
            metadata={"offset": offset, "limit": limit},
        )
        await db.commit()

    items: list[RowItemResponse] = []
    for row in page_df.iter_rows(named=True):
        prompt = str(row.get("prompt") or "")
        response = str(row.get("response") or "")
        text = str(row.get("text") or "")
        has_pii = bool(detect_pii_spans(text))

        if has_pii and not should_reveal:
            prompt = apply_pii_transform(prompt, mode="mask")
            response = apply_pii_transform(response, mode="mask")
            text = apply_pii_transform(text, mode="mask")

        items.append(
            RowItemResponse(
                row_id=str(row["row_id"]),
                line_number=int(row["line_number"]),
                prompt=prompt,
                response=response,
                text=text,
                is_valid=bool(row["is_valid"]),
                validation_error=str(row.get("validation_error") or ""),
                curation_status=str(row.get("curation_status") or "pending"),
                has_pii=has_pii,
            )
        )

    return PaginatedRowsResponse(
        version_id=ver.id,
        total=total,
        limit=limit,
        offset=offset,
        pii_revealed=should_reveal,
        items=items,
    )


def preview_text_diff(text: str, *, pii_mode: str, clean_artifacts: bool) -> tuple[str, str]:
    """Compute before/after preview for PII transformation and artifact cleaning."""
    after = apply_pii_transform(text, mode=pii_mode)
    if clean_artifacts:
        after = clean_text_artifacts(after)
    return text, after


async def apply_curation_batch(
    db: AsyncSession,
    *,
    version_id: str,
    actions: list[CurationActionItem],
    user_id: str,
) -> DatasetVersion:
    """Apply curation actions and create a new immutable DatasetVersion."""
    ver = await get_version_or_404(db, version_id)
    ds = await get_dataset_or_404(db, ver.dataset_id)
    pq_bytes = load_version_parquet_bytes(ds.id, ver.version_number)
    df = pl.read_parquet(io.BytesIO(pq_bytes))

    action_map = {item.row_id: item for item in actions}
    updated_rows: list[dict[str, Any]] = []

    for row in df.iter_rows(named=True):
        rid = str(row["row_id"])
        act = action_map.get(rid)
        if act is None:
            updated_rows.append(dict(row))
            continue

        old_snapshot = {
            "prompt": row["prompt"],
            "response": row["response"],
            "curation_status": row["curation_status"],
        }
        new_row = dict(row)

        if act.action == "approve":
            new_row["curation_status"] = "approved"
        elif act.action == "reject":
            new_row["curation_status"] = "rejected"
        elif act.action == "flag":
            new_row["curation_status"] = "flagged"
        elif act.action == "mask_pii":
            new_row["prompt"] = apply_pii_transform(str(row["prompt"] or ""), mode="mask")
            new_row["response"] = apply_pii_transform(str(row["response"] or ""), mode="mask")
            new_row["text"] = apply_pii_transform(str(row["text"] or ""), mode="mask")
            new_row["curation_status"] = "approved"
        elif act.action == "edit":
            if act.prompt is not None:
                new_row["prompt"] = act.prompt
            if act.response is not None:
                new_row["response"] = act.response
            if act.text is not None:
                new_row["text"] = act.text
            else:
                new_row["text"] = f"{new_row['prompt']}\n{new_row['response']}".strip()
            new_row["curation_status"] = "approved"

        updated_rows.append(new_row)
        db.add(
            CurationAction(
                version_id=ver.id,
                row_id=rid,
                action=act.action,
                old_value=old_snapshot,
                new_value={"curation_status": new_row["curation_status"]},
                user_id=user_id,
            )
        )

    max_num = (
        await db.execute(
            select(func.max(DatasetVersion.version_number)).where(
                DatasetVersion.dataset_id == ds.id
            )
        )
    ).scalar_one_or_none()
    next_num = (max_num or ver.version_number) + 1

    new_df = pl.DataFrame(updated_rows, schema=df.schema)
    buf = io.BytesIO()
    new_df.write_parquet(buf)
    pq_payload = buf.getvalue()

    storage = get_storage_backend()
    pq_key = f"datasets/{ds.id}/v{next_num}/data.parquet"
    pq_uri = storage.put_bytes(pq_key, pq_payload)

    new_ver = DatasetVersion(
        dataset_id=ds.id,
        version_number=next_num,
        parent_version_id=ver.id,
        source_format=ver.source_format,
        storage_uri=pq_uri,
        raw_storage_uri=ver.raw_storage_uri,
        row_count=new_df.height,
        invalid_row_count=ver.invalid_row_count,
        checksum=hashlib.sha256(pq_payload).hexdigest(),
        status="curated",
        validation_summary=ver.validation_summary,
    )
    db.add(new_ver)
    await db.flush()

    await record_audit_log(
        db,
        action="dataset.curate",
        target_type="dataset_version",
        target_id=new_ver.id,
        user_id=user_id,
        workspace_id=ds.workspace_id,
        metadata={
            "parent_version_id": ver.id,
            "new_version_number": next_num,
            "actions_count": len(actions),
        },
    )
    await db.commit()
    await db.refresh(new_ver)
    return new_ver
