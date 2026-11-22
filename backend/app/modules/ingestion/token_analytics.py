"""Module A5: Token length analytics (histogram, percentiles, truncation risk, tokenizer)."""

import importlib
import math
import re
from collections.abc import Callable

import polars as pl

from app.modules.base import ModuleConfig, ModuleResult, RowFinding

_TOKEN_PATTERN = re.compile(r"\w+|[^\w\s]", re.UNICODE)


def estimate_token_count(text: str) -> int:
    """Estimate token count deterministically without requiring external model downloads."""
    if not text:
        return 0
    # Subword approximation (~1.25x word/punctuation units for Indonesian/English mixed text)
    units = len(_TOKEN_PATTERN.findall(text))
    return max(1, math.ceil(units * 1.25))


def _resolve_token_counter(tokenizer: str) -> Callable[[str], int]:
    """Resolve optional `tiktoken:<enc>` or `hf:<model>` counter with heuristic fallback."""
    if tokenizer.startswith("tiktoken:"):
        enc_name = tokenizer.split(":", 1)[1].strip() or "cl100k_base"
        try:
            tiktoken_mod = importlib.import_module("tiktoken")
            encoder = tiktoken_mod.get_encoding(enc_name)
            return lambda t: len(encoder.encode(t)) if t else 0
        except Exception:
            return estimate_token_count

    if tokenizer.startswith("hf:"):
        model_id = tokenizer.split(":", 1)[1].strip()
        if model_id:
            try:
                tok_mod = importlib.import_module("tokenizers")
                hf_tok = tok_mod.Tokenizer.from_pretrained(model_id)
                return lambda t: len(hf_tok.encode(t).ids) if t else 0
            except Exception:
                return estimate_token_count

    return estimate_token_count


def _percentile(sorted_vals: list[int], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, max(0, int(math.ceil((pct / 100.0) * len(sorted_vals))) - 1))
    return float(sorted_vals[idx])


class TokenAnalyticsModule:
    """Computes token length distribution, histogram buckets, and truncation risk."""

    name = "token_analytics"
    dimension = "structure_validity"
    version = "0.2.0"

    def run(
        self,
        data: pl.LazyFrame,
        config: ModuleConfig,
        seed: int,
    ) -> tuple[ModuleResult, pl.DataFrame]:
        del seed  # Deterministic without randomness
        max_tokens = int(config.params.get("max_tokens", 4096))
        tokenizer_pref = str(config.params.get("tokenizer", "heuristic"))
        counter_fn = _resolve_token_counter(tokenizer_pref)

        total_rows = 0
        token_counts: list[int] = []
        findings: list[dict[str, object]] = []
        truncated_count = 0
        valid_count = 0
        hist = {
            "hist_0_128": 0,
            "hist_129_512": 0,
            "hist_513_1024": 0,
            "hist_1025_2048": 0,
            "hist_2049_4096": 0,
            "hist_gt_4096": 0,
        }

        for batch in data.select(["row_id", "text", "is_valid"]).collect_batches(chunk_size=10_000):
            total_rows += batch.height
            for row in batch.iter_rows(named=True):
                if bool(row.get("is_valid", True)):
                    valid_count += 1
                text = str(row.get("text") or "")
                tok_count = counter_fn(text)
                token_counts.append(tok_count)

                if tok_count <= 128:
                    hist["hist_0_128"] += 1
                elif tok_count <= 512:
                    hist["hist_129_512"] += 1
                elif tok_count <= 1024:
                    hist["hist_513_1024"] += 1
                elif tok_count <= 2048:
                    hist["hist_1025_2048"] += 1
                elif tok_count <= 4096:
                    hist["hist_2049_4096"] += 1
                else:
                    hist["hist_gt_4096"] += 1

                if tok_count > max_tokens:
                    truncated_count += 1
                    findings.append(
                        RowFinding(
                            row_id=str(row["row_id"]),
                            module=self.name,
                            code="TOKEN_TRUNCATION_RISK",
                            severity="warning",
                            score=min(1.0, tok_count / max(1, max_tokens * 2)),
                            detail={"token_count": tok_count, "max_tokens": max_tokens},
                        ).model_dump()
                    )

        sorted_counts = sorted(token_counts)
        avg_tokens = (sum(token_counts) / total_rows) if total_rows else 0.0
        valid_ratio = (valid_count / total_rows) if total_rows else 1.0
        trunc_ratio = (truncated_count / total_rows) if total_rows else 0.0
        max_observed = float(sorted_counts[-1]) if sorted_counts else 0.0

        result = ModuleResult(
            module=self.name,
            dimension=self.dimension,
            metrics={
                "total_rows": float(total_rows),
                "valid_row_ratio": round(valid_ratio, 4),
                "avg_tokens": round(avg_tokens, 2),
                "p50_tokens": _percentile(sorted_counts, 50),
                "p95_tokens": _percentile(sorted_counts, 95),
                "p99_tokens": _percentile(sorted_counts, 99),
                "max_tokens_observed": max_observed,
                "truncation_risk_ratio": round(trunc_ratio, 4),
                **{k: float(v) for k, v in hist.items()},
            },
            findings_count=len(findings),
        )
        findings_df = pl.DataFrame(findings) if findings else pl.DataFrame()
        return result, findings_df
