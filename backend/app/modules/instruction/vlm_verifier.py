"""Module D6: VLM & structural verification of PDF text/table extraction against page visuals."""

import base64
import importlib
import importlib.util
import io
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import httpx
import polars as pl
from pypdf import PdfReader

from app.core.config import get_settings
from app.core.security import is_local_llm_url
from app.modules.base import ModuleConfig, ModuleResult, RowFinding
from app.modules.cleaning.pii import apply_pii_transform
from app.storage.s3 import get_storage_backend

_CID_GLYPH_RE = re.compile(r"\(cid:\d+\)|\ufffd")
_VECTOR_RECT_RE = re.compile(r"(?:^|\s)[-+]?\d*\.?\d+(?:\s+[-+]?\d*\.?\d+){3}\s+re\b")
_VECTOR_LINE_RE = re.compile(r"(?:^|\s)[-+]?\d*\.?\d+\s+[-+]?\d*\.?\d+\s+[ml]\b")
_MULTICOL_LINE_RE = re.compile(r"\S\s{3,}\S")
_WORD_RE = re.compile(r"[A-Za-z0-9_]+")
_FENCE_JSON_RE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.IGNORECASE | re.DOTALL)


def _detect_image_mime(raw: bytes) -> str:
    if raw.startswith(b"\x89PNG"):
        return "image/png"
    if raw.startswith(b"\xff\xd8"):
        return "image/jpeg"
    return "image/png"


def render_pdf_pages_to_png(
    pdf_bytes: bytes,
    *,
    max_pages: int = 20,
    dpi: int = 150,
) -> tuple[dict[int, bytes], str]:
    """Render PDF pages to PNG bytes via optional rasterizers or extract embedded page images."""
    if importlib.util.find_spec("pypdfium2") is not None:
        try:
            pdfium = importlib.import_module("pypdfium2")
            doc = pdfium.PdfDocument(pdf_bytes)
            pages_out: dict[int, bytes] = {}
            scale = max(0.5, dpi / 72.0)
            limit = min(len(doc), max_pages)
            for idx in range(limit):
                page = doc[idx]
                bitmap = page.render(scale=scale)
                pil_img = bitmap.to_pil()
                buf = io.BytesIO()
                pil_img.save(buf, format="PNG")
                pages_out[idx + 1] = buf.getvalue()
            if pages_out:
                return pages_out, "pypdfium2"
        except Exception:
            pass

    if importlib.util.find_spec("fitz") is not None:
        try:
            fitz = importlib.import_module("fitz")
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            pages_out = {}
            zoom = max(0.5, dpi / 72.0)
            mat = fitz.Matrix(zoom, zoom)
            limit = min(len(doc), max_pages)
            for idx in range(limit):
                pix = doc[idx].get_pixmap(matrix=mat)
                pages_out[idx + 1] = bytes(pix.tobytes("png"))
            if pages_out:
                return pages_out, "pymupdf"
        except Exception:
            pass

    pdftoppm_bin = shutil.which("pdftoppm")
    if pdftoppm_bin:
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_path = Path(tmpdir)
                pdf_path = tmp_path / "input.pdf"
                pdf_path.write_bytes(pdf_bytes)
                prefix = tmp_path / "page"
                subprocess.run(
                    [
                        pdftoppm_bin,
                        "-png",
                        "-r",
                        str(dpi),
                        "-f",
                        "1",
                        "-l",
                        str(max_pages),
                        str(pdf_path),
                        str(prefix),
                    ],
                    check=True,
                    capture_output=True,
                    timeout=30,
                )
                pages_out = {}
                for img_file in sorted(tmp_path.glob("page-*.png")):
                    suffix = img_file.stem.rsplit("-", 1)[-1]
                    if suffix.isdigit():
                        pages_out[int(suffix)] = img_file.read_bytes()
                if pages_out:
                    return pages_out, "pdftoppm"
        except Exception:
            pass

    # Air-gapped fallback: extract embedded page images via pypdf
    pages_out = {}
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        for page_idx, page in enumerate(reader.pages[:max_pages], start=1):
            try:
                imgs = getattr(page, "images", [])
                for img in imgs:
                    data = getattr(img, "data", b"")
                    if isinstance(data, (bytes, bytearray)) and len(data) > 0:
                        pages_out[page_idx] = bytes(data)
                        break
            except Exception:
                continue
    except Exception:
        pass
    return pages_out, "pypdf_inspector"


