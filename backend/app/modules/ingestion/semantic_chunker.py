"""Automatic semantic chunking and PII-safe metadata enrichment for RAG / Vector DBs."""

import json
import re
from collections import Counter
from typing import Any

from app.modules.cleaning.language import detect_language
from app.modules.cleaning.pii import apply_pii_transform, detect_pii_spans
from app.modules.ingestion.token_analytics import estimate_token_count
from app.modules.semantic.embeddings import cosine_similarity, embed_texts

_MD_HEADING_RE = re.compile(r"^#{1,6}\s+(.+)$")
_NUM_HEADING_RE = re.compile(r"^(?:BAB|Pasal|Section|Bagian|\d+(?:\.\d+)*)\s+[A-Z0-9].{2,80}$")
_SENTENCE_SPLIT_RE = re.compile(
    r"(?<!\bNo)(?<!\bJl)(?<!\bDr)(?<!\bBpk)(?<!\bIbu)(?<!\bSdr)(?<!\ba\.n)"
    r"(?<!\be\.g)(?<!\bi\.e)(?<!\betc)(?<=[.!?])\s+"
)
_MASKED_TOKEN_RE = re.compile(
    r"\[(?:NIK|NPWP|TELEPON|EMAIL|REKENING|KARTU|NAMA|ALAMAT|PII_[A-Z_]+:[0-9a-f]+)\]"
)
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}")
_STOPWORDS = frozenset(
    {
        "yang",
        "dan",
        "di",
        "ke",
        "dari",
        "ini",
        "itu",
        "untuk",
        "dengan",
        "adalah",
        "pada",
        "tidak",
        "bisa",
        "saya",
        "anda",
        "kita",
        "atau",
        "jika",
        "karena",
        "apa",
        "dalam",
        "oleh",
        "sebagai",
        "tersebut",
        "the",
        "and",
        "for",
        "with",
        "from",
        "that",
        "this",
        "are",
        "was",
        "were",
        "have",
        "has",
        "you",
        "your",
        "not",
        "can",
        "will",
        "into",
    }
)


def split_into_segments(text: str, *, include_headings: bool = True) -> list[tuple[str, str]]:
    """Split raw document text into `(section_title, segment_text)` tuples."""
    if not text.strip():
        return []

    segments: list[tuple[str, str]] = []
    current_section = ""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", text) if p.strip()]
    for para in paragraphs:
        lines = [ln.strip() for ln in para.splitlines() if ln.strip()]
        body_lines: list[str] = []
        for ln in lines:
            md_match = _MD_HEADING_RE.match(ln)
            if include_headings and md_match:
                if body_lines:
                    joined = " ".join(body_lines)
                    for sent in _SENTENCE_SPLIT_RE.split(joined):
                        if sent.strip():
                            segments.append((current_section, sent.strip()))
                    body_lines = []
                current_section = md_match.group(1).strip()
            elif include_headings and len(lines) == 1 and _NUM_HEADING_RE.match(ln):
                current_section = ln
            else:
                body_lines.append(ln)

        if body_lines:
            joined = " ".join(body_lines)
            for sent in _SENTENCE_SPLIT_RE.split(joined):
                if sent.strip():
                    segments.append((current_section, sent.strip()))
    return segments


def extract_metadata_keywords(text: str, top_k: int = 5) -> list[str]:
    """Extract top deterministic keywords after masking any PII spans."""
    masked = apply_pii_transform(text, mode="mask")
    scrubbed = _MASKED_TOKEN_RE.sub(" ", masked)
    words = [
        w.lower() for w in _WORD_RE.findall(scrubbed) if w.lower() not in _STOPWORDS and len(w) >= 3
    ]
    if not words:
        return []
    counts = Counter(words)
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [term for term, _cnt in ranked[:top_k]]


def enrich_chunk_metadata(
    chunk_text: str,
    *,
    chunk_id: str,
    chunk_index: int,
    source_row_ids: list[str],
    page_numbers: list[int],
    section_title: str,
    coherence_score: float,
    embedding_backend: str,
    mask_pii_in_metadata: bool = True,
) -> dict[str, Any]:
    """Build Vector-DB-ready metadata payload with optional PII sanitization."""
    has_pii = bool(detect_pii_spans(chunk_text))
    safe_section = (
        apply_pii_transform(section_title, mode="mask") if mask_pii_in_metadata else section_title
    )
    has_table_cue = bool(
        re.search(r"\|.+\|", chunk_text)
        or chunk_text.count("\t") >= 2
        or bool(re.search(r"\S\s{3,}\S", chunk_text))
    )
    return {
        "chunk_id": chunk_id,
        "chunk_index": chunk_index,
        "language": detect_language(chunk_text),
        "token_count": estimate_token_count(chunk_text),
        "char_count": len(chunk_text),
        "keywords": extract_metadata_keywords(chunk_text, top_k=5),
        "has_pii": has_pii,
        "has_code_block": "```" in chunk_text,
        "has_table_cue": has_table_cue,
        "section_title": safe_section,
        "page_numbers": sorted(set(page_numbers)),
        "source_row_ids": list(dict.fromkeys(source_row_ids)),
        "coherence_score": round(max(0.0, min(1.0, coherence_score)), 4),
        "chunking_strategy": "semantic",
        "embedding_backend": embedding_backend,
    }


