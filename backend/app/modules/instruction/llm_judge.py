"""Module D2: Response alignment & helpfulness with editable rubric & seeded sampling."""

import hashlib
import random
from typing import Any

import httpx
import polars as pl

from app.core.config import get_settings
from app.core.security import is_local_llm_url
from app.modules.base import ModuleConfig, ModuleResult, RowFinding

DEFAULT_JUDGE_RUBRIC = (
    "Nilai kesesuaian jawaban terhadap instruksi (helpfulness, ketepatan format, kejelasan bahasa) "
    "pada rentang 0.0 hingga 1.0."
)


def _heuristic_rubric_score(
    prompt: str,
    response: str,
    criteria_weights: dict[str, Any] | None = None,
) -> float:
    """Deterministic local rubric fallback when LLM-judge is in placeholder mode."""
    p_words = set(prompt.lower().split())
    r_words = response.strip().split()
    if not r_words:
        return 0.0
    len_w = 0.55
    ovr_w = 0.45
    if isinstance(criteria_weights, dict):
        raw_lw = max(0.0, float(criteria_weights.get("length_weight", 0.55)))
        raw_ow = max(0.0, float(criteria_weights.get("overlap_weight", 0.45)))
        total_w = max(1e-6, raw_lw + raw_ow)
        len_w = raw_lw / total_w
        ovr_w = raw_ow / total_w
    length_score = min(1.0, len(r_words) / 15.0)
    overlap = len(p_words & {w.lower() for w in r_words}) / max(1, len(p_words))
    return round(min(1.0, len_w * length_score + ovr_w * min(1.0, overlap * 2.0)), 4)


def _call_openai_compat_judge(
    prompt: str,
    response: str,
    base_url: str,
    api_key: str,
    model: str,
    rubric: str = DEFAULT_JUDGE_RUBRIC,
    client: httpx.Client | None = None,
) -> float | None:
    """Synchronously score a single prompt/response pair via OpenAI-compatible endpoint."""
    try:
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
                        f"{rubric}\n"
                        "Score helpfulness and alignment from 0.0 to 1.0. "
                        "Reply with a single float only."
                    ),
                },
                {"role": "user", "content": f"Prompt: {prompt}\nResponse: {response}"},
            ],
        }
        url = f"{base_url.rstrip('/')}/chat/completions"
        if client is not None:
            resp = client.post(url, headers=headers, json=payload)
        else:
            resp = httpx.post(url, headers=headers, json=payload, timeout=10.0)
        resp.raise_for_status()
        content = str(resp.json()["choices"][0]["message"]["content"]).strip()
        val = float(content.split()[0])
        return max(0.0, min(1.0, val))
    except Exception:
        return None


class LlmJudgeModule:
    """Evaluates response alignment via editable rubric, seeded sampling, and LLM/placeholder."""

    name = "llm_judge"
    dimension = "instruction_quality"
    version = "0.2.0"

    def run(
        self,
        data: pl.LazyFrame,
        config: ModuleConfig,
        seed: int,
    ) -> tuple[ModuleResult, pl.DataFrame]:
        settings = get_settings()
        sample_limit = max(1, int(config.params.get("sample_limit", 50)))
        min_score = float(config.params.get("min_alignment_score", 0.4))
        rubric = str(config.params.get("rubric") or DEFAULT_JUDGE_RUBRIC).strip()
        rubric_hash = hashlib.sha256(rubric.encode("utf-8")).hexdigest()[:12]
        raw_cw = config.params.get("criteria_weights")
        criteria_weights = dict(raw_cw) if isinstance(raw_cw, dict) else None

        use_remote = (
            settings.llm_provider != "placeholder"
            and bool(settings.llm_model)
            and (settings.allow_external_llm or is_local_llm_url(settings.llm_base_url))
        )

        df = data.select(["row_id", "prompt", "response"]).collect()
        total_rows = df.height
        rows_list = list(df.iter_rows(named=True))

        if total_rows > sample_limit:
            rng = random.Random(seed)
            sampled_indices = sorted(rng.sample(range(total_rows), sample_limit))
        else:
            sampled_indices = list(range(total_rows))

        scores: list[float] = []
        findings: list[dict[str, object]] = []

        with httpx.Client(timeout=10.0) as http_client:
            for row_idx in sampled_indices:
                row = rows_list[row_idx]
                row_id = str(row["row_id"])
                prompt = str(row.get("prompt") or "")
                response = str(row.get("response") or "")

                score: float | None = None
                used_llm = False
                if use_remote:
                    score = _call_openai_compat_judge(
                        prompt,
                        response,
                        settings.llm_base_url,
                        settings.llm_api_key,
                        settings.llm_model,
                        rubric=rubric,
                        client=http_client,
                    )
                    used_llm = score is not None
                if score is None:
                    score = _heuristic_rubric_score(
                        prompt, response, criteria_weights=criteria_weights
                    )

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
                                "judge_mode": "llm" if used_llm else "placeholder_rubric",
                                "rubric_hash": rubric_hash,
                                "sampled_row_index": row_idx,
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
                "sampled_rows": float(len(sampled_indices)),
                "avg_judge_score": avg_score,
                "low_alignment_rows": float(len(findings)),
            },
            findings_count=len(findings),
        )
        findings_df = pl.DataFrame(findings) if findings else pl.DataFrame()
        return result, findings_df
