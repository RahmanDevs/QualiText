"""Module A5: Token length analytics (histogram, percentiles, truncation risk)."""

import math
import re

import polars as pl

from app.modules.base import ModuleConfig, ModuleResult, RowFinding

_TOKEN_PATTERN = re.compile(r"\w+|[^\w\s]", re.UNICODE)


def estimate_token_count(text: str) -> int:
    """Estimate token count deterministically without requiring external model downloads."""
    if not text:
        return 0
    # Subword approximation (~1.3x word/punctuation units for Indonesian/English mixed text)
    units = len(_TOKEN_PATTERN.findall(text))
    return max(1, math.ceil(units * 1.25))


def _percentile(sorted_vals: list[int], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, max(0, int(math.ceil((pct / 100.0) * len(sorted_vals))) - 1))
    return float(sorted_vals[idx])


class TokenAnalyticsModule:
    """Computes token length distribution and flags rows at truncation risk."""

    name = "token_analytics"
    dimension = "structure_validity"
    version = "0.1.0"

    def run(
        self,
        data: pl.LazyFrame,
        config: ModuleConfig,
        seed: int,
    ) -> tuple[ModuleResult, pl.DataFrame]:
        del seed  # Deterministic without randomness
        max_tokens = int(config.params.get("max_tokens", 4096))

        df = data.select(["row_id", "text", "is_valid"]).collect()
        total_rows = df.height
        token_counts: list[int] = []
        findings: list[dict[str, object]] = []
        truncated_count = 0
        valid_count = 0

        for row in df.iter_rows(named=True):
            if bool(row.get("is_valid", True)):
                valid_count += 1
            text = str(row.get("text") or "")
            tok_count = estimate_token_count(text)
            token_counts.append(tok_count)

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
                "truncation_risk_ratio": round(trunc_ratio, 4),
            },
            findings_count=len(findings),
        )
        findings_df = pl.DataFrame(findings) if findings else pl.DataFrame()
        return result, findings_df