def _split_oversized_segment(segment: str, max_tokens: int) -> list[str]:
    words = segment.split()
    if not words:
        return []
    parts: list[str] = []
    cur: list[str] = []
    for word in words:
        cand = " ".join([*cur, word])
        if cur and estimate_token_count(cand) > max_tokens:
            parts.append(" ".join(cur))
            cur = [word]
        else:
            cur.append(word)
    if cur:
        parts.append(" ".join(cur))
    return parts


def semantic_chunk_rows(
    rows: list[dict[str, Any]],
    *,
    max_chunk_tokens: int = 512,
    min_chunk_tokens: int = 64,
    similarity_threshold: float = 0.55,
    overlap_sentences: int = 1,
    include_headings: bool = True,
    mask_pii_in_metadata: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Group row segments by embedding similarity and token bounds with enriched metadata."""
    seg_records: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        rid = str(row.get("row_id") or f"row_{idx + 1:06d}")
        raw_line = row.get("line_number", idx + 1)
        page_no = int(raw_line) if isinstance(raw_line, int) and raw_line >= 1 else idx + 1
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        for sec, seg in split_into_segments(text, include_headings=include_headings):
            for piece in _split_oversized_segment(seg, max_chunk_tokens):
                seg_records.append(
                    {"row_id": rid, "page_number": page_no, "section": sec, "text": piece}
                )

    if not seg_records:
        return [], {
            "source_row_count": len(rows),
            "chunk_count": 0,
            "avg_tokens_per_chunk": 0.0,
            "avg_coherence_score": 1.0,
            "embedding_backend": "local_hash",
        }

    seg_texts = [str(s["text"]) for s in seg_records]
    vectors, backend = embed_texts(seg_texts)

    adj_sims: list[float] = [1.0]
    for i in range(1, len(vectors)):
        sim = max(0.0, min(1.0, cosine_similarity(vectors[i - 1], vectors[i])))
        adj_sims.append(sim)

    groups: list[list[int]] = []
    current_group: list[int] = [0]
    current_tokens = estimate_token_count(seg_texts[0])

    for i in range(1, len(seg_records)):
        prev_sec = str(seg_records[i - 1]["section"])
        cur_sec = str(seg_records[i]["section"])
        seg_toks = estimate_token_count(seg_texts[i])
        sec_changed = include_headings and cur_sec != prev_sec
        exceeds_max = current_tokens + seg_toks > max_chunk_tokens
        topic_shift = adj_sims[i] < similarity_threshold and current_tokens >= min_chunk_tokens

        if sec_changed or exceeds_max or topic_shift:
            groups.append(current_group)
            current_group = [i]
            current_tokens = seg_toks
        else:
            current_group.append(i)
            current_tokens += seg_toks
    if current_group:
        groups.append(current_group)

    chunk_rows: list[dict[str, Any]] = []
    token_totals: list[int] = []
    coherence_scores: list[float] = []

    for g_idx, indices in enumerate(groups):
        chunk_section = str(seg_records[indices[0]]["section"])
        chunk_segments = [seg_texts[i] for i in indices]

        if overlap_sentences > 0 and g_idx > 0:
            prev_indices = groups[g_idx - 1]
            prev_section = str(seg_records[prev_indices[-1]]["section"])
            if prev_section == chunk_section:
                carry = [seg_texts[i] for i in prev_indices[-overlap_sentences:]]
                cand_text = " ".join([*carry, *chunk_segments])
                if estimate_token_count(cand_text) <= max_chunk_tokens:
                    chunk_segments = [*carry, *chunk_segments]

        chunk_text = " ".join(chunk_segments).strip()
        src_rids = [str(seg_records[i]["row_id"]) for i in indices]
        pages = [int(seg_records[i]["page_number"]) for i in indices]
        internal_sims = [adj_sims[i] for i in indices[1:]] if len(indices) > 1 else [1.0]
        coherence = round(sum(internal_sims) / len(internal_sims), 4)

        chunk_id = f"row_{g_idx + 1:06d}"
        meta = enrich_chunk_metadata(
            chunk_text,
            chunk_id=chunk_id,
            chunk_index=g_idx,
            source_row_ids=src_rids,
            page_numbers=pages,
            section_title=chunk_section,
            coherence_score=coherence,
            embedding_backend=backend,
            mask_pii_in_metadata=mask_pii_in_metadata,
        )
        token_totals.append(int(meta["token_count"]))
        coherence_scores.append(float(meta["coherence_score"]))

        chunk_rows.append(
            {
                "row_id": chunk_id,
                "line_number": pages[0] if pages else g_idx + 1,
                "prompt": "",
                "response": "",
                "chosen": "",
                "rejected": "",
                "text": chunk_text,
                "messages_json": "",
                "metadata_json": json.dumps(meta, ensure_ascii=False),
                "is_valid": bool(chunk_text),
                "validation_error": "",
                "curation_status": "pending",
            }
        )

    summary = {
        "source_row_count": len(rows),
        "chunk_count": len(chunk_rows),
        "avg_tokens_per_chunk": round(sum(token_totals) / len(token_totals), 2)
        if token_totals
        else 0.0,
        "avg_coherence_score": round(sum(coherence_scores) / len(coherence_scores), 4)
        if coherence_scores
        else 1.0,
        "embedding_backend": backend,
    }
    return chunk_rows, summary
