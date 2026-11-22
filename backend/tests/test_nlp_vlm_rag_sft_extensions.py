"""Tests for Linguistic Quality (B7), VLM PDF Verification (D6), Semantic Chunking, and QA/SFT."""

import io
import json
import zipfile
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import httpx
import polars as pl
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.observability import reset_metrics
from app.core.rate_limit import login_rate_limiter
from app.core.security import Role, hash_password
from app.db.base import Base
from app.db.models import AuditLog, Membership, Organization, User, Workspace
from app.db.session import get_db
from app.main import app
from app.modules.base import ModuleConfig, ModuleResult
from app.modules.cleaning.linguistic import LinguisticQualityModule
from app.modules.instruction import vlm_verifier
from app.modules.instruction.vlm_verifier import VlmPdfVerifierModule, verify_pdf_extraction
from app.scoring.dqi import compute_dqi


def _assemble_pdf(objects: list[bytes]) -> bytes:
    header = b"%PDF-1.4\n"
    offsets: list[int] = []
    body = bytearray()
    pos = len(header)
    for obj in objects:
        offsets.append(pos)
        body.extend(obj)
        pos += len(obj)
    xref_pos = len(header) + len(body)
    xref = [f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("latin-1")]
    for off in offsets:
        xref.append(f"{off:010d} 00000 n \n".encode("latin-1"))
    trailer = (
        f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n"
    ).encode("latin-1")
    return header + bytes(body) + b"".join(xref) + trailer


def _build_text_pdf(text: str) -> bytes:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("latin-1")
    return _assemble_pdf(
        [
            b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
            b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
            (
                b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj\n"
            ),
            (
                f"4 0 obj << /Length {len(stream)} >> stream\n".encode("latin-1")
                + stream
                + b"\nendstream endobj\n"
            ),
            b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n",
        ]
    )


def _build_scanned_image_pdf() -> bytes:
    # Minimal 1x1 grayscale raw image XObject with no text operators on the page
    img_data = b"\x80"
    content_stream = b"q 100 0 0 100 72 600 cm /Im1 Do Q"
    return _assemble_pdf(
        [
            b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
            b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
            (
                b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                b"/Contents 4 0 R /Resources << /XObject << /Im1 5 0 R >> >> >> endobj\n"
            ),
            (
                f"4 0 obj << /Length {len(content_stream)} >> stream\n".encode("latin-1")
                + content_stream
                + b"\nendstream endobj\n"
            ),
            (
                b"5 0 obj << /Type /XObject /Subtype /Image /Width 1 /Height 1 "
                b"/ColorSpace /DeviceGray /BitsPerComponent 8 "
                + f"/Length {len(img_data)} >> stream\n".encode("latin-1")
                + img_data
                + b"\nendstream endobj\n"
            ),
        ]
    )


def _build_vector_table_pdf() -> bytes:
    rects = "\n".join(f"{50 + i * 60} 650 55 20 re S" for i in range(8))
    stream = (f"{rects}\nBT /F1 11 Tf 55 655 Td (Kolom Nama Nilai Status Keterangan) Tj ET").encode(
        "latin-1"
    )
    return _assemble_pdf(
        [
            b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
            b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
            (
                b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj\n"
            ),
            (
                f"4 0 obj << /Length {len(stream)} >> stream\n".encode("latin-1")
                + stream
                + b"\nendstream endobj\n"
            ),
            b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n",
        ]
    )


