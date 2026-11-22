"""Regression tests for Tahap 1 P0 hotfixes and security blockers."""

import hashlib
import json
import tomllib
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.security import Role, hash_password
from app.db.base import Base
from app.db.models import (
    AuditLog,
    CurationAction,
    Membership,
    Organization,
    QualityRun,
    RunModuleResult,
    User,
    Workspace,
)
from app.db.session import get_db
from app.main import app
from app.modules.base import ModuleConfig
from app.modules.cleaning.pii import PiiDetectionModule
from app.modules.semantic.dedup import _shingles


@pytest.fixture
async def stage1_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[dict[str, Any], None]:
    """Create isolated DB/storage with Analyst+Admin in WS-A and Admin in WS-B."""
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("LOCAL_STORAGE_PATH", str(tmp_path / "storage"))
    get_settings.cache_clear()

    db_file = tmp_path / "stage1.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as db:
        org = Organization(name="Org S1", slug="org-s1", settings={})
        db.add(org)
        await db.flush()

        ws_a = Workspace(organization_id=org.id, name="WS A", slug="ws-a", settings={})
        ws_b = Workspace(organization_id=org.id, name="WS B", slug="ws-b", settings={})
        db.add_all([ws_a, ws_b])
        await db.flush()

        analyst_a = User(
            email="analyst_a@example.com",
            full_name="Analyst A",
            password_hash=hash_password("Pass123!"),
            is_active=True,
        )
        admin_a = User(
            email="admin_a@example.com",
            full_name="Admin A",
            password_hash=hash_password("Pass123!"),
            is_active=True,
        )
        admin_b = User(
            email="admin_b@example.com",
            full_name="Admin B",
            password_hash=hash_password("Pass123!"),
            is_active=True,
        )
        db.add_all([analyst_a, admin_a, admin_b])
        await db.flush()

        db.add_all(
            [
                Membership(user_id=analyst_a.id, workspace_id=ws_a.id, role=Role.ANALYST.value),
                Membership(user_id=admin_a.id, workspace_id=ws_a.id, role=Role.ADMIN.value),
                Membership(user_id=admin_b.id, workspace_id=ws_b.id, role=Role.ADMIN.value),
            ]
        )
        await db.commit()
        ws_a_id, ws_b_id = ws_a.id, ws_b.id

    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        analyst_tok = (
            await ac.post(
                "/api/v1/auth/login",
                json={"email": "analyst_a@example.com", "password": "Pass123!"},
            )
        ).json()["access_token"]
        admin_a_tok = (
            await ac.post(
                "/api/v1/auth/login",
                json={"email": "admin_a@example.com", "password": "Pass123!"},
            )
        ).json()["access_token"]
        admin_b_tok = (
            await ac.post(
                "/api/v1/auth/login",
                json={"email": "admin_b@example.com", "password": "Pass123!"},
            )
        ).json()["access_token"]

        yield {
            "client": ac,
            "session_factory": session_factory,
            "ws_a": ws_a_id,
            "ws_b": ws_b_id,
            "analyst_a_headers": {"Authorization": f"Bearer {analyst_tok}"},
            "admin_a_headers": {"Authorization": f"Bearer {admin_a_tok}"},
            "admin_b_headers": {"Authorization": f"Bearer {admin_b_tok}"},
        }

    app.dependency_overrides.clear()
    await engine.dispose()
    get_settings.cache_clear()


def test_ops01_httpx_in_main_dependencies() -> None:
    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    main_deps: list[str] = data["project"]["dependencies"]
    dev_deps: list[str] = data["dependency-groups"]["dev"]

    assert any(dep.startswith("httpx") for dep in main_deps)
    assert not any(dep.startswith("httpx") for dep in dev_deps)


