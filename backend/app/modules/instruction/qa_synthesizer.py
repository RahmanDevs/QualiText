"""Automatic grounded QA / SFT instruction pair synthesis from raw or chunked documents."""

import json
import random
import re
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.security import is_local_llm_url
from app.modules.cleaning.language import detect_language
from app.modules.cleaning.pii import apply_pii_transform
from app.modules.ingestion.semantic_chunker import extract_metadata_keywords

_ID_DEF_RE = re.compile(
    r"\b([A-Za-z][A-Za-z0-9\s/-]{2,50}?)\s+(?:adalah|merupakan|yaitu)\s+([^.!?]+[.!?]?)",
    re.IGNORECASE,
)
_EN_DEF_RE = re.compile(
    r"\b([A-Za-z][A-Za-z0-9\s/-]{2,50}?)\s+(?:is|are|refers\s+to|means)\s+([^.!?]+[.!?]?)",
    re.IGNORECASE,
)
_STEP_CUE_RE = re.compile(
    r"(?:(?:^|\n)\s*(?:\d+\.|[-*])\s+|\b(?:langkah|cara|prosedur|syarat|steps|how\s+to)\b)",
    re.IGNORECASE,
)
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_FENCE_JSON_RE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.IGNORECASE | re.DOTALL)


def _resolve_target_lang(text: str, requested_lang: str) -> str:
    if requested_lang in {"id", "en"}:
        return requested_lang
    detected = detect_language(text)
    return "en" if detected == "en" else "id"


def _generate_local_qa_pairs(
    text: str,
    *,
    language: str,
    pairs_per_row: int,
    section_title: str = "",
    seed: int = 42,
) -> list[dict[str, str]]:
    """Deterministically synthesize grounded QA pairs without external LLM calls."""
    cleaned = text.strip()
    if len(cleaned) < 15:
        return []

    lang = _resolve_target_lang(cleaned, language)
    sentences = [s.strip() for s in _SENTENCE_RE.split(cleaned) if len(s.strip()) >= 12]
    if not sentences:
        sentences = [cleaned]

    candidates: list[dict[str, str]] = []
    def_re = _EN_DEF_RE if lang == "en" else _ID_DEF_RE
    for match in def_re.finditer(cleaned):
        subject = " ".join(match.group(1).split()).strip(" ,:;-")
        if len(subject) < 3 or len(subject.split()) > 6:
            continue
        answer_sent = match.group(0).strip()
        prompt = (
            f"What is {subject} according to the document?"
            if lang == "en"
            else f"Apa yang dimaksud dengan {subject} berdasarkan dokumen?"
        )
        candidates.append({"prompt": prompt, "response": answer_sent, "qa_type": "definitional"})
        break

    if _STEP_CUE_RE.search(cleaned):
        topic = section_title or (
            ", ".join(extract_metadata_keywords(cleaned, top_k=2))
            or ("this topic" if lang == "en" else "topik ini")
        )
        prompt = (
            f"What steps or requirements are described for {topic}?"
            if lang == "en"
            else f"Sebutkan langkah atau ketentuan terkait {topic}."
        )
        candidates.append({"prompt": prompt, "response": cleaned, "qa_type": "procedural"})

    keywords = extract_metadata_keywords(cleaned, top_k=3)
    topic_label = section_title or (", ".join(keywords[:2]) if keywords else "")
    if topic_label:
        prompt_sum = (
            f"Summarize the key points regarding {topic_label}."
            if lang == "en"
            else f"Ringkas poin utama mengenai {topic_label} dari teks berikut."
        )
    else:
        prompt_sum = (
            "Summarize the main information conveyed in this passage."
            if lang == "en"
            else "Ringkas informasi utama yang disampaikan pada teks berikut."
        )
    summary_resp = " ".join(sentences[:2]).strip()
    candidates.append({"prompt": prompt_sum, "response": summary_resp, "qa_type": "summary"})

    if keywords:
        focus = keywords[0]
        prompt_fact = (
            f"Explain the information related to '{focus}' in this document."
            if lang == "en"
            else f"Jelaskan informasi terkait '{focus}' berdasarkan dokumen ini."
        )
        matching_sents = [s for s in sentences if focus in s.lower()]
        fact_resp = " ".join(matching_sents[:2]).strip() if matching_sents else summary_resp
        candidates.append({"prompt": prompt_fact, "response": fact_resp, "qa_type": "factual"})

    rng = random.Random(seed)
    if len(candidates) > pairs_per_row:
        head = candidates[:1]
        tail = candidates[1:]
        rng.shuffle(tail)
        candidates = head + tail

    deduped: list[dict[str, str]] = []
    seen_prompts: set[str] = set()
    for item in candidates:
        p_norm = item["prompt"].strip().lower()
        r_clean = item["response"].strip()
        if not p_norm or not r_clean or p_norm in seen_prompts:
            continue
        seen_prompts.add(p_norm)
        deduped.append(item)
        if len(deduped) >= pairs_per_row:
            break
    return deduped


