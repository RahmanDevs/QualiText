"""Regression & enterprise readiness tests for Tahap 3 (v1.0 P1 + Security + Infra)."""

import base64
import hashlib
import hmac
import io
import json
import sqlite3
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import jwt
import polars as pl
import pytest
import yaml
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from alembic import command
from app.core.config import DEFAULT_INSECURE_SECRET_KEY, Settings, get_settings
from app.core.observability import reset_metrics
from app.core.rate_limit import login_rate_limiter
from app.core.security import Permission, Role, has_permission, hash_password, is_local_llm_url
from app.db.base import Base
from app.db.models import (
    ApiToken,
    AuditLog,
    Membership,
    Organization,
    User,
    WebhookDelivery,
    Workspace,
)
from app.db.session import get_db
from app.main import app
from app.modules.base import ModuleConfig
from app.modules.instruction.llm_judge import LlmJudgeModule
from app.modules.safety.bias import BiasScannerModule
from app.modules.semantic.clustering import project_vectors_2d
from app.modules.semantic.embeddings import clear_embedding_cache
from app.modules.semantic.semantic_dedup import SemanticDedupModule, compute_dedup_groups
from app.services.webhook_service import sign_webhook_payload


@pytest.fixture
async def stage3_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[dict[str, Any], None]:
    """Isolated DB, storage, and Admin user for Tahap 3 tests."""
    login_rate_limiter.reset()
    reset_metrics()
    storage_dir = tmp_path / "storage"
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("LOCAL_STORAGE_PATH", str(storage_dir))
    monkeypatch.setenv("CELERY_TASK_ALWAYS_EAGER", "true")
    monkeypatch.setenv("ALLOW_EXTERNAL_LLM", "false")
    monkeypatch.setenv("LLM_PROVIDER", "placeholder")
    get_settings.cache_clear()

    db_file = tmp_path / "stage3.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as db:
        org = Organization(name="Org S3", slug="org-s3", settings={})
        db.add(org)
        await db.flush()
        ws = Workspace(organization_id=org.id, name="WS S3", slug="ws-s3", settings={})
        db.add(ws)
        await db.flush()
        admin = User(
            email="admin_s3@example.com",
            full_name="Admin S3",
            password_hash=hash_password("Pass123!"),
            is_active=True,
            is_superadmin=True,
        )
        db.add(admin)
        await db.flush()
        db.add(Membership(user_id=admin.id, workspace_id=ws.id, role=Role.ADMIN.value))
        await db.commit()
        ws_id = ws.id
        admin_id = admin.id

    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        login_res = await ac.post(
            "/api/v1/auth/login",
            json={"email": "admin_s3@example.com", "password": "Pass123!"},
        )
        assert login_res.status_code == 200
        tok_pair = login_res.json()
        yield {
            "client": ac,
            "jwt": tok_pair["access_token"],
            "refresh": tok_pair["refresh_token"],
            "headers": {"Authorization": f"Bearer {tok_pair['access_token']}"},
            "ws_id": ws_id,
            "admin_id": admin_id,
            "session_factory": session_factory,
        }

    app.dependency_overrides.clear()
    await engine.dispose()
    login_rate_limiter.reset()
    get_settings.cache_clear()


