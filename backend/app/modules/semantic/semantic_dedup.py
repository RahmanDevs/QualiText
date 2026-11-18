"""Module C2: Semantic deduplication using dense embeddings + cosine similarity."""

import polars as pl

from app.modules.base import ModuleConfig, ModuleResult, RowFinding
from app.modules.semantic.embeddings import cosine_similarity, embed_texts_deterministic


class SemanticDedupModule:
    """Groups semantically similar rows and recommends the longest/cleanest canonical row."""

    name = "dedup_semantic"
    dimension = "uniqueness_diversity"
    version = "0.1.0"

    def run(
        self,
        data: pl.LazyFrame,
        config: ModuleConfig,
        seed: int,
    ) -> tuple[ModuleResult, pl.DataFrame]:
        del seed
        threshold = float(config.params.get("similarity_threshold", 0.90))
        df = data.select(["row_id", "text"]).collect()
        total_rows = df.height

        row_ids = [str(r["row_id"]) for r in df.iter_rows(named=True)]
        texts = [str(r.get("text") or "") for r in df.iter_rows(named=True)]
        vectors = embed_texts_deterministic(texts)

        # Keep-first/longest canonical representatives
        canonicals: list[tuple[str, list[float]]] = []
        semantic_dups = 0
        findings: list[dict[str, object]] = []

        for rid, vec in zip(row_ids, vectors, strict=True):
            best_canon: str | None = None
            best_sim = 0.0
            for cid, cvec in canonicals:
                sim = cosine_similarity(vec, cvec)
                if sim >= threshold and sim > best_sim:
                    best_sim = sim
                    best_canon = cid
            if best_canon is not None:
                semantic_dups += 1
                findings.append(
                    RowFinding(
                        row_id=rid,
                        module=self.name,
                        code="DUP_SEMANTIC",
                        severity="info",
                        score=round(best_sim, 4),
                        detail={"canonical_row_id": best_canon, "similarity": round(best_sim, 4)},
                    ).model_dump()
                )
            else:
                canonicals.append((rid, vec))

        unique_ratio = max(0.0, 1.0 - (semantic_dups / total_rows)) if total_rows else 1.0
        result = ModuleResult(
            module=self.name,
            dimension=self.dimension,
            metrics={
                "total_rows": float(total_rows),
                "semantic_duplicates": float(semantic_dups),
                "unique_ratio": round(unique_ratio, 4),
            },
            findings_count=len(findings),
        )
        findings_df = pl.DataFrame(findings) if findings else pl.DataFrame()
        return result, findings_df
