"""Deterministic & cached embedding helpers with sentence-transformers and vector index hooks."""

import hashlib
import importlib
import importlib.util
import math
import random
import re
import threading
from collections import OrderedDict
from typing import Any

from app.core.config import get_settings

_WS_RE = re.compile(r"\s+")
_MAX_CACHE_ENTRIES = 50_000

_cache_lock = threading.Lock()
_EMBEDDING_CACHE: OrderedDict[tuple[str, str], list[float]] = OrderedDict()
_CACHE_HITS = 0
_CACHE_MISSES = 0
_MODEL_INSTANCES: dict[str, Any] = {}


def get_embedding_cache_stats() -> dict[str, int]:
    """Return bounded embedding cache entry count, hits, and misses."""
    with _cache_lock:
        return {
            "entries": len(_EMBEDDING_CACHE),
            "hits": _CACHE_HITS,
            "misses": _CACHE_MISSES,
        }


def clear_embedding_cache() -> None:
    """Clear in-memory embedding cache and hit/miss counters."""
    global _CACHE_HITS, _CACHE_MISSES
    with _cache_lock:
        _EMBEDDING_CACHE.clear()
        _CACHE_HITS = 0
        _CACHE_MISSES = 0


def embed_texts_deterministic(texts: list[str], dim: int = 64) -> list[list[float]]:
    """Project texts into L2-normalized dense vectors via character-trigram feature hashing."""
    vectors: list[list[float]] = []
    for text in texts:
        vec = [0.0] * dim
        norm = _WS_RE.sub(" ", text.strip().lower())
        if len(norm) < 3:
            if norm:
                idx = int.from_bytes(hashlib.blake2b(norm.encode(), digest_size=2).digest()) % dim
                vec[idx] = 1.0
            vectors.append(vec)
            continue

        for i in range(len(norm) - 2):
            trigram = norm[i : i + 3].encode("utf-8")
            digest = hashlib.blake2b(trigram, digest_size=4).digest()
            idx = int.from_bytes(digest[:2]) % dim
            sign = 1.0 if (digest[2] & 1) == 0 else -1.0
            vec[idx] += sign

        norm_val = math.sqrt(sum(v * v for v in vec))
        if norm_val > 0.0:
            vec = [v / norm_val for v in vec]
        vectors.append(vec)
    return vectors


def _load_sentence_transformer(model_name: str, device: str) -> Any | None:
    if importlib.util.find_spec("sentence_transformers") is None:
        return None
    cache_key = f"{model_name}@{device}"
    if cache_key in _MODEL_INSTANCES:
        return _MODEL_INSTANCES[cache_key]
    try:
        st_mod = importlib.import_module("sentence_transformers")
        cls = st_mod.SentenceTransformer
        instance = cls(model_name, device=device)
        _MODEL_INSTANCES[cache_key] = instance
        return instance
    except Exception:
        return None