async def test_g5_api_token_auth_and_scope_enforcement(stage3_env: dict[str, Any]) -> None:
    client: AsyncClient = stage3_env["client"]
    headers: dict[str, str] = stage3_env["headers"]
    ws_id: str = stage3_env["ws_id"]
    session_factory: async_sessionmaker[AsyncSession] = stage3_env["session_factory"]

    # Create dataset & version using admin JWT first
    ds_id = (
        await client.post(
            f"/api/v1/workspaces/{ws_id}/datasets",
            json={"name": "Token Scope DS", "dataset_type": "sft"},
            headers=headers,
        )
    ).json()["id"]
    row = json.dumps({"prompt": "NIK saya 3201234567890001", "response": "Tercatat."})
    v1_id = (
        await client.post(
            f"/api/v1/datasets/{ds_id}/versions/upload-direct",
            files={"file": ("tok.jsonl", (row + "\n").encode(), "application/jsonl")},
            data={"source_format": "jsonl"},
            headers=headers,
        )
    ).json()["id"]

    # Mask row into v2 so export Quality Gate passes
    v2_id = (
        await client.post(
            f"/api/v1/versions/{v1_id}/curation",
            json={"actions": [{"row_id": "row_000001", "action": "mask_pii"}]},
            headers=headers,
        )
    ).json()["id"]

    # Create scoped API token (dataset:read + export:create ONLY, even though owner is superadmin)
    tok_res = await client.post(
        "/api/v1/api-tokens",
        json={"name": "CI Export Only", "scopes": ["dataset:read", "export:create"]},
        headers=headers,
    )
    assert tok_res.status_code == 201
    api_token_id = tok_res.json()["id"]
    qt_secret = tok_res.json()["token"]
    assert qt_secret.startswith("qt_")
    qt_headers = {"Authorization": f"Bearer {qt_secret}"}

    # Allowed: read datasets + export
    assert (
        await client.get(f"/api/v1/workspaces/{ws_id}/datasets", headers=qt_headers)
    ).status_code == 200
    exp_res = await client.post(
        f"/api/v1/versions/{v2_id}/exports",
        json={"export_format": "jsonl"},
        headers=qt_headers,
    )
    assert exp_res.status_code == 201

    # Forbidden scope: dataset:write and pii:view
    create_denied = await client.post(
        f"/api/v1/workspaces/{ws_id}/datasets",
        json={"name": "Should Fail", "dataset_type": "sft"},
        headers=qt_headers,
    )
    assert create_denied.status_code == 403
    assert create_denied.json()["error"]["code"] == "INSUFFICIENT_TOKEN_SCOPE"

    pii_denied = await client.get(
        f"/api/v1/versions/{v1_id}/rows?reveal_pii=true",
        headers=qt_headers,
    )
    assert pii_denied.status_code == 403
    assert pii_denied.json()["error"]["code"] == "FORBIDDEN_PII_VIEW"

    # Expired token fails with TOKEN_EXPIRED
    expired_raw = "qt_expired_test_token_123456"
    async with session_factory() as db:
        db.add(
            ApiToken(
                user_id=stage3_env["admin_id"],
                name="Expired Tok",
                token_hash=hashlib.sha256(expired_raw.encode()).hexdigest(),
                scopes="dataset:read",
                expires_at=datetime.now(UTC) - timedelta(hours=1),
            )
        )
        await db.commit()
    exp_check = await client.get(
        f"/api/v1/workspaces/{ws_id}/datasets",
        headers={"Authorization": f"Bearer {expired_raw}"},
    )
    assert exp_check.status_code == 401
    assert exp_check.json()["error"]["code"] == "TOKEN_EXPIRED"

    # Revoking active token blocks subsequent use & writes audit log
    del_res = await client.delete(f"/api/v1/api-tokens/{api_token_id}", headers=headers)
    assert del_res.status_code == 204
    revoked_check = await client.get(
        f"/api/v1/workspaces/{ws_id}/datasets",
        headers=qt_headers,
    )
    assert revoked_check.status_code == 401
    assert revoked_check.json()["error"]["code"] == "INVALID_TOKEN"


async def test_sec05_refresh_rotation_reuse_detection_and_logout(
    stage3_env: dict[str, Any],
) -> None:
    client: AsyncClient = stage3_env["client"]
    refresh_1: str = stage3_env["refresh"]
    session_factory: async_sessionmaker[AsyncSession] = stage3_env["session_factory"]

    # First refresh rotates cleanly
    r1 = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_1})
    assert r1.status_code == 200
    refresh_2 = r1.json()["refresh_token"]
    assert refresh_2 != refresh_1

    # Reusing refresh_1 triggers family revocation + security audit log
    reuse = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_1})
    assert reuse.status_code == 401
    assert reuse.json()["error"]["code"] == "REFRESH_TOKEN_REVOKED"

    # refresh_2 in same family is now also revoked!
    child_check = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_2})
    assert child_check.status_code == 401
    assert child_check.json()["error"]["code"] == "REFRESH_TOKEN_REVOKED"

    async with session_factory() as db:
        actions = (await db.execute(select(AuditLog.action))).scalars().all()
        assert "auth.refresh_reuse_detected" in actions

    # Fresh login + logout invalidates its refresh token
    fresh_login = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin_s3@example.com", "password": "Pass123!"},
    )
    refresh_3 = fresh_login.json()["refresh_token"]
    lo_res = await client.post("/api/v1/auth/logout", json={"refresh_token": refresh_3})
    assert lo_res.status_code == 200
    after_logout = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_3})
    assert after_logout.status_code == 401


