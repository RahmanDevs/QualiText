"""End-to-end tests for Fase 1 MVP pipeline: upload -> run -> DQI -> explorer -> curate -> export."""  # noqa: E501

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
async def fase1_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[tuple[AsyncClient, str, str], None]:
    """Set up isolated DB + local storage + authenticated Analyst user."""
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("LOCAL_STORAGE_PATH", str(tmp_path / "storage"))
    get_settings.cache_clear()

    db_file = tmp_path / "fase1.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as db:
        org = Organization(name="Org F1", slug="org-f1", settings={})
        db.add(org)
        await db.flush()
        ws = Workspace(organization_id=org.id, name="WS F1", slug="ws-f1", settings={})
        db.add(ws)
        await db.flush()
        user = User(
            email="analyst@example.com",
            full_name="Analyst F1",
            password_hash=hash_password("Pass123!"),
            is_active=True,
        )
        db.add(user)
        await db.flush()
        db.add(Membership(user_id=user.id, workspace_id=ws.id, role=Role.ANALYST.value))
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
            json={"email": "analyst@example.com", "password": "Pass123!"},
        )
        token = login_res.json()["access_token"]
        yield ac, token, ws_id

    app.dependency_overrides.clear()
    await engine.dispose()
    get_settings.cache_clear()


async def test_full_fase1_e2e_pipeline(
    fase1_client: tuple[AsyncClient, str, str],
) -> None:
    client, token, ws_id = fase1_client
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Create dataset
    ds_res = await client.post(
        f"/api/v1/workspaces/{ws_id}/datasets",
        json={"name": "SFT CS Indonesia", "description": "Sintetis", "dataset_type": "sft"},
        headers=headers,
    )
    assert ds_res.status_code == 201
    dataset_id = ds_res.json()["id"]

    # 2. Upload JSONL with PII (NIK), artifact (HTML), duplicate, and 1 invalid line
    rows = [
        json.dumps({"prompt": "Halo NIK saya 3201234567890001", "response": "Baik, dicatat."}),
        json.dumps({"prompt": "<p>Apa itu NLP?</p>", "response": "Pemrosesan bahasa alami."}),
        json.dumps({"prompt": "<p>Apa itu NLP?</p>", "response": "Pemrosesan bahasa alami."}),
        "{broken json line",
    ]
    payload_bytes = ("\n".join(rows) + "\n").encode("utf-8")

    up_res = await client.post(
        f"/api/v1/datasets/{dataset_id}/versions/upload-direct",
        files={"file": ("sample.jsonl", payload_bytes, "application/jsonl")},
        data={"source_format": "jsonl"},
        headers=headers,
    )
    assert up_res.status_code == 201
    v1 = up_res.json()
    assert v1["row_count"] == 4
    assert v1["invalid_row_count"] == 1
    v1_id = v1["id"]

    # 3. Trigger quality run twice with same seed -> deterministic DQI
    run_res_1 = await client.post(
        f"/api/v1/versions/{v1_id}/runs",
        json={"seed": 42},
        headers=headers,
    )
    assert run_res_1.status_code == 201
    r1 = run_res_1.json()
    assert r1["status"] == "completed"
    assert r1["dqi_summary"]["is_partial"] is True
    assert r1["dqi_summary"]["critical_capped"] is True
    assert r1["dqi_total"] <= 60.0

    run_res_2 = await client.post(
        f"/api/v1/versions/{v1_id}/runs",
        json={"seed": 42},
        headers=headers,
    )
    assert run_res_2.json()["dqi_total"] == r1["dqi_total"]

    # 4. Explorer masks PII by default and blocks reveal_pii for Analyst
    exp_res = await client.get(f"/api/v1/versions/{v1_id}/rows", headers=headers)
    assert exp_res.status_code == 200
    first_row = exp_res.json()["items"][0]
    assert "[NIK]" in first_row["prompt"]
    assert "3201234567890001" not in first_row["prompt"]

    rev_res = await client.get(f"/api/v1/versions/{v1_id}/rows?reveal_pii=true", headers=headers)
    assert rev_res.status_code == 403

    # 5. Export blocked by Quality Gate due to unmasked PII
    blocked_exp = await client.post(
        f"/api/v1/versions/{v1_id}/exports",
        json={"export_format": "jsonl"},
        headers=headers,
    )
    assert blocked_exp.status_code == 409
    assert blocked_exp.json()["error"]["code"] == "QUALITY_GATE_PII_BLOCKED"

    # 6. Curate: mask PII on row_000001 and reject invalid row_000004 -> creates immutable v2
    cur_res = await client.post(
        f"/api/v1/versions/{v1_id}/curation",
        json={
            "actions": [
                {"row_id": "row_000001", "action": "mask_pii"},
                {"row_id": "row_000004", "action": "reject"},
            ]
        },
        headers=headers,
    )
    assert cur_res.status_code == 200
    v2 = cur_res.json()
    assert v2["version_number"] == 2
    assert v2["parent_version_id"] == v1_id

    # 7. Export v2 succeeds now that PII is masked and invalid row is rejected!
    ok_exp = await client.post(
        f"/api/v1/versions/{v2['id']}/exports",
        json={"export_format": "jsonl", "exclude_rejected": True},
        headers=headers,
    )
    assert ok_exp.status_code == 201
    exp_data = ok_exp.json()
    assert exp_data["manifest"]["exported_rows"] == 3

    dl_res = await client.get(f"/api/v1/exports/{exp_data['id']}/download", headers=headers)
    assert dl_res.status_code == 200
    assert "[NIK]" in dl_res.text
    assert "3201234567890001" not in dl_res.text
