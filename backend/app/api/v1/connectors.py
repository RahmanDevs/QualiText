"""Admin-gated external/internal data source connector import endpoint (Module A6)."""

import asyncio
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.datasets import _ensure_dataset_access, _to_version_summary
from app.core.deps import get_current_user
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.security import Permission
from app.db.models import User
from app.db.session import get_db
from app.modules.ingestion.connectors import CONNECTOR_REGISTRY, ConnectorConfig
from app.schemas.datasets import DatasetVersionSummary
from app.services import dataset_service
from app.services.audit_service import extract_client_ip, record_audit_log

router = APIRouter(tags=["connectors"])
logger = get_logger(__name__)


class ConnectorImportRequest(BaseModel):
    """Payload for importing a new dataset version via an authorized connector."""

    connector_type: Literal["s3", "database", "sharepoint"]
    uri: str = Field(min_length=1, max_length=1024)
    credentials_ref: str = Field(default="", max_length=128)
    source_format: Literal["jsonl", "csv", "parquet", "txt", "pdf", "md"] = "jsonl"
    options: dict[str, Any] = Field(default_factory=dict)


def _sanitize_uri_for_audit(raw_uri: str) -> str:
    """Strip query params, fragments, and userinfo from connector URIs before auditing."""
    parsed = urlparse(raw_uri)
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{host}{port}{parsed.path}"


@router.post(
    "/datasets/{dataset_id}/versions/import-connector",
    response_model=DatasetVersionSummary,
    status_code=status.HTTP_201_CREATED,
)
async def import_from_connector(
    dataset_id: str,
    payload: ConnectorImportRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DatasetVersionSummary:
    """Pull raw bytes from an external/internal source and ingest as an immutable version."""
    await _ensure_dataset_access(db, user, dataset_id, Permission.POLICY_MANAGE)
    ds = await _ensure_dataset_access(db, user, dataset_id, Permission.DATASET_WRITE)

    connector = CONNECTOR_REGISTRY.get(payload.connector_type)
    if connector is None:
        raise AppError(
            code="UNSUPPORTED_CONNECTOR",
            message=f"Tipe konektor '{payload.connector_type}' tidak didukung.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    config = ConnectorConfig(
        connector_type=payload.connector_type,
        uri=payload.uri,
        credentials_ref=payload.credentials_ref,
        options=payload.options,
    )
    if not connector.validate_config(config):
        raise AppError(
            code="INVALID_CONNECTOR_CONFIG",
            message="Konfigurasi konektor tidak valid atau tidak memenuhi standar keamanan.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        raw_bytes = await asyncio.to_thread(connector.fetch_bytes, config)
    except (ValueError, OSError, httpx.HTTPError) as exc:
        logger.warning(
            "connector_import_failed",
            connector_type=payload.connector_type,
            dataset_id=ds.id,
            error_type=type(exc).__name__,
        )
        raise AppError(
            code="CONNECTOR_FETCH_FAILED",
            message=f"Gagal mengambil data dari konektor: {exc}",
            status_code=status.HTTP_400_BAD_REQUEST,
        ) from exc

    effective_format = "jsonl" if payload.connector_type == "database" else payload.source_format
    try:
        ver = await dataset_service.direct_upload_version(
            db,
            dataset=ds,
            source_format=effective_format,
            raw_bytes=raw_bytes,
            user_id=user.id,
            ip_address=extract_client_ip(request),
        )
    except ValueError as exc:
        raise AppError(
            code="VALIDATION_FAILED",
            message=f"Validasi data konektor gagal: {exc}",
            status_code=status.HTTP_400_BAD_REQUEST,
        ) from exc

    await record_audit_log(
        db,
        action="dataset.connector_import",
        target_type="dataset_version",
        target_id=ver.id,
        user_id=user.id,
        workspace_id=ds.workspace_id,
        ip_address=extract_client_ip(request),
        metadata={
            "dataset_id": ds.id,
            "connector_type": payload.connector_type,
            "source_uri": _sanitize_uri_for_audit(payload.uri),
            "version_number": ver.version_number,
            "row_count": ver.row_count,
        },
    )
    await db.commit()
    await db.refresh(ver)
    return _to_version_summary(ver)
