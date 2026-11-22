"""AI Repairing & deterministic PDF/OCR parsing cleanup service (Human-in-the-loop)."""

import re
import unicodedata
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.security import is_local_llm_url
from app.modules.cleaning.artifacts import clean_text_artifacts

_HYPHEN_BREAK_RE = re.compile(r"(\w)-\s*\n\s*(\w)")
_SINGLE_NEWLINE_RE = re.compile(r"(?<!\n)\n(?!\n)")
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")


def deterministic_pdf_repair(text: str) -> str:
    """Fix common PDF extraction artifacts: hyphenated wraps, broken newlines, and spacing."""
    repaired = unicodedata.normalize("NFKC", text)
    repaired = _HYPHEN_BREAK_RE.sub(r"\1\2", repaired)
    repaired = _SINGLE_NEWLINE_RE.sub(" ", repaired)
    repaired = clean_text_artifacts(repaired)
    repaired = _MULTI_SPACE_RE.sub(" ", repaired).strip()
    return repaired


def _prepare_llm_request(
    local_cleaned: str, instruction: str | None
) -> tuple[str, dict[str, str], dict[str, Any]] | None:
    """Build OpenAI-compatible request tuple when remote/local LLM is explicitly enabled."""
    settings = get_settings()
    use_remote = (
        settings.llm_provider != "placeholder"
        and bool(settings.llm_model)
        and (settings.allow_external_llm or is_local_llm_url(settings.llm_base_url))
    )
    if not use_remote:
        return None

    sys_prompt = (
        "Perbaiki teks hasil parsing PDF yang rusak (kata terpotong, spasi salah, atau typo OCR) "
        "tanpa mengubah makna fakta aslinya. Kembalikan hanya teks yang sudah diperbaiki."
    )
    if instruction:
        sys_prompt = f"{sys_prompt} Instruksi tambahan: {instruction}"

    headers = {"Content-Type": "application/json"}
    if settings.llm_api_key:
        headers["Authorization"] = f"Bearer {settings.llm_api_key}"

    url = f"{settings.llm_base_url.rstrip('/')}/chat/completions"
    body: dict[str, Any] = {
        "model": settings.llm_model,
        "temperature": 0.1,
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": local_cleaned},
        ],
    }
    return url, headers, body


async def repair_parsed_text_async(text: str, instruction: str | None = None) -> tuple[str, str]:
    """Return `(repaired_text, mode)` via configured LLM or deterministic fallback."""
    local_cleaned = deterministic_pdf_repair(text)
    req = _prepare_llm_request(local_cleaned, instruction)
    if req is None:
        return local_cleaned, "local_heuristic"

    url, headers, body = req
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            ai_text = str(resp.json()["choices"][0]["message"]["content"]).strip()
            if ai_text:
                return ai_text, "llm"
    except Exception:
        pass

    return local_cleaned, "local_heuristic"
