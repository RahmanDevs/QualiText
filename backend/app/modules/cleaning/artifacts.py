"""Module B3, B6: Text artifact detection & cleaning (HTML, zero-width, mojibake, Unicode)."""

import html
import re
import unicodedata

import polars as pl

from app.modules.base import ModuleConfig, ModuleResult, RowFinding

_HTML_TAG_RE = re.compile(r"<[a-zA-Z/][^>]*>")
_ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\ufeff\u2060]")
_MOJIBAKE_RE = re.compile(r"(?:Ã.|â€.|Â\s)")


def clean_text_artifacts(text: str) -> str:
    """Normalize Unicode, strip zero-width characters, and unescape/remove HTML tags."""
    cleaned = unicodedata.normalize("NFKC", text)
    cleaned = _ZERO_WIDTH_RE.sub("", cleaned)
    cleaned = html.unescape(cleaned)
    cleaned = _HTML_TAG_RE.sub(" ", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip()
    return cleaned


class ArtifactCleaningModule:
    """Detects formatting artifacts and Unicode anomalies."""

    name = "artifacts"
    dimension = "cleanliness_privacy"
    version = "0.1.0"

    def run(
        self,
        data: pl.LazyFrame,
        config: ModuleConfig,
        seed: int,
    ) -> tuple[ModuleResult, pl.DataFrame]:
        del config, seed
        total_rows = 0
        rows_with_artifacts = 0
        findings: list[dict[str, object]] = []

        for batch in data.select(["row_id", "text"]).collect_batches(chunk_size=10_000):
            total_rows += batch.height
            for row in batch.iter_rows(named=True):
                text = str(row.get("text") or "")
                row_id = str(row["row_id"])
                row_flagged = False

                if _HTML_TAG_RE.search(text):
                    row_flagged = True
                    findings.append(
                        RowFinding(
                            row_id=row_id,
                            module=self.name,
                            code="ARTIFACT_HTML_TAG",
                            severity="warning",
                            detail={"kind": "html_tag"},
                        ).model_dump()
                    )
                if _ZERO_WIDTH_RE.search(text):
                    row_flagged = True
                    findings.append(
                        RowFinding(
                            row_id=row_id,
                            module=self.name,
                            code="ARTIFACT_ZERO_WIDTH",
                            severity="info",
                            detail={"kind": "zero_width_space"},
                        ).model_dump()
                    )
                if _MOJIBAKE_RE.search(text):
                    row_flagged = True
                    findings.append(
                        RowFinding(
                            row_id=row_id,
                            module=self.name,
                            code="ARTIFACT_MOJIBAKE",
                            severity="warning",
                            detail={"kind": "mojibake"},
                        ).model_dump()
                    )

                if row_flagged:
                    rows_with_artifacts += 1

        clean_ratio = (1.0 - (rows_with_artifacts / total_rows)) if total_rows else 1.0
        result = ModuleResult(
            module=self.name,
            dimension=self.dimension,
            metrics={
                "total_rows": float(total_rows),
                "rows_with_artifacts": float(rows_with_artifacts),
                "artifact_clean_ratio": round(clean_ratio, 4),
            },
            findings_count=len(findings),
        )
        findings_df = pl.DataFrame(findings) if findings else pl.DataFrame()
        return result, findings_df