def _count_embedded_images(page: Any) -> int:
    count = 0
    try:
        count = len(getattr(page, "images", []))
    except Exception:
        count = 0

    try:
        resources = page.get("/Resources")
        if hasattr(resources, "get_object"):
            resources = resources.get_object()
        if isinstance(resources, dict):
            xobj = resources.get("/XObject")
            if xobj is not None and hasattr(xobj, "get_object"):
                xobj = xobj.get_object()
            if isinstance(xobj, dict):
                xobj_imgs = 0
                for val in xobj.values():
                    resolved = val.get_object() if hasattr(val, "get_object") else val
                    if isinstance(resolved, dict) and str(resolved.get("/Subtype")) == "/Image":
                        xobj_imgs += 1
                count = max(count, xobj_imgs)
    except Exception:
        pass
    return count


def _read_page_content_stream(page: Any) -> str:
    try:
        contents = page.get_contents()
        if contents is None:
            return ""
        if isinstance(contents, list):
            chunks = [bytes(item.get_object().get_data()) for item in contents]
            return b"\n".join(chunks).decode("latin-1", errors="ignore")
        return bytes(contents.get_data()).decode("latin-1", errors="ignore")
    except Exception:
        return ""


def _inspect_pdf_page_structure(page: Any, extracted_page_text: str) -> dict[str, Any]:
    """Inspect a PDF page structurally and compare against extracted Parquet text."""
    try:
        layout_text = (page.extract_text(extraction_mode="layout") or "").strip()
    except Exception:
        try:
            layout_text = (page.extract_text() or "").strip()
        except Exception:
            layout_text = ""

    image_count = _count_embedded_images(page)
    stream_str = _read_page_content_stream(page)
    rect_ops = len(_VECTOR_RECT_RE.findall(stream_str))
    line_ops = len(_VECTOR_LINE_RE.findall(stream_str))

    layout_lines = [ln for ln in layout_text.splitlines() if ln.strip()]
    multicol_lines = sum(1 for ln in layout_lines if _MULTICOL_LINE_RE.search(ln))
    has_table_grid = rect_ops >= 6 or line_ops >= 8 or multicol_lines >= 3

    ext_stripped = extracted_page_text.strip()
    has_tabular_formatting = (
        "|" in ext_stripped or "\t" in ext_stripped or ext_stripped.count("\n") >= 2
    )

    issues: list[str] = []
    accuracy_score = 1.0
    table_fidelity_score = 1.0

    if image_count > 0 and len(ext_stripped) < 20 and len(layout_text) < 20:
        issues.append("VLM_SCANNED_PAGE_LOW_TEXT")
        accuracy_score = min(accuracy_score, 0.25)

    if _CID_GLYPH_RE.search(ext_stripped) or _CID_GLYPH_RE.search(layout_text):
        issues.append("VLM_GLYPH_ENCODING_ERROR")
        accuracy_score = min(accuracy_score, 0.45)

    if has_table_grid and not has_tabular_formatting:
        issues.append("VLM_TABLE_STRUCTURE_MISMATCH")
        table_fidelity_score = 0.35
        accuracy_score = min(accuracy_score, 0.55)
    elif has_table_grid:
        table_fidelity_score = 0.85

    layout_tokens = [w.lower() for w in _WORD_RE.findall(layout_text)]
    ext_tokens_set = {w.lower() for w in _WORD_RE.findall(ext_stripped)}
    if len(layout_tokens) >= 5:
        matched = sum(1 for tok in layout_tokens if tok in ext_tokens_set)
        recall = matched / len(layout_tokens)
        if recall < 0.70:
            issues.append("VLM_TEXT_EXTRACTION_DRIFT")
            accuracy_score = min(accuracy_score, round(max(0.0, recall), 4))
    elif len(layout_tokens) > 0 and not ext_stripped:
        issues.append("VLM_TEXT_EXTRACTION_DRIFT")
        accuracy_score = min(accuracy_score, 0.10)

    return {
        "layout_text": layout_text,
        "image_count": image_count,
        "has_table_grid": has_table_grid,
        "accuracy_score": round(max(0.0, min(1.0, accuracy_score)), 4),
        "table_fidelity_score": round(max(0.0, min(1.0, table_fidelity_score)), 4),
        "issues": issues,
    }


