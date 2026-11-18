"""Module A (A1-A4): Ingestion, format detection, column mapping, and schema/syntax validation."""

import csv
import io
import json
from dataclasses import dataclass
from typing import Any

import polars as pl

ALLOWED_CHAT_ROLES = frozenset({"system", "user", "assistant"})


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


def parse_and_validate(
    raw_bytes: bytes,
    source_format: str,
    dataset_type: str = "sft",
    column_mapping: dict[str, str] | None = None,
) -> IngestionOutput:
    """Parse raw JSONL/CSV/Parquet/TXT bytes, apply column mapping, and validate schema."""
    fmt = source_format.lower()
    mapping = column_mapping or {}
    errors: list[ValidationErrorItem] = []
    raw_records: list[dict[str, Any]] = []

    if fmt == "jsonl":
        text = raw_bytes.decode("utf-8", errors="replace")
        for idx, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
                if not isinstance(obj, dict):
                    errors.append(
                        ValidationErrorItem(idx, "INVALID_TYPE", "Baris JSON bukan objek.")
                    )
                    raw_records.append({"__line": idx, "__raw_invalid": True})
                else:
                    obj["__line"] = idx
                    raw_records.append(obj)
            except json.JSONDecodeError as exc:
                errors.append(
                    ValidationErrorItem(
                        idx,
                        "INVALID_JSON",
                        f"Baris {idx} bukan JSON valid ({exc.msg}).",
                    )
                )
                raw_records.append({"__line": idx, "__raw_invalid": True})
    elif fmt == "csv":
        text = raw_bytes.decode("utf-8", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        for idx, row in enumerate(reader, start=2):
            rec: dict[str, Any] = dict(row)
            rec["__line"] = idx
            raw_records.append(rec)
    elif fmt == "parquet":
        pq_df = pl.read_parquet(io.BytesIO(raw_bytes))
        for idx, row in enumerate(pq_df.to_dicts(), start=1):
            row["__line"] = idx
            raw_records.append(row)
    elif fmt == "txt":
        text = raw_bytes.decode("utf-8", errors="replace")
        for idx, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped:
                raw_records.append({"__line": idx, "text": stripped})
    else:
        raise ValueError(f"Format tidak didukung: {source_format}")

    detected_cols: list[str] = []
    if raw_records:
        seen_cols: set[str] = set()
        for rec in raw_records[:50]:
            for k in rec:
                if not k.startswith("__") and k not in seen_cols:
                    seen_cols.add(k)
                    detected_cols.append(k)

    # Reverse map: canonical_role -> source_column_name
    role_to_col = {role: src for src, role in mapping.items()}
    prompt_col = role_to_col.get("prompt", "prompt")
    response_col = role_to_col.get("response", "response")
    messages_col = role_to_col.get("messages", "messages")
    text_col = role_to_col.get("text", "text")

    normalized_rows: list[dict[str, Any]] = []
    invalid_count = 0

    for idx, rec in enumerate(raw_records):
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
        messages_json = ""
        text_val = ""

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
