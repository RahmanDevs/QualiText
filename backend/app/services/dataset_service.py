"""Dataset ingestion, chunked upload assembly, Parquet normalization, and mapping service."""

import hashlib
import io
import tempfile
from datetime import UTC, datetime, timedelta
from typing import IO, Any, cast

from fastapi import status
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.models import (
    ColumnMapping,
    CurationAction,
    Dataset,
    DatasetVersion,
    ExportRecord,
    QualityRun,
    RowFindingRecord,
    RunModuleResult,
    Workspace,
)
from app.modules.ingestion.validator import (
    IngestionOutput,
    parse_and_validate,
    parse_and_validate_stream,
)
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
    retention_days: int | None = None,
    ip_address: str | None = None,
) -> Dataset:
    """Create a new logical dataset in a workspace."""
    ds = Dataset(
        workspace_id=workspace_id,
        name=name,
        description=description,
        dataset_type=dataset_type,
        retention_days=retention_days,
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
        ip_address=ip_address,
        metadata={
            "name": name,
            "dataset_type": dataset_type,
            "retention_days": retention_days,
        },
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
    if chunk_index < 0:
        raise AppError(
            code="INVALID_CHUNK_INDEX",
            message="Indeks potongan unggahan tidak boleh negatif.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    storage = get_storage_backend()
    key = f"uploads/{version_id}/chunk_{chunk_index:05d}"
    return storage.put_bytes(key, data)


def _ingest_to_parquet(
    raw_bytes: bytes | None,
    dataset: Dataset,
    version: DatasetVersion,
    mapping: dict[str, str] | None,
    *,
    raw_stream: IO[bytes] | None = None,
    checksum: str | None = None,
    raw_uri: str | None = None,
) -> IngestionOutput:
    """Validate raw bytes or stream and write normalized Parquet to storage."""
    if raw_stream is not None:
        raw_stream.seek(0)
        output = parse_and_validate_stream(
            raw_stream,
            source_format=version.source_format,
            dataset_type=dataset.dataset_type,
            column_mapping=mapping,
        )
    elif raw_bytes is not None:
        output = parse_and_validate(
            raw_bytes=raw_bytes,
            source_format=version.source_format,
            dataset_type=dataset.dataset_type,
            column_mapping=mapping,
        )
    else:
        raise ValueError("Diperlukan raw_bytes atau raw_stream untuk proses ingesti.")

    storage = get_storage_backend()
    if raw_uri is None:
        raw_key = f"datasets/{dataset.id}/v{version.version_number}/raw.{version.source_format}"
        if raw_stream is not None:
            raw_stream.seek(0)
            raw_uri = storage.put_file(raw_key, raw_stream)
        elif raw_bytes is not None:
            raw_uri = storage.put_bytes(raw_key, raw_bytes)

    if checksum is None:
        if raw_bytes is not None:
            checksum = hashlib.sha256(raw_bytes).hexdigest()
        elif raw_stream is not None:
            hasher = hashlib.sha256()
            raw_stream.seek(0)
            while chunk := raw_stream.read(1024 * 1024):
                hasher.update(chunk)
            checksum = hasher.hexdigest()

    buf = io.BytesIO()
    output.df.write_parquet(buf)
    pq_key = f"datasets/{dataset.id}/v{version.version_number}/data.parquet"
    pq_uri = storage.put_bytes(pq_key, buf.getvalue())

    version.raw_storage_uri = raw_uri or ""
    version.storage_uri = pq_uri
    version.row_count = output.total_rows
    version.invalid_row_count = output.invalid_rows
    version.checksum = checksum or ""
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
    ip_address: str | None = None,
) -> DatasetVersion:
    """Assemble uploaded chunks via spooled temp storage, validate stream, normalize, and audit."""
    if version.status != "uploading":
        raise AppError(
            code="VERSION_ALREADY_FINALIZED",
            message="Versi dataset ini sudah difinalisasi dan bersifat immutable.",
            status_code=status.HTTP_409_CONFLICT,
        )
    if total_chunks < 1:
        raise AppError(
            code="INVALID_CHUNK_COUNT",
            message="Jumlah potongan unggahan minimal 1.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    storage = get_storage_backend()
    hasher = hashlib.sha256()
    chunk_keys: list[str] = []

    with tempfile.SpooledTemporaryFile(max_size=32 * 1024 * 1024) as spool:
        for idx in range(total_chunks):
            key = f"uploads/{version.id}/chunk_{idx:05d}"
            if not storage.exists(key):
                raise AppError(
                    code="MISSING_CHUNK",
                    message=f"Potongan unggahan ke-{idx} tidak ditemukan.",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
            chunk_bytes = storage.get_bytes(key)
            hasher.update(chunk_bytes)
            spool.write(chunk_bytes)
            chunk_keys.append(key)

        spool.seek(0)
        raw_key = f"datasets/{dataset.id}/v{version.version_number}/raw.{version.source_format}"
        raw_uri = storage.put_file(raw_key, spool)
        spool.seek(0)
        try:
            _ingest_to_parquet(
                None,
                dataset,
                version,
                mapping=None,
                raw_stream=cast(IO[bytes], spool),
                checksum=hasher.hexdigest(),
                raw_uri=raw_uri,
            )
        except Exception:
            version.status = "failed"
            await db.commit()
            for key in (*chunk_keys, raw_key):
                if storage.exists(key):
                    storage.delete_key(key)
            raise

    for key in chunk_keys:
        storage.delete_key(key)
    await record_audit_log(
        db,
        action="dataset.upload",
        target_type="dataset_version",
        target_id=version.id,
        user_id=user_id,
        workspace_id=dataset.workspace_id,
        ip_address=ip_address,
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
    raw_bytes: bytes | None = None,
    raw_stream: IO[bytes] | None = None,
    checksum: str | None = None,
    user_id: str,
    ip_address: str | None = None,
) -> DatasetVersion:
    """Create and validate a dataset version from bytes or a spooled binary stream."""
    ds_id = dataset.id
    next_num = await _next_version_number(db, ds_id)
    ver = DatasetVersion(
        dataset_id=ds_id,
        version_number=next_num,
        source_format=source_format,
        status="uploading",
    )
    db.add(ver)
    await db.flush()
    try:
        _ingest_to_parquet(
            raw_bytes,
            dataset,
            ver,
            mapping=None,
            raw_stream=raw_stream,
            checksum=checksum,
        )
    except Exception:
        await db.rollback()
        storage = get_storage_backend()
        for key in (
            f"datasets/{ds_id}/v{next_num}/raw.{source_format}",
            f"datasets/{ds_id}/v{next_num}/data.parquet",
        ):
            if storage.exists(key):
                storage.delete_key(key)
        raise

    await record_audit_log(
        db,
        action="dataset.upload",
        target_type="dataset_version",
        target_id=ver.id,
        user_id=user_id,
        workspace_id=dataset.workspace_id,
        ip_address=ip_address,
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
    user_id: str | None = None,
    ip_address: str | None = None,
) -> DatasetVersion:
    """Save column mapping, re-run validation, and append audit log."""
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
    await record_audit_log(
        db,
        action="dataset.mapping_update",
        target_type="dataset_version",
        target_id=version.id,
        user_id=user_id,
        workspace_id=ds.workspace_id,
        ip_address=ip_address,
        metadata={"mapping_keys": sorted(mapping.keys())},
    )
    await db.commit()
    await db.refresh(version)
    return version


def load_version_parquet_bytes(dataset_id: str, version_number: int) -> bytes:
    """Read normalized Parquet bytes for a dataset version from storage."""
    storage = get_storage_backend()
    pq_key = f"datasets/{dataset_id}/v{version_number}/data.parquet"
    if not storage.exists(pq_key):
        raise AppError(
            code="VERSION_DATA_NOT_READY",
            message="Data Parquet untuk versi ini belum tersedia.",
            status_code=status.HTTP_409_CONFLICT,
        )
    return storage.get_bytes(pq_key)


def get_validation_summary_errors(version: DatasetVersion) -> list[dict[str, Any]]:
    """Return structured validation errors recorded during ingestion."""
    errors = version.validation_summary.get("errors", [])
    return list(errors) if isinstance(errors, list) else []


def storage_key_from_uri(uri: str) -> str | None:
    """Extract object storage key from `local://...` or `s3://bucket/...` URI."""
    if not uri:
        return None
    if uri.startswith("local://"):
        return uri.removeprefix("local://").lstrip("/")
    if uri.startswith("s3://"):
        rest = uri.removeprefix("s3://")
        parts = rest.split("/", 1)
        return parts[1].lstrip("/") if len(parts) == 2 and parts[1] else None
    return None


async def delete_dataset_and_artifacts(
    db: AsyncSession,
    *,
    dataset: Dataset,
    user_id: str | None = None,
    ip_address: str | None = None,
    audit_action: str = "dataset.delete",
    auto_commit: bool = True,
) -> int:
    """Erase a dataset, dependent rows, and stored object artifacts (UU PDP right to erasure)."""
    ds_id = dataset.id
    ws_id = dataset.workspace_id
    ds_name = dataset.name

    versions = (
        (await db.execute(select(DatasetVersion).where(DatasetVersion.dataset_id == ds_id)))
        .scalars()
        .all()
    )
    version_ids = [v.id for v in versions]

    storage = get_storage_backend()
    keys_to_delete: set[str] = set()
    for ver in versions:
        for uri in (ver.raw_storage_uri, ver.storage_uri):
            key = storage_key_from_uri(uri)
            if key:
                keys_to_delete.add(key)
        keys_to_delete.add(f"datasets/{ds_id}/v{ver.version_number}/raw.{ver.source_format}")
        keys_to_delete.add(f"datasets/{ds_id}/v{ver.version_number}/data.parquet")

    if version_ids:
        exports = (
            (await db.execute(select(ExportRecord).where(ExportRecord.version_id.in_(version_ids))))
            .scalars()
            .all()
        )
        for exp in exports:
            key = storage_key_from_uri(exp.storage_uri)
            if key:
                keys_to_delete.add(key)

        run_ids = list(
            (await db.execute(select(QualityRun.id).where(QualityRun.version_id.in_(version_ids))))
            .scalars()
            .all()
        )
        await db.execute(delete(ExportRecord).where(ExportRecord.version_id.in_(version_ids)))
        await db.execute(delete(CurationAction).where(CurationAction.version_id.in_(version_ids)))
        if run_ids:
            await db.execute(delete(RowFindingRecord).where(RowFindingRecord.run_id.in_(run_ids)))
            await db.execute(delete(RunModuleResult).where(RunModuleResult.run_id.in_(run_ids)))
            await db.execute(delete(QualityRun).where(QualityRun.id.in_(run_ids)))
        await db.execute(delete(ColumnMapping).where(ColumnMapping.version_id.in_(version_ids)))
        await db.execute(
            update(DatasetVersion)
            .where(DatasetVersion.dataset_id == ds_id)
            .values(parent_version_id=None)
        )
        await db.execute(delete(DatasetVersion).where(DatasetVersion.id.in_(version_ids)))

    await db.execute(delete(Dataset).where(Dataset.id == ds_id))

    for key in keys_to_delete:
        if storage.exists(key):
            storage.delete_key(key)

    await record_audit_log(
        db,
        action=audit_action,
        target_type="dataset",
        target_id=ds_id,
        user_id=user_id,
        workspace_id=ws_id,
        ip_address=ip_address,
        metadata={
            "dataset_id": ds_id,
            "name": ds_name,
            "versions_deleted": len(versions),
        },
    )
    if auto_commit:
        await db.commit()
    return len(versions)


async def purge_expired_datasets(
    db: AsyncSession,
    *,
    workspace_id: str,
    user_id: str | None = None,
    ip_address: str | None = None,
) -> dict[str, int]:
    """Delete datasets older than their dataset or workspace retention window."""
    ws = (
        await db.execute(select(Workspace).where(Workspace.id == workspace_id))
    ).scalar_one_or_none()
    if ws is None:
        raise AppError(
            code="WORKSPACE_NOT_FOUND",
            message="Workspace tidak ditemukan.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    raw_default = (ws.settings or {}).get("default_retention_days")
    default_days = int(raw_default) if isinstance(raw_default, int) and raw_default > 0 else None

    datasets = (
        (await db.execute(select(Dataset).where(Dataset.workspace_id == workspace_id)))
        .scalars()
        .all()
    )
    now = datetime.now(UTC)
    purged_datasets = 0
    purged_versions = 0

    for ds in datasets:
        effective_days = ds.retention_days if ds.retention_days is not None else default_days
        if effective_days is None or effective_days <= 0:
            continue
        created_at = ds.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        if created_at + timedelta(days=effective_days) <= now:
            deleted_vers = await delete_dataset_and_artifacts(
                db,
                dataset=ds,
                user_id=user_id,
                ip_address=ip_address,
                audit_action="dataset.retention_purge",
                auto_commit=False,
            )
            purged_datasets += 1
            purged_versions += deleted_vers

    await record_audit_log(
        db,
        action="workspace.retention_purge",
        target_type="workspace",
        target_id=workspace_id,
        user_id=user_id,
        workspace_id=workspace_id,
        ip_address=ip_address,
        metadata={
            "purged_datasets": purged_datasets,
            "purged_versions": purged_versions,
        },
    )
    await db.commit()
    return {"purged_datasets": purged_datasets, "purged_versions": purged_versions}
