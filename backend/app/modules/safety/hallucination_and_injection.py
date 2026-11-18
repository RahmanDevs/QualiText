"""Module E2 & E3: Hallucination risk tagging and prompt injection detection."""

import re

import polars as pl

from app.modules.base import ModuleConfig, ModuleResult, RowFinding

_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"ignore\s+(?:all\s+)?previous\s+instructions", re.IGNORECASE),
    re.compile(r"abaikan\s+(?:semua\s+)?instruksi\s+sebelumnya", re.IGNORECASE),
    re.compile(r"system\s*prompt\s*[:=]|reveal\s+your\s+instructions", re.IGNORECASE),
    re.compile(r"\bDAN\b.*do\s+anything\s+now", re.IGNORECASE),
]

_OVERCONFIDENT_HALLUCINATION_RE = re.compile(
    r"\b(?:100%\s+pasti|dijamin\s+benar\s+tanpa\s+sumber|menurut\s+penelitian\s+rahasia)\b",
    re.IGNORECASE,
)


class HallucinationAndInjectionModule:
    """Tags hallucination *risk* (not certainty) and detects prompt injection payloads."""

    name = "hallucination_injection"
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
        flagged_rows = 0
        findings: list[dict[str, object]] = []

        for row in df.iter_rows(named=True):
            rid = str(row["row_id"])
            text = str(row.get("text") or "")
            row_hit = False

            if any(p.search(text) for p in _INJECTION_PATTERNS):
                row_hit = True
                findings.append(
                    RowFinding(
                        row_id=rid,
                        module=self.name,
                        code="PROMPT_INJECTION_RISK",
                        severity="critical",
                        score=0.9,
                        detail={"kind": "prompt_injection"},
                    ).model_dump()
                )

            if _OVERCONFIDENT_HALLUCINATION_RE.search(text):
                row_hit = True
                findings.append(
                    RowFinding(
                        row_id=rid,
                        module=self.name,
                        code="HALLUCINATION_RISK",
                        severity="warning",
                        score=0.65,
                        detail={"note": "Risiko halusinasi (perlu verifikasi manusia)"},
                    ).model_dump()
                )

            if row_hit:
                flagged_rows += 1

        risk_ratio = (flagged_rows / total_rows) if total_rows else 0.0
        result = ModuleResult(
            module=self.name,
            dimension=self.dimension,
            metrics={
                "total_rows": float(total_rows),
                "flagged_rows": float(flagged_rows),
                "toxic_ratio": round(risk_ratio, 4),
            },
            findings_count=len(findings),
        )
        findings_df = pl.DataFrame(findings) if findings else pl.DataFrame()
        return result, findings_df
