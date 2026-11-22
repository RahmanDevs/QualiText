"""Regression tests for Tahap 2: Celery dispatch, streaming uploads, A5/B1-B2/F5, and Alembic."""

import io
import json
import zipfile
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import httpx
import polars as pl
import pytest
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from alembic import command
from app.core.config import get_settings
from app.core.security import Role, hash_password
from app.db.base import Base
from app.db.models import (
    Membership,
    Organization,
    RowFindingRecord,
    User,
    Workspace,
)
from app.db.session import get_db
from app.main import app
from app.modules.base import ModuleConfig
from app.modules.cleaning.pii import detect_pii_spans
from app.modules.ingestion.token_analytics import TokenAnalyticsModule
from app.services.repair_service import repair_parsed_text_async
from app.services.run_service import execute_run_by_id


@pytest.fixture
async def stage2_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[dict[str, Any], None]:
    """Isolated DB + local storage + Admin/Curator user for Stage 2 tests."""
    storage_dir = tmp_path / "storage"
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("LOCAL_STORAGE_PATH", str(storage_dir))
    get_settings.cache_clear()

    db_file = tmp_path / "stage2.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as db:
        org = Organization(name="Org S2", slug="org-s2", settings={})
        db.add(org)
        await db.flush()
        ws = Workspace(organization_id=org.id, name="WS S2", slug="ws-s2", settings={})
        db.add(ws)
        await db.flush()
        user = User(
            email="admin_s2@example.com",
            full_name="Admin S2",
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
        tok = (
            await ac.post(
                "/api/v1/auth/login",
                json={"email": "admin_s2@example.com", "password": "Pass123!"},
            )
        ).json()["access_token"]
        yield {
            "client": ac,
            "headers": {"Authorization": f"Bearer {tok}"},
            "ws_id": ws_id,
            "session_factory": session_factory,
            "storage_dir": storage_dir,
        }

    app.dependency_overrides.clear()
    await engine.dispose()
    get_settings.cache_clear()


async def test_perf01_celery_async_dispatch_and_bulk_findings(
    stage2_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    client: AsyncClient = stage2_env["client"]
    headers: dict[str, str] = stage2_env["headers"]
    ws_id: str = stage2_env["ws_id"]
    session_factory: async_sessionmaker[AsyncSession] = stage2_env["session_factory"]

    ds_id = (
        await client.post(
            f"/api/v1/workspaces/{ws_id}/datasets",
            json={"name": "Async Run DS", "dataset_type": "sft"},
            headers=headers,
        )
    ).json()["id"]

    rows = [
        json.dumps({"prompt": "NIK saya 3201234567890001", "response": "Baik."}),
        json.dumps({"prompt": "<p>Halo</p>", "response": "Hai."}),
    ]
    v1_id = (
        await client.post(
            f"/api/v1/datasets/{ds_id}/versions/upload-direct",
            files={"file": ("async.jsonl", ("\n".join(rows) + "\n").encode(), "application/jsonl")},
            data={"source_format": "jsonl"},
            headers=headers,
        )
    ).json()["id"]

    dispatched_ids: list[str] = []

    class _FakeTask:
        @staticmethod
        def delay(run_id: str) -> None:
            dispatched_ids.append(run_id)

    monkeypatch.setenv("CELERY_TASK_ALWAYS_EAGER", "false")
    get_settings.cache_clear()
    from app.workers import tasks as worker_tasks

    monkeypatch.setattr(worker_tasks, "execute_quality_run_task", _FakeTask)

    run_res = await client.post(
        f"/api/v1/versions/{v1_id}/runs",
        json={"modules": ["pii", "artifacts"], "seed": 42},
        headers=headers,
    )
    assert run_res.status_code == 201
    queued_run = run_res.json()
    assert queued_run["status"] == "queued"
    assert dispatched_ids == [queued_run["id"]]

    async with session_factory() as worker_db:
        completed = await execute_run_by_id(worker_db, queued_run["id"])
        assert completed.status == "completed"
        assert completed.progress_pct == 100
        findings_count = (
            await worker_db.execute(
                select(func.count(RowFindingRecord.id)).where(
                    RowFindingRecord.run_id == queued_run["id"]
                )
            )
        ).scalar_one()
        assert findings_count >= 2


async def test_perf01_streaming_upload_limit_and_chunked_assembly(
    stage2_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    client: AsyncClient = stage2_env["client"]
    headers: dict[str, str] = stage2_env["headers"]
    ws_id: str = stage2_env["ws_id"]
    storage_dir: Path = stage2_env["storage_dir"]

    ds_id = (
        await client.post(
            f"/api/v1/workspaces/{ws_id}/datasets",
            json={"name": "Chunked Upload DS", "dataset_type": "raw"},
            headers=headers,
        )
    ).json()["id"]

    # 1. Multi-chunk assembly cleans up temporary chunk files
    init_res = await client.post(
        f"/api/v1/datasets/{ds_id}/versions/uploads",
        json={"filename": "big.txt", "source_format": "txt", "total_chunks": 2},
        headers=headers,
    )
    assert init_res.status_code == 200
    ver_id = init_res.json()["version_id"]

    chunk_0 = b"Baris pertama korpus.\n"
    chunk_1 = b"Baris kedua korpus.\n"
    assert (
        await client.put(
            f"/api/v1/datasets/{ds_id}/versions/{ver_id}/chunks/0",
            content=chunk_0,
            headers=headers,
        )
    ).status_code == 200
    assert (
        await client.put(
            f"/api/v1/datasets/{ds_id}/versions/{ver_id}/chunks/1",
            content=chunk_1,
            headers=headers,
        )
    ).status_code == 200

    comp_res = await client.post(
        f"/api/v1/datasets/{ds_id}/versions/{ver_id}/complete?total_chunks=2",
        headers=headers,
    )
    assert comp_res.status_code == 200
    comp_data = comp_res.json()
    assert comp_data["row_count"] == 2
    assert not (storage_dir / "uploads" / ver_id / "chunk_00000").exists()
    assert not (storage_dir / "uploads" / ver_id / "chunk_00001").exists()

    # 2. Direct upload streaming limit rejects oversized stream with 413
    from app.api.v1 import datasets as datasets_router

    monkeypatch.setattr(datasets_router, "MAX_DIRECT_UPLOAD_BYTES", 64)
    too_big = await client.post(
        f"/api/v1/datasets/{ds_id}/versions/upload-direct",
        files={"file": ("big.txt", b"x" * 128, "text/plain")},
        data={"source_format": "txt"},
        headers=headers,
    )
    assert too_big.status_code == 413
    assert too_big.json()["error"]["code"] == "FILE_TOO_LARGE"


async def test_perf01_repair_parsed_text_async(monkeypatch: pytest.MonkeyPatch) -> None:
    text_out, mode = await repair_parsed_text_async("Kata terpo-\ntong")
    assert text_out == "Kata terpotong"
    assert mode == "local_heuristic"

    monkeypatch.setenv("ALLOW_EXTERNAL_LLM", "true")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "gpt-test")
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.local/v1")
    get_settings.cache_clear()

    async def _fake_post(_self: httpx.AsyncClient, *_args: Any, **_kwargs: Any) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "Teks bersih."}}]},
            request=httpx.Request("POST", "https://llm.local/v1/chat/completions"),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)
    ai_out, ai_mode = await repair_parsed_text_async("Kata rusak")
    assert ai_out == "Teks bersih."
    assert ai_mode == "llm"