def embed_texts(
    texts: list[str],
    *,
    model_name: str | None = None,
    backend: str | None = None,
    dim: int = 64,
) -> tuple[list[list[float]], str]:
    """Compute cached dense embeddings via `sentence_transformers` (if available) or local hash."""
    if not texts:
        return [], "local_hash"

    cfg = get_settings()
    chosen_model = model_name or cfg.embedding_model_name
    req_backend = backend or cfg.embedding_backend

    st_model: Any | None = None
    active_backend = "local_hash"
    if req_backend in {"auto", "sentence_transformers"}:
        st_model = _load_sentence_transformer(chosen_model, cfg.embedding_device)
        if st_model is not None:
            active_backend = "sentence_transformers"

    namespace = (
        f"{active_backend}:{chosen_model}"
        if active_backend == "sentence_transformers"
        else f"local_hash:dim{dim}"
    )

    text_hashes = [hashlib.sha256(t.encode("utf-8")).hexdigest() for t in texts]
    results: list[list[float] | None] = [None] * len(texts)
    miss_indices: list[int] = []
    miss_texts: list[str] = []

    global _CACHE_HITS, _CACHE_MISSES
    with _cache_lock:
        for i, th in enumerate(text_hashes):
            key = (namespace, th)
            cached = _EMBEDDING_CACHE.get(key)
            if cached is not None:
                _EMBEDDING_CACHE.move_to_end(key)
                results[i] = cached
                _CACHE_HITS += 1
            else:
                miss_indices.append(i)
                miss_texts.append(texts[i])
                _CACHE_MISSES += 1

    if miss_texts:
        encoded: list[list[float]] | None = None
        if st_model is not None:
            try:
                raw_vecs = st_model.encode(
                    miss_texts,
                    batch_size=64,
                    normalize_embeddings=True,
                    show_progress_bar=False,
                )
                encoded = [[float(x) for x in row] for row in raw_vecs]
            except Exception:
                encoded = None
                active_backend = "local_hash"
                namespace = f"local_hash:dim{dim}"

        if encoded is None:
            active_backend = "local_hash"
            namespace = f"local_hash:dim{dim}"
            results = [None] * len(texts)
            miss_indices = []
            miss_texts = []
            with _cache_lock:
                for i, th in enumerate(text_hashes):
                    key = (namespace, th)
                    cached = _EMBEDDING_CACHE.get(key)
                    if cached is not None:
                        _EMBEDDING_CACHE.move_to_end(key)
                        results[i] = cached
                    else:
                        miss_indices.append(i)
                        miss_texts.append(texts[i])
            encoded = embed_texts_deterministic(miss_texts, dim=dim) if miss_texts else []

        with _cache_lock:
            for idx, vec in zip(miss_indices, encoded, strict=True):
                results[idx] = vec
                key = (namespace, text_hashes[idx])
                _EMBEDDING_CACHE[key] = vec
                _EMBEDDING_CACHE.move_to_end(key)
                if len(_EMBEDDING_CACHE) > _MAX_CACHE_ENTRIES:
                    _EMBEDDING_CACHE.popitem(last=False)

    final_vectors = [vec if vec is not None else [0.0] * dim for vec in results]
    return final_vectors, active_backend


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Compute cosine similarity between two L2-normalized vectors."""
    val = sum(a * b for a, b in zip(vec_a, vec_b, strict=True))
    return max(-1.0, min(1.0, val))


def _faiss_candidate_pairs(
    vectors: list[list[float]],
    threshold: float,
) -> list[tuple[int, int, float]] | None:
    if importlib.util.find_spec("faiss") is None or importlib.util.find_spec("numpy") is None:
        return None
    try:
        faiss_mod = importlib.import_module("faiss")
        np_mod = importlib.import_module("numpy")
        arr = np_mod.asarray(vectors, dtype="float32")
        dim = int(arr.shape[1])
        index = faiss_mod.IndexFlatIP(dim)
        index.add(arr)
        top_k = min(len(vectors), 32)
        sims, nbrs = index.search(arr, top_k)
        pairs: dict[tuple[int, int], float] = {}
        for i in range(len(vectors)):
            for rank in range(top_k):
                j = int(nbrs[i][rank])
                sim = float(sims[i][rank])
                if j <= i or sim < threshold:
                    continue
                pairs[(i, j)] = sim
        return [(i, j, sim) for (i, j), sim in sorted(pairs.items())]
    except Exception:
        return None


def _simhash_lsh_pairs(
    vectors: list[list[float]],
    *,
    threshold: float,
    seed: int,
) -> list[tuple[int, int, float]]:
    dim = len(vectors[0])
    rng = random.Random(seed)
    num_tables = 6
    bits_per_table = 8

    candidate_pairs: set[tuple[int, int]] = set()
    for _table in range(num_tables):
        planes = [[rng.gauss(0.0, 1.0) for _ in range(dim)] for _ in range(bits_per_table)]
        buckets: dict[int, list[int]] = {}
        for row_idx, vec in enumerate(vectors):
            sig = 0
            for b_idx, plane in enumerate(planes):
                if sum(v * p for v, p in zip(vec, plane, strict=True)) >= 0.0:
                    sig |= 1 << b_idx
            buckets.setdefault(sig, []).append(row_idx)
        for members in buckets.values():
            if len(members) < 2:
                continue
            for a_pos in range(len(members)):
                i = members[a_pos]
                for b_pos in range(a_pos + 1, len(members)):
                    j = members[b_pos]
                    pair = (i, j) if i < j else (j, i)
                    candidate_pairs.add(pair)

    verified: list[tuple[int, int, float]] = []
    for i, j in sorted(candidate_pairs):
        sim = cosine_similarity(vectors[i], vectors[j])
        if sim >= threshold:
            verified.append((i, j, sim))
    return verified


def find_similar_candidates(
    vectors: list[list[float]],
    *,
    threshold: float,
    seed: int = 42,
    backend: str | None = None,
) -> list[tuple[int, int, float]]:
    """Return sorted candidate pairs `(i, j, similarity)` meeting `threshold`."""
    n = len(vectors)
    if n < 2:
        return []

    req_backend = backend or get_settings().vector_index_backend
    if req_backend in {"auto", "faiss"}:
        faiss_res = _faiss_candidate_pairs(vectors, threshold)
        if faiss_res is not None:
            return faiss_res

    if req_backend == "lsh" or (req_backend == "auto" and n > 256):
        return _simhash_lsh_pairs(vectors, threshold=threshold, seed=seed)

    pairs: list[tuple[int, int, float]] = []
    for i in range(n):
        vi = vectors[i]
        for j in range(i + 1, n):
            sim = cosine_similarity(vi, vectors[j])
            if sim >= threshold:
                pairs.append((i, j, sim))
    return pairs