def _call_llm_qa_generator(
    client: httpx.Client,
    *,
    base_url: str,
    api_key: str,
    model: str,
    text: str,
    language: str,
    pairs_per_row: int,
) -> list[dict[str, str]] | None:
    """Call OpenAI-compatible LLM to synthesize grounded instruction/response pairs."""
    lang_hint = "English" if language == "en" else "Indonesian"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    sys_prompt = (
        f"Generate up to {pairs_per_row} grounded instruction/QA pairs in {lang_hint} strictly "
        "from the provided source text without inventing external facts. "
        'Reply ONLY with a JSON array: [{"prompt": "...", "response": "...", "qa_type": "..."}].'
    )
    payload = {
        "model": model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": text[:2000]},
        ],
    }
    url = f"{base_url.rstrip('/')}/chat/completions"
    try:
        resp = client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        raw = str(resp.json()["choices"][0]["message"]["content"]).strip()
        fence = _FENCE_JSON_RE.search(raw)
        if fence:
            raw = fence.group(1).strip()
        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            return None
        pairs: list[dict[str, str]] = []
        for item in parsed[:pairs_per_row]:
            if not isinstance(item, dict):
                continue
            p_val = str(item.get("prompt") or "").strip()
            r_val = str(item.get("response") or "").strip()
            q_type = str(item.get("qa_type") or "llm_synthesized").strip()
            if p_val and r_val:
                pairs.append({"prompt": p_val, "response": r_val, "qa_type": q_type})
        return pairs if pairs else None
    except Exception:
        return None


def synthesize_qa_pairs_from_rows(
    rows: list[dict[str, Any]],
    *,
    pairs_per_row: int = 2,
    max_source_rows: int = 100,
    language: str = "auto",
    mode: str = "auto",
    mask_pii: bool = True,
    include_system_prompt: bool = True,
    seed: int = 42,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Synthesize grounded SFT QA/instruction pairs from raw or chunked rows."""
    settings = get_settings()
    remote_allowed = (
        settings.llm_provider != "placeholder"
        and bool(settings.llm_model)
        and (settings.allow_external_llm or is_local_llm_url(settings.llm_base_url))
    )
    use_llm = mode in {"auto", "llm"} and remote_allowed

    sft_rows: list[dict[str, Any]] = []
    llm_count = 0
    local_count = 0
    processed_sources = 0

    http_client: httpx.Client | None = httpx.Client(timeout=15.0) if use_llm else None
    try:
        for idx, row in enumerate(rows[:max_source_rows]):
            raw_text = str(row.get("text") or "").strip()
            if not raw_text:
                continue
            processed_sources += 1
            src_rid = str(row.get("row_id") or f"row_{idx + 1:06d}")
            src_line = int(row.get("line_number") or idx + 1)

            section_title = ""
            raw_meta = row.get("metadata_json")
            if isinstance(raw_meta, str) and raw_meta.strip():
                try:
                    parsed_meta = json.loads(raw_meta)
                    if isinstance(parsed_meta, dict):
                        section_title = str(parsed_meta.get("section_title") or "")
                except json.JSONDecodeError:
                    section_title = ""

            source_text = apply_pii_transform(raw_text, mode="mask") if mask_pii else raw_text
            if mask_pii and section_title:
                section_title = apply_pii_transform(section_title, mode="mask")

            resolved_lang = _resolve_target_lang(source_text, language)
            generated: list[dict[str, str]] | None = None
            row_gen_mode = "local_template"

            if http_client is not None:
                generated = _call_llm_qa_generator(
                    http_client,
                    base_url=settings.llm_base_url,
                    api_key=settings.llm_api_key,
                    model=settings.llm_model,
                    text=source_text,
                    language=resolved_lang,
                    pairs_per_row=pairs_per_row,
                )
                if generated:
                    row_gen_mode = "llm"
                    llm_count += len(generated)

            if not generated:
                generated = _generate_local_qa_pairs(
                    source_text,
                    language=resolved_lang,
                    pairs_per_row=pairs_per_row,
                    section_title=section_title,
                    seed=seed + idx,
                )
                local_count += len(generated)

            sys_msg = (
                "You are a helpful, factual corporate assistant."
                if resolved_lang == "en"
                else "Anda adalah asisten korporat yang membantu dan menjawab berdasarkan dokumen."
            )
            for pair in generated:
                row_idx = len(sft_rows) + 1
                p_txt = pair["prompt"]
                r_txt = pair["response"]
                msgs: list[dict[str, str]] = []
                if include_system_prompt:
                    msgs.append({"role": "system", "content": sys_msg})
                msgs.append({"role": "user", "content": p_txt})
                msgs.append({"role": "assistant", "content": r_txt})

                meta = {
                    "source_row_id": src_rid,
                    "qa_type": pair["qa_type"],
                    "language": resolved_lang,
                    "generation_mode": row_gen_mode,
                    "grounded": True,
                }
                sft_rows.append(
                    {
                        "row_id": f"row_{row_idx:06d}",
                        "line_number": src_line,
                        "prompt": p_txt,
                        "response": r_txt,
                        "chosen": "",
                        "rejected": "",
                        "text": f"{p_txt}\n{r_txt}".strip(),
                        "messages_json": json.dumps(msgs, ensure_ascii=False),
                        "metadata_json": json.dumps(meta, ensure_ascii=False),
                        "is_valid": True,
                        "validation_error": "",
                        "curation_status": "pending",
                    }
                )
    finally:
        if http_client is not None:
            http_client.close()

    if llm_count > 0 and local_count > 0:
        overall_mode = "hybrid"
    elif llm_count > 0:
        overall_mode = "llm"
    else:
        overall_mode = "local_template"

    summary = {
        "source_rows_processed": processed_sources,
        "generated_pairs_count": len(sft_rows),
        "generation_mode": overall_mode,
        "pii_masked": mask_pii,
    }
    return sft_rows, summary
