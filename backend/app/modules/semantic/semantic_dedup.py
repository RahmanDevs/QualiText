"""Module C2: Semantic deduplication using cached embeddings + candidate index."""

from typing import Any

import polars as pl

from app.modules.base import ModuleConfig, ModuleResult, RowFinding
from app.modules.semantic.embeddings import (
    cosine_similarity,
    embed_texts,
    find_similar_candidates,
    get_embedding_cache_stats,
)


def compute_dedup_groups(
    row_ids: list[str],
    texts: list[str],
    *,
    threshold: float = 0.90,
    seed: int = 42,
) -> tuple[list[dict[str, Any]], str, float]:
    """Group semantically duplicate rows and select the longest/cleanest canonical row."""
    n = len(row_ids)
    if n < 2:
        return [], "local_hash", 0.0

    before = get_embedding_cache_stats()
    vectors, backend = embed_texts(texts)
    after = get_embedding_cache_stats()
    delta_hits = after["hits"] - before["hits"]
    delta_misses = after["misses"] - before["misses"]
    hit_ratio = (
        round(delta_hits / (delta_hits + delta_misses), 4)
        if (delta_hits + delta_misses) > 0
        else 0.0
    )

    pairs = find_similar_candidates(vectors, threshold=threshold, seed=seed)
    if not pairs:
        return [], backend, hit_ratio

    parent = list(range(n))

    def _find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def _union(a: int, b: int) -> None:
        ra, rb = _find(a), _find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    for i, j, _sim in pairs:
        _union(i, j)

    components: dict[int, list[int]] = {}
    for idx in range(n):
        root = _find(idx)
        components.setdefault(root, []).append(idx)

    groups: list[dict[str, Any]] = []
    group_counter = 0
    for root in sorted(components.keys()):
        comp = components[root]
        if len(comp) < 2:
            continue
        group_counter += 1
        # Choose longest cleaned text as canonical representative; tie-break by earliest index
        canon_idx = max(comp, key=lambda idx: (len(texts[idx].strip()), -idx))
        canon_vec = vectors[canon_idx]

        members: list[dict[str, Any]] = []
        non_canon_sims: list[float] = []
        for idx in comp:
            sim = 1.0 if idx == canon_idx else round(cosine_similarity(vectors[idx], canon_vec), 4)
            if idx != canon_idx:
                non_canon_sims.append(sim)
            members.append(
                {
                    "row_id": row_ids[idx],
                    "similarity": sim,
                    "is_canonical": idx == canon_idx,
                    "char_len": len(texts[idx].strip()),
                }
            )

        members.sort(key=lambda m: (not bool(m["is_canonical"]), -float(m["similarity"])))
        avg_sim = round(sum(non_canon_sims) / len(non_canon_sims), 4) if non_canon_sims else 1.0
        groups.append(
            {
                "group_id": f"grp_{group_counter:04d}",
                "canonical_row_id": row_ids[canon_idx],
                "avg_similarity": avg_sim,
                "members": members,
            }
        )

    return groups, backend, hit_ratio


class SemanticDedupModule:
    """Groups semantically similar rows and recommends the longest/cleanest canonical row."""

    name = "dedup_semantic"
    dimension = "uniqueness_diversity"
    version = "0.2.0"

    def run(
        self,
        data: pl.LazyFrame,
        config: ModuleConfig,
        seed: int,
    ) -> tuple[ModuleResult, pl.DataFrame]:
        threshold = float(config.params.get("similarity_threshold", 0.90))
        df = data.select(["row_id", "text"]).collect()
        total_rows = df.height

        row_ids = [str(r["row_id"]) for r in df.iter_rows(named=True)]
        texts = [str(r.get("text") or "") for r in df.iter_rows(named=True)]

        groups, backend, hit_ratio = compute_dedup_groups(
            row_ids, texts, threshold=threshold, seed=seed
        )

        semantic_dups = 0
        findings: list[dict[str, object]] = []
        for grp in groups:
            canon_rid = str(grp["canonical_row_id"])
            gid = str(grp["group_id"])
            for member in grp["members"]:
                if member["is_canonical"]:
                    continue
                semantic_dups += 1
                findings.append(
                    RowFinding(
                        row_id=str(member["row_id"]),
                        module=self.name,
                        code="DUP_SEMANTIC",
                        severity="info",
                        score=max(0.0, min(1.0, float(member["similarity"]))),
                        detail={
                            "group_id": gid,
                            "canonical_row_id": canon_rid,
                            "similarity": float(member["similarity"]),
                            "embedding_backend": backend,
                        },
                    ).model_dump()
                )

        unique_ratio = max(0.0, 1.0 - (semantic_dups / total_rows)) if total_rows else 1.0
        result = ModuleResult(
            module=self.name,
            dimension=self.dimension,
            metrics={
                "total_rows": float(total_rows),
                "semantic_duplicates": float(semantic_dups),
                "duplicate_groups": float(len(groups)),
                "unique_ratio": round(unique_ratio, 4),
                "embedding_cache_hit_ratio": hit_ratio,
            },
            findings_count=len(findings),
        )
        findings_df = pl.DataFrame(findings) if findings else pl.DataFrame()
        return result, findings_df
