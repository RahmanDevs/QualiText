"""Module C3 & C4: Topic clustering (2D PCA projection + entropy) and n-gram overlap."""

import math
import random
import re
from collections import Counter
from typing import Any

import polars as pl

from app.modules.base import ModuleConfig, ModuleResult, RowFinding
from app.modules.semantic.embeddings import cosine_similarity, embed_texts

_WORD_RE = re.compile(r"\w+", re.UNICODE)
_STOPWORDS = frozenset(
    {
        "dan",
        "yang",
        "di",
        "ke",
        "dari",
        "untuk",
        "pada",
        "dengan",
        "adalah",
        "ini",
        "itu",
        "atau",
        "tidak",
        "ya",
        "apa",
        "the",
        "and",
        "for",
        "with",
        "from",
        "that",
        "this",
        "are",
        "was",
        "you",
        "your",
    }
)


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


def _cross_row_ngram_overlap_ratio(texts: list[str], n: int = 3) -> float:
    """Measure ratio of distinct n-grams that appear in at least two different rows."""
    if len(texts) < 2:
        return 0.0
    doc_freq: Counter[tuple[str, ...]] = Counter()
    for text in texts:
        words = [w.lower() for w in _WORD_RE.findall(text)]
        if len(words) < n:
            continue
        row_ngrams = {tuple(words[i : i + n]) for i in range(len(words) - n + 1)}
        for ng in row_ngrams:
            doc_freq[ng] += 1
    if not doc_freq:
        return 0.0
    shared = sum(1 for freq in doc_freq.values() if freq >= 2)
    return round(shared / len(doc_freq), 4)


