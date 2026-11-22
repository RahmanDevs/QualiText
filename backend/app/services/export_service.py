"""Export pipeline with Quality Gate enforcement and manifest generation (Module F5)."""

import io
import json
import uuid
import zipfile
from datetime import UTC, datetime
from typing import Any

import polars as pl
from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.models import ExportRecord, QualityPolicy, QualityRun
from app.modules.cleaning.language import detect_language
from app.modules.cleaning.pii import detect_pii_spans
from app.modules.ingestion.semantic_chunker import extract_metadata_keywords
from app.modules.ingestion.token_analytics import estimate_token_count
from app.services.audit_service import record_audit_log
from app.services.dataset_service import (
    get_dataset_or_404,
    get_version_or_404,
    load_version_parquet_bytes,
)
from app.services.webhook_service import dispatch_workspace_webhooks
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
    ip_address: str | None = None,
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
            pii_check_cols = [
                c
                for c in (
                    "row_id",
                    "prompt",
                    "response",
                    "text",
                    "chosen",
                    "rejected",
                    "metadata_json",
                )
                if c in df.columns
            ]
            for row in df.select(pii_check_cols).iter_rows(named=True):
                for col_name in pii_check_cols:
                    if col_name == "row_id":
                        continue
                    if detect_pii_spans(str(row.get(col_name) or "")):
                        raise AppError(
                            code="QUALITY_GATE_PII_BLOCKED",
                            message=(
                                "Ekspor diblokir oleh kebijakan kualitas karena masih terdapat "
                                "PII mentah pada baris yang akan diekspor. "
                                "Lakukan masking terlebih dahulu."
                            ),
                            status_code=status.HTTP_409_CONFLICT,
                            details={"row_id": str(row["row_id"])},
                        )

        if min_dqi > 0.0:
            if latest_run is None or latest_run.dqi_total is None:
                raise AppError(
                    code="QUALITY_GATE_NO_RUN",
                    message=(
                        "Ekspor diblokir: versi dataset ini belum memiliki run penilaian "
                        f"kualitas selesai untuk memverifikasi ambang minimum DQI ({min_dqi})."
                    ),
                    status_code=status.HTTP_409_CONFLICT,
                )
            if latest_run.dqi_total < min_dqi:
                raise AppError(
                    code="QUALITY_GATE_DQI_BELOW_MIN",
                    message=(
                        f"Ekspor diblokir: DQI ({latest_run.dqi_total}) di bawah ambang batas "
                        f"minimum ({min_dqi})."
                    ),
                    status_code=status.HTTP_409_CONFLICT,
                )

        raw_dim_thresholds = (
            policy.rules_json.get("dimension_thresholds", {}) if policy and policy.is_active else {}
        )
        if isinstance(raw_dim_thresholds, dict):
            dim_thresholds = {
                str(k): float(v)
                for k, v in raw_dim_thresholds.items()
                if isinstance(v, (int, float)) and float(v) > 0.0
            }
            if dim_thresholds:
                if latest_run is None:
                    raise AppError(
                        code="QUALITY_GATE_NO_RUN",
                        message=(
                            "Ekspor diblokir: belum ada run selesai untuk memverifikasi ambang "
                            "batas dimensi kebijakan kualitas."
                        ),
                        status_code=status.HTTP_409_CONFLICT,
                    )
                dim_scores = {
                    str(item.get("dimension")): float(item.get("score", 0.0))
                    for item in latest_run.dqi_summary.get("dimensions", [])
                    if isinstance(item, dict) and item.get("dimension")
                }
                for dim_key, threshold_val in dim_thresholds.items():
                    if dim_key not in dim_scores:
                        raise AppError(
                            code="QUALITY_GATE_DIMENSION_MISSING",
                            message=(
                                f"Ekspor diblokir: dimensi wajib `{dim_key}` (ambang minimum "
                                f"{threshold_val}) belum dinilai pada run terakhir."
                            ),
                            status_code=status.HTTP_409_CONFLICT,
                            details={"dimension": dim_key, "threshold": threshold_val},
                        )
                    if dim_scores[dim_key] < threshold_val:
                        raise AppError(
                            code="QUALITY_GATE_DIMENSION_BELOW_MIN",
                            message=(
                                f"Ekspor diblokir: skor dimensi `{dim_key}` ({dim_scores[dim_key]})"
                                f" di bawah ambang minimum ({threshold_val})."
                            ),
                            status_code=status.HTTP_409_CONFLICT,
                            details={
                                "dimension": dim_key,
                                "score": dim_scores[dim_key],
                                "threshold": threshold_val,
                            },
                        )

    export_cols = ["row_id", "prompt", "response", "text", "curation_status"]
    out_df = df.select([c for c in export_cols if c in df.columns])

    manifest: dict[str, Any] = {
        "dataset_id": ds.id,
        "dataset_name": ds.name,
        "version_id": ver.id,
        "version_number": ver.version_number,
        "export_format": export_format,
        "exported_rows": out_df.height,
        "dqi_total": latest_run.dqi_total if latest_run else None,
        "seed": latest_run.seed if latest_run else None,
        "config_snapshot": latest_run.config_snapshot if latest_run else None,
        "exported_at": datetime.now(UTC).isoformat(),
    }

    if export_format == "jsonl":
        lines = [json.dumps(r, ensure_ascii=False) for r in out_df.iter_rows(named=True)]
        payload_bytes = ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")
    elif export_format == "rag_vector":
        rag_lines: list[str] = []
        for row in df.iter_rows(named=True):
            row_text = str(row.get("text") or "")
            meta_out: dict[str, Any] = {}
            raw_meta = row.get("metadata_json")
            if isinstance(raw_meta, str) and raw_meta.strip():
                try:
                    parsed = json.loads(raw_meta)
                    if isinstance(parsed, dict):
                        meta_out = dict(parsed)
                except json.JSONDecodeError:
                    meta_out = {}
            if not meta_out:
                meta_out = {
                    "language": detect_language(row_text),
                    "token_count": estimate_token_count(row_text),
                    "keywords": extract_metadata_keywords(row_text, top_k=5),
                    "line_number": row.get("line_number"),
                }
            meta_out["dataset_id"] = ds.id
            meta_out["version_number"] = ver.version_number
            meta_out["curation_status"] = str(row.get("curation_status") or "pending")
            rag_lines.append(
                json.dumps(
                    {
                        "id": str(row["row_id"]),
                        "text": row_text,
                        "metadata": meta_out,
                    },
                    ensure_ascii=False,
                )
            )
        payload_bytes = ("\n".join(rag_lines) + ("\n" if rag_lines else "")).encode("utf-8")
    elif export_format == "sft_chat":
        chat_lines: list[str] = []
        for row in df.iter_rows(named=True):
            msgs: list[dict[str, str]] = []
            raw_msgs = row.get("messages_json")
            if isinstance(raw_msgs, str) and raw_msgs.strip():
                try:
                    parsed_msgs = json.loads(raw_msgs)
                    if isinstance(parsed_msgs, list):
                        msgs = [
                            {"role": str(m["role"]), "content": str(m["content"])}
                            for m in parsed_msgs
                            if isinstance(m, dict) and m.get("role") and m.get("content")
                        ]
                except json.JSONDecodeError:
                    msgs = []
            if not msgs:
                p_val = str(row.get("prompt") or "").strip()
                r_val = str(row.get("response") or "").strip()
                t_val = str(row.get("text") or "").strip()
                if p_val and r_val:
                    msgs = [
                        {"role": "user", "content": p_val},
                        {"role": "assistant", "content": r_val},
                    ]
                elif p_val:
                    msgs = [{"role": "user", "content": p_val}]
                elif t_val:
                    msgs = [{"role": "user", "content": t_val}]
            chat_lines.append(json.dumps({"messages": msgs}, ensure_ascii=False))
        payload_bytes = ("\n".join(chat_lines) + ("\n" if chat_lines else "")).encode("utf-8")
    elif export_format == "csv":
        payload_bytes = out_df.write_csv().encode("utf-8")
    elif export_format == "parquet":
        buf = io.BytesIO()
        out_df.write_parquet(buf)
        payload_bytes = buf.getvalue()
    elif export_format == "huggingface":
        hf_cols = [c for c in export_cols if c in df.columns]
        for extra_col in ("messages_json", "metadata_json"):
            if extra_col in df.columns:
                non_empty = df.filter(pl.col(extra_col).cast(pl.Utf8).str.len_chars() > 0).height
                if non_empty > 0:
                    hf_cols.append(extra_col)
        hf_df = df.select(hf_cols)

        pq_buf = io.BytesIO()
        hf_df.write_parquet(pq_buf)
        pq_bytes = pq_buf.getvalue()

        features = {col: {"dtype": "string", "_type": "Value"} for col in hf_df.columns}
        dataset_info = {
            "dataset_name": ds.name,
            "description": ds.description,
            "features": features,
            "splits": {
                "train": {
                    "name": "train",
                    "num_examples": hf_df.height,
                    "num_bytes": len(pq_bytes),
                }
            },
        }
        readme_md = (
            "---\n"
            "configs:\n"
            "- config_name: default\n"
            "  data_files:\n"
            "  - split: train\n"
            "    path: data/train-*.parquet\n"
            "---\n\n"
            f"# {ds.name}\n\n"
            f"Diekspor dari QualiText (v{ver.version_number}, "
            f"DQI: {manifest['dqi_total']}, baris: {hf_df.height}).\n"
        )
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("data/train-00000-of-00001.parquet", pq_bytes)
            zf.writestr("dataset_info.json", json.dumps(dataset_info, indent=2))
            zf.writestr("README.md", readme_md)
            zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        payload_bytes = zip_buf.getvalue()
    else:
        raise AppError(
            code="UNSUPPORTED_EXPORT_FORMAT",
            message=f"Format ekspor tidak didukung: {export_format}",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    storage = get_storage_backend()
    ts_tag = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    if export_format == "huggingface":
        ext = "hf.zip"
    elif export_format in {"rag_vector", "sft_chat"}:
        ext = f"{export_format}.jsonl"
    else:
        ext = export_format
    exp_key = f"exports/{ds.id}/v{ver.version_number}/export_{ts_tag}_{uuid.uuid4().hex[:8]}.{ext}"
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
        ip_address=ip_address,
        metadata={
            "version_id": ver.id,
            "export_format": export_format,
            "exported_rows": out_df.height,
            "quality_gate_bypassed": not enforce_quality_gate,
        },
    )
    await db.commit()
    await db.refresh(record)
    await dispatch_workspace_webhooks(
        db,
        workspace_id=ds.workspace_id,
        event_type="export.completed",
        payload={
            "export_id": record.id,
            "dataset_id": ds.id,
            "version_id": ver.id,
            "export_format": export_format,
            "exported_rows": out_df.height,
            "dqi_total": manifest["dqi_total"],
        },
    )
    return record
