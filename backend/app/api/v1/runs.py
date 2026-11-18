"""Quality run endpoints (`/api/v1/versions/{id}/runs`, `/api/v1/runs/{id}`)."""

import json
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.datasets import _ensure_dataset_access
from app.core.deps import get_current_user
from app.core.security import Permission
from app.db.models import DatasetVersion, QualityRun, RowFindingRecord, RunModuleResult, User
from app.db.session import get_db
from app.schemas.runs import (
    RowFindingResponse,
    RunCreateRequest,
    RunResponse,
    RunSummaryResponse,
)
from app.services import dataset_service, run_service

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
        payload = {
            "run_id": run.id,
            "status": run.status,
            "current_stage": run.current_stage,
            "progress_pct": run.progress_pct,
            "dqi_total": run.dqi_total,
        }
        yield f"data: {json.dumps(payload)}\n\n"

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
    dims = run.dqi_summary.get("dimensions", [])
    dim_rows = "".join(
        f"<tr><td>{d['label']}</td><td>{d['score']}</td>"
        f"<td>{round(d['weight'] * 100)}%</td><td>{d['findings_count']}</td></tr>"
        for d in dims
    )
    mod_rows = "".join(
        f"<tr><td>{m.module}</td><td>{m.dimension}</td>"
        f"<td>{m.findings_count}</td><td><code>{json.dumps(m.metrics)}</code></td></tr>"
        for m in mod_results
    )
    html_doc = f"""<!doctype html>
<html lang="id">
<head><meta charset="utf-8"><title>QualiText Report — {run.id}</title>
<style>
body{{font-family:Inter,system-ui,sans-serif;max-width:900px;margin:40px auto;color:#0f172a;}}
table{{width:100%;border-collapse:collapse;margin:16px 0;}}
th,td{{border:1px solid #e2e8f0;padding:8px 12px;text-align:left;font-size:13px;}}
th{{background:#f1f5f9;}}
</style></head>
<body>
<h1>Laporan Kualitas Dataset — QualiText</h1>
<p><strong>Run ID:</strong> {run.id} | <strong>DQI Total:</strong> {run.dqi_total} / 100
 ({run.dqi_summary.get("status_label", "")})</p>
<h2>Rincian Dimensi</h2>
<table><thead><tr><th>Dimensi</th><th>Skor</th><th>Bobot</th><th>Temuan</th></tr></thead>
<tbody>{dim_rows}</tbody></table>
<h2>Hasil Modul</h2>
<table><thead><tr><th>Modul</th><th>Dimensi</th><th>Temuan</th><th>Metrik</th></tr></thead>
<tbody>{mod_rows}</tbody></table>
</body></html>"""
    return HTMLResponse(
        content=html_doc,
        headers={"Content-Disposition": f'attachment; filename="qualitext_report_{run.id}.html"'},
    )


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
