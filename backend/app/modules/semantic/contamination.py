"""Module C5: Benchmark and evaluation-set contamination detection."""

import re

import polars as pl

from app.modules.base import ModuleConfig, ModuleResult, RowFinding
from app.modules.semantic.embeddings import cosine_similarity, embed_texts_deterministic

_WORD_RE = re.compile(r"\w+", re.UNICODE)


def _word_ngrams(text: str, n: int = 6) -> set[tuple[str, ...]]:
    words = [w.lower() for w in _WORD_RE.findall(text)]
    if len(words) < n:
        return {tuple(words)} if words else set()
    return {tuple(words[i : i + n]) for i in range(len(words) - n + 1)}


class ContaminationDetectionModule:
    """Detects n-gram or semantic overlap against reference benchmark texts."""

    name = "contamination"
    dimension = "uniqueness_diversity"
    version = "0.1.0"

    def run(
        self,
        data: pl.LazyFrame,
        config: ModuleConfig,
        seed: int,
    ) -> tuple[ModuleResult, pl.DataFrame]:
        del seed
        raw_benchmarks = config.params.get("benchmark_texts", [])
        benchmark_texts = [str(t) for t in raw_benchmarks if str(t).strip()]
        sim_threshold = float(config.params.get("similarity_threshold", 0.92))

        df = data.select(["row_id", "text"]).collect()
        total_rows = df.height
        if not benchmark_texts or total_rows == 0:
            return (
                ModuleResult(
                    module=self.name,
                    dimension=self.dimension,
                    metrics={"total_rows": float(total_rows), "contaminated_rows": 0.0},
                    findings_count=0,
                ),
                pl.DataFrame(),
            )

        bench_ngrams = [_word_ngrams(b) for b in benchmark_texts]
        bench_vecs = embed_texts_deterministic(benchmark_texts)

        row_ids = [str(r["row_id"]) for r in df.iter_rows(named=True)]
        texts = [str(r.get("text") or "") for r in df.iter_rows(named=True)]
        row_vecs = embed_texts_deterministic(texts)

        contaminated = 0
        findings: list[dict[str, object]] = []

        for rid, text, rvec in zip(row_ids, texts, row_vecs, strict=True):
            rngs = _word_ngrams(text)
            ngram_hit = any(bool(rngs & bng) for bng in bench_ngrams if bng)
            best_sim = max((cosine_similarity(rvec, bvec) for bvec in bench_vecs), default=0.0)

            if ngram_hit or best_sim >= sim_threshold:
                contaminated += 1
                findings.append(
                    RowFinding(
                        row_id=rid,
                        module=self.name,
                        code="BENCHMARK_CONTAMINATION",
                        severity="critical",
                        score=round(max(best_sim, 1.0 if ngram_hit else 0.0), 4),
                        detail={
                            "ngram_overlap": ngram_hit,
                            "semantic_similarity": round(best_sim, 4),
                        },
                    ).model_dump()
                )

        clean_ratio = max(0.0, 1.0 - (contaminated / total_rows)) if total_rows else 1.0
        result = ModuleResult(
            module=self.name,
            dimension=self.dimension,
            metrics={
                "total_rows": float(total_rows),
                "contaminated_rows": float(contaminated),
                "unique_ratio": round(clean_ratio, 4),
            },
            findings_count=len(findings),
        )
        findings_df = pl.DataFrame(findings) if findings else pl.DataFrame()
        return result, findings_df
