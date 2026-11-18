"""Export pipeline with Quality Gate enforcement and manifest generation (Module F5)."""

import io
import json
from datetime import UTC, datetime
from typing import Any

import polars as pl
from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.models import ExportRecord, QualityPolicy, QualityRun
from app.modules.cleaning.pii import detect_pii_spans
from app.services.audit_service import record_audit_log
from app.services.dataset_service import (
    get_dataset_or_404,
    get_version_or_404,
    load_version_parquet_bytes,
)
from app.storage.s3 import get_storage_backend


async def create_export(
    db: AsyncSession,
    *,
    version_id: str,
    export_format: str,
    exclude_rejected: bool,
    only_approved: bool,
    enforce_quality_gate: bool,
    user_id: str,
) -> ExportRecord:
    """Validate quality gate, filter rows, write export + manifest.json, and audit."""
    ver = await get_version_or_404(db, version_id)
    ds = await get_dataset_or_404(db, ver.dataset_id)

    latest_run = (
        await db.execute(
            select(QualityRun)
            .where(QualityRun.version_id == ver.id, QualityRun.status == "completed")
            .order_by(QualityRun.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    policy = (
        await db.execute(select(QualityPolicy).where(QualityPolicy.workspace_id == ds.workspace_id))
    ).scalar_one_or_none()

    pq_bytes = load_version_parquet_bytes(ds.id, ver.version_number)
    df = pl.read_parquet(io.BytesIO(pq_bytes))

    if only_approved:
        df = df.filter(pl.col("curation_status") == "approved")
    elif exclude_rejected:
        df = df.filter(pl.col("curation_status") != "rejected")

    # Quality Gate check (US-3 & Module F7)
    if enforce_quality_gate:
        block_pii = True
        min_dqi = 0.0
        if policy and policy.is_active:
            block_pii = bool(policy.rules_json.get("block_if_critical_pii", True))
            min_dqi = float(policy.rules_json.get("min_dqi", 0.0))

        if block_pii:
            for row in df.select(["row_id", "text"]).iter_rows(named=True):
                if detect_pii_spans(str(row.get("text") or "")):
                    raise AppError(
                        code="QUALITY_GATE_PII_BLOCKED",
                        message=(
                            "Ekspor diblokir oleh kebijakan kualitas karena masih terdapat PII "
                            "mentah pada baris yang akan diekspor. Lakukan masking terlebih dahulu."
                        ),
                        status_code=status.HTTP_409_CONFLICT,
                        details={"row_id": str(row["row_id"])},
                    )

        if min_dqi > 0.0 and latest_run and (latest_run.dqi_total or 0.0) < min_dqi:
            raise AppError(
                code="QUALITY_GATE_DQI_BELOW_MIN",
                message=(
                    f"Ekspor diblokir: DQI ({latest_run.dqi_total}) di bawah ambang batas "
                    f"minimum ({min_dqi})."
                ),
                status_code=status.HTTP_409_CONFLICT,
            )

    export_cols = ["row_id", "prompt", "response", "text", "curation_status"]
    out_df = df.select([c for c in export_cols if c in df.columns])

    if export_format == "jsonl":
        lines = [json.dumps(r, ensure_ascii=False) for r in out_df.iter_rows(named=True)]
        payload_bytes = ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")
    elif export_format == "csv":
        payload_bytes = out_df.write_csv().encode("utf-8")
    elif export_format == "parquet":
        buf = io.BytesIO()
        out_df.write_parquet(buf)
        payload_bytes = buf.getvalue()
    else:
        raise AppError(
            code="UNSUPPORTED_EXPORT_FORMAT",
            message=f"Format ekspor tidak didukung: {export_format}",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    manifest: dict[str, Any] = {
        "dataset_id": ds.id,
        "dataset_name": ds.name,
        "version_id": ver.id,
        "version_number": ver.version_number,
        "export_format": export_format,
        "exported_rows": out_df.height,
        "dqi_total": latest_run.dqi_total if latest_run else None,
        "exported_at": datetime.now(UTC).isoformat(),
    }

    storage = get_storage_backend()
    ts_tag = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    exp_key = f"exports/{ds.id}/v{ver.version_number}/export_{ts_tag}.{export_format}"
    storage_uri = storage.put_bytes(exp_key, payload_bytes)
    storage.put_bytes(f"{exp_key}.manifest.json", json.dumps(manifest, indent=2).encode("utf-8"))

    record = ExportRecord(
        version_id=ver.id,
        user_id=user_id,
        export_format=export_format,
        filter_json={"exclude_rejected": exclude_rejected, "only_approved": only_approved},
        storage_uri=storage_uri,
        manifest_json=manifest,
        status="completed",
    )
    db.add(record)
    await db.flush()

    await record_audit_log(
        db,
        action="dataset.export",
        target_type="export",
        target_id=record.id,
        user_id=user_id,
        workspace_id=ds.workspace_id,
        metadata={
            "version_id": ver.id,
            "export_format": export_format,
            "exported_rows": out_df.height,
        },
    )
    await db.commit()
    await db.refresh(record)
    return record
