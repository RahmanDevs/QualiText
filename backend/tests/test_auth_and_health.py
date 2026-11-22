"""Foundation tests for health, auth, RBAC, audit logging, and storage."""

import asyncio
import io
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.db.session as db_session_mod
import scripts.seed as seed_mod
from alembic import command
from app.core.config import get_settings
from app.core.rate_limit import login_rate_limiter
from app.core.security import Permission, Role, has_permission, hash_password
from app.db.base import Base
from app.db.models import AuditLog, Membership, Organization, User, Workspace
from app.db.session import ensure_postgres_version_column_width, get_db
from app.main import app
from app.storage.s3 import LocalStorageBackend


@pytest.fixture
async def db_session(tmp_path: Path) -> AsyncGenerator[AsyncSession, None]:
    """Create an isolated temporary SQLite database per test."""
    db_file = tmp_path / "test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Provide an HTTPX AsyncClient wired to the isolated test DB."""

    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


async def test_healthz(client: AsyncClient) -> None:
    res = await client.get("/healthz")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_rbac_permission_matrix() -> None:
    assert has_permission(Role.ADMIN, Permission.PII_VIEW) is True
    assert has_permission(Role.GOVERNANCE, Permission.PII_VIEW) is True
    assert has_permission(Role.ANALYST, Permission.PII_VIEW) is False
    assert has_permission(Role.VIEWER, Permission.DATASET_WRITE) is False
    assert has_permission("unknown", Permission.DATASET_READ) is False


