"""Module A (A1-A4): Streaming ingestion, magic-bytes guard, mapping, and validation."""

import csv
import io
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import IO, Any

import polars as pl
from pypdf import PdfReader

ALLOWED_CHAT_ROLES = frozenset({"system", "user", "assistant"})
_BINARY_TEXT_SIGNATURES = (b"%PDF-", b"PAR1", b"\x89PNG", b"\x1f\x8b", b"PK\x03\x04")


@dataclass
class ValidationErrorItem:
    """Single syntax or schema validation error at a source line/row."""

    line_number: int
    error_type: str
    message: str


@dataclass
class IngestionOutput:
    """Normalized dataset DataFrame + validation report."""

    df: pl.DataFrame
    total_rows: int
    valid_rows: int
    invalid_rows: int
    detected_columns: list[str]
    errors: list[ValidationErrorItem]


def validate_content_magic(head: bytes, source_format: str) -> None:
    """Verify file magic bytes against declared `source_format` to reject spoofed uploads."""
    fmt = source_format.lower()
    if fmt == "parquet":
        if len(head) < 4 or head[:4] != b"PAR1":
            raise ValueError("Magic bytes berkas Parquet tidak valid (harus diawali PAR1).")
        return
    if fmt == "pdf":
        if b"%PDF-" not in head[:1024]:
            raise ValueError("Magic bytes berkas PDF tidak valid (harus diawali %PDF-).")
        return
    if fmt in {"jsonl", "csv", "txt", "md"}:
        trimmed = head.lstrip()
        if any(trimmed.startswith(sig) for sig in _BINARY_TEXT_SIGNATURES) or b"\x00" in head:
            raise ValueError(
                f"Berkas terdeteksi sebagai data biner, bukan format teks {source_format}."
            )


def _extract_chat_text(messages: Any) -> tuple[str, str, list[str]]:
    """Validate `messages` chat template and extract prompt/response text."""
    errs: list[str] = []
    if not isinstance(messages, list) or len(messages) == 0:
        return "", "", ["Kolom `messages` harus berupa array non-kosong."]

    user_parts: list[str] = []
    assistant_parts: list[str] = []
    for idx, msg in enumerate(messages):
        if not isinstance(msg, dict):
            errs.append(f"Pesan ke-{idx + 1} bukan objek JSON.")
            continue
        role = str(msg.get("role", "")).lower()
        content = msg.get("content")
        if role not in ALLOWED_CHAT_ROLES:
            errs.append(f"Role tidak valid pada pesan ke-{idx + 1}: {role!r}.")
        if not isinstance(content, str) or not content.strip():
            errs.append(f"Konten kosong pada pesan ke-{idx + 1}.")
            continue
        if role in {"system", "user"}:
            user_parts.append(content)
        elif role == "assistant":
            assistant_parts.append(content)

    return "\n".join(user_parts), "\n".join(assistant_parts), errs


