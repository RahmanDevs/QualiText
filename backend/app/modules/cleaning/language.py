"""Module B4: Per-row language detection and corpus consistency (ID / EN / mixed)."""

import re
from collections import Counter

import polars as pl

from app.modules.base import ModuleConfig, ModuleResult, RowFinding

_ID_STOPWORDS = frozenset(
    {
        "yang",
        "dan",
        "di",
        "ke",
        "dari",
        "ini",
        "itu",
        "untuk",
        "dengan",
        "adalah",
        "pada",
        "tidak",
        "bisa",
        "saya",
        "anda",
        "kita",
        "atau",
        "jika",
        "karena",
        "apa",
        "bagaimana",
    }
)
_EN_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "is",
        "are",
        "to",
        "of",
        "in",
        "for",
        "with",
        "that",
        "this",
        "not",
        "you",
        "we",
        "or",
        "if",
        "because",
        "what",
        "how",
        "can",
    }
)
_WORD_RE = re.compile(r"[a-zA-Z]+")


def detect_language(text: str) -> str:
    """Detect whether `text` is `id`, `en`, `mixed`, or `unknown`."""
    words = [w.lower() for w in _WORD_RE.findall(text)]
    if not words:
        return "unknown"
    id_hits = sum(1 for w in words if w in _ID_STOPWORDS)
    en_hits = sum(1 for w in words if w in _EN_STOPWORDS)
    if id_hits > 0 and en_hits > 0:
        ratio = min(id_hits, en_hits) / max(id_hits, en_hits)
        if ratio >= 0.35:
            return "mixed"
    if id_hits > en_hits:
        return "id"
    if en_hits > id_hits:
        return "en"
    return "id" if len(words) <= 4 else "unknown"


class LanguageDetectionModule:
    """Detects row languages and flags inconsistencies against expected corpus language."""

    name = "language"
    dimension = "cleanliness_privacy"
    version = "0.1.0"

    def run(
        self,
        data: pl.LazyFrame,
        config: ModuleConfig,
        seed: int,
    ) -> tuple[ModuleResult, pl.DataFrame]:
        del seed
        expected_lang = str(config.params.get("expected_language", "id"))
        df = data.select(["row_id", "text"]).collect()
        total_rows = df.height

        counts: Counter[str] = Counter()
        findings: list[dict[str, object]] = []
        consistent = 0

        for row in df.iter_rows(named=True):
            row_id = str(row["row_id"])
            lang = detect_language(str(row.get("text") or ""))
            counts[lang] += 1
            if lang == expected_lang or lang == "mixed":
                consistent += 1
            elif expected_lang != "any":
                findings.append(
                    RowFinding(
                        row_id=row_id,
                        module=self.name,
                        code="LANG_MISMATCH",
                        severity="info",
                        detail={"detected": lang, "expected": expected_lang},
                    ).model_dump()
                )

        consistency_ratio = (consistent / total_rows) if total_rows else 1.0
        result = ModuleResult(
            module=self.name,
            dimension=self.dimension,
            metrics={
                "total_rows": float(total_rows),
                "lang_id_count": float(counts.get("id", 0)),
                "lang_en_count": float(counts.get("en", 0)),
                "lang_mixed_count": float(counts.get("mixed", 0)),
                "lang_consistency_ratio": round(consistency_ratio, 4),
            },
            findings_count=len(findings),
        )
        findings_df = pl.DataFrame(findings) if findings else pl.DataFrame()
        return result, findings_df
