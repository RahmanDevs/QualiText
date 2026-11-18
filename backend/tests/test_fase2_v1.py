"""Tests for Fase 2 (v1.0 P1): all 5 DQI dimensions, HTML report, version compare, API tokens."""

import json
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


@pytest.fixture
async def fase2_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[tuple[AsyncClient, str, str], None]:
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("LOCAL_STORAGE_PATH", str(tmp_path / "storage"))
    get_settings.cache_clear()

    db_file = tmp_path / "fase2.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as db:
        org = Organization(name="Org F2", slug="org-f2", settings={})
        db.add(org)
        await db.flush()
        ws = Workspace(organization_id=org.id, name="WS F2", slug="ws-f2", settings={})
        db.add(ws)
        await db.flush()
        user = User(
            email="lead@example.com",
            full_name="Lead F2",
            password_hash=hash_password("Pass123!"),
            is_active=True,
        )
        db.add(user)
        await db.flush()
        db.add(Membership(user_id=user.id, workspace_id=ws.id, role=Role.ADMIN.value))
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
            json={"email": "lead@example.com", "password": "Pass123!"},
        )
        token = login_res.json()["access_token"]
        yield ac, token, ws_id

    app.dependency_overrides.clear()
    await engine.dispose()
    get_settings.cache_clear()


async def test_fase2_full_5_dimension_dqi_report_compare_and_tokens(
    fase2_client: tuple[AsyncClient, str, str],
) -> None:
    client, token, ws_id = fase2_client
    headers = {"Authorization": f"Bearer {token}"}

    ds_res = await client.post(
        f"/api/v1/workspaces/{ws_id}/datasets",
        json={"name": "Dataset v1.0", "dataset_type": "sft"},
        headers=headers,
    )
    ds_id = ds_res.json()["id"]

    rows = [
        json.dumps(
            {
                "prompt": "Jelaskan cara kerja pipeline NLP dalam format JSON.",
                "response": '{"pipeline": ["tokenisasi", "normalisasi"]}',
            }
        ),
        json.dumps(
            {
                "prompt": "Apa manfaat deduplikasi semantik pada data latih LLM?",
                "response": "Mengurangi overfitting dan menghemat biaya pelatihan model.",
            }
        ),
    ]
    up_res = await client.post(
        f"/api/v1/datasets/{ds_id}/versions/upload-direct",
        files={"file": ("v1.jsonl", ("\n".join(rows) + "\n").encode(), "application/jsonl")},
        data={"source_format": "jsonl"},
        headers=headers,
    )
    v1_id = up_res.json()["id"]

    # Run all 11 modules across all 5 dimensions
    all_modules = [
        "token_analytics",
        "artifacts",
        "pii",
        "language",
        "toxicity",
        "dedup_exact_near",
        "dedup_semantic",
        "topic_ngram",
        "instruction_format",
        "llm_judge",
        "bias_scanner",
    ]
    run_res = await client.post(
        f"/api/v1/versions/{v1_id}/runs",
        json={"modules": all_modules, "seed": 42},
        headers=headers,
    )
    assert run_res.status_code == 201
    run_data = run_res.json()
    assert run_data["status"] == "completed"
    assert run_data["dqi_summary"]["is_partial"] is False
    assert len(run_data["dqi_summary"]["dimensions"]) == 5

    # HTML Quality Report
    rep_res = await client.get(f"/api/v1/runs/{run_data['id']}/report.html", headers=headers)
    assert rep_res.status_code == 200
    assert "Laporan Kualitas Dataset" in rep_res.text

    # Curate v1 -> v2 and compare versions
    cur_res = await client.post(
        f"/api/v1/versions/{v1_id}/curation",
        json={"actions": [{"row_id": "row_000001", "action": "approve"}]},
        headers=headers,
    )
    v2_id = cur_res.json()["id"]

    cmp_res = await client.get(f"/api/v1/versions/{v1_id}/compare/{v2_id}", headers=headers)
    assert cmp_res.status_code == 200
    assert cmp_res.json()["changed_count"] == 1

    # API Token lifecycle
    tok_res = await client.post(
        "/api/v1/api-tokens",
        json={"name": "CI Export Token", "scopes": ["dataset:read", "export:create"]},
        headers=headers,
    )
    assert tok_res.status_code == 201
    tok_data = tok_res.json()
    assert tok_data["token"].startswith("qt_")

    list_tok = await client.get("/api/v1/api-tokens", headers=headers)
    assert list_tok.status_code == 200
    assert len(list_tok.json()) == 1

    del_tok = await client.delete(f"/api/v1/api-tokens/{tok_data['id']}", headers=headers)
    assert del_tok.status_code == 204
