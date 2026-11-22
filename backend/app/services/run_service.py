"""Quality pipeline execution service (async Celery dispatch + eager fast-dev fallback)."""

import io
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import polars as pl
from fastapi import status
from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.observability import get_tracer, record_quality_run
from app.db.models import (
    BenchmarkSet,
    Dataset,
    QualityPolicy,
    QualityRun,
    RowFindingRecord,
    RunModuleResult,
)
from app.modules.base import ModuleConfig, ModuleResult, QualityModule
from app.modules.cleaning.artifacts import ArtifactCleaningModule
from app.modules.cleaning.language import LanguageDetectionModule
from app.modules.cleaning.pii import PiiDetectionModule
from app.modules.cleaning.toxicity import ToxicityModule
from app.modules.ingestion.token_analytics import TokenAnalyticsModule
from app.modules.instruction.complexity_and_format import InstructionComplexityAndFormatModule
from app.modules.instruction.llm_judge import LlmJudgeModule
from app.modules.instruction.preference import PreferenceConsistencyModule
from app.modules.instruction.rag_quality import RagChunkQualityModule
from app.modules.safety.bias import BiasScannerModule
from app.modules.safety.hallucination_and_injection import HallucinationAndInjectionModule
from app.modules.semantic.clustering import TopicAndNgramModule
from app.modules.semantic.contamination import ContaminationDetectionModule
from app.modules.semantic.dedup import ExactAndNearDedupModule
from app.modules.semantic.semantic_dedup import SemanticDedupModule
from app.scoring.dqi import DEFAULT_DIMENSION_WEIGHTS, compute_dqi
from app.services.audit_service import record_audit_log
from app.services.dataset_service import (
    get_dataset_or_404,
    get_version_or_404,
    load_version_parquet_bytes,
)
from app.services.webhook_service import dispatch_workspace_webhooks
from app.workers.tasks import publish_run_progress

FINDING_BULK_BATCH_SIZE = 1000
logger = get_logger(__name__)

MODULE_REGISTRY: dict[str, QualityModule] = {
    "token_analytics": TokenAnalyticsModule(),
    "artifacts": ArtifactCleaningModule(),
    "pii": PiiDetectionModule(),
    "language": LanguageDetectionModule(),
    "toxicity": ToxicityModule(),
    "dedup_exact_near": ExactAndNearDedupModule(),
    "dedup_semantic": SemanticDedupModule(),
    "topic_ngram": TopicAndNgramModule(),
    "instruction_format": InstructionComplexityAndFormatModule(),
    "llm_judge": LlmJudgeModule(),
    "preference_consistency": PreferenceConsistencyModule(),
    "rag_chunk_quality": RagChunkQualityModule(),
    "bias_scanner": BiasScannerModule(),
    "hallucination_injection": HallucinationAndInjectionModule(),
    "contamination": ContaminationDetectionModule(),
}

PIPELINE_ORDER = [
    "token_analytics",
    "artifacts",
    "pii",
    "language",
    "toxicity",
    "dedup_exact_near",
    "dedup_semantic",
    "topic_ngram",
    "instruction_format",
    "llm_judge",
    "bias_scanner",
]