async def test_sec01_and_bug04_curation_redacts_pii_and_sets_resulting_version(
    stage1_env: dict[str, Any],
) -> None:
    client: AsyncClient = stage1_env["client"]
    headers: dict[str, str] = stage1_env["analyst_a_headers"]
    ws_a: str = stage1_env["ws_a"]
    session_factory: async_sessionmaker[AsyncSession] = stage1_env["session_factory"]

    ds_res = await client.post(
        f"/api/v1/workspaces/{ws_a}/datasets",
        json={"name": "PII Curation Test", "dataset_type": "sft"},
        headers=headers,
    )
    ds_id = ds_res.json()["id"]

    row = json.dumps({"prompt": "NIK saya 3201234567890001", "response": "Siap diproses."})
    up_res = await client.post(
        f"/api/v1/datasets/{ds_id}/versions/upload-direct",
        files={"file": ("pii.jsonl", (row + "\n").encode("utf-8"), "application/jsonl")},
        data={"source_format": "jsonl"},
        headers=headers,
    )
    v1_id = up_res.json()["id"]

    cur_v2 = await client.post(
        f"/api/v1/versions/{v1_id}/curation",
        json={"actions": [{"row_id": "row_000001", "action": "mask_pii"}]},
        headers=headers,
    )
    assert cur_v2.status_code == 200
    v2_id = cur_v2.json()["id"]

    cur_v3 = await client.post(
        f"/api/v1/versions/{v2_id}/curation",
        json={
            "actions": [
                {
                    "row_id": "row_000001",
                    "action": "edit",
                    "prompt": "NIK baru 3201234567890001 diperbarui",
                    "response": "Sudah diubah",
                }
            ]
        },
        headers=headers,
    )
    assert cur_v3.status_code == 200
    v3_id = cur_v3.json()["id"]

    async with session_factory() as db:
        actions = (
            (await db.execute(select(CurationAction).order_by(CurationAction.created_at.asc())))
            .scalars()
            .all()
        )
        assert len(actions) == 2
        for act in actions:
            assert "3201234567890001" not in json.dumps(act.old_value)
            assert "3201234567890001" not in json.dumps(act.new_value)

        assert actions[0].old_value is not None
        assert "[NIK]" in actions[0].old_value["prompt"]
        assert actions[0].old_value["pii_redacted"] is True
        assert actions[0].resulting_version_id == v2_id

        assert actions[1].new_value is not None
        assert "[NIK]" in actions[1].new_value["prompt"]
        assert actions[1].new_value["pii_redacted"] is True
        assert actions[1].resulting_version_id == v3_id


