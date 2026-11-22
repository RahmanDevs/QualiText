"""Quality run endpoints (`/api/v1/versions/{id}/runs`, `/api/v1/runs/{id}`)."""

import asyncio
import io
import json
import re
import time
from collections.abc import AsyncGenerator

import polars as pl
import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.datasets import _ensure_dataset_access
from app.core.config import get_settings
from app.core.deps import get_current_user
from app.core.security import Permission
from app.db.models import DatasetVersion, QualityRun, RowFindingRecord, RunModuleResult, User
from app.db.session import AsyncSessionLocal, get_db
from app.modules.cleaning.pii import apply_pii_transform
from app.modules.semantic.clustering import compute_topic_clusters
from app.modules.semantic.semantic_dedup import compute_dedup_groups
from app.schemas.runs import (
    RowFindingResponse,
    RunCreateRequest,
    RunResponse,
    RunSummaryResponse,
)
from app.services import dataset_service, report_service, run_service
from app.services.audit_service import extract_client_ip
from app.workers.tasks import RUN_PROGRESS_CHANNEL_PREFIX

router = APIRouter(tags=["runs"])


def _to_run_response(run: QualityRun) -> RunResponse:
    return RunResponse(
        id=run.id,
        version_id=run.version_id,
        status=run.status,
        current_stage=run.current_stage,
        progress_pct=run.progress_pct,
        seed=run.seed,
        dqi_total=run.dqi_total,
        dqi_summary=run.dqi_summary,
        started_at=run.started_at,
        completed_at=run.completed_at,
        created_at=run.created_at,
    )


