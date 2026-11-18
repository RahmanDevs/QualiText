"""Module B5: Profanity, hate speech, and toxicity filter (ID/EN lexicon + scoring)."""

import re

import polars as pl

from app.modules.base import ModuleConfig, ModuleResult, RowFinding

_TOXIC_TERMS_ID = frozenset(
    {
        "anjing",
        "bangsat",
        "bajingan",
        "brengsek",
        "goblok",
        "tolol",
        "bodoh",
        "kampret",
        "keparat",
        "jahanam",
    }
)
_TOXIC_TERMS_EN = frozenset(
    {
        "idiot",
        "moron",
        "stupid",
        "bastard",
        "asshole",
        "bullshit",
        "dumbass",
        "scumbag",
    }
)
_WORD_RE = re.compile(r"[a-zA-Z]+")


def score_toxicity(text: str) -> tuple[float, list[str]]:
    """Return normalized toxicity score [0, 1] and matched category tags."""
    words = [w.lower() for w in _WORD_RE.findall(text)]
    if not words:
        return 0.0, []
    hits_id = [w for w in words if w in _TOXIC_TERMS_ID]
    hits_en = [w for w in words if w in _TOXIC_TERMS_EN]
    total_hits = len(hits_id) + len(hits_en)
    if total_hits == 0:
        return 0.0, []
    score = min(1.0, 0.4 + 0.2 * total_hits)
    tags: list[str] = []
    if hits_id:
        tags.append("profanity_id")
    if hits_en:
        tags.append("profanity_en")
    return round(score, 4), tags


class ToxicityModule:
    """Detects toxic/profane language in Indonesian and English text."""

    name = "toxicity"
    dimension = "safety_bias"
    version = "0.1.0"

    def run(
        self,
        data: pl.LazyFrame,
        config: ModuleConfig,
        seed: int,
    ) -> tuple[ModuleResult, pl.DataFrame]:
        del seed
        threshold = float(config.params.get("threshold", 0.4))
        df = data.select(["row_id", "text"]).collect()
        total_rows = df.height
        toxic_rows = 0
        findings: list[dict[str, object]] = []

        for row in df.iter_rows(named=True):
            row_id = str(row["row_id"])
            score, tags = score_toxicity(str(row.get("text") or ""))
            if score >= threshold:
                toxic_rows += 1
                findings.append(
                    RowFinding(
                        row_id=row_id,
                        module=self.name,
                        code="TOXIC_LANGUAGE",
                        severity="warning" if score < 0.8 else "critical",
                        score=score,
                        detail={"categories": tags},
                    ).model_dump()
                )

        toxic_ratio = (toxic_rows / total_rows) if total_rows else 0.0
        result = ModuleResult(
            module=self.name,
            dimension=self.dimension,
            metrics={
                "total_rows": float(total_rows),
                "toxic_rows": float(toxic_rows),
                "toxic_ratio": round(toxic_ratio, 4),
            },
            findings_count=len(findings),
        )
        findings_df = pl.DataFrame(findings) if findings else pl.DataFrame()
        return result, findings_df