def _call_vlm_openai_compat(
    client: httpx.Client,
    *,
    base_url: str,
    api_key: str,
    model: str,
    image_bytes: bytes,
    mime_type: str,
    extracted_text: str,
    page_number: int,
) -> dict[str, Any] | None:
    """Call an OpenAI-compatible VLM endpoint to verify extracted text/table against page image."""
    b64_img = base64.b64encode(image_bytes).decode("ascii")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    prompt = (
        f"Verify PDF page {page_number} extraction accuracy against the rendered page image.\n"
        "Compare visible text and table structure with this extracted text:\n"
        f"{extracted_text[:1500]}\n\n"
        "Reply ONLY with valid JSON: "
        '{"text_accuracy": 0.0..1.0, "table_fidelity": 0.0..1.0, '
        '"missing_content": true|false, "table_mismatch": true|false, "summary": "..."}'
    )
    payload = {
        "model": model,
        "temperature": 0.0,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{b64_img}"},
                    },
                ],
            }
        ],
    }
    url = f"{base_url.rstrip('/')}/chat/completions"
    try:
        resp = client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        content = str(resp.json()["choices"][0]["message"]["content"]).strip()
        fence_match = _FENCE_JSON_RE.search(content)
        if fence_match:
            content = fence_match.group(1).strip()
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            return None
        text_acc = max(0.0, min(1.0, float(parsed.get("text_accuracy", 1.0))))
        tbl_fid = max(0.0, min(1.0, float(parsed.get("table_fidelity", 1.0))))
        return {
            "text_accuracy": round(text_acc, 4),
            "table_fidelity": round(tbl_fid, 4),
            "missing_content": bool(parsed.get("missing_content", False)),
            "table_mismatch": bool(parsed.get("table_mismatch", False)),
            "summary": str(parsed.get("summary", ""))[:200],
        }
    except Exception:
        return None


