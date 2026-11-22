"""Module B1, B2, B6: Indonesian & standard PII detection, masking, and diff preview."""

import hashlib
import re
from dataclasses import dataclass
from typing import Literal

import polars as pl

from app.modules.base import ModuleConfig, ModuleResult, RowFinding


@dataclass(frozen=True)
class PiiMatch:
    """Single PII span match (never logged or persisted in raw form)."""

    entity_type: str
    start: int
    end: int
    severity: Literal["info", "warning", "critical"]
    mask_token: str


_PII_PATTERNS: list[tuple[str, re.Pattern[str], Literal["info", "warning", "critical"], str]] = [
    # Explicitly labeled NPWP (dotted or 15/16-digit) placed before unlabeled numeric NIK
    (
        "PII_NPWP",
        re.compile(r"\b\d{2}\.\d{3}\.\d{3}\.\d-\d{3}\.\d{3}\b"),
        "critical",
        "[NPWP]",
    ),
    (
        "PII_NPWP",
        re.compile(r"(?i:\bNPWP\b)(?:\s+[a-z]{2,12})?\s*[:#-]?\s*(\d{15,16})\b"),
        "critical",
        "[NPWP]",
    ),
    # Explicitly labeled NIK including space/dash-grouped digits
    (
        "PII_NIK",
        re.compile(r"(?i:\bNIK\b)(?:\s+[a-z]{2,12})?\s*[:#-]?\s*(\d{4}(?:[-\s]\d{4}){3}|\d{16})\b"),
        "critical",
        "[NIK]",
    ),
    # Bank account / virtual account numbers (allowing bank/account qualifiers before separator)
    (
        "PII_BANK_ACCOUNT",
        re.compile(
            r"(?i:\b(?:rekening|rek\.?|no\.?\s*rek|virtual\s*account|\bva))"
            r"(?:\s+[a-z]{2,15}){0,2}\s*[:#-]?\s*(\d[\d\s-]{6,18}\d)\b"
        ),
        "critical",
        "[REKENING]",
    ),
    # Unlabeled Indonesian NIK: 16 digits starting with valid province/region digits (11-94)
    (
        "PII_NIK",
        re.compile(r"\b(?:1[1-9]|[2-8]\d|9[0-4])\d{14}\b"),
        "critical",
        "[NIK]",
    ),
    # Indonesian phone numbers (+62-8..., +62 8..., 628..., 08...)
    (
        "PII_PHONE_ID",
        re.compile(r"(?:\+62|62|0)[-\s]?8[1-9](?:[-\s]?\d){7,11}\b"),
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
    # Payment card 16-digit Visa/Mastercard patterns
    (
        "PII_BANK_ACCOUNT",
        re.compile(r"\b(?:4\d{3}|5[1-5]\d{2})[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b"),
        "critical",
        "[KARTU]",
    ),
    # Indonesian person name preceded by explicit label (Nama: ..., Nasabah: ..., a.n.: ...)
    (
        "PII_PERSON",
        re.compile(
            r"(?i:\b(?:nama(?:\s+lengkap)?|pelanggan|nasabah|pemohon|a\.?n\.?))\s*[:\-]\s*"
            r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3})\b"
        ),
        "critical",
        "[NAMA]",
    ),
    # Indonesian honorific + capitalized full name (2-3 words)
    (
        "PII_PERSON",
        re.compile(
            r"(?i:\b(?:bpk\.?|bapak|ibu|sdr\.?|sdri\.?))\s+"
            r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\b"
        ),
        "critical",
        "[NAMA]",
    ),
    # Indonesian street address with house number, RT/RW, or 5-digit postal code
    (
        "PII_ADDRESS",
        re.compile(
            r"(?i:\b(?:jl\.?|jalan|gg\.?|gang|perum(?:ahan)?|komplek)\s+)"
            r"[A-Za-z0-9][A-Za-z0-9\s.,/-]{3,60}"
            r"(?i:\b(?:no\.?\s*\d+|rt\.?\s*\d+\s*/?\s*rw\.?\s*\d+|kode\s*pos\s*\d{5})\b)"
        ),
        "critical",
        "[ALAMAT]",
    ),
]


def detect_pii_spans(text: str) -> list[PiiMatch]:
    """Detect non-overlapping PII spans in `text` using Indonesian + standard recognizers."""
    raw_matches: list[PiiMatch] = []
    for entity_type, pattern, severity, mask_token in _PII_PATTERNS:
        for m in pattern.finditer(text):
            start, end = m.span(1) if m.lastindex else m.span()
            raw_matches.append(
                PiiMatch(
                    entity_type=entity_type,
                    start=start,
                    end=end,
                    severity=severity,
                    mask_token=mask_token,
                )
            )
    raw_matches.sort(key=lambda item: (item.start, -(item.end - item.start)))
    resolved: list[PiiMatch] = []
    last_end = -1
    for item in raw_matches:
        if item.start >= last_end:
            resolved.append(item)
            last_end = item.end
    return resolved


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

        total_rows = 0
        rows_with_pii = 0
        findings: list[dict[str, object]] = []

        for batch in data.select(["row_id", "text"]).collect_batches(chunk_size=10_000):
            total_rows += batch.height
            for row in batch.iter_rows(named=True):
                text = str(row.get("text") or "")
                matches = detect_pii_spans(text)
                if matches:
                    rows_with_pii += 1
                for match in matches:
                    findings.append(
                        RowFinding(
                            row_id=str(row["row_id"]),
                            module=self.name,
                            code=match.entity_type,
                            severity=match.severity,
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
