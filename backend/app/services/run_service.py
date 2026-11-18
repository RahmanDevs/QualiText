"""Quality pipeline execution service (A5 -> B1-B3 -> C1 -> DQI Scoring)."""

import io
from datetime import UTC, datetime
from typing import Any

import polars as pl
from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.models import Dataset, QualityRun, RowFindingRecord, RunModuleResult
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
from app.scoring.dqi import compute_dqi
from app.services.audit_service import record_audit_log
from app.services.dataset_service import (
    get_dataset_or_404,
    get_version_or_404,
    load_version_parquet_bytes,
)

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


async def create_and_execute_run(
    db: AsyncSession,
    *,
    version_id: str,
    modules: list[str],
    seed: int,
    params: dict[str, dict[str, Any]],
    user_id: str,
) -> QualityRun:
    """Create a QualityRun and execute selected modules deterministically."""
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
        status="running",
        current_stage="loading_data",
        progress_pct=5,
        started_at=datetime.now(UTC),
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
        metadata={"version_id": version.id, "modules": ordered_modules, "seed": seed},
    )

    try:
        pq_bytes = load_version_parquet_bytes(dataset.id, version.version_number)
        lazy_df = pl.read_parquet(io.BytesIO(pq_bytes)).lazy()

        module_results: list[ModuleResult] = []
        has_critical = False

        for idx, mod_name in enumerate(ordered_modules):
            mod = MODULE_REGISTRY[mod_name]
            run.current_stage = mod_name
            run.progress_pct = int(((idx + 1) / (len(ordered_modules) + 1)) * 90)

            cfg = ModuleConfig(version=mod.version, params=params.get(mod_name, {}))
            mod_res, findings_df = mod.run(lazy_df, cfg, seed)
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
                    span = f_row.get("span")
                    has_span = isinstance(span, (list, tuple)) and len(span) == 2
                    span_start = int(span[0]) if has_span else None
                    span_end = int(span[1]) if has_span else None
                    db.add(
                        RowFindingRecord(
                            run_id=run.id,
                            row_id=str(f_row["row_id"]),
                            module=str(f_row["module"]),
                            code=str(f_row["code"]),
                            severity=sev,
                            score=float(f_row["score"]) if f_row.get("score") is not None else None,
                            span_start=span_start,
                            span_end=span_end,
                            detail=dict(f_row.get("detail") or {}),
                        )
                    )

        dqi = compute_dqi(module_results, has_critical_findings=has_critical)
        run.dqi_total = dqi.dqi_total
        run.dqi_summary = {
            "status_label": dqi.status_label,
            "is_partial": dqi.is_partial,
            "critical_capped": dqi.critical_capped,
            "critical_cap_value": dqi.critical_cap_value,
            "dimensions": dqi.dimensions,
        }
        run.status = "completed"
        run.current_stage = "completed"
        run.progress_pct = 100
        run.completed_at = datetime.now(UTC)
    except Exception as exc:
        run.status = "failed"
        run.current_stage = "failed"
        run.error_message = str(exc)
        run.completed_at = datetime.now(UTC)

    await db.commit()
    await db.refresh(run)
    return run