async def test_f5_huggingface_export_manifest_and_get_export(stage2_env: dict[str, Any]) -> None:
    client: AsyncClient = stage2_env["client"]
    headers: dict[str, str] = stage2_env["headers"]
    ws_id: str = stage2_env["ws_id"]

    ds_id = (
        await client.post(
            f"/api/v1/workspaces/{ws_id}/datasets",
            json={"name": "HF Export DS", "description": "Dataset uji HF", "dataset_type": "sft"},
            headers=headers,
        )
    ).json()["id"]

    row = json.dumps({"prompt": "Apa itu LLM?", "response": "Model bahasa berukuran besar."})
    v1_id = (
        await client.post(
            f"/api/v1/datasets/{ds_id}/versions/upload-direct",
            files={"file": ("hf.jsonl", (row + "\n").encode(), "application/jsonl")},
            data={"source_format": "jsonl"},
            headers=headers,
        )
    ).json()["id"]

    run_res = await client.post(
        f"/api/v1/versions/{v1_id}/runs",
        json={"modules": ["token_analytics", "pii"], "seed": 77},
        headers=headers,
    )
    assert run_res.status_code == 201

    exp_res = await client.post(
        f"/api/v1/versions/{v1_id}/exports",
        json={"export_format": "huggingface"},
        headers=headers,
    )
    assert exp_res.status_code == 201
    exp_id = exp_res.json()["id"]

    meta_res = await client.get(f"/api/v1/exports/{exp_id}", headers=headers)
    assert meta_res.status_code == 200
    meta = meta_res.json()
    assert meta["export_format"] == "huggingface"
    assert meta["manifest"]["seed"] == 77
    assert meta["manifest"]["config_snapshot"]["modules"] == ["token_analytics", "pii"]

    dl_res = await client.get(f"/api/v1/exports/{exp_id}/download", headers=headers)
    assert dl_res.status_code == 200
    assert dl_res.headers["content-type"] == "application/zip"

    with zipfile.ZipFile(io.BytesIO(dl_res.content), mode="r") as zf:
        names = set(zf.namelist())
        assert {
            "data/train-00000-of-00001.parquet",
            "dataset_info.json",
            "README.md",
            "manifest.json",
        }.issubset(names)
        info = json.loads(zf.read("dataset_info.json").decode("utf-8"))
        assert info["splits"]["train"]["num_examples"] == 1
        pq_df = pl.read_parquet(io.BytesIO(zf.read("data/train-00000-of-00001.parquet")))
        assert pq_df.height == 1


