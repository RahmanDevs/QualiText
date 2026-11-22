"""Explorer, PII masking/reveal, diff preview, and immutable curation service."""

import hashlib
import io
import json
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


def _sanitize_meta_value(val: Any) -> Any:
    if isinstance(val, str):
        return apply_pii_transform(val, mode="mask")
    if isinstance(val, list):
        return [_sanitize_meta_value(item) for item in val]
    if isinstance(val, dict):
        return {str(k): _sanitize_meta_value(v) for k, v in val.items()}
    return val


def _parse_row_metadata(raw_meta: Any, *, mask_pii: bool) -> dict[str, Any]:
    if not isinstance(raw_meta, str) or not raw_meta.strip():
        return {}
    try:
        parsed = json.loads(raw_meta)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return _sanitize_meta_value(parsed) if mask_pii else parsed


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
    ip_address: str | None = None,
) -> PaginatedRowsResponse:
    """Read Parquet rows with server-side filtering, pagination, and PII masking."""
    should_reveal = reveal_pii and can_view_pii
    if reveal_pii and not can_view_pii:
        raise AppError(
            code="FORBIDDEN_PII_VIEW",
            message="Anda tidak memiliki izin `pii:view` untuk membuka teks PII asli.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    ver = await get_version_or_404(db, version_id)
    ds = await get_dataset_or_404(db, ver.dataset_id)
    pq_bytes = load_version_parquet_bytes(ds.id, ver.version_number)
    df = pl.read_parquet(io.BytesIO(pq_bytes))

    if curation_status:
        df = df.filter(pl.col("curation_status") == curation_status)
    if only_invalid:
        df = df.filter(~pl.col("is_valid"))

    if should_reveal:
        if search:
            needle = search.lower()
            df = df.filter(
                pl.col("text").str.to_lowercase().str.contains(needle, literal=True)
                | pl.col("row_id").str.to_lowercase().str.contains(needle, literal=True)
            )
        await record_audit_log(
            db,
            action="pii.reveal",
            target_type="dataset_version",
            target_id=ver.id,
            user_id=user_id,
            workspace_id=ds.workspace_id,
            ip_address=ip_address,
            metadata={"offset": offset, "limit": limit},
        )
        await db.commit()

    items: list[RowItemResponse] = []
    if search and not should_reveal:
        needle = search.lower()
        matched: list[RowItemResponse] = []
        for row in df.iter_rows(named=True):
            raw_prompt = str(row.get("prompt") or "")
            raw_response = str(row.get("response") or "")
            raw_text = str(row.get("text") or "")
            raw_meta = str(row.get("metadata_json") or "")
            row_id_val = str(row["row_id"])
            has_pii = bool(
                detect_pii_spans(raw_text)
                or detect_pii_spans(raw_prompt)
                or detect_pii_spans(raw_response)
                or (raw_meta and detect_pii_spans(raw_meta))
            )
            disp_prompt = apply_pii_transform(raw_prompt, mode="mask") if has_pii else raw_prompt
            disp_response = (
                apply_pii_transform(raw_response, mode="mask") if has_pii else raw_response
            )
            disp_text = apply_pii_transform(raw_text, mode="mask") if has_pii else raw_text
            if (
                needle in disp_text.lower()
                or needle in disp_prompt.lower()
                or needle in disp_response.lower()
                or needle in row_id_val.lower()
            ):
                matched.append(
                    RowItemResponse(
                        row_id=row_id_val,
                        line_number=int(row["line_number"]),
                        prompt=disp_prompt,
                        response=disp_response,
                        text=disp_text,
                        is_valid=bool(row["is_valid"]),
                        validation_error=str(row.get("validation_error") or ""),
                        curation_status=str(row.get("curation_status") or "pending"),
                        has_pii=has_pii,
                        metadata=_parse_row_metadata(raw_meta, mask_pii=True),
                    )
                )
        total = len(matched)
        items = matched[offset : offset + limit]
    else:
        total = df.height
        page_df = df.slice(offset, limit)
        for row in page_df.iter_rows(named=True):
            prompt = str(row.get("prompt") or "")
            response = str(row.get("response") or "")
            text = str(row.get("text") or "")
            raw_meta = str(row.get("metadata_json") or "")
            has_pii = bool(
                detect_pii_spans(text)
                or detect_pii_spans(prompt)
                or detect_pii_spans(response)
                or (raw_meta and detect_pii_spans(raw_meta))
            )

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
                    metadata=_parse_row_metadata(raw_meta, mask_pii=not should_reveal),
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


def _build_redacted_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    """Build a curation audit snapshot with detected PII masked before DB persistence."""
    prompt_raw = str(row.get("prompt") or "")
    response_raw = str(row.get("response") or "")
    text_raw = str(row.get("text") or "")
    has_pii = bool(
        detect_pii_spans(prompt_raw) or detect_pii_spans(response_raw) or detect_pii_spans(text_raw)
    )
    return {
        "prompt": apply_pii_transform(prompt_raw, mode="mask") if has_pii else prompt_raw,
        "response": apply_pii_transform(response_raw, mode="mask") if has_pii else response_raw,
        "curation_status": str(row.get("curation_status") or ""),
        "pii_redacted": has_pii,
    }


async def apply_curation_batch(
    db: AsyncSession,
    *,
    version_id: str,
    actions: list[CurationActionItem],
    user_id: str,
    ip_address: str | None = None,
) -> DatasetVersion:
    """Apply curation actions and create a new immutable DatasetVersion."""
    ver = await get_version_or_404(db, version_id)
    ds = await get_dataset_or_404(db, ver.dataset_id)
    pq_bytes = load_version_parquet_bytes(ds.id, ver.version_number)
    df = pl.read_parquet(io.BytesIO(pq_bytes))

    action_map = {item.row_id: item for item in actions}
    updated_rows: list[dict[str, Any]] = []
    pending_actions: list[CurationAction] = []

    for row in df.iter_rows(named=True):
        rid = str(row["row_id"])
        act = action_map.get(rid)
        if act is None:
            updated_rows.append(dict(row))
            continue

        row_dict = dict(row)
        old_snapshot = _build_redacted_snapshot(row_dict)
        new_row = dict(row_dict)

        if act.action == "drop_row":
            pending_actions.append(
                CurationAction(
                    version_id=ver.id,
                    row_id=rid,
                    action=act.action,
                    old_value=old_snapshot,
                    new_value={
                        "curation_status": "dropped",
                        "dropped": True,
                        "pii_redacted": old_snapshot["pii_redacted"],
                    },
                    user_id=user_id,
                )
            )
            continue

        if act.action == "approve":
            new_row["curation_status"] = "approved"
        elif act.action == "reject":
            new_row["curation_status"] = "rejected"
        elif act.action == "flag":
            new_row["curation_status"] = "flagged"
        elif act.action in {"mask_pii", "hash_pii"}:
            pii_mode = "hash" if act.action == "hash_pii" else "mask"
            new_row["prompt"] = apply_pii_transform(str(row["prompt"] or ""), mode=pii_mode)
            new_row["response"] = apply_pii_transform(str(row["response"] or ""), mode=pii_mode)
            new_row["text"] = apply_pii_transform(str(row["text"] or ""), mode=pii_mode)
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
            if ds.dataset_type == "raw":
                row_valid = bool(str(new_row.get("text") or "").strip())
            elif act.prompt is not None or act.response is not None:
                row_valid = bool(str(new_row.get("prompt") or "").strip()) and bool(
                    str(new_row.get("response") or "").strip()
                )
            else:
                row_valid = bool(str(new_row.get("text") or "").strip())
            new_row["is_valid"] = row_valid
            new_row["validation_error"] = "" if row_valid else "EMPTY_CONTENT"
            new_row["curation_status"] = "approved"

        updated_rows.append(new_row)
        pending_actions.append(
            CurationAction(
                version_id=ver.id,
                row_id=rid,
                action=act.action,
                old_value=old_snapshot,
                new_value=_build_redacted_snapshot(new_row),
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
    new_invalid_count = (
        int(new_df.filter(~pl.col("is_valid")).height) if "is_valid" in new_df.columns else 0
    )
    updated_summary = dict(ver.validation_summary or {})
    updated_summary["total_rows"] = new_df.height
    updated_summary["invalid_rows"] = new_invalid_count
    updated_summary["valid_rows"] = max(0, new_df.height - new_invalid_count)

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
        invalid_row_count=new_invalid_count,
        checksum=hashlib.sha256(pq_payload).hexdigest(),
        status="curated",
        validation_summary=updated_summary,
    )
    db.add(new_ver)
    await db.flush()

    for ca in pending_actions:
        ca.resulting_version_id = new_ver.id
        db.add(ca)

    await record_audit_log(
        db,
        action="dataset.curate",
        target_type="dataset_version",
        target_id=new_ver.id,
        user_id=user_id,
        workspace_id=ds.workspace_id,
        ip_address=ip_address,
        metadata={
            "parent_version_id": ver.id,
            "new_version_number": next_num,
            "actions_count": len(actions),
        },
    )
    await db.commit()
    await db.refresh(new_ver)
    return new_ver
