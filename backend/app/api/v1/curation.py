"""Explorer, Curation, Diff Preview, Policy, Export, and Audit Log endpoints (`/api/v1`)."""

import csv
import io
import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.datasets import _ensure_dataset_access, _to_version_summary
from app.core.deps import (
    check_api_token_scope,
    get_current_user,
    has_api_token_scope,
    require_workspace_permission,
)
from app.core.errors import AppError
from app.core.security import Permission, has_permission
from app.db.models import AuditLog, ExportRecord, Membership, QualityPolicy, User
from app.db.session import get_db
from app.schemas.curation import (
    AiRepairPreviewRequest,
    AiRepairPreviewResponse,
    CurationBatchRequest,
    DiffPreviewRequest,
    DiffPreviewResponse,
    ExportCreateRequest,
    ExportResponse,
    PaginatedRowsResponse,
    QualityPolicyPayload,
)
from app.schemas.datasets import DatasetVersionSummary
from app.services import curation_service, dataset_service, export_service, repair_service
from app.services.audit_service import extract_client_ip, record_audit_log
from app.storage.s3 import get_storage_backend

router = APIRouter(tags=["curation"])


def _safe_csv_cell(value: object) -> str:
    text = "" if value is None else str(value)
    if text and text[0] in ("=", "+", "-", "@"):
        return f"'{text}"
    return text


def _serialize_audit_log(log: AuditLog) -> dict[str, Any]:
    return {
        "id": log.id,
        "created_at": log.created_at.isoformat(),
        "user_id": log.user_id,
        "action": log.action,
        "target_type": log.target_type,
        "target_id": log.target_id,
        "ip_address": log.ip_address,
        "metadata": log.metadata_json,
    }


async def _query_workspace_audit_logs(
    db: AsyncSession,
    *,
    workspace_id: str,
    action: str | None,
    target_type: str | None,
    limit: int,
) -> list[AuditLog]:
    stmt = select(AuditLog).where(AuditLog.workspace_id == workspace_id)
    if action:
        stmt = stmt.where(AuditLog.action.ilike(f"%{action.strip()}%"))
    if target_type:
        stmt = stmt.where(AuditLog.target_type == target_type.strip())
    stmt = stmt.order_by(AuditLog.created_at.desc()).limit(limit)
    return list((await db.execute(stmt)).scalars().all())