def _normalize_vec(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm <= 1e-12:
        return [0.0] * len(vec)
    return [v / norm for v in vec]


def project_vectors_2d(vectors: list[list[float]], seed: int = 42) -> list[tuple[float, float]]:
    """Project dense vectors to 2D `[-1.0, 1.0]` using deterministic covariance power iteration."""
    n = len(vectors)
    if n == 0:
        return []
    if n == 1:
        return [(0.0, 0.0)]

    dim = len(vectors[0])
    means = [sum(v[d] for v in vectors) / n for d in range(dim)]
    centered = [[v[d] - means[d] for d in range(dim)] for v in vectors]

    rng = random.Random(seed)
    basis: list[list[float]] = []
    for _comp in range(2):
        w = _normalize_vec([rng.gauss(0.0, 1.0) for _ in range(dim)])
        for prev in basis:
            dot = sum(w[d] * prev[d] for d in range(dim))
            w = _normalize_vec([w[d] - dot * prev[d] for d in range(dim)])

        for _it in range(25):
            # cov * w = sum_i (row_i . w) * row_i
            next_w = [0.0] * dim
            for row in centered:
                proj = sum(row[d] * w[d] for d in range(dim))
                for d in range(dim):
                    next_w[d] += proj * row[d]
            for prev in basis:
                dot = sum(next_w[d] * prev[d] for d in range(dim))
                next_w = [next_w[d] - dot * prev[d] for d in range(dim)]
            cand = _normalize_vec(next_w)
            if sum(x * x for x in cand) <= 1e-12:
                break
            w = cand
        basis.append(w)

    raw_coords: list[tuple[float, float]] = []
    max_abs = 0.0
    for row in centered:
        x = sum(row[d] * basis[0][d] for d in range(dim))
        y = sum(row[d] * basis[1][d] for d in range(dim))
        max_abs = max(max_abs, abs(x), abs(y))
        raw_coords.append((x, y))

    if max_abs <= 1e-12:
        return [(0.0, 0.0) for _ in vectors]
    return [(round(x / max_abs, 4), round(y / max_abs, 4)) for x, y in raw_coords]


def compute_topic_clusters(
    row_ids: list[str],
    texts: list[str],
    *,
    num_clusters: int = 5,
    seed: int = 42,
) -> dict[str, Any]:
    """Cluster texts into topics, compute 2D coordinates, and extract top terms per cluster."""
    n = len(row_ids)
    if n == 0:
        return {"clusters": [], "points": [], "entropy": 0.0, "embedding_backend": "local_hash"}

    vectors, backend = embed_texts(texts)
    actual_k = max(1, min(num_clusters, n))
    rng = random.Random(seed)
    centroid_indices = rng.sample(range(n), actual_k)
    centroids = [list(vectors[i]) for i in centroid_indices]

    assignments = [0] * n
    dim = len(vectors[0])
    for _step in range(3):
        for idx, vec in enumerate(vectors):
            assignments[idx] = max(
                range(actual_k),
                key=lambda ci: cosine_similarity(vec, centroids[ci]),
            )
        new_centroids: list[list[float]] = []
        for ci in range(actual_k):
            members = [vectors[i] for i, a in enumerate(assignments) if a == ci]
            if not members:
                new_centroids.append(centroids[ci])
                continue
            mean_vec = [sum(m[d] for m in members) / len(members) for d in range(dim)]
            normed = _normalize_vec(mean_vec)
            new_centroids.append(normed if any(normed) else centroids[ci])
        centroids = new_centroids

    coords = project_vectors_2d(vectors, seed=seed)
    cluster_terms: dict[int, Counter[str]] = {ci: Counter() for ci in range(actual_k)}
    cluster_counts: Counter[int] = Counter()

    points: list[dict[str, Any]] = []
    for rid, text, cid, (px, py) in zip(row_ids, texts, assignments, coords, strict=True):
        cluster_counts[cid] += 1
        words = [
            w.lower() for w in _WORD_RE.findall(text) if len(w) >= 3 and w.lower() not in _STOPWORDS
        ]
        cluster_terms[cid].update(words)
        points.append(
            {
                "row_id": rid,
                "x": px,
                "y": py,
                "cluster_id": cid,
                "text_preview": text[:160],
            }
        )

    clusters = [
        {
            "cluster_id": ci,
            "count": cluster_counts.get(ci, 0),
            "top_terms": [term for term, _cnt in cluster_terms[ci].most_common(5)],
        }
        for ci in range(actual_k)
        if cluster_counts.get(ci, 0) > 0
    ]
    counts_list = [cluster_counts[ci] for ci in range(actual_k) if cluster_counts.get(ci, 0) > 0]
    entropy = _shannon_entropy(counts_list)
    return {
        "clusters": clusters,
        "points": points,
        "entropy": entropy,
        "embedding_backend": backend,
    }


class TopicAndNgramModule:
    """Computes topic cluster entropy, 2D coordinates, and intra/cross-row n-gram repetition."""

    name = "topic_ngram"
    dimension = "uniqueness_diversity"
    version = "0.2.0"

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
        entropy = 0.0
        cross_overlap = _cross_row_ngram_overlap_ratio(texts)

        if total_rows > 0:
            clustered = compute_topic_clusters(row_ids, texts, num_clusters=k, seed=seed)
            entropy = float(clustered["entropy"])
            for rid, text, pt in zip(row_ids, texts, clustered["points"], strict=True):
                rep_ratio = _ngram_repetition_ratio(text)
                if rep_ratio >= rep_threshold:
                    findings.append(
                        RowFinding(
                            row_id=rid,
                            module=self.name,
                            code="NGRAM_REPETITION",
                            severity="warning",
                            score=rep_ratio,
                            detail={
                                "repetition_ratio": rep_ratio,
                                "cluster_id": int(pt["cluster_id"]),
                                "x": float(pt["x"]),
                                "y": float(pt["y"]),
                            },
                        ).model_dump()
                    )

        diversity_score = max(0.0, 1.0 - (len(findings) / total_rows)) if total_rows else 1.0
        result = ModuleResult(
            module=self.name,
            dimension=self.dimension,
            metrics={
                "total_rows": float(total_rows),
                "cluster_entropy": entropy,
                "repetitive_rows": float(len(findings)),
                "cross_row_ngram_overlap_ratio": cross_overlap,
                "unique_ratio": round(diversity_score, 4),
            },
            findings_count=len(findings),
        )
        findings_df = pl.DataFrame(findings) if findings else pl.DataFrame()
        return result, findings_df