async def test_login_me_rbac_and_audit_log(client: AsyncClient, db_session: AsyncSession) -> None:
    org = Organization(name="Org Test", slug="org-test", settings={})
    db_session.add(org)
    await db_session.flush()

    ws = Workspace(organization_id=org.id, name="WS Test", slug="ws-test", settings={})
    db_session.add(ws)
    await db_session.flush()

    user = User(
        email="dina@example.com",
        full_name="Dina Test",
        password_hash=hash_password("Secret123!"),
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()

    db_session.add(Membership(user_id=user.id, workspace_id=ws.id, role=Role.ANALYST.value))
    await db_session.commit()

    login_res = await client.post(
        "/api/v1/auth/login",
        json={"email": "dina@example.com", "password": "Secret123!"},
    )
    assert login_res.status_code == 200
    tokens = login_res.json()
    assert "access_token" in tokens
    assert "refresh_token" in tokens

    me_res = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert me_res.status_code == 200
    me_data = me_res.json()
    assert me_data["email"] == "dina@example.com"
    assert len(me_data["workspaces"]) == 1
    assert me_data["workspaces"][0]["role"] == "analyst"

    ping_res = await client.get(
        f"/api/v1/workspaces/{ws.id}/ping",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert ping_res.status_code == 200

    audit_rows = (await db_session.execute(select(AuditLog))).scalars().all()
    assert any(a.action == "auth.login" and a.user_id == user.id for a in audit_rows)


def test_local_storage_backend(tmp_path: Path) -> None:
    storage = LocalStorageBackend(str(tmp_path))
    uri = storage.put_bytes("datasets/sample.txt", b"hello qualitext")
    assert uri == "local://datasets/sample.txt"
    assert storage.exists("datasets/sample.txt") is True
    assert storage.get_bytes("datasets/sample.txt") == b"hello qualitext"


async def test_migrated_db_login_me_and_overview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify migrated + seeded DB handles login, /auth/me, and workspace overview cleanly."""
    login_rate_limiter.reset()
    db_path = tmp_path / "migrated_login.db"
    storage_dir = tmp_path / "storage"
    db_url = f"sqlite+aiosqlite:///{db_path}"

    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("LOCAL_STORAGE_PATH", str(storage_dir))
    get_settings.cache_clear()

    def _upgrade_sync() -> None:
        backend_root = Path(__file__).resolve().parents[1]
        cfg = Config(str(backend_root / "alembic.ini"))
        cfg.set_main_option("script_location", str(backend_root / "alembic"))
        command.upgrade(cfg, "head")

    await asyncio.to_thread(_upgrade_sync)

    engine = create_async_engine(db_url, future=True)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db_session_mod, "AsyncSessionLocal", session_factory)
    monkeypatch.setattr(seed_mod, "AsyncSessionLocal", session_factory)

    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        await seed_mod.seed_data()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            login_res = await ac.post(
                "/api/v1/auth/login",
                json={"email": "admin@qualitext.id", "password": "Admin123!"},
                headers={"Origin": "http://127.0.0.1:5173"},
            )
            assert login_res.status_code == 200
            assert login_res.headers.get("access-control-allow-origin") == "http://127.0.0.1:5173"
            token = login_res.json()["access_token"]

            me_res = await ac.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert me_res.status_code == 200
            workspaces = me_res.json()["workspaces"]
            assert len(workspaces) == 1

            ov_res = await ac.get(
                f"/api/v1/workspaces/{workspaces[0]['id']}/overview",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert ov_res.status_code == 200
            assert ov_res.json()["total_datasets"] == 1
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
        login_rate_limiter.reset()
        get_settings.cache_clear()


def test_alembic_postgres_version_width_and_0003_pkey_rename(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify PostgreSQL version_num widening helper and 0003 PK constraint rename SQL."""

    class _StubDialect:
        def __init__(self, name: str) -> None:
            self.name = name

    class _StubConnection:
        def __init__(self, dialect_name: str) -> None:
            self.dialect = _StubDialect(dialect_name)
            self.statements: list[str] = []

        def execute(self, statement: object) -> None:
            self.statements.append(str(statement))

    sqlite_conn = _StubConnection("sqlite")
    ensure_postgres_version_column_width(sqlite_conn)  # type: ignore[arg-type]
    assert sqlite_conn.statements == []

    pg_conn = _StubConnection("postgresql")
    ensure_postgres_version_column_width(pg_conn)  # type: ignore[arg-type]
    assert len(pg_conn.statements) == 2
    assert "CREATE TABLE IF NOT EXISTS alembic_version" in pg_conn.statements[0]
    assert "VARCHAR(128)" in pg_conn.statements[0]
    assert (
        "ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(128)"
        in pg_conn.statements[1]
    )

    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+asyncpg://qualitext:qualitext@localhost:5432/qualitext"
    )
    get_settings.cache_clear()
    try:
        backend_root = Path(__file__).resolve().parents[1]
        sql_buf = io.StringIO()
        cfg = Config(str(backend_root / "alembic.ini"), output_buffer=sql_buf)
        cfg.set_main_option("script_location", str(backend_root / "alembic"))
        command.upgrade(cfg, "head", sql=True)
        sql_text = sql_buf.getvalue()
        rename_stmt = (
            "ALTER TABLE row_findings_unpartitioned "
            "RENAME CONSTRAINT row_findings_pkey TO row_findings_unpartitioned_pkey"
        )
        partitioned_stmt = "PARTITION BY HASH (run_id)"
        assert rename_stmt in sql_text
        assert partitioned_stmt in sql_text
        assert sql_text.index(rename_stmt) < sql_text.index(partitioned_stmt)
    finally:
        get_settings.cache_clear()


async def test_unhandled_error_preserves_cors_headers() -> None:
    """Verify 500 JSON responses keep CORS headers for configured origins."""

    async def _broken_get_db() -> AsyncGenerator[AsyncSession, None]:
        raise RuntimeError("db boom")
        yield  # pragma: no cover

    app.dependency_overrides[get_db] = _broken_get_db
    try:
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            for origin in ("http://localhost:5173", "http://127.0.0.1:5173"):
                res = await ac.post(
                    "/api/v1/auth/login",
                    json={"email": "admin@qualitext.id", "password": "Admin123!"},
                    headers={"Origin": origin},
                )
                assert res.status_code == 500
                assert res.json()["error"]["code"] == "INTERNAL_ERROR"
                assert res.headers.get("access-control-allow-origin") == origin
    finally:
        app.dependency_overrides.clear()
