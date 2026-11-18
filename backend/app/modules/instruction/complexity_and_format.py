"""Module D1 & D3: Instruction complexity classification and formatting compliance."""

import json
import re

import polars as pl

from app.modules.base import ModuleConfig, ModuleResult, RowFinding

_REASONING_KEYWORDS = frozenset(
    {
        "mengapa",
        "jelaskan",
        "bandingkan",
        "analisis",
        "langkah",
        "why",
        "explain",
        "compare",
        "analyze",
        "step-by-step",
        "derive",
    }
)
_JSON_REQUEST_RE = re.compile(r"\bjson\b", re.IGNORECASE)


def classify_complexity(prompt: str) -> str:
    """Classify instruction complexity into `simple`, `moderate`, or `complex`."""
    lower = prompt.lower()
    words = lower.split()
    has_reasoning = any(k in lower for k in _REASONING_KEYWORDS)
    if len(words) >= 35 or has_reasoning:
        return "complex"
    if len(words) >= 12:
        return "moderate"
    return "simple"


def check_formatting_compliance(prompt: str, response: str) -> list[str]:
    """Check for unclosed markdown code fences and invalid JSON when requested."""
    issues: list[str] = []
    fence_count = response.count("```")
    if fence_count % 2 != 0:
        issues.append("UNCLOSED_CODE_BLOCK")

    if _JSON_REQUEST_RE.search(prompt) and response.strip():
        candidate = response.strip()
        if "```json" in candidate:
            parts = candidate.split("```json", 1)[1].split("```", 1)
            candidate = parts[0].strip()
        try:
            json.loads(candidate)
        except json.JSONDecodeError:
            issues.append("INVALID_JSON_RESPONSE")
    return issues


class InstructionComplexityAndFormatModule:
    """Scores instruction complexity distribution and checks response format compliance."""

    name = "instruction_format"
    dimension = "instruction_quality"
    version = "0.1.0"

    def run(
        self,
        data: pl.LazyFrame,
        config: ModuleConfig,
        seed: int,
    ) -> tuple[ModuleResult, pl.DataFrame]:
        del config, seed
        df = data.select(["row_id", "prompt", "response"]).collect()
        total_rows = df.height

        complex_count = 0
        compliant_count = 0
        findings: list[dict[str, object]] = []

        for row in df.iter_rows(named=True):
            row_id = str(row["row_id"])
            prompt = str(row.get("prompt") or "")
            response = str(row.get("response") or "")

            if classify_complexity(prompt) == "complex":
                complex_count += 1

            issues = check_formatting_compliance(prompt, response)
            if not issues:
                compliant_count += 1
            else:
                for code in issues:
                    findings.append(
                        RowFinding(
                            row_id=row_id,
                            module=self.name,
                            code=code,
                            severity="warning",
                            detail={"issue": code},
                        ).model_dump()
                    )

        compliance_ratio = (compliant_count / total_rows) if total_rows else 1.0
        complex_ratio = (complex_count / total_rows) if total_rows else 0.0

        result = ModuleResult(
            module=self.name,
            dimension=self.dimension,
            metrics={
                "total_rows": float(total_rows),
                "format_compliance_ratio": round(compliance_ratio, 4),
                "complex_instruction_ratio": round(complex_ratio, 4),
                "avg_judge_score": round(compliance_ratio, 4),
            },
            findings_count=len(findings),
        )
        findings_df = pl.DataFrame(findings) if findings else pl.DataFrame()
        return result, findings_df