@pytest.fixture
async def ext_pipeline_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[dict[str, Any], None]:
    """Isolated DB, storage, and RBAC users for NLP/VLM/RAG/SFT extension tests."""
    login_rate_limiter.reset()
    reset_metrics()
    storage_dir = tmp_path / "storage"
    db_file = tmp_path / "ext_pipe.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_file}")
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("LOCAL_STORAGE_PATH", str(storage_dir))
    monkeypatch.setenv("CELERY_TASK_ALWAYS_EAGER", "true")
    monkeypatch.setenv("ALLOW_EXTERNAL_LLM", "false")
    monkeypatch.setenv("LLM_PROVIDER", "placeholder")
    monkeypatch.setenv("VLM_PROVIDER", "placeholder")
    get_settings.cache_clear()

    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as db:
        org = Organization(name="Org Ext2", slug="org-ext2", settings={})
        db.add(org)
        await db.flush()
        ws = Workspace(organization_id=org.id, name="WS Ext2", slug="ws-ext2", settings={})
        db.add(ws)
        await db.flush()
        curator = User(
            email="curator_ext@example.com",
            full_name="Curator Ext",
            password_hash=hash_password("Pass123!"),
            is_active=True,
            is_superadmin=False,
        )
        viewer = User(
            email="viewer_ext@example.com",
            full_name="Viewer Ext",
            password_hash=hash_password("Pass123!"),
            is_active=True,
            is_superadmin=False,
        )
        db.add_all([curator, viewer])
        await db.flush()
        db.add_all(
            [
                Membership(user_id=curator.id, workspace_id=ws.id, role=Role.CURATOR.value),
                Membership(user_id=viewer.id, workspace_id=ws.id, role=Role.VIEWER.value),
            ]
        )
        await db.commit()
        ws_id = ws.id

    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        c_tok = (
            await ac.post(
                "/api/v1/auth/login",
                json={"email": "curator_ext@example.com", "password": "Pass123!"},
            )
        ).json()["access_token"]
        v_tok = (
            await ac.post(
                "/api/v1/auth/login",
                json={"email": "viewer_ext@example.com", "password": "Pass123!"},
            )
        ).json()["access_token"]
        yield {
            "client": ac,
            "headers": {"Authorization": f"Bearer {c_tok}"},
            "viewer_headers": {"Authorization": f"Bearer {v_tok}"},
            "ws_id": ws_id,
            "session_factory": session_factory,
        }

    app.dependency_overrides.clear()
    await engine.dispose()
    login_rate_limiter.reset()
    get_settings.cache_clear()


def test_linguistic_quality_perplexity_oov_gibberish_and_dqi() -> None:
    """Verify perplexity, OOV ratio, OCR/gibberish detection, custom_vocab, and DQI integration."""
    clean_df = pl.DataFrame(
        [
            {
                "row_id": "ok_1",
                "text": (
                    "Kebijakan privasi perusahaan melindungi data pelanggan dengan aman pada 2026."
                ),
            },
            {
                "row_id": "ok_2",
                "text": (
                    "Enterprise data pipelines prepare clean instruction datasets for training."
                ),
            },
            {
                "row_id": "ok_3",
                "text": "Identitas pengguna [NIK] dan [EMAIL] sudah disamarkan pada versi v1.",
            },
        ]
    ).lazy()
    clean_res, clean_findings = LinguisticQualityModule().run(clean_df, ModuleConfig(), seed=42)
    assert clean_findings.height == 0
    assert clean_res.metrics["valid_word_ratio"] >= 0.80
    assert clean_res.metrics["avg_perplexity"] < 35.0
    assert clean_res.metrics["linguistic_quality_ratio"] == 1.0

    noisy_df = pl.DataFrame(
        [
            {
                "row_id": "bad_1",
                "text": "d0kum3n k3bijakan xqzvj kwrpt plmnb zzzzzz @@@ ### $$$",
            },
            {
                "row_id": "bad_2",
                "text": "kustomterm qwertyu asdfghj zxcvbnm mnjkpl brtghy",
            },
        ]
    ).lazy()
    noisy_res, noisy_findings = LinguisticQualityModule().run(
        noisy_df,
        ModuleConfig(params={"custom_vocab": ["kustomterm"]}),
        seed=42,
    )
    codes = set(noisy_findings["code"].to_list())
    assert {"HIGH_PERPLEXITY", "HIGH_OOV_RATIO", "OCR_GIBBERISH_NOISE"} <= codes
    assert noisy_res.metrics["gibberish_rows"] >= 1.0
    assert noisy_res.metrics["oov_ratio"] > 0.40
    # Privacy: raw text / raw OOV words must never appear inside RowFinding.detail
    for detail in noisy_findings["detail"].to_list():
        serialized = json.dumps(detail)
        assert "xqzvj" not in serialized
        assert "d0kum3n" not in serialized

    # Verify DQI scoring supports linguistic_quality_ratio & vlm_verification_score
    dqi = compute_dqi(
        [
            ModuleResult(
                module="linguistic_quality",
                dimension="cleanliness_privacy",
                metrics={"linguistic_quality_ratio": 0.75},
                findings_count=1,
            ),
            ModuleResult(
                module="vlm_pdf_verifier",
                dimension="instruction_quality",
                metrics={"vlm_verification_score": 0.85},
                findings_count=1,
            ),
        ],
        has_critical_findings=False,
    )
    dim_scores = {d["dimension"]: d["score"] for d in dqi.dimensions}
    assert dim_scores["cleanliness_privacy"] == 75.0
    assert dim_scores["instruction_quality"] == 85.0


