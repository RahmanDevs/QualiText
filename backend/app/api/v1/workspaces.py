"""Workspace endpoints (`/api/v1/workspaces`)."""

from typing import Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, require_workspace_permission
from app.core.errors import AppError
from app.core.security import Permission
from app.db.models import (
    Dataset,
    DatasetVersion,
    Membership,
    QualityRun,
    RowFindingRecord,
    User,
    Workspace,
)
from app.db.session import get_db
from app.schemas.auth import WorkspaceMembershipResponse
from app.services import dataset_service
from app.services.audit_service import extract_client_ip, record_audit_log

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.get("", response_model=list[WorkspaceMembershipResponse])
async def list_my_workspaces(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[WorkspaceMembershipResponse]:
    """List workspaces accessible by the authenticated user."""
    stmt = (
        select(Workspace, Membership.role)
        .join(Membership, Membership.workspace_id == Workspace.id)
        .where(Membership.user_id == user.id)
        .order_by(Workspace.name)
    )
    rows = (await db.execute(stmt)).all()
    return [
        WorkspaceMembershipResponse(id=ws.id, name=ws.name, slug=ws.slug, role=role)
        for ws, role in rows
    ]


@router.get("/{workspace_id}/ping")
async def workspace_rbac_check(
    workspace_id: str,
    _user: User = Depends(require_workspace_permission(Permission.DATASET_READ)),
) -> dict[str, str]:
    """Verify workspace RBAC permission enforcement."""
    return {"status": "ok", "workspace_id": workspace_id}


@router.get("/{workspace_id}/overview")
async def workspace_overview(
    workspace_id: str,
    _user: User = Depends(require_workspace_permission(Permission.DATASET_READ)),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Aggregate live workspace KPIs, DQI trend, open critical PII, and attention list."""
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
    if not datasets:
        return {
            "workspace_id": workspace_id,
            "total_datasets": 0,
            "avg_dqi": None,
            "open_critical_pii": 0,
            "active_runs": 0,
            "datasets_needing_attention": [],
            "dqi_trend": [],
        }

    ds_by_id = {ds.id: ds for ds in datasets}
    ds_ids = list(ds_by_id.keys())

    latest_subq = (
        select(
            DatasetVersion.dataset_id,
            func.max(DatasetVersion.version_number).label("max_ver"),
        )
        .where(
            DatasetVersion.dataset_id.in_(ds_ids),
            DatasetVersion.status.in_(["validated", "curated"]),
        )
        .group_by(DatasetVersion.dataset_id)
        .subquery()
    )
    latest_versions = (
        (
            await db.execute(
                select(DatasetVersion).join(
                    latest_subq,
                    (DatasetVersion.dataset_id == latest_subq.c.dataset_id)
                    & (DatasetVersion.version_number == latest_subq.c.max_ver),
                )
            )
        )
        .scalars()
        .all()
    )
    latest_ver_by_ds = {ver.dataset_id: ver for ver in latest_versions}

    active_runs = (
        await db.execute(
            select(func.count(QualityRun.id))
            .join(DatasetVersion, QualityRun.version_id == DatasetVersion.id)
            .where(
                DatasetVersion.dataset_id.in_(ds_ids),
                QualityRun.status.in_(["queued", "running"]),
            )
        )
    ).scalar_one()

    completed_stmt = (
        select(QualityRun, DatasetVersion.dataset_id, DatasetVersion.version_number)
        .join(DatasetVersion, QualityRun.version_id == DatasetVersion.id)
        .where(
            DatasetVersion.dataset_id.in_(ds_ids),
            QualityRun.status == "completed",
        )
        .order_by(QualityRun.created_at.asc())
    )
    completed_rows = (await db.execute(completed_stmt)).all()

    latest_run_by_ver: dict[str, QualityRun] = {}
    dqi_trend: list[dict[str, Any]] = []
    for run, dsid, ver_num in completed_rows:
        latest_run_by_ver[run.version_id] = run
        ds = ds_by_id.get(dsid)
        dqi_trend.append(
            {
                "run_id": run.id,
                "dataset_id": dsid,
                "dataset_name": ds.name if ds else dsid,
                "version_id": run.version_id,
                "version_number": ver_num,
                "dqi_total": run.dqi_total,
                "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            }
        )

    latest_run_ids: list[str] = []
    for ver in latest_versions:
        l_run = latest_run_by_ver.get(ver.id)
        if l_run is not None:
            latest_run_ids.append(l_run.id)

    pii_counts_by_run: dict[str, int] = {}
    if latest_run_ids:
        pii_stmt = (
            select(RowFindingRecord.run_id, func.count(RowFindingRecord.id))
            .where(
                RowFindingRecord.run_id.in_(latest_run_ids),
                RowFindingRecord.severity == "critical",
                RowFindingRecord.module == "pii",
            )
            .group_by(RowFindingRecord.run_id)
        )
        for rid, cnt in (await db.execute(pii_stmt)).all():
            pii_counts_by_run[str(rid)] = int(cnt)

    dqi_values: list[float] = []
    total_open_pii = 0
    attention: list[dict[str, Any]] = []

    for ds in datasets:
        ver = latest_ver_by_ds.get(ds.id)
        if ver is None:
            attention.append(
                {
                    "dataset_id": ds.id,
                    "dataset_name": ds.name,
                    "version_id": None,
                    "dqi_total": None,
                    "critical_pii": 0,
                    "invalid_row_count": 0,
                    "reason": "no_upload",
                }
            )
            continue

        run = latest_run_by_ver.get(ver.id)
        dqi_val = run.dqi_total if run is not None else None
        if dqi_val is not None:
            dqi_values.append(float(dqi_val))
        pii_cnt = pii_counts_by_run.get(run.id, 0) if run is not None else 0
        total_open_pii += pii_cnt

        reason: str | None = None
        if pii_cnt > 0:
            reason = "critical_pii"
        elif dqi_val is None:
            reason = "unassessed"
        elif dqi_val < 75.0:
            reason = "low_dqi"
        elif ver.invalid_row_count > 0:
            reason = "invalid_rows"

        if reason is not None:
            attention.append(
                {
                    "dataset_id": ds.id,
                    "dataset_name": ds.name,
                    "version_id": ver.id,
                    "version_number": ver.version_number,
                    "run_id": run.id if run else None,
                    "dqi_total": dqi_val,
                    "critical_pii": pii_cnt,
                    "invalid_row_count": ver.invalid_row_count,
                    "reason": reason,
                }
            )

    avg_dqi = round(sum(dqi_values) / len(dqi_values), 2) if dqi_values else None
    return {
        "workspace_id": workspace_id,
        "total_datasets": len(datasets),
        "avg_dqi": avg_dqi,
        "open_critical_pii": total_open_pii,
        "active_runs": int(active_runs),
        "datasets_needing_attention": attention,
        "dqi_trend": dqi_trend,
    }


class RetentionPolicyResponse(BaseModel):
    """Workspace UU PDP data retention policy configuration."""

    workspace_id: str
    default_retention_days: int | None = None


class RetentionPolicyUpdateRequest(BaseModel):
    """Update default retention days for a workspace (None clears auto-expiry)."""

    default_retention_days: int | None = Field(default=None, ge=1, le=3650)


class RetentionPurgeResponse(BaseModel):
    """Summary of datasets and versions purged under UU PDP retention enforcement."""

    workspace_id: str
    purged_datasets: int
    purged_versions: int


@router.get("/{workspace_id}/retention", response_model=RetentionPolicyResponse)
async def get_workspace_retention(
    workspace_id: str,
    _user: User = Depends(require_workspace_permission(Permission.DATASET_READ)),
    db: AsyncSession = Depends(get_db),
) -> RetentionPolicyResponse:
    """Get workspace default retention window in days."""
    ws = (
        await db.execute(select(Workspace).where(Workspace.id == workspace_id))
    ).scalar_one_or_none()
    if ws is None:
        raise AppError(
            code="WORKSPACE_NOT_FOUND",
            message="Workspace tidak ditemukan.",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    raw = (ws.settings or {}).get("default_retention_days")
    days = int(raw) if isinstance(raw, int) and raw > 0 else None
    return RetentionPolicyResponse(workspace_id=workspace_id, default_retention_days=days)


@router.put("/{workspace_id}/retention", response_model=RetentionPolicyResponse)
async def update_workspace_retention(
    workspace_id: str,
    payload: RetentionPolicyUpdateRequest,
    request: Request,
    user: User = Depends(require_workspace_permission(Permission.POLICY_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> RetentionPolicyResponse:
    """Configure workspace default retention window in days."""
    ws = (
        await db.execute(select(Workspace).where(Workspace.id == workspace_id))
    ).scalar_one_or_none()
    if ws is None:
        raise AppError(
            code="WORKSPACE_NOT_FOUND",
            message="Workspace tidak ditemukan.",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    new_settings = dict(ws.settings or {})
    if payload.default_retention_days is None:
        new_settings.pop("default_retention_days", None)
    else:
        new_settings["default_retention_days"] = payload.default_retention_days
    ws.settings = new_settings

    await record_audit_log(
        db,
        action="workspace.retention_update",
        target_type="workspace",
        target_id=workspace_id,
        user_id=user.id,
        workspace_id=workspace_id,
        ip_address=extract_client_ip(request),
        metadata={"default_retention_days": payload.default_retention_days},
    )
    await db.commit()
    return RetentionPolicyResponse(
        workspace_id=workspace_id,
        default_retention_days=payload.default_retention_days,
    )


@router.post("/{workspace_id}/retention/purge", response_model=RetentionPurgeResponse)
async def trigger_retention_purge(
    workspace_id: str,
    request: Request,
    user: User = Depends(require_workspace_permission(Permission.POLICY_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> RetentionPurgeResponse:
    """Purge datasets exceeding their dataset or workspace retention window."""
    counts = await dataset_service.purge_expired_datasets(
        db,
        workspace_id=workspace_id,
        user_id=user.id,
        ip_address=extract_client_ip(request),
    )
    return RetentionPurgeResponse(
        workspace_id=workspace_id,
        purged_datasets=counts["purged_datasets"],
        purged_versions=counts["purged_versions"],
    )
