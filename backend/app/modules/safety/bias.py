"""Module E1: Bilingual (ID/EN) stereotype and demographic bias scanner."""

import re

import polars as pl

from app.modules.base import ModuleConfig, ModuleResult, RowFinding

_BIAS_CATEGORIES = (
    "gender_stereotype",
    "ethnic_religion_bias",
    "socioeconomic_bias",
    "age_disability_bias",
)

_BIAS_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "gender_stereotype",
        re.compile(
            r"\b(wanita|perempuan|ibu rumah tangga|women|females?)\s+(?:itu\s+|memang\s+|are\s+)?"
            r"(?:tidak bisa|lemah|harus di dapur|weak|cannot lead|belong in the kitchen)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "ethnic_religion_bias",
        re.compile(
            r"\b(kaum|suku|agama|ethnic|religious)\s+\w+\s+(?:are\s+)?"
            r"(?:selalu|semuanya|always|all)\s+(?:malas|jahat|lazy|corrupt|dangerous)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "socioeconomic_bias",
        re.compile(
            r"\b(orang miskin|warga kampung|poor people|slum residents)\s+(?:itu\s+|are\s+)?"
            r"(?:bodoh|pemalas|stupid|lazy|untrustworthy)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "age_disability_bias",
        re.compile(
            r"\b(orang tua|lansia|penyandang disabilitas|elderly|disabled people)\s+"
            r"(?:itu\s+|are\s+)?(?:tidak berguna|lemot|useless|incapable|burden)\b",
            re.IGNORECASE,
        ),
    ),
]


class BiasScannerModule:
    """Scans ID/EN text for stereotype patterns and emits demographic category counts."""

    name = "bias_scanner"
    dimension = "safety_bias"
    version = "0.2.0"

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
        cat_counts: dict[str, int] = dict.fromkeys(_BIAS_CATEGORIES, 0)
        findings: list[dict[str, object]] = []

        for row in df.iter_rows(named=True):
            row_id = str(row["row_id"])
            text = str(row.get("text") or "")
            matched_cats: list[str] = []
            for cat, pat in _BIAS_PATTERNS:
                if pat.search(text):
                    if cat not in matched_cats:
                        matched_cats.append(cat)
                    cat_counts[cat] += 1

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
        metrics: dict[str, float] = {
            "total_rows": float(total_rows),
            "biased_rows": float(biased_rows),
            "bias_ratio": round(bias_ratio, 4),
        }
        for cat in _BIAS_CATEGORIES:
            metrics[f"cat_{cat}"] = float(cat_counts[cat])

        result = ModuleResult(
            module=self.name,
            dimension=self.dimension,
            metrics=metrics,
            findings_count=len(findings),
        )
        findings_df = pl.DataFrame(findings) if findings else pl.DataFrame()
        return result, findings_df
