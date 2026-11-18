"""Dataset, upload, and column-mapping endpoints (`/api/v1`)."""

from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, require_workspace_permission
from app.core.errors import AppError
from app.core.security import Permission
from app.db.models import ColumnMapping, Dataset, DatasetVersion, Membership, User
from app.db.session import get_db
from app.schemas.datasets import (
    ColumnMappingPayload,
    ColumnMappingResponse,
    DatasetCreateRequest,
    DatasetDetailResponse,
    DatasetResponse,
    DatasetVersionSummary,
    UploadInitRequest,
    UploadInitResponse,
)
from app.services import dataset_service

router = APIRouter(tags=["datasets"])

# 200 MB for single-request; chunked supports up to 5 GB
MAX_DIRECT_UPLOAD_BYTES = 200 * 1024 * 1024


async def _ensure_dataset_access(
    db: AsyncSession, user: User, dataset_id: str, permission: Permission
) -> Dataset:
    ds = await dataset_service.get_dataset_or_404(db, dataset_id)
    if user.is_superadmin:
        return ds
    membership = (
        await db.execute(
            select(Membership).where(
                Membership.user_id == user.id,
                Membership.workspace_id == ds.workspace_id,
            )
        )
    ).scalar_one_or_none()
    from app.core.security import has_permission

    if membership is None or not has_permission(membership.role, permission):
        raise AppError(
            code="FORBIDDEN",
            message="Anda tidak memiliki izin pada dataset ini.",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    return ds


def _to_version_summary(v: DatasetVersion) -> DatasetVersionSummary:
    return DatasetVersionSummary(
        id=v.id,
        dataset_id=v.dataset_id,
        version_number=v.version_number,
        parent_version_id=v.parent_version_id,
        source_format=v.source_format,
        row_count=v.row_count,
        invalid_row_count=v.invalid_row_count,
        checksum=v.checksum,
        status=v.status,
        validation_summary=v.validation_summary,
        created_at=v.created_at,
    )


@router.get("/workspaces/{workspace_id}/datasets", response_model=list[DatasetResponse])
async def list_datasets(
    workspace_id: str,
    _user: User = Depends(require_workspace_permission(Permission.DATASET_READ)),
    db: AsyncSession = Depends(get_db),
) -> list[DatasetResponse]:
    """List datasets in a workspace along with their latest version."""
    datasets = (
        (
            await db.execute(
                select(Dataset)
                .where(Dataset.workspace_id == workspace_id)
                .order_by(Dataset.created_at.desc())
            )
        )
        .scalars()
        .all()
    )

    out: list[DatasetResponse] = []
    for ds in datasets:
        latest = (
            await db.execute(
                select(DatasetVersion)
                .where(DatasetVersion.dataset_id == ds.id)
                .order_by(DatasetVersion.version_number.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        out.append(
            DatasetResponse(
                id=ds.id,
                workspace_id=ds.workspace_id,
                name=ds.name,
                description=ds.description,
                dataset_type=ds.dataset_type,
                created_at=ds.created_at,
                latest_version=_to_version_summary(latest) if latest else None,
            )
        )
    return out


@router.post(
    "/workspaces/{workspace_id}/datasets",
    response_model=DatasetResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_dataset(
    workspace_id: str,
    payload: DatasetCreateRequest,
    user: User = Depends(require_workspace_permission(Permission.DATASET_WRITE)),
    db: AsyncSession = Depends(get_db),
) -> DatasetResponse:
    """Create a new dataset in the given workspace."""
    ds = await dataset_service.create_dataset(
        db,
        workspace_id=workspace_id,
        name=payload.name,
        description=payload.description,
        dataset_type=payload.dataset_type,
        user_id=user.id,
    )
    return DatasetResponse(
        id=ds.id,
        workspace_id=ds.workspace_id,
        name=ds.name,
        description=ds.description,
        dataset_type=ds.dataset_type,
        created_at=ds.created_at,
        latest_version=None,
    )


@router.get("/datasets/{dataset_id}", response_model=DatasetDetailResponse)
async def get_dataset_detail(
    dataset_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DatasetDetailResponse:
    """Get dataset metadata and all immutable versions."""
    ds = await _ensure_dataset_access(db, user, dataset_id, Permission.DATASET_READ)
    versions = (
        (
            await db.execute(
                select(DatasetVersion)
                .where(DatasetVersion.dataset_id == ds.id)
                .order_by(DatasetVersion.version_number.desc())
            )
        )
        .scalars()
        .all()
    )
    summaries = [_to_version_summary(v) for v in versions]
    return DatasetDetailResponse(
        id=ds.id,
        workspace_id=ds.workspace_id,
        name=ds.name,
        description=ds.description,
        dataset_type=ds.dataset_type,
        created_at=ds.created_at,
        latest_version=summaries[0] if summaries else None,
        versions=summaries,
    )


@router.post("/datasets/{dataset_id}/versions/uploads", response_model=UploadInitResponse)
async def init_upload(
    dataset_id: str,
    payload: UploadInitRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UploadInitResponse:
    """Initialize a chunked upload for a new dataset version."""
    ds = await _ensure_dataset_access(db, user, dataset_id, Permission.DATASET_WRITE)
    ver = await dataset_service.init_version_upload(
        db, dataset=ds, source_format=payload.source_format
    )
    return UploadInitResponse(
        version_id=ver.id, version_number=ver.version_number, status=ver.status
    )


@router.put("/datasets/{dataset_id}/versions/{version_id}/chunks/{chunk_index}")
async def upload_chunk(
    dataset_id: str,
    version_id: str,
    chunk_index: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Upload a raw binary chunk for an initialized dataset version."""
    await _ensure_dataset_access(db, user, dataset_id, Permission.DATASET_WRITE)
    ver = await dataset_service.get_version_or_404(db, version_id)
    if ver.dataset_id != dataset_id:
        raise AppError("VERSION_MISMATCH", "Versi tidak cocok.", status.HTTP_400_BAD_REQUEST)
    body = await request.body()
    dataset_service.store_upload_chunk(ver.id, chunk_index, body)
    return {"chunk_index": chunk_index, "bytes": len(body)}


@router.post(
    "/datasets/{dataset_id}/versions/{version_id}/complete",
    response_model=DatasetVersionSummary,
)
async def complete_upload(
    dataset_id: str,
    version_id: str,
    total_chunks: int = 1,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DatasetVersionSummary:
    """Complete a chunked upload, validate schema, and normalize to Parquet."""
    ds = await _ensure_dataset_access(db, user, dataset_id, Permission.DATASET_WRITE)
    ver = await dataset_service.get_version_or_404(db, version_id)
    updated = await dataset_service.complete_version_upload(
        db, dataset=ds, version=ver, total_chunks=total_chunks, user_id=user.id
    )
    return _to_version_summary(updated)


@router.post(
    "/datasets/{dataset_id}/versions/upload-direct",
    response_model=DatasetVersionSummary,
    status_code=status.HTTP_201_CREATED,
)
async def upload_direct(
    dataset_id: str,
    file: UploadFile = File(...),
    source_format: Literal["jsonl", "csv", "parquet", "txt"] = Form("jsonl"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DatasetVersionSummary:
    """Direct single-file upload convenience endpoint for files <= 200 MB."""
    ds = await _ensure_dataset_access(db, user, dataset_id, Permission.DATASET_WRITE)
    raw_bytes = await file.read()
    if len(raw_bytes) > MAX_DIRECT_UPLOAD_BYTES:
        raise AppError(
            code="FILE_TOO_LARGE",
            message="Ukuran berkas melebihi 200 MB. Gunakan unggah chunked.",
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        )
    ver = await dataset_service.direct_upload_version(
        db, dataset=ds, source_format=source_format, raw_bytes=raw_bytes, user_id=user.id
    )
    return _to_version_summary(ver)


@router.get("/versions/{version_id}/mapping", response_model=ColumnMappingResponse)
async def get_mapping(
    version_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ColumnMappingResponse:
    """Retrieve column mapping and detected columns for a dataset version."""
    ver = await dataset_service.get_version_or_404(db, version_id)
    await _ensure_dataset_access(db, user, ver.dataset_id, Permission.DATASET_READ)
    cm = (
        await db.execute(select(ColumnMapping).where(ColumnMapping.version_id == version_id))
    ).scalar_one_or_none()
    detected = ver.validation_summary.get("detected_columns", [])
    return ColumnMappingResponse(
        version_id=version_id,
        mapping=cm.mapping_json if cm else {},
        detected_columns=list(detected) if isinstance(detected, list) else [],
    )


@router.put("/versions/{version_id}/mapping", response_model=DatasetVersionSummary)
async def put_mapping(
    version_id: str,
    payload: ColumnMappingPayload,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DatasetVersionSummary:
    """Update column mapping and re-validate the dataset version."""
    ver = await dataset_service.get_version_or_404(db, version_id)
    await _ensure_dataset_access(db, user, ver.dataset_id, Permission.DATASET_WRITE)
    updated = await dataset_service.update_column_mapping(db, version=ver, mapping=payload.mapping)
    return _to_version_summary(updated)


@router.get("/versions/{version_id}/validation-errors")
async def get_validation_errors(
    version_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return syntax/schema validation errors for a dataset version."""
    ver = await dataset_service.get_version_or_404(db, version_id)
    await _ensure_dataset_access(db, user, ver.dataset_id, Permission.DATASET_READ)
    return {
        "version_id": ver.id,
        "invalid_row_count": ver.invalid_row_count,
        "errors": dataset_service.get_validation_summary_errors(ver),
    }
