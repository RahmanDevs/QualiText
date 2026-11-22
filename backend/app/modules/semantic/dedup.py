"""Module C1: Exact deduplication (SHA-256) and near-deduplication (MinHash / LSH)."""

import hashlib
import re
from collections import defaultdict

import polars as pl

from app.modules.base import ModuleConfig, ModuleResult, RowFinding

_WS_RE = re.compile(r"\s+")
_NUM_PERM = 32
_BANDS = 8
_ROWS_PER_BAND = _NUM_PERM // _BANDS
_PRIME = (1 << 61) - 1


def _normalize_text(text: str) -> str:
    return _WS_RE.sub(" ", text.strip().lower())


def _shingles(text: str, k: int = 5) -> set[int]:
    norm = _normalize_text(text)
    if len(norm) <= k:
        return (
            {int.from_bytes(hashlib.blake2b(norm.encode("utf-8"), digest_size=4).digest())}
            if norm
            else set()
        )
    return {
        int.from_bytes(hashlib.blake2b(norm[i : i + k].encode("utf-8"), digest_size=4).digest())
        for i in range(len(norm) - k + 1)
    }


def _minhash_signature(shingle_hashes: set[int], salts: list[tuple[int, int]]) -> list[int]:
    if not shingle_hashes:
        return [0] * len(salts)
    sig: list[int] = []
    for a, b in salts:
        min_val = min(((a * h + b) % _PRIME) for h in shingle_hashes)
        sig.append(min_val)
    return sig


def _jaccard_from_sig(sig_a: list[int], sig_b: list[int]) -> float:
    matches = sum(1 for x, y in zip(sig_a, sig_b, strict=True) if x == y)
    return matches / len(sig_a)


class ExactAndNearDedupModule:
    """Detects exact and MinHash/LSH near-duplicate rows deterministically."""

    name = "dedup_exact_near"
    dimension = "uniqueness_diversity"
    version = "0.1.0"

    def run(
        self,
        data: pl.LazyFrame,
        config: ModuleConfig,
        seed: int,
    ) -> tuple[ModuleResult, pl.DataFrame]:
        threshold = float(config.params.get("similarity_threshold", 0.85))
        df = data.select(["row_id", "text"]).collect()
        total_rows = df.height

        # Deterministic salts derived from `seed`
        salts = [
            ((seed * 1103515245 + i * 2654435761) | 1, (seed + i * 97531) % _PRIME)
            for i in range(_NUM_PERM)
        ]

        seen_exact: dict[str, str] = {}
        exact_dups = 0
        near_dups = 0
        findings: list[dict[str, object]] = []

        buckets: dict[tuple[int, tuple[int, ...]], list[tuple[str, list[int]]]] = defaultdict(list)
        unique_signatures: dict[str, list[int]] = {}

        for row in df.iter_rows(named=True):
            row_id = str(row["row_id"])
            text = str(row.get("text") or "")
            norm = _normalize_text(text)
            digest = hashlib.sha256(norm.encode("utf-8")).hexdigest()

            if digest in seen_exact:
                exact_dups += 1
                findings.append(
                    RowFinding(
                        row_id=row_id,
                        module=self.name,
                        code="DUP_EXACT",
                        severity="warning",
                        score=1.0,
                        detail={"duplicate_of": seen_exact[digest]},
                    ).model_dump()
                )
                continue

            seen_exact[digest] = row_id
            shingles = _shingles(text)
            sig = _minhash_signature(shingles, salts)
            unique_signatures[row_id] = sig

            matched_canonical: str | None = None
            best_sim = 0.0
            for band_idx in range(_BANDS):
                start = band_idx * _ROWS_PER_BAND
                band_key = (band_idx, tuple(sig[start : start + _ROWS_PER_BAND]))
                for cand_id, cand_sig in buckets[band_key]:
                    sim = _jaccard_from_sig(sig, cand_sig)
                    if sim >= threshold and sim > best_sim:
                        best_sim = sim
                        matched_canonical = cand_id
                buckets[band_key].append((row_id, sig))

            if matched_canonical is not None:
                near_dups += 1
                findings.append(
                    RowFinding(
                        row_id=row_id,
                        module=self.name,
                        code="DUP_NEAR",
                        severity="info",
                        score=round(best_sim, 4),
                        detail={"near_duplicate_of": matched_canonical},
                    ).model_dump()
                )

        unique_ratio = (
            max(0.0, 1.0 - ((exact_dups + near_dups) / total_rows)) if total_rows else 1.0
        )

        result = ModuleResult(
            module=self.name,
            dimension=self.dimension,
            metrics={
                "total_rows": float(total_rows),
                "exact_duplicates": float(exact_dups),
                "near_duplicates": float(near_dups),
                "unique_ratio": round(unique_ratio, 4),
            },
            findings_count=len(findings),
        )
        findings_df = pl.DataFrame(findings) if findings else pl.DataFrame()
        return result, findings_df
