"""Foundation tests for health, auth, RBAC, audit logging, and storage."""

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import Permission, Role, has_permission, hash_password
from app.db.base import Base
from app.db.models import AuditLog, Membership, Organization, User, Workspace
from app.db.session import get_db
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