def test_vlm_verifier_structural_and_mocked_vlm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify PDF structural inspection, mocked local VLM call, fallback, and SSRF guard."""
    clean_pdf = _build_text_pdf("Dokumen kebijakan internal perusahaan yang terstruktur.")
    clean_rep = verify_pdf_extraction(
        clean_pdf, {1: "Dokumen kebijakan internal perusahaan yang terstruktur."}
    )
    assert clean_rep["avg_vlm_accuracy_score"] >= 0.85
    assert clean_rep["flagged_pages"] == 0
    assert clean_rep["verification_mode"] == "local_pdf_structural"

    scanned_pdf = _build_scanned_image_pdf()
    scanned_rep = verify_pdf_extraction(scanned_pdf, {1: ""})
    assert scanned_rep["flagged_pages"] == 1
    assert "VLM_SCANNED_PAGE_LOW_TEXT" in scanned_rep["pages"][0]["issues"]

    table_pdf = _build_vector_table_pdf()
    table_rep = verify_pdf_extraction(table_pdf, {1: "Kolom Nama Nilai Status Keterangan"})
    assert "VLM_TABLE_STRUCTURE_MISMATCH" in table_rep["pages"][0]["issues"]

    # Mocked local OpenAI-compatible VLM endpoint
    monkeypatch.setenv("VLM_PROVIDER", "openai_compat")
    monkeypatch.setenv("VLM_MODEL", "qwen2.5vl:7b")
    monkeypatch.setenv("VLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("ALLOW_EXTERNAL_LLM", "false")
    get_settings.cache_clear()

    monkeypatch.setattr(
        vlm_verifier,
        "render_pdf_pages_to_png",
        lambda *_a, **_k: ({1: b"\x89PNG\r\n\x1a\nfake-png-bytes"}, "mock_renderer"),
    )
    captured_payloads: list[dict[str, Any]] = []

    def _mock_vlm_post(_self: httpx.Client, url: str, **kwargs: Any) -> httpx.Response:
        captured_payloads.append(dict(kwargs.get("json") or {}))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "text_accuracy": 0.62,
                                    "table_fidelity": 0.40,
                                    "missing_content": True,
                                    "table_mismatch": True,
                                    "summary": "Table columns collapsed",
                                }
                            )
                        }
                    }
                ]
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.Client, "post", _mock_vlm_post)
    vlm_rep = verify_pdf_extraction(clean_pdf, {1: "Dokumen kebijakan internal perusahaan."})
    assert vlm_rep["verification_mode"] == "vlm"
    assert vlm_rep["flagged_pages"] == 1
    assert {
        "VLM_TABLE_STRUCTURE_MISMATCH",
        "VLM_TEXT_EXTRACTION_DRIFT",
    } <= set(vlm_rep["pages"][0]["issues"])
    assert "image_url" in json.dumps(captured_payloads[0])

    # Fallback cleanly to local_pdf_structural when VLM endpoint fails
    def _failing_post(*_a: Any, **_k: Any) -> httpx.Response:
        raise httpx.ConnectError("vlm offline")

    monkeypatch.setattr(httpx.Client, "post", _failing_post)
    fb_rep = verify_pdf_extraction(
        clean_pdf, {1: "Dokumen kebijakan internal perusahaan yang terstruktur."}
    )
    assert fb_rep["verification_mode"] == "local_pdf_structural"

    # External VLM URL blocked when ALLOW_EXTERNAL_LLM=false
    monkeypatch.setenv("VLM_BASE_URL", "https://api.external-vlm.example.com/v1")
    get_settings.cache_clear()
    ext_calls: list[str] = []

    def _should_not_call(_self: httpx.Client, url: str, **_k: Any) -> httpx.Response:
        ext_calls.append(url)
        return httpx.Response(200, json={}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.Client, "post", _should_not_call)
    blocked_rep = verify_pdf_extraction(
        clean_pdf, {1: "Dokumen kebijakan internal perusahaan yang terstruktur."}
    )
    assert blocked_rep["verification_mode"] == "local_pdf_structural"
    assert ext_calls == []
    get_settings.cache_clear()

    # Non-PDF graceful fallback on VlmPdfVerifierModule
    non_pdf_df = pl.DataFrame(
        [{"row_id": "r1", "text": "Teks dengan glyph rusak (cid:42) terdeteksi."}]
    ).lazy()
    mod_res, mod_findings = VlmPdfVerifierModule().run(non_pdf_df, ModuleConfig(), seed=42)
    assert mod_res.metrics["pdf_source_available"] == 0.0
    assert mod_findings["code"].to_list() == ["VLM_GLYPH_ENCODING_ERROR"]


async def test_api_vlm_verify_linguistic_run_and_rbac(
    ext_pipeline_env: dict[str, Any],
) -> None:
    """Verify PDF VLM endpoint, linguistic_quality run, non-PDF 400 error, RBAC, and AuditLog."""
    client: AsyncClient = ext_pipeline_env["client"]
    headers: dict[str, str] = ext_pipeline_env["headers"]
    viewer_headers: dict[str, str] = ext_pipeline_env["viewer_headers"]
    ws_id: str = ext_pipeline_env["ws_id"]
    session_factory: async_sessionmaker[AsyncSession] = ext_pipeline_env["session_factory"]

    ds_id = (
        await client.post(
            f"/api/v1/workspaces/{ws_id}/datasets",
            json={"name": "PDF Table Audit", "dataset_type": "raw"},
            headers=headers,
        )
    ).json()["id"]

    pdf_bytes = _build_vector_table_pdf()
    v1 = (
        await client.post(
            f"/api/v1/datasets/{ds_id}/versions/upload-direct",
            files={"file": ("table.pdf", pdf_bytes, "application/pdf")},
            data={"source_format": "pdf"},
            headers=headers,
        )
    ).json()
    v1_id = v1["id"]

    # Module catalog exposes linguistic_quality and vlm_pdf_verifier
    mods = (await client.get("/api/v1/modules", headers=headers)).json()
    mod_names = {m["name"] for m in mods}
    assert {"linguistic_quality", "vlm_pdf_verifier"} <= mod_names

    # Viewer blocked on /vlm-verify
    assert (
        await client.post(
            f"/api/v1/versions/{v1_id}/vlm-verify",
            json={"max_pages": 5},
            headers=viewer_headers,
        )
    ).status_code == 403

    # Curator executes /vlm-verify -> detects table mismatch
    vlm_res = await client.post(
        f"/api/v1/versions/{v1_id}/vlm-verify",
        json={"max_pages": 5, "min_accuracy_score": 0.75},
        headers=headers,
    )
    assert vlm_res.status_code == 200
    vlm_data = vlm_res.json()
    assert vlm_data["verified_pages"] == 1
    assert "VLM_TABLE_STRUCTURE_MISMATCH" in vlm_data["pages"][0]["issues"]

    # Quality Run executing both linguistic_quality & vlm_pdf_verifier
    run_res = await client.post(
        f"/api/v1/versions/{v1_id}/runs",
        json={"modules": ["linguistic_quality", "vlm_pdf_verifier"], "seed": 42},
        headers=headers,
    )
    assert run_res.status_code == 201
    run_id = run_res.json()["id"]
    summary = (await client.get(f"/api/v1/runs/{run_id}/summary", headers=headers)).json()
    assert {m["module"] for m in summary["module_results"]} == {
        "linguistic_quality",
        "vlm_pdf_verifier",
    }

    # Non-PDF version returns 400 NOT_A_PDF_VERSION
    txt_ds = (
        await client.post(
            f"/api/v1/workspaces/{ws_id}/datasets",
            json={"name": "Plain Text DS", "dataset_type": "raw"},
            headers=headers,
        )
    ).json()["id"]
    txt_v1_id = (
        await client.post(
            f"/api/v1/datasets/{txt_ds}/versions/upload-direct",
            files={"file": ("plain.txt", b"Baris biasa saja.\n", "text/plain")},
            data={"source_format": "txt"},
            headers=headers,
        )
    ).json()["id"]
    bad_vlm = await client.post(
        f"/api/v1/versions/{txt_v1_id}/vlm-verify",
        json={"max_pages": 5},
        headers=headers,
    )
    assert bad_vlm.status_code == 400
    assert bad_vlm.json()["error"]["code"] == "NOT_A_PDF_VERSION"

    async with session_factory() as db:
        logs = (
            (await db.execute(select(AuditLog).where(AuditLog.action == "dataset.vlm_verify")))
            .scalars()
            .all()
        )
        assert len(logs) == 1


async def test_semantic_chunking_metadata_enrichment_and_rag_vector_export(
    ext_pipeline_env: dict[str, Any],
) -> None:
    """Verify semantic chunking, PII-safe metadata, Quality Gate, and rag_vector export."""
    client: AsyncClient = ext_pipeline_env["client"]
    headers: dict[str, str] = ext_pipeline_env["headers"]
    viewer_headers: dict[str, str] = ext_pipeline_env["viewer_headers"]
    ws_id: str = ext_pipeline_env["ws_id"]
    session_factory: async_sessionmaker[AsyncSession] = ext_pipeline_env["session_factory"]

    ds_id = (
        await client.post(
            f"/api/v1/workspaces/{ws_id}/datasets",
            json={"name": "SOP Keamanan RAG", "dataset_type": "rag"},
            headers=headers,
        )
    ).json()["id"]

    md_doc = (
        b"# Kebijakan Privasi\n\n"
        b"Data sensitif seperti NIK 3273010101900001 dan email budi@contoh.id wajib dilindungi. "
        b"Setiap sistem harus menerapkan kontrol akses berbasis peran.\n\n"
        b"# Prosedur Keamanan\n\n"
        b"Semua API internal harus menggunakan token bertanda tangan dan audit log.\n"
    )

    v1_id = (
        await client.post(
            f"/api/v1/datasets/{ds_id}/versions/upload-direct",
            files={"file": ("sop.md", md_doc, "text/markdown")},
            data={"source_format": "md"},
            headers=headers,
        )
    ).json()["id"]

    # Viewer blocked on /semantic-chunk
    assert (
        await client.post(
            f"/api/v1/versions/{v1_id}/semantic-chunk",
            json={},
            headers=viewer_headers,
        )
    ).status_code == 403

    chunk_res = await client.post(
        f"/api/v1/versions/{v1_id}/semantic-chunk",
        json={
            "max_chunk_tokens": 256,
            "min_chunk_tokens": 16,
            "similarity_threshold": 0.55,
            "overlap_sentences": 1,
            "include_headings": True,
            "mask_pii_in_metadata": True,
        },
        headers=headers,
    )
    assert chunk_res.status_code == 200
    chunk_data = chunk_res.json()
    v2 = chunk_data["version"]
    assert v2["version_number"] == 2
    assert v2["parent_version_id"] == v1_id
    assert chunk_data["chunk_count"] >= 2

    # Metadata enrichment contains required fields and never leaks raw PII
    first_meta = chunk_data["sample_chunks"][0]["metadata"]
    assert {"language", "token_count", "keywords", "section_title", "coherence_score"} <= set(
        first_meta.keys()
    )
    assert "3273010101900001" not in json.dumps(chunk_data["sample_chunks"])
    assert "budi@contoh.id" not in json.dumps(first_meta["keywords"])

    # Explorer /rows exposes sanitized metadata dict
    rows_res = (await client.get(f"/api/v1/versions/{v2['id']}/rows", headers=headers)).json()
    assert rows_res["items"][0]["metadata"]["chunking_strategy"] == "semantic"

    # rag_vector export blocked while unmasked PII remains in chunk text
    blocked_exp = await client.post(
        f"/api/v1/versions/{v2['id']}/exports",
        json={"export_format": "rag_vector"},
        headers=headers,
    )
    assert blocked_exp.status_code == 409
    assert blocked_exp.json()["error"]["code"] == "QUALITY_GATE_PII_BLOCKED"

    # Mask PII via curation -> v3 -> run quality -> export rag_vector JSONL
    v3_id = (
        await client.post(
            f"/api/v1/versions/{v2['id']}/curation",
            json={
                "actions": [
                    {"row_id": item["row_id"], "action": "mask_pii"} for item in rows_res["items"]
                ]
            },
            headers=headers,
        )
    ).json()["id"]
    await client.post(
        f"/api/v1/versions/{v3_id}/runs",
        json={"modules": ["pii", "rag_chunk_quality"], "seed": 42},
        headers=headers,
    )

    rag_exp = await client.post(
        f"/api/v1/versions/{v3_id}/exports",
        json={"export_format": "rag_vector"},
        headers=headers,
    )
    assert rag_exp.status_code == 201
    dl = await client.get(f"/api/v1/exports/{rag_exp.json()['id']}/download", headers=headers)
    assert dl.status_code == 200
    parsed_lines = [json.loads(line) for line in dl.text.strip().splitlines()]
    assert len(parsed_lines) == chunk_data["chunk_count"]
    assert {"id", "text", "metadata"} <= set(parsed_lines[0].keys())
    assert parsed_lines[0]["metadata"]["dataset_id"] == ds_id
    assert "3273010101900001" not in dl.text

    async with session_factory() as db:
        logs = (
            (await db.execute(select(AuditLog).where(AuditLog.action == "dataset.semantic_chunk")))
            .scalars()
            .all()
        )
        assert len(logs) == 1


async def test_qa_sft_synthesis_local_llm_and_exports(
    ext_pipeline_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify QA/SFT synthesis (local + mocked LLM), new dataset/version, and sft_chat/HF."""
    client: AsyncClient = ext_pipeline_env["client"]
    headers: dict[str, str] = ext_pipeline_env["headers"]
    viewer_headers: dict[str, str] = ext_pipeline_env["viewer_headers"]
    ws_id: str = ext_pipeline_env["ws_id"]
    session_factory: async_sessionmaker[AsyncSession] = ext_pipeline_env["session_factory"]

    ds_id = (
        await client.post(
            f"/api/v1/workspaces/{ws_id}/datasets",
            json={"name": "Dokumen Referensi NLP", "dataset_type": "raw"},
            headers=headers,
        )
    ).json()["id"]

    raw_rows = [
        json.dumps(
            {
                "text": (
                    "QualiText adalah platform evaluasi kualitas data teks korporat. "
                    "Kontak admin: NIK 3273010101900001."
                )
            }
        ),
        json.dumps(
            {
                "text": (
                    "Retrieval Augmented Generation refers to combining dense retrieval "
                    "with language model synthesis for accurate enterprise answers."
                )
            }
        ),
    ]
    v1_id = (
        await client.post(
            f"/api/v1/datasets/{ds_id}/versions/upload-direct",
            files={
                "file": ("docs.jsonl", ("\n".join(raw_rows) + "\n").encode(), "application/jsonl")
            },
            data={"source_format": "jsonl"},
            headers=headers,
        )
    ).json()["id"]

    # Viewer blocked
    assert (
        await client.post(
            f"/api/v1/versions/{v1_id}/generate-qa",
            json={"pairs_per_row": 2},
            headers=viewer_headers,
        )
    ).status_code == 403

    # 1. Generate into a new SFT dataset (local_template + mask_pii=True)
    qa_ds_res = await client.post(
        f"/api/v1/versions/{v1_id}/generate-qa",
        json={
            "pairs_per_row": 2,
            "language": "auto",
            "mode": "local_template",
            "mask_pii": True,
            "output_target": "new_dataset",
            "target_dataset_name": "SFT QA Sintetis",
        },
        headers=headers,
    )
    assert qa_ds_res.status_code == 200
    qa_ds_data = qa_ds_res.json()
    assert qa_ds_data["dataset"]["dataset_type"] == "sft"
    assert qa_ds_data["dataset"]["name"] == "SFT QA Sintetis"
    assert qa_ds_data["generation_mode"] == "local_template"
    assert qa_ds_data["generated_pairs_count"] >= 2
    assert "3273010101900001" not in json.dumps(qa_ds_data["sample_pairs"])

    sft_v1_id = qa_ds_data["version"]["id"]
    await client.post(
        f"/api/v1/versions/{sft_v1_id}/runs",
        json={"modules": ["token_analytics", "pii"], "seed": 42},
        headers=headers,
    )

    # Export sft_chat ChatML JSONL
    sft_exp = await client.post(
        f"/api/v1/versions/{sft_v1_id}/exports",
        json={"export_format": "sft_chat"},
        headers=headers,
    )
    assert sft_exp.status_code == 201
    sft_dl = await client.get(f"/api/v1/exports/{sft_exp.json()['id']}/download", headers=headers)
    assert sft_dl.status_code == 200
    chat_rows = [json.loads(line) for line in sft_dl.text.strip().splitlines()]
    assert len(chat_rows) == qa_ds_data["generated_pairs_count"]
    roles = [m["role"] for m in chat_rows[0]["messages"]]
    assert roles == ["system", "user", "assistant"]

    # Export Hugging Face .hf.zip includes messages_json & metadata_json columns
    hf_exp = await client.post(
        f"/api/v1/versions/{sft_v1_id}/exports",
        json={"export_format": "huggingface"},
        headers=headers,
    )
    assert hf_exp.status_code == 201
    hf_dl = await client.get(f"/api/v1/exports/{hf_exp.json()['id']}/download", headers=headers)
    with zipfile.ZipFile(io.BytesIO(hf_dl.content), mode="r") as zf:
        pq_df = pl.read_parquet(io.BytesIO(zf.read("data/train-00000-of-00001.parquet")))
        assert {"prompt", "response", "messages_json", "metadata_json"} <= set(pq_df.columns)

    # 2. Mocked local LLM mode -> output_target="new_version"
    monkeypatch.setenv("LLM_PROVIDER", "openai_compat")
    monkeypatch.setenv("LLM_MODEL", "qwen2.5")
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    get_settings.cache_clear()

    def _mock_llm_post(_self: httpx.Client, url: str, **_k: Any) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                [
                                    {
                                        "prompt": "Jelaskan fungsi utama QualiText.",
                                        "response": "Evaluasi kualitas data teks korporat.",
                                        "qa_type": "llm_synthesized",
                                    }
                                ]
                            )
                        }
                    }
                ]
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.Client, "post", _mock_llm_post)
    llm_res = await client.post(
        f"/api/v1/versions/{v1_id}/generate-qa",
        json={
            "pairs_per_row": 1,
            "mode": "llm",
            "mask_pii": True,
            "output_target": "new_version",
        },
        headers=headers,
    )
    assert llm_res.status_code == 200
    llm_data = llm_res.json()
    assert llm_data["generation_mode"] == "llm"
    assert llm_data["version"]["version_number"] == 2
    assert llm_data["version"]["parent_version_id"] == v1_id

    async with session_factory() as db:
        logs = (
            (await db.execute(select(AuditLog).where(AuditLog.action == "dataset.generate_qa")))
            .scalars()
            .all()
        )
        assert len(logs) == 2
    get_settings.cache_clear()
