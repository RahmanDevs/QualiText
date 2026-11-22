"""Quality report rendering service (HTML and self-contained PDF 1.4, Module F6)."""

import html
import json
from typing import Any

from app.db.models import QualityRun, RunModuleResult


def _esc_html(val: object) -> str:
    return html.escape(str(val), quote=True)


def _pdf_ascii_text(val: object) -> str:
    """Normalize text for standard PDF Type1 Helvetica encoding and escape parens/backslashes."""
    raw = (
        str(val)
        .replace("\u2014", "-")
        .replace("\u2013", "-")
        .replace("\u2264", "<=")
        .replace("\u2265", ">=")
    )
    ascii_safe = raw.encode("latin-1", "replace").decode("latin-1")
    return ascii_safe.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def render_html_report(run: QualityRun, mod_results: list[RunModuleResult]) -> str:
    """Render an escaped self-contained HTML audit & management quality report."""
    dims: list[dict[str, Any]] = list(run.dqi_summary.get("dimensions", []))
    dim_rows = "".join(
        f"<tr><td>{_esc_html(d.get('label', ''))}</td><td>{_esc_html(d.get('score', ''))}</td>"
        f"<td>{round(float(d.get('weight', 0)) * 100)}%</td>"
        f"<td>{_esc_html(d.get('findings_count', 0))}</td></tr>"
        for d in dims
    )
    mod_rows = "".join(
        f"<tr><td>{_esc_html(m.module)}</td><td>{_esc_html(m.dimension)}</td>"
        f"<td>{_esc_html(m.findings_count)}</td><td><code>{_esc_html(json.dumps(m.metrics))}</code></td></tr>"
        for m in mod_results
    )
    esc_run_id = _esc_html(run.id)
    esc_dqi = _esc_html(run.dqi_total)
    esc_label = _esc_html(run.dqi_summary.get("status_label", ""))
    return f"""<!doctype html>
<html lang="id">
<head><meta charset="utf-8"><title>QualiText Report - {esc_run_id}</title>
<style>
body{{font-family:Inter,system-ui,sans-serif;max-width:900px;margin:40px auto;color:#0f172a;}}
table{{width:100%;border-collapse:collapse;margin:16px 0;}}
th,td{{border:1px solid #e2e8f0;padding:8px 12px;text-align:left;font-size:13px;}}
th{{background:#f1f5f9;}}
</style></head>
<body>
<h1>Laporan Kualitas Dataset - QualiText</h1>
<p><strong>Run ID:</strong> {esc_run_id} | <strong>DQI Total:</strong> {esc_dqi} / 100
 ({esc_label}) | <strong>Seed:</strong> {_esc_html(run.seed)}</p>
<h2>Rincian Dimensi</h2>
<table><thead><tr><th>Dimensi</th><th>Skor</th><th>Bobot</th><th>Temuan</th></tr></thead>
<tbody>{dim_rows}</tbody></table>
<h2>Hasil Modul</h2>
<table><thead><tr><th>Modul</th><th>Dimensi</th><th>Temuan</th><th>Metrik</th></tr></thead>
<tbody>{mod_rows}</tbody></table>
</body></html>"""


def render_pdf_report(run: QualityRun, mod_results: list[RunModuleResult]) -> bytes:
    """Build a valid multi-page PDF 1.4 summary report readable by pypdf & standard viewers."""
    dims: list[dict[str, Any]] = list(run.dqi_summary.get("dimensions", []))
    lines: list[tuple[str, bool, int]] = [
        ("Laporan Kualitas Dataset - QualiText", True, 16),
        (f"Run ID: {run.id}", False, 10),
        (
            f"Version ID: {run.version_id} | Status: {run.status} | Seed: {run.seed}",
            False,
            10,
        ),
        (
            f"DQI Total: {run.dqi_total if run.dqi_total is not None else '-'} / 100 "
            f"({run.dqi_summary.get('status_label', 'N/A')})",
            True,
            12,
        ),
        (
            f"Partial: {run.dqi_summary.get('is_partial', False)} | "
            f"Critical Cap Applied: {run.dqi_summary.get('critical_capped', False)}",
            False,
            10,
        ),
        ("", False, 10),
        ("Rincian Dimensi DQI", True, 12),
    ]
    for d in dims:
        weight_pct = round(float(d.get("weight", 0.0)) * 100)
        lines.append(
            (
                f"- {d.get('label', d.get('dimension', ''))}: skor {d.get('score', '-')} "
                f"(bobot {weight_pct}%, temuan {d.get('findings_count', 0)})",
                False,
                10,
            )
        )

    lines.append(("", False, 10))
    lines.append(("Ringkasan Modul Kualitas", True, 12))
    for mr in mod_results:
        metric_pairs = ", ".join(f"{k}={v}" for k, v in sorted(mr.metrics.items())[:5])
        lines.append(
            (
                f"- {mr.module} [{mr.dimension}] | temuan={mr.findings_count} | {metric_pairs}",
                False,
                9,
            )
        )

    # Paginate A4 lines (top margin ~790pt down to ~55pt)
    pages_ops: list[str] = []
    cur_ops: list[str] = []
    y = 790
    for text_line, bold, size in lines:
        if y < 60:
            pages_ops.append("\n".join(cur_ops))
            cur_ops = []
            y = 790
        if not text_line:
            y -= 10
            continue
        font_ref = "/F2" if bold else "/F1"
        safe = _pdf_ascii_text(text_line[:110])
        cur_ops.append(f"BT {font_ref} {size} Tf 50 {y} Td ({safe}) Tj ET")
        y -= size + 6
    if cur_ops or not pages_ops:
        pages_ops.append("\n".join(cur_ops))

    objects: list[bytes] = []
    # Object 1: Catalog, Object 2: Pages, Object 3: Helvetica, Object 4: Helvetica-Bold
    page_ids: list[int] = []
    next_id = 5
    page_contents: list[tuple[int, int, str]] = []
    for ops in pages_ops:
        page_id = next_id
        content_id = next_id + 1
        next_id += 2
        page_ids.append(page_id)
        page_contents.append((page_id, content_id, ops))

    kids_str = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects.append(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")
    objects.append(
        f"2 0 obj\n<< /Type /Pages /Kids [{kids_str}] /Count {len(page_ids)} >>\nendobj\n".encode(
            "ascii"
        )
    )
    objects.append(b"3 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n")
    objects.append(
        b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>\nendobj\n"
    )

    for page_id, content_id, ops in page_contents:
        stream_bytes = ops.encode("latin-1")
        page_obj = (
            f"{page_id} 0 obj\n"
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            "/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> "
            f"/Contents {content_id} 0 R >>\nendobj\n"
        ).encode("ascii")
        content_header = f"{content_id} 0 obj\n<< /Length {len(stream_bytes)} >>\nstream\n".encode(
            "ascii"
        )
        content_obj = content_header + stream_bytes + b"\nendstream\nendobj\n"
        objects.append(page_obj)
        objects.append(content_obj)

    header = b"%PDF-1.4\n"
    body = bytearray(header)
    offsets: list[int] = [0]
    for obj in objects:
        offsets.append(len(body))
        body.extend(obj)

    xref_pos = len(body)
    xref_lines = [f"xref\n0 {len(objects) + 1}\n", "0000000000 65535 f \n"]
    for off in offsets[1:]:
        xref_lines.append(f"{off:010d} 00000 n \n")
    trailer = f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n"
    body.extend("".join(xref_lines).encode("ascii"))
    body.extend(trailer.encode("ascii"))
    return bytes(body)