def verify_pdf_extraction(
    pdf_bytes: bytes,
    page_texts: dict[int, str],
    *,
    max_pages: int = 20,
    min_accuracy_score: float = 0.75,
    render_dpi: int = 150,
) -> dict[str, Any]:
    """Verify PDF page extraction fidelity using VLM (if configured) and structural analysis."""
    settings = get_settings()
    use_vlm = (
        settings.vlm_provider != "placeholder"
        and bool(settings.vlm_model)
        and (settings.allow_external_llm or is_local_llm_url(settings.vlm_base_url))
    )

    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
    except Exception as exc:
        raise ValueError("Berkas PDF rusak atau tidak dapat dibaca untuk verifikasi VLM.") from exc

    total_pdf_pages = len(reader.pages)
    limit = min(total_pdf_pages, max(1, max_pages))
    page_images, render_backend = render_pdf_pages_to_png(
        pdf_bytes, max_pages=limit, dpi=render_dpi
    )

    pages_out: list[dict[str, Any]] = []
    flagged_pages = 0
    vlm_used_any = False

    http_client: httpx.Client | None = httpx.Client(timeout=15.0) if use_vlm else None
    try:
        for page_no in range(1, limit + 1):
            page = reader.pages[page_no - 1]
            ext_text = page_texts.get(page_no, "")
            struct = _inspect_pdf_page_structure(page, ext_text)

            acc_score = float(struct["accuracy_score"])
            tbl_score = float(struct["table_fidelity_score"])
            issues: list[str] = list(struct["issues"])
            page_mode = "local_pdf_structural"

            img_bytes = page_images.get(page_no)
            if http_client is not None and img_bytes:
                vlm_res = _call_vlm_openai_compat(
                    http_client,
                    base_url=settings.vlm_base_url,
                    api_key=settings.vlm_api_key,
                    model=settings.vlm_model,
                    image_bytes=img_bytes,
                    mime_type=_detect_image_mime(img_bytes),
                    extracted_text=ext_text,
                    page_number=page_no,
                )
                if vlm_res is not None:
                    vlm_used_any = True
                    page_mode = "vlm"
                    acc_score = round(min(acc_score, float(vlm_res["text_accuracy"])), 4)
                    tbl_score = round(min(tbl_score, float(vlm_res["table_fidelity"])), 4)
                    if vlm_res["table_mismatch"] and "VLM_TABLE_STRUCTURE_MISMATCH" not in issues:
                        issues.append("VLM_TABLE_STRUCTURE_MISMATCH")
                    if vlm_res["missing_content"] and "VLM_TEXT_EXTRACTION_DRIFT" not in issues:
                        issues.append("VLM_TEXT_EXTRACTION_DRIFT")

            if (acc_score < min_accuracy_score or tbl_score < min_accuracy_score) and not issues:
                issues.append("VLM_VERIFICATION_LOW_SCORE")

            if issues:
                flagged_pages += 1

            masked_preview = apply_pii_transform(ext_text, mode="mask")[:200]
            pages_out.append(
                {
                    "page_number": page_no,
                    "accuracy_score": acc_score,
                    "table_fidelity_score": tbl_score,
                    "has_embedded_images": int(struct["image_count"]) > 0,
                    "has_table_grid": bool(struct["has_table_grid"]),
                    "verification_mode": page_mode,
                    "issues": issues,
                    "extracted_preview": masked_preview,
                }
            )
    finally:
        if http_client is not None:
            http_client.close()

    verified_count = len(pages_out)
    avg_acc = (
        round(sum(float(p["accuracy_score"]) for p in pages_out) / verified_count, 4)
        if verified_count
        else 1.0
    )
    avg_tbl = (
        round(sum(float(p["table_fidelity_score"]) for p in pages_out) / verified_count, 4)
        if verified_count
        else 1.0
    )
    overall_score = round((avg_acc + avg_tbl) / 2.0, 4)
    return {
        "total_pages": total_pdf_pages,
        "verified_pages": verified_count,
        "flagged_pages": flagged_pages,
        "avg_vlm_accuracy_score": avg_acc,
        "avg_table_fidelity_score": avg_tbl,
        "vlm_verification_score": overall_score,
        "verification_mode": "vlm" if vlm_used_any else "local_pdf_structural",
        "render_backend": render_backend,
        "pages": pages_out,
    }


def _load_pdf_bytes_from_params(params: dict[str, Any]) -> bytes | None:
    raw_b64 = params.get("pdf_bytes_b64")
    if isinstance(raw_b64, str) and raw_b64.strip():
        try:
            return base64.b64decode(raw_b64)
        except Exception:
            return None

    storage_key = params.get("raw_pdf_storage_key")
    if isinstance(storage_key, str) and storage_key.strip():
        try:
            storage = get_storage_backend()
            if storage.exists(storage_key):
                return storage.get_bytes(storage_key)
        except Exception:
            return None
    return None