async def test_sec05_login_rate_limiting_production_secret_and_local_llm_url(
    stage3_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client: AsyncClient = stage3_env["client"]
    login_rate_limiter.reset()
    monkeypatch.setenv("LOGIN_RATE_LIMIT_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("LOGIN_RATE_LIMIT_WINDOW_SECONDS", "60")
    get_settings.cache_clear()

    for _ in range(3):
        bad = await client.post(
            "/api/v1/auth/login",
            json={"email": "admin_s3@example.com", "password": "WrongPassword!"},
        )
        assert bad.status_code == 401

    blocked = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin_s3@example.com", "password": "Pass123!"},
    )
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"
    assert blocked.json()["error"]["details"]["retry_after_seconds"] >= 1

    # Production secret validator guard
    with pytest.raises(ValueError, match="SECRET_KEY"):
        Settings(app_env="production", secret_key=DEFAULT_INSECURE_SECRET_KEY)
    prod_ok = Settings(app_env="production", secret_key="a-very-strong-production-secret-32+")
    assert prod_ok.app_env == "production"

    # Strict host validation for local LLM URL
    assert is_local_llm_url("http://localhost:11434/v1") is True
    assert is_local_llm_url("http://127.0.0.1:8001/v1") is True
    assert is_local_llm_url("https://localhost.attacker.com/v1") is False
    assert is_local_llm_url("ftp://localhost/v1") is False


async def test_g1_oidc_and_saml_sso_flows(
    stage3_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client: AsyncClient = stage3_env["client"]
    oidc_secret = "oidc-hs256-test-signing-key-32bytes"
    saml_secret = "saml-hmac-shared-secret"

    monkeypatch.setenv("OIDC_ENABLED", "true")
    monkeypatch.setenv("OIDC_PROVIDER_NAME", "Keycloak Corp")
    monkeypatch.setenv("OIDC_ISSUER_URL", "https://sso.corp.local")
    monkeypatch.setenv("OIDC_CLIENT_ID", "qualitext-web")
    monkeypatch.setenv("OIDC_HS256_SECRET", oidc_secret)
    monkeypatch.setenv("OIDC_DEFAULT_WORKSPACE_SLUG", "ws-s3")
    monkeypatch.setenv("SAML_ENABLED", "true")
    monkeypatch.setenv("SAML_IDP_SSO_URL", "https://sso.corp.local/saml")
    monkeypatch.setenv("SAML_SHARED_SECRET", saml_secret)
    get_settings.cache_clear()

    prov = await client.get("/api/v1/auth/sso/providers")
    assert prov.status_code == 200
    assert prov.json() == {
        "oidc_enabled": True,
        "oidc_provider_name": "Keycloak Corp",
        "saml_enabled": True,
    }

    # OIDC flow
    oidc_init = await client.get("/api/v1/auth/oidc/login")
    assert oidc_init.status_code == 200
    state = oidc_init.json()["state"]

    id_token = jwt.encode(
        {
            "iss": "https://sso.corp.local",
            "aud": "qualitext-web",
            "email": "oidc.curator@example.com",
            "name": "OIDC Curator",
            "role": "curator",
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        oidc_secret,
        algorithm="HS256",
    )
    oidc_cb = await client.post(
        "/api/v1/auth/oidc/callback",
        json={"state": state, "id_token": id_token},
    )
    assert oidc_cb.status_code == 200
    me_oidc = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {oidc_cb.json()['access_token']}"},
    )
    assert me_oidc.status_code == 200
    assert me_oidc.json()["email"] == "oidc.curator@example.com"
    assert me_oidc.json()["workspaces"][0]["role"] == "curator"

    # SAML metadata & ACS guards (XXE, unsigned, signed)
    meta = await client.get("/api/v1/auth/saml/metadata")
    assert meta.status_code == 200
    assert "SPSSODescriptor" in meta.text

    xxe_payload = (
        b'<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY xxe "pwn">]>'
        b"<Assertion><NameID>a@b.c</NameID></Assertion>"
    )
    xxe_b64 = base64.b64encode(xxe_payload).decode()
    xxe_res = await client.post("/api/v1/auth/saml/acs", json={"saml_response": xxe_b64})
    assert xxe_res.status_code == 400
    assert xxe_res.json()["error"]["code"] == "INVALID_SAML_XML"

    saml_xml = (
        b'<saml:Assertion xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" '
        b'IssueInstant="2026-11-19T00:00:00Z">'
        b"<saml:Subject><saml:NameID>saml.analyst@example.com</saml:NameID></saml:Subject>"
        b"<saml:AttributeStatement>"
        b'<saml:Attribute Name="full_name">'
        b"<saml:AttributeValue>SAML Analyst</saml:AttributeValue></saml:Attribute>"
        b'<saml:Attribute Name="role">'
        b"<saml:AttributeValue>analyst</saml:AttributeValue></saml:Attribute>"
        b"</saml:AttributeStatement>"
        b"</saml:Assertion>"
    )
    saml_b64 = base64.b64encode(saml_xml).decode()
    unsigned_res = await client.post("/api/v1/auth/saml/acs", json={"saml_response": saml_b64})
    assert unsigned_res.status_code == 401
    assert unsigned_res.json()["error"]["code"] == "INVALID_SAML_SIGNATURE"

    sig_hex = hmac.new(saml_secret.encode(), saml_b64.encode(), hashlib.sha256).hexdigest()
    signed_res = await client.post(
        "/api/v1/auth/saml/acs",
        json={"saml_response": saml_b64},
        headers={"X-SAML-Signature": sig_hex},
    )
    assert signed_res.status_code == 200
    me_saml = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {signed_res.json()['access_token']}"},
    )
    assert me_saml.json()["email"] == "saml.analyst@example.com"
    assert me_saml.json()["workspaces"][0]["role"] == "analyst"


async def test_g5_webhooks_ssrf_signing_and_run_delivery(
    stage3_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client: AsyncClient = stage3_env["client"]
    headers: dict[str, str] = stage3_env["headers"]
    ws_id: str = stage3_env["ws_id"]
    session_factory: async_sessionmaker[AsyncSession] = stage3_env["session_factory"]

    # Private SSRF URL blocked by default
    ssrf_res = await client.post(
        f"/api/v1/workspaces/{ws_id}/webhooks",
        json={"name": "Bad SSRF", "url": "http://169.254.169.254/latest"},
        headers=headers,
    )
    assert ssrf_res.status_code == 400
    assert ssrf_res.json()["error"]["code"] == "UNSAFE_WEBHOOK_URL"

    captured_calls: list[dict[str, Any]] = []
    orig_post = httpx.AsyncClient.post

    async def _fake_webhook_post(
        self_client: httpx.AsyncClient, url: str, **kwargs: Any
    ) -> httpx.Response:
        if str(url).startswith("https://hooks.example.com"):
            captured_calls.append(
                {
                    "url": str(url),
                    "content": kwargs.get("content", b""),
                    "headers": dict(kwargs.get("headers") or {}),
                }
            )
            return httpx.Response(200, request=httpx.Request("POST", url))
        return await orig_post(self_client, url, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_webhook_post)

    wh_res = await client.post(
        f"/api/v1/workspaces/{ws_id}/webhooks",
        json={
            "name": "MLOps Webhook",
            "url": "https://hooks.example.com/qualitext",
            "events": ["run.completed", "export.completed"],
        },
        headers=headers,
    )
    assert wh_res.status_code == 201
    wh_id = wh_res.json()["id"]
    wh_secret = wh_res.json()["signing_secret"]
    assert wh_secret.startswith("whsec_")

    # Trigger webhook test
    test_res = await client.post(
        f"/api/v1/workspaces/{ws_id}/webhooks/{wh_id}/test",
        headers=headers,
    )
    assert test_res.status_code == 200
    assert test_res.json()["delivered"] is True

    # Run quality pipeline containing raw PII -> webhook payload must NOT contain raw PII
    ds_id = (
        await client.post(
            f"/api/v1/workspaces/{ws_id}/datasets",
            json={"name": "Webhook DS", "dataset_type": "sft"},
            headers=headers,
        )
    ).json()["id"]
    row = json.dumps({"prompt": "NIK saya 3201234567890001", "response": "Oke."})
    v1_id = (
        await client.post(
            f"/api/v1/datasets/{ds_id}/versions/upload-direct",
            files={"file": ("wh.jsonl", (row + "\n").encode(), "application/jsonl")},
            data={"source_format": "jsonl"},
            headers=headers,
        )
    ).json()["id"]
    run_res = await client.post(
        f"/api/v1/versions/{v1_id}/runs",
        json={"modules": ["token_analytics", "pii"]},
        headers=headers,
    )
    assert run_res.status_code == 201

    assert len(captured_calls) == 2
    run_call = captured_calls[1]
    assert run_call["headers"]["X-QualiText-Event"] == "run.completed"
    expected_sig = sign_webhook_payload(wh_secret, run_call["content"])
    assert run_call["headers"]["X-QualiText-Signature"] == expected_sig
    assert b"3201234567890001" not in run_call["content"]

    deliveries_res = await client.get(
        f"/api/v1/workspaces/{ws_id}/webhooks/{wh_id}/deliveries",
        headers=headers,
    )
    assert deliveries_res.status_code == 200
    assert len(deliveries_res.json()) == 2

    async with session_factory() as db:
        rows = (await db.execute(select(WebhookDelivery))).scalars().all()
        assert all("3201234567890001" not in json.dumps(d.payload_json) for d in rows)


async def test_sec04_complete_audit_logs_and_export(stage3_env: dict[str, Any]) -> None:
    client: AsyncClient = stage3_env["client"]
    headers: dict[str, str] = stage3_env["headers"]
    ws_id: str = stage3_env["ws_id"]

    # Policy update
    pol_res = await client.put(
        f"/api/v1/workspaces/{ws_id}/policy",
        json={"min_dqi": 65.0, "block_if_critical_pii": True, "is_active": True},
        headers=headers,
    )
    assert pol_res.status_code == 200

    # Dataset + upload + mapping update
    ds_id = (
        await client.post(
            f"/api/v1/workspaces/{ws_id}/datasets",
            json={"name": "Audit Mapping DS", "dataset_type": "sft"},
            headers=headers,
        )
    ).json()["id"]
    raw_line = json.dumps({"instruction": "Halo?", "output": "Hai."})
    v1_id = (
        await client.post(
            f"/api/v1/datasets/{ds_id}/versions/upload-direct",
            files={"file": ("map.jsonl", (raw_line + "\n").encode(), "application/jsonl")},
            data={"source_format": "jsonl"},
            headers=headers,
        )
    ).json()["id"]
    map_res = await client.put(
        f"/api/v1/versions/{v1_id}/mapping",
        json={"mapping": {"instruction": "prompt", "output": "response"}},
        headers=headers,
    )
    assert map_res.status_code == 200

    # Canonical alias & filtered logs
    alias_res = await client.get(f"/api/v1/audit-logs?workspace_id={ws_id}", headers=headers)
    assert alias_res.status_code == 200
    actions = {entry["action"] for entry in alias_res.json()}
    assert {"policy.update", "dataset.mapping_update", "dataset.create"}.issubset(actions)
    assert all("ip_address" in entry for entry in alias_res.json())

    filtered = await client.get(
        f"/api/v1/workspaces/{ws_id}/audit-logs?action=policy.update",
        headers=headers,
    )
    assert filtered.status_code == 200
    assert len(filtered.json()) == 1
    assert filtered.json()[0]["action"] == "policy.update"

    # CSV and JSON exports
    csv_res = await client.get(
        f"/api/v1/workspaces/{ws_id}/audit-logs/export?format=csv",
        headers=headers,
    )
    assert csv_res.status_code == 200
    assert "text/csv" in csv_res.headers["content-type"]
    assert "policy.update" in csv_res.text

    json_res = await client.get(
        f"/api/v1/workspaces/{ws_id}/audit-logs/export?format=json",
        headers=headers,
    )
    assert json_res.status_code == 200
    assert len(json_res.json()) >= 3


async def test_c2_c3_d2_e1_semantic_cache_2d_clusters_rubric_and_bias(
    stage3_env: dict[str, Any],
) -> None:
    clear_embedding_cache()
    row_ids = ["r1", "r2", "r3"]
    texts = [
        "Bagaimana cara mereset kata sandi akun QualiText saya melalui panel profil?",
        "Bagaimana cara mereset kata sandi akun QualiText saya melalui panel profil dengan aman?",
        "Jelaskan arsitektur transformer dalam NLP modern.",
    ]

    groups_1, backend, hit_ratio_1 = compute_dedup_groups(row_ids, texts, threshold=0.80, seed=42)
    assert backend == "local_hash"
    assert hit_ratio_1 == 0.0
    assert len(groups_1) == 1
    # Longest row (r2) must be kept as canonical representative!
    assert groups_1[0]["canonical_row_id"] == "r2"

    # Running again hits bounded embedding cache
    _groups_2, _b2, hit_ratio_2 = compute_dedup_groups(row_ids, texts, threshold=0.80, seed=42)
    assert hit_ratio_2 == 1.0

    # Module C2 returns duplicate_groups metric and emits DUP_SEMANTIC on r1
    mod_df = pl.DataFrame(
        [{"row_id": rid, "text": txt} for rid, txt in zip(row_ids, texts, strict=True)]
    ).lazy()
    dedup_res, dedup_findings = SemanticDedupModule().run(
        mod_df, ModuleConfig(params={"similarity_threshold": 0.80}), seed=42
    )
    assert dedup_res.metrics["duplicate_groups"] == 1.0
    assert dedup_findings.height == 1

    # 2D PCA projection bounded inside [-1, 1]
    coords = project_vectors_2d([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [1.0, 1.0, 0.0]], seed=42)
    assert all(-1.0 <= x <= 1.0 and -1.0 <= y <= 1.0 for x, y in coords)

    # API preview endpoints mask PII cleanly
    client: AsyncClient = stage3_env["client"]
    headers: dict[str, str] = stage3_env["headers"]
    ws_id: str = stage3_env["ws_id"]
    ds_id = (
        await client.post(
            f"/api/v1/workspaces/{ws_id}/datasets",
            json={"name": "Semantic Preview DS", "dataset_type": "sft"},
            headers=headers,
        )
    ).json()["id"]
    jsonl_body = "\n".join(
        [
            json.dumps({"prompt": "NIK saya 3201234567890001 tolong cek.", "response": "Siap."}),
            json.dumps(
                {
                    "prompt": "NIK saya 3201234567890001 tolong cek segera.",
                    "response": "Siap kami bantu.",
                }
            ),
            json.dumps({"prompt": "Topik machine learning Indonesia.", "response": "Bagus."}),
        ]
    )
    v1_id = (
        await client.post(
            f"/api/v1/datasets/{ds_id}/versions/upload-direct",
            files={"file": ("sem.jsonl", (jsonl_body + "\n").encode(), "application/jsonl")},
            data={"source_format": "jsonl"},
            headers=headers,
        )
    ).json()["id"]

    dedup_prev = await client.get(
        f"/api/v1/versions/{v1_id}/dedup-preview?threshold=0.75",
        headers=headers,
    )
    assert dedup_prev.status_code == 200
    assert "3201234567890001" not in json.dumps(dedup_prev.json())

    clusters_prev = await client.get(
        f"/api/v1/versions/{v1_id}/topic-clusters?num_clusters=2",
        headers=headers,
    )
    assert clusters_prev.status_code == 200
    c_data = clusters_prev.json()
    assert len(c_data["points"]) == 3
    assert "3201234567890001" not in json.dumps(c_data)

    # LLM-Judge custom rubric hash & seeded sampling across larger frame
    judge_rows = [{"row_id": f"j_{i}", "prompt": "Apa?", "response": "Short"} for i in range(12)]
    judge_lf = pl.DataFrame(judge_rows).lazy()
    j1_res, j1_df = LlmJudgeModule().run(
        judge_lf,
        ModuleConfig(params={"sample_limit": 4, "min_alignment_score": 0.99, "rubric": "Rubrik A"}),
        seed=1,
    )
    _j2_res, j2_df = LlmJudgeModule().run(
        judge_lf,
        ModuleConfig(params={"sample_limit": 4, "min_alignment_score": 0.99, "rubric": "Rubrik B"}),
        seed=99,
    )
    assert j1_res.metrics["sampled_rows"] == 4.0
    assert j1_df.height == 4
    s1 = {r["detail"]["rubric_hash"] for r in j1_df.iter_rows(named=True)}
    s2 = {r["detail"]["rubric_hash"] for r in j2_df.iter_rows(named=True)}
    assert s1 != s2
    idx1 = [r["detail"]["sampled_row_index"] for r in j1_df.iter_rows(named=True)]
    idx2 = [r["detail"]["sampled_row_index"] for r in j2_df.iter_rows(named=True)]
    assert idx1 != idx2

    # Bilingual (ID + EN) bias categories emitted in metrics
    bias_lf = pl.DataFrame(
        [
            {"row_id": "b1", "text": "Wanita tidak bisa menjadi pemimpin proyek."},
            {"row_id": "b2", "text": "Disabled people are useless at technical tasks."},
        ]
    ).lazy()
    bias_res, _bias_df = BiasScannerModule().run(bias_lf, ModuleConfig(), seed=42)
    assert bias_res.metrics["cat_gender_stereotype"] == 1.0
    assert bias_res.metrics["cat_age_disability_bias"] == 1.0


async def test_f6_f7_pdf_report_per_dimension_policy_and_overview(
    stage3_env: dict[str, Any],
) -> None:
    client: AsyncClient = stage3_env["client"]
    headers: dict[str, str] = stage3_env["headers"]
    ws_id: str = stage3_env["ws_id"]

    # Configure per-dimension policy requiring structure_validity >= 95 and critical_cap = 55
    pol_res = await client.put(
        f"/api/v1/workspaces/{ws_id}/policy",
        json={
            "min_dqi": 0.0,
            "block_if_critical_pii": False,
            "is_active": True,
            "critical_cap": 55.0,
            "dimension_thresholds": {"structure_validity": 95.0},
        },
        headers=headers,
    )
    assert pol_res.status_code == 200

    ds_id = (
        await client.post(
            f"/api/v1/workspaces/{ws_id}/datasets",
            json={"name": "PDF & Overview DS", "dataset_type": "sft"},
            headers=headers,
        )
    ).json()["id"]
    bad_rows = "\n".join(
        [
            json.dumps({"prompt": "kata " * 5000, "response": "terlalu panjang"}),
            json.dumps({"prompt": "NIK saya 3201234567890001", "response": "ok"}),
        ]
    )
    v1_id = (
        await client.post(
            f"/api/v1/datasets/{ds_id}/versions/upload-direct",
            files={"file": ("pdf.jsonl", (bad_rows + "\n").encode(), "application/jsonl")},
            data={"source_format": "jsonl"},
            headers=headers,
        )
    ).json()["id"]

    run_res = await client.post(
        f"/api/v1/versions/{v1_id}/runs",
        json={
            "modules": ["token_analytics", "pii"],
            "params": {"token_analytics": {"max_tokens": 128}},
        },
        headers=headers,
    )
    assert run_res.status_code == 201
    run_data = run_res.json()
    assert run_data["dqi_summary"]["critical_capped"] is True
    assert run_data["dqi_total"] <= 55.0

    # PDF Report downloadable and readable by pypdf.PdfReader
    pdf_res = await client.get(f"/api/v1/runs/{run_data['id']}/report.pdf", headers=headers)
    assert pdf_res.status_code == 200
    assert pdf_res.headers["content-type"] == "application/pdf"
    reader = PdfReader(io.BytesIO(pdf_res.content))
    extracted = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert "QualiText" in extracted
    assert run_data["id"] in extracted

    # Per-dimension threshold blocks export cleanly
    exp_blocked = await client.post(
        f"/api/v1/versions/{v1_id}/exports",
        json={"export_format": "jsonl"},
        headers=headers,
    )
    assert exp_blocked.status_code == 409
    assert exp_blocked.json()["error"]["code"] == "QUALITY_GATE_DIMENSION_BELOW_MIN"

    # Live Workspace Overview
    ov_res = await client.get(f"/api/v1/workspaces/{ws_id}/overview", headers=headers)
    assert ov_res.status_code == 200
    ov = ov_res.json()
    assert ov["total_datasets"] == 1
    assert ov["open_critical_pii"] >= 1
    assert len(ov["datasets_needing_attention"]) == 1
    assert len(ov["dqi_trend"]) == 1


async def test_observability_readyz_metrics_helm_and_openapi_contract(
    stage3_env: dict[str, Any],
) -> None:
    client: AsyncClient = stage3_env["client"]
    ws_id: str = stage3_env["ws_id"]
    headers: dict[str, str] = stage3_env["headers"]

    ready = await client.get("/readyz")
    assert ready.status_code == 200
    r_data = ready.json()
    assert r_data["status"] == "ready"
    assert r_data["checks"]["database"] == "ok"
    assert r_data["checks"]["storage"] == "ok"

    await client.get(f"/api/v1/workspaces/{ws_id}/datasets", headers=headers)
    metrics = await client.get("/metrics")
    assert metrics.status_code == 200
    assert "qualitext_http_requests_total" in metrics.text
    assert "qualitext_http_request_duration_seconds_bucket" in metrics.text
    assert "qualitext_quality_runs_total" in metrics.text

    # OpenAPI schema completeness
    schema = app.openapi()
    expected_paths = {
        "/healthz",
        "/readyz",
        "/metrics",
        "/api/v1/auth/sso/providers",
        "/api/v1/auth/oidc/login",
        "/api/v1/auth/oidc/callback",
        "/api/v1/auth/saml/metadata",
        "/api/v1/auth/saml/acs",
        "/api/v1/auth/logout",
        "/api/v1/workspaces/{workspace_id}/overview",
        "/api/v1/workspaces/{workspace_id}/webhooks",
        "/api/v1/workspaces/{workspace_id}/audit-logs/export",
        "/api/v1/audit-logs",
        "/api/v1/runs/{run_id}/report.pdf",
        "/api/v1/versions/{version_id}/dedup-preview",
        "/api/v1/versions/{version_id}/topic-clusters",
    }
    assert expected_paths.issubset(set(schema["paths"].keys()))
    assert has_permission(Role.ADMIN, Permission.DATASET_READ) is True


def test_helm_chart_templates_and_manifests() -> None:
    """Verify Helm chart YAML manifests and templates are present and valid."""
    repo_root = Path(__file__).resolve().parents[2]
    helm_dir = repo_root / "deploy" / "helm" / "qualitext"
    chart_meta = yaml.safe_load((helm_dir / "Chart.yaml").read_text(encoding="utf-8"))
    values_meta = yaml.safe_load((helm_dir / "values.yaml").read_text(encoding="utf-8"))
    assert chart_meta["name"] == "qualitext"
    assert values_meta["api"]["env"]["ALLOW_EXTERNAL_LLM"] == "false"

    tmpl_dir = helm_dir / "templates"
    required_templates = {
        "_helpers.tpl",
        "configmap.yaml",
        "secret.yaml",
        "api-deployment.yaml",
        "api-service.yaml",
        "worker-deployment.yaml",
        "worker-hpa.yaml",
        "frontend-deployment.yaml",
        "frontend-service.yaml",
        "ingress.yaml",
        "servicemonitor.yaml",
    }
    assert required_templates.issubset({p.name for p in tmpl_dir.iterdir()})
    api_deploy_text = (tmpl_dir / "api-deployment.yaml").read_text(encoding="utf-8")
    assert "/healthz" in api_deploy_text
    assert "/readyz" in api_deploy_text
    assert "prometheus.io/scrape" in api_deploy_text


def test_alembic_0004_migration_sqlite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    backend_root = Path(__file__).resolve().parents[1]
    db_path = tmp_path / "alembic_stage3.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    get_settings.cache_clear()

    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    command.upgrade(cfg, "0004_stage3_enterprise_tables")

    with sqlite3.connect(db_path) as conn:
        rev = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        assert rev == ("0004_stage3_enterprise_tables",)
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert {
            "refresh_token_sessions",
            "webhook_subscriptions",
            "webhook_deliveries",
        }.issubset(tables)

    get_settings.cache_clear()
