"""Module B1, B2, B6: Indonesian & standard PII detection, masking, and diff preview."""

import hashlib
import re
from dataclasses import dataclass

import polars as pl

from app.modules.base import ModuleConfig, ModuleResult, RowFinding


@dataclass(frozen=True)
class PiiMatch:
    """Single PII span match (never logged or persisted in raw form)."""

    entity_type: str
    start: int
    end: int
    severity: str
    mask_token: str


_PII_PATTERNS: list[tuple[str, re.Pattern[str], str, str]] = [
    # Indonesian NIK: 16 digits starting with valid province/region digits (11-94)
    (
        "PII_NIK",
        re.compile(r"\b(?:1[1-9]|[2-8]\d|9[0-4])\d{14}\b"),
        "critical",
        "[NIK]",
    ),
    # Indonesian NPWP: XX.XXX.XXX.X-XXX.XXX
    (
        "PII_NPWP",
        re.compile(r"\b\d{2}\.\d{3}\.\d{3}\.\d-\d{3}\.\d{3}\b"),
        "critical",
        "[NPWP]",
    ),
    # Indonesian phone numbers (+628..., 628..., 08...)
    (
        "PII_PHONE_ID",
        re.compile(r"(?:\+62|62|0)8[1-9][0-9\-\s]{7,12}\b"),
        "critical",
        "[TELEPON]",
    ),
    # Email address
    (
        "PII_EMAIL",
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
        "critical",
        "[EMAIL]",
    ),
    # Bank account / credit card-like 10-16 digit sequences preceded by rekening/rek/kartu
    (
        "PII_BANK_ACCOUNT",
        re.compile(r"(?i)(?:rekening|rek\.?|no\.?\s*rek)\s*[:#-]?\s*(\d{8,16})\b"),
        "critical",
        "[REKENING]",
    ),
]


def detect_pii_spans(text: str) -> list[PiiMatch]:
    """Detect PII spans in `text` using Indonesian + standard recognizers."""
    matches: list[PiiMatch] = []
    for entity_type, pattern, severity, mask_token in _PII_PATTERNS:
        for m in pattern.finditer(text):
            start, end = m.span(1) if m.lastindex else m.span()
            matches.append(
                PiiMatch(
                    entity_type=entity_type,
                    start=start,
                    end=end,
                    severity=severity,
                    mask_token=mask_token,
                )
            )
    matches.sort(key=lambda item: item.start)
    return matches


def apply_pii_transform(text: str, mode: str = "mask") -> str:
    """Apply `detect_only`, `mask`, or `hash` transformation to `text` for diff preview."""
    if mode == "detect_only":
        return text
    matches = detect_pii_spans(text)
    if not matches:
        return text

    parts: list[str] = []
    cursor = 0
    for match in matches:
        if match.start < cursor:
            continue
        parts.append(text[cursor : match.start])
        raw_val = text[match.start : match.end]
        if mode == "hash":
            digest = hashlib.sha256(raw_val.encode("utf-8")).hexdigest()[:10]
            parts.append(f"[{match.entity_type}:{digest}]")
        else:
            parts.append(match.mask_token)
        cursor = match.end
    parts.append(text[cursor:])
    return "".join(parts)


class PiiDetectionModule:
    """Detects PII entities without ever storing raw PII values in findings."""

    name = "pii"
    dimension = "cleanliness_privacy"
    version = "0.1.0"

    def run(
        self,
        data: pl.LazyFrame,
        config: ModuleConfig,
        seed: int,
    ) -> tuple[ModuleResult, pl.DataFrame]:
        del seed
        mode = str(config.params.get("mode", "detect_only"))

        df = data.select(["row_id", "text"]).collect()
        total_rows = df.height
        rows_with_pii = 0
        findings: list[dict[str, object]] = []

        for row in df.iter_rows(named=True):
            text = str(row.get("text") or "")
            matches = detect_pii_spans(text)
            if matches:
                rows_with_pii += 1
            for match in matches:
                severity = "warning" if mode in {"mask", "hash"} else match.severity
                findings.append(
                    RowFinding(
                        row_id=str(row["row_id"]),
                        module=self.name,
                        code=match.entity_type,
                        severity=severity,
                        score=0.95,
                        span=(match.start, match.end),
                        detail={
                            "entity_type": match.entity_type,
                            "mode": mode,
                            "mask_token": match.mask_token,
                        },
                    ).model_dump()
                )

        pii_per_1k = ((rows_with_pii / total_rows) * 1000.0) if total_rows else 0.0
        clean_ratio = (1.0 - (rows_with_pii / total_rows)) if total_rows else 1.0

        result = ModuleResult(
            module=self.name,
            dimension=self.dimension,
            metrics={
                "total_rows": float(total_rows),
                "rows_with_pii": float(rows_with_pii),
                "pii_per_1k_rows": round(pii_per_1k, 2),
                "pii_clean_ratio": round(clean_ratio, 4),
            },
            findings_count=len(findings),
        )
        findings_df = pl.DataFrame(findings) if findings else pl.DataFrame()
        return result, findings_df
