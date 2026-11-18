"""Lightweight deterministic embedding helper with optional sentence-transformers hook."""

import hashlib
import math
import re

_WS_RE = re.compile(r"\s+")


def embed_texts_deterministic(texts: list[str], dim: int = 64) -> list[list[float]]:
    """Project texts into normalized dense vectors via character-trigram feature hashing.

    This guarantees 100% offline, deterministic embeddings for fast-dev and unit tests.
    In GPU/production deployments with `sentence-transformers` installed, this helper
    can be swapped to load `settings.embedding_model_name` (ADR-002).
    """
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


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Compute cosine similarity between two L2-normalized vectors."""
    return sum(a * b for a, b in zip(vec_a, vec_b, strict=True))