def _iter_records_stream(
    stream: IO[bytes],
    fmt: str,
    errors: list[ValidationErrorItem],
) -> Iterator[dict[str, Any]]:
    """Yield raw record dictionaries line-by-line or per-block without full-file buffering."""
    if fmt in {"jsonl", "csv", "txt", "md"}:
        wrapper = io.TextIOWrapper(stream, encoding="utf-8", errors="replace", newline="")
        try:
            if fmt == "jsonl":
                for idx, line in enumerate(wrapper, start=1):
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        obj = json.loads(stripped)
                        if not isinstance(obj, dict):
                            errors.append(
                                ValidationErrorItem(idx, "INVALID_TYPE", "Baris JSON bukan objek.")
                            )
                            yield {"__line": idx, "__raw_invalid": True}
                        else:
                            obj["__line"] = idx
                            yield obj
                    except json.JSONDecodeError as exc:
                        errors.append(
                            ValidationErrorItem(
                                idx,
                                "INVALID_JSON",
                                f"Baris {idx} bukan JSON valid ({exc.msg}).",
                            )
                        )
                        yield {"__line": idx, "__raw_invalid": True}
            elif fmt == "csv":
                reader = csv.DictReader(wrapper)
                for idx, row in enumerate(reader, start=2):
                    rec: dict[str, Any] = dict(row)
                    rec["__line"] = idx
                    yield rec
            elif fmt == "txt":
                for idx, line in enumerate(wrapper, start=1):
                    stripped = line.strip()
                    if stripped:
                        yield {"__line": idx, "text": stripped}
            elif fmt == "md":
                block_lines: list[str] = []
                block_idx = 0
                for line in wrapper:
                    if not line.strip():
                        if block_lines:
                            block_idx += 1
                            yield {"__line": block_idx, "text": "\n".join(block_lines).strip()}
                            block_lines = []
                    else:
                        block_lines.append(line.rstrip("\r\n"))
                if block_lines:
                    block_idx += 1
                    yield {"__line": block_idx, "text": "\n".join(block_lines).strip()}
        finally:
            wrapper.detach()
        return

    raw_bytes = stream.read()
    if fmt == "parquet":
        try:
            pq_df = pl.read_parquet(io.BytesIO(raw_bytes))
        except Exception as exc:
            raise ValueError("Berkas PARQUET rusak atau tidak dapat dibaca.") from exc
        for idx, row in enumerate(pq_df.to_dicts(), start=1):
            row["__line"] = idx
            yield row
    elif fmt == "pdf":
        try:
            pdf_reader = PdfReader(io.BytesIO(raw_bytes))
        except Exception as exc:
            raise ValueError("Berkas PDF rusak atau tidak dapat dibaca.") from exc
        for page_no, page in enumerate(pdf_reader.pages, start=1):
            page_text = (page.extract_text() or "").strip()
            if not page_text:
                continue
            paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", page_text) if p.strip()]
            if not paragraphs:
                paragraphs = [page_text]
            for para in paragraphs:
                yield {"__line": page_no, "text": para}
    else:
        raise ValueError(f"Format tidak didukung: {fmt}")