async def test_sec02_cross_workspace_idor_compare_and_complete_upload(
    stage1_env: dict[str, Any],
) -> None:
    client: AsyncClient = stage1_env["client"]
    h_a: dict[str, str] = stage1_env["analyst_a_headers"]
    h_b: dict[str, str] = stage1_env["admin_b_headers"]
    ws_a: str = stage1_env["ws_a"]
    ws_b: str = stage1_env["ws_b"]

    ds_a1 = (
        await client.post(
            f"/api/v1/workspaces/{ws_a}/datasets",
            json={"name": "DS A1", "dataset_type": "raw"},
            headers=h_a,
        )
    ).json()["id"]
    ds_a2 = (
        await client.post(
            f"/api/v1/workspaces/{ws_a}/datasets",
            json={"name": "DS A2", "dataset_type": "raw"},
            headers=h_a,
        )
    ).json()["id"]
    ds_b1 = (
        await client.post(
            f"/api/v1/workspaces/{ws_b}/datasets",
            json={"name": "DS B1", "dataset_type": "raw"},
            headers=h_b,
        )
    ).json()["id"]

    v_a1 = (
        await client.post(
            f"/api/v1/datasets/{ds_a1}/versions/upload-direct",
            files={"file": ("a1.txt", b"teks a1\n", "text/plain")},
            data={"source_format": "txt"},
            headers=h_a,
        )
    ).json()["id"]
    v_a2 = (
        await client.post(
            f"/api/v1/datasets/{ds_a2}/versions/upload-direct",
            files={"file": ("a2.txt", b"teks a2\n", "text/plain")},
            data={"source_format": "txt"},
            headers=h_a,
        )
    ).json()["id"]
    v_b1 = (
        await client.post(
            f"/api/v1/datasets/{ds_b1}/versions/upload-direct",
            files={"file": ("b1.txt", b"teks b1\n", "text/plain")},
            data={"source_format": "txt"},
            headers=h_b,
        )
    ).json()["id"]

    # Cross-workspace compare must fail with 403 FORBIDDEN
    idor_cmp = await client.get(f"/api/v1/versions/{v_a1}/compare/{v_b1}", headers=h_a)
    assert idor_cmp.status_code == 403

    # Same-workspace compare across different datasets must fail with 400 VERSION_MISMATCH
    mismatch_cmp = await client.get(f"/api/v1/versions/{v_a1}/compare/{v_a2}", headers=h_a)
    assert mismatch_cmp.status_code == 400
    assert mismatch_cmp.json()["error"]["code"] == "VERSION_MISMATCH"

    # complete_upload across mismatched dataset must fail with 400 VERSION_MISMATCH
    init_a = (
        await client.post(
            f"/api/v1/datasets/{ds_a1}/versions/uploads",
            json={"filename": "chunked.txt", "source_format": "txt", "total_chunks": 1},
            headers=h_a,
        )
    ).json()
    init_b = (
        await client.post(
            f"/api/v1/datasets/{ds_b1}/versions/uploads",
            json={"filename": "chunked_b.txt", "source_format": "txt", "total_chunks": 1},
            headers=h_b,
        )
    ).json()

    cross_complete = await client.post(
        f"/api/v1/datasets/{ds_a1}/versions/{init_b['version_id']}/complete?total_chunks=1",
        headers=h_a,
    )
    assert cross_complete.status_code == 400
    assert cross_complete.json()["error"]["code"] == "VERSION_MISMATCH"

    wrong_ds_complete = await client.post(
        f"/api/v1/datasets/{ds_a2}/versions/{init_a['version_id']}/complete?total_chunks=1",
        headers=h_a,
    )
    assert wrong_ds_complete.status_code == 400
    assert wrong_ds_complete.json()["error"]["code"] == "VERSION_MISMATCH"


async def test_sec03_quality_gate_no_bypass_and_requires_completed_run(
    stage1_env: dict[str, Any],
) -> None:
    client: AsyncClient = stage1_env["client"]
    h_analyst: dict[str, str] = stage1_env["analyst_a_headers"]
    h_admin: dict[str, str] = stage1_env["admin_a_headers"]
    ws_a: str = stage1_env["ws_a"]
    session_factory: async_sessionmaker[AsyncSession] = stage1_env["session_factory"]

    pol_res = await client.put(
        f"/api/v1/workspaces/{ws_a}/policy",
        json={"min_dqi": 60.0, "block_if_critical_pii": True, "is_active": True},
        headers=h_admin,
    )
    assert pol_res.status_code == 200

    ds_id = (
        await client.post(
            f"/api/v1/workspaces/{ws_a}/datasets",
            json={"name": "Gate Test", "dataset_type": "sft"},
            headers=h_analyst,
        )
    ).json()["id"]

    clean_row = json.dumps(
        {"prompt": "Apa itu tokenisasi teks?", "response": "Pemecahan teks menjadi unit token."}
    )
    v1_id = (
        await client.post(
            f"/api/v1/datasets/{ds_id}/versions/upload-direct",
            files={"file": ("clean.jsonl", (clean_row + "\n").encode(), "application/jsonl")},
            data={"source_format": "jsonl"},
            headers=h_analyst,
        )
    ).json()["id"]

    # Analyst cannot disable quality gate
    bypass_res = await client.post(
        f"/api/v1/versions/{v1_id}/exports",
        json={"export_format": "jsonl", "enforce_quality_gate": False},
        headers=h_analyst,
    )
    assert bypass_res.status_code == 403

    # Exporting before any completed quality run must fail when min_dqi > 0
    no_run_res = await client.post(
        f"/api/v1/versions/{v1_id}/exports",
        json={"export_format": "jsonl"},
        headers=h_analyst,
    )
    assert no_run_res.status_code == 409
    assert no_run_res.json()["error"]["code"] == "QUALITY_GATE_NO_RUN"

    # Admin with policy:manage may explicitly override and audit log records the bypass
    admin_override = await client.post(
        f"/api/v1/versions/{v1_id}/exports",
        json={"export_format": "jsonl", "enforce_quality_gate": False},
        headers=h_admin,
    )
    assert admin_override.status_code == 201

    async with session_factory() as db:
        logs = (
            (await db.execute(select(AuditLog).where(AuditLog.action == "dataset.export")))
            .scalars()
            .all()
        )
        assert any(log.metadata_json.get("quality_gate_bypassed") is True for log in logs)

    # After completing a quality run >= 60, Analyst export succeeds
    run_res = await client.post(
        f"/api/v1/versions/{v1_id}/runs",
        json={"seed": 42},
        headers=h_analyst,
    )
    assert run_res.status_code == 201
    assert run_res.json()["dqi_total"] >= 60.0

    ok_export = await client.post(
        f"/api/v1/versions/{v1_id}/exports",
        json={"export_format": "jsonl"},
        headers=h_analyst,
    )
    assert ok_export.status_code == 201


