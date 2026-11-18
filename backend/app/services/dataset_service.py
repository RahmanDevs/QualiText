"""Dataset ingestion, chunked upload assembly, Parquet normalization, and mapping service."""

import hashlib
import io
from typing import Any

from fastapi import status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.models import ColumnMapping, Dataset, DatasetVersion
from app.modules.ingestion.validator import IngestionOutput, parse_and_validate
from app.services.audit_service import record_audit_log
from app.storage.s3 import get_storage_backend


async def get_dataset_or_404(db: AsyncSession, dataset_id: str) -> Dataset:
    """Fetch a Dataset by ID or raise 404 AppError."""
    ds = (await db.execute(select(Dataset).where(Dataset.id == dataset_id))).scalar_one_or_none()
    if ds is None:
        raise AppError(
            code="DATASET_NOT_FOUND",
            message="Dataset tidak ditemukan.",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return ds


async def get_version_or_404(db: AsyncSession, version_id: str) -> DatasetVersion:
    """Fetch a DatasetVersion by ID or raise 404 AppError."""
    ver = (
        await db.execute(select(DatasetVersion).where(DatasetVersion.id == version_id))
    ).scalar_one_or_none()
    if ver is None:
        raise AppError(
            code="VERSION_NOT_FOUND",
            message="Versi dataset tidak ditemukan.",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return ver


async def create_dataset(
    db: AsyncSession,
    *,
    workspace_id: str,
    name: str,
    description: str,
    dataset_type: str,
    user_id: str,
) -> Dataset:
    """Create a new logical dataset in a workspace."""
    ds = Dataset(
        workspace_id=workspace_id,
        name=name,
        description=description,
        dataset_type=dataset_type,
        created_by=user_id,
    )
    db.add(ds)
    await db.flush()
    await record_audit_log(
        db,
        action="dataset.create",
        target_type="dataset",
        target_id=ds.id,
        user_id=user_id,
        workspace_id=workspace_id,
        metadata={"name": name, "dataset_type": dataset_type},
    )
    await db.commit()
    await db.refresh(ds)
    return ds


async def _next_version_number(db: AsyncSession, dataset_id: str) -> int:
    max_num = (
        await db.execute(
            select(func.max(DatasetVersion.version_number)).where(
                DatasetVersion.dataset_id == dataset_id
            )
        )
    ).scalar_one_or_none()
    return (max_num or 0) + 1


async def init_version_upload(
    db: AsyncSession,
    *,
    dataset: Dataset,
    source_format: str,
) -> DatasetVersion:
    """Create a new DatasetVersion in `uploading` state."""
    next_num = await _next_version_number(db, dataset.id)
    ver = DatasetVersion(
        dataset_id=dataset.id,
        version_number=next_num,
        source_format=source_format,
        status="uploading",
    )
    db.add(ver)
    await db.commit()
    await db.refresh(ver)
    return ver


def store_upload_chunk(version_id: str, chunk_index: int, data: bytes) -> str:
    """Persist a single upload chunk to object storage."""
    storage = get_storage_backend()
    key = f"uploads/{version_id}/chunk_{chunk_index:05d}"
    return storage.put_bytes(key, data)


def _ingest_to_parquet(
    raw_bytes: bytes,
    dataset: Dataset,
    version: DatasetVersion,
    mapping: dict[str, str] | None,
) -> IngestionOutput:
    """Validate raw bytes and write normalized Parquet to storage."""
    output = parse_and_validate(
        raw_bytes=raw_bytes,
        source_format=version.source_format,
        dataset_type=dataset.dataset_type,
        column_mapping=mapping,
    )
    storage = get_storage_backend()
    raw_key = f"datasets/{dataset.id}/v{version.version_number}/raw.{version.source_format}"
    raw_uri = storage.put_bytes(raw_key, raw_bytes)

    buf = io.BytesIO()
    output.df.write_parquet(buf)
    pq_key = f"datasets/{dataset.id}/v{version.version_number}/data.parquet"
    pq_uri = storage.put_bytes(pq_key, buf.getvalue())

    version.raw_storage_uri = raw_uri
    version.storage_uri = pq_uri
    version.row_count = output.total_rows
    version.invalid_row_count = output.invalid_rows
    version.checksum = hashlib.sha256(raw_bytes).hexdigest()
    version.status = "validated"
    version.validation_summary = {
        "total_rows": output.total_rows,
        "valid_rows": output.valid_rows,
        "invalid_rows": output.invalid_rows,
        "detected_columns": output.detected_columns,
        "errors": [
            {
                "line_number": e.line_number,
                "error_type": e.error_type,
                "message": e.message,
            }
            for e in output.errors[:500]
        ],
    }
    return output


async def complete_version_upload(
    db: AsyncSession,
    *,
    dataset: Dataset,
    version: DatasetVersion,
    total_chunks: int,
    user_id: str,
) -> DatasetVersion:
    """Assemble uploaded chunks, validate schema, normalize to Parquet, and audit."""
    storage = get_storage_backend()
    parts: list[bytes] = []
    for idx in range(total_chunks):
        key = f"uploads/{version.id}/chunk_{idx:05d}"
        if not storage.exists(key):
            raise AppError(
                code="MISSING_CHUNK",
                message=f"Potongan unggahan ke-{idx} tidak ditemukan.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        parts.append(storage.get_bytes(key))
    raw_bytes = b"".join(parts)

    _ingest_to_parquet(raw_bytes, dataset, version, mapping=None)
    await record_audit_log(
        db,
        action="dataset.upload",
        target_type="dataset_version",
        target_id=version.id,
        user_id=user_id,
        workspace_id=dataset.workspace_id,
        metadata={
            "dataset_id": dataset.id,
            "version_number": version.version_number,
            "row_count": version.row_count,
            "invalid_row_count": version.invalid_row_count,
        },
    )
    await db.commit()
    await db.refresh(version)
    return version


async def direct_upload_version(
    db: AsyncSession,
    *,
    dataset: Dataset,
    source_format: str,
    raw_bytes: bytes,
    user_id: str,
) -> DatasetVersion:
    """Create and validate a dataset version from a single uploaded payload."""
    ver = await init_version_upload(db, dataset=dataset, source_format=source_format)
    _ingest_to_parquet(raw_bytes, dataset, ver, mapping=None)
    await record_audit_log(
        db,
        action="dataset.upload",
        target_type="dataset_version",
        target_id=ver.id,
        user_id=user_id,
        workspace_id=dataset.workspace_id,
        metadata={
            "dataset_id": dataset.id,
            "version_number": ver.version_number,
            "row_count": ver.row_count,
        },
    )
    await db.commit()
    await db.refresh(ver)
    return ver


async def update_column_mapping(
    db: AsyncSession,
    *,
    version: DatasetVersion,
    mapping: dict[str, str],
) -> DatasetVersion:
    """Save column mapping and re-run normalization + validation on the raw source."""
    ds = await get_dataset_or_404(db, version.dataset_id)
    storage = get_storage_backend()
    raw_key = f"datasets/{ds.id}/v{version.version_number}/raw.{version.source_format}"
    if not storage.exists(raw_key):
        raise AppError(
            code="RAW_SOURCE_NOT_FOUND",
            message="Berkas mentah versi ini tidak ditemukan untuk pemetaan ulang.",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    raw_bytes = storage.get_bytes(raw_key)

    existing = (
        await db.execute(select(ColumnMapping).where(ColumnMapping.version_id == version.id))
    ).scalar_one_or_none()
    if existing is None:
        db.add(ColumnMapping(version_id=version.id, mapping_json=mapping))
    else:
        existing.mapping_json = mapping

    _ingest_to_parquet(raw_bytes, ds, version, mapping=mapping)
    await db.commit()
    await db.refresh(version)
    return version


def load_version_parquet_bytes(dataset_id: str, version_number: int) -> bytes:
    """Read normalized Parquet bytes for a dataset version from storage."""
    storage = get_storage_backend()
    pq_key = f"datasets/{dataset_id}/v{version_number}/data.parquet"
    return storage.get_bytes(pq_key)


def get_validation_summary_errors(version: DatasetVersion) -> list[dict[str, Any]]:
    """Return structured validation errors recorded during ingestion."""
    errors = version.validation_summary.get("errors", [])
    return list(errors) if isinstance(errors, list) else []
