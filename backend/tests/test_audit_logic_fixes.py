"""Regression tests verifying all 14 Senior Engineer logic audit fixes."""

import base64
import hashlib
import hmac
import io
import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import jwt
import polars as pl
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.observability import render_prometheus_metrics, reset_metrics
from app.core.rate_limit import login_rate_limiter
from app.core.security import Role, create_token, hash_password
from app.db.base import Base
from app.db.models import (
    DatasetVersion,
    Membership,
    Organization,
    RowFindingRecord,
    RunModuleResult,
    User,
    Workspace,
)
from app.db.session import get_db
from app.main import app
from app.modules.base import ModuleConfig
from app.modules.cleaning.language import LanguageDetectionModule
from app.modules.ingestion.connectors import ConnectorConfig, DatabaseConnector, SharePointConnector
from app.modules.ingestion.validator import parse_and_validate_stream
from app.modules.instruction.llm_judge import LlmJudgeModule
from app.modules.instruction.preference import PreferenceConsistencyModule
from app.modules.semantic import embeddings as emb_mod
from app.modules.semantic.semantic_dedup import SemanticDedupModule
from app.services import run_service
from app.services.dataset_service import load_version_parquet_bytes


@pytest.fixture
async def audit_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[dict[str, Any], None]:
    """Isolated DB, storage, and RBAC users for logic audit tests."""
    login_rate_limiter.reset()
    reset_metrics()
    emb_mod.clear_embedding_cache()
    storage_dir = tmp_path / "storage"
    db_file = tmp_path / "audit.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_file}")
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("LOCAL_STORAGE_PATH", str(storage_dir))
    monkeypatch.setenv("CELERY_TASK_ALWAYS_EAGER", "true")
    monkeypatch.setenv("ALLOW_EXTERNAL_LLM", "false")
    monkeypatch.setenv("LLM_PROVIDER", "placeholder")
    monkeypatch.setenv("WEBHOOK_ALLOW_PRIVATE_TARGETS", "false")
    get_settings.cache_clear()

    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as db:
        org = Organization(name="Audit Org", slug="audit-org", settings={})
        db.add(org)
        await db.flush()
        ws = Workspace(organization_id=org.id, name="Audit WS", slug="audit-ws", settings={})
        db.add(ws)
        await db.flush()

        admin = User(
            email="admin_audit@example.com",
            full_name="Admin Audit",
            password_hash=hash_password("Pass123!"),
            is_active=True,
            is_superadmin=True,
        )
        gov = User(
            email="gov_audit@example.com",
            full_name="Governance Audit",
            password_hash=hash_password("Pass123!"),
            is_active=True,
            is_superadmin=False,
        )
        db.add_all([admin, gov])
        await db.flush()
        db.add_all(
            [
                Membership(user_id=admin.id, workspace_id=ws.id, role=Role.ADMIN.value),
                Membership(user_id=gov.id, workspace_id=ws.id, role=Role.GOVERNANCE.value),
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
        admin_tok = (
            await ac.post(
                "/api/v1/auth/login",
                json={"email": "admin_audit@example.com", "password": "Pass123!"},
            )
        ).json()["access_token"]
        gov_tok = (
            await ac.post(
                "/api/v1/auth/login",
                json={"email": "gov_audit@example.com", "password": "Pass123!"},
            )
        ).json()["access_token"]
        yield {
            "client": ac,
            "admin_headers": {"Authorization": f"Bearer {admin_tok}"},
            "gov_headers": {"Authorization": f"Bearer {gov_tok}"},
            "ws_id": ws_id,
            "db_file": db_file,
            "session_factory": session_factory,
        }

    app.dependency_overrides.clear()
    await engine.dispose()
    login_rate_limiter.reset()
    emb_mod.clear_embedding_cache()
    get_settings.cache_clear()


async def test_logic01_missing_dimension_threshold_blocks_export(audit_env: dict[str, Any]) -> None:
    client: AsyncClient = audit_env["client"]
    headers: dict[str, str] = audit_env["admin_headers"]
    ws_id: str = audit_env["ws_id"]

    pol_res = await client.put(
        f"/api/v1/workspaces/{ws_id}/policy",
        json={
            "min_dqi": 0.0,
            "block_if_critical_pii": True,
            "is_active": True,
            "dimension_thresholds": {"safety_bias": 80.0},
        },
        headers=headers,
    )
    assert pol_res.status_code == 200

    ds_id = (
        await client.post(
            f"/api/v1/workspaces/{ws_id}/datasets",
            json={"name": "Dim Missing DS", "dataset_type": "sft"},
            headers=headers,
        )
    ).json()["id"]
    row = json.dumps({"prompt": "Apa itu tokenisasi?", "response": "Pemecahan kata."})
    v1_id = (
        await client.post(
            f"/api/v1/datasets/{ds_id}/versions/upload-direct",
            files={"file": ("clean.jsonl", (row + "\n").encode(), "application/jsonl")},
            data={"source_format": "jsonl"},
            headers=headers,
        )
    ).json()["id"]

    # Run only token_analytics -> safety_bias was never evaluated
    run_res = await client.post(
        f"/api/v1/versions/{v1_id}/runs",
        json={"modules": ["token_analytics"], "seed": 42},
        headers=headers,
    )
    assert run_res.status_code == 201

    exp_res = await client.post(
        f"/api/v1/versions/{v1_id}/exports",
        json={"export_format": "jsonl"},
        headers=headers,
    )
    assert exp_res.status_code == 409
    assert exp_res.json()["error"]["code"] == "QUALITY_GATE_DIMENSION_MISSING"


def test_logic02_connectors_ssrf_and_internal_db_blocked(audit_env: dict[str, Any]) -> None:
    db_file: Path = audit_env["db_file"]

    sp = SharePointConnector()
    assert (
        sp.validate_config(
            ConnectorConfig(
                connector_type="sharepoint",
                uri="https://127.0.0.1/secret.jsonl",
                options={"allow_private": True},
            )
        )
        is False
    )

    db_conn = DatabaseConnector()
    assert (
        db_conn.validate_config(
            ConnectorConfig(connector_type="database", uri=f"sqlite:///{db_file}", options={})
        )
        is False
    )
    with pytest.raises(ValueError, match="internal"):
        db_conn.fetch_bytes(
            ConnectorConfig(
                connector_type="database",
                uri=f"sqlite:///{db_file}",
                options={"table": "users"},
            )
        )


async def test_logic03_explorer_no_pii_search_oracle_and_export_prompt_pii_gate(
    audit_env: dict[str, Any],
) -> None:
    client: AsyncClient = audit_env["client"]
    headers: dict[str, str] = audit_env["admin_headers"]
    ws_id: str = audit_env["ws_id"]

    ds_id = (
        await client.post(
            f"/api/v1/workspaces/{ws_id}/datasets",
            json={"name": "PII Oracle DS", "dataset_type": "sft"},
            headers=headers,
        )
    ).json()["id"]
    row = json.dumps({"prompt": "Nomor NIK saya 3273010101900001", "response": "Baik dicatat."})
    v1_id = (
        await client.post(
            f"/api/v1/datasets/{ds_id}/versions/upload-direct",
            files={"file": ("pii.jsonl", (row + "\n").encode(), "application/jsonl")},
            data={"source_format": "jsonl"},
            headers=headers,
        )
    ).json()["id"]

    # Search raw NIK without reveal_pii -> must NOT reveal row presence
    raw_search = await client.get(
        f"/api/v1/versions/{v1_id}/rows?search=3273010101900001",
        headers=headers,
    )
    assert raw_search.status_code == 200
    assert raw_search.json()["total"] == 0

    # Search masked token [NIK] -> returns the row masked
    masked_search = await client.get(
        f"/api/v1/versions/{v1_id}/rows?search=%5BNIK%5D",
        headers=headers,
    )
    assert masked_search.status_code == 200
    assert masked_search.json()["total"] == 1

    # Edit only `text` to hide PII while leaving PII inside `prompt` -> export must STILL block
    v2_id = (
        await client.post(
            f"/api/v1/versions/{v1_id}/curation",
            json={
                "actions": [
                    {
                        "row_id": "row_000001",
                        "action": "edit",
                        "text": "Teks bersih tanpa nomor identitas",
                    }
                ]
            },
            headers=headers,
        )
    ).json()["id"]

    exp_blocked = await client.post(
        f"/api/v1/versions/{v2_id}/exports",
        json={"export_format": "jsonl"},
        headers=headers,
    )
    assert exp_blocked.status_code == 409
    assert exp_blocked.json()["error"]["code"] == "QUALITY_GATE_PII_BLOCKED"


async def test_logic04_atomic_uploads_chunk_immutability_and_validator_edges(
    audit_env: dict[str, Any],
) -> None:
    client: AsyncClient = audit_env["client"]
    headers: dict[str, str] = audit_env["admin_headers"]
    ws_id: str = audit_env["ws_id"]
    session_factory: async_sessionmaker[AsyncSession] = audit_env["session_factory"]

    ds_id = (
        await client.post(
            f"/api/v1/workspaces/{ws_id}/datasets",
            json={"name": "Atomic Upload DS", "dataset_type": "sft"},
            headers=headers,
        )
    ).json()["id"]

    # Failed direct upload (corrupt PDF with valid magic bytes) leaves zero orphan versions
    bad_pdf = await client.post(
        f"/api/v1/datasets/{ds_id}/versions/upload-direct",
        files={"file": ("corrupt.pdf", b"%PDF-1.7\ngarbage-not-a-pdf", "application/pdf")},
        data={"source_format": "pdf"},
        headers=headers,
    )
    assert bad_pdf.status_code == 400
    async with session_factory() as db:
        vers = (
            (await db.execute(select(DatasetVersion).where(DatasetVersion.dataset_id == ds_id)))
            .scalars()
            .all()
        )
        assert len(vers) == 0

    # Chunked upload immutability & negative chunk index guard
    init_res = await client.post(
        f"/api/v1/datasets/{ds_id}/versions/uploads",
        json={"filename": "ok.jsonl", "source_format": "jsonl", "total_chunks": 1},
        headers=headers,
    )
    ver_id = init_res.json()["version_id"]

    neg_chunk = await client.put(
        f"/api/v1/datasets/{ds_id}/versions/{ver_id}/chunks/-1",
        content=b"{}",
        headers=headers,
    )
    assert neg_chunk.status_code == 400
    assert neg_chunk.json()["error"]["code"] == "INVALID_CHUNK_INDEX"

    ok_line = json.dumps({"prompt": "Halo", "response": "Hai"}).encode() + b"\n"
    assert (
        await client.put(
            f"/api/v1/datasets/{ds_id}/versions/{ver_id}/chunks/0",
            content=ok_line,
            headers=headers,
        )
    ).status_code == 200
    assert (
        await client.post(
            f"/api/v1/datasets/{ds_id}/versions/{ver_id}/complete?total_chunks=1",
            headers=headers,
        )
    ).status_code == 200

    # Mutating or re-completing finalized version fails with 409 VERSION_ALREADY_FINALIZED
    re_chunk = await client.put(
        f"/api/v1/datasets/{ds_id}/versions/{ver_id}/chunks/0",
        content=b"overwrite",
        headers=headers,
    )
    assert re_chunk.status_code == 409
    assert re_chunk.json()["error"]["code"] == "VERSION_ALREADY_FINALIZED"

    re_complete = await client.post(
        f"/api/v1/datasets/{ds_id}/versions/{ver_id}/complete?total_chunks=1",
        headers=headers,
    )
    assert re_complete.status_code == 409
    assert re_complete.json()["error"]["code"] == "VERSION_ALREADY_FINALIZED"

    # CSV with extra columns does not raise AttributeError on None key
    csv_bytes = b"prompt,response\nHalo,Hai,ExtraColValue\n"
    csv_out = parse_and_validate_stream(
        io.BytesIO(csv_bytes), source_format="csv", dataset_type="sft"
    )
    assert csv_out.valid_rows == 1
    assert csv_out.detected_columns == ["prompt", "response"]

    # Preference row having prompt+text but missing chosen/rejected must still be flagged invalid
    pref_bytes = (json.dumps({"prompt": "P1", "text": "Fallback"}) + "\n").encode()
    pref_out = parse_and_validate_stream(
        io.BytesIO(pref_bytes), source_format="jsonl", dataset_type="preference"
    )
    assert pref_out.invalid_rows == 1


async def test_logic05_failed_run_rolls_back_partial_results_and_retries_idempotently(
    audit_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    client: AsyncClient = audit_env["client"]
    headers: dict[str, str] = audit_env["admin_headers"]
    ws_id: str = audit_env["ws_id"]
    session_factory: async_sessionmaker[AsyncSession] = audit_env["session_factory"]

    ds_id = (
        await client.post(
            f"/api/v1/workspaces/{ws_id}/datasets",
            json={"name": "Rollback Run DS", "dataset_type": "sft"},
            headers=headers,
        )
    ).json()["id"]
    row = json.dumps({"prompt": "NIK saya 3273010101900001", "response": "Ok"})
    v1_id = (
        await client.post(
            f"/api/v1/datasets/{ds_id}/versions/upload-direct",
            files={"file": ("rb.jsonl", (row + "\n").encode(), "application/jsonl")},
            data={"source_format": "jsonl"},
            headers=headers,
        )
    ).json()["id"]

    class _ExplodingModule:
        name = "exploding_mod"
        dimension = "safety_bias"
        version = "0.1.0"

        def run(self, data: pl.LazyFrame, config: ModuleConfig, seed: int) -> Any:
            del data, config, seed
            raise RuntimeError("boom mid-run")

    orig_mod = run_service.MODULE_REGISTRY["toxicity"]
    monkeypatch.setitem(run_service.MODULE_REGISTRY, "toxicity", _ExplodingModule())

    run_res = await client.post(
        f"/api/v1/versions/{v1_id}/runs",
        json={"modules": ["pii", "toxicity"], "seed": 42},
        headers=headers,
    )
    assert run_res.status_code == 201
    run_id = run_res.json()["id"]
    assert run_res.json()["status"] == "failed"

    async with session_factory() as db:
        mod_count = len(
            (await db.execute(select(RunModuleResult).where(RunModuleResult.run_id == run_id)))
            .scalars()
            .all()
        )
        finding_count = len(
            (await db.execute(select(RowFindingRecord).where(RowFindingRecord.run_id == run_id)))
            .scalars()
            .all()
        )
        assert mod_count == 0
        assert finding_count == 0

    # Retrying with healthy module succeeds without duplicates
    monkeypatch.setitem(run_service.MODULE_REGISTRY, "toxicity", orig_mod)
    async with session_factory() as db:
        retried = await run_service.execute_run_by_id(db, run_id)
        assert retried.status == "completed"
        mods = (
            (await db.execute(select(RunModuleResult).where(RunModuleResult.run_id == run_id)))
            .scalars()
            .all()
        )
        assert len(mods) == 2


async def test_logic06_governance_token_with_policy_manage_can_delete_dataset(
    audit_env: dict[str, Any],
) -> None:
    client: AsyncClient = audit_env["client"]
    admin_headers: dict[str, str] = audit_env["admin_headers"]
    gov_headers: dict[str, str] = audit_env["gov_headers"]
    ws_id: str = audit_env["ws_id"]

    ds_id = (
        await client.post(
            f"/api/v1/workspaces/{ws_id}/datasets",
            json={"name": "Gov Delete DS", "dataset_type": "raw"},
            headers=admin_headers,
        )
    ).json()["id"]

    tok_res = await client.post(
        "/api/v1/api-tokens",
        json={"name": "Gov Policy Token", "scopes": ["policy:manage"]},
        headers=gov_headers,
    )
    assert tok_res.status_code == 201
    gov_qt = {"Authorization": f"Bearer {tok_res.json()['token']}"}

    del_res = await client.delete(f"/api/v1/datasets/{ds_id}", headers=gov_qt)
    assert del_res.status_code == 204


def test_logic08_09_10_12_module_correctness(monkeypatch: pytest.MonkeyPatch) -> None:
    # LOGIC-08: language any -> consistency ratio 1.0 & zero findings
    lang_df = pl.DataFrame(
        [
            {"row_id": "l1", "text": "The quick brown fox jumps over the lazy dog"},
            {"row_id": "l2", "text": "Saya dan anda belajar pemrosesan bahasa alami"},
        ]
    ).lazy()
    lang_res, lang_findings = LanguageDetectionModule().run(
        lang_df, ModuleConfig(params={"expected_language": "any"}), seed=42
    )
    assert lang_res.metrics["lang_consistency_ratio"] == 1.0
    assert lang_findings.height == 0

    # LOGIC-09: incomplete preference pair with response fallback -> PREF_PAIR_INCOMPLETE
    pref_df = pl.DataFrame(
        [
            {
                "row_id": "p1",
                "prompt": "P",
                "response": "Good",
                "chosen": "Good",
                "rejected": "",
                "messages_json": "",
            }
        ]
    ).lazy()
    _pref_res, pref_findings = PreferenceConsistencyModule().run(pref_df, ModuleConfig(), seed=42)
    assert pref_findings["code"].to_list() == ["PREF_PAIR_INCOMPLETE"]

    # LOGIC-10: cosine_similarity clamped, RowFinding edge scores, uniform fallback
    assert emb_mod.cosine_similarity([1.0, 0.0], [1.0000002, 0.0]) <= 1.0

    class _BadStModel:
        def encode(self, *_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("encoder error")

    emb_mod.clear_embedding_cache()
    monkeypatch.setattr(emb_mod, "_load_sentence_transformer", lambda *_a: _BadStModel())
    with emb_mod._cache_lock:
        emb_mod._EMBEDDING_CACHE[
            (
                "sentence_transformers:intfloat/multilingual-e5-small",
                hashlib.sha256(b"cached").hexdigest(),
            )
        ] = [0.5] * 384
    vecs, backend = emb_mod.embed_texts(["cached", "uncached"], backend="sentence_transformers")
    assert backend == "local_hash"
    assert all(len(v) == 64 for v in vecs)

    # LOGIC-10: SemanticDedupModule clamps negative transitive similarity scores safely
    orig_find = emb_mod.find_similar_candidates
    from app.modules.semantic import semantic_dedup as sd_mod

    monkeypatch.setattr(
        sd_mod, "embed_texts", lambda texts, **_k: ([[1.0, 0.0], [-1.0, 0.0]], "local_hash")
    )
    monkeypatch.setattr(sd_mod, "find_similar_candidates", lambda *_a, **_k: [(0, 1, 0.95)])
    sd_df = pl.DataFrame(
        [{"row_id": "s1", "text": "longer canonical text"}, {"row_id": "s2", "text": "short"}]
    ).lazy()
    _sd_res, sd_findings = SemanticDedupModule().run(sd_df, ModuleConfig(), seed=42)
    assert sd_findings.height == 1
    assert 0.0 <= float(sd_findings["score"][0]) <= 1.0
    monkeypatch.setattr(sd_mod, "find_similar_candidates", orig_find)

    # LOGIC-12: LLM judge records placeholder_rubric when remote fails
    monkeypatch.setenv("ALLOW_EXTERNAL_LLM", "true")
    monkeypatch.setenv("LLM_PROVIDER", "openai_compat")
    monkeypatch.setenv("LLM_MODEL", "qwen")
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    get_settings.cache_clear()
    monkeypatch.setattr(
        httpx.Client,
        "post",
        lambda *_a, **_k: (_ for _ in ()).throw(httpx.ConnectError("offline")),
    )
    j_df = pl.DataFrame([{"row_id": "j1", "prompt": "Apa?", "response": "Short"}]).lazy()
    _j_res, j_findings = LlmJudgeModule().run(
        j_df, ModuleConfig(params={"min_alignment_score": 0.99}), seed=42
    )
    assert j_findings["detail"][0]["judge_mode"] == "placeholder_rubric"
    get_settings.cache_clear()


async def test_logic11_curation_drop_updates_invalid_count_and_compare_shows_dropped(
    audit_env: dict[str, Any],
) -> None:
    client: AsyncClient = audit_env["client"]
    headers: dict[str, str] = audit_env["admin_headers"]
    ws_id: str = audit_env["ws_id"]

    ds_id = (
        await client.post(
            f"/api/v1/workspaces/{ws_id}/datasets",
            json={"name": "Invalid Drop DS", "dataset_type": "sft"},
            headers=headers,
        )
    ).json()["id"]
    payload = (json.dumps({"prompt": "Valid", "response": "Ok"}) + "\n{broken-json\n").encode()
    v1_res = await client.post(
        f"/api/v1/datasets/{ds_id}/versions/upload-direct",
        files={"file": ("mix.jsonl", payload, "application/jsonl")},
        data={"source_format": "jsonl"},
        headers=headers,
    )
    v1 = v1_res.json()
    assert v1["invalid_row_count"] == 1

    # Drop invalid row_000002 -> v2 invalid_row_count becomes 0
    v2_res = await client.post(
        f"/api/v1/versions/{v1['id']}/curation",
        json={"actions": [{"row_id": "row_000002", "action": "drop_row"}]},
        headers=headers,
    )
    assert v2_res.status_code == 200
    v2 = v2_res.json()
    assert v2["row_count"] == 1
    assert v2["invalid_row_count"] == 0
    assert load_version_parquet_bytes(ds_id, 2)

    cmp_res = await client.get(f"/api/v1/versions/{v1['id']}/compare/{v2['id']}", headers=headers)
    assert cmp_res.status_code == 200
    changes = cmp_res.json()["changes"]
    assert any(c["row_id"] == "row_000002" and c["after_status"] == "dropped" for c in changes)


async def test_logic12_and_14_metrics_unmatched_and_sso_hardening(
    audit_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    client: AsyncClient = audit_env["client"]

    reset_metrics()
    await client.get("/api/v1/does-not-exist-random-12345")
    metrics_txt = render_prometheus_metrics()
    assert "__unmatched__" in metrics_txt
    assert "does-not-exist-random-12345" not in metrics_txt

    # LOGIC-14: OIDC rejects email_verified=False; SAML rejects expired NotOnOrAfter
    monkeypatch.setenv("OIDC_ENABLED", "true")
    monkeypatch.setenv("OIDC_ISSUER_URL", "https://sso.corp.local")
    monkeypatch.setenv("OIDC_CLIENT_ID", "qualitext-web")
    monkeypatch.setenv("OIDC_HS256_SECRET", "oidc-hs256-test-signing-key-32bytes")
    monkeypatch.setenv("SAML_ENABLED", "true")
    monkeypatch.setenv("SAML_IDP_SSO_URL", "https://sso.corp.local/saml")
    monkeypatch.setenv("SAML_SHARED_SECRET", "saml-hmac-shared-secret")
    get_settings.cache_clear()

    state = create_token("oidc", "oidc_state", timedelta(minutes=5), jti="jti-1")
    unverified_tok = jwt.encode(
        {
            "iss": "https://sso.corp.local",
            "aud": "qualitext-web",
            "email": "unverified@example.com",
            "email_verified": False,
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        "oidc-hs256-test-signing-key-32bytes",
        algorithm="HS256",
    )
    oidc_res = await client.post(
        "/api/v1/auth/oidc/callback",
        json={"state": state, "id_token": unverified_tok},
    )
    assert oidc_res.status_code == 403
    assert oidc_res.json()["error"]["code"] == "OIDC_EMAIL_UNVERIFIED"

    expired_dt = (datetime.now(UTC) - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    saml_xml = (
        f'<saml:Assertion xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" '
        f'IssueInstant="{expired_dt}">'
        f"<saml:Subject><saml:NameID>exp@example.com</saml:NameID></saml:Subject>"
        f'<saml:Conditions NotOnOrAfter="{expired_dt}"/>'
        f"</saml:Assertion>"
    ).encode()
    saml_b64 = base64.b64encode(saml_xml).decode()
    sig_hex = hmac.new(b"saml-hmac-shared-secret", saml_b64.encode(), hashlib.sha256).hexdigest()
    saml_res = await client.post(
        "/api/v1/auth/saml/acs",
        json={"saml_response": saml_b64},
        headers={"X-SAML-Signature": sig_hex},
    )
    assert saml_res.status_code == 401
    assert saml_res.json()["error"]["code"] == "SAML_ASSERTION_EXPIRED"
