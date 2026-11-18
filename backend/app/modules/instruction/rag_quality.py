"""Module D5: RAG chunk quality assessment (length, completeness, coherence)."""

import polars as pl

from app.modules.base import ModuleConfig, ModuleResult, RowFinding


class RagChunkQualityModule:
    """Evaluates RAG document chunks for too-short fragments or mid-sentence truncation."""

    name = "rag_chunk_quality"
    dimension = "instruction_quality"
    version = "0.1.0"

    def run(
        self,
        data: pl.LazyFrame,
        config: ModuleConfig,
        seed: int,
    ) -> tuple[ModuleResult, pl.DataFrame]:
        del seed
        min_chars = int(config.params.get("min_chars", 40))
        max_chars = int(config.params.get("max_chars", 8000))

        df = data.select(["row_id", "text"]).collect()
        total_rows = df.height
        low_quality = 0
        findings: list[dict[str, object]] = []

        for row in df.iter_rows(named=True):
            rid = str(row["row_id"])
            text = str(row.get("text") or "").strip()
            if len(text) < min_chars:
                low_quality += 1
                findings.append(
                    RowFinding(
                        row_id=rid,
                        module=self.name,
                        code="RAG_CHUNK_TOO_SHORT",
                        severity="warning",
                        detail={"char_length": len(text), "min_chars": min_chars},
                    ).model_dump()
                )
            elif len(text) > max_chars:
                low_quality += 1
                findings.append(
                    RowFinding(
                        row_id=rid,
                        module=self.name,
                        code="RAG_CHUNK_TOO_LONG",
                        severity="info",
                        detail={"char_length": len(text), "max_chars": max_chars},
                    ).model_dump()
                )
            elif text and text[-1] not in {".", "!", "?", '"', "'", ")", "]"}:
                findings.append(
                    RowFinding(
                        row_id=rid,
                        module=self.name,
                        code="RAG_CHUNK_INCOMPLETE_ENDING",
                        severity="info",
                        detail={"ends_with": text[-1]},
                    ).model_dump()
                )

        quality_ratio = max(0.0, 1.0 - (low_quality / total_rows)) if total_rows else 1.0
        result = ModuleResult(
            module=self.name,
            dimension=self.dimension,
            metrics={
                "total_rows": float(total_rows),
                "low_quality_chunks": float(low_quality),
                "avg_judge_score": round(quality_ratio, 4),
            },
            findings_count=len(findings),
        )
        findings_df = pl.DataFrame(findings) if findings else pl.DataFrame()
        return result, findings_df
