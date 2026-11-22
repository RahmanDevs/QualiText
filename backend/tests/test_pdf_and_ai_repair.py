"""Tests for PDF & Markdown ingestion, manual edit, and AI Repairing preview."""

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.security import Role, hash_password
from app.db.base import Base
from app.db.models import Membership, Organization, User, Workspace
from app.db.session import get_db
from app.main import app


def _build_minimal_pdf(text: str) -> bytes:
    """Create a valid minimal single-page PDF containing `text` without external deps."""
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("latin-1")
    objects = [
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


@pytest.fixture
async def ext_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[tuple[AsyncClient, str, str], None]:
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("LOCAL_STORAGE_PATH", str(tmp_path / "storage"))
    get_settings.cache_clear()

    db_file = tmp_path / "ext.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as db:
        org = Organization(name="Org Ext", slug="org-ext", settings={})
        db.add(org)
        await db.flush()
        ws = Workspace(organization_id=org.id, name="WS Ext", slug="ws-ext", settings={})
        db.add(ws)
        await db.flush()
        user = User(
            email="curator@example.com",
            full_name="Curator Ext",
            password_hash=hash_password("Pass123!"),
            is_active=True,
        )
        db.add(user)
        await db.flush()
        db.add(Membership(user_id=user.id, workspace_id=ws.id, role=Role.CURATOR.value))
        await db.commit()
        ws_id = ws.id

    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        login_res = await ac.post(
            "/api/v1/auth/login",
            json={"email": "curator@example.com", "password": "Pass123!"},
        )
        token = login_res.json()["access_token"]
        yield ac, token, ws_id

    app.dependency_overrides.clear()
    await engine.dispose()
    get_settings.cache_clear()


async def test_pdf_upload_semantic_check_and_ai_repair_flow(
    ext_client: tuple[AsyncClient, str, str],
) -> None:
    client, token, ws_id = ext_client
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Create raw/RAG dataset and upload PDF
    ds_res = await client.post(
        f"/api/v1/workspaces/{ws_id}/datasets",
        json={"name": "SOP PDF Internal", "dataset_type": "raw"},
        headers=headers,
    )
    assert ds_res.status_code == 201
    ds_id = ds_res.json()["id"]

    pdf_bytes = _build_minimal_pdf("Dokumen kebijakan internal perusahaan.")
    up_res = await client.post(
        f"/api/v1/datasets/{ds_id}/versions/upload-direct",
        files={"file": ("sop.pdf", pdf_bytes, "application/pdf")},
        data={"source_format": "pdf"},
        headers=headers,
    )
    assert up_res.status_code == 201
    v1 = up_res.json()
    assert v1["row_count"] >= 1
    v1_id = v1["id"]

    # 2. Run semantic & RAG quality checks
    run_res = await client.post(
        f"/api/v1/versions/{v1_id}/runs",
        json={"modules": ["artifacts", "rag_chunk_quality", "dedup_semantic"], "seed": 42},
        headers=headers,
    )
    assert run_res.status_code == 201
    assert run_res.json()["status"] == "completed"

    # 3. AI Repairing preview on broken PDF extraction text
    broken_text = "Dokumen kebi-\njakan  internal\u200b."
    rep_res = await client.post(
        "/api/v1/curation/ai-repair-preview",
        json={"text": broken_text},
        headers=headers,
    )
    assert rep_res.status_code == 200
    rep_data = rep_res.json()
    assert rep_data["changed"] is True
    assert rep_data["after"] == "Dokumen kebijakan internal."
    assert rep_data["mode"] == "local_heuristic"

    # 4. Apply repaired text -> creates immutable v2
    cur_res = await client.post(
        f"/api/v1/versions/{v1_id}/curation",
        json={"actions": [{"row_id": "row_000001", "action": "edit", "text": rep_data["after"]}]},
        headers=headers,
    )
    assert cur_res.status_code == 200
    assert cur_res.json()["version_number"] == 2