async def test_bug02_html_report_escapes_xss(stage1_env: dict[str, Any]) -> None:
    client: AsyncClient = stage1_env["client"]
    headers: dict[str, str] = stage1_env["analyst_a_headers"]
    ws_a: str = stage1_env["ws_a"]
    session_factory: async_sessionmaker[AsyncSession] = stage1_env["session_factory"]

    ds_id = (
        await client.post(
            f"/api/v1/workspaces/{ws_a}/datasets",
            json={"name": "XSS Report Test", "dataset_type": "raw"},
            headers=headers,
        )
    ).json()["id"]
    v1_id = (
        await client.post(
            f"/api/v1/datasets/{ds_id}/versions/upload-direct",
            files={"file": ("xss.txt", b"contoh kalimat aman\n", "text/plain")},
            data={"source_format": "txt"},
            headers=headers,
        )
    ).json()["id"]

    async with session_factory() as db:
        run = QualityRun(
            version_id=v1_id,
            status="completed",
            current_stage="completed",
            progress_pct=100,
            dqi_total=80.0,
            dqi_summary={
                "status_label": "<script>alert(1)</script>",
                "dimensions": [
                    {
                        "label": "<b>Dimensi</b>",
                        "score": 80.0,
                        "weight": 1.0,
                        "findings_count": 1,
                    }
                ],
            },
        )
        db.add(run)
        await db.flush()
        db.add(
            RunModuleResult(
                run_id=run.id,
                module="<img src=x onerror=alert(1)>",
                dimension="safety_bias",
                module_version="0.1.0",
                metrics={"note": 1.0},
                findings_count=1,
            )
        )
        await db.commit()
        run_id = run.id

    rep = await client.get(f"/api/v1/runs/{run_id}/report.html", headers=headers)
    assert rep.status_code == 200
    assert "<script>" not in rep.text
    assert "<img " not in rep.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rep.text
    assert "&lt;img src=x onerror=alert(1)&gt;" in rep.text
    assert "&lt;b&gt;Dimensi&lt;/b&gt;" in rep.text


def test_bug05_dedup_deterministic_short_text_and_pii_always_critical() -> None:
    expected = {int.from_bytes(hashlib.blake2b(b"abc", digest_size=4).digest())}
    assert _shingles("abc") == expected

    df = pl.DataFrame([{"row_id": "row_000001", "text": "NIK 3201234567890001"}]).lazy()
    _res, findings_df = PiiDetectionModule().run(df, ModuleConfig(params={"mode": "mask"}), seed=42)
    assert findings_df.height == 1
    assert findings_df["severity"][0] == "critical"
