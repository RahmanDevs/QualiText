"""Module E1: Stereotype and demographic bias scanner."""

import re

import polars as pl

from app.modules.base import ModuleConfig, ModuleResult, RowFinding

_BIAS_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "gender_stereotype",
        re.compile(
            r"\b(wanita|perempuan|ibu rumah tangga)\s+(?:tidak bisa|lemah|harus di dapur)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "ethnic_religion_bias",
        re.compile(
            r"\b(kaum|suku|agama)\s+\w+\s+(?:selalu|semuanya)\s+(?:malas|jahat)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "socioeconomic_bias",
        re.compile(r"\b(orang miskin|warga kampung)\s+(?:bodoh|pemalas)\b", re.IGNORECASE),
    ),
]


class BiasScannerModule:
    """Scans text for stereotype and demographic bias patterns."""

    name = "bias_scanner"
    dimension = "safety_bias"
    version = "0.1.0"

    def run(
        self,
        data: pl.LazyFrame,
        config: ModuleConfig,
        seed: int,
    ) -> tuple[ModuleResult, pl.DataFrame]:
        del config, seed
        df = data.select(["row_id", "text"]).collect()
        total_rows = df.height
        biased_rows = 0
        findings: list[dict[str, object]] = []

        for row in df.iter_rows(named=True):
            row_id = str(row["row_id"])
            text = str(row.get("text") or "")
            matched_cats: list[str] = []
            for cat, pat in _BIAS_PATTERNS:
                if pat.search(text):
                    matched_cats.append(cat)

            if matched_cats:
                biased_rows += 1
                findings.append(
                    RowFinding(
                        row_id=row_id,
                        module=self.name,
                        code="BIAS_STEREOTYPE",
                        severity="warning",
                        score=0.8,
                        detail={"categories": matched_cats},
                    ).model_dump()
                )

        bias_ratio = (biased_rows / total_rows) if total_rows else 0.0
        result = ModuleResult(
            module=self.name,
            dimension=self.dimension,
            metrics={
                "total_rows": float(total_rows),
                "biased_rows": float(biased_rows),
                "bias_ratio": round(bias_ratio, 4),
            },
            findings_count=len(findings),
        )
        findings_df = pl.DataFrame(findings) if findings else pl.DataFrame()
        return result, findings_df