class VlmPdfVerifierModule:
    """Quality module verifying PDF extracted text/tables against rendered page visuals."""

    name = "vlm_pdf_verifier"
    dimension = "instruction_quality"
    version = "0.1.0"

    def run(
        self,
        data: pl.LazyFrame,
        config: ModuleConfig,
        seed: int,
    ) -> tuple[ModuleResult, pl.DataFrame]:
        del seed
        max_pages = int(config.params.get("max_pages", 20))
        min_accuracy_score = float(config.params.get("min_accuracy_score", 0.75))
        render_dpi = int(config.params.get("render_dpi", 150))

        df = data.collect()
        pdf_bytes = _load_pdf_bytes_from_params(config.params)

        if pdf_bytes is None:
            # Graceful fallback for non-PDF datasets: inspect extracted text for PDF/table cues
            findings: list[dict[str, Any]] = []
            flagged = 0
            for row in df.iter_rows(named=True):
                rid = str(row.get("row_id", ""))
                txt = str(row.get("text") or "")
                if _CID_GLYPH_RE.search(txt):
                    flagged += 1
                    findings.append(
                        RowFinding(
                            row_id=rid,
                            module=self.name,
                            code="VLM_GLYPH_ENCODING_ERROR",
                            severity="warning",
                            score=0.45,
                            detail={"source": "text_only_fallback"},
                        ).model_dump()
                    )
            total_rows = df.height
            clean_score = round(max(0.0, 1.0 - (flagged / total_rows)), 4) if total_rows else 1.0
            result = ModuleResult(
                module=self.name,
                dimension=self.dimension,
                metrics={
                    "total_pages": 0.0,
                    "verified_pages": 0.0,
                    "flagged_pages": float(flagged),
                    "avg_vlm_accuracy_score": clean_score,
                    "avg_table_fidelity_score": 1.0,
                    "vlm_verification_score": clean_score,
                    "pdf_source_available": 0.0,
                },
                findings_count=len(findings),
            )
            return result, (pl.DataFrame(findings) if findings else pl.DataFrame())

        page_texts_parts: dict[int, list[str]] = {}
        page_first_row_id: dict[int, str] = {}
        for row in df.iter_rows(named=True):
            rid = str(row.get("row_id", "row_000001"))
            raw_page = row.get("line_number", 1)
            page_no = int(raw_page) if isinstance(raw_page, int) and raw_page >= 1 else 1
            page_texts_parts.setdefault(page_no, []).append(str(row.get("text") or ""))
            page_first_row_id.setdefault(page_no, rid)

        page_texts = {p: "\n\n".join(parts) for p, parts in page_texts_parts.items()}
        report = verify_pdf_extraction(
            pdf_bytes,
            page_texts,
            max_pages=max_pages,
            min_accuracy_score=min_accuracy_score,
            render_dpi=render_dpi,
        )

        findings = []
        default_rid = str(df["row_id"][0]) if df.height > 0 and "row_id" in df.columns else "page_1"
        for page_info in report["pages"]:
            p_no = int(page_info["page_number"])
            rid = page_first_row_id.get(p_no, default_rid)
            acc = max(0.0, min(1.0, float(page_info["accuracy_score"])))
            tbl = max(0.0, min(1.0, float(page_info["table_fidelity_score"])))
            mode_str = str(page_info["verification_mode"])
            for code in page_info["issues"]:
                score_val = tbl if code == "VLM_TABLE_STRUCTURE_MISMATCH" else acc
                findings.append(
                    RowFinding(
                        row_id=rid,
                        module=self.name,
                        code=str(code),
                        severity="warning",
                        score=max(0.0, min(1.0, score_val)),
                        detail={
                            "page_number": p_no,
                            "accuracy_score": acc,
                            "table_fidelity_score": tbl,
                            "verification_mode": mode_str,
                        },
                    ).model_dump()
                )

        result = ModuleResult(
            module=self.name,
            dimension=self.dimension,
            metrics={
                "total_pages": float(report["total_pages"]),
                "verified_pages": float(report["verified_pages"]),
                "flagged_pages": float(report["flagged_pages"]),
                "avg_vlm_accuracy_score": float(report["avg_vlm_accuracy_score"]),
                "avg_table_fidelity_score": float(report["avg_table_fidelity_score"]),
                "vlm_verification_score": float(report["vlm_verification_score"]),
                "pdf_source_available": 1.0,
            },
            findings_count=len(findings),
        )
        findings_df = pl.DataFrame(findings) if findings else pl.DataFrame()
        return result, findings_df