@router.get("/versions/{version_id}/rows", response_model=PaginatedRowsResponse)
async def list_rows(
    version_id: str,
    request: Request,
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
    can_view_pii = can_view_pii and has_api_token_scope(Permission.PII_VIEW)

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
        ip_address=extract_client_ip(request),
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


@router.post("/curation/ai-repair-preview", response_model=AiRepairPreviewResponse)
async def ai_repair_preview(
    payload: AiRepairPreviewRequest,
    _user: User = Depends(get_current_user),
) -> AiRepairPreviewResponse:
    """Preview AI-assisted or deterministic PDF parsing repair before applying."""
    after, mode = await repair_service.repair_parsed_text_async(
        payload.text, instruction=payload.instruction
    )
    return AiRepairPreviewResponse(
        before=payload.text,
        after=after,
        changed=(payload.text != after),
        mode=mode,  # type: ignore[arg-type]
    )


@router.post("/versions/{version_id}/curation", response_model=DatasetVersionSummary)
async def curate_version(
    version_id: str,
    payload: CurationBatchRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DatasetVersionSummary:
    """Apply batch curation actions and create a new immutable DatasetVersion."""
    ver = await dataset_service.get_version_or_404(db, version_id)
    await _ensure_dataset_access(db, user, ver.dataset_id, Permission.DATASET_WRITE)
    new_ver = await curation_service.apply_curation_batch(
        db,
        version_id=version_id,
        actions=payload.actions,
        user_id=user.id,
        ip_address=extract_client_ip(request),
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
    raw_dim_thresholds = pol.rules_json.get("dimension_thresholds", {})
    raw_dim_weights = pol.rules_json.get("dimension_weights", {})
    return QualityPolicyPayload(
        min_dqi=float(pol.rules_json.get("min_dqi", 60.0)),
        block_if_critical_pii=bool(pol.rules_json.get("block_if_critical_pii", True)),
        is_active=pol.is_active,
        dimension_thresholds={
            str(k): float(v) for k, v in raw_dim_thresholds.items() if isinstance(v, (int, float))
        }
        if isinstance(raw_dim_thresholds, dict)
        else {},
        critical_cap=float(pol.rules_json.get("critical_cap", 60.0)),
        dimension_weights={
            str(k): float(v) for k, v in raw_dim_weights.items() if isinstance(v, (int, float))
        }
        if isinstance(raw_dim_weights, dict)
        else {},
    )


@router.put("/workspaces/{workspace_id}/policy", response_model=QualityPolicyPayload)
async def put_policy(
    workspace_id: str,
    payload: QualityPolicyPayload,
    request: Request,
    user: User = Depends(require_workspace_permission(Permission.POLICY_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> QualityPolicyPayload:
    """Update workspace quality gate policy and record immutable audit log."""
    pol = (
        await db.execute(select(QualityPolicy).where(QualityPolicy.workspace_id == workspace_id))
    ).scalar_one_or_none()
    rules: dict[str, Any] = {
        "min_dqi": payload.min_dqi,
        "block_if_critical_pii": payload.block_if_critical_pii,
        "dimension_thresholds": payload.dimension_thresholds,
        "critical_cap": payload.critical_cap,
        "dimension_weights": payload.dimension_weights,
    }
    if pol is None:
        pol = QualityPolicy(
            workspace_id=workspace_id, rules_json=rules, is_active=payload.is_active
        )
        db.add(pol)
        await db.flush()
    else:
        pol.rules_json = rules
        pol.is_active = payload.is_active
        await db.flush()

    await record_audit_log(
        db,
        action="policy.update",
        target_type="quality_policy",
        target_id=pol.id,
        user_id=user.id,
        workspace_id=workspace_id,
        ip_address=extract_client_ip(request),
        metadata={
            "min_dqi": payload.min_dqi,
            "block_if_critical_pii": payload.block_if_critical_pii,
            "is_active": payload.is_active,
            "critical_cap": payload.critical_cap,
            "dimension_thresholds": payload.dimension_thresholds,
        },
    )
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
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ExportResponse:
    """Export a dataset version (JSONL/CSV/Parquet/HuggingFace) after Quality Gate evaluation."""
    ver = await dataset_service.get_version_or_404(db, version_id)
    await _ensure_dataset_access(db, user, ver.dataset_id, Permission.EXPORT_CREATE)
    if not payload.enforce_quality_gate:
        await _ensure_dataset_access(db, user, ver.dataset_id, Permission.POLICY_MANAGE)
    rec = await export_service.create_export(
        db,
        version_id=version_id,
        export_format=payload.export_format,
        exclude_rejected=payload.exclude_rejected,
        only_approved=payload.only_approved,
        enforce_quality_gate=payload.enforce_quality_gate,
        user_id=user.id,
        ip_address=extract_client_ip(request),
    )
    return ExportResponse(
        id=rec.id,
        version_id=rec.version_id,
        export_format=rec.export_format,
        status=rec.status,
        manifest=rec.manifest_json,
        created_at=rec.created_at,
    )


@router.get("/exports/{export_id}", response_model=ExportResponse)
async def get_export(
    export_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ExportResponse:
    """Retrieve export metadata and manifest."""
    rec = (
        await db.execute(select(ExportRecord).where(ExportRecord.id == export_id))
    ).scalar_one_or_none()
    if rec is None:
        raise AppError("EXPORT_NOT_FOUND", "Ekspor tidak ditemukan.", status.HTTP_404_NOT_FOUND)
    ver = await dataset_service.get_version_or_404(db, rec.version_id)
    await _ensure_dataset_access(db, user, ver.dataset_id, Permission.DATASET_READ)
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
    key = dataset_service.storage_key_from_uri(rec.storage_uri)
    if not key or not storage.exists(key):
        raise AppError(
            code="EXPORT_FILE_NOT_FOUND",
            message="Berkas ekspor tidak ditemukan di penyimpanan.",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    data = storage.get_bytes(key)
    if rec.export_format == "huggingface":
        ext = "hf.zip"
        media_type = "application/zip"
    elif rec.export_format in {"rag_vector", "sft_chat"}:
        ext = f"{rec.export_format}.jsonl"
        media_type = "application/jsonl"
    else:
        ext = rec.export_format
        media_type = "application/octet-stream"
    return Response(
        content=data,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="qualitext_export.{ext}"'},
    )


@router.get("/versions/{version_id}/compare/{other_version_id}")
async def compare_versions(
    version_id: str,
    other_version_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Compare two immutable dataset versions (Module F4)."""
    import polars as pl

    v1 = await dataset_service.get_version_or_404(db, version_id)
    v2 = await dataset_service.get_version_or_404(db, other_version_id)
    await _ensure_dataset_access(db, user, v1.dataset_id, Permission.DATASET_READ)
    await _ensure_dataset_access(db, user, v2.dataset_id, Permission.DATASET_READ)
    if v1.dataset_id != v2.dataset_id:
        raise AppError(
            "VERSION_MISMATCH",
            "Kedua versi yang dibandingkan harus berasal dari dataset yang sama.",
            status.HTTP_400_BAD_REQUEST,
        )

    df1 = pl.read_parquet(
        io.BytesIO(dataset_service.load_version_parquet_bytes(v1.dataset_id, v1.version_number))
    )
    df2 = pl.read_parquet(
        io.BytesIO(dataset_service.load_version_parquet_bytes(v2.dataset_id, v2.version_number))
    )

    map1 = {str(r["row_id"]): r for r in df1.iter_rows(named=True)}
    map2: dict[str, dict[str, Any]] = {}
    changed_rows: list[dict[str, Any]] = []
    for r2 in df2.iter_rows(named=True):
        rid = str(r2["row_id"])
        map2[rid] = r2
        r1 = map1.get(rid)
        if r1 is None or (
            r1.get("curation_status") != r2.get("curation_status")
            or r1.get("text") != r2.get("text")
            or r1.get("prompt") != r2.get("prompt")
            or r1.get("response") != r2.get("response")
        ):
            changed_rows.append(
                {
                    "row_id": rid,
                    "before_status": r1.get("curation_status") if r1 else None,
                    "after_status": r2.get("curation_status"),
                }
            )
    for rid, r1 in map1.items():
        if rid not in map2:
            changed_rows.append(
                {
                    "row_id": rid,
                    "before_status": r1.get("curation_status"),
                    "after_status": "dropped",
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
    action: str | None = Query(default=None),
    target_type: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    _user: User = Depends(require_workspace_permission(Permission.AUDIT_READ)),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """List immutable audit log entries for a workspace with optional filters."""
    logs = await _query_workspace_audit_logs(
        db, workspace_id=workspace_id, action=action, target_type=target_type, limit=limit
    )
    return [_serialize_audit_log(log) for log in logs]


@router.get("/audit-logs")
async def list_audit_logs_alias(
    workspace_id: str = Query(...),
    action: str | None = Query(default=None),
    target_type: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Canonical `GET /api/v1/audit-logs?workspace_id=...` alias (CLAUDE.md 2.8)."""
    check_api_token_scope(Permission.AUDIT_READ)
    if not user.is_superadmin:
        membership = (
            await db.execute(
                select(Membership).where(
                    Membership.user_id == user.id,
                    Membership.workspace_id == workspace_id,
                )
            )
        ).scalar_one_or_none()
        if membership is None or not has_permission(membership.role, Permission.AUDIT_READ):
            raise AppError(
                code="FORBIDDEN",
                message="Anda tidak memiliki izin `audit:read` pada workspace tersebut.",
                status_code=status.HTTP_403_FORBIDDEN,
            )
    logs = await _query_workspace_audit_logs(
        db, workspace_id=workspace_id, action=action, target_type=target_type, limit=limit
    )
    return [_serialize_audit_log(log) for log in logs]


@router.get("/workspaces/{workspace_id}/audit-logs/export")
async def export_audit_logs(
    workspace_id: str,
    export_format: Literal["csv", "json"] = Query(default="csv", alias="format"),
    action: str | None = Query(default=None),
    target_type: str | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=2000),
    _user: User = Depends(require_workspace_permission(Permission.AUDIT_READ)),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Export workspace immutable audit logs in CSV or JSON format."""
    logs = await _query_workspace_audit_logs(
        db, workspace_id=workspace_id, action=action, target_type=target_type, limit=limit
    )
    serialized = [_serialize_audit_log(log) for log in logs]
    if export_format == "json":
        return Response(
            content=json.dumps(serialized, ensure_ascii=False, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="audit_logs.json"'},
        )

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["id", "created_at", "user_id", "action", "target_type", "target_id", "ip_address"]
    )
    for item in serialized:
        writer.writerow(
            [
                _safe_csv_cell(item["id"]),
                _safe_csv_cell(item["created_at"]),
                _safe_csv_cell(item["user_id"]),
                _safe_csv_cell(item["action"]),
                _safe_csv_cell(item["target_type"]),
                _safe_csv_cell(item["target_id"]),
                _safe_csv_cell(item["ip_address"]),
            ]
        )
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="audit_logs.csv"'},
    )
