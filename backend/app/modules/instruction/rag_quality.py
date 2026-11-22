"""Module D5: RAG chunk quality assessment (length, completeness, entropy, and redundancy)."""

import re

import polars as pl

from app.modules.base import ModuleConfig, ModuleResult, RowFinding

_WORD_RE = re.compile(r"\w+", re.UNICODE)
_WS_RE = re.compile(r"\s+")


class RagChunkQualityModule:
    """Evaluates RAG document chunks for length, truncation, OCR noise, and duplicate chunks."""

    name = "rag_chunk_quality"
    dimension = "instruction_quality"
    version = "0.2.0"

    def run(
        self,
        data: pl.LazyFrame,
        config: ModuleConfig,
        seed: int,
    ) -> tuple[ModuleResult, pl.DataFrame]:
        del seed
        min_chars = int(config.params.get("min_chars", 40))
        max_chars = int(config.params.get("max_chars", 8000))
        min_ttr = float(config.params.get("min_unique_token_ratio", 0.25))
        max_symbol_ratio = float(config.params.get("max_symbol_ratio", 0.45))

        df = data.select(["row_id", "text"]).collect()
        total_rows = df.height
        low_quality = 0
        seen_normalized: set[str] = set()
        findings: list[dict[str, object]] = []

        for row in df.iter_rows(named=True):
            rid = str(row["row_id"])
            text = str(row.get("text") or "").strip()
            norm_key = _WS_RE.sub(" ", text.lower())

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
            else:
                non_ws = [c for c in text if not c.isspace()]
                symbols = sum(1 for c in non_ws if not c.isalnum() and c not in ".,!?;:-'\"()[]/")
                symbol_ratio = (symbols / len(non_ws)) if non_ws else 0.0
                words = [w.lower() for w in _WORD_RE.findall(text)]
                ttr = (len(set(words)) / len(words)) if words else 0.0

                if symbol_ratio > max_symbol_ratio or (len(words) >= 8 and ttr < min_ttr):
                    low_quality += 1
                    findings.append(
                        RowFinding(
                            row_id=rid,
                            module=self.name,
                            code="RAG_CHUNK_LOW_ENTROPY",
                            severity="warning",
                            detail={
                                "symbol_ratio": round(symbol_ratio, 4),
                                "unique_token_ratio": round(ttr, 4),
                            },
                        ).model_dump()
                    )
                elif norm_key and norm_key in seen_normalized:
                    findings.append(
                        RowFinding(
                            row_id=rid,
                            module=self.name,
                            code="RAG_CHUNK_CONTEXT_DUPLICATE",
                            severity="info",
                            detail={"char_length": len(text)},
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

            if norm_key:
                seen_normalized.add(norm_key)

        quality_ratio = max(0.0, 1.0 - (low_quality / total_rows)) if total_rows else 1.0
        result = ModuleResult(
            module=self.name,
            dimension=self.dimension,
            metrics={
                "total_rows": float(total_rows),
                "low_quality_chunks": float(low_quality),
                "rag_chunk_quality_score": round(quality_ratio, 4),
                "avg_judge_score": round(quality_ratio, 4),
            },
            findings_count=len(findings),
        )
        findings_df = pl.DataFrame(findings) if findings else pl.DataFrame()
        return result, findings_df