def parse_and_validate_stream(
    stream: IO[bytes],
    source_format: str,
    dataset_type: str = "sft",
    column_mapping: dict[str, str] | None = None,
) -> IngestionOutput:
    """Stream-parse raw input, validate magic bytes and schema, and normalize to Polars."""
    fmt = source_format.lower()
    head = stream.read(1024)
    stream.seek(0)
    validate_content_magic(head, fmt)

    mapping = column_mapping or {}
    errors: list[ValidationErrorItem] = []

    role_to_col = {role: src for src, role in mapping.items()}
    prompt_col = role_to_col.get("prompt", "prompt")
    response_col = role_to_col.get("response", "response")
    messages_col = role_to_col.get("messages", "messages")
    text_col = role_to_col.get("text", "text")
    chosen_col = role_to_col.get("chosen", "chosen")
    rejected_col = role_to_col.get("rejected", "rejected")

    detected_cols: list[str] = []
    seen_cols: set[str] = set()
    normalized_rows: list[dict[str, Any]] = []
    invalid_count = 0

    for idx, rec in enumerate(_iter_records_stream(stream, fmt, errors)):
        if idx < 50:
            for col in rec:
                if isinstance(col, str) and not col.startswith("__") and col not in seen_cols:
                    seen_cols.add(col)
                    detected_cols.append(col)

        line_no = int(rec.get("__line", idx + 1))
        row_id = f"row_{idx + 1:06d}"

        if rec.get("__raw_invalid"):
            invalid_count += 1
            normalized_rows.append(
                {
                    "row_id": row_id,
                    "line_number": line_no,
                    "prompt": "",
                    "response": "",
                    "chosen": "",
                    "rejected": "",
                    "text": "",
                    "messages_json": "",
                    "is_valid": False,
                    "validation_error": "INVALID_JSON",
                    "curation_status": "flagged",
                }
            )
            continue

        row_errs: list[str] = []
        prompt_val = ""
        response_val = ""
        chosen_val = ""
        rejected_val = ""
        messages_json = ""
        text_val = ""

        has_pref_cols = chosen_col in rec or rejected_col in rec
        if messages_col in rec and rec[messages_col] is not None:
            raw_msgs = rec[messages_col]
            if isinstance(raw_msgs, str):
                try:
                    raw_msgs = json.loads(raw_msgs)
                except json.JSONDecodeError:
                    row_errs.append("Kolom `messages` bukan JSON array valid.")
                    raw_msgs = None
            if raw_msgs is not None:
                p_txt, r_txt, msg_errs = _extract_chat_text(raw_msgs)
                prompt_val, response_val = p_txt, r_txt
                messages_json = json.dumps(raw_msgs, ensure_ascii=False)
                row_errs.extend(msg_errs)
        elif has_pref_cols or dataset_type == "preference":
            p_raw = rec.get(prompt_col)
            c_raw = rec.get(chosen_col)
            rej_raw = rec.get(rejected_col)
            prompt_val = str(p_raw) if p_raw is not None else ""
            chosen_val = str(c_raw) if c_raw is not None else ""
            rejected_val = str(rej_raw) if rej_raw is not None else ""
            response_val = chosen_val
            if not chosen_val.strip():
                row_errs.append(f"Kolom `{chosen_col}` kosong atau tidak ada.")
            if not rejected_val.strip():
                row_errs.append(f"Kolom `{rejected_col}` kosong atau tidak ada.")
            if not text_val:
                text_val = "\n".join(
                    part for part in (prompt_val, chosen_val, rejected_val) if part
                ).strip()
        elif dataset_type == "raw" or (text_col in rec and prompt_col not in rec):
            raw_txt = rec.get(text_col, "")
            text_val = str(raw_txt) if raw_txt is not None else ""
            if not text_val.strip():
                row_errs.append("Teks kosong.")
        else:
            p_raw = rec.get(prompt_col)
            r_raw = rec.get(response_col)
            prompt_val = str(p_raw) if p_raw is not None else ""
            response_val = str(r_raw) if r_raw is not None else ""
            if not prompt_val.strip():
                row_errs.append(f"Kolom `{prompt_col}` kosong atau tidak ada.")
            if not response_val.strip():
                row_errs.append(f"Kolom `{response_col}` kosong atau tidak ada.")

        if not text_val:
            text_val = f"{prompt_val}\n{response_val}".strip()

        is_valid = len(row_errs) == 0
        if not is_valid:
            invalid_count += 1
            errors.append(
                ValidationErrorItem(
                    line_number=line_no,
                    error_type="SCHEMA_ERROR",
                    message="; ".join(row_errs),
                )
            )

        normalized_rows.append(
            {
                "row_id": row_id,
                "line_number": line_no,
                "prompt": prompt_val,
                "response": response_val,
                "chosen": chosen_val,
                "rejected": rejected_val,
                "text": text_val,
                "messages_json": messages_json,
                "is_valid": is_valid,
                "validation_error": "; ".join(row_errs),
                "curation_status": "pending" if is_valid else "flagged",
            }
        )

    schema = {
        "row_id": pl.Utf8,
        "line_number": pl.Int64,
        "prompt": pl.Utf8,
        "response": pl.Utf8,
        "chosen": pl.Utf8,
        "rejected": pl.Utf8,
        "text": pl.Utf8,
        "messages_json": pl.Utf8,
        "is_valid": pl.Boolean,
        "validation_error": pl.Utf8,
        "curation_status": pl.Utf8,
    }
    df = pl.DataFrame(normalized_rows, schema=schema)
    total = len(normalized_rows)
    return IngestionOutput(
        df=df,
        total_rows=total,
        valid_rows=total - invalid_count,
        invalid_rows=invalid_count,
        detected_columns=detected_cols,
        errors=errors,
    )


def parse_and_validate(
    raw_bytes: bytes,
    source_format: str,
    dataset_type: str = "sft",
    column_mapping: dict[str, str] | None = None,
) -> IngestionOutput:
    """Parse raw JSONL/CSV/Parquet/TXT bytes, apply column mapping, and validate schema."""
    return parse_and_validate_stream(
        io.BytesIO(raw_bytes),
        source_format=source_format,
        dataset_type=dataset_type,
        column_mapping=column_mapping,
    )
