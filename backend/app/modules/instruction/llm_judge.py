"""Module D2: Response alignment & helpfulness (OpenAI-compatible local/free API + placeholder).

Respects CLAUDE.md 0.1 #3 (privacy-first):
- When `LLM_PROVIDER == "placeholder"` or (`ALLOW_EXTERNAL_LLM == False` and endpoint is not
  localhost), runs a deterministic heuristic rubric locally without making external network calls.
- When configured to a local Ollama/vLLM or explicitly opted-in free API, calls `/chat/completions`.
"""

import httpx
import polars as pl

from app.core.config import get_settings
from app.modules.base import ModuleConfig, ModuleResult, RowFinding


def _heuristic_rubric_score(prompt: str, response: str) -> float:
    """Deterministic local rubric fallback when LLM-judge is in placeholder mode."""
    p_words = set(prompt.lower().split())
    r_words = response.strip().split()
    if not r_words:
        return 0.0
    length_score = min(1.0, len(r_words) / 15.0)
    overlap = len(p_words & set(w.lower() for w in r_words)) / max(1, len(p_words))
    return round(min(1.0, 0.55 * length_score + 0.45 * min(1.0, overlap * 2.0)), 4)


def _call_openai_compat_judge(
    prompt: str, response: str, base_url: str, api_key: str, model: str
) -> float | None:
    """Synchronously score a single prompt/response pair via OpenAI-compatible endpoint."""
    try:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        resp = httpx.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers=headers,
            json={
                "model": model,
                "temperature": 0.0,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Score helpfulness and alignment from 0.0 to 1.0. "
                            "Reply with a single float only."
                        ),
                    },
                    {"role": "user", "content": f"Prompt: {prompt}\nResponse: {response}"},
                ],
            },
            timeout=10.0,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()
        val = float(content.split()[0])
        return max(0.0, min(1.0, val))
    except Exception:
        return None


class LlmJudgeModule:
    """Evaluates response alignment and helpfulness via configurable LLM-judge or placeholder."""

    name = "llm_judge"
    dimension = "instruction_quality"
    version = "0.1.0"

    def run(
        self,
        data: pl.LazyFrame,
        config: ModuleConfig,
        seed: int,
    ) -> tuple[ModuleResult, pl.DataFrame]:
        del seed
        settings = get_settings()
        sample_limit = int(config.params.get("sample_limit", 50))
        min_score = float(config.params.get("min_alignment_score", 0.4))

        use_remote = (
            settings.llm_provider != "placeholder"
            and bool(settings.llm_model)
            and (
                settings.allow_external_llm
                or "localhost" in settings.llm_base_url
                or "127.0.0.1" in settings.llm_base_url
            )
        )

        df = data.select(["row_id", "prompt", "response"]).collect()
        total_rows = df.height
        scores: list[float] = []
        findings: list[dict[str, object]] = []

        for idx, row in enumerate(df.iter_rows(named=True)):
            row_id = str(row["row_id"])
            prompt = str(row.get("prompt") or "")
            response = str(row.get("response") or "")

            score: float | None = None
            if use_remote and idx < sample_limit:
                score = _call_openai_compat_judge(
                    prompt,
                    response,
                    settings.llm_base_url,
                    settings.llm_api_key,
                    settings.llm_model,
                )
            if score is None:
                score = _heuristic_rubric_score(prompt, response)

            scores.append(score)
            if score < min_score:
                findings.append(
                    RowFinding(
                        row_id=row_id,
                        module=self.name,
                        code="LOW_ALIGNMENT_SCORE",
                        severity="warning",
                        score=score,
                        detail={
                            "judge_mode": "llm" if use_remote else "placeholder_rubric",
                            "score": score,
                        },
                    ).model_dump()
                )

        avg_score = round(sum(scores) / len(scores), 4) if scores else 1.0
        result = ModuleResult(
            module=self.name,
            dimension=self.dimension,
            metrics={
                "total_rows": float(total_rows),
                "avg_judge_score": avg_score,
                "low_alignment_rows": float(len(findings)),
            },
            findings_count=len(findings),
        )
        findings_df = pl.DataFrame(findings) if findings else pl.DataFrame()
        return result, findings_df
