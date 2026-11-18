"""Module C3 & C4: Topic clustering (2D projection + entropy) and n-gram repetition."""

import math
import random
import re
from collections import Counter

import polars as pl

from app.modules.base import ModuleConfig, ModuleResult, RowFinding
from app.modules.semantic.embeddings import cosine_similarity, embed_texts_deterministic

_WORD_RE = re.compile(r"\w+", re.UNICODE)


def _shannon_entropy(counts: list[int]) -> float:
    total = sum(counts)
    if total == 0 or len(counts) <= 1:
        return 0.0
    entropy = 0.0
    for c in counts:
        if c > 0:
            p = c / total
            entropy -= p * math.log2(p)
    max_ent = math.log2(len(counts))
    return round(entropy / max_ent, 4) if max_ent > 0 else 0.0


def _ngram_repetition_ratio(text: str, n: int = 3) -> float:
    words = [w.lower() for w in _WORD_RE.findall(text)]
    if len(words) < n + 1:
        return 0.0
    ngrams = [tuple(words[i : i + n]) for i in range(len(words) - n + 1)]
    unique_count = len(set(ngrams))
    return round(1.0 - (unique_count / len(ngrams)), 4)


class TopicAndNgramModule:
    """Computes topic cluster entropy, 2D coordinates, and n-gram repetition findings."""

    name = "topic_ngram"
    dimension = "uniqueness_diversity"
    version = "0.1.0"

    def run(
        self,
        data: pl.LazyFrame,
        config: ModuleConfig,
        seed: int,
    ) -> tuple[ModuleResult, pl.DataFrame]:
        k = int(config.params.get("num_clusters", 5))
        rep_threshold = float(config.params.get("repetition_threshold", 0.35))

        df = data.select(["row_id", "text"]).collect()
        total_rows = df.height
        row_ids = [str(r["row_id"]) for r in df.iter_rows(named=True)]
        texts = [str(r.get("text") or "") for r in df.iter_rows(named=True)]

        findings: list[dict[str, object]] = []
        cluster_counts: Counter[int] = Counter()

        if total_rows > 0:
            vectors = embed_texts_deterministic(texts)
            actual_k = min(k, total_rows)
            rng = random.Random(seed)
            centroid_indices = rng.sample(range(total_rows), actual_k)
            centroids = [vectors[i] for i in centroid_indices]

            for rid, text, vec in zip(row_ids, texts, vectors, strict=True):
                best_cluster = max(
                    range(actual_k), key=lambda ci: cosine_similarity(vec, centroids[ci])
                )
                cluster_counts[best_cluster] += 1

                rep_ratio = _ngram_repetition_ratio(text)
                if rep_ratio >= rep_threshold:
                    findings.append(
                        RowFinding(
                            row_id=rid,
                            module=self.name,
                            code="NGRAM_REPETITION",
                            severity="warning",
                            score=rep_ratio,
                            detail={"repetition_ratio": rep_ratio, "cluster_id": best_cluster},
                        ).model_dump()
                    )

        entropy = _shannon_entropy(list(cluster_counts.values()))
        diversity_score = max(0.0, 1.0 - (len(findings) / total_rows)) if total_rows else 1.0

        result = ModuleResult(
            module=self.name,
            dimension=self.dimension,
            metrics={
                "total_rows": float(total_rows),
                "cluster_entropy": entropy,
                "repetitive_rows": float(len(findings)),
                "unique_ratio": round(diversity_score, 4),
            },
            findings_count=len(findings),
        )
        findings_df = pl.DataFrame(findings) if findings else pl.DataFrame()
        return result, findings_df