@router.get("/versions/{version_id}/runs", response_model=list[RunResponse])
async def list_version_runs(
    version_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[RunResponse]:
    """List quality runs for a dataset version."""
    ver = await dataset_service.get_version_or_404(db, version_id)
    await _ensure_dataset_access(db, user, ver.dataset_id, Permission.DATASET_READ)
    runs = (
        (
            await db.execute(
                select(QualityRun)
                .where(QualityRun.version_id == version_id)
                .order_by(QualityRun.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [_to_run_response(r) for r in runs]


@router.post(
    "/versions/{version_id}/runs",
    response_model=RunResponse,
    status_code=status.HTTP_201_CREATED,
)
async def start_quality_run(
    version_id: str,
    payload: RunCreateRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RunResponse:
    """Start a quality assessment run on a dataset version."""
    ver = await dataset_service.get_version_or_404(db, version_id)
    await _ensure_dataset_access(db, user, ver.dataset_id, Permission.RUN_EXECUTE)
    run = await run_service.create_and_execute_run(
        db,
        version_id=version_id,
        modules=payload.modules,
        seed=payload.seed,
        params=payload.params,
        user_id=user.id,
        ip_address=extract_client_ip(request),
    )
    return _to_run_response(run)


@router.get("/runs/{run_id}", response_model=RunResponse)
async def get_run(
    run_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RunResponse:
    """Get quality run status."""
    run = await run_service.get_run_or_404(db, run_id)
    ver = await dataset_service.get_version_or_404(db, run.version_id)
    await _ensure_dataset_access(db, user, ver.dataset_id, Permission.DATASET_READ)
    return _to_run_response(run)


@router.get("/runs/{run_id}/events")
async def stream_run_events(
    run_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Server-Sent Events (SSE) endpoint reporting run progress."""
    run = await run_service.get_run_or_404(db, run_id)
    ver = await dataset_service.get_version_or_404(db, run.version_id)
    await _ensure_dataset_access(db, user, ver.dataset_id, Permission.DATASET_READ)

    async def _event_stream() -> AsyncGenerator[str, None]:
        initial = {
            "run_id": run.id,
            "status": run.status,
            "current_stage": run.current_stage,
            "progress_pct": run.progress_pct,
            "dqi_total": run.dqi_total,
        }
        yield f"data: {json.dumps(initial)}\n\n"
        if run.status in {"completed", "failed"}:
            return

        settings = get_settings()
        channel = f"{RUN_PROGRESS_CHANNEL_PREFIX}{run.id}"
        deadline = time.monotonic() + 60.0

        try:
            client = aioredis.from_url(settings.redis_url, decode_responses=True)
            pubsub = client.pubsub()
            try:
                await pubsub.subscribe(channel)
                async with AsyncSessionLocal() as check_db:
                    latest = (
                        await check_db.execute(select(QualityRun).where(QualityRun.id == run.id))
                    ).scalar_one_or_none()
                    if latest is not None and (
                        latest.status in {"completed", "failed"}
                        or latest.progress_pct != run.progress_pct
                        or latest.current_stage != run.current_stage
                    ):
                        snap = {
                            "run_id": latest.id,
                            "status": latest.status,
                            "current_stage": latest.current_stage,
                            "progress_pct": latest.progress_pct,
                            "dqi_total": latest.dqi_total,
                        }
                        yield f"data: {json.dumps(snap)}\n\n"
                        if latest.status in {"completed", "failed"}:
                            return
                while time.monotonic() < deadline:
                    msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                    if msg and isinstance(msg.get("data"), str):
                        raw_data = msg["data"]
                        yield f"data: {raw_data}\n\n"
                        try:
                            parsed = json.loads(raw_data)
                            if parsed.get("status") in {"completed", "failed"}:
                                return
                        except json.JSONDecodeError:
                            pass
            finally:
                await pubsub.unsubscribe(channel)
                await pubsub.aclose()
                await client.aclose()
        except Exception:
            poll_deadline = time.monotonic() + 30.0
            last_pct = run.progress_pct
            last_stage = run.current_stage
            while time.monotonic() < poll_deadline:
                await asyncio.sleep(0.5)
                db.expire_all()
                current = (
                    await db.execute(select(QualityRun).where(QualityRun.id == run_id))
                ).scalar_one_or_none()
                if current is None:
                    return
                if (
                    current.progress_pct != last_pct
                    or current.current_stage != last_stage
                    or current.status in {"completed", "failed"}
                ):
                    last_pct = current.progress_pct
                    last_stage = current.current_stage
                    payload = {
                        "run_id": current.id,
                        "status": current.status,
                        "current_stage": current.current_stage,
                        "progress_pct": current.progress_pct,
                        "dqi_total": current.dqi_total,
                    }
                    yield f"data: {json.dumps(payload)}\n\n"
                    if current.status in {"completed", "failed"}:
                        return

    return StreamingResponse(_event_stream(), media_type="text/event-stream")


@router.get("/runs/{run_id}/summary", response_model=RunSummaryResponse)
async def get_run_summary(
    run_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RunSummaryResponse:
    """Get DQI breakdown and per-module metrics for a run."""
    run = await run_service.get_run_or_404(db, run_id)
    ver = await dataset_service.get_version_or_404(db, run.version_id)
    await _ensure_dataset_access(db, user, ver.dataset_id, Permission.DATASET_READ)

    mod_results = (
        (await db.execute(select(RunModuleResult).where(RunModuleResult.run_id == run_id)))
        .scalars()
        .all()
    )
    return RunSummaryResponse(
        run_id=run.id,
        version_id=run.version_id,
        status=run.status,
        dqi_total=run.dqi_total,
        dqi_summary=run.dqi_summary,
        module_results=[
            {
                "module": mr.module,
                "dimension": mr.dimension,
                "module_version": mr.module_version,
                "metrics": mr.metrics,
                "findings_count": mr.findings_count,
            }
            for mr in mod_results
        ],
    )


@router.get("/runs/{run_id}/findings", response_model=list[RowFindingResponse])
async def get_run_findings(
    run_id: str,
    severity: str | None = Query(default=None),
    module: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[RowFindingResponse]:
    """List paginated row-level findings for a quality run."""
    run = await run_service.get_run_or_404(db, run_id)
    ver = await dataset_service.get_version_or_404(db, run.version_id)
    await _ensure_dataset_access(db, user, ver.dataset_id, Permission.DATASET_READ)

    stmt = select(RowFindingRecord).where(RowFindingRecord.run_id == run_id)
    if severity:
        stmt = stmt.where(RowFindingRecord.severity == severity)
    if module:
        stmt = stmt.where(RowFindingRecord.module == module)
    stmt = stmt.offset(offset).limit(limit)

    rows = (await db.execute(stmt)).scalars().all()
    return [
        RowFindingResponse(
            id=r.id,
            row_id=r.row_id,
            module=r.module,
            code=r.code,
            severity=r.severity,
            score=r.score,
            span_start=r.span_start,
            span_end=r.span_end,
            detail=r.detail,
        )
        for r in rows
    ]


@router.get("/runs/{run_id}/report.html", response_class=HTMLResponse)
async def download_html_report(
    run_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Generate a self-contained HTML quality report for audit and management (Module F6)."""
    run = await run_service.get_run_or_404(db, run_id)
    ver = await dataset_service.get_version_or_404(db, run.version_id)
    await _ensure_dataset_access(db, user, ver.dataset_id, Permission.DATASET_READ)

    mod_results = (
        (await db.execute(select(RunModuleResult).where(RunModuleResult.run_id == run_id)))
        .scalars()
        .all()
    )
    safe_run_id = re.sub(r"[^A-Za-z0-9_-]", "", run.id)
    html_doc = report_service.render_html_report(run, list(mod_results))
    disposition = f'attachment; filename="qualitext_report_{safe_run_id}.html"'
    return HTMLResponse(content=html_doc, headers={"Content-Disposition": disposition})


@router.get("/runs/{run_id}/report.pdf")
async def download_pdf_report(
    run_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Generate a downloadable PDF 1.4 quality summary report (Module F6)."""
    run = await run_service.get_run_or_404(db, run_id)
    ver = await dataset_service.get_version_or_404(db, run.version_id)
    await _ensure_dataset_access(db, user, ver.dataset_id, Permission.DATASET_READ)

    mod_results = (
        (await db.execute(select(RunModuleResult).where(RunModuleResult.run_id == run_id)))
        .scalars()
        .all()
    )
    safe_run_id = re.sub(r"[^A-Za-z0-9_-]", "", run.id)
    pdf_bytes = report_service.render_pdf_report(run, list(mod_results))
    disposition = f'attachment; filename="qualitext_report_{safe_run_id}.pdf"'
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": disposition},
    )


@router.get("/versions/{version_id}/dedup-preview")
async def preview_semantic_dedup(
    version_id: str,
    threshold: float = Query(default=0.88, ge=0.5, le=0.99),
    max_rows: int = Query(default=500, ge=2, le=2000),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Preview semantic duplicate groups at an interactive threshold without re-running (US-5)."""
    ver = await dataset_service.get_version_or_404(db, version_id)
    ds = await _ensure_dataset_access(db, user, ver.dataset_id, Permission.DATASET_READ)
    pq_bytes = dataset_service.load_version_parquet_bytes(ds.id, ver.version_number)
    df = pl.read_parquet(io.BytesIO(pq_bytes)).select(["row_id", "text"]).slice(0, max_rows)

    row_ids = [str(r["row_id"]) for r in df.iter_rows(named=True)]
    texts = [str(r.get("text") or "") for r in df.iter_rows(named=True)]
    groups, backend, hit_ratio = compute_dedup_groups(row_ids, texts, threshold=threshold)

    preview_by_rid = {
        rid: apply_pii_transform(txt, mode="mask")[:180]
        for rid, txt in zip(row_ids, texts, strict=True)
    }
    for grp in groups:
        for member in grp["members"]:
            member["text_preview"] = preview_by_rid.get(str(member["row_id"]), "")

    duplicate_rows = sum(max(0, len(g["members"]) - 1) for g in groups)
    return {
        "version_id": ver.id,
        "threshold": threshold,
        "scanned_rows": len(row_ids),
        "duplicate_groups_count": len(groups),
        "duplicate_rows_count": duplicate_rows,
        "embedding_backend": backend,
        "cache_hit_ratio": hit_ratio,
        "groups": groups,
    }


@router.get("/versions/{version_id}/topic-clusters")
async def get_topic_clusters(
    version_id: str,
    num_clusters: int = Query(default=5, ge=2, le=12),
    seed: int = Query(default=42, ge=0),
    max_rows: int = Query(default=300, ge=2, le=1000),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Compute 2D projected topic cluster scatter data with PII masked in previews (C3)."""
    ver = await dataset_service.get_version_or_404(db, version_id)
    ds = await _ensure_dataset_access(db, user, ver.dataset_id, Permission.DATASET_READ)
    pq_bytes = dataset_service.load_version_parquet_bytes(ds.id, ver.version_number)
    df = pl.read_parquet(io.BytesIO(pq_bytes)).select(["row_id", "text"]).slice(0, max_rows)

    row_ids = [str(r["row_id"]) for r in df.iter_rows(named=True)]
    masked_texts = [
        apply_pii_transform(str(r.get("text") or ""), mode="mask") for r in df.iter_rows(named=True)
    ]
    clustered = compute_topic_clusters(row_ids, masked_texts, num_clusters=num_clusters, seed=seed)
    return {
        "version_id": ver.id,
        "scanned_rows": len(row_ids),
        "entropy": clustered["entropy"],
        "embedding_backend": clustered["embedding_backend"],
        "clusters": clustered["clusters"],
        "points": clustered["points"],
    }


@router.get("/datasets/{dataset_id}/dqi-trend")
async def get_dataset_dqi_trend(
    dataset_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, object]]:
    """Return chronological DQI trend across dataset versions (Module F8)."""
    await _ensure_dataset_access(db, user, dataset_id, Permission.DATASET_READ)
    stmt = (
        select(QualityRun, DatasetVersion.version_number)
        .join(DatasetVersion, QualityRun.version_id == DatasetVersion.id)
        .where(DatasetVersion.dataset_id == dataset_id, QualityRun.status == "completed")
        .order_by(QualityRun.created_at.asc())
    )
    rows = (await db.execute(stmt)).all()
    return [
        {
            "run_id": run.id,
            "version_id": run.version_id,
            "version_number": ver_num,
            "dqi_total": run.dqi_total,
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        }
        for run, ver_num in rows
    ]
