"""Module E2 & E3: Hallucination risk tagging, harmful taxonomy, and prompt injection detection."""

import re

import httpx
import polars as pl

from app.core.config import get_settings
from app.core.security import is_local_llm_url
from app.modules.base import ModuleConfig, ModuleResult, RowFinding

_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"ignore\s+(?:all\s+)?previous\s+instructions", re.IGNORECASE),
    re.compile(r"abaikan\s+(?:semua\s+)?instruksi\s+sebelumnya", re.IGNORECASE),
    re.compile(
        r"system\s*prompt\s*[:=]|reveal\s+(?:the\s+)?(?:your\s+)?(?:system|hidden)\s*instructions",
        re.IGNORECASE,
    ),
    re.compile(r"\bDAN\b.*do\s+anything\s+now", re.IGNORECASE),
    re.compile(
        r"<\|im_start\|>\s*system\b|bypass\s+(?:all\s+)?(?:safety|policy|filters)",
        re.IGNORECASE,
    ),
]

_OVERCONFIDENT_HALLUCINATION_RE = re.compile(
    r"\b(?:100%\s+pasti|dijamin\s+benar\s+tanpa\s+sumber|menurut\s+penelitian\s+rahasia|"
    r"100%\s+guaranteed\s+fact|secret\s+unpublished\s+study)\b",
    re.IGNORECASE,
)

_HARMFUL_CONTENT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "cyber_exploitation",
        re.compile(
            r"\b(?:write\s+ransomware|buatkan\s+script\s+ransomware|phishing\s+kit|"
            r"steal\s+credentials|keylogger\s+payload)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "self_harm_instruction",
        re.compile(
            r"\b(?:cara\s+bunuh\s+diri|how\s+to\s+commit\s+suicide)\b",
            re.IGNORECASE,
        ),
    ),
]


def _query_local_hallucination_risk(
    client: httpx.Client,
    *,
    base_url: str,
    api_key: str,
    model: str,
    text: str,
) -> bool:
    """Optionally call a configured self-hosted OpenAI-compatible judge in-cluster."""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": model,
        "temperature": 0.0,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Balas HANYA 'RISIKO' atau 'AMAN': apakah teks berikut mengandung "
                    "klaim faktual palsu atau halusinasi tanpa bukti?"
                ),
            },
            {"role": "user", "content": text[:800]},
        ],
    }
    url = f"{base_url.rstrip('/')}/chat/completions"
    try:
        resp = client.post(url, headers=headers, json=payload)
        if resp.status_code >= 400:
            return False
        body = resp.json()
        choices = body.get("choices")
        if isinstance(choices, list) and choices:
            content = str(choices[0]["message"]["content"]).upper()
        else:
            content = str(body.get("response") or body.get("text") or "").upper()
        return "RISIKO" in content
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
        return False


class HallucinationAndInjectionModule:
    """Tags hallucination *risk*, prompt injection payloads, and E3 harmful taxonomy."""

    name = "hallucination_injection"
    dimension = "safety_bias"
    version = "0.2.0"

    def run(
        self,
        data: pl.LazyFrame,
        config: ModuleConfig,
        seed: int,
    ) -> tuple[ModuleResult, pl.DataFrame]:
        del seed
        enable_local_judge = bool(config.params.get("enable_local_llm_judge", False))
        settings = get_settings()
        use_local_judge = (
            enable_local_judge
            and settings.llm_provider != "placeholder"
            and bool(settings.llm_model)
            and (settings.allow_external_llm or is_local_llm_url(settings.llm_base_url))
        )

        df = data.select(["row_id", "text"]).collect()
        total_rows = df.height
        flagged_rows = 0
        findings: list[dict[str, object]] = []

        http_client: httpx.Client | None = httpx.Client(timeout=5.0) if use_local_judge else None
        try:
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

                for taxonomy, harm_re in _HARMFUL_CONTENT_PATTERNS:
                    if harm_re.search(text):
                        row_hit = True
                        findings.append(
                            RowFinding(
                                row_id=rid,
                                module=self.name,
                                code="HARMFUL_CONTENT_RISK",
                                severity="critical",
                                score=0.95,
                                detail={"taxonomy": taxonomy},
                            ).model_dump()
                        )
                        break

                heuristic_hallucination = bool(_OVERCONFIDENT_HALLUCINATION_RE.search(text))
                llm_flagged = (
                    not heuristic_hallucination
                    and http_client is not None
                    and _query_local_hallucination_risk(
                        http_client,
                        base_url=settings.llm_base_url,
                        api_key=settings.llm_api_key,
                        model=settings.llm_model,
                        text=text,
                    )
                )
                if heuristic_hallucination or llm_flagged:
                    row_hit = True
                    findings.append(
                        RowFinding(
                            row_id=rid,
                            module=self.name,
                            code="HALLUCINATION_RISK",
                            severity="warning",
                            score=0.65,
                            detail={
                                "note": "Risiko halusinasi (perlu verifikasi manusia)",
                                "judge_source": "local_llm" if llm_flagged else "heuristic",
                            },
                        ).model_dump()
                    )

                if row_hit:
                    flagged_rows += 1
        finally:
            if http_client is not None:
                http_client.close()

        risk_ratio = (flagged_rows / total_rows) if total_rows else 0.0
        result = ModuleResult(
            module=self.name,
            dimension=self.dimension,
            metrics={
                "total_rows": float(total_rows),
                "flagged_rows": float(flagged_rows),
                "toxic_ratio": round(risk_ratio, 4),
                "safety_clean_ratio": round(max(0.0, 1.0 - risk_ratio), 4),
            },
            findings_count=len(findings),
        )
        findings_df = pl.DataFrame(findings) if findings else pl.DataFrame()
        return result, findings_df