def test_a5_token_analytics_histogram_and_tokenizer_fallback() -> None:
    df = pl.DataFrame(
        [
            {"row_id": "r1", "text": "halo dunia", "is_valid": True},
            {"row_id": "r2", "text": "kata " * 150, "is_valid": True},
            {"row_id": "r3", "text": "panjang " * 3800, "is_valid": True},
        ]
    ).lazy()
    res, findings_df = TokenAnalyticsModule().run(
        df,
        ModuleConfig(params={"tokenizer": "tiktoken:cl100k_base", "max_tokens": 4096}),
        seed=42,
    )
    bucket_sum = (
        res.metrics["hist_0_128"]
        + res.metrics["hist_129_512"]
        + res.metrics["hist_513_1024"]
        + res.metrics["hist_1025_2048"]
        + res.metrics["hist_2049_4096"]
        + res.metrics["hist_gt_4096"]
    )
    assert bucket_sum == res.metrics["total_rows"] == 3.0
    assert res.metrics["hist_0_128"] == 1.0
    assert res.metrics["hist_gt_4096"] == 1.0
    assert res.metrics["max_tokens_observed"] > 4096.0
    assert findings_df.height == 1


async def test_b1_b2_pii_person_address_and_hash_drop_row(stage2_env: dict[str, Any]) -> None:
    # Zero false positives on ordinary Indonesian city sentence
    assert detect_pii_spans("Ibu kota Indonesia adalah Jakarta.") == []

    spans = detect_pii_spans(
        "Nama: Budi Santoso dan Bpk. Agus Salim tinggal di Jl. Sudirman No. 45 RT 02/RW 05"
    )
    types = {s.entity_type for s in spans}
    assert "PII_PERSON" in types
    assert "PII_ADDRESS" in types

    client: AsyncClient = stage2_env["client"]
    headers: dict[str, str] = stage2_env["headers"]
    ws_id: str = stage2_env["ws_id"]

    ds_id = (
        await client.post(
            f"/api/v1/workspaces/{ws_id}/datasets",
            json={"name": "PII B1 B2 DS", "dataset_type": "sft"},
            headers=headers,
        )
    ).json()["id"]

    rows = [
        json.dumps({"prompt": "Nama: Budi Santoso", "response": "Catat pesanan."}),
        json.dumps({"prompt": "Apa kabar?", "response": "Baik."}),
    ]
    v1_id = (
        await client.post(
            f"/api/v1/datasets/{ds_id}/versions/upload-direct",
            files={
                "file": ("pii_b2.jsonl", ("\n".join(rows) + "\n").encode(), "application/jsonl")
            },
            data={"source_format": "jsonl"},
            headers=headers,
        )
    ).json()["id"]

    # hash_pii transforms PII with [PII_PERSON:...] digest
    v2_res = await client.post(
        f"/api/v1/versions/{v1_id}/curation",
        json={"actions": [{"row_id": "row_000001", "action": "hash_pii"}]},
        headers=headers,
    )
    assert v2_res.status_code == 200
    v2_id = v2_res.json()["id"]

    rows_v2 = (await client.get(f"/api/v1/versions/{v2_id}/rows", headers=headers)).json()["items"]
    assert "[PII_PERSON:" in rows_v2[0]["prompt"]
    assert "Budi Santoso" not in rows_v2[0]["prompt"]

    # drop_row removes row from resulting immutable version v3
    v3_res = await client.post(
        f"/api/v1/versions/{v2_id}/curation",
        json={"actions": [{"row_id": "row_000001", "action": "drop_row"}]},
        headers=headers,
    )
    assert v3_res.status_code == 200
    v3_data = v3_res.json()
    assert v3_data["row_count"] == 1

    rows_v3 = (await client.get(f"/api/v1/versions/{v3_data['id']}/rows", headers=headers)).json()[
        "items"
    ]
    assert len(rows_v3) == 1
    assert rows_v3[0]["row_id"] == "row_000002"


def test_alembic_0003_migration_sqlite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    backend_root = Path(__file__).resolve().parents[1]
    db_path = tmp_path / "alembic_test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    get_settings.cache_clear()

    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    command.upgrade(cfg, "0003_fase2_row_findings_partitioning")

    import sqlite3

    with sqlite3.connect(db_path) as conn:
        rev = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        assert rev == ("0003_fase2_row_findings_partitioning",)
        indexes = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='row_findings'"
            ).fetchall()
        }
        assert "ix_row_findings_run_row" in indexes

    get_settings.cache_clear()