async def get_run_or_404(db: AsyncSession, run_id: str) -> QualityRun:
    """Fetch a QualityRun by ID or raise 404."""
    run = (await db.execute(select(QualityRun).where(QualityRun.id == run_id))).scalar_one_or_none()
    if run is None:
        raise AppError(
            code="RUN_NOT_FOUND",
            message="Run kualitas tidak ditemukan.",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return run


def _progress_payload(run: QualityRun) -> dict[str, Any]:
    return {
        "run_id": run.id,
        "status": run.status,
        "current_stage": run.current_stage,
        "progress_pct": run.progress_pct,
        "dqi_total": run.dqi_total,
    }


async def create_run(
    db: AsyncSession,
    *,
    version_id: str,
    modules: list[str],
    seed: int,
    params: dict[str, dict[str, Any]],
    user_id: str,
    ip_address: str | None = None,
) -> QualityRun:
    """Validate module list, persist a queued QualityRun, and write audit log."""
    version = await get_version_or_404(db, version_id)
    dataset: Dataset = await get_dataset_or_404(db, version.dataset_id)

    ordered_modules = [m for m in PIPELINE_ORDER if m in modules]
    for m in modules:
        if m in MODULE_REGISTRY and m not in ordered_modules:
            ordered_modules.append(m)

    if not ordered_modules:
        raise AppError(
            code="NO_VALID_MODULES",
            message="Pilih minimal satu modul kualitas yang valid.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    run = QualityRun(
        version_id=version.id,
        created_by=user_id,
        config_snapshot={"modules": ordered_modules, "params": params},
        seed=seed,
        status="queued",
        current_stage="queued",
        progress_pct=0,
    )
    db.add(run)
    await db.flush()

    await record_audit_log(
        db,
        action="run.execute",
        target_type="quality_run",
        target_id=run.id,
        user_id=user_id,
        workspace_id=dataset.workspace_id,
        ip_address=ip_address,
        metadata={"version_id": version.id, "modules": ordered_modules, "seed": seed},
    )
    await db.commit()
    await db.refresh(run)
    return run


async def execute_run_by_id(db: AsyncSession, run_id: str) -> QualityRun:
    """Execute modules for an existing QualityRun, bulk-insert findings, and compute DQI."""
    run = await get_run_or_404(db, run_id)
    version = await get_version_or_404(db, run.version_id)
    dataset: Dataset = await get_dataset_or_404(db, version.dataset_id)
    workspace_id = dataset.workspace_id
    dataset_id = dataset.id
    version_id = version.id
    version_number = version.version_number

    ordered_modules = list(run.config_snapshot.get("modules", []))
    params: dict[str, dict[str, Any]] = dict(run.config_snapshot.get("params", {}))

    run.status = "running"
    run.current_stage = "loading_data"
    run.progress_pct = 5
    run.started_at = datetime.now(UTC)
    await db.execute(delete(RowFindingRecord).where(RowFindingRecord.run_id == run.id))
    await db.execute(delete(RunModuleResult).where(RunModuleResult.run_id == run.id))
    await db.commit()
    publish_run_progress(_progress_payload(run))

    policy = (
        await db.execute(select(QualityPolicy).where(QualityPolicy.workspace_id == workspace_id))
    ).scalar_one_or_none()
    critical_cap = 60.0
    custom_weights: dict[str, float] | None = None
    if policy and policy.is_active:
        critical_cap = float(policy.rules_json.get("critical_cap", 60.0))
        raw_weights = policy.rules_json.get("dimension_weights")
        if isinstance(raw_weights, dict):
            parsed_w = {
                str(k): float(v)
                for k, v in raw_weights.items()
                if str(k) in DEFAULT_DIMENSION_WEIGHTS
                and isinstance(v, (int, float))
                and float(v) > 0.0
            }
            if parsed_w:
                merged_w = dict(DEFAULT_DIMENSION_WEIGHTS)
                merged_w.update(parsed_w)
                custom_weights = merged_w

    start_time = time.monotonic()
    tracer = get_tracer("qualitext.worker")
    try:
        with tracer.start_as_current_span("quality_run.execute") as span:
            span.set_attribute("qualitext.run_id", run.id)
            span.set_attribute("qualitext.modules_count", len(ordered_modules))

            pq_bytes = load_version_parquet_bytes(dataset_id, version_number)
            lazy_df = pl.read_parquet(io.BytesIO(pq_bytes)).lazy()

            workspace_benchmark_prompts: list[str] = []
            if "contamination" in ordered_modules:
                cont_params = params.get("contamination", {})
                raw_ids = cont_params.get("benchmark_set_ids", [])
                requested_ids = (
                    [str(bid) for bid in raw_ids if str(bid).strip()]
                    if isinstance(raw_ids, list)
                    else []
                )
                bench_query = select(BenchmarkSet).where(
                    BenchmarkSet.workspace_id == workspace_id,
                    BenchmarkSet.is_active.is_(True),
                )
                if requested_ids:
                    bench_query = bench_query.where(BenchmarkSet.id.in_(requested_ids))
                if requested_ids or not cont_params.get("benchmark_texts"):
                    bench_sets = (await db.execute(bench_query)).scalars().all()
                    for bset in bench_sets:
                        workspace_benchmark_prompts.extend(
                            str(p) for p in bset.prompts_json if str(p).strip()
                        )

            module_results: list[ModuleResult] = []
            finding_rows: list[dict[str, Any]] = []
            has_critical = False

            for idx, mod_name in enumerate(ordered_modules):
                mod = MODULE_REGISTRY[mod_name]
                run.current_stage = mod_name
                run.progress_pct = int(((idx + 1) / (len(ordered_modules) + 1)) * 90)
                publish_run_progress(_progress_payload(run))

                mod_params = dict(params.get(mod_name, {}))
                if mod_name == "contamination" and workspace_benchmark_prompts:
                    explicit_texts = [
                        str(t) for t in mod_params.get("benchmark_texts", []) if str(t).strip()
                    ]
                    mod_params["benchmark_texts"] = explicit_texts + workspace_benchmark_prompts

                cfg = ModuleConfig(version=mod.version, params=mod_params)
                mod_res, findings_df = mod.run(lazy_df, cfg, run.seed)
                module_results.append(mod_res)

                db.add(
                    RunModuleResult(
                        run_id=run.id,
                        module=mod_res.module,
                        dimension=mod_res.dimension,
                        module_version=mod.version,
                        metrics=mod_res.metrics,
                        findings_count=mod_res.findings_count,
                    )
                )

                if findings_df.height > 0:
                    for f_row in findings_df.iter_rows(named=True):
                        sev = str(f_row.get("severity", "info"))
                        if sev == "critical":
                            has_critical = True
                        span_val = f_row.get("span")
                        has_span = isinstance(span_val, (list, tuple)) and len(span_val) == 2
                        span_start = int(span_val[0]) if has_span else None
                        span_end = int(span_val[1]) if has_span else None
                        finding_rows.append(
                            {
                                "id": str(uuid.uuid4()),
                                "run_id": run.id,
                                "row_id": str(f_row["row_id"]),
                                "module": str(f_row["module"]),
                                "code": str(f_row["code"]),
                                "severity": sev,
                                "score": (
                                    float(f_row["score"])
                                    if f_row.get("score") is not None
                                    else None
                                ),
                                "span_start": span_start,
                                "span_end": span_end,
                                "detail": dict(f_row.get("detail") or {}),
                            }
                        )
                        if len(finding_rows) >= FINDING_BULK_BATCH_SIZE:
                            await db.execute(insert(RowFindingRecord), finding_rows)
                            finding_rows.clear()

            if finding_rows:
                await db.execute(insert(RowFindingRecord), finding_rows)
                finding_rows.clear()

            dqi = compute_dqi(
                module_results,
                has_critical_findings=has_critical,
                critical_cap=critical_cap,
                custom_weights=custom_weights,
            )
            elapsed = time.monotonic() - start_time
            run.dqi_total = dqi.dqi_total
            run.dqi_summary = {
                "status_label": dqi.status_label,
                "is_partial": dqi.is_partial,
                "critical_capped": dqi.critical_capped,
                "critical_cap_value": dqi.critical_cap_value,
                "run_duration_seconds": round(elapsed, 4),
                "dimensions": dqi.dimensions,
            }
            run.status = "completed"
            run.current_stage = "completed"
            run.progress_pct = 100
            run.completed_at = datetime.now(UTC)
            span.set_attribute("qualitext.dqi_total", float(dqi.dqi_total))
    except Exception as exc:
        elapsed = time.monotonic() - start_time
        logger.warning("quality_run_failed", run_id=run.id, error_type=type(exc).__name__)
        await db.rollback()
        run = await get_run_or_404(db, run_id)
        run.status = "failed"
        run.current_stage = "failed"
        run.error_message = (
            exc.message
            if isinstance(exc, AppError)
            else f"{type(exc).__name__}: eksekusi modul kualitas gagal."
        )
        run.completed_at = datetime.now(UTC)

    record_quality_run(run.status, time.monotonic() - start_time)
    await db.commit()
    await db.refresh(run)
    publish_run_progress(_progress_payload(run))

    event_name = "run.completed" if run.status == "completed" else "run.failed"
    await dispatch_workspace_webhooks(
        db,
        workspace_id=workspace_id,
        event_type=event_name,
        payload={
            "run_id": run.id,
            "dataset_id": dataset_id,
            "version_id": version_id,
            "status": run.status,
            "dqi_total": run.dqi_total,
            "seed": run.seed,
        },
    )
    return run


async def create_and_execute_run(
    db: AsyncSession,
    *,
    version_id: str,
    modules: list[str],
    seed: int,
    params: dict[str, dict[str, Any]],
    user_id: str,
    ip_address: str | None = None,
) -> QualityRun:
    """Create a QualityRun and either execute eagerly or dispatch to Celery worker."""
    run = await create_run(
        db,
        version_id=version_id,
        modules=modules,
        seed=seed,
        params=params,
        user_id=user_id,
        ip_address=ip_address,
    )
    if get_settings().celery_task_always_eager:
        return await execute_run_by_id(db, run.id)

    from app.workers.tasks import execute_quality_run_task

    execute_quality_run_task.delay(run.id)
    publish_run_progress(_progress_payload(run))
    return run
