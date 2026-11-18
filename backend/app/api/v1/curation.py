"""Explorer, Curation, Diff Preview, Policy, Export, and Audit Log endpoints (`/api/v1`)."""

from typing import Any

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.datasets import _ensure_dataset_access, _to_version_summary
from app.core.deps import get_current_user, require_workspace_permission
from app.core.errors import AppError
from app.core.security import Permission, has_permission
from app.db.models import AuditLog, ExportRecord, Membership, QualityPolicy, User
from app.db.session import get_db
from app.schemas.curation import (
    CurationBatchRequest,
    DiffPreviewRequest,
    DiffPreviewResponse,
    ExportCreateRequest,
    ExportResponse,
    PaginatedRowsResponse,
    QualityPolicyPayload,
)
from app.schemas.datasets import DatasetVersionSummary
from app.services import curation_service, dataset_service, export_service
from app.storage.s3 import get_storage_backend

router = APIRouter(tags=["curation"])


@router.get("/versions/{version_id}/rows", response_model=PaginatedRowsResponse)
async def list_rows(
    version_id: str,
    curation_status: str | None = Query(default=None),
    only_invalid: bool = Query(default=False),
    search: str | None = Query(default=None),
    reveal_pii: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PaginatedRowsResponse:
    """Server-side paginated rows for Explorer with default PII masking."""
    ver = await dataset_service.get_version_or_404(db, version_id)
    ds = await _ensure_dataset_access(db, user, ver.dataset_id, Permission.DATASET_READ)

    can_view_pii = user.is_superadmin
    if not can_view_pii:
        membership = (
            await db.execute(
                select(Membership).where(
                    Membership.user_id == user.id,
                    Membership.workspace_id == ds.workspace_id,
                )
            )
        ).scalar_one_or_none()
        can_view_pii = bool(membership and has_permission(membership.role, Permission.PII_VIEW))

    return await curation_service.list_version_rows(
        db,
        version_id=version_id,
        user_id=user.id,
        can_view_pii=can_view_pii,
        reveal_pii=reveal_pii,
        curation_status=curation_status,
        only_invalid=only_invalid,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.post("/curation/diff-preview", response_model=DiffPreviewResponse)
async def diff_preview(
    payload: DiffPreviewRequest,
    _user: User = Depends(get_current_user),
) -> DiffPreviewResponse:
    """Preview PII transformation and artifact cleaning before applying (Module B6)."""
    before, after = curation_service.preview_text_diff(
        payload.text, pii_mode=payload.pii_mode, clean_artifacts=payload.clean_artifacts
    )
    return DiffPreviewResponse(before=before, after=after, changed=(before != after))


@router.post("/versions/{version_id}/curation", response_model=DatasetVersionSummary)
async def curate_version(
    version_id: str,
    payload: CurationBatchRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DatasetVersionSummary:
    """Apply batch curation actions and create a new immutable DatasetVersion."""
    ver = await dataset_service.get_version_or_404(db, version_id)
    await _ensure_dataset_access(db, user, ver.dataset_id, Permission.DATASET_WRITE)
    new_ver = await curation_service.apply_curation_batch(
        db, version_id=version_id, actions=payload.actions, user_id=user.id
    )
    return _to_version_summary(new_ver)


@router.get("/workspaces/{workspace_id}/policy", response_model=QualityPolicyPayload)
async def get_policy(
    workspace_id: str,
    _user: User = Depends(require_workspace_permission(Permission.DATASET_READ)),
    db: AsyncSession = Depends(get_db),
) -> QualityPolicyPayload:
    """Get workspace quality gate policy."""
    pol = (
        await db.execute(select(QualityPolicy).where(QualityPolicy.workspace_id == workspace_id))
    ).scalar_one_or_none()
    if pol is None:
        return QualityPolicyPayload()
    return QualityPolicyPayload(
        min_dqi=float(pol.rules_json.get("min_dqi", 60.0)),
        block_if_critical_pii=bool(pol.rules_json.get("block_if_critical_pii", True)),
        is_active=pol.is_active,
    )


@router.put("/workspaces/{workspace_id}/policy", response_model=QualityPolicyPayload)
async def put_policy(
    workspace_id: str,
    payload: QualityPolicyPayload,
    _user: User = Depends(require_workspace_permission(Permission.POLICY_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> QualityPolicyPayload:
    """Update workspace quality gate policy."""
    pol = (
        await db.execute(select(QualityPolicy).where(QualityPolicy.workspace_id == workspace_id))
    ).scalar_one_or_none()
    rules = {"min_dqi": payload.min_dqi, "block_if_critical_pii": payload.block_if_critical_pii}
    if pol is None:
        pol = QualityPolicy(
            workspace_id=workspace_id, rules_json=rules, is_active=payload.is_active
        )
        db.add(pol)
    else:
        pol.rules_json = rules
        pol.is_active = payload.is_active
    await db.commit()
    return payload


@router.post(
    "/versions/{version_id}/exports",
    response_model=ExportResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_version_export(
    version_id: str,
    payload: ExportCreateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ExportResponse:
    """Export a dataset version (JSONL/CSV/Parquet) after Quality Gate evaluation."""
    ver = await dataset_service.get_version_or_404(db, version_id)
    await _ensure_dataset_access(db, user, ver.dataset_id, Permission.EXPORT_CREATE)
    rec = await export_service.create_export(
        db,
        version_id=version_id,
        export_format=payload.export_format,
        exclude_rejected=payload.exclude_rejected,
        only_approved=payload.only_approved,
        enforce_quality_gate=payload.enforce_quality_gate,
        user_id=user.id,
    )
    return ExportResponse(
        id=rec.id,
        version_id=rec.version_id,
        export_format=rec.export_format,
        status=rec.status,
        manifest=rec.manifest_json,
        created_at=rec.created_at,
    )


@router.get("/exports/{export_id}/download")
async def download_export(
    export_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Download exported dataset bytes."""
    rec = (
        await db.execute(select(ExportRecord).where(ExportRecord.id == export_id))
    ).scalar_one_or_none()
    if rec is None:
        raise AppError("EXPORT_NOT_FOUND", "Ekspor tidak ditemukan.", status.HTTP_404_NOT_FOUND)
    ver = await dataset_service.get_version_or_404(db, rec.version_id)
    await _ensure_dataset_access(db, user, ver.dataset_id, Permission.DATASET_READ)

    storage = get_storage_backend()
    key = rec.storage_uri.replace("local://", "").split("/", 3)[-1]
    if rec.storage_uri.startswith("local://"):
        key = rec.storage_uri.removeprefix("local://")
    data = storage.get_bytes(key)
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="qualitext_export.{rec.export_format}"'
        },
    )


@router.get("/versions/{version_id}/compare/{other_version_id}")
async def compare_versions(
    version_id: str,
    other_version_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Compare two immutable dataset versions (Module F4)."""
    import io

    import polars as pl

    v1 = await dataset_service.get_version_or_404(db, version_id)
    v2 = await dataset_service.get_version_or_404(db, other_version_id)
    await _ensure_dataset_access(db, user, v1.dataset_id, Permission.DATASET_READ)

    df1 = pl.read_parquet(
        io.BytesIO(dataset_service.load_version_parquet_bytes(v1.dataset_id, v1.version_number))
    )
    df2 = pl.read_parquet(
        io.BytesIO(dataset_service.load_version_parquet_bytes(v2.dataset_id, v2.version_number))
    )

    map1 = {str(r["row_id"]): r for r in df1.iter_rows(named=True)}
    changed_rows: list[dict[str, Any]] = []
    for r2 in df2.iter_rows(named=True):
        rid = str(r2["row_id"])
        r1 = map1.get(rid)
        if r1 is None or (
            r1.get("curation_status") != r2.get("curation_status")
            or r1.get("text") != r2.get("text")
        ):
            changed_rows.append(
                {
                    "row_id": rid,
                    "before_status": r1.get("curation_status") if r1 else None,
                    "after_status": r2.get("curation_status"),
                }
            )

    return {
        "base_version": {"id": v1.id, "version_number": v1.version_number, "rows": df1.height},
        "target_version": {"id": v2.id, "version_number": v2.version_number, "rows": df2.height},
        "changed_count": len(changed_rows),
        "changes": changed_rows[:200],
    }


@router.get("/workspaces/{workspace_id}/audit-logs")
async def list_audit_logs(
    workspace_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    _user: User = Depends(require_workspace_permission(Permission.AUDIT_READ)),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """List immutable audit log entries for a workspace."""
    logs = (
        (
            await db.execute(
                select(AuditLog)
                .where(AuditLog.workspace_id == workspace_id)
                .order_by(AuditLog.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": log.id,
            "created_at": log.created_at.isoformat(),
            "user_id": log.user_id,
            "action": log.action,
            "target_type": log.target_type,
            "target_id": log.target_id,
            "metadata": log.metadata_json,
        }
        for log in logs
    ]
